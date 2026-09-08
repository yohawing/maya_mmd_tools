"""Real Maya DAG rollback and offsetParentMatrix following."""

from unittest import mock

from maya import cmds

from mmd_tools.core.maya_mesh_utils import separate_render_proxy
from tests.common.maya_test_base import MayaTestBase


class TestRenderProxyTransform(MayaTestBase):
    def make_scene(self):
        parent = cmds.group(empty=True, name="model")
        source = cmds.parent(cmds.polyCube()[0], parent)[0]
        proxy = cmds.listRelatives(source, shapes=True, fullPath=True)[0]
        api = mock.Mock(wraps=cmds)
        # The helper only reparents a DAG shape. Use a stock mesh to exercise
        # this boundary without requiring the native renderer in mayapy.
        api.listRelatives.return_value = [proxy]
        return parent, source, proxy, api

    def test_world_transform_includes_offset_and_parent_motion(self):
        parent, source, _, api = self.make_scene()
        render = separate_render_proxy(source, parent, cmds_module=api)
        offset = (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 4, 5, 6, 1)
        cmds.setAttr(source + ".offsetParentMatrix", *offset, type="matrix")
        cmds.setAttr(source + ".rotateY", 35)
        cmds.setAttr(parent + ".translateX", 10)
        self.assertEqual(cmds.xform(source, q=True, worldSpace=True, matrix=True),
                         cmds.xform(render, q=True, worldSpace=True, matrix=True))
        cmds.setAttr(parent + ".scale", 2, 3, 4, type="double3")
        self.assertEqual(cmds.xform(source, q=True, worldSpace=True, matrix=True),
                         cmds.xform(render, q=True, worldSpace=True, matrix=True))

    def test_failed_connection_restores_shape_before_deleting_parent(self):
        parent, source, proxy, api = self.make_scene()
        identity = cmds.ls(proxy, uuid=True)[0]
        api.connectAttr.side_effect = RuntimeError("injected connection failure")
        with self.assertRaisesRegex(RuntimeError, "injected connection failure"):
            separate_render_proxy(source, parent, cmds_module=api)
        restored = cmds.ls(identity, long=True)[0]
        self.assertEqual(cmds.listRelatives(restored, parent=True)[0], source)
        self.assertFalse(cmds.ls("*_render", type="transform"))
