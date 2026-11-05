"""Recognize native XYZ joint outputs through Maya animation-layer bases.

Only native output families and standard layer inputA/output edges participate.
These queries describe provenance; they never replace actual connection records.
"""

from __future__ import annotations

import re


class NativeLayerRouteError(ValueError):
    """A joint family does not have one complete, supported native source."""


_NATIVE_TYPES = frozenset({"mmdCcdIk", "mmdAppend", "mmdBoneMorphAccum"})
_SCALAR_BLENDS = frozenset({"animBlendNodeAdditiveDA", "animBlendNodeAdditiveDL", "animBlendNodeAdditiveF"})
_ROTATION_BLEND = "animBlendNodeAdditiveRotation"
_TIME_CURVES = frozenset({"animCurveTA", "animCurveTL", "animCurveTT", "animCurveTU"})


def _cmds(module):
    if module is not None:
        return module
    from maya import cmds
    return cmds


def _canonical(cmds, plug):
    node, attr = plug.split(".", 1)
    matches = cmds.ls(node, long=True) or []
    if len(matches) != 1:
        raise NativeLayerRouteError(f"Ambiguous native route node: {node}")
    return f"{matches[0]}.{attr}"


def _incoming(cmds, destination):
    return cmds.connectionInfo(destination, sourceFromDestination=True) or None


def _native_parent(cmds, source, family, axis):
    node, attr = source.split(".", 1)
    node_type = cmds.nodeType(node)
    stem = "output" + family.title()
    if node_type == "mmdCcdIk" and family == "rotate":
        match = re.fullmatch(r"(outputRotate\[\d+\])(?:\.outputRotateElement([XYZ]))?", attr)
        if match and (match[2] is None or match[2] == axis):
            return _canonical(cmds, f"{node}.{match[1]}")
    elif node_type in {"mmdAppend", "mmdBoneMorphAccum"} and attr in {stem, stem + axis}:
        return _canonical(cmds, f"{node}.{stem}")
    raise NativeLayerRouteError(f"Native output component does not match {family}{axis}: {source}")


def _require_animation_source(cmds, source):
    node = source.split(".", 1)[0]
    for upstream in {node, *(cmds.listHistory(node, pruneDagObjects=True) or [])}:
        if cmds.nodeType(upstream) not in _TIME_CURVES | {"time", "unitConversion"}:
            raise NativeLayerRouteError(f"Unsupported animation-layer value writer: {source}")


def _trace_axis(cmds, destination, family, axis):
    visited = set()
    while True:
        source = _incoming(cmds, destination)
        if not source:
            return None
        if source in visited:
            raise NativeLayerRouteError(f"Cyclic native layer route: {destination}")
        visited.add(source)
        node, attr = source.split(".", 1)
        node_type = cmds.nodeType(node)
        if node_type in _NATIVE_TYPES:
            return _native_parent(cmds, source, family, axis)
        if node_type in _TIME_CURVES | {"unitConversion"}:
            _require_animation_source(cmds, source)
            return None
        if node_type == _ROTATION_BLEND and family == "rotate" and attr in {"output", "output" + axis}:
            suffix = axis
        elif node_type in _SCALAR_BLENDS and attr == "output":
            suffix = ""
        else:
            raise NativeLayerRouteError(f"Unsupported joint-family writer: {source}")
        layer_value = _incoming(cmds, f"{node}.inputB{suffix}")
        if layer_value:
            _require_animation_source(cmds, layer_value)
        destination = f"{node}.inputA{suffix}"


def resolve_joint_input_source(joint, family, *, cmds_module=None):
    """Return one native parent output for all XYZ, or None for ordinary animation.

    Mixed native/ordinary axes, different native owners, and unsupported writers
    raise instead of weakening ownership to an apparently direct joint input.
    """
    if family not in {"translate", "rotate"}:
        raise ValueError(f"Unsupported joint family: {family}")
    cmds = _cmds(cmds_module)
    sources = tuple(_trace_axis(cmds, f"{joint}.{family}{axis}", family, axis) for axis in "XYZ")
    if not any(sources):
        return None
    if len(set(sources)) != 1:
        raise NativeLayerRouteError(f"Mixed native components on {joint}.{family}: {sources}")
    return sources[0]


def source_joint_destinations(source_plug, family, *, cmds_module=None):
    """Return joint families reached by a native parent/XYZ output and layer bases."""
    if family not in {"translate", "rotate"}:
        raise ValueError(f"Unsupported joint family: {family}")
    cmds = _cmds(cmds_module)
    source_plug = _canonical(cmds, source_plug)
    node, attr = source_plug.split(".", 1)
    if cmds.nodeType(node) == "mmdCcdIk":
        children = [f"{source_plug}.outputRotateElement{axis}" for axis in "XYZ"]
    else:
        children = [source_plug + axis for axis in "XYZ"]
    pending = [source_plug, *children]
    visited = set()
    joints = set()
    while pending:
        output = pending.pop()
        if output in visited:
            continue
        visited.add(output)
        for destination in cmds.listConnections(output, source=False, destination=True, plugs=True) or []:
            target, attribute = destination.split(".", 1)
            node_type = cmds.nodeType(target)
            if node_type == "joint" and attribute in {family, *(family + axis for axis in "XYZ")}:
                if resolve_joint_input_source(target, family, cmds_module=cmds) == source_plug:
                    matches = cmds.ls(target, long=True) or []
                    if len(matches) != 1:
                        raise NativeLayerRouteError(f"Ambiguous native destination: {target}")
                    joints.add(str(matches[0]))
            elif node_type == _ROTATION_BLEND and attribute in {"inputA", "inputAX", "inputAY", "inputAZ"}:
                pending.extend(f"{target}.output{axis}" for axis in "XYZ")
            elif node_type in _SCALAR_BLENDS and attribute == "inputA":
                pending.append(f"{target}.output")
    return sorted(joints)
