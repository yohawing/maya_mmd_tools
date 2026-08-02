"""Versioned Maya GUI evidence for the authoring collider display contract."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
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


def _semantic_failures(report: dict, tolerance: float = 1.0e-9) -> list[str]:
    checks = report.get("checks", {})
    failures = []

    def finite_value(name):
        try:
            value = float(checks[name])
        except (KeyError, TypeError, ValueError):
            failures.append(f"{name} is missing or not numeric")
            return None
        if not math.isfinite(value):
            failures.append(f"{name} is not finite")
            return None
        return value

    for name in ("editedCapsuleTotalHeight", "reopenCapsuleTotalHeight"):
        value = finite_value(name)
        if value is not None and abs(value - 6.0) > tolerance:
            failures.append(f"{name} != 6.0")
    if checks.get("boxHidden") is not True:
        failures.append("boxHidden is not true")
    if checks.get("pluginInitializeComplete") is not True:
        failures.append("plugin initialization did not complete")
    if checks.get("unselectedSelection") != []:
        failures.append("unselected capture contains a selection")
    if checks.get("unselectedDisplayStatus") != 2:
        failures.append("unselected display status is not kDormant")
    selection = checks.get("selectedSelection") or []
    if len(selection) != 1 or not str(selection[0]).endswith("|ColliderEvidence|capsule"):
        failures.append("selected capture is not the capsule transform")
    if checks.get("selectedDisplayStatus") not in (0, 4, 7, 8):
        failures.append("selected display status is not active/lead/hilite")
    matrix_error = finite_value("reopenMatrixMaxError")
    if matrix_error is not None and matrix_error > tolerance:
        failures.append(f"reopenMatrixMaxError > {tolerance}")
    for name in ("realRestMatrixMaxError", "followOffsetMaxError", "followBboxCenterMaxError"):
        value = finite_value(name)
        if value is not None and value > 1.0e-5:
            failures.append(f"{name} > 1e-5")
    bone_delta = finite_value("boneAnimationMatrixMaxDelta")
    if bone_delta is not None and bone_delta <= 1.0e-4:
        failures.append("bone did not animate relative to the model root")
    if checks.get("boundFollowConstraint") is not True:
        failures.append("bound collider has no authoring follow constraint")
    if checks.get("rawPoseUnchanged") is not True:
        failures.append("raw PMX pose changed during follow capture")
    if checks.get("physicsModeLineStyles") != [0, 0, 0]:
        failures.append("physics modes do not all use solid wire")
    if not isinstance(checks.get("realColliderCount"), int) or checks["realColliderCount"] <= 0:
        failures.append("real PMX has no captured colliders")
    if checks.get("realCollidersVisible") is not True:
        failures.append("real PMX colliders are not visible")
    if not isinstance(checks.get("realMeshCount"), int) or checks["realMeshCount"] <= 0:
        failures.append("real PMX has no captured mesh")
    if checks.get("realMeshVisible") is not True:
        failures.append("real PMX mesh is not visible")
    return failures


def run_capture(output_dir: str, log_path: str) -> None:
    """Run inside Maya GUI and write authoring plus real-PMX follow captures."""
    import traceback

    from maya import cmds
    import maya.api.OpenMaya as om
    import maya.api.OpenMayaRender as omr

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

    def capture(name, panel, frame=1):
        requested = out / f"{name}.png"
        cmds.currentTime(frame)
        cmds.refresh(force=True)
        cmds.playblast(
            format="image", filename=str(requested), compression="png", frame=frame,
            widthHeight=(720, 480), viewer=False, offScreen=False,
            showOrnaments=False, percent=100, forceOverwrite=True,
            editorPanelName=panel,
        )
        actual = _resolve_playblast(requested)
        if not actual.is_file() or actual.stat().st_size == 0:
            raise RuntimeError(f"capture missing or empty: {actual}")
        report["captures"].append({"name": name, "path": str(actual), "bytes": actual.stat().st_size})
        return actual

    def display_status(node):
        selection = om.MSelectionList()
        selection.add(node)
        return int(omr.MGeometryUtilities.displayStatus(selection.getDagPath(0)))

    def configure_panel():
        panels = cmds.getPanel(type="modelPanel") or []
        focused = cmds.getPanel(withFocus=True)
        panel = focused if focused in panels else (panels[0] if panels else None)
        if not panel:
            raise RuntimeError("no modelPanel available in Maya GUI")
        cmds.modelEditor(
            panel, edit=True, rendererName="vp2Renderer", allObjects=False,
            locators=True, polymeshes=True, grid=False, manipulators=False,
            selectionHiliteDisplay=True, displayAppearance="smoothShaded",
        )
        cmds.modelPanel(panel, edit=True, camera="persp")
        cmds.setAttr("persp.translate", 0.0, 1.0, 22.0, type="double3")
        cmds.setAttr("persp.rotate", 0.0, 0.0, 0.0, type="double3")
        cmds.setAttr("perspShape.nearClipPlane", 0.1)
        cmds.setAttr("perspShape.farClipPlane", 1000.0)
        return panel

    try:
        note("begin")
        cmds.file(new=True, force=True)
        note("scene-created")
        plugin = _ROOT / "mmd_tools" / "plugin_main.py"
        loaded_plugins = cmds.pluginInfo(query=True, listPlugins=True) or []
        if "mmdRigidBodyShape" not in (cmds.allNodeTypes() or []):
            cmds.loadPlugin(str(plugin), quiet=True)
        init_trace_path = Path(os.environ.get("MMD_TOOLS_INIT_TRACE_PATH", ""))
        init_trace = (
            init_trace_path.read_text(encoding="utf-8", errors="replace").splitlines()
            if init_trace_path.is_file()
            else []
        )
        report["checks"]["initialLoadedPlugins"] = loaded_plugins
        report["checks"]["pluginInitializeComplete"] = "initialize:done" in init_trace
        if not report["checks"]["pluginInitializeComplete"]:
            raise RuntimeError(f"plugin initialization incomplete: {init_trace[-5:]}")
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

        panel = configure_panel()
        note("panel-ready")

        capsule, capsule_shape = nodes["capsule"]
        cmds.setAttr(f"{capsule_shape}.shapeSizeY", 4.0)
        capsule_position = list(cmds.getAttr(f"{capsule_shape}.position")[0])
        capsule_position[1] = 1.0
        capsule_rotation = tuple(
            math.radians(value) for value in cmds.getAttr(f"{capsule_shape}.rotation")[0]
        )
        set_collider_authoring_pose(
            capsule,
            capsule_shape,
            capsule_position,
            capsule_rotation,
        )
        report["checks"]["editedCapsuleTotalHeight"] = (
            cmds.getAttr(f"{capsule_shape}.shapeSizeY")
            + 2.0 * cmds.getAttr(f"{capsule_shape}.shapeSizeX")
        )
        cmds.select(clear=True)
        report["checks"]["unselectedSelection"] = cmds.ls(selection=True, long=True) or []
        report["checks"]["unselectedDisplayStatus"] = display_status(capsule_shape)
        capture("01-edited", panel)
        note("edited-captured")

        box, _box_shape = nodes["box"]
        cmds.setAttr(f"{box}.visibility", False)
        report["checks"]["boxHidden"] = not cmds.getAttr(f"{box}.visibility")
        capture("02-visibility", panel)
        note("visibility-captured")
        cmds.setAttr(f"{box}.visibility", True)

        cmds.select(capsule, replace=True)
        report["checks"]["selectedSelection"] = cmds.ls(selection=True, long=True) or []
        report["checks"]["selectedDisplayStatus"] = display_status(capsule_shape)
        selected_path = om.MSelectionList()
        selected_path.add(capsule_shape)
        selection_color = omr.MGeometryUtilities.wireframeColor(selected_path.getDagPath(0))
        report["checks"]["selectedWireframeColor"] = list(selection_color)
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

        cmds.file(new=True, force=True)
        from mmd_tools.core.collider_display import physics_mode_line_style
        from mmd_tools.core.coordinate_transform import mmd_point_to_maya
        from tests.common.maya_coordinate_oracle import reflected_mmd_euler_matrix
        from mmd_tools.core.mmd_parser import parse_pmx_file
        from mmd_tools.core.visibility_state import (
            get_visibility_category,
            set_visibility_category,
            sync_visibility_connections,
        )
        from mmd_tools.adapters.maya_cmds_adapter import MayaCmdsAdapter
        from mmd_tools.io.mmd_importer import import_mmd_file

        fixture = _ROOT / "tests" / "data" / "physics" / "test_hair_physics.pmx"
        source_pmx = parse_pmx_file(str(fixture), use_native_pmx_parse=False)
        root = import_mmd_file(
            str(fixture),
            scale=1.0,
            options={
                "import_physics": True,
                "create_mmd_shaders": False,
                "setup_rig": False,
                "use_cpp_fast_load": False,
                "use_native_pmx_parse": False,
                "require_native_pmx_parse": False,
            },
        )
        panel = configure_panel()
        adapter = MayaCmdsAdapter()
        set_visibility_category(adapter, root, "colliders", True)
        sync_visibility_connections(adapter, root, "colliders")
        real_shapes = cmds.ls(type="mmdRigidBodyShape", long=True) or []
        real_meshes = [
            shape
            for shape in (
                cmds.listRelatives(root, allDescendents=True, fullPath=True, type="mesh") or []
            )
            if not cmds.getAttr(f"{shape}.intermediateObject")
        ]
        physics_groups = [
            child
            for child in (
                cmds.listRelatives(root, children=True, fullPath=True, type="transform") or []
            )
            if child.rsplit("|", 1)[-1].rsplit(":", 1)[-1] == "Physics"
        ]
        report["checks"]["realColliderCount"] = len(real_shapes)
        report["checks"]["realMeshCount"] = len(real_meshes)
        visible_meshes = []
        for mesh in real_meshes:
            mesh_selection = om.MSelectionList()
            mesh_selection.add(mesh)
            if mesh_selection.getDagPath(0).isVisible():
                visible_meshes.append(mesh)
        report["checks"]["realMeshVisible"] = bool(
            len(visible_meshes) == len(real_meshes)
            and cmds.modelEditor(panel, query=True, polymeshes=True)
        )
        report["checks"]["realCollidersVisible"] = bool(
            real_shapes
            and len(physics_groups) == 1
            and cmds.getAttr(f"{physics_groups[0]}.visibility")
            and get_visibility_category(adapter, root, "colliders")
            and all(cmds.getAttr(f"{shape}.enable") for shape in real_shapes)
        )
        bound = None
        for real_shape in real_shapes:
            bones = cmds.listConnections(
                f"{real_shape}.relatedBone", source=True, destination=False, type="joint"
            ) or []
            if bones:
                real_transform = cmds.listRelatives(real_shape, parent=True, fullPath=True)[0]
                bound = (real_transform, real_shape, bones[0])
                break
        if bound is None:
            raise RuntimeError("real fixture has no bound collider")
        real_transform, real_shape, bone = bound
        pmx_index = cmds.getAttr(f"{real_shape}.pmxIndex")
        source_body = source_pmx.rigid_bodies[pmx_index]
        raw_position = list(cmds.getAttr(f"{real_shape}.position")[0])
        raw_rotation = list(cmds.getAttr(f"{real_shape}.rotation")[0])

        expected = om.MTransformationMatrix(reflected_mmd_euler_matrix(source_body.rotation))
        expected.setTranslation(
            om.MVector(*mmd_point_to_maya(source_body.position)), om.MSpace.kTransform
        )
        actual = om.MMatrix(
            cmds.xform(real_transform, query=True, worldSpace=True, matrix=True)
        )
        expected_matrix = expected.asMatrix()
        report["checks"]["realRestMatrixMaxError"] = max(
            abs(actual[index] - expected_matrix[index]) for index in range(16)
        )

        def relative_matrix():
            collider_world = om.MMatrix(
                cmds.xform(real_transform, query=True, worldSpace=True, matrix=True)
            )
            bone_world = om.MMatrix(cmds.xform(bone, query=True, worldSpace=True, matrix=True))
            return collider_world * bone_world.inverse()

        def bone_model_matrix():
            bone_world = om.MMatrix(cmds.xform(bone, query=True, worldSpace=True, matrix=True))
            root_world = om.MMatrix(cmds.xform(root, query=True, worldSpace=True, matrix=True))
            return bone_world * root_world.inverse()

        cmds.currentTime(1)
        for node in (root, bone):
            cmds.setKeyframe(node, attribute="translate")
            cmds.setKeyframe(node, attribute="rotate")
        rest_offset = relative_matrix()
        rest_bone_model = bone_model_matrix()
        follow_constraints = cmds.listConnections(
            real_transform, source=True, destination=False, type="parentConstraint"
        ) or []
        follow_matrix_nodes = cmds.listConnections(
            f"{real_transform}.offsetParentMatrix",
            source=True,
            destination=False,
            type="multMatrix",
        ) or []
        legacy_follow = any(
            cmds.attributeQuery(
                "mmdColliderAuthoringFollow", node=constraint, exists=True
            )
            and cmds.getAttr(f"{constraint}.mmdColliderAuthoringFollow")
            for constraint in follow_constraints
        )
        opm_follow = any(
            cmds.attributeQuery(
                "mmdColliderAuthoringFollow", node=node, exists=True
            )
            and cmds.getAttr(f"{node}.mmdColliderAuthoringFollow")
            for node in follow_matrix_nodes
        )
        # Keep the legacy report key for downstream consumers while exposing
        # the actual follow implementation used by the current importer.
        report["checks"]["boundFollowConstraint"] = legacy_follow or opm_follow
        report["checks"]["boundFollowNodeType"] = (
            "multMatrix" if opm_follow else "parentConstraint" if legacy_follow else None
        )
        report["checks"]["boundFollowUsesOffsetParentMatrix"] = opm_follow
        report["checks"]["physicsModeLineStyles"] = [
            physics_mode_line_style(mode) for mode in range(3)
        ]

        cmds.select(root, replace=True)
        cmds.viewFit("persp", all=False, fitFactor=1.15)
        cmds.select(clear=True)
        capture("05-real-mesh-follow-frame1", panel, frame=1)

        cmds.currentTime(12)
        cmds.move(1.5, -0.75, 0.0, root, relative=True)
        cmds.rotate(0.0, 0.0, 6.0, root, relative=True)
        cmds.rotate(0.0, 0.0, 1.0, bone, relative=True, objectSpace=True)
        for node in (root, bone):
            cmds.setKeyframe(node, attribute="translate")
            cmds.setKeyframe(node, attribute="rotate")
        animated_offset = relative_matrix()
        report["checks"]["followOffsetMaxError"] = max(
            abs(animated_offset[index] - rest_offset[index]) for index in range(16)
        )
        animated_bone_model = bone_model_matrix()
        report["checks"]["boneAnimationMatrixMaxDelta"] = max(
            abs(animated_bone_model[index] - rest_bone_model[index]) for index in range(16)
        )
        bbox = cmds.exactWorldBoundingBox(real_transform)
        bbox_center = [(bbox[axis] + bbox[axis + 3]) * 0.5 for axis in range(3)]
        world_position = cmds.xform(
            real_transform, query=True, worldSpace=True, translation=True
        )
        report["checks"]["followBboxCenterMaxError"] = max(
            abs(bbox_center[axis] - world_position[axis]) for axis in range(3)
        )
        report["checks"]["rawPoseUnchanged"] = (
            list(cmds.getAttr(f"{real_shape}.position")[0]) == raw_position
            and list(cmds.getAttr(f"{real_shape}.rotation")[0]) == raw_rotation
        )
        capture("06-real-mesh-follow-frame12", panel, frame=12)
        note("real-follow-captured")
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
    init_trace = output / "plugin-initialize.log"
    for stale in (log, report_path, init_trace):
        if stale.exists():
            stale.unlink()

    proc = maya_commandport.launch_maya(
        version=args.maya, project_root=_ROOT, output_dir=output,
        port=args.port, launch_mode="explorer",
        env_overrides={
            "MMD_TOOLS_SKIP_SHADER_OVERRIDE": "1",
            "MMD_TOOLS_INIT_TRACE_PATH": str(init_trace),
        },
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
    semantic_failures = _semantic_failures(report)
    if report["errors"] or len(report["captures"]) != 6 or semantic_failures:
        report["semanticFailures"] = semantic_failures
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        raise RuntimeError(f"collider capture failed: {report}")
    LOGGER.info("Maya %s collider evidence: %s", report["mayaVersion"], report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
