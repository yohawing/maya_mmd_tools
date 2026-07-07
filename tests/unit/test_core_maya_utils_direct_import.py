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
                "setup_mmd_color_management",
                "setup_mmd_transparency",
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

    def test_maya_name_utils_public_helpers_import_directly(self):
        self._assert_callables(
            "mmd_tools.core.maya_name_utils",
            (
                "sanitize_text",
                "sanitize_bone_name",
            ),
        )

if __name__ == "__main__":
    unittest.main()
