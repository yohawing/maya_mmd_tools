"""Recreate authored attributes as Maya proxies using undoable native commands."""

import json
import re

from maya import cmds, mel
from maya.api import OpenMaya as om


def _attribute(plug):
    selection = om.MSelectionList()
    selection.add(plug)
    return om.MFnAttribute(selection.getPlug(0).attribute())


def is_proxy(plug):
    """Return the proxy flag without evaluating the connected value."""
    return _attribute(plug).isProxyAttribute


def supports_proxy(plug):
    """Keep customized RGB attribute definitions on their existing route."""
    selection = om.MSelectionList()
    selection.add(plug)
    root = selection.getPlug(0)
    if not root.isCompound:
        return True  # Scalar definitions are replayed verbatim.
    if cmds.getAttr(plug, type=True) != "double3" or root.numChildren() != 3:
        return False
    # Maya infers compound proxy definitions from the source. Only migrate the
    # ordinary authored RGB schema; retain custom defaults/limits/UI metadata.
    for member in [root] + [root.child(index) for index in range(3)]:
        fn = om.MFnAttribute(member.attribute())
        command = fn.getAddAttrCmd(False)
        if (fn.hidden or fn.usedAsColor or not (fn.readable and fn.writable and fn.storable)
                or fn.shortName != fn.name
                or re.search(r"\s-(?:nn|dv|min|max|smn|smx)\s", command)):
            return False
    return True


def set_proxy(plug, enabled, source=None):
    """Preserve a numeric attribute family while changing its proxy storage.

    Changing MFnAttribute.isProxyAttribute on allocated data is unsafe at scene
    teardown. Replaying Maya's own attribute definitions allocates the correct
    storage and lets ordinary migration Undo restore the original attributes.
    """
    if is_proxy(plug) == enabled:
        return
    selection = om.MSelectionList()
    selection.add(plug)
    root = selection.getPlug(0)
    while root.isChild:
        root = root.parent()
        if source:
            selected_source = om.MSelectionList()
            selected_source.add(source)
            source = selected_source.getPlug(0).parent().name()
    if not om.MFnAttribute(root.attribute()).dynamic:
        raise RuntimeError("Only dynamic attributes can become material proxies")
    node = om.MFnDependencyNode(root.node()).name()
    family = [root]
    for member in family:
        if member.isCompound:
            family.extend(member.child(index) for index in range(member.numChildren()))
    definitions = []
    connections = []
    for member in family:
        name = member.name()
        fn = om.MFnAttribute(member.attribute())
        definition = {"name": name, "command": fn.getAddAttrCmd(False),
                      "keyable": member.isKeyable, "channelBox": member.isChannelBox,
                      "locked": member.isLocked}
        try:
            definition.update(value=cmds.getAttr(name), type=cmds.getAttr(name, type=True))
        except RuntimeError:
            pass  # A rollback may already have disconnected the proxy source.
        definitions.append(definition)
        if cmds.connectionInfo(name, isExactDestination=True):
            connections.append((cmds.connectionInfo(name, sourceFromDestination=True), name))
        # Child queries can report an inherited parent connection. Preserve
        # the actual source, otherwise a scalar is reconnected to an RGB plug.
        connections.extend((name, target.name()) for target in member.connectedTo(False, True)
                           if target.source() == member)
    names = {om.MFnAttribute(member.attribute()).name for member in family}
    aliases = cmds.aliasAttr(node, query=True) or []
    aliases = [(alias, attr) for alias, attr in zip(aliases[::2], aliases[1::2]) if attr in names]
    compound = root.isCompound
    root_name = om.MFnAttribute(root.attribute()).name
    if enabled and compound and not source:
        raise RuntimeError("A compound proxy requires its base source")
    cmds.deleteAttr(root.name())
    if enabled and compound:
        # addAttr -proxy creates the internal parent/child proxy storage.
        cmds.addAttr(node, longName=root_name, proxy=source)
        created_children = cmds.attributeQuery(root_name, node=node, listChildren=True) or []
        for old, definition in zip(created_children, definitions[1:]):
            cmds.renameAttr(f"{node}.{old}", definition["name"].split(".", 1)[1])
    for definition in definitions:
        # The command comes from Maya, including exact names, defaults and limits.
        command = re.sub(r"\s-(?:usedAsProxy|uap)\b", "", definition["command"])
        if not (enabled and compound):
            mel.eval(command.rstrip().rstrip(";") + (" -uap" if enabled else "")
                     + " " + json.dumps(node, ensure_ascii=False) + ";")
    for definition in definitions:
        name = definition["name"]
        if not enabled and "value" in definition:
            value = definition["value"]
            if definition["type"] in {"double3", "float3"}:
                cmds.setAttr(name, *value[0], type=definition["type"])
            else:
                cmds.setAttr(name, value)
        cmds.setAttr(name, keyable=definition["keyable"], channelBox=definition["channelBox"])
    for source, destination in dict.fromkeys(connections):
        cmds.connectAttr(source, destination, force=True)
    for alias, attr in aliases:
        cmds.aliasAttr(alias, f"{node}.{attr}")
    for definition in definitions:
        if definition["locked"]:
            cmds.setAttr(definition["name"], lock=True)
