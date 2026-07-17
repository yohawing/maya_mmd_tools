"""Versioned Maya GUI evidence for the authoring collider display contract."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.common import maya_commandport

COMPLETION_MARKER = "//-- COLLIDER AUTHORING CAPTURE FINISHED --//"
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
LOGGER = logging.getLogger(__name__)


def _resolve_playblast(path: Path) -> Path:
    candidates = [path, path.with_name(f"{path.stem}.0000{path.suffix}")]
    candidates.extend(sorted(path.parent.glob(f"{path.stem}.*{path.suffix}")))
    return next((candidate for candidate in candidates if candidate.is_file()), path)


def run_capture(output_dir: str, log_path: str) -> None:
    """Run inside Maya GUI and write four viewport captures plus a report."""
    import traceback

    from maya import cmds
    import maya.api.OpenMaya as om

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    log = Path(log_path)

    def note(message):
        with log.open("a", encoding="utf-8") as stream:
            stream.write(str(message) + "\n")

    report = {
        "schemaVersion": 1,
        "kind": "collider-authoring-capture",
        "mayaVersion": cmds.about(version=True),
        "mayaApiVersion": cmds.about(apiVersion=True),
        "captures": [],
        "checks": {},
        "errors": [],
    }

    def capture(name, panel):
        requested = out / f"{name}.png"
        cmds.currentTime(1)
        cmds.refresh(force=True)
        cmds.playblast(
            format="image", filename=str(requested), compression="png", frame=1,
            widthHeight=(720, 480), viewer=False, offScreen=False,
            showOrnaments=False, percent=100, forceOverwrite=True,
            editorPanelName=panel,
        )
        actual = _resolve_playblast(requested)
        if not actual.is_file() or actual.stat().st_size == 0:
            raise RuntimeError(f"capture missing or empty: {actual}")
        report["captures"].append({"name": name, "path": str(actual), "bytes": actual.stat().st_size})

    try:
        note("begin")
        cmds.file(new=True, force=True)
        note("scene-created")
        plugin = _ROOT / "mmd_tools" / "plugin_main.py"
        if not cmds.pluginInfo(str(plugin), query=True, loaded=True):
            cmds.loadPlugin(str(plugin), quiet=True)
        note("plugin-loaded")

        from mmd_tools.core.collider_authoring import set_collider_authoring_pose

        group = cmds.createNode("transform", name="ColliderEvidence")
        cases = (
            ("sphere", 0, (-4.0, 0.0, 0.0), (1.25, 1.0, 1.0), 0, 0),
            ("box", 1, (0.0, 0.0, 0.0), (2.5, 3.0, 1.5), 7, 1),
            ("capsule", 2, (4.0, 0.0, 0.0), (1.0, 3.0, 1.0), 15, 2),
        )
        nodes = {}
        for name, shape_type, position, size, collision_group, mode in cases:
            transform = cmds.createNode("transform", name=name, parent=group)
            shape = cmds.createNode("mmdRigidBodyShape", name=f"{name}Shape", parent=transform)
            cmds.setAttr(f"{shape}.shapeType", shape_type)
            cmds.setAttr(f"{shape}.shapeSize", *size, type="double3")
            cmds.setAttr(f"{shape}.collisionGroup", collision_group)
            cmds.setAttr(f"{shape}.physicsMode", mode)
            set_collider_authoring_pose(transform, shape, position, (0.0, 0.0, 0.0))
            nodes[name] = (transform, shape)
        note("colliders-created")

        panel = cmds.getPanel(withFocus=True)
        if cmds.getPanel(typeOf=panel) != "modelPanel":
            panel = (cmds.getPanel(type="modelPanel") or [None])[0]
        if not panel:
            raise RuntimeError("no modelPanel available in Maya GUI")
        cmds.modelEditor(
            panel, edit=True, rendererName="vp2Renderer", allObjects=False,
            locators=True, grid=False, manipulators=False, selectionHiliteDisplay=True,
        )
        cmds.modelPanel(panel, edit=True, camera="persp")
        cmds.setAttr("persp.translate", 0.0, 1.0, 22.0, type="double3")
        cmds.setAttr("persp.rotate", 0.0, 0.0, 0.0, type="double3")
        cmds.setAttr("perspShape.nearClipPlane", 0.1)
        cmds.setAttr("perspShape.farClipPlane", 1000.0)
        note("panel-ready")

        capsule, capsule_shape = nodes["capsule"]
        cmds.setAttr(f"{capsule_shape}.shapeSizeY", 4.0)
        cmds.setAttr(f"{capsule_shape}.positionY", 1.0)
        report["checks"]["editedCapsuleTotalHeight"] = (
            cmds.getAttr(f"{capsule_shape}.shapeSizeY")
            + 2.0 * cmds.getAttr(f"{capsule_shape}.shapeSizeX")
        )
        capture("01-edited", panel)
        note("edited-captured")

        box, _box_shape = nodes["box"]
        cmds.setAttr(f"{box}.visibility", False)
        report["checks"]["boxHidden"] = not cmds.getAttr(f"{box}.visibility")
        capture("02-visibility", panel)
        note("visibility-captured")
        cmds.setAttr(f"{box}.visibility", True)

        cmds.select(capsule, replace=True)
        report["checks"]["selection"] = cmds.ls(selection=True, long=True)
        capture("03-selected", panel)
        note("selection-captured")
        cmds.select(clear=True)

        scene_path = out / "collider-authoring-evidence.ma"
        cmds.file(rename=str(scene_path))
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(new=True, force=True)
        cmds.file(str(scene_path), open=True, force=True)
        capsule_shape = (cmds.ls("capsuleShape", long=True) or [None])[0]
        if not capsule_shape:
            raise RuntimeError("capsule shape missing after reopen")
        selection = om.MSelectionList()
        selection.add(capsule_shape)
        draw_matrix = selection.getDagPath(0).inclusiveMatrix()
        authoring_matrix = om.MMatrix(cmds.getAttr(f"{capsule_shape}.authoringMatrix"))
        report["checks"]["reopenMatrixMaxError"] = max(
            abs(draw_matrix[index] - authoring_matrix[index]) for index in range(16)
        )
        report["checks"]["reopenCapsuleTotalHeight"] = (
            cmds.getAttr(f"{capsule_shape}.shapeSizeY")
            + 2.0 * cmds.getAttr(f"{capsule_shape}.shapeSizeX")
        )
        capture("04-reopened", panel)
        note("reopen-captured")
    except Exception as exc:
        report["errors"].append({"error": str(exc), "traceback": traceback.format_exc()})
    finally:
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        note(COMPLETION_MARKER)


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--port", type=int, default=7730)
    parser.add_argument("--timeout", type=int, default=240)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output = Path(args.out).resolve()
    output.mkdir(parents=True, exist_ok=True)
    log = output / "capture.log"
    report_path = output / "report.json"
    for stale in (log, report_path):
        if stale.exists():
            stale.unlink()

    proc = maya_commandport.launch_maya(
        version=args.maya, project_root=_ROOT, output_dir=output,
        port=args.port, launch_mode="explorer",
        env_overrides={"MMD_TOOLS_SKIP_SHADER_OVERRIDE": "1"},
    )
    try:
        maya_commandport.wait_for_port(args.port, args.timeout, proc)
        time.sleep(10.0)
        code = (
            "from tests.viewport.collider_authoring_capture import run_capture; "
            f"run_capture({str(output)!r}, {str(log)!r})"
        )
        maya_commandport.send_python(args.port, code, label="<collider-authoring-capture>")
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            if log.is_file() and COMPLETION_MARKER in log.read_text(encoding="utf-8", errors="replace"):
                break
            time.sleep(0.5)
        else:
            raise TimeoutError(f"capture did not complete: {log}")
    finally:
        if proc is None:
            maya_commandport.quit_maya(args.port)
        else:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except Exception:
                proc.kill()
                proc.wait(timeout=20)
        maya_commandport.close_process_logs(proc)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report["errors"] or len(report["captures"]) != 4:
        raise RuntimeError(f"collider capture failed: {report}")
    LOGGER.info("Maya %s collider evidence: %s", report["mayaVersion"], report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
