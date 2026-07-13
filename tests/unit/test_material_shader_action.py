import unittest

from tests.common.maya_stub import install_maya_stub

install_maya_stub(profile="headless")

from mmd_tools.actions.material_shader_action import apply_sphere_map  # noqa: E402
from mmd_tools.core.pmx_data.material import PmxSphereMode  # noqa: E402


class _FakeMayaAdapter:
    def __init__(self, file_nodes=None, base_files=None):
        self.file_nodes = list(file_nodes or [])
        self.base_files = list(base_files or [])
        self.created = []
        self.connections = []

    def ls(self, **kwargs):
        if kwargs.get("type") == "file":
            return list(self.file_nodes)
        return []

    def shading_node(self, node_type, **kwargs):
        name = kwargs["name"]
        self.created.append((node_type, kwargs))
        if node_type == "file":
            self.file_nodes.append(name)
        return name

    def list_connections(self, attr, **kwargs):
        if attr.endswith(".baseColor") and kwargs.get("type") == "file":
            return list(self.base_files)
        return []

    def connect_attr(self, source, destination, **kwargs):
        self.connections.append((source, destination, kwargs))


class TestMaterialShaderActionSphereMap(unittest.TestCase):
    def test_apply_sphere_map_additive_creates_file_and_routes_emission(self):
        adapter = _FakeMayaAdapter()
        set_calls = []

        result = apply_sphere_map(
            "mat",
            "sphere.spa",
            PmxSphereMode.ADDITIVE,
            maya_adapter=adapter,
            path_exists=lambda _path: True,
            get_attribute_func=lambda _node, _attr: None,
            set_attribute_func=lambda *args: set_calls.append(args),
        )

        self.assertTrue(result)
        self.assertEqual(adapter.created, [("file", {"asTexture": True, "name": "mat_sphere"})])
        self.assertIn(("mat_sphere", "fileTextureName", "sphere.spa", "str"), set_calls)
        self.assertIn(("mat_sphere.outColor", "mat.emissionColor", {"force": True}), adapter.connections)
        self.assertIn(("mat", "emission", 0.5, "float"), set_calls)

    def test_apply_sphere_map_multiply_reuses_existing_file_and_layered_texture(self):
        adapter = _FakeMayaAdapter(file_nodes=["existing_sphere"], base_files=["base_file"])
        set_calls = []

        result = apply_sphere_map(
            "mat",
            "sphere.sph",
            PmxSphereMode.MULTIPLY,
            maya_adapter=adapter,
            path_exists=lambda _path: True,
            get_attribute_func=lambda node, attr: "sphere.sph" if (node, attr) == ("existing_sphere", "fileTextureName") else None,
            set_attribute_func=lambda *args: set_calls.append(args),
        )

        self.assertTrue(result)
        self.assertEqual(adapter.created, [("layeredTexture", {"asTexture": True, "name": "mat_layered"})])
        self.assertIn(("base_file.outColor", "mat_layered.inputs[0].color", {}), adapter.connections)
        self.assertIn(("existing_sphere.outColor", "mat_layered.inputs[1].color", {}), adapter.connections)
        self.assertIn(("mat_layered.outColor", "mat.baseColor", {"force": True}), adapter.connections)
        self.assertIn(("mat_layered", "inputs[1].blendMode", 6, "int"), set_calls)

    def test_apply_sphere_map_missing_file_returns_false_without_maya_calls(self):
        adapter = _FakeMayaAdapter()

        result = apply_sphere_map(
            "mat",
            "missing.spa",
            PmxSphereMode.ADDITIVE,
            maya_adapter=adapter,
            path_exists=lambda _path: False,
        )

        self.assertFalse(result)
        self.assertEqual(adapter.created, [])
        self.assertEqual(adapter.connections, [])


if __name__ == "__main__":
    unittest.main()
