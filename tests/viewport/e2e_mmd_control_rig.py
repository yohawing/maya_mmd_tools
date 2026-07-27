"""Maya GUI commandPort E2E gate for the MMD-native control rig.

The Maya-side check imports the checked-in PMX/VMD fixture, creates the
detached control rig, enters EDIT, moves only the left foot IK controller,
checks the owned ``mmdCcdIk`` response and cycle state, toggles ``ikEnabled``,
bakes back to MMD inputs, saves/reopens, and performs a VMD export/re-import
round-trip.  The host side always launches a fresh Maya process and refuses to
use an already-open commandPort.

Usage::

    python tests/viewport/e2e_mmd_control_rig.py --maya 2024
    python tests/viewport/e2e_mmd_control_rig.py --maya 2026 --port 7734
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import math
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Iterable, Mapping

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.common import maya_commandport

COMMAND_PORT = 7734
COMPLETION_MARKER = "//-- MMD_CONTROL_RIG_E2E_DONE --//"
TEST_TIMEOUT = 600
LOG_POLL_INTERVAL = 0.5
MOVE_OFFSET_X = 0.35
MOVE_EPSILON = 1.0e-5
ROUNDTRIP_MATRIX_EPSILON = 5.0e-3
ROUNDTRIP_FRAMES = tuple(range(0, 6))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _flatten_numeric(value: Any) -> list[float]:
    """Flatten Maya numeric wrappers into a JSON-safe float list."""

    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return []
    if isinstance(value, Iterable):
        result: list[float] = []
        for item in value:
            result.extend(_flatten_numeric(item))
        return result
    try:
        return [float(value)]
    except (TypeError, ValueError):
        return []


def _distance(left: Iterable[float], right: Iterable[float]) -> float:
    values = list(zip(left, right))
    return math.sqrt(sum((a - b) ** 2 for a, b in values))


def _matrix(node: str, cmds) -> list[float]:
    return _flatten_numeric(cmds.xform(node, query=True, worldSpace=True, matrix=True))


def _world_translation(node: str, cmds) -> list[float]:
    return _flatten_numeric(
        cmds.xform(node, query=True, worldSpace=True, translation=True)
    )


def _find_joint_for_mmd_name(name: str, cmds) -> str | None:
    """Resolve a PMX bone-name metadata value to its Maya joint."""

    for joint in cmds.ls(type="joint", long=True) or []:
        try:
            if not cmds.attributeQuery("mmd_bone_name", node=joint, exists=True):
                continue
            if str(cmds.getAttr(f"{joint}.mmd_bone_name")) == str(name):
                return str(joint)
        except RuntimeError:
            continue
    return None


def _cycle_state(label: str, cmds) -> dict[str, Any]:
    """Capture Maya's cycleCheck output without mutating its enable state."""

    evaluation_on = bool(cmds.cycleCheck(query=True, evaluation=True))
    plugs = sorted(str(item) for item in (cmds.cycleCheck(all=True, list=True) or []))
    return {"label": label, "evaluationOn": evaluation_on, "cyclePlugs": plugs}


def _joint_worlds(cmds, frames: Iterable[int]) -> dict[str, dict[str, list[float]]]:
    """Capture indexed PMX joint world matrices for stable round-trip comparison."""

    indexed: dict[str, str] = {}
    for joint in cmds.ls(type="joint", long=True) or []:
        try:
            if not cmds.attributeQuery("mmd_bone_index", node=joint, exists=True):
                continue
            index = cmds.getAttr(f"{joint}.mmd_bone_index")
            indexed[str(int(index))] = str(joint)
        except (TypeError, ValueError, RuntimeError):
            continue

    result: dict[str, dict[str, list[float]]] = {}
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        cmds.refresh(force=True)
        result[str(frame)] = {
            index: _matrix(joint, cmds)
            for index, joint in sorted(indexed.items())
            if cmds.objExists(joint)
        }
    return result


def _ik_states(cmds, frames: Iterable[int]) -> dict[str, dict[str, bool | None]]:
    """Capture enabled state of all mmdCcdIk solvers by PMX IK name."""

    nodes = [str(node) for node in (cmds.ls(type="mmdCcdIk", long=True) or [])]
    names: dict[str, str] = {}
    for node in nodes:
        try:
            name = (
                cmds.getAttr(f"{node}.mmd_ik_bone_name")
                if cmds.attributeQuery("mmd_ik_bone_name", node=node, exists=True)
                else node
            )
        except RuntimeError:
            name = node
        names[str(name)] = node

    result: dict[str, dict[str, bool | None]] = {}
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        cmds.refresh(force=True)
        result[str(frame)] = {}
        for name, node in sorted(names.items()):
            try:
                enabled = bool(cmds.getAttr(f"{node}.enabled"))
            except RuntimeError:
                enabled = None
            result[str(frame)][name] = enabled
    return result


def _solver_owned_joint_indices(cmds) -> dict[str, dict[str, Any]]:
    """Resolve joints driven by native ``mmdCcdIk`` output plugs.

    These links are expected to be re-solved when a VMD target is quantized
    to its float32 representation.  Keep them visible in the report, but do
    not treat their numerical drift as authored-channel parity failure.
    """

    owned: dict[str, dict[str, Any]] = {}
    for solver in cmds.ls(type="mmdCcdIk", long=True) or []:
        solver_name = str(solver)
        try:
            ik_name = (
                cmds.getAttr(f"{solver}.mmd_ik_bone_name")
                if cmds.attributeQuery("mmd_ik_bone_name", node=solver, exists=True)
                else solver_name
            )
        except RuntimeError:
            ik_name = solver_name
        for slot in range(64):
            destinations = cmds.listConnections(
                f"{solver}.outputRotate[{slot}]",
                source=False,
                destination=True,
                type="joint",
            ) or []
            for destination in destinations:
                joints = cmds.ls(destination, long=True) or [destination]
                joint = str(joints[0])
                try:
                    if not cmds.attributeQuery("mmd_bone_index", node=joint, exists=True):
                        continue
                    index = str(int(cmds.getAttr(f"{joint}.mmd_bone_index")))
                except (TypeError, ValueError, RuntimeError):
                    continue
                owned[index] = {
                    "joint": joint,
                    "solver": solver_name,
                    "ikBoneName": str(ik_name),
                    "outputSlot": slot,
                }
    return owned


def _expand_solver_owned_joint_indices(
    direct_owned: Mapping[str, Mapping[str, Any]],
    cmds,
) -> dict[str, dict[str, Any]]:
    """Include descendants whose world matrices inherit a solver-owned link."""

    dependency_by_index: dict[str, set[str]] = {}
    for joint in cmds.ls(type="joint", long=True) or []:
        try:
            if not cmds.attributeQuery("mmd_bone_index", node=joint, exists=True):
                continue
            index = str(int(cmds.getAttr(f"{joint}.mmd_bone_index")))
            dependencies: set[str] = set()
            # A rig may insert non-joint transforms between two PMX bones.
            # Walk the DAG until the nearest indexed joint instead of assuming
            # the immediate parent carries ``mmd_bone_index``.
            parents = cmds.listRelatives(joint, parent=True, fullPath=True) or []
            while parents:
                parent = str(parents[0])
                if cmds.attributeQuery("mmd_bone_index", node=parent, exists=True):
                    dependencies.add(
                        str(int(cmds.getAttr(f"{parent}.mmd_bone_index")))
                    )
                    break
                parents = cmds.listRelatives(
                    parent, parent=True, fullPath=True
                ) or []
            # Append/grant bones can be siblings in the DAG while still
            # inheriting a solver-owned source rotation.  The importer keeps
            # this PMX relationship as metadata on the destination joint.
            if cmds.attributeQuery("mmd_grant_parent_index", node=joint, exists=True):
                grant_parent = int(cmds.getAttr(f"{joint}.mmd_grant_parent_index"))
                if grant_parent >= 0:
                    dependencies.add(str(grant_parent))
            dependency_by_index[index] = dependencies
        except (TypeError, ValueError, RuntimeError):
            continue

    effective = {str(index): dict(value) for index, value in direct_owned.items()}
    for index in dependency_by_index:
        # Preserve direct output metadata when a direct solver link also has
        # a solver-owned ancestor.
        if index in direct_owned:
            continue
        pending = list(dependency_by_index[index])
        visited: set[str] = set()
        while pending:
            ancestor = pending.pop()
            if ancestor in visited:
                continue
            visited.add(ancestor)
            if ancestor in direct_owned:
                effective[index] = {
                    **direct_owned[ancestor],
                    "propagatedFrom": ancestor,
                }
                break
            pending.extend(dependency_by_index.get(ancestor, ()))
    return effective


def _matrix_error_summary(
    locations: Iterable[Mapping[str, Any]],
    *,
    solver_owned_indices: set[str],
) -> dict[str, Any]:
    """Summarize matrix errors while preserving the exact worst entry."""

    locations = list(locations)
    non_solver = [
        item for item in locations if str(item["jointIndex"]) not in solver_owned_indices
    ]
    solver = [
        item for item in locations if str(item["jointIndex"]) in solver_owned_indices
    ]

    def _summary(items: list[Mapping[str, Any]]) -> dict[str, Any]:
        by_frame = {
            str(frame): max(
                (
                    float(item["error"])
                    for item in items
                    if int(item["frame"]) == int(frame)
                ),
                default=0.0,
            )
            for frame in sorted({int(item["frame"]) for item in items})
        }
        return {
            "maxWorldMatrixError": max(
                (float(item["error"]) for item in items),
                default=0.0,
            ),
            "maxWorldMatrixErrorByFrame": by_frame,
            "worstEntry": max(
                items,
                key=lambda item: float(item["error"]),
                default=None,
            ),
            "jointCount": len({str(item["jointIndex"]) for item in items}),
        }

    return {"nonSolverOwned": _summary(non_solver), "solverOwned": _summary(solver)}


def _resolve_foot_solver(root: str, metadata: Mapping[str, Any], cmds) -> tuple[str, str]:
    """Return the left-foot solver and an output-driven effector joint."""

    binding = metadata.get("bindings", {}).get("left_foot_ik", {})
    solvers = [str(value) for value in binding.get("ikSolvers", []) if value]
    if not solvers:
        raise RuntimeError("left_foot_ik binding has no mmdCcdIk solver")
    solver = solvers[0]
    if not cmds.objExists(solver):
        matches = cmds.ls(solver, long=True) or []
        if len(matches) == 1:
            solver = str(matches[0])
    if not cmds.objExists(solver):
        raise RuntimeError(f"left foot solver is missing: {solver}")

    destinations: list[str] = []
    for index in range(32):
        for value in (
            cmds.listConnections(
                f"{solver}.outputRotate[{index}]",
                source=False,
                destination=True,
                type="joint",
            )
            or []
        ):
            long_name = cmds.ls(value, long=True) or [value]
            destinations.append(str(long_name[0]))
    if destinations:
        return solver, sorted(set(destinations))[-1]

    fallback = str(binding.get("joint", ""))
    if fallback and cmds.objExists(fallback):
        return solver, fallback
    matches = cmds.ls(fallback, long=True) or []
    if len(matches) == 1:
        return solver, str(matches[0])
    raise RuntimeError(f"left foot solver has no output-driven effector: {solver}")


def _solver_snapshot(solver: str, effector: str, cmds) -> dict[str, Any]:
    """Capture solver goal/output and the selected effector world matrix."""

    chain = {}
    try:
        raw_chain = cmds.getAttr(f"{solver}.chainJson")
        chain = json.loads(raw_chain) if raw_chain else {}
    except (TypeError, ValueError, RuntimeError):
        chain = {}
    links = chain.get("links", []) if isinstance(chain, dict) else []
    count = max(1, len(links))
    outputs = {}
    for index in range(count):
        try:
            outputs[str(index)] = _flatten_numeric(
                cmds.getAttr(f"{solver}.outputRotate[{index}]")
            )
        except RuntimeError:
            outputs[str(index)] = []
    try:
        enabled = bool(cmds.getAttr(f"{solver}.enabled"))
    except RuntimeError:
        enabled = None
    return {
        "solver": solver,
        "enabled": enabled,
        "goalWorldMatrix": _flatten_numeric(cmds.getAttr(f"{solver}.goalWorldMatrix")),
        "outputRotate": outputs,
        "effector": effector,
        "effectorWorldMatrix": _matrix(effector, cmds),
        "effectorWorldTranslation": _world_translation(effector, cmds),
    }


def _control_worlds(controls: Mapping[str, str], cmds) -> dict[str, list[float]]:
    return {
        str(role): _matrix(str(node), cmds)
        for role, node in sorted(controls.items())
        if cmds.objExists(str(node))
    }


def _dag_descendant_roles(
    controls: Mapping[str, str], ancestor_role: str, cmds
) -> set[str]:
    """Return controls that are DAG descendants of ``ancestor_role``.

    Control zero groups are intentionally nested below their nearest parent
    control.  Moving a parent therefore changes each child control's world
    matrix even though no child channel was authored.  Resolve long DAG paths
    before comparing them so namespace and nested-group changes do not turn
    expected inherited motion into an unrelated-control failure.
    """

    ancestor = controls.get(ancestor_role)
    if not ancestor:
        return set()
    try:
        ancestor_paths = cmds.ls(str(ancestor), long=True) or [str(ancestor)]
    except RuntimeError:
        ancestor_paths = [str(ancestor)]

    descendants: set[str] = set()
    for ancestor_path in ancestor_paths:
        try:
            descendants.update(
                str(node)
                for node in (
                    cmds.listRelatives(
                        str(ancestor_path),
                        allDescendents=True,
                        fullPath=True,
                    )
                    or []
                )
            )
        except RuntimeError:
            continue
    if not descendants:
        return set()

    result: set[str] = set()
    for role, node in controls.items():
        if str(role) == str(ancestor_role):
            continue
        try:
            node_paths = cmds.ls(str(node), long=True) or [str(node)]
        except RuntimeError:
            node_paths = [str(node)]
        if descendants.intersection(str(path) for path in node_paths):
            result.add(str(role))
    return result


def _find_rig_root(cmds) -> str:
    from mmd_tools.core.constants import ATTR_MMD_CONTROL_RIG_JSON

    roots = cmds.ls(f"*.{ATTR_MMD_CONTROL_RIG_JSON}", objectsOnly=True, long=True) or []
    if len(roots) != 1:
        raise RuntimeError(f"expected one MMD control-rig metadata root, found {roots}")
    return str(roots[0])


def _write_maya_report(report_path: Path, report: Mapping[str, Any]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# ===================================================================
# Maya-side: runs inside the live Maya GUI
# ===================================================================
def run_e2e_check(
    log_path: str,
    model_path: str,
    motion_path: str,
    report_path: str,
    scene_path: str,
    exported_vmd_path: str,
) -> None:
    """Execute the complete control-rig workflow in a live Maya GUI."""

    import maya.cmds as cmds

    log_file = Path(log_path)
    report_file = Path(report_path)
    report: dict[str, Any] = {
        "kind": "mmd-control-rig-gui-e2e",
        "roundtripOracle": "internal_maya_vmd_export_reimport",
        "internalOracle": {
            "identity": "maya_vmd_export_reimport_authored_parity",
            "status": "pending",
        },
        "externalOracle": {
            "identity": "mmd_anim_mesh_oracle_compare_rig_pmx_bind",
            "status": "pending",
        },
        "status": "error",
        "mayaVersion": None,
        "model": str(model_path),
        "motion": str(motion_path),
        "states": {},
        "roles": [],
        "vmdApplicability": {},
        "ikMove": {},
        "ikToggle": {},
        "cycles": [],
        "roundtrip": {},
        "errors": [],
    }

    def _log(message: str) -> None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(str(message) + "\n")
        try:
            print(message)
        except Exception:
            pass

    dll_directory_handle = None
    try:
        report["mayaVersion"] = str(cmds.about(version=True))
        _log("=== MMD Control Rig GUI E2E begin ===")

        plugin_path = _PROJECT_ROOT / "mmd_tools" / "plugin_main.py"
        plugin_name = plugin_path.stem
        maya_major = str(cmds.about(version=True)).split(".", 1)[0]
        cpp_plugin = _PROJECT_ROOT / "plug-ins" / maya_major / "Debug" / "mmd_tools_cpp.mll"
        if not cpp_plugin.is_file():
            raise RuntimeError(
                f"Maya {maya_major} Debug C++ plugin is required for mmdCcdIk E2E: {cpp_plugin}"
            )
        plugin_dir = str(cpp_plugin.parent)
        if plugin_dir not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = plugin_dir + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            dll_directory_handle = os.add_dll_directory(plugin_dir)
        if not cmds.pluginInfo(str(cpp_plugin), query=True, loaded=True):
            cmds.loadPlugin(str(cpp_plugin), quiet=True)
            _log(f"loaded C++ plugin: {cpp_plugin}")
        if not cmds.pluginInfo(plugin_name, query=True, loaded=True):
            cmds.loadPlugin(str(plugin_path), quiet=True)
            _log(f"loaded plugin: {plugin_path}")

        from mmd_tools.core.mmd_control_rig_builder import (
            CONTROL_RIG_ATTACHED,
            CONTROL_RIG_BAKED,
            CONTROL_RIG_EDIT,
            build_mmd_control_rig,
            read_mmd_control_rig_metadata,
        )
        from mmd_tools.core.mmd_control_rig_motion import (
            bake_mmd_control_rig,
            enter_mmd_control_rig_edit,
        )
        from mmd_tools.core.vmd_data import VmdData
        from mmd_tools.converters.vmd_scene_collector import VmdSceneCollector
        from mmd_tools.io.mmd_importer import import_mmd_file
        from mmd_tools.io.vmd_exporter import VmdExporter

        source_vmd = VmdData().parse_file(str(motion_path))
        report["vmdApplicability"]["boneFrameCount"] = len(source_vmd.bone_frames)
        if not source_vmd.bone_frames:
            raise RuntimeError("fixture VMD contains no bone frames")

        cmds.file(new=True, force=True)
        root = import_mmd_file(
            str(model_path),
            options={
                "setup_rig": True,
                "setup_bone_orientation": True,
                "import_physics": False,
            },
        )
        if not root:
            raise RuntimeError(f"PMX import returned no model root: {model_path}")
        root = str(root)
        _log(f"imported PMX root: {root}")

        imported_motion = import_mmd_file(
            str(motion_path),
            options={"target_model": root, "pmx_path": str(model_path)},
        )
        if not imported_motion:
            raise RuntimeError(f"VMD import returned no result: {motion_path}")
        _log(f"imported VMD: {motion_path}")

        sample = None
        sample_joint = None
        for candidate in sorted(
            source_vmd.bone_frames,
            key=lambda item: (int(item.frame_number), str(item.bone_name)),
        ):
            has_payload = any(abs(float(value)) > MOVE_EPSILON for value in candidate.position)
            has_payload = has_payload or abs(float(candidate.rotation[3]) - 1.0) > MOVE_EPSILON
            if not has_payload or int(candidate.frame_number) <= 0:
                continue
            joint = _find_joint_for_mmd_name(candidate.bone_name, cmds)
            if joint:
                sample = candidate
                sample_joint = joint
                break
        if sample is None or sample_joint is None:
            raise RuntimeError("fixture VMD has no non-rest keyed bone mapped to a Maya joint")
        cmds.currentTime(0, edit=True)
        cmds.refresh(force=True)
        sample_before = _matrix(sample_joint, cmds)
        cmds.currentTime(int(sample.frame_number), edit=True)
        cmds.refresh(force=True)
        sample_after = _matrix(sample_joint, cmds)
        sample_delta = max(
            (abs(actual - expected) for actual, expected in zip(sample_before, sample_after)),
            default=0.0,
        )
        report["vmdApplicability"].update(
            {
                "sampleBone": str(sample.bone_name),
                "sampleJoint": sample_joint,
                "sampleFrame": int(sample.frame_number),
                "samplePosition": [float(value) for value in sample.position],
                "sampleRotation": [float(value) for value in sample.rotation],
                "sampleWorldMatrixMaxAbsDelta": sample_delta,
                "pass": sample_delta > MOVE_EPSILON,
            }
        )
        _log(
            "VMD applicability: boneFrames=%d sample=%s@%d worldMatrixMaxAbsDelta=%.8f"
            % (len(source_vmd.bone_frames), sample.bone_name, sample.frame_number, sample_delta)
        )
        if sample_delta <= MOVE_EPSILON:
            raise RuntimeError("imported VMD has keyed data but no non-rest world effect")

        baseline_cycle = _cycle_state("after_vmd_import", cmds)
        report["cycles"].append(baseline_cycle)

        rig = build_mmd_control_rig(root)
        report["states"]["afterBuild"] = rig.state
        report["roles"] = sorted(str(role) for role in rig.controls)
        if rig.state != CONTROL_RIG_ATTACHED:
            raise RuntimeError(f"build did not produce ATTACHED state: {rig.state}")
        if "left_foot_ik" not in rig.controls:
            raise RuntimeError("fixture has no left_foot_ik control")
        _log(f"built control rig ({len(rig.controls)} controls)")

        metadata = read_mmd_control_rig_metadata(root)
        if not metadata:
            raise RuntimeError("control-rig metadata missing after build")
        solver, effector = _resolve_foot_solver(root, metadata, cmds)
        control = str(rig.controls["left_foot_ik"])
        _log(f"left foot control={control}, solver={solver}, effector={effector}")

        edit_metadata = enter_mmd_control_rig_edit(root)
        report["states"]["afterEdit"] = edit_metadata.get("state")
        if edit_metadata.get("state") != CONTROL_RIG_EDIT:
            raise RuntimeError(f"EDIT transition failed: {edit_metadata.get('state')}")

        frame = 3
        cmds.currentTime(frame, edit=True)
        cmds.refresh(force=True)
        before_solver = _solver_snapshot(solver, effector, cmds)
        before_controls = _control_worlds(rig.controls, cmds)
        before_x = float(cmds.getAttr(f"{control}.translateX"))
        before_cycle = _cycle_state("before_ik_move", cmds)
        report["cycles"].append(before_cycle)

        cmds.setKeyframe(
            control,
            attribute="translateX",
            time=frame,
            value=before_x + MOVE_OFFSET_X,
        )
        cmds.refresh(force=True)
        after_solver = _solver_snapshot(solver, effector, cmds)
        after_controls = _control_worlds(rig.controls, cmds)
        after_cycle = _cycle_state("after_ik_move", cmds)
        report["cycles"].append(after_cycle)

        goal_delta = _distance(
            before_solver["goalWorldMatrix"], after_solver["goalWorldMatrix"]
        )
        output_delta = _distance(
            [item for values in before_solver["outputRotate"].values() for item in values],
            [item for values in after_solver["outputRotate"].values() for item in values],
        )
        effector_delta = _distance(
            before_solver["effectorWorldMatrix"], after_solver["effectorWorldMatrix"]
        )
        control_deltas = {
            role: _distance(before_controls.get(role, []), after_controls.get(role, []))
            for role in sorted(set(before_controls) | set(after_controls))
        }
        descendant_roles = _dag_descendant_roles(rig.controls, "left_foot_ik", cmds)
        descendant_control_deltas = {
            role: delta
            for role, delta in control_deltas.items()
            if role in descendant_roles
        }
        other_control_deltas = {
            role: delta
            for role, delta in control_deltas.items()
            if role != "left_foot_ik" and role not in descendant_roles
        }
        report["ikMove"] = {
            "frame": frame,
            "control": control,
            "solver": solver,
            "effector": effector,
            "before": before_solver,
            "after": after_solver,
            "goalWorldMatrixDelta": goal_delta,
            "outputRotateDelta": output_delta,
            "effectorWorldMatrixDelta": effector_delta,
            "controlWorldDeltas": control_deltas,
            "descendantControlRoles": sorted(descendant_roles),
            "descendantControlWorldDeltas": descendant_control_deltas,
            "otherControlWorldDeltas": other_control_deltas,
            "pass": bool(
                goal_delta > MOVE_EPSILON
                and max(output_delta, effector_delta) > MOVE_EPSILON
                and all(delta <= MOVE_EPSILON for delta in other_control_deltas.values())
            ),
        }
        _log(
            "IK move: goalDelta=%.8f outputDelta=%.8f effectorDelta=%.8f"
            % (goal_delta, output_delta, effector_delta)
        )
        if descendant_control_deltas:
            _log(
                "IK move: inherited descendant control deltas=%s"
                % json.dumps(descendant_control_deltas, sort_keys=True)
            )
        if other_control_deltas:
            _log(
                "IK move: unrelated control deltas=%s"
                % json.dumps(other_control_deltas, sort_keys=True)
            )
        if not report["ikMove"]["pass"]:
            raise RuntimeError("left foot IK move did not produce an owned solver response")

        enabled_before = bool(cmds.getAttr(f"{solver}.enabled"))
        enabled_after_expected = not enabled_before
        enabled_sources = cmds.listConnections(
            f"{control}.ikEnabled",
            source=True,
            destination=False,
            plugs=True,
        ) or []
        enabled_source_keys = None
        if enabled_sources:
            # EDIT preserves an existing animation source on the controller.
            # Key that source directly; setKeyframe on a destination with an
            # incoming animCurve can be accepted by Maya but leave its value
            # unchanged.
            source_node = str(enabled_sources[0]).split(".", 1)[0]
            source_type = str(cmds.nodeType(source_node))
            if not source_type.startswith("animCurve"):
                raise RuntimeError(
                    f"ikEnabled source is not an animCurve: {enabled_sources[0]} ({source_type})"
                )
            cmds.setKeyframe(
                source_node,
                time=frame,
                value=int(enabled_after_expected),
            )
            try:
                enabled_source_keys = {
                    "node": source_node,
                    "type": source_type,
                    "times": _flatten_numeric(
                        cmds.keyframe(source_node, query=True, timeChange=True)
                    ),
                    "values": _flatten_numeric(
                        cmds.keyframe(source_node, query=True, valueChange=True)
                    ),
                }
            except RuntimeError:
                enabled_source_keys = {"node": source_node, "type": source_type}
        else:
            cmds.setKeyframe(
                control,
                attribute="ikEnabled",
                time=frame,
                value=int(enabled_after_expected),
            )
        # Keying a controller attribute does not always dirty a custom bool
        # input in a GUI evaluation context.  Explicitly dirty the owned solver
        # before reading its evaluated enabled state.
        cmds.dgdirty(allPlugs=True)
        cmds.dgdirty(control)
        cmds.dgdirty(solver)
        cmds.refresh(force=True)
        enabled_after = bool(cmds.getAttr(f"{solver}.enabled"))
        try:
            control_enabled_after = bool(cmds.getAttr(f"{control}.ikEnabled"))
        except RuntimeError:
            control_enabled_after = None
        control_enabled_sources = [
            str(value)
            for value in (
                cmds.listConnections(
                    f"{control}.ikEnabled",
                    source=True,
                    destination=False,
                    plugs=True,
                )
                or []
            )
        ]
        solver_enabled_sources = [
            str(value)
            for value in (
                cmds.listConnections(
                    f"{solver}.enabled",
                    source=True,
                    destination=False,
                    plugs=True,
                )
                or []
            )
        ]
        report["ikToggle"] = {
            "frame": frame,
            "control": control,
            "solver": solver,
            "before": enabled_before,
            "after": enabled_after,
            "expectedAfter": enabled_after_expected,
            "controlAfter": control_enabled_after,
            "controlSources": control_enabled_sources,
            "solverSources": solver_enabled_sources,
            "sourceKeys": enabled_source_keys,
            "pass": enabled_after == enabled_after_expected,
        }
        _log(f"IK enabled toggle: {enabled_before} -> {enabled_after}")
        if not report["ikToggle"]["pass"]:
            raise RuntimeError("ikEnabled toggle did not reach mmdCcdIk.enabled")

        baked_metadata = bake_mmd_control_rig(root)
        report["states"]["afterBake"] = baked_metadata.get("state")
        if baked_metadata.get("state") != CONTROL_RIG_BAKED:
            raise RuntimeError(f"Bake did not produce BAKED state: {baked_metadata.get('state')}")
        baked_cycle = _cycle_state("after_bake", cmds)
        report["cycles"].append(baked_cycle)

        scene_file = Path(scene_path)
        scene_file.parent.mkdir(parents=True, exist_ok=True)
        cmds.file(rename=str(scene_file))
        cmds.file(save=True, force=True, type="mayaAscii")
        cmds.file(str(scene_file), open=True, force=True)
        reopened_root = _find_rig_root(cmds)
        reopened_metadata = read_mmd_control_rig_metadata(reopened_root)
        report["states"]["afterReopen"] = reopened_metadata.get("state") if reopened_metadata else None
        if not reopened_metadata or reopened_metadata.get("state") != CONTROL_RIG_BAKED:
            raise RuntimeError("save/reopen did not preserve BAKED control-rig metadata")
        reopened_cycle = _cycle_state("after_reopen", cmds)
        report["cycles"].append(reopened_cycle)

        source_world = _joint_worlds(cmds, ROUNDTRIP_FRAMES)
        source_ik = _ik_states(cmds, ROUNDTRIP_FRAMES)
        source_solver_owned_direct = _solver_owned_joint_indices(cmds)
        source_solver_owned = _expand_solver_owned_joint_indices(
            source_solver_owned_direct,
            cmds,
        )
        collected = VmdSceneCollector().collect({"target_model": reopened_root})
        output_vmd = Path(exported_vmd_path)
        output_vmd.parent.mkdir(parents=True, exist_ok=True)
        VmdExporter().export_vmd_animation(str(output_vmd), collected)
        parsed_vmd = VmdData().parse_file(str(output_vmd))
        report["roundtrip"]["exportedBoneFrames"] = len(parsed_vmd.bone_frames)
        report["roundtrip"]["exportedIkFrames"] = len(parsed_vmd.ik_show_hide_frames)
        if not output_vmd.is_file() or not parsed_vmd.bone_frames:
            raise RuntimeError("VMD export produced no bone frames")

        cmds.file(new=True, force=True)
        fresh_root = import_mmd_file(
            str(model_path),
            options={
                "setup_rig": True,
                "setup_bone_orientation": True,
                "import_physics": False,
            },
        )
        if not fresh_root:
            raise RuntimeError("fresh PMX import failed for VMD round-trip")
        if not import_mmd_file(
            str(output_vmd),
            options={"target_model": str(fresh_root), "pmx_path": str(model_path)},
        ):
            raise RuntimeError("fresh VMD import failed for VMD round-trip")
        fresh_world = _joint_worlds(cmds, ROUNDTRIP_FRAMES)
        fresh_ik = _ik_states(cmds, ROUNDTRIP_FRAMES)
        fresh_solver_owned_direct = _solver_owned_joint_indices(cmds)
        fresh_solver_owned = _expand_solver_owned_joint_indices(
            fresh_solver_owned_direct,
            cmds,
        )
        source_frame_keys = set(source_world)
        fresh_frame_keys = set(fresh_world)
        if source_frame_keys != fresh_frame_keys:
            raise RuntimeError(
                "round-trip frame key set mismatch: "
                f"source={sorted(source_frame_keys)} fresh={sorted(fresh_frame_keys)}"
            )
        for frame in sorted(source_frame_keys):
            source_indices = set(source_world[frame])
            fresh_indices = set(fresh_world[frame])
            if source_indices != fresh_indices:
                raise RuntimeError(
                    f"round-trip joint-index set mismatch at frame {frame}: "
                    f"source={sorted(source_indices)} fresh={sorted(fresh_indices)}"
                )
            for index in sorted(source_indices):
                if len(source_world[frame][index]) != len(fresh_world[frame][index]):
                    raise RuntimeError(
                        f"round-trip matrix length mismatch at frame={frame} index={index}"
                    )
        if set(source_solver_owned) != set(fresh_solver_owned):
            raise RuntimeError(
                "round-trip solver-owned joint set mismatch: "
                f"source={sorted(source_solver_owned)} fresh={sorted(fresh_solver_owned)}"
            )
        if set(source_solver_owned_direct) != set(fresh_solver_owned_direct):
            raise RuntimeError(
                "round-trip direct solver-owned joint set mismatch: "
                f"source={sorted(source_solver_owned_direct)} "
                f"fresh={sorted(fresh_solver_owned_direct)}"
            )
        matrix_error_locations = [
            {
                "error": abs(actual - expected),
                "frame": int(frame),
                "jointIndex": str(index),
                "element": int(element),
                "source": actual,
                "fresh": expected,
            }
            for frame in sorted(source_frame_keys)
            for index in sorted(source_world[frame])
            for element, (actual, expected) in enumerate(
                zip(source_world[frame][index], fresh_world[frame][index])
            )
        ]
        matrix_errors = [item["error"] for item in matrix_error_locations]
        max_matrix_error = max(matrix_errors, default=0.0)
        error_summary = _matrix_error_summary(
            matrix_error_locations,
            solver_owned_indices=set(source_solver_owned),
        )
        non_solver_summary = error_summary["nonSolverOwned"]
        solver_summary = error_summary["solverOwned"]
        authored_pass = bool(
            matrix_errors
            and non_solver_summary["jointCount"] > 0
            and non_solver_summary["maxWorldMatrixError"] < ROUNDTRIP_MATRIX_EPSILON
            and source_ik == fresh_ik
        )
        report["roundtrip"].update(
            {
                "frames": list(ROUNDTRIP_FRAMES),
                "maxWorldMatrixError": max_matrix_error,
                "matrixErrorMetric": "max_abs_element",
                "maxWorldMatrixErrorLocation": max(
                    matrix_error_locations,
                    key=lambda item: item["error"],
                    default=None,
                ),
                "maxWorldMatrixErrorByFrame": {
                    str(frame): max(
                        (
                            item["error"]
                            for item in matrix_error_locations
                            if item["frame"] == int(frame)
                        ),
                        default=0.0,
                    )
                    for frame in sorted(source_frame_keys)
                },
                "solverOwnedJointIndices": sorted(source_solver_owned),
                "directSolverOwnedJointIndices": sorted(source_solver_owned_direct),
                "solverOwnedJoints": source_solver_owned,
                "nonSolverOwned": non_solver_summary,
                "solverOwned": solver_summary,
                "ikStatesEqual": source_ik == fresh_ik,
                "sourceIkStates": source_ik,
                "freshIkStates": fresh_ik,
                "authoredParityPass": authored_pass,
                "solverDriftDelegatedToExternalOracle": bool(
                    solver_summary["maxWorldMatrixError"] >= ROUNDTRIP_MATRIX_EPSILON
                ),
                "pass": authored_pass,
            }
        )
        report["internalOracle"] = {
            "identity": "maya_vmd_export_reimport_authored_parity",
            "status": "pass" if authored_pass else "fail",
            "solverOwnedDriftDelegatedToExternalOracle": report["roundtrip"][
                "solverDriftDelegatedToExternalOracle"
            ],
        }
        _log(
            "round-trip: boneFrames=%d ikFrames=%d nonSolverMax=%.8f solverMax=%.8f ikEqual=%s"
            % (
                report["roundtrip"]["exportedBoneFrames"],
                report["roundtrip"]["exportedIkFrames"],
                non_solver_summary["maxWorldMatrixError"],
                solver_summary["maxWorldMatrixError"],
                source_ik == fresh_ik,
            )
        )
        if not authored_pass:
            raise RuntimeError("VMD authored-channel parity exceeded the numeric gate")

        final_cycle = _cycle_state("after_roundtrip", cmds)
        report["cycles"].append(final_cycle)
        if any(not bool(state.get("evaluationOn")) for state in report["cycles"]):
            raise RuntimeError("cycleCheck evaluation must remain enabled for every gate")
        baseline_plugs = set(baseline_cycle["cyclePlugs"])
        new_cycles = sorted(
            plug
            for state in report["cycles"]
            for plug in set(state["cyclePlugs"]) - baseline_plugs
        )
        report["newCyclePlugs"] = new_cycles
        if new_cycles:
            raise RuntimeError(f"new DG cycles detected: {new_cycles}")

        report["status"] = "pass"
        _log("PASS: MMD control-rig GUI E2E numeric gates passed")
    except Exception:
        report["errors"].append(traceback.format_exc())
        _log(f"EXCEPTION:\n{traceback.format_exc()}")
    finally:
        _write_maya_report(report_file, report)
        _log(f"RESULT_JSON: {json.dumps(report, ensure_ascii=False, sort_keys=True)}")
        _log(COMPLETION_MARKER)
        if dll_directory_handle is not None:
            try:
                dll_directory_handle.close()
            except Exception:
                pass


def _repo_path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if _PROJECT_ROOT not in path.parents and path != _PROJECT_ROOT:
        raise ValueError(f"path must stay inside repository: {path}")
    return path


def _wait_for_file(path: Path, timeout: float) -> None:
    start = time.time()
    while time.time() - start < timeout:
        if path.is_file():
            return
        time.sleep(LOG_POLL_INTERVAL)
    raise TimeoutError(f"timed out waiting for file: {path}")


def _monitor_result(log_path: Path, report_path: Path, timeout: float) -> dict[str, Any]:
    log_path.touch(exist_ok=True)
    start = time.time()
    result: dict[str, Any] | None = None
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        # The command may finish before the host opens the log.  Start at the
        # beginning of this freshly removed file so a fast failure/pass cannot
        # hide its completion marker behind an end seek.
        handle.seek(0)
        while time.time() - start < timeout:
            line = handle.readline()
            if line:
                print(line, end="")
                if line.strip().startswith("RESULT_JSON:"):
                    result = json.loads(line.split("RESULT_JSON:", 1)[1].strip())
                if COMPLETION_MARKER in line:
                    break
            else:
                time.sleep(LOG_POLL_INTERVAL)
        else:
            raise TimeoutError(f"timed out waiting for completion marker: {log_path}")
    _wait_for_file(report_path, timeout=30)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if result is not None and result.get("status") != report.get("status"):
        raise RuntimeError("Maya RESULT_JSON and report status disagree")
    return report


# ===================================================================
# Host-side: launch a fresh GUI process and drive commandPort
# ===================================================================
def main() -> int:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    parser = argparse.ArgumentParser(description="MMD-native control-rig Maya GUI E2E")
    parser.add_argument("--maya", default="2026")
    parser.add_argument(
        "--model",
        default=str(_PROJECT_ROOT / "tests" / "data" / "mmt_test_model.pmx"),
    )
    parser.add_argument(
        "--motion",
        default=str(_PROJECT_ROOT / "tests" / "data" / "mmt_test_model_test_motion.vmd"),
    )
    parser.add_argument("--port", type=int, default=COMMAND_PORT)
    parser.add_argument("--timeout", type=float, default=TEST_TIMEOUT)
    parser.add_argument("--out-dir", default=str(_PROJECT_ROOT / "build" / "e2e"))
    args = parser.parse_args()

    out_dir = _repo_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"mmd_control_rig_e2e_maya{args.maya}.json"
    log_path = out_dir / f"mmd_control_rig_e2e_maya{args.maya}.log"
    scene_path = out_dir / f"mmd_control_rig_e2e_maya{args.maya}.ma"
    exported_vmd_path = out_dir / f"mmd_control_rig_e2e_maya{args.maya}.vmd"
    model_path = _repo_path(args.model)
    motion_path = _repo_path(args.motion)
    maya_commandport.remove_stale_logs(
        [log_path, report_path, scene_path, exported_vmd_path]
    )

    proc = None
    maya_owned = False
    try:
        if maya_commandport.is_port_open(args.port):
            raise RuntimeError(
                f"commandPort :{args.port} is already open; refusing to attach; choose a free port"
            )
        proc = maya_commandport.launch_maya(
            version=args.maya,
            project_root=_PROJECT_ROOT,
            output_dir=out_dir,
            port=args.port,
            launch_mode="explorer" if sys.platform == "win32" else "direct",
        )
        # On Windows the Explorer launcher is detached and returns no PID, so
        # commandPort preflight is the available ownership guard.  A port that
        # was already open is rejected above; a residual launch race is logged.
        maya_owned = True
        maya_commandport.wait_for_port(args.port, timeout=120, process=proc)
        logger.info("fresh Maya commandPort :%d ready", args.port)
        if proc is None:
            logger.warning(
                "Explorer launch is detached; commandPort ownership is guarded by the preflight only"
            )

        model_posix = model_path.as_posix()
        motion_posix = motion_path.as_posix()
        command = (
            "import sys\n"
            "from pathlib import Path\n"
            f"project_root = Path(r'{_PROJECT_ROOT.as_posix()}')\n"
            "if str(project_root) not in sys.path:\n"
            "    sys.path.insert(0, str(project_root))\n"
            "from tests.viewport.e2e_mmd_control_rig import run_e2e_check\n"
            f"run_e2e_check(r'{log_path.as_posix()}', r'{model_posix}', r'{motion_posix}', r'{report_path.as_posix()}', r'{scene_path.as_posix()}', r'{exported_vmd_path.as_posix()}')\n"
        )
        maya_commandport.send_python(args.port, command, label="<mmd-control-rig-e2e>")
        report = _monitor_result(log_path, report_path, args.timeout)
        logger.info("MMD control-rig E2E status: %s", report.get("status"))
        logger.info("report: %s", report_path)
        if report.get("errors"):
            for error in report["errors"]:
                logger.error("%s", str(error)[-1000:])
        return 0 if report.get("status") == "pass" else 1
    except (FileNotFoundError, TimeoutError, RuntimeError, ValueError) as exc:
        blocked = {
            "kind": "mmd-control-rig-gui-e2e",
            "status": "blocked",
            "maya": args.maya,
            "port": args.port,
            "error": str(exc),
        }
        _write_maya_report(report_path, blocked)
        logger.error("MMD control-rig GUI E2E blocked: %s", exc)
        return 2
    finally:
        if maya_owned:
            try:
                maya_commandport.quit_maya(args.port)
                time.sleep(3)
            finally:
                try:
                    if proc is not None and proc.poll() is None:
                        proc.terminate()
                finally:
                    maya_commandport.close_process_logs(proc)


if __name__ == "__main__":
    sys.exit(main())
