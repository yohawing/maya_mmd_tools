"""VMD scene name mapping context tests."""

from unittest.mock import MagicMock

import maya.cmds as cmds

from mmd_tools.converters.vmd_context import VmdNameMappingContext
from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.converters.vmd_name_mapping import build_name_mappings
from mmd_tools.core.constants import ATTR_MMD_BONE_INDEX, ATTR_MMD_BONE_NAME
from tests.common.maya_test_base import MayaTestBase


def _add_mmd_bone_attrs(joint: str, bone_name: str, bone_index: int) -> None:
    if not cmds.attributeQuery(ATTR_MMD_BONE_NAME, node=joint, exists=True):
        cmds.addAttr(joint, longName=ATTR_MMD_BONE_NAME, dataType="string")
    if not cmds.attributeQuery(ATTR_MMD_BONE_INDEX, node=joint, exists=True):
        cmds.addAttr(joint, longName=ATTR_MMD_BONE_INDEX, attributeType="long")
    cmds.setAttr(f"{joint}.{ATTR_MMD_BONE_NAME}", bone_name, type="string")
    cmds.setAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}", bone_index)


class TestVmdNameMapping(MayaTestBase):
    """Name mapping helpers work from explicit context state."""

    def setUp(self):
        super().setUp()
        self.converter = VmdConverter()

    def test_build_name_mappings_accepts_direct_context(self):
        """Direct name-mapping contexts collect bone and index mappings."""
        joint = cmds.joint(name="name_mapping_center")
        _add_mmd_bone_attrs(joint, "センター", 3)
        morph_refreshes = []
        bone_name_mapping = {}
        bone_name_to_index = {"stale": 99}
        bone_index_to_joint = {99: "stale_joint"}
        logger_mock = MagicMock()
        context = VmdNameMappingContext(
            logger=logger_mock,
            bone_name_mapping=bone_name_mapping,
            bone_name_to_index=bone_name_to_index,
            bone_index_to_joint=bone_index_to_joint,
            build_morph_mappings=lambda target_model: morph_refreshes.append(target_model),
        )

        build_name_mappings(context)

        self.assertEqual(bone_name_mapping["センター"], joint)
        self.assertEqual(bone_name_to_index, {"センター": 3})
        self.assertEqual(bone_index_to_joint, {3: joint})
        self.assertEqual(morph_refreshes, [None])

        # Internal mapping setup stays on DEBUG; not INFO.
        debug_msgs = [call[0][0] for call in logger_mock.debug.call_args_list if call[0]]
        info_msgs = [call[0][0] for call in logger_mock.info.call_args_list if call[0]]
        self.assertIn("Building name mapping", debug_msgs)
        self.assertTrue(
            any(
                isinstance(msg, str) and msg.startswith("Built ") and "bone mappings" in msg
                for msg in debug_msgs
            ),
            "expected DEBUG log for built bone mappings, got %r" % (debug_msgs,),
        )
        self.assertNotIn("Building name mapping", info_msgs)
        self.assertFalse(
            any(
                isinstance(msg, str) and msg.startswith("Built ") and "bone mappings" in msg
                for msg in info_msgs
            ),
            "built bone mappings must not be INFO",
        )

        cmds.delete(joint)

    def test_build_name_mappings_supports_legacy_no_arg_morph_refresh(self):
        """Existing direct contexts with no-arg callbacks remain compatible."""
        refreshes = []
        context = VmdNameMappingContext(
            logger=MagicMock(),
            bone_name_mapping={},
            bone_name_to_index={},
            bone_index_to_joint={},
            build_morph_mappings=lambda: refreshes.append("called"),
        )

        build_name_mappings(context, target_model="missing_model_root")

        self.assertEqual(refreshes, ["called"])

    def test_build_name_mappings_scopes_namespace_less_duplicate_bones_to_target_root(self):
        """The explicit root, not a namespace or scene-wide name, owns bone mapping."""
        root_a = cmds.group(empty=True, name="model_a_root")
        root_b = cmds.group(empty=True, name="model_b_root")
        cmds.select(clear=True)
        joint_a = cmds.joint(name="model_a_center")
        cmds.parent(joint_a, root_a)
        cmds.select(clear=True)
        joint_b = cmds.joint(name="model_b_center")
        cmds.parent(joint_b, root_b)
        _add_mmd_bone_attrs(joint_a, "センター", 0)
        _add_mmd_bone_attrs(joint_b, "センター", 0)

        morph_roots = []
        context = VmdNameMappingContext(
            logger=MagicMock(),
            bone_name_mapping={},
            bone_name_to_index={},
            bone_index_to_joint={},
            build_morph_mappings=lambda target_model: morph_roots.append(target_model),
        )

        build_name_mappings(context, target_model=root_b)

        mapped_joint = context.bone_name_mapping["センター"]
        self.assertEqual(cmds.ls(mapped_joint, long=True), cmds.ls(joint_b, long=True))
        self.assertNotEqual(cmds.ls(mapped_joint, long=True), cmds.ls(joint_a, long=True))
        self.assertEqual(context.bone_name_to_index, {"センター": 0})
        self.assertEqual(cmds.ls(context.bone_index_to_joint[0], long=True), cmds.ls(joint_b, long=True))
        self.assertEqual(morph_roots, [root_b])

    def test_name_mapping_context_factory_binds_converter_state(self):
        """Converter factory exposes mutable mapping state and morph refresh callable."""
        context = self.converter._name_mapping_context()

        self.assertIsInstance(context, VmdNameMappingContext)
        self.assertIs(context.logger, self.converter.logger)
        self.assertIs(context.bone_name_mapping, self.converter.bone_name_mapping)
        self.assertIs(context.bone_name_to_index, self.converter.bone_name_to_index)
        self.assertIs(context.bone_index_to_joint, self.converter.bone_index_to_joint)
        self.assertIs(context.build_morph_mappings.__self__, self.converter)
