"""Thin adapter around maya.cmds used by application services and actions."""


class MayaCmdsAdapter:
    """Pass-through wrapper for the small maya.cmds surface used by UI services."""

    def __init__(self, cmds_module=None):
        if cmds_module is None:
            from maya import cmds as maya_cmds

            cmds_module = maya_cmds
        self._cmds = cmds_module

    def new_scene(self, force=True):
        """Create a new Maya scene."""
        return self._cmds.file(new=True, force=force)

    def object_exists(self, node):
        """Return whether a Maya node exists."""
        return self._cmds.objExists(node)

    def ls(self, *args, **kwargs):
        """Pass through to maya.cmds.ls."""
        return self._cmds.ls(*args, **kwargs)

    def attribute_exists(self, attr, node):
        """Return whether a Maya node has an attribute."""
        return self._cmds.attributeQuery(attr, node=node, exists=True)

    def attribute_range(self, attr, node):
        """Return Maya attribute min/max bounds as optional floats."""
        minimum = None
        maximum = None
        if self._cmds.attributeQuery(attr, node=node, minExists=True):
            values = self._cmds.attributeQuery(attr, node=node, minimum=True)
            if values:
                minimum = float(values[0])
        if self._cmds.attributeQuery(attr, node=node, maxExists=True):
            values = self._cmds.attributeQuery(attr, node=node, maximum=True)
            if values:
                maximum = float(values[0])
        return minimum, maximum

    def get_attr(self, attr_path):
        """Pass through to maya.cmds.getAttr."""
        return self._cmds.getAttr(attr_path)

    def is_attr_settable(self, attr_path):
        """Return whether a Maya plug is unlocked and has no blocking input."""
        return bool(self._cmds.getAttr(attr_path, settable=True))

    def set_attr(self, *args, **kwargs):
        """Pass through to maya.cmds.setAttr."""
        return self._cmds.setAttr(*args, **kwargs)

    def add_attr(self, *args, **kwargs):
        """Pass through to maya.cmds.addAttr."""
        return self._cmds.addAttr(*args, **kwargs)

    def delete_attr(self, *args, **kwargs):
        """Pass through to maya.cmds.deleteAttr."""
        return self._cmds.deleteAttr(*args, **kwargs)

    def create_node(self, *args, **kwargs):
        """Pass through to maya.cmds.createNode."""
        return self._cmds.createNode(*args, **kwargs)

    def all_node_types(self, *args, **kwargs):
        """Pass through to maya.cmds.allNodeTypes."""
        return self._cmds.allNodeTypes(*args, **kwargs)

    def list_relatives(self, node, **kwargs):
        """Pass through to maya.cmds.listRelatives."""
        return self._cmds.listRelatives(node, **kwargs)

    def poly_evaluate(self, shape, vertex=True):
        """Pass through to maya.cmds.polyEvaluate."""
        return self._cmds.polyEvaluate(shape, vertex=vertex)

    def list_connections(self, node, **kwargs):
        """Pass through to maya.cmds.listConnections."""
        return self._cmds.listConnections(node, **kwargs)

    def node_type(self, *args, **kwargs):
        """Pass through to maya.cmds.nodeType."""
        return self._cmds.nodeType(*args, **kwargs)

    def list_attr(self, *args, **kwargs):
        """Pass through to maya.cmds.listAttr."""
        return self._cmds.listAttr(*args, **kwargs)

    def alias_attr(self, *args, **kwargs):
        """Pass through to maya.cmds.aliasAttr."""
        return self._cmds.aliasAttr(*args, **kwargs)

    def list_history(self, shapes):
        """Pass through to maya.cmds.listHistory."""
        return self._cmds.listHistory(shapes)

    def blend_shape(self, node, **kwargs):
        """Pass through to maya.cmds.blendShape."""
        return self._cmds.blendShape(node, **kwargs)

    def shading_node(self, *args, **kwargs):
        """Pass through to maya.cmds.shadingNode."""
        return self._cmds.shadingNode(*args, **kwargs)

    def connect_attr(self, *args, **kwargs):
        """Pass through to maya.cmds.connectAttr."""
        return self._cmds.connectAttr(*args, **kwargs)

    def hyper_shade(self, *args, **kwargs):
        """Pass through to maya.cmds.hyperShade."""
        return self._cmds.hyperShade(*args, **kwargs)

    def window(self, *args, **kwargs):
        """Pass through to maya.cmds.window."""
        return self._cmds.window(*args, **kwargs)

    def workspace(self, *args, **kwargs):
        """Pass through to maya.cmds.workspace."""
        return self._cmds.workspace(*args, **kwargs)

    def xform(self, *args, **kwargs):
        """Pass through to maya.cmds.xform."""
        return self._cmds.xform(*args, **kwargs)

    def select(self, nodes, replace=True):
        """Pass through to maya.cmds.select."""
        return self._cmds.select(nodes, replace=replace)

    def undo_info(self, **kwargs):
        """Pass through to maya.cmds.undoInfo."""
        return self._cmds.undoInfo(**kwargs)

    def undo(self):
        """Undo the most recent Maya operation or closed chunk."""
        return self._cmds.undo()
