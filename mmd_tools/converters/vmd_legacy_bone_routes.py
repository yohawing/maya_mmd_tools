"""Legacy bone-key routing helpers for VMD conversion."""

from __future__ import annotations

import json
from typing import Dict, List, Optional

import maya.cmds as cmds

from ..core.mmd_control_rig_motion import (
    control_rig_edit_authoring_bases_for_joints,
    control_rig_edit_routes_for_joints,
    control_rig_fixed_axis_twist_joints,
)
from ..core.model_registry import get_model_registry
from .vmd_runtime_rig_helper import _ls_mmd_ccd_ik_nodes


_PHYSICS_PRE_INPUT_ATTRS = {
    "translateX": "inPreTranslateX",
    "translateY": "inPreTranslateY",
    "translateZ": "inPreTranslateZ",
    "rotateX": "inPreRotateX",
    "rotateY": "inPreRotateY",
    "rotateZ": "inPreRotateZ",
}


def _canonical_dag_path(node: str) -> Optional[str]:
    """Return one unambiguous long DAG path, or ``None``."""

    try:
        matches = cmds.ls(node, long=True) or []
    except Exception:
        return None
    return str(matches[0]) if len(matches) == 1 else None


def _physics_pre_input_routes(
    joints,
) -> tuple[Dict[str, Dict[str, tuple[str, str]]], Dict[str, str]]:
    """Resolve model-owned physics pre-input routes for mapped joints.

    A route is accepted only when the driver has one solver owner, that solver
    has one model-root owner containing the target joint, and both the target
    and bone index are unique. Ambiguous scene ownership therefore never
    redirects imported keys to an arbitrary model or driver.
    """

    mapped = {}
    for joint in joints:
        canonical = _canonical_dag_path(str(joint))
        if canonical:
            mapped[canonical] = str(joint)
    if not mapped:
        return {}, {}

    try:
        solvers = sorted({str(node) for node in (cmds.ls(type="mmdPhysicsSolver") or [])})
    except Exception:
        return {}, {}

    driver_solvers: Dict[str, set[str]] = {}
    solver_roots: Dict[str, Optional[str]] = {}
    for solver in solvers:
        try:
            roots = cmds.listConnections(
                f"{solver}.modelRoot", source=True, destination=False
            ) or []
            registries = cmds.listConnections(
                f"{solver}.modelRegistry", source=True, destination=False
            ) or []
        except Exception:
            roots = []
            registries = []
        canonical_roots = {
            value
            for root in roots
            if (value := _canonical_dag_path(str(root))) is not None
        }
        direct_root = (
            next(iter(canonical_roots))
            if len(roots) == 1 and len(canonical_roots) == 1
            else None
        )
        registry_root = None
        if not roots and len(registries) == 1:
            registry = str(registries[0])
            try:
                registry_roots = cmds.listConnections(
                    f"{registry}.modelRoot",
                    source=True,
                    destination=False,
                ) or []
            except Exception:
                registry_roots = []
            canonical_registry_roots = {
                value
                for root in registry_roots
                if (value := _canonical_dag_path(str(root))) is not None
            }
            if len(registry_roots) == 1 and len(canonical_registry_roots) == 1:
                candidate_root = next(iter(canonical_registry_roots))
                try:
                    validated_registry = get_model_registry(candidate_root)
                except Exception:
                    validated_registry = None
                if str(validated_registry or "") == registry:
                    registry_root = candidate_root
        solver_roots[solver] = direct_root or registry_root
        for output_attr in ("outBoneMatrices", "outBoneCount", "outSolved"):
            try:
                drivers = cmds.listConnections(
                    f"{solver}.{output_attr}",
                    source=False,
                    destination=True,
                    type="mmdPhysicsBoneDriver",
                ) or []
            except Exception:
                continue
            for driver in drivers:
                driver_solvers.setdefault(str(driver), set()).add(solver)

    candidates = []
    blocked_joints = set()
    for driver, owners in sorted(driver_solvers.items()):
        try:
            has_target = cmds.attributeQuery(
                "mmd_target_joint_message", node=driver, exists=True
            )
            targets = (
                cmds.listConnections(
                    f"{driver}.mmd_target_joint_message",
                    source=True,
                    destination=False,
                    type="joint",
                )
                or []
                if has_target
                else []
            )
        except Exception:
            targets = []
        canonical_targets = [
            value
            for target in targets
            if (value := _canonical_dag_path(str(target))) is not None
        ]
        mapped_targets = [target for target in canonical_targets if target in mapped]
        if not mapped_targets:
            continue
        if len(targets) != 1 or len(mapped_targets) != 1 or len(owners) != 1:
            blocked_joints.update(mapped_targets)
            continue

        target = mapped_targets[0]
        solver = next(iter(owners))
        root = solver_roots.get(solver)
        if root is None or not (target == root or target.startswith(f"{root}|")):
            blocked_joints.add(target)
            continue
        try:
            pre_inputs_exist = all(
                bool(cmds.attributeQuery(attr, node=driver, exists=True))
                for attr in _PHYSICS_PRE_INPUT_ATTRS.values()
            )
            has_index = cmds.attributeQuery("inBoneIndex", node=driver, exists=True)
            bone_index = int(cmds.getAttr(f"{driver}.inBoneIndex")) if has_index else -1
        except (TypeError, ValueError, RuntimeError, OverflowError):
            pre_inputs_exist = False
            bone_index = -1
        if not pre_inputs_exist or bone_index < 0:
            blocked_joints.add(target)
            continue
        candidates.append((target, driver, bone_index))

    target_counts: Dict[str, int] = {}
    index_counts: Dict[tuple[str, int], int] = {}
    for target, driver, bone_index in candidates:
        target_counts[target] = target_counts.get(target, 0) + 1
        root = solver_roots[next(iter(driver_solvers[driver]))]
        index_key = (str(root), bone_index)
        index_counts[index_key] = index_counts.get(index_key, 0) + 1

    routes = {}
    blocked = {
        mapped[target]: "ambiguous_or_unowned_physics_driver"
        for target in blocked_joints
    }
    for target, driver, bone_index in candidates:
        root = solver_roots[next(iter(driver_solvers[driver]))]
        if (
            target in blocked_joints
            or target_counts[target] != 1
            or index_counts[(str(root), bone_index)] != 1
        ):
            blocked[mapped[target]] = "duplicate_physics_target_or_bone_index"
            continue
        routes[mapped[target]] = {
            source_attr: (driver, target_attr)
            for source_attr, target_attr in _PHYSICS_PRE_INPUT_ATTRS.items()
        }
    return routes, blocked


def collect_ik_link_joints() -> dict:
    """Collect IK link joints driven by mmdCcdIk outputRotate."""
    ik_link_joints: dict = {}
    for node in _ls_mmd_ccd_ik_nodes():
        try:
            raw_chain = cmds.getAttr(f"{node}.chainJson")
            cfg = json.loads(raw_chain) if raw_chain else {}
        except Exception:
            continue

        links = cfg.get("links", [])
        for link_index, link in enumerate(links):
            dests = cmds.listConnections(
                f"{node}.outputRotate[{link_index}]",
                s=False,
                d=True,
                p=True,
            ) or []
            bone_slot = link.get("bone_slot", link_index)
            for dest in dests:
                jnt = dest.split(".", 1)[0]
                info = {"solver": node, "slot": bone_slot}
                ik_link_joints[jnt] = info
                try:
                    for long_name in cmds.ls(jnt, long=True) or []:
                        ik_link_joints[long_name] = info
                except Exception:
                    pass
    return ik_link_joints


def native_ik_handle_link_joints(handle: str) -> List[str]:
    """Return native IK handle link joints recorded on a Maya node."""
    if not cmds.attributeQuery("mmd_ik_link_joints_json", node=handle, exists=True):
        return []
    try:
        raw = cmds.getAttr(f"{handle}.mmd_ik_link_joints_json") or "[]"
        links = json.loads(raw)
    except Exception:
        return []
    return [j for j in links if isinstance(j, str) and cmds.objExists(j)]


def build_legacy_bone_key_routes(converter) -> Dict[str, dict]:
    """Build per-joint key routes for legacy sparse VMD bone animation."""
    append_info = converter._collect_append_info()
    ik_link_joints = converter._collect_ik_link_joints()
    control_routes = control_rig_edit_routes_for_joints(converter.bone_name_mapping.values())
    authoring_bases = control_rig_edit_authoring_bases_for_joints(
        converter.bone_name_mapping.values()
    )
    fixed_axis_twist_joints = control_rig_fixed_axis_twist_joints(
        converter.bone_name_mapping.values()
    )
    physics_routes, blocked_physics_routes = _physics_pre_input_routes(
        converter.bone_name_mapping.values()
    )
    routes: Dict[str, dict] = {}

    for joint in set(converter.bone_name_mapping.values()):
        ik_info = ik_link_joints.get(joint)
        control_route = control_routes.get(joint, {})
        route = {
            "attr_targets": {},
            "skip_rotate": joint in ik_link_joints,
            "ik_solver_rotate": ik_info,
            "control_owned": bool(control_route),
            "control_owned_channels": tuple(sorted(control_route)),
            "quaternion_interpolation_safe": False,
            "fixed_axis_twist": joint in fixed_axis_twist_joints,
            # EDIT inserts a live basis converter between complete XYZ
            # controls and joint.rotate. Author control keys in that persisted
            # basis so the converter reconstructs the original joint-space
            # quaternion. FixedAxis Twist keeps X/Y hidden and locked in the
            # UI, but still authors the complete compound for exact playback.
            "authoring_basis": authoring_bases.get(joint),
        }
        info = append_info.get(joint)
        if info:
            append_node = info.get("node")
            for src_attr, dst_attr in info.get("attr_map", {}).items():
                if append_node:
                    route["attr_targets"][src_attr] = (append_node, dst_attr)

        # In EDIT, the owned curve is the authored animation input. Unsupported
        # bones and solver-output links retain the established legacy route.
        route["attr_targets"].update(control_route)
        # A complete owned component route is authored as one quaternion
        # track in the controller's persisted authoring basis.
        route["quaternion_interpolation_safe"] = (
            all(channel in control_route for channel in ("rotateX", "rotateY", "rotateZ"))
        )

        # A CONTROL_OWNED MMD Control Rig is a single-writer path: when all
        # rotation channels are routed to an owned controller, author those
        # channels there and do not also key the solver's ``inputRotate``
        # array.  The latter is a connected/locked solver input in EDIT and
        # can both fail API key creation and violate controller ownership.
        if all(channel in route["attr_targets"] for channel in ("rotateX", "rotateY", "rotateZ")):
            route["skip_rotate"] = False
            route["ik_solver_rotate"] = None

        # Physics remains the final joint owner. Route otherwise-direct keys
        # into its pre-physics inputs, while preserving established append,
        # Control Rig, and IK ownership decisions above.
        for channel, target in physics_routes.get(joint, {}).items():
            route["attr_targets"].setdefault(channel, target)

        if joint in blocked_physics_routes:
            blocked_channels = set(_PHYSICS_PRE_INPUT_ATTRS).difference(
                route["attr_targets"]
            )
            if route["skip_rotate"]:
                blocked_channels.difference_update(
                    {"rotateX", "rotateY", "rotateZ"}
                )
            if blocked_channels:
                route["blocked_channels"] = tuple(sorted(blocked_channels))
                route["block_reason"] = blocked_physics_routes[joint]

        if (
            route["attr_targets"]
            or route["skip_rotate"]
            or ik_info
            or route.get("blocked_channels")
        ):
            routes[joint] = route

    return routes
