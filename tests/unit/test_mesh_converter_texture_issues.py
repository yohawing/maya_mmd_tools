"""Pure-Python checks for MeshConverter texture issue reporting."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.converters.mesh_converter import MeshConverter  # noqa: E402


class TestMeshConverterTextureIssues(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=r"F:\tmp")
        self.root = Path(self.tmp.name)
        self.model = self.root / "model.pmx"
        self.model.write_bytes(b"model")
        self.texture = self.root / "颜.png"
        self.texture.write_bytes(b"texture")

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_unresolved_texture_issue_dict_shape(self):
        converter = MeshConverter(str(self.model))
        material = SimpleNamespace(name="Face")

        issue = converter._record_unresolved_texture_issue(
            file_node="Face_file",
            shader="Face_shader",
            material=material,
            original_path=self.texture.name,
            current_path=str(self.texture),
        )

        self.assertEqual(issue["file_node"], "Face_file")
        self.assertEqual(issue["material"], "Face_shader")
        self.assertEqual(issue["material_name"], "Face")
        self.assertEqual(issue["original_path"], self.texture.name)
        self.assertEqual(issue["current_path"], str(self.texture))
        self.assertIn("reason", issue)
        self.assertTrue(issue["resolvable"])
        self.assertEqual(Path(issue["source_path"]), self.texture)
        self.assertEqual(converter.unresolved_texture_count, 1)
        self.assertEqual(converter.profile["unresolved_texture_count"], 1)
        self.assertEqual(converter.profile["unresolved_textures"], [issue])


if __name__ == "__main__":
    unittest.main()
