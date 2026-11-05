"""Shared ownership-aware inputs for Reset Pose and Animator Copy/Paste."""

from __future__ import annotations

import json
from dataclasses import dataclass

TRANSFORM_CHANNELS = tuple(kind + axis for kind in ("translate", "rotate") for axis in "XYZ")


@dataclass(frozen=True)
class PoseAuthoringRoute:
    """Exact live inputs and their node identities, independent of layer choice."""

    target_uuid: str
    model_uuid: str
    owner: str
    plugs: tuple[str, ...]
    input_uuids: tuple[str, ...]


@dataclass(frozen=True)
class PoseAuthoringSnapshot:
    """Layer-composed authoring values before append, morph and IK evaluation."""

    route: PoseAuthoringRoute
    values: tuple[float, ...]


def _uuid(cmds, node):
    values = cmds.ls(node, uuid=True) or []
    if len(values) != 1:
        raise RuntimeError(f"Pose node identity is unavailable: {node}")
    return str(values[0])


def _scalar_plugs(cmds, plug):
    node, attribute = plug.split(".", 1)
    children = cmds.attributeQuery(attribute.rsplit(".", 1)[-1].split("[")[0], node=node, listChildren=True) or []
    if children:
        return tuple(f"{plug}.{child}" if "[" in attribute else f"{node}.{child}" for child in children)
    return (plug,)


def resolve_pose_routes(cmds, targets, model_root=None):
    """Resolve selected joints/controllers without bypassing their current owner."""
    owner = "DIRECT"
    model_uuid = ""
    mapped = None
    control_channels = None
    if model_root:
        from .mmd_control_rig_builder import read_mmd_control_rig_metadata

        model_uuid = _uuid(cmds, model_root)
        metadata = read_mmd_control_rig_metadata(model_root, cmds_module=cmds) or {}
        owner = str(metadata.get("owner") or "MMD_OWNED")
        mapped = resolve_model_pose_plugs(model_root, targets, cmds)
        if owner == "CONTROL_OWNED":
            from .mmd_control_rig_channels import (
                derive_mmd_control_rig_channel_policy,
                union_mmd_control_rig_channel_policies,
            )

            policies = {}
            for role, uuid in (metadata.get("controls") or {}).items():
                policy = derive_mmd_control_rig_channel_policy(role, (metadata.get("bindings") or {}).get(role))
                policies.setdefault(uuid, []).append(policy)
            control_channels = {
                uuid: union_mmd_control_rig_channel_policies(tuple(rows)).keyable_channels
                for uuid, rows in policies.items()
            }
    routes = {}
    for target in targets:
        paths = cmds.ls(target, long=True) or []
        if len(paths) != 1:
            raise RuntimeError(f"Pose target is ambiguous or missing: {target}")
        resolved = str(paths[0])
        if cmds.nodeType(resolved) not in {"joint", "transform"}:
            raise RuntimeError(f"Pose target is not a joint/controller: {target}")
        target_uuid = _uuid(cmds, resolved)
        channels = TRANSFORM_CHANNELS
        if control_channels is not None:
            channels = control_channels.get(target_uuid, ())
            if not channels:
                raise RuntimeError(f"Pose target is not an editable owned controller: {target}")
        authored = mapped.get(resolved) if mapped is not None else tuple(
            f"{resolved}.{channel}" for channel in channels
        )
        if not authored:
            raise RuntimeError(f"Pose authoring inputs are unavailable: {target}")
        plugs = tuple(dict.fromkeys(child for plug in authored for child in _scalar_plugs(cmds, plug)))
        routes[target] = PoseAuthoringRoute(
            target_uuid, model_uuid, owner, plugs,
            tuple(_uuid(cmds, plug.split(".", 1)[0]) for plug in plugs),
        )
    return routes


def selected_pose_layer(cmds):
    """Choose the user's selected layer, refusing an ambiguous selection."""
    layers = cmds.ls(type="animLayer") or []
    if not layers:
        return None
    selected = [layer for layer in layers if cmds.animLayer(layer, query=True, selected=True)]
    preferred = [layer for layer in (selected or layers) if cmds.animLayer(layer, query=True, preferred=True)]
    candidates = preferred or selected
    if len(candidates) > 1:
        raise RuntimeError("Paste Pose requires one selected animation layer")
    return candidates[0] if candidates else cmds.animLayer(query=True, root=True)


def validate_pose_input(cmds, plug):
    """Reject locked or non-animation drivers before creating any keys."""
    node = plug.split(".", 1)[0]
    if cmds.getAttr(plug, lock=True) or any(cmds.lockNode(node, query=True, lock=True) or []):
        raise RuntimeError(f"Paste Pose input is locked: {plug}")
    sources = cmds.listConnections(plug, source=True, destination=False, plugs=True) or []
    if len(sources) > 1:
        raise RuntimeError(f"Paste Pose input has ambiguous writers: {plug}")
    for source in sources:
        node = source.split(".", 1)[0]
        # Maya's existing history query includes upstream layer inputs and
        # conversion nodes: a wrapper cannot hide a constraint/solver writer.
        history = {node, *(cmds.listHistory(node, pruneDagObjects=True) or [])}
        for upstream in history:
            node_type = cmds.nodeType(upstream)
            if node_type in {"time", "animLayer", "unitConversion", "animCurveTA", "animCurveTL", "animCurveTT", "animCurveTU"}:
                continue
            if node_type.startswith("animBlendNode"):
                continue
            raise RuntimeError(f"Paste Pose input has an unsupported driver: {plug} ({node_type})")


def resolve_model_pose_plugs(
    root: str,
    targets: list[str],
    cmds,
) -> dict[str, tuple[str, ...]] | None:
    """Resolve MMD-owned semantic joints to their safe authoring inputs."""

    from .mmd_control_rig_analyzer import (
        INPUT_SOLVER_OUTPUT,
        analyze_mmd_control_rig,
    )
    from .mmd_control_rig_builder import read_mmd_control_rig_metadata

    metadata = read_mmd_control_rig_metadata(root, cmds_module=cmds) or {}
    owner = str(metadata.get("owner") or "MMD_OWNED")
    if owner == "CONTROL_OWNED":
        return None
    if owner != "MMD_OWNED":
        raise RuntimeError(f"unsupported Reset Pose owner: {owner}")
    spec = analyze_mmd_control_rig(root, cmds_module=cmds)
    bindings = {str(binding.joint): binding for binding in spec.bones}
    role_bindings = {}
    for role in spec.roles:
        binding = role.binding
        if binding is None or binding.blocked or not binding.authored_plugs:
            continue
        role_bindings.setdefault(str(binding.joint), []).append(binding)
    result = {}
    for target in targets:
        paths = cmds.ls(target, long=True) or []
        if len(paths) != 1:
            raise RuntimeError(f"ambiguous Reset Pose target: {target}")
        resolved = str(paths[0])
        binding = bindings.get(resolved)
        candidates = []
        if binding is not None and not binding.blocked and binding.authored_plugs:
            candidates.append(tuple(str(plug) for plug in binding.authored_plugs))
        candidates.extend(
            tuple(str(plug) for plug in role_binding.authored_plugs)
            for role_binding in role_bindings.get(resolved, ())
        )
        if (
            not candidates
            and binding is not None
            and binding.input_kind == INPUT_SOLVER_OUTPUT
        ):
            candidates.append(
                resolve_solver_pose_plugs(
                    binding,
                    resolved,
                    cmds,
                )
            )
        routes = tuple(dict.fromkeys(candidates))
        if len(routes) != 1:
            raise RuntimeError(
                f"Reset Pose authoring route is unavailable: {resolved}"
            )
        result[resolved] = routes[0]
    return result

def resolve_solver_pose_plugs(
    binding,
    target: str,
    cmds,
) -> tuple[str, ...]:
    """Map one CCD output index through chain metadata to its bone-slot input."""

    rows = [
        row
        for row in binding.incoming
        if str(row.source_node_type) == "mmdCcdIk"
    ]
    rotation_rows = [row for row in binding.incoming if str(row.destination_plug).startswith(f"{target}.rotate")]
    layered_or_children = rotation_rows and (
        all(str(row.destination_plug) in {f"{target}.rotate{axis}" for axis in "XYZ"} for row in rotation_rows)
        or any(str(row.source_node_type).startswith("animBlendNode") for row in rotation_rows)
    )
    if layered_or_children:
        from .native_layer_routes import NativeLayerRouteError, resolve_joint_input_source

        try:
            source = resolve_joint_input_source(target, "rotate", cmds_module=cmds)
        except NativeLayerRouteError as exc:
            raise RuntimeError(f"Reset Pose solver output route is ambiguous: {target}") from exc
        if not source or cmds.nodeType(source.split(".", 1)[0]) != "mmdCcdIk":
            raise RuntimeError(f"Reset Pose solver output route is ambiguous: {target}")
    elif len(rows) == 1:
        row = rows[0]
        if str(row.destination_plug) != f"{target}.rotate":
            raise RuntimeError(f"Reset Pose solver output destination is unsupported: {target}")
        source = str(row.source_plug)
    else:
        raise RuntimeError(f"Reset Pose solver output route is ambiguous: {target}")
    node, separator, attribute = source.rpartition(".")
    prefix = "outputRotate["
    if (
        not separator
        or not node
        or not attribute.startswith(prefix)
        or not attribute.endswith("]")
    ):
        raise RuntimeError(
            f"Reset Pose solver output index is unavailable: {target}"
        )
    index = attribute[len(prefix) : -1]
    if not index.isdigit():
        raise RuntimeError(
            f"Reset Pose solver output index is unavailable: {target}"
        )
    try:
        chain = json.loads(cmds.getAttr(f"{node}.chainJson"))
        links = chain["links"]
        if not isinstance(links, list):
            raise TypeError("links must be a list")
        slots = []
        for link in links:
            if not isinstance(link, dict):
                raise TypeError("link must be an object")
            slot = link["bone_slot"]
            if isinstance(slot, bool) or not isinstance(slot, int) or slot < 0:
                raise ValueError("bone_slot must be a non-negative integer")
            slots.append(slot)
        if len(set(slots)) != len(slots):
            raise ValueError("bone_slot values must be unique")
        slot = slots[int(index)]
    except (
        KeyError,
        IndexError,
        RuntimeError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise RuntimeError(
            f"Reset Pose solver chain metadata is unavailable: {target}"
        ) from exc
    compound = f"{node}.inputRotate[{slot}]"
    children = tuple(
        f"{compound}.inputRotateElement{axis}" for axis in "XYZ"
    )
    exists = getattr(cmds, "objExists", None)
    if not callable(exists) or not exists(compound) or not all(
        exists(child) for child in children
    ):
        raise RuntimeError(
            f"Reset Pose solver input is unavailable: {target}"
        )
    return children
