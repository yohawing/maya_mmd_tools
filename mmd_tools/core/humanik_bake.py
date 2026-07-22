"""Fail-safe HumanIK TARGET preview bake across the NEUTRAL boundary."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from mmd_tools.core.humanik_preview import HumanIkTargetPreview, stop_humanik_target_preview
from mmd_tools.core.humanik_utils import maya_cmds


CHANNELS = ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
_CHANNELS_BY_PARENT = {"translate": ("translateX", "translateY", "translateZ"), "rotate": ("rotateX", "rotateY", "rotateZ")}
_OUTPUT_INDEX = re.compile(r"^outputRotate\[(\d+)\](?:[XYZ])?$")
_FOOT_IK_NODE = re.compile(r"(?:left|right)_leg_ik_mmdccdik$", re.IGNORECASE)
_RESIDUAL_TOLERANCE = 1.0e-5


@dataclass
class HumanIkBakeResult:
    """Bake routes, key count, and all-frame live-versus-baked residual."""

    start: int
    end: int
    key_count: int
    routes: Dict[str, str]
    max_error: float
    warnings: List[str]
    pre_bake_restore_state_restored: bool = False
    disabled_ik_nodes: List[str] = field(default_factory=list)
    enabled_ik_nodes: List[str] = field(default_factory=list)
    baked_ik_controllers: List[str] = field(default_factory=list)
    ik_controller_routes: Dict[str, str] = field(default_factory=dict)
    frame_errors: Dict[int, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "keyCount": self.key_count,
            "routes": dict(sorted(self.routes.items())),
            "maxError": self.max_error,
            "frameErrors": {str(frame): error for frame, error in sorted(self.frame_errors.items())},
            "warnings": list(self.warnings),
            "preBakeRestoreStateRestored": bool(self.pre_bake_restore_state_restored),
            "disabledIkNodes": list(self.disabled_ik_nodes),
            "enabledIkNodes": list(self.enabled_ik_nodes),
            "bakedIkControllers": list(self.baked_ik_controllers),
            "ikControllerRoutes": dict(sorted(self.ik_controller_routes.items())),
        }


@dataclass(frozen=True)
class _BakeRoute:
    """Resolved authoring destination and optional mmdAppend correction metadata."""

    source_plug: str
    destination: str
    kind: str
    node: Optional[str] = None
    parent: Optional[str] = None


@dataclass(frozen=True)
class _FootIkBakeTarget:
    """Importer foot-IK solver plus its controller and solved target joint."""

    node: str
    controller: str
    target: str


def bake_humanik_target_preview(
    preview: HumanIkTargetPreview,
    joints: Iterable[str],
    start: int,
    end: int,
    channels: Sequence[str] = CHANNELS,
    cmds_module=None,
    mel_module=None,
) -> HumanIkBakeResult:
    """Sample TARGET output, restore NEUTRAL, and author fail-safe bake keys.

    The operation is deliberately transactional after preview restoration:
    pre-existing curves are never overwritten, newly-created curves are removed
    on any authoring/verification failure, and solver enable attributes are
    restored before the original exception is re-raised.
    """
    if not preview.active:
        raise RuntimeError("HumanIK TARGET preview is not active")
    if end < start:
        raise ValueError("bake end must be greater than or equal to start")
    cmds = cmds_module or maya_cmds()
    joint_list = sorted(set(str(joint) for joint in joints))
    channel_list = tuple(str(channel) for channel in channels)
    if not joint_list or not channel_list:
        raise ValueError("HumanIK bake requires at least one joint and channel")
    frames = list(range(int(start), int(end) + 1))
    source_plugs = [f"{joint}.{channel}" for joint in joint_list for channel in channel_list]
    routes = {plug: _resolve_route(preview, plug, cmds) for plug in source_plugs}
    foot_ik_targets = _resolve_foot_ik_bake_targets(routes.values(), cmds)
    raw_samples: Dict[str, List[float]] = {plug: [] for plug in source_plugs}
    foot_ik_world_samples: Dict[str, List[Tuple[float, float, float]]] = {
        target.node: [] for target in foot_ik_targets
    }
    append_states: Dict[Tuple[str, str, int], Tuple[Tuple[float, float, float], Tuple[float, float, float]]] = {}
    append_state_keys = {
        (route.node, route.parent)
        for route in routes.values()
        if route.kind == "mmdAppend" and route.node and route.parent
    }
    try:
        for frame in frames:
            cmds.currentTime(frame, edit=True)
            for plug in source_plugs:
                raw_samples[plug].append(_scalar_value(cmds.getAttr(plug), plug))
            for target in foot_ik_targets:
                foot_ik_world_samples[target.node].append(
                    _world_translation(cmds, target.target)
                )
            for node, parent in append_state_keys:
                append_states[(node, parent, frame)] = _read_append_state(cmds, node, parent)
    finally:
        # Never leave HIK live after a sample failure.
        if preview.active:
            stop_humanik_target_preview(preview, cmds_module=cmds, mel_module=mel_module)

    pre_bake_restore_state_restored = _verify_restore_state_restored(preview, cmds, mel_module)
    if not pre_bake_restore_state_restored:
        raise RuntimeError("HumanIK preview restore_state was not restored before bake authoring")
    corrected_samples = _correct_samples(routes, raw_samples, append_states, frames)
    solver_nodes = sorted(
        {
            route.node
            for route in routes.values()
            if route.kind == "mmdCcdIk" and route.node
        }
    )
    foot_solver_nodes = {target.node for target in foot_ik_targets}
    legacy_solver_nodes = sorted(set(solver_nodes) - foot_solver_nodes)
    controller_routes = _foot_ik_controller_routes(foot_ik_targets)
    _preflight_routes(cmds, controller_routes.values(), controller_routes)
    controller_samples = _local_foot_ik_samples(
        cmds,
        foot_ik_targets,
        foot_ik_world_samples,
        frames,
    )
    preexisting_curves = _anim_curve_nodes(cmds)
    solver_attrs = _capture_solver_attrs(cmds, solver_nodes)
    hik_connections = _preflight_routes(cmds, routes.values(), source_plugs)
    route_values = _capture_route_values(cmds, (*routes.values(), *controller_routes.values()))

    key_count = 0
    disabled_ik_nodes: List[str] = []
    warnings: List[str] = []
    try:
        for destination, sources in hik_connections.items():
            for source in sources:
                cmds.disconnectAttr(source, destination)
        for node in legacy_solver_nodes:
            cmds.setAttr(f"{node}.enabled", False)
            disabled_ik_nodes.append(node)
        for node in sorted(foot_solver_nodes):
            cmds.setAttr(f"{node}.enabled", True)
        if legacy_solver_nodes:
            warnings.append("mmd_non_leg_ik_controls_may_be_stale")
        for source_plug in source_plugs:
            route = routes[source_plug]
            for frame, value in zip(frames, corrected_samples[source_plug]):
                cmds.setKeyframe(route.destination, time=frame, value=value)
                key_count += 1
        for source_plug, route in controller_routes.items():
            for frame, value in zip(frames, controller_samples[source_plug]):
                cmds.setKeyframe(route.destination, time=frame, value=value)
                key_count += 1
        if not _verify_disabled_solvers(cmds, legacy_solver_nodes):
            raise RuntimeError("mmdCcdIk solver did not remain disabled after bake authoring")
        if not _verify_enabled_solvers(cmds, foot_solver_nodes):
            raise RuntimeError("MMD foot IK solver did not remain enabled after bake authoring")
        frame_errors = _evaluate_all_frames(cmds, raw_samples, source_plugs, frames)
        max_error = max(frame_errors.values(), default=0.0)
        if max_error > _RESIDUAL_TOLERANCE:
            worst_frame = max(frame_errors, key=frame_errors.get)
            cmds.currentTime(worst_frame, edit=True)
            worst_index = frames.index(worst_frame)
            worst_plug = max(
                source_plugs,
                key=lambda plug: abs(
                    _scalar_value(cmds.getAttr(plug), plug) - raw_samples[plug][worst_index]
                ),
            )
            actual = _scalar_value(cmds.getAttr(worst_plug), worst_plug)
            expected = raw_samples[worst_plug][worst_index]
            raise RuntimeError(
                "HumanIK bake residual exceeds tolerance: "
                f"{max_error} at frame {worst_frame} {worst_plug} "
                f"(expected={expected}, actual={actual})"
            )
    except Exception as authoring_error:
        try:
            _rollback_authoring(
                cmds,
                preexisting_curves,
                route_values,
                solver_attrs,
                hik_connections,
            )
        except Exception as rollback_error:
            raise RuntimeError(
                f"HumanIK bake authoring failed and rollback was incomplete: {rollback_error}"
            ) from authoring_error
        raise

    return HumanIkBakeResult(
        start=int(start),
        end=int(end),
        key_count=key_count,
        routes={plug: route.destination for plug, route in routes.items()},
        max_error=max_error,
        warnings=warnings,
        pre_bake_restore_state_restored=pre_bake_restore_state_restored,
        disabled_ik_nodes=disabled_ik_nodes,
        enabled_ik_nodes=sorted(foot_solver_nodes),
        baked_ik_controllers=sorted(target.controller for target in foot_ik_targets),
        ik_controller_routes={
            source: route.destination for source, route in controller_routes.items()
        },
        frame_errors=frame_errors,
    )


def _resolve_route(preview: HumanIkTargetPreview, plug: str, cmds) -> _BakeRoute:
    node, channel = plug.rsplit(".", 1)
    parent = "translate" if channel.startswith("translate") else "rotate"
    axis = channel[-1]
    snapshot = _restore_state_snapshot(preview, node, channel, parent)
    if snapshot is None or not snapshot.sources:
        return _BakeRoute(plug, plug, "direct")
    if len(snapshot.sources) != 1:
        raise RuntimeError(f"HumanIK bake route has ambiguous writers: {plug}")
    source = str(snapshot.sources[0])
    if "." not in source:
        raise RuntimeError(f"HumanIK bake route is malformed: {source}")
    source_node, source_attr = source.rsplit(".", 1)
    source_type = _node_type(cmds, source_node)
    if source_type == "mmdAppend":
        expected_prefix = f"output{parent.capitalize()}"
        if not source_attr.startswith(expected_prefix):
            raise RuntimeError(f"mmdAppend route attribute does not match sampled channel: {source}")
        return _BakeRoute(
            plug,
            f"{source_node}.base{parent.capitalize()}{axis}",
            "mmdAppend",
            node=source_node,
            parent=parent,
        )
    if source_type == "mmdCcdIk" and parent == "rotate":
        match = _OUTPUT_INDEX.fullmatch(source_attr)
        if not match:
            raise RuntimeError(f"mmdCcdIk route index is missing: {source}")
        link_index = int(match.group(1))
        bone_slot = _ccd_bone_slot(cmds, source_node, link_index)
        compound = f"{source_node}.inputRotate[{bone_slot}]"
        candidate = f"{compound}.inputRotateElement{axis}"
        if not cmds.objExists(candidate):
            raise RuntimeError(f"mmdCcdIk destination does not exist: {candidate}")
        return _BakeRoute(plug, candidate, "mmdCcdIk", node=source_node, parent=parent)
    return _BakeRoute(plug, plug, "direct")


def _bake_route(preview: HumanIkTargetPreview, plug: str, cmds) -> str:
    """Compatibility wrapper returning only the resolved destination plug."""
    return _resolve_route(preview, plug, cmds).destination


def _correct_samples(
    routes: Dict[str, _BakeRoute],
    raw_samples: Dict[str, List[float]],
    append_states: Dict[Tuple[str, str, int], Tuple[Tuple[float, float, float], Tuple[float, float, float]]],
    frames: Sequence[int],
) -> Dict[str, List[float]]:
    corrected = {plug: list(values) for plug, values in raw_samples.items()}
    groups: Dict[Tuple[str, str, str], Dict[str, str]] = {}
    for plug, route in routes.items():
        if route.kind != "mmdAppend" or not route.node or not route.parent:
            continue
        joint, channel = plug.rsplit(".", 1)
        groups.setdefault((joint, route.node, route.parent), {})[channel] = plug
    for (joint, node, parent), channel_plugs in groups.items():
        required = _CHANNELS_BY_PARENT[parent]
        if any(channel not in channel_plugs for channel in required):
            raise RuntimeError(f"mmdAppend correction requires all {parent} channels: {joint}")
        for index, frame in enumerate(frames):
            state = append_states.get((node, parent, frame))
            if state is None:
                raise RuntimeError(f"mmdAppend evaluation data is unavailable: {node} frame {frame}")
            base, output = state
            desired_output = tuple(raw_samples[channel_plugs[channel]][index] for channel in required)
            desired_base = _append_base_sample(base, output, desired_output, parent)
            for axis, channel in enumerate(required):
                corrected[channel_plugs[channel]][index] = desired_base[axis]
    return corrected


def _read_append_state(cmds, node: str, parent: str):
    base = _vector3(cmds.getAttr(f"{node}.base{parent.capitalize()}"), f"{node}.base{parent.capitalize()}")
    output = _vector3(cmds.getAttr(f"{node}.output{parent.capitalize()}"), f"{node}.output{parent.capitalize()}")
    return base, output


def _append_base_sample(current_base, current_output, desired_output, parent: str):
    """Solve mmdAppend's base input while preserving its evaluated grant."""
    if parent == "translate":
        return tuple(float(desired_output[i]) - (float(current_output[i]) - float(current_base[i])) for i in range(3))
    if parent != "rotate":
        raise ValueError(f"Unsupported mmdAppend parent: {parent}")
    import maya.api.OpenMaya as om

    import math

    base_q = om.MEulerRotation(
        *(math.radians(float(value)) for value in current_base)
    ).asQuaternion()
    output_q = om.MEulerRotation(
        *(math.radians(float(value)) for value in current_output)
    ).asQuaternion()
    desired_q = om.MEulerRotation(
        *(math.radians(float(value)) for value in desired_output)
    ).asQuaternion()
    grant_q = base_q.inverse() * output_q
    desired_base = desired_q * grant_q.inverse()
    euler = desired_base.asEulerRotation()
    return tuple(math.degrees(float(value)) for value in (euler.x, euler.y, euler.z))


def _ccd_bone_slot(cmds, node: str, link_index: int) -> int:
    payload = _ccd_chain_payload(cmds, node)
    links = payload.get("links")
    if not isinstance(links, list) or not 0 <= link_index < len(links):
        raise RuntimeError(f"mmdCcdIk chainJson link index is out of range: {node}[{link_index}]")
    entry = links[link_index]
    bone_slot = entry.get("bone_slot") if isinstance(entry, dict) else None
    if isinstance(bone_slot, bool) or not isinstance(bone_slot, int) or bone_slot < 0:
        raise RuntimeError(f"mmdCcdIk chainJson bone_slot is invalid: {node}[{link_index}]")
    return bone_slot


def _ccd_chain_payload(cmds, node: str) -> Dict[str, Any]:
    """Read one importer ``mmdCcdIk`` chain definition or fail closed."""
    try:
        payload = json.loads(cmds.getAttr(f"{node}.chainJson") or "")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Malformed mmdCcdIk chainJson: {node}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Malformed mmdCcdIk chainJson: {node}")
    return payload


def _resolve_foot_ik_bake_targets(
    routes: Iterable[_BakeRoute],
    cmds,
) -> Tuple[_FootIkBakeTarget, ...]:
    """Resolve only reviewed importer leg/toe IK graphs for controller bake."""
    nodes = sorted(
        {
            str(route.node)
            for route in routes
            if route.kind == "mmdCcdIk" and route.node
        }
    )
    targets = []
    for node in nodes:
        leaf = node.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
        if _FOOT_IK_NODE.fullmatch(leaf) is None:
            continue
        payload = _ccd_chain_payload(cmds, node)
        controller_slot = _valid_chain_slot(payload, "controllerBoneSlot", node)
        target_slot = _valid_chain_slot(payload, "targetBoneSlot", node)
        controller = _connected_input_transform(cmds, node, "inputTranslate", controller_slot)
        target = _connected_input_transform(cmds, node, "inputTranslate", target_slot)
        goal_sources = cmds.listConnections(
            f"{node}.goalWorldMatrix",
            source=True,
            destination=False,
            plugs=True,
        ) or []
        if goal_sources:
            goal_nodes = {str(source).rsplit(".", 1)[0] for source in goal_sources}
            if len(goal_nodes) != 1:
                raise RuntimeError(f"MMD foot IK goal has ambiguous controller: {node}")
            goal_controller = next(iter(goal_nodes))
            if goal_controller != controller:
                raise RuntimeError(
                    f"MMD foot IK controller inputs disagree: {node} "
                    f"({controller} != {goal_controller})"
                )
        if controller == target:
            raise RuntimeError(f"MMD foot IK controller and target are identical: {node}")
        targets.append(_FootIkBakeTarget(node=node, controller=controller, target=target))
    return tuple(targets)


def _valid_chain_slot(payload: Dict[str, Any], key: str, node: str) -> int:
    slot = payload.get(key)
    if isinstance(slot, bool) or not isinstance(slot, int) or slot < 0:
        raise RuntimeError(f"mmdCcdIk {key} is invalid: {node}")
    return slot


def _connected_input_transform(cmds, node: str, parent: str, slot: int) -> str:
    compound = f"{node}.{parent}[{slot}]"
    plugs = [compound, *(f"{compound}.{parent}Element{axis}" for axis in "XYZ")]
    source_nodes = set()
    for plug in plugs:
        for source in cmds.listConnections(
            plug,
            source=True,
            destination=False,
            plugs=True,
        ) or []:
            source_nodes.add(str(source).rsplit(".", 1)[0])
    if len(source_nodes) != 1:
        raise RuntimeError(f"mmdCcdIk {parent}[{slot}] source is unavailable or ambiguous: {node}")
    transform = next(iter(source_nodes))
    if not cmds.objExists(transform):
        raise RuntimeError(f"mmdCcdIk connected transform does not exist: {transform}")
    return transform


def _foot_ik_controller_routes(
    targets: Iterable[_FootIkBakeTarget],
) -> Dict[str, _BakeRoute]:
    routes = {}
    for target in targets:
        for axis in "XYZ":
            plug = f"{target.controller}.translate{axis}"
            routes[plug] = _BakeRoute(plug, plug, "mmdIkController", node=target.node)
    return routes


def _world_translation(cmds, node: str) -> Tuple[float, float, float]:
    return _vector3(
        cmds.xform(node, query=True, worldSpace=True, translation=True),
        f"{node}.worldTranslate",
    )


def _local_foot_ik_samples(
    cmds,
    targets: Iterable[_FootIkBakeTarget],
    world_samples: Dict[str, List[Tuple[float, float, float]]],
    frames: Sequence[int],
) -> Dict[str, List[float]]:
    """Convert sampled target points to each MMD IK controller's local translate."""
    result = {
        f"{target.controller}.translate{axis}": []
        for target in targets
        for axis in "XYZ"
    }
    for frame_index, frame in enumerate(frames):
        cmds.currentTime(frame, edit=True)
        for target in targets:
            compound = f"{target.controller}.translate"
            original = _vector3(cmds.getAttr(compound), compound)
            try:
                cmds.xform(
                    target.controller,
                    worldSpace=True,
                    translation=world_samples[target.node][frame_index],
                )
                local = _vector3(cmds.getAttr(compound), compound)
            finally:
                cmds.setAttr(compound, *original, type="double3")
            for axis, value in zip("XYZ", local):
                result[f"{target.controller}.translate{axis}"].append(value)
    return result


def _restore_state_snapshot(preview: HumanIkTargetPreview, node: str, channel: str, parent: str):
    snapshots = getattr(getattr(preview, "restore_state", None), "plugs", ())
    for plug in (f"{node}.{channel}", f"{node}.{parent}"):
        snapshot = next((item for item in snapshots if item.plug == plug), None)
        if snapshot is not None:
            return snapshot
    return None


def _preflight_routes(cmds, routes: Iterable[_BakeRoute], source_plugs: Iterable[str]) -> Dict[str, List[str]]:
    route_list = list(routes)
    hik_connections: Dict[str, List[str]] = {}
    for route in route_list:
        destination = route.destination
        if not cmds.objExists(destination):
            raise RuntimeError(f"HumanIK bake destination does not exist: {destination}")
        try:
            locked = bool(cmds.getAttr(destination, lock=True))
            settable = bool(cmds.getAttr(destination, settable=True))
        except Exception as exc:
            raise RuntimeError(f"HumanIK bake destination cannot be validated: {destination}") from exc
        incoming = cmds.listConnections(destination, source=True, destination=False, plugs=True) or []
        if incoming:
            incoming = [str(source) for source in incoming]
            if route.kind != "direct" or any(
                _node_type(cmds, source.rsplit(".", 1)[0]) != "HIKState2SK" for source in incoming
            ):
                raise RuntimeError(f"HumanIK bake refuses to overwrite existing writer: {destination} <- {incoming}")
            hik_connections[destination] = incoming
        if locked or (not settable and destination not in hik_connections):
            raise RuntimeError(f"HumanIK bake destination is locked or not settable: {destination}")
    route_by_source = {route.source_plug: route for route in route_list}
    for source_plug in source_plugs:
        incoming = [
            str(source)
            for source in (cmds.listConnections(source_plug, source=True, destination=False, plugs=True) or [])
        ]
        if not incoming:
            continue
        route = route_by_source[source_plug]
        non_hik = [
            source
            for source in incoming
            if _node_type(cmds, source.rsplit(".", 1)[0]) not in {"HIKState2SK"}
        ]
        if non_hik and route.kind == "direct":
            raise RuntimeError(f"HumanIK bake refuses non-HIK joint writer: {source_plug} <- {non_hik}")
        if non_hik and any(
            _node_type(cmds, source.rsplit(".", 1)[0]) not in {"mmdCcdIk", "mmdAppend"}
            for source in non_hik
        ):
            raise RuntimeError(f"HumanIK bake refuses unexpected joint writer: {source_plug} <- {non_hik}")
        hik = [source for source in incoming if source not in non_hik]
        if hik:
            hik_connections[source_plug] = hik
    return hik_connections


def _capture_solver_attrs(cmds, nodes: Iterable[str]) -> Dict[str, Any]:
    values = {}
    for node in nodes:
        if not cmds.attributeQuery("enabled", node=node, exists=True):
            raise RuntimeError(f"mmdCcdIk enabled attribute is unavailable: {node}")
        try:
            if bool(cmds.getAttr(f"{node}.enabled", lock=True)) or not bool(cmds.getAttr(f"{node}.enabled", settable=True)):
                raise RuntimeError(f"mmdCcdIk enabled attribute is not settable: {node}")
            values[node] = cmds.getAttr(f"{node}.enabled")
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"mmdCcdIk enabled state is unavailable: {node}") from exc
    return values


def _verify_disabled_solvers(cmds, nodes: Iterable[str]) -> bool:
    for node in nodes:
        try:
            if bool(cmds.getAttr(f"{node}.enabled")):
                return False
        except Exception:
            return False
    return True


def _verify_enabled_solvers(cmds, nodes: Iterable[str]) -> bool:
    for node in nodes:
        try:
            if not bool(cmds.getAttr(f"{node}.enabled")):
                return False
        except Exception:
            return False
    return True


def _rollback_authoring(
    cmds,
    preexisting_curves: Set[str],
    route_values: Dict[str, float],
    solver_attrs: Dict[str, Any],
    hik_connections: Dict[str, List[str]],
) -> None:
    failures = []
    try:
        current_curves = _anim_curve_nodes(cmds)
        new_curves = sorted(current_curves - set(preexisting_curves))
        if new_curves:
            cmds.delete(new_curves)
    except Exception as exc:
        failures.append(f"curve cleanup: {exc}")
    failures.extend(_restore_route_values(cmds, route_values))
    for destination, sources in hik_connections.items():
        for source in sources:
            try:
                if not cmds.isConnected(source, destination):
                    cmds.connectAttr(source, destination, force=True)
            except Exception as exc:
                failures.append(f"connection {source} -> {destination}: {exc}")
    for node, value in solver_attrs.items():
        try:
            cmds.setAttr(f"{node}.enabled", value)
        except Exception as exc:
            failures.append(f"solver {node}.enabled: {exc}")
    if failures:
        raise RuntimeError("; ".join(failures))


def _capture_route_values(cmds, routes: Iterable[_BakeRoute]) -> Dict[str, float]:
    """Capture every scalar authoring destination before creating curves."""
    values = {}
    for destination in sorted({route.destination for route in routes}):
        values[destination] = _scalar_value(cmds.getAttr(destination), destination)
    return values


def _restore_route_values(cmds, route_values: Dict[str, float]) -> List[str]:
    """Restore writable destinations after deleting newly-created curves."""
    failures = []
    for destination, value in route_values.items():
        try:
            if bool(cmds.getAttr(destination, lock=True)):
                failures.append(f"route {destination}: locked")
                continue
            if not bool(cmds.getAttr(destination, settable=True)):
                failures.append(f"route {destination}: not settable")
                continue
            cmds.setAttr(destination, value)
        except Exception as exc:
            failures.append(f"route {destination}: {exc}")
    return failures


def _anim_curve_nodes(cmds) -> Set[str]:
    values = getattr(cmds, "ls", None)
    if values is None:
        return set()
    return {str(node) for node in (values(type="animCurve") or [])}


def _evaluate_all_frames(cmds, raw_samples, source_plugs, frames) -> Dict[int, float]:
    errors = {}
    for index, frame in enumerate(frames):
        cmds.currentTime(frame, edit=True)
        frame_error = 0.0
        for plug in source_plugs:
            actual = _scalar_value(cmds.getAttr(plug), plug)
            frame_error = max(frame_error, abs(actual - raw_samples[plug][index]))
        errors[int(frame)] = frame_error
    return errors


def _verify_restore_state_restored(preview: HumanIkTargetPreview, cmds, mel_module=None) -> bool:
    """Verify exact incoming plugs, node state, and HIK state before authoring."""
    restore_state = getattr(preview, "restore_state", None)
    if restore_state is None:
        return False
    list_connections = getattr(cmds, "listConnections", None)
    if list_connections is not None:
        for snapshot in getattr(restore_state, "plugs", ()):
            actual = sorted(str(source) for source in (list_connections(snapshot.plug, source=True, destination=False, plugs=True) or []))
            if actual != sorted(str(source) for source in snapshot.sources):
                return False
    get_attr = getattr(cmds, "getAttr", None)
    for snapshot in getattr(restore_state, "nodes", ()):
        for attribute, expected in getattr(snapshot, "attributes", {}).items():
            if get_attr is None:
                return False
            try:
                actual = get_attr(f"{snapshot.node}.{attribute}")
            except Exception:
                return False
            if actual != expected:
                return False
    if mel_module is not None:
        try:
            source = str(mel_module.eval(f'hikGetRetargetCharacterInput("{restore_state.character}")') or "")
            if source != str(getattr(restore_state, "input_source", "")):
                return False
            locked = bool(mel_module.eval(f'hikIsDefinitionLocked("{restore_state.character}")'))
            if locked != bool(getattr(restore_state, "lock_state", locked)):
                return False
        except Exception:
            return False
    return True


def _node_type(cmds, node: str) -> str:
    try:
        return str(cmds.nodeType(node))
    except Exception:
        return ""


def _vector3(value: Any, plug: str) -> Tuple[float, float, float]:
    while isinstance(value, (list, tuple)) and len(value) == 1:
        value = value[0]
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"Expected a 3-vector for HumanIK bake plug: {plug}")
    try:
        return tuple(float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Expected numeric 3-vector for HumanIK bake plug: {plug}") from exc


def _scalar_value(value: Any, plug: str) -> float:
    while isinstance(value, (tuple, list)) and len(value) == 1:
        value = value[0]
    if isinstance(value, (tuple, list)):
        raise ValueError(f"Expected scalar value for HumanIK bake plug: {plug}")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Expected numeric value for HumanIK bake plug: {plug}") from exc
