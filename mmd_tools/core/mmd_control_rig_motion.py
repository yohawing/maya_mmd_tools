"""Move imported MMD animation ownership between joints and curve controls.

EDIT preserves existing animation nodes by reconnecting their outputs to the
owned curve controls. ATTACHED restores the original dependency-graph edges.
The imported skeleton hierarchy and solver-owned output joints are untouched.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import math
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
    resolve_mmd_control_rig_binding_authored_plugs,
    resolve_mmd_control_rig_binding_ik_solvers,
    resolve_mmd_control_rig_binding_joint,
)
from mmd_tools.core.mmd_control_rig_analyzer import INPUT_IK_CONTROLLER


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
            try:
                joint = resolve_mmd_control_rig_binding_joint(cmds, binding)
                authored_plugs = _expanded_authored_plugs(binding, cmds_module=cmds)
            except MmdControlRigBuildError:
                continue
            if joint not in wanted:
                continue
            control_uuid = metadata.get("controls", {}).get(role)
            if not control_uuid:
                continue
            control = _resolve_uuid(cmds, control_uuid)
            for target in authored_plugs:
                channel = _control_channel_for_target(target)
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
    display_reference_time = _display_reference_time(metadata)
    offset_controls = set()

    with _undo_chunk(cmds, "Enter MMD Control Rig Edit"):
        try:
            for role, binding in metadata.get("bindings", {}).items():
                control = controls.get(role)
                if control is None:
                    raise MmdControlRigBuildError(f"missing owned control for {role}")
                for target in _expanded_authored_plugs(binding, cmds_module=cmds):
                    if target in claimed_targets:
                        continue
                    claimed_targets.add(target)
                    channel = _control_channel_for_target(target)
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
                        _journal_plug_row(
                            cmds,
                            source=source,
                            control=control_plug,
                            target=target,
                            value=value,
                        )
                    )

                offset_controls.add(control)

                if binding.get("inputKind") == INPUT_IK_CONTROLLER:
                    _connect_ik_enabled(cmds, control, binding, journal, operations)

            _zero_control_display_offsets(
                cmds,
                sorted(offset_controls),
                journal,
                reference_time=display_reference_time,
            )
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
    ik_rows = [_resolve_journal_plug_row(cmds, row) for row in journal.get("ikEnabled", [])]
    channel_rows = [
        _resolve_journal_plug_row(cmds, row) for row in journal.get("channels", [])
    ]
    offset_rows = [
        _resolve_journal_offset_row(cmds, row)
        for row in journal.get("offsetParentMatrix", [])
    ]
    transaction_plugs = {
        str(row[key])
        for row in (*ik_rows, *channel_rows)
        for key in ("control", "target")
    }
    transaction_plugs.update(str(row["control"]) for row in offset_rows)
    plug_states = _capture_plug_states(cmds, transaction_plugs)
    metadata_before = cmds.getAttr(f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}")

    try:
        with _undo_chunk(cmds, "Restore MMD Control Rig Attached"):
            for row in reversed(ik_rows):
                source, target = row["control"], row["target"]
                if cmds.isConnected(source, target):
                    cmds.disconnectAttr(source, target)
                prior = row.get("source")
                if prior:
                    cmds.connectAttr(prior, target, force=False)
                else:
                    cmds.setAttr(target, bool(row["value"]))
            for row in reversed(channel_rows):
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
            _restore_offsets(cmds, offset_rows, strict=True)
            metadata.pop("journal", None)
            metadata["state"] = CONTROL_RIG_ATTACHED
            _write_metadata(cmds, root, metadata)
    except Exception as exc:
        try:
            _restore_plug_states(cmds, plug_states)
            cmds.setAttr(
                f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}",
                metadata_before,
                type="string",
            )
        except Exception as rollback_exc:
            raise MmdControlRigBuildError(
                f"control-rig restore failed and rollback was incomplete: {rollback_exc}"
            ) from exc
        raise
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

    ik_rows = [_resolve_journal_plug_row(cmds, row) for row in journal.get("ikEnabled", [])]
    channel_rows = [
        _resolve_journal_plug_row(cmds, row) for row in journal.get("channels", [])
    ]
    offset_rows = [
        _resolve_journal_offset_row(cmds, row)
        for row in journal.get("offsetParentMatrix", [])
    ]
    rows = ik_rows + channel_rows
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

    transaction_plugs = {
        str(row[key])
        for row in rows
        for key in ("control", "target")
    }
    transaction_plugs.update(
        str(row["control"])
        for row in offset_rows
    )
    plug_states = _capture_plug_states(cmds, transaction_plugs)
    metadata_before = cmds.getAttr(f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}")

    try:
        with _undo_chunk(cmds, "Bake MMD Control Rig"):
            for row in reversed(ik_rows):
                _commit_control_input(cmds, row, sources_by_control[row["control"]])
            for row in reversed(channel_rows):
                _commit_control_input(cmds, row, sources_by_control[row["control"]])
            _restore_offsets(cmds, offset_rows, strict=True)
            metadata.pop("journal", None)
            metadata["state"] = CONTROL_RIG_BAKED
            _write_metadata(cmds, root, metadata)
    except Exception as exc:
        try:
            _restore_plug_states(cmds, plug_states)
            cmds.setAttr(
                f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}",
                metadata_before,
                type="string",
            )
        except Exception as rollback_exc:
            raise MmdControlRigBuildError(
                f"control-rig bake failed and rollback was incomplete: {rollback_exc}"
            ) from exc
        raise
    return metadata


def _expanded_authored_plugs(
    binding: Mapping[str, Any], *, cmds_module=None
) -> Tuple[str, ...]:
    plugs = []
    authored_plugs = (
        resolve_mmd_control_rig_binding_authored_plugs(cmds_module, binding)
        if cmds_module is not None
        else tuple(str(plug) for plug in binding.get("authoredPlugs", []))
    )
    for plug in authored_plugs:
        if plug.endswith((".translate", ".baseTranslate")):
            plugs.extend(f"{plug}{axis}" for axis in "XYZ")
        elif plug.endswith((".rotate", ".baseRotate")):
            plugs.extend(f"{plug}{axis}" for axis in "XYZ")
        else:
            plugs.append(str(plug))
    return tuple(plugs)


def _control_channel_for_target(target: str) -> str:
    """Map MMD append child names onto the equivalent control channel."""
    channel = target.rsplit(".", 1)[-1]
    if channel.startswith("baseRotate") and channel[-1:] in "XYZ":
        return f"rotate{channel[-1]}"
    if channel.startswith("baseTranslate") and channel[-1:] in "XYZ":
        return f"translate{channel[-1]}"
    if channel.startswith("inputRotateElement") and channel[-1:] in "XYZ":
        return f"rotate{channel[-1]}"
    return channel


def _require_animation_source(cmds, source: str, target: str) -> None:
    node = source.split(".", 1)[0]
    node_type = str(cmds.nodeType(node))
    if node_type in _SAFE_ANIMATION_NODES or node_type.startswith(_SAFE_ANIMATION_TYPES):
        return
    raise MmdControlRigBuildError(
        f"non-animation input blocks EDIT: {source} -> {target} ({node_type})"
    )


def _plug_reference(cmds, plug: str) -> Dict[str, str]:
    """Return a rename-stable UUID and attribute reference for one plug."""
    node, separator, attribute = str(plug).partition(".")
    if not separator or not attribute:
        raise MmdControlRigBuildError(f"invalid journal plug: {plug}")
    uuids = cmds.ls(node, uuid=True) or []
    if len(uuids) != 1:
        raise MmdControlRigBuildError(f"could not resolve journal plug node: {plug}")
    return {"nodeUuid": str(uuids[0]), "attribute": attribute}


def _journal_plug_row(
    cmds,
    *,
    source: Optional[str],
    control: str,
    target: str,
    value: Any,
) -> Dict[str, Any]:
    """Create a connection-journal row with readable and stable plug names."""
    return {
        "source": source,
        "sourceRef": _plug_reference(cmds, source) if source else None,
        "control": control,
        "controlRef": _plug_reference(cmds, control),
        "target": target,
        "targetRef": _plug_reference(cmds, target),
        "value": value,
    }


def _resolve_plug_reference(
    cmds,
    reference: Any,
    fallback: Any,
    description: str,
) -> str:
    """Resolve a journal plug by UUID, retaining legacy name-only support."""
    if isinstance(reference, Mapping):
        node_uuid = reference.get("nodeUuid")
        attribute = reference.get("attribute")
        if node_uuid and attribute:
            nodes = cmds.ls(str(node_uuid), long=True) or []
            if len(nodes) != 1:
                raise MmdControlRigBuildError(
                    f"{description} node is missing: {node_uuid}"
                )
            plug = f"{nodes[0]}.{attribute}"
            if not cmds.objExists(plug):
                raise MmdControlRigBuildError(f"{description} plug is missing: {plug}")
            return str(plug)
    if fallback and cmds.objExists(str(fallback)):
        return str(fallback)
    raise MmdControlRigBuildError(f"{description} plug is missing: {fallback}")


def _resolve_journal_plug_row(cmds, row: Mapping[str, Any]) -> Dict[str, Any]:
    """Resolve one connection journal row without mutating persisted metadata."""
    resolved = dict(row)
    resolved["control"] = _resolve_plug_reference(
        cmds,
        row.get("controlRef"),
        row.get("control"),
        "journal control",
    )
    resolved["target"] = _resolve_plug_reference(
        cmds,
        row.get("targetRef"),
        row.get("target"),
        "journal target",
    )
    source = row.get("source")
    resolved["source"] = (
        _resolve_plug_reference(
            cmds,
            row.get("sourceRef"),
            source,
            "journal source",
        )
        if source
        else None
    )
    return resolved


def _resolve_journal_offset_row(cmds, row: Mapping[str, Any]) -> Dict[str, Any]:
    """Resolve one display-offset journal row by its control UUID."""
    resolved = dict(row)
    resolved["control"] = _resolve_plug_reference(
        cmds,
        row.get("controlRef"),
        row.get("control"),
        "journal offset",
    )
    return resolved


def _connect_ik_enabled(cmds, control, binding, journal, operations) -> None:
    solvers = resolve_mmd_control_rig_binding_ik_solvers(cmds, binding)
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
            _journal_plug_row(
                cmds,
                source=source,
                control=control_plug,
                target=target,
                value=value,
            )
        )


def _display_reference_time(metadata: Mapping[str, Any]) -> Optional[float]:
    """Resolve the build-time display reference, or preserve legacy entry-time behavior."""
    if "displayReferenceTime" not in metadata:
        return None
    try:
        value = float(metadata.get("displayReferenceTime", 0.0))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _zero_control_display_offsets(
    cmds,
    controls: List[str],
    journal: Dict[str, Any],
    *,
    reference_time: Optional[float] = None,
) -> None:
    """Cancel authored local values for unique controls in one time-sampling batch."""
    plugs = []
    for control in controls:
        plug = f"{control}.offsetParentMatrix"
        if not cmds.objExists(plug):
            continue
        incoming = cmds.listConnections(plug, source=True, destination=False, plugs=True) or []
        if incoming:
            raise MmdControlRigBuildError(f"control offsetParentMatrix is already driven: {control}")
        plugs.append((control, plug))
    if not plugs:
        return
    restore_time = None
    try:
        restore_time = float(cmds.currentTime(query=True))
        if reference_time is not None:
            cmds.currentTime(reference_time, edit=True)
        for control, plug in plugs:
            previous = list(cmds.getAttr(plug))
            local = om.MMatrix(cmds.xform(control, query=True, objectSpace=True, matrix=True))
            inverse = list(local.inverse())
            cmds.setAttr(plug, *inverse, type="matrix")
            journal["offsetParentMatrix"].append(
                {
                    "control": plug,
                    "controlRef": _plug_reference(cmds, plug),
                    "value": previous,
                }
            )
    finally:
        if restore_time is not None:
            cmds.currentTime(restore_time, edit=True)


def _rollback(cmds, operations) -> None:
    for action, source, target in reversed(operations):
        try:
            if action == "disconnect" and cmds.isConnected(source, target):
                cmds.disconnectAttr(source, target)
            elif action == "connect" and not cmds.isConnected(source, target):
                cmds.connectAttr(source, target, force=False)
        except Exception:
            pass


def _restore_offsets(cmds, rows, *, strict: bool = False) -> None:
    for row in reversed(rows):
        try:
            cmds.setAttr(row["control"], *row["value"], type="matrix")
        except Exception:
            if strict:
                raise


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


def _capture_plug_states(cmds, plugs) -> Dict[str, Dict[str, Any]]:
    states = {}
    for plug in sorted(set(plugs)):
        states[plug] = {
            "incoming": list(
                cmds.listConnections(
                    plug, source=True, destination=False, plugs=True
                )
                or []
            ),
            "type": str(cmds.getAttr(plug, type=True)),
            "value": cmds.getAttr(plug),
        }
    return states


def _restore_plug_states(cmds, states: Mapping[str, Mapping[str, Any]]) -> None:
    for plug in states:
        for source in cmds.listConnections(
            plug, source=True, destination=False, plugs=True
        ) or []:
            cmds.disconnectAttr(source, plug)
    for plug, state in states.items():
        incoming = state["incoming"]
        if not incoming:
            _set_plug_value(cmds, plug, state["value"], state["type"])
        for source in incoming:
            if not cmds.isConnected(source, plug):
                cmds.connectAttr(source, plug, force=False)


def _set_plug_value(cmds, plug: str, value: Any, attr_type: str) -> None:
    if attr_type == "matrix":
        cmds.setAttr(plug, *list(value), type="matrix")
    elif attr_type == "bool":
        cmds.setAttr(plug, bool(value))
    else:
        cmds.setAttr(plug, value)


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
