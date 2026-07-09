"""Minimal Maya plug-in for the MMD rigid-body display locator.

The full MMD Tools plug-in also registers this node, but physics import may run
before that UI-oriented plug-in is loaded. Keeping this entry point locator-only
avoids menu and shader override initialization when a plain PMX import only
needs collider display shapes.
"""

from maya import cmds
import maya.api.OpenMaya as om

from mmd_tools import __version__
from mmd_tools.nodes import mmd_rigid_body_locator_node

_registered = False


def maya_useNewAPI():
    """Tell Maya to use the Python API 2.0."""
    pass


def initializePlugin(mobject):
    """Register only the mmdRigidBodyLocator node."""
    global _registered
    type_name = mmd_rigid_body_locator_node.MmdRigidBodyLocatorNode.kTypeName
    if type_name in (cmds.allNodeTypes() or []):
        _registered = False
        return
    plugin_fn = om.MFnPlugin(mobject, "yohawing", __version__)
    mmd_rigid_body_locator_node.register(plugin_fn)
    _registered = True


def uninitializePlugin(mobject):
    """Deregister only the mmdRigidBodyLocator node."""
    global _registered
    if not _registered:
        return
    plugin_fn = om.MFnPlugin(mobject)
    mmd_rigid_body_locator_node.deregister(plugin_fn)
    _registered = False
