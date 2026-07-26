import json
import unittest

from maya import cmds

from mmd_tools.core import maya_attribute_utils
from mmd_tools.core.constants import (
    ATTR_MMD_BONE_FLAGS,
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_MORPH_OFFSETS_RAW_JSON,
    ATTR_MMD_BONE_PARENT_INDEX,
    ATTR_MMD_DEFORM_LAYER,
    ATTR_MMD_FIXED_AXIS,
    ATTR_MMD_GRANT_PARENT_INDEX,
    ATTR_MMD_GRANT_RATE,
    ATTR_MMD_IK_LIMIT_ANGLE,
    ATTR_MMD_IK_LINKS,
    ATTR_MMD_IK_LOOP,
    ATTR_MMD_IK_TARGET_INDEX,
    ATTR_MMD_LOCAL_X_AXIS,
    ATTR_MMD_LOCAL_Z_AXIS,
    ATTR_MMD_MORPH_DATA,
    ATTR_MMD_PMX_REST_POSITION,
)
from mmd_tools.core.model_dag_descriptor import ModelDagDescriptorError, build_model_descriptors_from_dag
from mmd_tools.core.native.mmd_anim_runtime_handles import MmdRuntimeInstance, MmdRuntimeModel
from mmd_tools.core.native.mmd_anim_runtime_types import (
    MMD_RUNTIME_FEATURE_MODEL_DESCRIPTOR,
    MMD_RUNTIME_MODEL_APPEND_ROTATION,
    MMD_RUNTIME_MODEL_BONE_FIXED_AXIS,
    MMD_RUNTIME_MODEL_BONE_LOCAL_AXIS,
    MMD_RUNTIME_MODEL_BONE_TRANSFORM_AFTER_PHYSICS,
)
from mmd_tools.core.pmx_data.bone import PmxBoneFlag
from tests.common.maya_test_base import MayaTestBase


class TestModelDagDescriptor(MayaTestBase):
    def setUp(self):
        super().setUp()
        cmds.file(new=True, force=True)

    def _scene(self):
        root = cmds.group(empty=True, name="model_root")
        joints = []
        for index, parent in enumerate((-1, 0)):
            joint = cmds.createNode("joint", name=f"bone_{index}", parent=root)
            flags = 0
            attrs = {
                ATTR_MMD_BONE_INDEX: index,
                ATTR_MMD_BONE_PARENT_INDEX: parent,
                ATTR_MMD_PMX_REST_POSITION: (1.0 + index * 4.0, 2.0, 3.0),
                ATTR_MMD_DEFORM_LAYER: 3 + index,
            }
            if index == 0:
                flags = int(PmxBoneFlag.AXIS_FIXED | PmxBoneFlag.LOCAL_AXIS | PmxBoneFlag.IK)
                attrs.update(
                    {
                        ATTR_MMD_FIXED_AXIS: (0.0, 1.0, 0.0),
                        ATTR_MMD_LOCAL_X_AXIS: (1.0, 0.0, 0.0),
                        ATTR_MMD_LOCAL_Z_AXIS: (0.0, 0.0, 1.0),
                        ATTR_MMD_IK_TARGET_INDEX: 1,
                        ATTR_MMD_IK_LOOP: 8,
                        ATTR_MMD_IK_LIMIT_ANGLE: 0.5,
                        ATTR_MMD_IK_LINKS: json.dumps(
                            [{"bone": 1, "limit_enabled": False, "lower_limit": [0, 0, 0], "upper_limit": [0, 0, 0]}]
                        ),
                    }
                )
            else:
                flags = int(PmxBoneFlag.DEFORM_AFTER_PHYSICS | PmxBoneFlag.GRANT_PARENT_ROTATE)
                attrs.update({ATTR_MMD_GRANT_PARENT_INDEX: 0, ATTR_MMD_GRANT_RATE: 0.25})
            attrs[ATTR_MMD_BONE_FLAGS] = flags
            maya_attribute_utils.set_custom_attributes(joint, attrs)
            joints.append(joint)

        for name, morph_type, index, attr, value in (
            (
                "bone_morph",
                "bone",
                0,
                ATTR_MMD_BONE_MORPH_OFFSETS_RAW_JSON,
                [{"bone_index": 1, "translation": [0.5, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0, 1.0]}],
            ),
            (
                "group_morph",
                "group",
                1,
                "mmd_group_morph_offsets_json",
                [{"morph_index": 0, "morph_rate": 0.75}],
            ),
        ):
            node = cmds.createNode("network", name=name)
            maya_attribute_utils.set_custom_attributes(
                node,
                {"mmd_morph_type": morph_type, "mmd_morph_index": index, attr: json.dumps(value)},
            )
            cmds.addAttr(node, longName="mmd_model_root", attributeType="message")
            cmds.connectAttr(f"{root}.message", f"{node}.mmd_model_root")
        return root

    @staticmethod
    def _set_authoritative_morph_data(root, count):
        cmds.addAttr(root, longName=ATTR_MMD_MORPH_DATA, dataType="string")
        cmds.setAttr(
            f"{root}.{ATTR_MMD_MORPH_DATA}",
            json.dumps([{"index": index} for index in range(count)]),
            type="string",
        )

    def test_compiles_complete_scene_metadata(self):
        descriptors = build_model_descriptors_from_dag(self._scene())
        self.assertEqual(len(descriptors.bones), 2)
        self.assertEqual(list(descriptors.bones[0].rest_position_xyz), [1.0, 2.0, 3.0])
        self.assertEqual(descriptors.bones[0].transform_order, 3)
        self.assertEqual(
            descriptors.bones[0].flags,
            MMD_RUNTIME_MODEL_BONE_FIXED_AXIS | MMD_RUNTIME_MODEL_BONE_LOCAL_AXIS,
        )
        self.assertEqual(descriptors.bones[1].flags, MMD_RUNTIME_MODEL_BONE_TRANSFORM_AFTER_PHYSICS)
        self.assertEqual(len(descriptors.ik_solvers), 1)
        self.assertEqual(len(descriptors.ik_links), 1)
        self.assertEqual(len(descriptors.append_transforms), 1)
        self.assertEqual(descriptors.append_transforms[0].flags, MMD_RUNTIME_MODEL_APPEND_ROTATION)
        self.assertEqual(descriptors.morph_count, 2)
        self.assertEqual(len(descriptors.bone_morph_offsets), 1)
        self.assertEqual(len(descriptors.group_morph_offsets), 1)

    def test_native_model_uses_absolute_rest_contract(self):
        descriptors = build_model_descriptors_from_dag(self._scene())
        lib = MmdRuntimeModel._get_library()
        if lib is None or not hasattr(lib, "mmd_runtime_model_create_from_descriptor"):
            self.skipTest("payload-free model descriptor ABI unavailable")
        if not hasattr(lib, "mmd_runtime_feature_flags") or not (
            int(lib.mmd_runtime_feature_flags()) & MMD_RUNTIME_FEATURE_MODEL_DESCRIPTOR
        ):
            self.skipTest("payload-free model descriptor feature unavailable")
        model = MmdRuntimeModel.from_descriptors(descriptors)
        self.assertIsNotNone(model)
        instance = MmdRuntimeInstance.for_model(model)
        self.assertIsNotNone(instance)
        self.assertTrue(instance.evaluate_rest_pose())
        matrices = instance.get_world_matrices()
        self.assertEqual([round(value, 5) for value in matrices[0][12:15]], [1.0, 2.0, 3.0])
        self.assertEqual([round(value, 5) for value in matrices[1][12:15]], [5.0, 2.0, 3.0])
        instance.free()
        model.free()

    def test_rejects_unindexed_joint(self):
        root = self._scene()
        cmds.createNode("joint", name="unindexed_helper", parent=root)
        with self.assertRaisesRegex(ModelDagDescriptorError, "missing required attribute mmd_bone_index"):
            build_model_descriptors_from_dag(root)

    def test_rejects_non_finite_scalar(self):
        root = self._scene()
        cmds.setAttr(f"bone_0.{ATTR_MMD_IK_LIMIT_ANGLE}", float("nan"))
        with self.assertRaisesRegex(ModelDagDescriptorError, "expected finite float"):
            build_model_descriptors_from_dag(root)

    def test_rejects_out_of_range_group_morph_child(self):
        root = self._scene()
        cmds.setAttr(
            "group_morph.mmd_group_morph_offsets_json",
            json.dumps([{"morph_index": 2, "morph_rate": 1.0}]),
            type="string",
        )
        with self.assertRaisesRegex(ModelDagDescriptorError, "exceeds morph count"):
            build_model_descriptors_from_dag(root)

    def test_authoritative_source_morph_count_preserves_high_group_child(self):
        root = self._scene()
        self._set_authoritative_morph_data(root, 131)
        cmds.setAttr("bone_morph.mmd_morph_index", 128)
        cmds.setAttr("group_morph.mmd_morph_index", 29)
        cmds.setAttr(
            "group_morph.mmd_group_morph_offsets_json",
            json.dumps([{"morph_index": 130, "morph_rate": 1.0}]),
            type="string",
        )

        descriptors = build_model_descriptors_from_dag(root)

        self.assertEqual(descriptors.morph_count, 131)
        self.assertEqual(descriptors.group_morph_offsets[0].morph_index, 29)
        self.assertEqual(descriptors.group_morph_offsets[0].child_morph_index, 130)

    def test_legacy_morph_data_schemas_preserve_network_fallback_count(self):
        for label, raw in (
            ("empty string", ""),
            ("empty list", "[]"),
            ("legacy dict", json.dumps({"0": "base", "1": "group"})),
        ):
            with self.subTest(schema=label):
                root = self._scene()
                cmds.addAttr(root, longName=ATTR_MMD_MORPH_DATA, dataType="string")
                cmds.setAttr(f"{root}.{ATTR_MMD_MORPH_DATA}", raw, type="string")

                descriptors = build_model_descriptors_from_dag(root)

                self.assertEqual(descriptors.morph_count, 2)

    def test_authoritative_source_morph_count_rejects_group_child_131(self):
        root = self._scene()
        self._set_authoritative_morph_data(root, 131)
        cmds.setAttr(
            "group_morph.mmd_group_morph_offsets_json",
            json.dumps([{"morph_index": 131, "morph_rate": 1.0}]),
            type="string",
        )

        with self.assertRaisesRegex(ModelDagDescriptorError, "exceeds morph count 131"):
            build_model_descriptors_from_dag(root)

    def test_authoritative_source_morph_count_rejects_network_index_131(self):
        root = self._scene()
        self._set_authoritative_morph_data(root, 131)
        node = cmds.createNode("network", name="network_morph_131")
        maya_attribute_utils.set_custom_attributes(
            node,
            {
                "mmd_morph_type": "bone",
                "mmd_morph_index": 131,
                ATTR_MMD_BONE_MORPH_OFFSETS_RAW_JSON: "[]",
            },
        )
        cmds.addAttr(node, longName="mmd_model_root", attributeType="message")
        cmds.connectAttr(f"{root}.message", f"{node}.mmd_model_root")

        with self.assertRaisesRegex(ModelDagDescriptorError, "exceeds authoritative morph count 131"):
            build_model_descriptors_from_dag(root)

    def test_rejects_malformed_authoritative_morph_list(self):
        root = self._scene()
        self._set_authoritative_morph_data(root, 1)
        cmds.setAttr(
            f"{root}.{ATTR_MMD_MORPH_DATA}",
            json.dumps([{"index": 1}]),
            type="string",
        )

        with self.assertRaisesRegex(ModelDagDescriptorError, "entry 0 index must be 0"):
            build_model_descriptors_from_dag(root)


if __name__ == "__main__":
    unittest.main()
