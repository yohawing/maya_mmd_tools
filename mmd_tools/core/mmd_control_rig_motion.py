"""Move imported MMD animation ownership between joints and curve controls.

EDIT preserves existing animation nodes by reconnecting their outputs to the
owned curve controls. ATTACHED restores the original dependency-graph edges.
The imported skeleton hierarchy and solver-owned output joints are untouched.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
from typing import Any, Dict, List, Mapping, Optional, Tuple

import maya.api.OpenMaya as om

from mmd_tools.core.constants import ATTR_MMD_CONTROL_RIG_JSON
from mmd_tools.core.humanik_utils import maya_cmds
from mmd_tools.core.mmd_control_rig_builder import (
    CONTROL_RIG_ATTACHED,
    CONTROL_RIG_BAKED,
    CONTROL_RIG_EDIT,
    MmdControlRigBuildError,
    read_mmd_control_rig_metadata,
)


_CHANNELS = ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
_SAFE_ANIMATION_TYPES = ("animCurve", "animBlendNode")
_SAFE_ANIMATION_NODES = frozenset({"pairBlend", "unitConversion"})


def control_rig_edit_routes_for_joints(joints, *, cmds_module=None) -> Dict[str, Dict[str, Tuple[str, str]]]:
    """Return VMD key destinations for joints owned by control rigs in EDIT."""
    cmds = cmds_module or maya_cmds()
    wanted = set()
    for joint in joints:
        matches = cmds.ls(joint, long=True) or []
        wanted.add(str(matches[0]) if len(matches) == 1 else str(joint))
    routes: Dict[str, Dict[str, Tuple[str, str]]] = {}
    roots = cmds.ls(f"*.{ATTR_MMD_CONTROL_RIG_JSON}", objectsOnly=True, long=True) or []
    for root in roots:
        metadata = read_mmd_control_rig_metadata(str(root), cmds_module=cmds)
        if not metadata or metadata["state"] != CONTROL_RIG_EDIT:
            continue
        for role, binding in metadata.get("bindings", {}).items():
            joint = str(binding.get("joint", ""))
            matches = cmds.ls(joint, long=True) or []
            joint = str(matches[0]) if len(matches) == 1 else joint
            if joint not in wanted:
                continue
            control_uuid = metadata.get("controls", {}).get(role)
            if not control_uuid:
                continue
            control = _resolve_uuid(cmds, control_uuid)
            for target in _expanded_authored_plugs(binding):
                channel = target.rsplit(".", 1)[-1]
                if channel in _CHANNELS:
                    routes.setdefault(joint, {})[channel] = (control, channel)
    return routes


def enter_mmd_control_rig_edit(model_root: str, *, cmds_module=None) -> Dict[str, Any]:
    """Route MMD authored inputs through owned controls without recreating keys."""
    cmds = cmds_module or maya_cmds()
    root = _canonical_node(cmds, model_root)
    metadata = read_mmd_control_rig_metadata(root, cmds_module=cmds)
    if metadata is None:
        raise MmdControlRigBuildError("build the MMD control rig before entering EDIT")
    if metadata["state"] == CONTROL_RIG_EDIT:
        return metadata
    if metadata["state"] not in {CONTROL_RIG_ATTACHED, CONTROL_RIG_BAKED}:
        raise MmdControlRigBuildError(f"cannot enter EDIT from {metadata['state']}")

    controls = {
        role: _resolve_uuid(cmds, uuid)
        for role, uuid in metadata.get("controls", {}).items()
    }
    operations: List[Tuple[str, str, str]] = []
    journal: Dict[str, Any] = {"channels": [], "offsetParentMatrix": [], "ikEnabled": []}
    claimed_targets = set()

    with _undo_chunk(cmds, "Enter MMD Control Rig Edit"):
        try:
            for role, binding in metadata.get("bindings", {}).items():
                control = controls.get(role)
                if control is None:
                    raise MmdControlRigBuildError(f"missing owned control for {role}")
                for target in _expanded_authored_plugs(binding):
                    if target in claimed_targets:
                        continue
                    claimed_targets.add(target)
                    channel = target.rsplit(".", 1)[-1]
                    if channel not in _CHANNELS:
                        raise MmdControlRigBuildError(f"unsupported authored channel: {target}")
                    control_plug = f"{control}.{channel}"
                    incoming = cmds.listConnections(
                        target, source=True, destination=False, plugs=True
                    ) or []
                    if len(incoming) > 1:
                        raise MmdControlRigBuildError(f"multiple incoming sources: {target}")
                    control_incoming = cmds.listConnections(
                        control_plug, source=True, destination=False, plugs=True
                    ) or []
                    if control_incoming:
                        raise MmdControlRigBuildError(f"control channel already driven: {control_plug}")

                    value = float(cmds.getAttr(target))
                    source = str(incoming[0]) if incoming else None
                    if source is not None:
                        _require_animation_source(cmds, source, target)
                        cmds.disconnectAttr(source, target)
                        operations.append(("connect", source, target))
                        cmds.connectAttr(source, control_plug, force=False)
                        operations.append(("disconnect", source, control_plug))
                    else:
                        cmds.setAttr(control_plug, value)
                    cmds.connectAttr(control_plug, target, force=False)
                    operations.append(("disconnect", control_plug, target))
                    journal["channels"].append(
                        {"source": source, "control": control_plug, "target": target, "value": value}
                    )

                _zero_control_display_offset(cmds, control, journal)

                if role in {"left_foot_ik", "right_foot_ik"}:
                    _connect_ik_enabled(cmds, control, binding, journal, operations)

            metadata["journal"] = journal
            metadata["state"] = CONTROL_RIG_EDIT
            _write_metadata(cmds, root, metadata)
        except Exception:
            _rollback(cmds, operations)
            _restore_offsets(cmds, journal.get("offsetParentMatrix", []))
            raise
    return metadata


def restore_mmd_control_rig_attached(model_root: str, *, cmds_module=None) -> Dict[str, Any]:
    """Restore the exact pre-EDIT animation connections and channel values."""
    cmds = cmds_module or maya_cmds()
    root = _canonical_node(cmds, model_root)
    metadata = read_mmd_control_rig_metadata(root, cmds_module=cmds)
    if metadata is None:
        raise MmdControlRigBuildError("MMD control rig metadata is missing")
    if metadata["state"] == CONTROL_RIG_ATTACHED:
        return metadata
    if metadata["state"] == CONTROL_RIG_BAKED:
        metadata["state"] = CONTROL_RIG_ATTACHED
        _write_metadata(cmds, root, metadata)
        return metadata
    if metadata["state"] != CONTROL_RIG_EDIT:
        raise MmdControlRigBuildError(f"cannot restore ATTACHED from {metadata['state']}")

    journal = metadata.get("journal")
    if not isinstance(journal, dict):
        raise MmdControlRigBuildError("EDIT connection journal is missing")
    with _undo_chunk(cmds, "Restore MMD Control Rig Attached"):
        for row in reversed(journal.get("ikEnabled", [])):
            source, target = row["control"], row["target"]
            if cmds.isConnected(source, target):
                cmds.disconnectAttr(source, target)
            prior = row.get("source")
            if prior:
                cmds.connectAttr(prior, target, force=False)
            else:
                cmds.setAttr(target, bool(row["value"]))
        for row in reversed(journal.get("channels", [])):
            control, target = row["control"], row["target"]
            if cmds.isConnected(control, target):
                cmds.disconnectAttr(control, target)
            source = row.get("source")
            if source:
                if cmds.isConnected(source, control):
                    cmds.disconnectAttr(source, control)
                cmds.connectAttr(source, target, force=False)
            else:
                cmds.setAttr(target, float(row["value"]))
        _restore_offsets(cmds, journal.get("offsetParentMatrix", []))
        metadata.pop("journal", None)
        metadata["state"] = CONTROL_RIG_ATTACHED
        _write_metadata(cmds, root, metadata)
    return metadata


def bake_mmd_control_rig(model_root: str, *, cmds_module=None) -> Dict[str, Any]:
    """Commit controller animation edges back to MMD authored inputs."""
    cmds = cmds_module or maya_cmds()
    root = _canonical_node(cmds, model_root)
    metadata = read_mmd_control_rig_metadata(root, cmds_module=cmds)
    if metadata is None or metadata.get("state") != CONTROL_RIG_EDIT:
        state = metadata.get("state") if metadata else "missing"
        raise MmdControlRigBuildError(f"cannot bake MMD control rig from {state}")
    journal = metadata.get("journal")
    if not isinstance(journal, dict):
        raise MmdControlRigBuildError("EDIT connection journal is missing")

    rows = list(journal.get("ikEnabled", [])) + list(journal.get("channels", []))
    sources_by_control = {}
    for row in rows:
        incoming = cmds.listConnections(
            row["control"], source=True, destination=False, plugs=True
        ) or []
        if len(incoming) > 1:
            raise MmdControlRigBuildError(
                f"multiple controller animation inputs: {row['control']}"
            )
        source = str(incoming[0]) if incoming else None
        if source:
            _require_animation_source(cmds, source, row["target"])
        sources_by_control[row["control"]] = source

    with _undo_chunk(cmds, "Bake MMD Control Rig"):
        for row in reversed(journal.get("ikEnabled", [])):
            _commit_control_input(cmds, row, sources_by_control[row["control"]])
        for row in reversed(journal.get("channels", [])):
            _commit_control_input(cmds, row, sources_by_control[row["control"]])
        _restore_offsets(cmds, journal.get("offsetParentMatrix", []))
        metadata.pop("journal", None)
        metadata["state"] = CONTROL_RIG_BAKED
        _write_metadata(cmds, root, metadata)
    return metadata


def _expanded_authored_plugs(binding: Mapping[str, Any]) -> Tuple[str, ...]:
    plugs = []
    for plug in binding.get("authoredPlugs", []):
        if plug.endswith(".translate"):
            plugs.extend(f"{plug}{axis}" for axis in "XYZ")
        elif plug.endswith(".rotate"):
            plugs.extend(f"{plug}{axis}" for axis in "XYZ")
        else:
            plugs.append(str(plug))
    return tuple(plugs)


def _require_animation_source(cmds, source: str, target: str) -> None:
    node = source.split(".", 1)[0]
    node_type = str(cmds.nodeType(node))
    if node_type in _SAFE_ANIMATION_NODES or node_type.startswith(_SAFE_ANIMATION_TYPES):
        return
    raise MmdControlRigBuildError(
        f"non-animation input blocks EDIT: {source} -> {target} ({node_type})"
    )


def _connect_ik_enabled(cmds, control, binding, journal, operations) -> None:
    solvers = binding.get("ikSolvers", [])
    if not solvers:
        return
    if not cmds.attributeQuery("ikEnabled", node=control, exists=True):
        cmds.addAttr(control, longName="ikEnabled", attributeType="bool", keyable=True)
    control_plug = f"{control}.ikEnabled"
    for solver in solvers:
        target = f"{solver}.enabled"
        if not cmds.objExists(target):
            raise MmdControlRigBuildError(f"IK solver enabled input is missing: {target}")
        incoming = cmds.listConnections(target, source=True, destination=False, plugs=True) or []
        if len(incoming) > 1:
            raise MmdControlRigBuildError(f"multiple IK enabled sources: {target}")
        source = str(incoming[0]) if incoming else None
        value = bool(cmds.getAttr(target))
        if source:
            _require_animation_source(cmds, source, target)
            control_incoming = cmds.listConnections(
                control_plug, source=True, destination=False, plugs=True
            ) or []
            if control_incoming and str(control_incoming[0]) != source:
                raise MmdControlRigBuildError(
                    f"IK solvers have different enabled animation sources: {control}"
                )
            cmds.disconnectAttr(source, target)
            operations.append(("connect", source, target))
            if not control_incoming:
                cmds.connectAttr(source, control_plug, force=False)
                operations.append(("disconnect", source, control_plug))
        elif not (cmds.listConnections(control_plug, source=True, destination=False) or []):
            cmds.setAttr(control_plug, value)
        cmds.connectAttr(control_plug, target, force=False)
        operations.append(("disconnect", control_plug, target))
        journal["ikEnabled"].append(
            {"source": source, "control": control_plug, "target": target, "value": value}
        )


def _zero_control_display_offset(cmds, control: str, journal: Dict[str, Any]) -> None:
    """Cancel the current authored local value visually through OPM."""
    plug = f"{control}.offsetParentMatrix"
    if not cmds.objExists(plug):
        return
    incoming = cmds.listConnections(plug, source=True, destination=False, plugs=True) or []
    if incoming:
        raise MmdControlRigBuildError(f"control offsetParentMatrix is already driven: {control}")
    previous = list(cmds.getAttr(plug))
    local = om.MMatrix(cmds.xform(control, query=True, objectSpace=True, matrix=True))
    inverse = list(local.inverse())
    cmds.setAttr(plug, *inverse, type="matrix")
    journal["offsetParentMatrix"].append({"control": plug, "value": previous})


def _rollback(cmds, operations) -> None:
    for action, source, target in reversed(operations):
        try:
            if action == "disconnect" and cmds.isConnected(source, target):
                cmds.disconnectAttr(source, target)
            elif action == "connect" and not cmds.isConnected(source, target):
                cmds.connectAttr(source, target, force=False)
        except Exception:
            pass


def _restore_offsets(cmds, rows) -> None:
    for row in reversed(rows):
        try:
            cmds.setAttr(row["control"], *row["value"], type="matrix")
        except Exception:
            pass


def _commit_control_input(cmds, row: Mapping[str, Any], source: Optional[str]) -> None:
    control, target = row["control"], row["target"]
    value = cmds.getAttr(control)
    if cmds.isConnected(control, target):
        cmds.disconnectAttr(control, target)
    if source:
        if cmds.isConnected(source, control):
            cmds.disconnectAttr(source, control)
        if not cmds.isConnected(source, target):
            cmds.connectAttr(source, target, force=False)
    else:
        cmds.setAttr(target, value)


def _resolve_uuid(cmds, uuid: str) -> str:
    nodes = cmds.ls(uuid, long=True) or []
    if len(nodes) != 1:
        raise MmdControlRigBuildError(f"owned control-rig node is missing: {uuid}")
    return str(nodes[0])


def _canonical_node(cmds, node: str) -> str:
    nodes = cmds.ls(node, long=True) or []
    if len(nodes) != 1:
        raise MmdControlRigBuildError(f"expected one scene node: {node}")
    return str(nodes[0])


def _write_metadata(cmds, root: str, metadata: Mapping[str, Any]) -> None:
    cmds.setAttr(
        f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}",
        json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        type="string",
    )


@contextmanager
def _undo_chunk(cmds, label: str):
    cmds.undoInfo(openChunk=True, chunkName=label)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)
