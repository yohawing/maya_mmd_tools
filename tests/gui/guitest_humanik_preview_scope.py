"""HumanIK preview ownership with a second model's indirect writer."""

from pathlib import Path

from maya import cmds

from mmd_tools.core.humanik_builder import (
    create_humanik_definition_from_scene,
    lock_humanik_definition,
)
from mmd_tools.core.humanik_preview import (
    begin_humanik_target_preview,
    stop_humanik_target_preview,
)
from tests.common.gui_test_base import GuiTestBase, requires_gui
from tests.viewport.humanik_definition_smoke import _build_body_fixture_scene


@requires_gui
class TestHumanIkPreviewScope(GuiTestBase):
    def test_indirect_writer_is_scoped_to_target(self):
        cmds.file(new=True, force=True)
        cmds.loadPlugin(str(Path(__file__).resolve().parents[2] / "mmd_tools/plugin_main.py"), quiet=True)
        source_root = cmds.rename(_build_body_fixture_scene(), "sourceModel")
        source = create_humanik_definition_from_scene(source_root, name_hint="Source", update_ui=False)
        target_root = _build_body_fixture_scene()
        target = create_humanik_definition_from_scene(target_root, name_hint="Target", update_ui=False)
        lock_humanik_definition(source)
        lock_humanik_definition(target)
        joints = set(cmds.listRelatives(target_root, allDescendents=True, type="joint", fullPath=True))

        cmds.select(clear=True)
        other_joint = cmds.joint(name="otherModelJoint")
        writer = cmds.createNode("mmdAppend", name="otherModelAppend")
        utility = cmds.createNode("unitConversion", name="otherModelUtility")
        cmds.connectAttr(writer + ".outputRotateX", utility + ".input")
        cmds.connectAttr(utility + ".output", other_joint + ".rotateX")

        preview = begin_humanik_target_preview("scope-test", target, source, {"rows": []}, joints)
        try:
            self.assertTrue(preview.active)
            self.assertTrue(cmds.isConnected(utility + ".output", other_joint + ".rotateX"))
        finally:
            stop_humanik_target_preview(preview)

        cmds.disconnectAttr(utility + ".output", other_joint + ".rotateX")
        destination = sorted(joints)[0] + ".rotateX"
        cmds.connectAttr(utility + ".output", destination)
        with self.assertRaisesRegex(RuntimeError, "pre-scan found blocker"):
            begin_humanik_target_preview("scope-test", target, source, {"rows": []}, joints)
        self.assertTrue(cmds.isConnected(utility + ".output", destination))
