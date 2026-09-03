"""Material placeholder names are resolved without changing PMX metadata."""
import unittest

from mmd_tools.core.pmx_data.material import PmxMaterial
from mmd_tools.core.pmx_data.header import PmxEncoding


class TestMaterialNamePolicy(unittest.TestCase):
    def test_en_placeholder_uses_original_name_without_mutating_metadata(self):
        for original, english, expected in (
            ("目", "en", "eyes"),
            ("髪", " EN ", "hair"),
            ("目", "Custom Eyes", "Custom Eyes"),
            ("目", "", "目"),
            ("", "en", "en"),
        ):
            with self.subTest(original=original, english=english):
                material = PmxMaterial(encoding=PmxEncoding.UTF8)
                material.name, material.name_english = original, english
                self.assertEqual(material.get_name(), expected)
                self.assertEqual((material.name, material.name_english), (original, english))
