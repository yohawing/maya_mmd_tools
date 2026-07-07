"""Direct-import safety net for split Maya utility modules."""

from __future__ import annotations

import importlib
import unittest

from tests.common.maya_stub import install_maya_stub

install_maya_stub(profile="headless")


class TestCoreMayaUtilsDirectImport(unittest.TestCase):
    def _assert_callables(self, module_name: str, names: tuple[str, ...]) -> None:
        module = importlib.import_module(module_name)
        for name in names:
            with self.subTest(module=module_name, name=name):
                self.assertTrue(callable(getattr(module, name)))

    def test_maya_mesh_utils_public_helpers_import_directly(self):
        self._assert_callables(
            "mmd_tools.core.maya_mesh_utils",
            (
                "create_mesh_with_uvs",
                "split_mesh_by_material",
                "get_materials_from_mesh",
                "apply_vertex_weights",
                "find_or_create_blendshape_node",
            ),
        )

    def test_maya_material_utils_public_helpers_import_directly(self):
        module = importlib.import_module("mmd_tools.core.maya_material_utils")

        self.assertIn("_texture", module.DX11_TEXTURE_SLOTS)
        self.assertEqual(module.ATTR_MMD_TEXTURE_SOURCE_KIND, "mmd_texture_source_kind")
        self.assertEqual(module.ATTR_MMD_SHARED_TOON_ID, "mmd_shared_toon_id")
        self._assert_callables(
            "mmd_tools.core.maya_material_utils",
            (
                "sanitize_texture_path",
                "mark_mmd_texture_file_node",
                "get_mmd_original_texture_path",
                "is_mmd_file_node_unreadable",
                "find_material_texture_file_node",
                "classify_mmd_texture_file_node",
                "resolve_mmd_texture_file_node",
                "bind_dx11_texture_file_node",
                "rebind_resolved_mmd_dx11_texture",
                "rebind_resolved_scene_mmd_dx11_textures",
                "resolve_mmd_material_texture",
                "resolve_scene_mmd_textures",
                "create_material",
                "assign_material",
                "assign_material_to_faces",
            ),
        )

    def test_maya_physics_utils_public_helpers_import_directly(self):
        self._assert_callables(
            "mmd_tools.core.maya_physics_utils",
            (
                "find_or_create_nucleus_solver",
                "create_collision_primitive",
                "apply_ncloth_to_mesh",
                "apply_nrigid_to_mesh",
                "create_dynamic_curve",
                "apply_nhair_to_curve",
            ),
        )

    def test_maya_animation_utils_public_helpers_import_directly(self):
        self._assert_callables(
            "mmd_tools.core.maya_animation_utils",
            (
                "create_animation_curves",
                "set_keyframes_batch",
            ),
        )

    def test_maya_transform_utils_public_helpers_import_directly(self):
        self._assert_callables(
            "mmd_tools.core.maya_transform_utils",
            (
                "create_matrix_from_axes",
                "matrix_to_euler",
            ),
        )

    def test_maya_viewport_utils_public_helpers_import_directly(self):
        self._assert_callables(
            "mmd_tools.core.maya_viewport_utils",
            (
                "set_viewport_backface_culling",
            ),
        )

    def test_maya_rig_utils_public_helpers_import_directly(self):
        self._assert_callables(
            "mmd_tools.core.maya_rig_utils",
            (
                "create_ik_handle",
                "set_joint_limits",
                "create_pole_vector_constraint",
            ),
        )

    def test_maya_scene_utils_public_helpers_import_directly(self):
        self._assert_callables(
            "mmd_tools.core.maya_scene_utils",
            (
                "select_objects",
                "object_exists",
                "parent_objects",
                "list_objects",
            ),
        )

    def test_maya_attribute_utils_public_helpers_import_directly(self):
        self._assert_callables(
            "mmd_tools.core.maya_attribute_utils",
            (
                "set_custom_attributes",
                "add_numeric_attribute",
                "add_typed_attribute",
                "repair_fbx_mojibake_string",
                "set_attribute",
                "get_attribute",
                "attribute_exists",
                "get_attr_safe",
                "read_json_attr",
                "write_json_attr",
                "find_tagged_nodes",
                "mark_bool_tag",
                "disconnect_sources",
                "connect_if_needed",
                "get_int_array_attribute",
            ),
        )

    def test_maya_utils_animation_helpers_remain_compatibility_shims(self):
        maya_utils = importlib.import_module("mmd_tools.core.maya_utils")
        maya_animation_utils = importlib.import_module("mmd_tools.core.maya_animation_utils")

        self.assertIs(maya_utils.create_animation_curves, maya_animation_utils.create_animation_curves)
        self.assertIs(maya_utils.set_keyframes_batch, maya_animation_utils.set_keyframes_batch)

    def test_maya_utils_transform_helpers_remain_compatibility_shims(self):
        maya_utils = importlib.import_module("mmd_tools.core.maya_utils")
        maya_transform_utils = importlib.import_module("mmd_tools.core.maya_transform_utils")

        self.assertIs(maya_utils.create_matrix_from_axes, maya_transform_utils.create_matrix_from_axes)
        self.assertIs(maya_utils.matrix_to_euler, maya_transform_utils.matrix_to_euler)

    def test_maya_utils_viewport_helpers_remain_compatibility_shims(self):
        maya_utils = importlib.import_module("mmd_tools.core.maya_utils")
        maya_viewport_utils = importlib.import_module("mmd_tools.core.maya_viewport_utils")

        self.assertIs(maya_utils.set_viewport_backface_culling, maya_viewport_utils.set_viewport_backface_culling)

    def test_maya_utils_rig_helpers_remain_compatibility_shims(self):
        maya_utils = importlib.import_module("mmd_tools.core.maya_utils")
        maya_rig_utils = importlib.import_module("mmd_tools.core.maya_rig_utils")

        self.assertIs(maya_utils.create_ik_handle, maya_rig_utils.create_ik_handle)
        self.assertIs(maya_utils.set_joint_limits, maya_rig_utils.set_joint_limits)
        self.assertIs(maya_utils.create_pole_vector_constraint, maya_rig_utils.create_pole_vector_constraint)

    def test_maya_utils_scene_helpers_remain_compatibility_shims(self):
        maya_utils = importlib.import_module("mmd_tools.core.maya_utils")
        maya_scene_utils = importlib.import_module("mmd_tools.core.maya_scene_utils")

        self.assertIs(maya_utils.select_objects, maya_scene_utils.select_objects)
        self.assertIs(maya_utils.object_exists, maya_scene_utils.object_exists)
        self.assertIs(maya_utils.parent_objects, maya_scene_utils.parent_objects)
        self.assertIs(maya_utils.list_objects, maya_scene_utils.list_objects)

    def test_maya_utils_attribute_helpers_remain_compatibility_shims(self):
        maya_utils = importlib.import_module("mmd_tools.core.maya_utils")
        maya_attribute_utils = importlib.import_module("mmd_tools.core.maya_attribute_utils")

        self.assertIs(maya_utils.set_custom_attributes, maya_attribute_utils.set_custom_attributes)
        self.assertIs(maya_utils.add_numeric_attribute, maya_attribute_utils.add_numeric_attribute)
        self.assertIs(maya_utils.add_typed_attribute, maya_attribute_utils.add_typed_attribute)
        self.assertIs(maya_utils.repair_fbx_mojibake_string, maya_attribute_utils.repair_fbx_mojibake_string)
        self.assertIs(maya_utils.set_attribute, maya_attribute_utils.set_attribute)
        self.assertIs(maya_utils.get_attribute, maya_attribute_utils.get_attribute)
        self.assertIs(maya_utils.attribute_exists, maya_attribute_utils.attribute_exists)
        self.assertIs(maya_utils.get_attr_safe, maya_attribute_utils.get_attr_safe)
        self.assertIs(maya_utils.read_json_attr, maya_attribute_utils.read_json_attr)
        self.assertIs(maya_utils.write_json_attr, maya_attribute_utils.write_json_attr)
        self.assertIs(maya_utils.find_tagged_nodes, maya_attribute_utils.find_tagged_nodes)
        self.assertIs(maya_utils.mark_bool_tag, maya_attribute_utils.mark_bool_tag)
        self.assertIs(maya_utils.disconnect_sources, maya_attribute_utils.disconnect_sources)
        self.assertIs(maya_utils.connect_if_needed, maya_attribute_utils.connect_if_needed)
        self.assertIs(maya_utils.get_int_array_attribute, maya_attribute_utils.get_int_array_attribute)


if __name__ == "__main__":
    unittest.main()
