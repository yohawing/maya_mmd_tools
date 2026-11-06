"""Reproduce leg/skirt mesh intersections through production Maya live physics."""
# ruff: noqa: E402
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
MARKER = "SKIRT_PENETRATION_COMPLETE"


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def prepare_output(path):
    """Require an empty run directory so old evidence cannot survive a failed attempt."""
    if path.exists() and any(path.iterdir()):
        raise ValueError("Output directory contains an earlier run; choose a new --out-dir: " + str(path))
    path.mkdir(parents=True, exist_ok=True)


def mesh_regions(root):
    """Classify actual Maya triangles by skin weights, independent of PMX vertex order."""
    import numpy as np
    from maya import cmds
    from maya.api import OpenMaya as om, OpenMayaAnim as oma

    regions = []
    for shape in cmds.listRelatives(root, ad=True, type="mesh", fullPath=True) or []:
        if cmds.getAttr(shape + ".intermediateObject"):
            continue
        skins = cmds.ls(cmds.listHistory(shape) or [], type="skinCluster")
        if not skins:
            continue
        sel = om.MSelectionList()
        sel.add(shape)
        dag = sel.getDagPath(0)
        mesh = om.MFnMesh(dag)
        sel.add(skins[0])
        skin = oma.MFnSkinCluster(sel.getDependNode(1))
        influences = skin.influenceObjects()
        names = [cmds.getAttr(p.fullPathName() + ".mmd_bone_name")
                 if cmds.attributeQuery("mmd_bone_name", node=p.fullPathName(), exists=True)
                 else p.partialPathName() for p in influences]
        skirt = [i for i, n in enumerate(names) if "スカート" in n]
        leg = [i for i, n in enumerate(names) if any(s in n for s in ("足", "ひざ")) and "ＩＫ" not in n and "IK" not in n]
        comp = om.MFnSingleIndexedComponent()
        obj = comp.create(om.MFn.kMeshVertComponent)
        comp.addElements(range(mesh.numVertices))
        values, count = skin.getWeights(dag, obj)
        weights = np.asarray(values).reshape((-1, count))
        counts, indices = mesh.getTriangles()
        triangles = np.asarray(indices, dtype=int).reshape((-1, 3))
        polygons = np.repeat(np.arange(mesh.numPolygons), np.asarray(counts))
        skirt_v = weights[:, skirt].sum(axis=1) > 0.5
        leg_v = weights[:, leg].sum(axis=1) > 0.5
        skirt_mask = np.all(skirt_v[triangles], axis=1)
        leg_mask = np.all(leg_v[triangles], axis=1)
        skirt_t, leg_t = triangles[skirt_mask], triangles[leg_mask]
        if len(skirt_t) or len(leg_t):
            regions.append((shape, mesh, skirt_t, leg_t,
                            {"skirt": np.unique(polygons[skirt_mask]).tolist(),
                             "legs": np.unique(polygons[leg_mask]).tolist()}))
    if not sum(len(r[2]) for r in regions) or not sum(len(r[3]) for r in regions):
        raise RuntimeError("No weighted skirt or leg triangles found")
    return regions


def crossings(edges, triangles, epsilon=1e-6):
    """Return strict segment/triangle crossings; tangency and coplanar overlap excluded."""
    import numpy as np

    hits = []
    if not len(triangles):
        return hits
    low, high = triangles.min(axis=1), triangles.max(axis=1)
    for a, b in edges:
        candidates = np.all(high >= np.minimum(a, b), axis=1) & np.all(low <= np.maximum(a, b), axis=1)
        tri = triangles[candidates]
        if not len(tri):
            continue
        direction = b - a
        e1, e2 = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
        p = np.cross(direction, e2)
        det = np.einsum("ij,ij->i", e1, p)
        good = np.abs(det) > epsilon
        inv = np.zeros_like(det)
        inv[good] = 1 / det[good]
        delta = a - tri[:, 0]
        u = np.einsum("ij,ij->i", delta, p) * inv
        q = np.cross(delta, e1)
        v = np.einsum("j,ij->i", direction, q) * inv
        t = np.einsum("ij,ij->i", e2, q) * inv
        valid = good & (u > epsilon) & (v > epsilon) & (u + v < 1 - epsilon) & (t > epsilon) & (t < 1 - epsilon)
        hits.extend((a + t[valid, None] * direction).tolist())
    return hits


def sample(regions, dump_path=None):
    import numpy as np
    from maya.api import OpenMaya as om

    skirt, legs = [], []
    for _, mesh, skirt_indices, leg_indices, _ in regions:
        points = np.asarray([(p.x, p.y, p.z) for p in mesh.getPoints(om.MSpace.kWorld)])
        skirt.extend(points[skirt_indices])
        legs.extend(points[leg_indices])
    skirt, legs = np.asarray(skirt), np.asarray(legs)
    if not np.isfinite(skirt).all() or not np.isfinite(legs).all():
        raise RuntimeError("Non-finite evaluated mesh vertices")
    if dump_path is not None:
        np.savez_compressed(str(dump_path), skirt=skirt, legs=legs)
    def edges(tri):
        return tri[:, ((0, 1), (1, 2), (2, 0)), :].reshape((-1, 2, 3))
    hits = crossings(edges(skirt), legs) + crossings(edges(legs), skirt)
    # Shared edges can generate duplicate hits; quantize only the diagnostic count.
    unique = sorted(set(tuple(round(v, 5) for v in p) for p in hits))
    return {"crossings": len(unique), "points": unique}


def diagnostic_snapshot(regions, root, out, frame, panel, camera):
    """Export a frozen evaluated mesh copy; colors are diagnostic, source shading untouched."""
    from maya import cmds
    from tools.render_override.common import capture_view

    copies, shaders = [], []
    try:
        groups = {}
        for name, color in (("other", (0.25, 0.25, 0.25)), ("skirt", (0.1, 0.45, 0.95)), ("legs", (1, 0.35, 0.05))):
            shader = cmds.shadingNode("lambert", asShader=True, name="diagnostic_" + name)
            sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=shader + "SG")
            shaders.extend([sg, shader])
            cmds.setAttr(shader + ".color", *color, type="double3")
            cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader")
            groups[name] = sg
        for shape, _, _, _, faces in regions:
            transform = cmds.listRelatives(shape, parent=True, fullPath=True)[0]
            copy = cmds.duplicate(transform, name="evaluated_frame_%d" % frame, returnRootsOnly=True)[0]
            copy = cmds.parent(copy, world=True)[0]
            copies.append(copy)
            cmds.delete(copy, constructionHistory=True)
            cmds.sets(copy, edit=True, forceElement=groups["other"])
            for name, indices in faces.items():
                if indices:
                    cmds.sets([copy + ".f[%d]" % i for i in indices], edit=True, forceElement=groups[name])
        cmds.hide(root)
        cmds.select(copies + [camera], replace=True)
        cmds.file(str(out / ("frozen-%04d.ma" % frame)), exportSelected=True, type="mayaAscii", force=True)
        cmds.select(clear=True)
        cmds.refresh(force=True)
        return str(capture_view(cmds, out / ("diagnostic-%04d.png" % frame), panel, 960, 720, frame))
    finally:
        cmds.showHidden(root)
        if copies:
            cmds.delete(copies)
        if shaders:
            cmds.delete(shaders)


def probe(config_path):
    from maya import cmds
    from maya.api import OpenMaya as om
    from tests.common.maya_plugin_setup import load_mmd_tools_plugin
    from mmd_tools.io.mmd_importer import import_mmd_file
    from tools.render_override.common import capture_view, make_parity_camera

    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    out = Path(config["out"])
    report = {"status": "fail", "config": config, "samples": [], "errors": [], "messages": [],
              "measurement_scope": "Frame 0 plus sample_start..end at stride intervals; not a full-motion clearance gate"}
    def message(text, kind, unused):
        if kind in (om.MCommandMessage.kError, om.MCommandMessage.kWarning):
            report["messages"].append({"kind": int(kind), "text": text})
    callback = om.MCommandMessage.addCommandOutputCallback(message)
    try:
        load_mmd_tools_plugin(ROOT, cmds_module=cmds)
        cmds.file(new=True, force=True)
        cmds.currentUnit(time="ntsc")
        cmds.evaluationManager(mode="off")
        cmds.currentTime(0)
        options = {"setup_rig": True, "import_physics": True, "use_namespace": True,
                   "create_mmd_shaders": False, "scale": 1.0}
        report["pmx_options"] = options
        report["maya"] = cmds.about(version=True)
        report["units"] = {"time": cmds.currentUnit(q=True, time=True), "linear": cmds.currentUnit(q=True, linear=True)}
        report["evaluation"] = cmds.evaluationManager(q=True, mode=True)
        root = import_mmd_file(config["pmx"], options=options)
        if not root:
            raise RuntimeError("PMX import failed")
        cmds.select(root, replace=True)
        if not import_mmd_file(config["vmd"], options={"target_model": root, "pmx_path": config["pmx"], "bake_mode": False}):
            raise RuntimeError("VMD import failed")
        from mmd_tools.core.native.mmd_anim_runtime import get_runtime_library_path
        runtime = Path(get_runtime_library_path())
        report["loaded_runtime"] = {"path": str(runtime), "sha256": hashlib.sha256(runtime.read_bytes()).hexdigest()}
        worlds = cmds.ls(type="mmdPhysicsWorldShape")
        solvers = cmds.ls(type="mmdPhysicsSolver")
        if len(worlds) != 1 or len(solvers) != 1:
            raise RuntimeError("Expected exactly one live physics world and solver")
        cmds.currentTime(0)
        cmds.setAttr(worlds[0] + ".enable", True)
        report["world"] = {a: cmds.getAttr(worlds[0] + "." + a)
                           for a in ("enable", "gravity", "fixedTimestep", "maxSubsteps")}
        regions = mesh_regions(root)
        report["regions"] = [{"mesh": s, "skirt_triangles": len(st), "leg_triangles": len(lt)} for s, _, st, lt, _ in regions]
        selection = om.MSelectionList()
        selection.add(solvers[0])
        if not cmds.getAttr(solvers[0] + ".outSolved"):
            raise RuntimeError("Live solver initialization failed")
        solver_node = om.MFnDependencyNode(selection.getDependNode(0)).userNode()
        if solver_node is None or solver_node._world is None:
            raise RuntimeError("Live solver world unavailable for diagnostic readback")
        report["body_shapes"] = dict(solver_node._rb_shape_paths)
        from mmd_tools.core.physics_dag_descriptor import build_descriptors_from_dag
        descriptors = build_descriptors_from_dag(root, bone_joints=solver_node._bone_joints,
                                                bone_count=len(solver_node._bone_joints))
        def fields(desc):
            return {name: list(getattr(desc, name)) if hasattr(getattr(desc, name), "__len__")
                    else getattr(desc, name) for name, _ in desc._fields_}
        report["body_descriptors"] = [fields(d) for d in descriptors.rigid_bodies]
        report["joint_descriptors"] = [fields(d) for d in descriptors.joints]
        panel = (cmds.getPanel(type="modelPanel") or [])[0]
        camera = make_parity_camera(cmds, config.get("camera", {"position": [30, 16, 45], "target": [0, 10, 0], "fov": 32}))
        cmds.lookThru(panel, camera)
        cmds.modelEditor(panel, e=True, rendererName="vp2Renderer", displayAppearance="smoothShaded",
                         displayTextures=True, allObjects=False, polymeshes=True, grid=False)
        cmds.select(clear=True)
        for frame in range(config["end"] + 1):
            cmds.currentTime(frame, edit=True)
            solved = cmds.getAttr(solvers[0] + ".outSolved")
            status = cmds.getAttr(solvers[0] + ".outStatus")
            if not solved:
                raise RuntimeError("Physics did not solve at %s: %s" % (frame, status))
            if frame == 0 or (frame >= config["sample_start"] and frame % config["stride"] == 0):
                capture = frame in config.get("capture_frames", list(range(0, config["end"] + 1, 30)))
                dump = out / ("mesh-%04d.npz" % frame) if capture else None
                row = sample(regions, dump)
                row.update(frame=frame, solver_status=status)
                row["rigid_body_readback_mmd"] = solver_node._world.copy_rigidbody_states()
                if row["rigid_body_readback_mmd"] is None:
                    raise RuntimeError("Native body readback failed")
                report["samples"].append(row)
                if capture:
                    row["kinematic_pose_signature"] = solver_node._last_kinematic_pose_signature
                    row["instance_world_matrices_mmd"] = solver_node._instance.get_world_matrices()
                    row["maya_joint_world_matrices"] = {
                        str(i): cmds.getAttr(j + ".worldMatrix[0]")
                        for i, j in enumerate(solver_node._bone_joints) if j}
                    cmds.refresh(force=True)
                    row["image"] = str(capture_view(cmds, out / ("frame-%04d.png" % frame), panel, 960, 720, frame))
                    row["diagnostic_image"] = diagnostic_snapshot(regions, root, out, frame, panel, camera)
                    cmds.file(rename=str(out / ("frame-%04d.ma" % frame)))
                    cmds.file(save=True, type="mayaAscii", force=True)
                write(out / "report.json", report)
                (out / "progress.txt").write_text(str(frame), encoding="ascii")
        cmds.file(rename=str(out / "reproduction.ma"))
        cmds.file(save=True, type="mayaAscii", force=True)
        report["status"] = "pass"
        report["reproduction"] = "mesh_crossings_observed" if any(r["crossings"] for r in report["samples"]) else "not_reproduced"
    except Exception:
        report["errors"].append(traceback.format_exc())
    finally:
        om.MMessage.removeCallback(callback)
        write(out / "report.json", report)
        (out / "run.log").write_text(MARKER + "\n", encoding="utf-8")


def main():
    from tests.viewport.maya_e2e_harness import run_maya_e2e
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "tests/data/physics/skirt_leg_real.fixture.json", help="UTF-8 JSON with pmx/vmd paths")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "build/reports/skirt-penetration")
    parser.add_argument("--end", type=int, default=150)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--sample-start", type=int, default=110)
    parser.add_argument("--assert-clear", action="store_true", help="Regression gate: fail on any measured crossing")
    parser.add_argument("--port", type=int, default=7756)
    args = parser.parse_args()
    if args.stride < 1 or args.end < 0 or not 0 <= args.sample_start <= args.end:
        parser.error("Require stride >= 1 and 0 <= sample-start <= end")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    out = args.out_dir.resolve()
    prepare_output(out)
    config.update(out=str(out), end=args.end, stride=args.stride, sample_start=args.sample_start,
                  hashes={k: hashlib.sha256(Path(config[k]).read_bytes()).hexdigest() for k in ("pmx", "vmd")})
    if config.get("expected_hashes") and config["expected_hashes"] != config["hashes"]:
        raise RuntimeError("Asset hashes do not match the reproduction fixture")
    plugin = ROOT / "plug-ins/2024/Release/mmd_tools_cpp.mll"
    runtime = ROOT / "mmd_tools/native/win64/mmd_runtime_ffi.dll"
    config["binary_sha256"] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in (plugin, runtime)}
    config["driver_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    write(out / "config.json", config)
    command = ("from maya import cmds\nfrom tools.maya_gui.skirt_penetration import probe\n"
               f"cmds.evalDeferred(lambda: probe({str(out / 'config.json')!r}), lowestPriority=True)")
    result = run_maya_e2e(project_root=ROOT, version="2024", out_dir=out, port=args.port, timeout=1800,
                          log_path=out / "run.log", report_path=out / "report.json", command=command,
                          marker=MARKER, send_label="skirt-penetration",
                          stale_paths=(out / "run.log", out / "report.json"),
                          env_overrides={"MAYA_SKIP_USERSETUP_PY": "1", "MMD_TOOLS_CPP_PLUGIN": str(plugin),
                                         "PATH": str(plugin.parent) + os.pathsep + os.environ.get("PATH", "")})
    print(json.dumps({k: result.get(k) for k in ("status", "reproduction", "measurement_scope", "errors")}))
    return 0 if result["status"] == "pass" and not (args.assert_clear and any(s["crossings"] for s in result["samples"])) else 1


if __name__ == "__main__":
    raise SystemExit(main())
