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
    python tests/viewport/e2e_mmd_control_rig.py --maya 2024 --create-on-import
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import math
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Iterable, Mapping

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.viewport.maya_e2e_harness import run_maya_e2e

COMMAND_PORT = 7734
COMPLETION_MARKER = "//-- MMD_CONTROL_RIG_E2E_DONE --//"
TEST_TIMEOUT = 600
MOVE_OFFSET_X = 0.35
MOVE_EPSILON = 1.0e-5
ROUNDTRIP_MATRIX_EPSILON = 5.0e-3
ROUNDTRIP_FRAMES = tuple(range(0, 6))
EVALUATION_MODE_CHOICES = ("default", "dg", "serial", "parallel")
_EVALUATION_MODE_TO_MAYA = {"dg": "off", "serial": "serial", "parallel": "parallel"}

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


def _evaluation_mode_snapshot(requested: str, cmds) -> dict[str, str]:
    """Apply and report the requested Maya evaluation mode.

    Maya exposes DG evaluation as ``off``; the report keeps the user-facing
    ``dg`` spelling while retaining the raw Maya mode for diagnostics.
    ``default`` intentionally leaves the current Maya mode untouched.
    """

    requested = str(requested or "default").lower()
    if requested not in EVALUATION_MODE_CHOICES:
        raise ValueError(f"unsupported evaluation mode: {requested}")
    target = _EVALUATION_MODE_TO_MAYA.get(requested)
    if target is not None:
        cmds.evaluationManager(mode=target)
    raw = cmds.evaluationManager(query=True, mode=True) or []
    maya_mode = str(raw[0]) if raw else "unknown"
    active = {"off": "dg"}.get(maya_mode, maya_mode)
    if target is not None and maya_mode != target:
        raise RuntimeError(
            f"requested evaluation mode {requested!r}, Maya reported {maya_mode!r}"
        )
    return {"requested": requested, "active": active, "mayaMode": maya_mode}


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


def _animation_layer_diagnostics(cmds) -> dict[str, Any]:
    """Capture animation-layer and blend ownership relevant to VMD import."""

    rows = []
    for layer in cmds.ls(type="animLayer") or []:
        layer_name = str(layer)
        try:
            attributes = [
                str(value)
                for value in (cmds.animLayer(layer, query=True, attribute=True) or [])
            ]
        except RuntimeError:
            attributes = []
        try:
            blend_nodes = [
                str(value)
                for value in (
                    cmds.listConnections(
                        layer,
                        source=False,
                        destination=True,
                        type="animBlendNodeBase",
                    )
                    or []
                )
            ]
        except RuntimeError:
            blend_nodes = []
        rows.append(
            {
                "name": layer_name,
                "attributes": sorted(set(attributes)),
                "animBlendNodes": sorted(set(blend_nodes)),
                "base": layer_name in {"BaseAnimation", "baseAnimation"},
            }
        )
    vmd_rows = [row for row in rows if row["name"] == "VMD_Motion"]
    populated_non_base = [
        row
        for row in rows
        if not row["base"] and (row["attributes"] or row["animBlendNodes"])
    ]
    return {
        "layers": sorted(rows, key=lambda row: row["name"]),
        "vmdMotion": vmd_rows,
        "populatedNonBase": populated_non_base,
        "vmdMotionOwnershipPass": not any(
            row["attributes"] or row["animBlendNodes"] for row in vmd_rows
        ),
        "singleWriterPass": not populated_non_base,
    }


def _vmd_role_diagnostics(vmd_data) -> dict[str, dict[str, Any]]:
    """Classify VMD bone roles by authored non-identity payload."""

    rows: dict[str, dict[str, Any]] = {}
    for frame in getattr(vmd_data, "bone_frames", []) or []:
        name = str(frame.bone_name)
        position = [float(value) for value in frame.position]
        rotation = [float(value) for value in frame.rotation]
        non_identity_position = any(abs(value) > MOVE_EPSILON for value in position)
        non_identity_rotation = (
            len(rotation) >= 4
            and (
                any(abs(value) > MOVE_EPSILON for value in rotation[:3])
                or abs(rotation[3] - 1.0) > MOVE_EPSILON
            )
        )
        row = rows.setdefault(
            name,
            {
                "frameCount": 0,
                "nonRestFrameCount": 0,
                "hasNonIdentityPosition": False,
                "hasNonIdentityRotation": False,
                "frames": [],
            },
        )
        row["frameCount"] += 1
        row["hasNonIdentityPosition"] |= non_identity_position
        row["hasNonIdentityRotation"] |= non_identity_rotation
        if non_identity_position or non_identity_rotation:
            row["nonRestFrameCount"] += 1
        row["frames"].append(int(frame.frame_number))
    for row in rows.values():
        row["frames"] = sorted(set(row["frames"]))
        row["identityOnly"] = row["nonRestFrameCount"] == 0
    return dict(sorted(rows.items()))


def _record_control_rig_diagnostics(
    report: dict[str, Any],
    profile: Mapping[str, Any],
    vmd_roles: Mapping[str, Mapping[str, Any]],
) -> None:
    """Persist converter diagnostics with VMD role payload classification."""

    diagnostics = dict(profile.get("mmd_control_rig") or {})
    rows = []
    for diagnostic in diagnostics.get("diagnostics", []) or []:
        detail = diagnostic.get("detail", []) if isinstance(diagnostic, Mapping) else []
        if not isinstance(detail, list):
            continue
        for role in detail:
            rows.append(
                {
                    "role": str(role),
                    **dict(vmd_roles.get(str(role), {})),
                }
            )
    diagnostics["unsupportedRoleClassification"] = rows
    converter_profile = profile.get("vmd_converter")
    if isinstance(converter_profile, Mapping):
        diagnostics["vmdConverter"] = dict(converter_profile)
    report["createOnImport"]["diagnostics"] = diagnostics


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
    evaluation_mode: str = "default",
    create_on_import: bool = False,
    auto_bake_only: bool = False,
) -> None:
    """Execute the complete control-rig workflow in a live Maya GUI.

    ``create_on_import`` is opt-in so the default invocation continues to
    exercise the legacy PMX import -> VMD import -> explicit rig-build route.
    When enabled, VMD import itself owns the transactional Control Rig create
    or reuse and direct controller keying path.

    ``auto_bake_only`` keeps the existing Control Rig edits and evidence but
    stops after the automatic Bake Timeline export gate.  It is intended for
    focused host diagnosis; normal mode retains every existing assertion and
    round-trip gate.
    """

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
        "evaluationMode": {
            "requested": str(evaluation_mode or "default"),
            "active": None,
            "mayaMode": None,
        },
        "focusedMode": {
            "autoBakeOnly": bool(auto_bake_only),
            "scope": "auto_bake_export" if auto_bake_only else "full_control_rig_roundtrip",
        },
        "model": str(model_path),
        "motion": str(motion_path),
        "createOnImport": {
            "requested": bool(create_on_import),
            "options": {
                "create_mmd_control_rig": bool(create_on_import),
                "bake_mode": False,
                "clear_existing_motion": bool(create_on_import),
            },
            "route": "vmd_import_control_rig"
            if create_on_import
            else "explicit_control_rig_build",
            "owner": None,
            "state": None,
            "rig": {},
            "diagnostics": {},
            "clearExistingMotion": {},
            "animationLayers": {},
        },
        "states": {},
        "roles": [],
        "vmdApplicability": {},
        "ikMove": {},
        "ikToggle": {},
        "autoBakeExport": {},
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

        if create_on_import:
            # Preserve Maya's script-editor diagnostics alongside the JSON
            # report so a fail-closed import exception retains its exact Maya
            # API or route error alongside the structured summary.
            history_path = log_file.with_suffix(".maya_history.log")
            try:
                cmds.scriptEditorInfo(
                    historyFilename=str(history_path),
                    writeHistory=True,
                    suppressInfo=False,
                    suppressWarnings=False,
                    suppressErrors=False,
                )
                report["createOnImport"]["mayaScriptEditorHistory"] = str(history_path)
            except Exception:
                report["createOnImport"]["mayaScriptEditorHistory"] = None

        report["evaluationMode"] = _evaluation_mode_snapshot(evaluation_mode, cmds)
        _log(
            "evaluation mode: requested=%s active=%s maya=%s"
            % (
                report["evaluationMode"]["requested"],
                report["evaluationMode"]["active"],
                report["evaluationMode"]["mayaMode"],
            )
        )

        from mmd_tools.core.mmd_control_rig_builder import (
            CONTROL_RIG_ATTACHED,
            CONTROL_RIG_BAKED,
            CONTROL_RIG_CONTROL_OWNED,
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
        vmd_role_diagnostics = _vmd_role_diagnostics(source_vmd)
        if create_on_import:
            report["createOnImport"]["vmdRoles"] = vmd_role_diagnostics

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

        # Seed one target joint with an out-of-band key only for the opt-in
        # route.  The VMD import's clear_existing_motion=True contract must
        # remove it before authored Control Rig keys are created.
        clear_seed: dict[str, Any] = {}
        if create_on_import:
            seed_joint = None
            for candidate in sorted(
                source_vmd.bone_frames,
                key=lambda item: (int(item.frame_number), str(item.bone_name)),
            ):
                seed_joint = _find_joint_for_mmd_name(candidate.bone_name, cmds)
                if seed_joint:
                    break
            if not seed_joint:
                raise RuntimeError(
                    "fixture VMD has no PMX joint available for clear-existing-motion seed"
                )
            seed_frame = 999
            seed_attr = "rotateX"
            cmds.setKeyframe(seed_joint, attribute=seed_attr, time=seed_frame, value=17.0)
            clear_seed = {
                "node": seed_joint,
                "attribute": seed_attr,
                "frame": seed_frame,
                "seeded": True,
            }

        vmd_profile: dict[str, Any] = {}
        vmd_options = {"target_model": root, "pmx_path": str(model_path)}
        if create_on_import:
            vmd_options.update(
                {
                    "create_mmd_control_rig": True,
                    "bake_mode": False,
                    "clear_existing_motion": True,
                    "profile": vmd_profile,
                }
            )
        try:
            imported_motion = import_mmd_file(
                str(motion_path),
                options=vmd_options,
            )
        except Exception:
            if create_on_import:
                _record_control_rig_diagnostics(
                    report,
                    vmd_profile,
                    vmd_role_diagnostics,
                )
                report["createOnImport"]["animationLayers"] = (
                    _animation_layer_diagnostics(cmds)
                )
            raise
        if not imported_motion:
            if create_on_import:
                _record_control_rig_diagnostics(
                    report,
                    vmd_profile,
                    vmd_role_diagnostics,
                )
                report["createOnImport"]["animationLayers"] = (
                    _animation_layer_diagnostics(cmds)
                )
            raise RuntimeError(f"VMD import returned no result: {motion_path}")
        _log(f"imported VMD: {motion_path}")

        if create_on_import:
            remaining_seed_frames = [
                int(value)
                for value in (
                    cmds.keyframe(
                        clear_seed["node"],
                        attribute=clear_seed["attribute"],
                        query=True,
                        timeChange=True,
                    )
                    or []
                )
            ]
            clear_seed["remainingFrames"] = remaining_seed_frames
            clear_seed["pass"] = clear_seed["frame"] not in remaining_seed_frames
            report["createOnImport"]["clearExistingMotion"] = clear_seed
            if not clear_seed["pass"]:
                raise RuntimeError(
                    "clear_existing_motion=True left the seeded target-joint key"
                )
            _record_control_rig_diagnostics(
                report,
                vmd_profile,
                vmd_role_diagnostics,
            )

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

        metadata_before_build = read_mmd_control_rig_metadata(root)
        rig = build_mmd_control_rig(root)
        report["states"]["afterBuild"] = rig.state
        report["roles"] = sorted(str(role) for role in rig.controls)
        if create_on_import:
            metadata = read_mmd_control_rig_metadata(root)
            if not metadata:
                raise RuntimeError("VMD create-on-import did not persist control-rig metadata")
            report["createOnImport"].update(
                {
                    "owner": metadata.get("owner"),
                    "state": metadata.get("state"),
                    "rig": {
                        "metadataPresentBeforeImport": metadata_before_build is not None,
                        "createdByImport": metadata_before_build is None,
                        "reusedLookup": not bool(rig.created),
                        "buildResultCreated": bool(rig.created),
                        "controlCount": len(rig.controls),
                    },
                }
            )
            if metadata.get("owner") != CONTROL_RIG_CONTROL_OWNED:
                raise RuntimeError(
                    "create-on-import did not make Control Rig the motion owner: "
                    f"{metadata.get('owner')}"
                )
            if metadata.get("state") != CONTROL_RIG_EDIT:
                raise RuntimeError(
                    "create-on-import did not enter EDIT state: "
                    f"{metadata.get('state')}"
                )
            if rig.owner != CONTROL_RIG_CONTROL_OWNED or rig.state != CONTROL_RIG_EDIT:
                raise RuntimeError(
                    "build lookup disagrees with create-on-import ownership/state: "
                    f"owner={rig.owner} state={rig.state}"
                )
            animation_layers = _animation_layer_diagnostics(cmds)
            report["createOnImport"]["animationLayers"] = animation_layers
            if not animation_layers["vmdMotionOwnershipPass"]:
                raise RuntimeError(
                    "create-on-import created VMD_Motion animLayer/animBlend ownership"
                )
            if not animation_layers["singleWriterPass"]:
                raise RuntimeError(
                    "create-on-import left populated non-base animation-layer ownership"
                )
        elif rig.state != CONTROL_RIG_ATTACHED:
            raise RuntimeError(f"build did not produce ATTACHED state: {rig.state}")
        if "left_foot_ik" not in rig.controls:
            raise RuntimeError("fixture has no left_foot_ik control")
        _log(f"built control rig ({len(rig.controls)} controls)")

        metadata = read_mmd_control_rig_metadata(root)
        if not metadata:
            raise RuntimeError("control-rig metadata missing after build")
        if create_on_import:
            report["createOnImport"]["owner"] = metadata.get("owner")
            report["createOnImport"]["state"] = metadata.get("state")
        solver, effector = _resolve_foot_solver(root, metadata, cmds)
        control = str(rig.controls["left_foot_ik"])
        _log(f"left foot control={control}, solver={solver}, effector={effector}")

        edit_metadata = (
            metadata if create_on_import else enter_mmd_control_rig_edit(root)
        )
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
        if not report["ikMove"]["pass"] and not auto_bake_only:
            raise RuntimeError("left foot IK move did not produce an owned solver response")
        if not report["ikMove"]["pass"] and auto_bake_only:
            _log("focused auto-bake mode: retaining failed IK move evidence")

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
        if not report["ikToggle"]["pass"] and not auto_bake_only:
            raise RuntimeError("ikEnabled toggle did not reach mmdCcdIk.enabled")
        if not report["ikToggle"]["pass"] and auto_bake_only:
            _log("focused auto-bake mode: retaining failed IK toggle evidence")

        # Exercise the production user-path while the Control Rig still owns
        # the authoring motion.  The preparation boundary must temporarily
        # bake to MMD inputs, publish a parseable VMD, validate the restored
        # EDIT token, and clean up its private stage before the explicit
        # manual-bake route below continues.
        from mmd_tools.adapters.maya_vmd_prepare_backend import (
            create_maya_vmd_prepare_action,
        )
        from mmd_tools.services.export_workflow_service import (
            ExportWorkflowRequest,
            ExportWorkflowService,
        )

        auto_output = Path(exported_vmd_path).with_suffix(".auto_bake.vmd")
        timeline_range = (
            float(cmds.playbackOptions(query=True, minTime=True)),
            float(cmds.playbackOptions(query=True, maxTime=True)),
        )
        auto_options = {
            "export_format": "vmd",
            "export_strategy": "bake_timeline",
            "current_model_root": root,
            "target_model": root,
            "require_current_model": True,
            "require_target": True,
            "frame_range": timeline_range,
            "frame_step": 1.0,
        }
        auto_request = ExportWorkflowRequest(str(auto_output), auto_options)
        auto_action = None
        auto_token = None
        auto_gate = {
            "status": "running",
            "outputPath": str(auto_output),
            "frameRange": list(timeline_range),
        }
        report["autoBakeExport"] = auto_gate
        try:
            auto_action = create_maya_vmd_prepare_action()
            auto_service = ExportWorkflowService(prepare_vmd_action=auto_action)
            prepared = auto_service.prepare_vmd(auto_request)
            if not prepared.succeeded or prepared.token is None:
                raise RuntimeError(f"automatic Bake Timeline prepare failed: {prepared.error}")
            auto_token = prepared.token
            staged_path = Path(auto_token.staged_artifact.file_path)
            staged_vmd = VmdData().parse_file(str(staged_path))
            auto_gate.update(
                {
                    "stagedPath": str(staged_path),
                    "stagedBoneFrames": len(staged_vmd.bone_frames),
                    "stagedParsePass": bool(staged_vmd.bone_frames),
                }
            )
            if not staged_vmd.bone_frames:
                raise RuntimeError("automatic Bake Timeline staged VMD contains no bone frames")

            restored_metadata = read_mmd_control_rig_metadata(root)
            auto_gate["restoredState"] = (
                {
                    "state": restored_metadata.get("state"),
                    "owner": restored_metadata.get("owner"),
                }
                if restored_metadata
                else None
            )
            restored_pass = bool(
                restored_metadata
                and restored_metadata.get("state") == CONTROL_RIG_EDIT
                and restored_metadata.get("owner") == CONTROL_RIG_CONTROL_OWNED
            )
            auto_gate["restoredEditPass"] = restored_pass
            if not restored_pass:
                raise RuntimeError(
                    "automatic Bake Timeline did not restore EDIT/CONTROL_OWNED: "
                    f"{restored_metadata}"
                )

            validation = auto_service.validate(
                ExportWorkflowRequest(
                    str(auto_output),
                    dict(auto_options),
                    prepared_vmd_token=auto_token,
                )
            )
            token_validation_pass = bool(
                validation.error is None and not validation.report.is_blocking
            )
            auto_gate["tokenValidation"] = {
                "state": validation.state,
                "pass": token_validation_pass,
            }
            if not token_validation_pass:
                raise RuntimeError(
                    f"restored-scene token validation failed: {validation.error or validation.report.summary}"
                )

            published = auto_service.execute(
                ExportWorkflowRequest(
                    str(auto_output),
                    dict(auto_options),
                    prepared_vmd_token=auto_token,
                )
            )
            published_vmd = VmdData().parse_file(str(auto_output))
            published_pass = bool(
                published.succeeded
                and auto_output.is_file()
                and published_vmd.bone_frames
            )
            auto_gate.update(
                {
                    "publishedState": published.state,
                    "publishedBoneFrames": len(published_vmd.bone_frames),
                    "publishedParsePass": bool(published_vmd.bone_frames),
                    "pass": published_pass,
                }
            )
            if not published_pass:
                raise RuntimeError(
                    f"automatic Bake Timeline publish/parse failed: {published.error}"
                )
            auto_gate["status"] = "pass"
        except Exception as exc:
            auto_gate["status"] = "fail"
            auto_gate["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if auto_action is not None:
                try:
                    if auto_token is not None:
                        auto_action.invalidate(auto_token)
                    else:
                        auto_action.close()
                except Exception as exc:
                    auto_gate["cleanupError"] = f"{type(exc).__name__}: {exc}"
                    raise
                else:
                    auto_gate["cleanupPass"] = True

        if auto_bake_only:
            report["status"] = "pass"
            _log("PASS: focused automatic Bake Timeline export gate passed")
            return

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
    parser.add_argument(
        "--evaluation-mode",
        choices=EVALUATION_MODE_CHOICES,
        default="default",
        help="Maya evaluation mode (default preserves the current Maya setting)",
    )
    parser.add_argument(
        "--create-on-import",
        action="store_true",
        help=(
            "Create or reuse the MMD Control Rig during VMD import, key "
            "controllers directly, and clear existing motion"
        ),
    )
    parser.add_argument(
        "--auto-bake-only",
        action="store_true",
        help=(
            "Run the Control Rig edits and automatic Bake Timeline export gate, "
            "then stop before manual bake and round-trip gates"
        ),
    )
    parser.add_argument("--out-dir", default=str(_PROJECT_ROOT / "build" / "e2e"))
    args = parser.parse_args()

    out_dir = _repo_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    mode_suffix = "" if args.evaluation_mode == "default" else f"_{args.evaluation_mode}"
    route_suffix = "_create_on_import" if args.create_on_import else ""
    focused_suffix = "_auto_bake_only" if args.auto_bake_only else ""
    output_suffix = f"{mode_suffix}{route_suffix}{focused_suffix}"
    report_path = out_dir / f"mmd_control_rig_e2e_maya{args.maya}{output_suffix}.json"
    log_path = out_dir / f"mmd_control_rig_e2e_maya{args.maya}{output_suffix}.log"
    scene_path = out_dir / f"mmd_control_rig_e2e_maya{args.maya}{output_suffix}.ma"
    exported_vmd_path = out_dir / f"mmd_control_rig_e2e_maya{args.maya}{output_suffix}.vmd"
    auto_exported_vmd_path = exported_vmd_path.with_suffix(".auto_bake.vmd")
    model_path = _repo_path(args.model)
    motion_path = _repo_path(args.motion)
    try:
        model_posix = model_path.as_posix()
        motion_posix = motion_path.as_posix()
        command = (
            "import sys\n"
            "from pathlib import Path\n"
            f"project_root = Path(r'{_PROJECT_ROOT.as_posix()}')\n"
            "if str(project_root) not in sys.path:\n"
            "    sys.path.insert(0, str(project_root))\n"
            "from tests.viewport.e2e_mmd_control_rig import run_e2e_check\n"
            f"run_e2e_check(r'{log_path.as_posix()}', r'{model_posix}', r'{motion_posix}', r'{report_path.as_posix()}', r'{scene_path.as_posix()}', r'{exported_vmd_path.as_posix()}', r'{args.evaluation_mode}', {bool(args.create_on_import)!r}, {bool(args.auto_bake_only)!r})\n"
        )
        report = run_maya_e2e(
            project_root=_PROJECT_ROOT,
            version=args.maya,
            out_dir=out_dir,
            port=args.port,
            timeout=args.timeout,
            log_path=log_path,
            report_path=report_path,
            command=command,
            marker=COMPLETION_MARKER,
            send_label="<mmd-control-rig-e2e>",
            stale_paths=[
                log_path,
                report_path,
                scene_path,
                exported_vmd_path,
                auto_exported_vmd_path,
            ],
            port_error=(
                f"commandPort :{args.port} is already open; refusing to attach; choose a free port"
            ),
            report_error=f"timed out waiting for file: {report_path}",
            log_ready=logger,
            warn_detached=True,
        )
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
            "evaluationMode": args.evaluation_mode,
            "autoBakeOnly": bool(args.auto_bake_only),
            "error": str(exc),
        }
        _write_maya_report(report_path, blocked)
        logger.error("MMD control-rig GUI E2E blocked: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
