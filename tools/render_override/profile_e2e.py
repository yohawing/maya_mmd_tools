"""Measure ordered-render CPU stages on a PMX in an isolated Maya GUI.

Forced refresh timings are not native playback FPS or GPU timestamp results.
The fixture adds a controlled cluster deformation; it is not a VMD/rig benchmark.
"""

import argparse
from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.viewport.maya_e2e_harness import run_maya_e2e  # noqa: E402
from tools.render_override.common import capture_view, require_requested_plugin  # noqa: E402
from tools.render_override.render_override_visual_gate import read_png_rgb  # noqa: E402

MARKER = "MMD RENDER PROFILE FINISHED"


def run_probe(config_path):
    """Yield to the GUI before measuring the explicitly sized single panel."""
    try:
        from PySide2.QtCore import QTimer
    except ImportError:
        from PySide6.QtCore import QTimer
    steps = probe_steps(json.loads(Path(config_path).read_text(encoding="utf-8")))

    def advance():
        try:
            next(steps)
        except StopIteration:
            return
        QTimer.singleShot(100, advance)

    advance()


def profile_events(cmds):
    """Retain inclusive CPU durations; nested events must not be added together."""
    events = defaultdict(list)
    count = cmds.profiler(query=True, eventCount=True)
    for index in range(count):
        name = cmds.profiler(query=True, eventIndex=index, eventName=True)
        if name.startswith("MMD.") or name.startswith("Vp2"):
            duration = cmds.profiler(query=True, eventIndex=index, eventDuration=True)
            events[name].append(duration / 1000.0)  # Maya reports microseconds.
    return {name: {"count": len(values), "totalMs": sum(values)}
            for name, values in events.items()}


def geometry_summary(meshes):
    """Fingerprint face corners so duplicated split-boundary vertices compare fairly."""
    from maya.api import OpenMaya as om

    corners = []
    shaded_corners = []
    vertices = 0
    polygons = 0
    for mesh in meshes:
        selection = om.MSelectionList()
        selection.add(mesh)
        fn = om.MFnMesh(selection.getDagPath(0))
        points = fn.getPoints(om.MSpace.kWorld)
        normals = fn.getNormals(om.MSpace.kWorld)
        _, normal_ids = fn.getNormalIds()
        _, indices = fn.getVertices()
        assert len(indices) == len(normal_ids)
        vertices += len(points)
        polygons += fn.numPolygons
        for index, normal_id in zip(indices, normal_ids):
            normal = normals[normal_id]
            point = points[index]
            corner = tuple(round(value, 6) for value in (point.x, point.y, point.z))
            corners.append(corner)
            shaded_corners.append(corner + tuple(round(value, 6) for value in normal))

    def digest(values):
        return hashlib.sha256(json.dumps(sorted(values)).encode("ascii")).hexdigest()

    return {"vertices": vertices, "polygons": polygons, "faceCorners": len(corners),
            "positionHash": digest(corners), "positionNormalHash": digest(shaded_corners)}


def probe_steps(config):
    from maya import cmds
    from maya.api import OpenMayaUI as omui
    from mmd_tools.io.mmd_importer import import_mmd_file

    out = Path(config["output"])
    report = {"status": "fail", "conditions": config, "cases": [],
              "scope": "forced refresh; controlled cluster deformation; inclusive CPU timings",
              "gpuTime": "not_run", "gpuDeformationApplicability": "not_run",
              "nativePlaybackFps": "not_run"}
    try:
        cmds.file(new=True, force=True)
        plugin = require_requested_plugin(cmds, Path(config["plugin"]), print)
        report.update(pluginSha256=hashlib.sha256(plugin.read_bytes()).hexdigest(),
                      modelSha256=hashlib.sha256(Path(config["model"]).read_bytes()).hexdigest(),
                      probeSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      mayaVersion=cmds.about(version=True), device=cmds.ogs(deviceInformation=True))
        cmds.loadPlugin(str(ROOT / "plug-ins/mmd_tools_plugin.py"), quiet=True)
        root = import_mmd_file(config["model"], options={
            "use_cpp_fast_load": True, "use_cpp_vp2_ownership": True,
            "import_morphs": False, "import_physics": False,
            "separate_meshes_by_material": config.get("splitMaterials", False),
        })
        assert root
        shapes = cmds.listRelatives(root, ad=True, type="mmdRenderShape", fullPath=True) or []
        assert shapes
        meshes = cmds.listRelatives(root, ad=True, type="mesh", fullPath=True) or []
        meshes = [mesh for mesh in meshes if not cmds.getAttr(mesh + ".intermediateObject")]
        assert meshes
        report["baselineGeometry"] = geometry_summary(meshes)
        if config.get("deformation", "partial") == "all":
            # One cluster translates all vertices in both import modes. This
            # isolates mesh partitioning; it is deliberately not a skin/VMD test.
            components = [mesh + ".vtx[*]" for mesh in meshes]
            deformed_meshes = meshes
            count = report["baselineGeometry"]["vertices"]
        else:
            mesh = max(meshes, key=lambda item: cmds.polyEvaluate(item, vertex=True))
            count = min(cmds.polyEvaluate(mesh, vertex=True), 1000)
            components = [mesh + f".vtx[0:{count - 1}]"]
            deformed_meshes = [mesh]
        _, handle = cmds.cluster(components, relative=True)
        extent = cmds.exactWorldBoundingBox(root)
        amplitude = max(extent[4] - extent[1], 0.01) * 0.01
        for old_panel in cmds.getPanel(type="modelPanel"):
            cmds.deleteUI(old_panel, panel=True)
        window = cmds.window(widthHeight=(660, 540))
        pane = cmds.paneLayout()
        panel = cmds.modelPanel(parent=pane)
        cmds.showWindow(window)
        camera, _ = cmds.camera()
        cmds.setAttr(camera + ".translateZ", 10)
        cmds.modelEditor(panel, edit=True, rendererName="vp2Renderer", camera=camera,
                         displayAppearance="smoothShaded", displayTextures=True, grid=False,
                         rendererOverrideName="mmdOrdered")
        cmds.select(root)
        cmds.viewFit(camera, fitFactor=0.9, animate=False)
        reference = config.get("reference")
        if reference:
            assert reference["pluginSha256"] == report["pluginSha256"]
            assert reference["modelSha256"] == report["modelSha256"]
            assert reference["mayaVersion"] == report["mayaVersion"]
            assert reference["baselineGeometry"]["positionHash"] == report["baselineGeometry"]["positionHash"]
            assert reference["baselineGeometry"]["positionNormalHash"] == report["baselineGeometry"]["positionNormalHash"]
            cmds.xform(camera, matrix=reference["cameraMatrix"], worldSpace=True)
            amplitude = reference["amplitude"]
        cmds.select(clear=True)
        cmds.setAttr("hardwareRenderingGlobals.multiSampleEnable", False)
        report["evaluationMode"] = cmds.evaluationManager(query=True, mode=True)
        report["meshCount"] = len(meshes)
        report["renderShapeCount"] = len(shapes)
        report["deformedMeshes"] = deformed_meshes
        report["deformedSourceVertices"] = count
        report["geometryFilterCount"] = len(cmds.ls(type="geometryFilter") or [])
        report["skinClusterCount"] = len(cmds.ls(type="skinCluster") or [])
        report["amplitude"] = amplitude
        yield
        cmds.refresh(force=True)
        view = omui.M3dView.getM3dViewFromModelPanel(panel)
        report["viewportSize"] = [view.portWidth(), view.portHeight()]
        if reference:
            assert reference["viewportSize"] == report["viewportSize"]
        assert len(cmds.getPanel(type="modelPanel")) == 1
        report["cameraMatrix"] = cmds.xform(camera, query=True, matrix=True, worldSpace=True)
        camera_x = cmds.getAttr(camera + ".translateX")
        baseline = read_png_rgb(capture_view(cmds, out / "baseline.png", panel, 640, 480))
        cmds.setAttr(handle + ".translateX", amplitude)
        cmds.refresh(force=True)
        report["deformedGeometry"] = geometry_summary(meshes)
        if reference:
            assert reference["deformedGeometry"]["positionHash"] == report["deformedGeometry"]["positionHash"]
            assert reference["deformedGeometry"]["positionNormalHash"] == report["deformedGeometry"]["positionNormalHash"]
        capture_view(cmds, out / "deformed.png", panel, 640, 480)
        cmds.setAttr(handle + ".translateX", 0)
        cmds.profiler(sampling=False)
        cmds.profiler(bufferSize=64)
        cmds.profiler(categoryName="MMD Render", categoryRecording=True)
        for repeat in range(config["repeats"]):
            for scenario in ("static", "camera", "deform"):
                # Alternate order to expose first-run and thermal bias.
                for enabled in ((False, True) if repeat % 2 == 0 else (True, False)):
                    cmds.setAttr(camera + ".translateX", camera_x)
                    cmds.setAttr(handle + ".translateX", 0)
                    cmds.modelEditor(panel, edit=True,
                                     rendererOverrideName="mmdOrdered" if enabled else "")
                    yield

                    def step(frame):
                        offset = math.sin(frame * 0.3) * amplitude
                        if scenario == "camera":
                            cmds.setAttr(camera + ".translateX", camera_x + offset)
                        elif scenario == "deform":
                            cmds.setAttr(handle + ".translateX", offset)
                        cmds.refresh(force=True)

                    for frame in range(config["warmup"]):
                        step(frame)
                    before = json.loads(cmds.mmdOrderedRenderWitness())
                    cmds.profiler(reset=True)
                    cmds.profiler(sampling=True)
                    durations = []
                    try:
                        for frame in range(config["frames"]):
                            start = time.perf_counter()
                            step(frame + config["warmup"])
                            durations.append((time.perf_counter() - start) * 1000)
                    finally:
                        cmds.profiler(sampling=False)
                    after = json.loads(cmds.mmdOrderedRenderWitness())
                    uploads = after["geometryUploads"] - before["geometryUploads"]
                    if enabled:
                        assert not after["error"] and after["drawCount"] > 0, after
                        assert uploads == (config["frames"] if scenario == "deform" else 0), uploads
                    assert [view.portWidth(), view.portHeight()] == report["viewportSize"]
                    label = f"{repeat}-{scenario}-{'on' if enabled else 'off'}"
                    cmds.profiler(output=str(out / (label + ".txt")))
                    events = profile_events(cmds)
                    if enabled:
                        # Ordered executes opaque and transparent phases once
                        # each per refresh. Reject skipped/truncated recordings.
                        assert events.get("MMD.Execute", {}).get("count") == 2 * config["frames"], events
                        assert events.get("Vp2ExecuteRenderOverride", {}).get("count") == config["frames"], events
                    else:
                        assert events.get("Vp2SceneRender", {}).get("count") == config["frames"], events
                        assert not any(name.startswith("MMD.") for name in events), events
                    report["cases"].append({"repeat": repeat, "scenario": scenario,
                        "enabled": enabled, "frameMs": durations,
                        "medianMs": statistics.median(durations), "events": events,
                        "geometryUploads": uploads, "drawCount": after["drawCount"],
                        "casterDrawCount": after["casterDrawCount"]})
        cmds.setAttr(camera + ".translateX", camera_x)
        cmds.setAttr(handle + ".translateX", 0)
        cmds.modelEditor(panel, edit=True, rendererOverrideName="mmdOrdered")
        yield
        restored = read_png_rgb(capture_view(cmds, out / "restored.png", panel, 640, 480))
        assert restored == baseline, "camera/deformation restore changed pixels"
        report["restoredPixelsEqual"] = True
        if config.get("inspectNormals", False):
            from tools.render_override.normal_analysis import inspect_normals

            report["normalAnalysis"] = inspect_normals(meshes)
        report["status"] = "pass"
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        cmds.profiler(sampling=False)
        (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (out / "probe.log").write_text(MARKER + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", choices=("2024", "2026"), default="2024")
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=7753)
    parser.add_argument("--frames", type=int, default=12)
    parser.add_argument("--warmup", type=int, default=4)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--split-materials", action="store_true")
    parser.add_argument("--inspect-normals", action="store_true",
                        help="Inspect restored mesh normals after viewport timing (read-only)")
    parser.add_argument("--deformation", choices=("partial", "all"), default="partial")
    parser.add_argument("--reference-report", type=Path,
                        help="Match an earlier same-version run's camera, size and geometry")
    args = parser.parse_args()
    out = args.out_dir.resolve()
    if not out.is_relative_to(ROOT / "build") or min(args.frames, args.warmup, args.repeats) < 1:
        parser.error("use an output directory inside build and positive sample counts")
    out.mkdir(parents=True, exist_ok=True)
    plugin = ROOT / f"plug-ins/{args.maya}/Release/mmd_tools_cpp.mll"
    config = out / "config.json"
    reference = None
    if args.reference_report:
        if args.deformation != "all":
            parser.error("--reference-report requires --deformation all; partial vertex IDs differ after splitting")
        reference = json.loads(args.reference_report.read_text(encoding="utf-8"))
        if reference.get("status") != "pass" or reference["conditions"].get("deformation") != args.deformation:
            parser.error("reference must be a successful run with the same deformation")
        reference = {key: reference[key] for key in (
            "pluginSha256", "modelSha256", "mayaVersion", "baselineGeometry",
            "deformedGeometry", "cameraMatrix", "viewportSize", "amplitude")}
    config.write_text(json.dumps({"model": str(args.model.resolve()), "plugin": str(plugin),
        "output": str(out), "frames": args.frames, "warmup": args.warmup,
        "repeats": args.repeats, "splitMaterials": args.split_materials,
        "deformation": args.deformation, "reference": reference,
        "inspectNormals": args.inspect_normals}), encoding="utf-8")
    report = run_maya_e2e(
        project_root=ROOT, version=args.maya, out_dir=out, port=args.port, timeout=360,
        log_path=out / "probe.log", report_path=out / "report.json",
        command=("from tools.render_override.profile_e2e import run_probe\n"
                 f"run_probe({str(config)!r})"),
        marker=MARKER, send_label="mmd-render-profile",
        stale_paths=(out / "probe.log", out / "report.json"),
        env_overrides={"MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11",
                       "MAYA_SKIP_USERSETUP_PY": "1", "MMD_TOOLS_CPP_PLUGIN": str(plugin),
                       "PATH": str(plugin.parent) + os.pathsep + os.environ.get("PATH", "")},
    )
    print(json.dumps({key: value for key, value in report.items() if key != "cases"}, indent=2))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
