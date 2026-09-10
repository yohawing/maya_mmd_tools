"""Profile a saved scene in an isolated Maya GUI without modifying its source.

Static samples force refresh; timeline samples use currentTime's evaluation/draw.
Neither is native playback FPS or a GPU timestamp measurement.
"""

import argparse
import hashlib
import json
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
from tools.render_override.profile_e2e import profile_events  # noqa: E402

MARKER = "MMD SCENE PROFILE FINISHED"


def run_probe(config_path):
    """Run each case between GUI event-loop turns in the isolated process."""
    from PySide6.QtCore import QTimer

    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    steps = probe_steps(config)

    def advance():
        try:
            next(steps)
        except StopIteration:
            return
        QTimer.singleShot(100, advance)

    advance()


def probe_steps(config):
    from maya import cmds
    from maya.api import OpenMayaUI as omui

    out = Path(config["output"])
    report = {"status": "fail", "conditions": config, "cases": [],
              "scope": __doc__, "gpuTime": "not_run"}
    try:
        plugin = require_requested_plugin(cmds, config["plugin"], print)
        cmds.loadPlugin(str(ROOT / "plug-ins/mmd_tools_plugin.py"), quiet=True)
        cmds.file(config["scene"], open=True, force=True, prompt=False)
        require_requested_plugin(cmds, config["plugin"], print)
        report["pluginSha256"] = hashlib.sha256(plugin.read_bytes()).hexdigest()
        report["sceneSha256"] = hashlib.sha256(Path(config["scene"]).read_bytes()).hexdigest()
        report["probeSha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        report["device"] = cmds.ogs(deviceInformation=True)
        cmds.evaluationManager(mode="off")
        report["evaluation"] = cmds.evaluationManager(query=True, mode=True)
        report["counts"] = {kind: len(cmds.ls(type=kind) or []) for kind in
                            ("mesh", "mmdRenderShape", "skinCluster", "joint", "animCurve")}
        assert report["counts"]["mmdRenderShape"] > 0, report["counts"]
        for panel in cmds.getPanel(type="modelPanel"):
            cmds.deleteUI(panel, panel=True)
        window = cmds.window(widthHeight=(1000, 800))
        pane = cmds.paneLayout()
        panel = cmds.modelPanel(parent=pane)
        cmds.showWindow(window)
        cmds.modelEditor(panel, edit=True, camera="persp", rendererName="vp2Renderer",
                         rendererOverrideName="mmdOrdered", displayAppearance="smoothShaded",
                         displayTextures=True, grid=False)
        cmds.select(clear=True)
        cmds.currentTime(config["start"])
        yield
        view = omui.M3dView.getM3dViewFromModelPanel(panel)
        report["viewportSize"] = [view.portWidth(), view.portHeight()]
        report["cameraMatrix"] = cmds.xform("persp", query=True, worldSpace=True, matrix=True)
        capture_view(cmds, out / "baseline.png", panel, 960, 720, config["start"])
        cmds.profiler(sampling=False)
        cmds.profiler(bufferSize=64)
        try:
            cmds.profiler(categoryName="MMD Render", categoryRecording=True)
            report["mmdCategory"] = "enabled"
        except RuntimeError as error:
            report["mmdCategory"] = str(error)
        for repeat in range(3):
            for scenario in ("static", "timeline"):
                for enabled in ((False, True) if repeat % 2 == 0 else (True, False)):
                    cmds.modelEditor(panel, edit=True,
                                     rendererOverrideName="mmdOrdered" if enabled else "")
                    cmds.currentTime(config["start"])
                    yield

                    def step(frame):
                        if scenario == "timeline":
                            cmds.currentTime(config["start"] + frame)
                        else:
                            cmds.refresh(force=True)

                    for frame in range(4):
                        step(frame)
                    cmds.profiler(reset=True)
                    cmds.profiler(sampling=True)
                    durations = []
                    try:
                        for frame in range(config["frames"]):
                            start = time.perf_counter()
                            step(frame + 4)
                            durations.append((time.perf_counter() - start) * 1000)
                    finally:
                        cmds.profiler(sampling=False)
                    events = profile_events(cmds)
                    witness = json.loads(cmds.mmdOrderedRenderWitness()) if enabled else None
                    if enabled:
                        assert not witness["error"] and witness["drawCount"] > 0, witness
                    name = "Vp2ExecuteRenderOverride" if enabled else "Vp2SceneRender"
                    assert events.get(name, {}).get("count") == config["frames"], events
                    assert [view.portWidth(), view.portHeight()] == report["viewportSize"]
                    cmds.profiler(output=str(out / f"{repeat}-{scenario}-{enabled}.txt"))
                    report["cases"].append({"repeat": repeat, "scenario": scenario,
                        "enabled": enabled, "frameMs": durations,
                        "medianMs": statistics.median(durations), "events": events,
                        "witness": witness})
        report["status"] = "pass"
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        cmds.profiler(sampling=False)
        (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (out / "probe.log").write_text(MARKER + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, required=True)
    parser.add_argument("--plugin", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--frames", type=int, default=12)
    args = parser.parse_args()
    out = args.out_dir.resolve()
    if not out.is_relative_to(ROOT / "build") or args.frames < 1:
        parser.error("use an output directory inside build and positive frames")
    for path in (args.scene, args.plugin):
        if not path.is_file():
            parser.error(f"missing input: {path}")
    out.mkdir(parents=True, exist_ok=True)
    plugin = args.plugin.resolve()
    config = out / "config.json"
    config.write_text(json.dumps({"scene": str(args.scene.resolve()), "plugin": str(plugin),
        "output": str(out), "start": args.start, "frames": args.frames}), encoding="utf-8")
    report = run_maya_e2e(
        project_root=ROOT, version="2026", out_dir=out, port=7757, timeout=600,
        log_path=out / "probe.log", report_path=out / "report.json",
        command=("from tools.render_override.scene_profile import run_probe\n"
                 f"run_probe({str(config)!r})"),
        marker=MARKER, send_label="mmd-scene-profile",
        stale_paths=(out / "probe.log", out / "report.json"),
        env_overrides={"MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11",
                       "MAYA_SKIP_USERSETUP_PY": "1", "MMD_TOOLS_CPP_PLUGIN": str(plugin),
                       "PATH": str(plugin.parent) + os.pathsep + os.environ.get("PATH", "")},
    )
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
