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

    def reference_query(self, node, **kwargs):
        """Return Maya reference state for a node (read-only probe)."""
        return self._cmds.referenceQuery(node, **kwargs)

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

    def get_attr(self, attr_path, **kwargs):
        """Pass through to maya.cmds.getAttr."""
        return self._cmds.getAttr(attr_path, **kwargs)

    def is_attr_settable(self, attr_path):
        """Return whether a Maya plug is unlocked and has no blocking input."""
        return bool(self._cmds.getAttr(attr_path, settable=True))

    def set_attr(self, *args, **kwargs):
        """Pass through to maya.cmds.setAttr."""
        return self._cmds.setAttr(*args, **kwargs)

    def current_time(self):
        """Return the current Maya time in the active UI time unit."""
        return self._cmds.currentTime(query=True)

    def _keying_layer(self, attr_path):
        best_layer = self._cmds.animLayer(attr_path, query=True, bestLayer=True)
        if best_layer == "":
            best_layer = self._cmds.animLayer(query=True, root=True)
        return best_layer if isinstance(best_layer, str) and best_layer else None

    def set_keyframe(self, *args, **kwargs):
        """Key only the best animation layer for the selected plug."""
        if args and "animLayer" not in kwargs:
            best_layer = self._keying_layer(args[0])
            if best_layer is None:
                raise RuntimeError(f"Could not resolve one keying layer for {args[0]}")
            kwargs["animLayer"] = best_layer
        return self._cmds.setKeyframe(*args, **kwargs)

    def keyframe(self, *args, **kwargs):
        """Pass through to maya.cmds.keyframe."""
        return self._cmds.keyframe(*args, **kwargs)

    def remove_keyframe(self, attr_path, time):
        """Remove keys at one time without cutting the connected DG object.

        ``cutKey`` can remove a custom DG node when its final key is cut from a
        multi element.  MFnAnimCurve removes only the identified key and keeps
        the controller element, alias, and connection intact.
        """
        from maya.api import OpenMaya as om
        from maya.api import OpenMayaAnim as oma

        best_layer = self._keying_layer(attr_path)
        if best_layer is None:
            return 0

        plug_curves = set(self._cmds.keyframe(attr_path, query=True, name=True) or [])
        resolved_curve = self._cmds.animLayer(
            best_layer,
            query=True,
            findCurveForPlug=attr_path,
        )
        if isinstance(resolved_curve, (list, tuple)):
            if len(resolved_curve) > 1:
                return 0
            resolved_curve = resolved_curve[0] if resolved_curve else None
        base_layer = self._cmds.animLayer(query=True, root=True)
        if not resolved_curve and best_layer == base_layer and len(plug_curves) == 1:
            resolved_curve = next(iter(plug_curves))
        if not isinstance(resolved_curve, str) or resolved_curve not in plug_curves:
            return 0

        curve_name = resolved_curve
        indices = self._cmds.keyframe(
            curve_name,
            query=True,
            time=(time, time),
            indexValue=True,
        ) or []
        if not indices:
            return 0

        selection = om.MSelectionList()
        selection.add(curve_name)
        curve = oma.MFnAnimCurve(selection.getDependNode(0))
        removed = 0
        for index in sorted({int(value) for value in indices}, reverse=True):
            curve.remove(index)
            removed += 1
        return removed

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

    def disconnect_attr(self, *args, **kwargs):
        """Pass through to maya.cmds.disconnectAttr."""
        return self._cmds.disconnectAttr(*args, **kwargs)

    def sets(self, *args, **kwargs):
        """Pass through to maya.cmds.sets."""
        return self._cmds.sets(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Pass through to maya.cmds.delete."""
        return self._cmds.delete(*args, **kwargs)

    def remove_multi_instance(self, *args, **kwargs):
        """Pass through to maya.cmds.removeMultiInstance."""
        return self._cmds.removeMultiInstance(*args, **kwargs)

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

    def select_fast(self, nodes, replace=True):
        """Update Maya's active selection directly through API 2.0.

        Animator pickers call this latency-sensitive path instead of entering
        the command engine for every click. Unsupported selection modes retain
        the regular ``cmds.select`` behavior.
        """

        from maya.api import OpenMaya as om

        node_list = [nodes] if isinstance(nodes, str) else list(nodes or [])
        selection = om.MSelectionList()
        for node in node_list:
            selection.add(node)
        mode = om.MGlobal.kReplaceList if replace else om.MGlobal.kAddToList
        om.MGlobal.setActiveSelectionList(selection, mode)
        return node_list

    def undo_info(self, **kwargs):
        """Pass through to maya.cmds.undoInfo."""
        return self._cmds.undoInfo(**kwargs)

    def undo(self):
        """Undo the most recent Maya operation or closed chunk."""
        return self._cmds.undo()

    def redo(self):
        """Redo the most recently undone Maya operation or closed chunk."""
        return self._cmds.redo()

    def mmd_render_queue_reindex(self, node, first_index, second_index):
        """Swap two material indices in one native ``mmdRenderShape`` queue."""
        return self._cmds.mmdRenderQueueReindex(
            node=node,
            firstMaterialIndex=int(first_index),
            secondMaterialIndex=int(second_index),
        )
