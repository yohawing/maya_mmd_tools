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

    def get_attr(self, attr_path):
        """Pass through to maya.cmds.getAttr."""
        return self._cmds.getAttr(attr_path)

    def list_relatives(self, node, **kwargs):
        """Pass through to maya.cmds.listRelatives."""
        return self._cmds.listRelatives(node, **kwargs)

    def poly_evaluate(self, shape, vertex=True):
        """Pass through to maya.cmds.polyEvaluate."""
        return self._cmds.polyEvaluate(shape, vertex=vertex)

    def list_connections(self, node, **kwargs):
        """Pass through to maya.cmds.listConnections."""
        return self._cmds.listConnections(node, **kwargs)

    def list_history(self, shapes):
        """Pass through to maya.cmds.listHistory."""
        return self._cmds.listHistory(shapes)

    def blend_shape(self, node, **kwargs):
        """Pass through to maya.cmds.blendShape."""
        return self._cmds.blendShape(node, **kwargs)

    def select(self, nodes, replace=True):
        """Pass through to maya.cmds.select."""
        return self._cmds.select(nodes, replace=replace)
