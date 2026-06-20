import base64
import unittest
from unittest.mock import patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.core import maya_utils  # noqa: E402
from mmd_tools.core.constants import ATTR_MMD_ORIGINAL_TEXTURE_PATH  # noqa: E402


class TestMayaUtilsTextureProvenance(unittest.TestCase):
    def test_mark_mmd_texture_file_node_stores_plain_original_path(self):
        with patch.object(maya_utils, "set_custom_attributes") as mock_set:
            maya_utils.mark_mmd_texture_file_node(
                "file1",
                "textures/髪.png",
                "F:/model/model.pmx",
                unresolved=True,
            )

        attrs = mock_set.call_args.args[1]
        self.assertEqual(attrs[ATTR_MMD_ORIGINAL_TEXTURE_PATH], "textures/髪.png")
        self.assertEqual(attrs["mmd_texture_source_kind"], "pmx_texture")
        self.assertTrue(attrs["mmd_texture_unresolved"])

    def test_get_mmd_original_texture_path_decodes_legacy_base64_when_path_like(self):
        encoded = base64.urlsafe_b64encode("textures/髪.png".encode("utf-8")).decode("ascii")
        with patch.object(maya_utils, "get_attribute", return_value=encoded):
            self.assertEqual(maya_utils.get_mmd_original_texture_path("file1"), "textures/髪.png")

    def test_get_mmd_original_texture_path_keeps_plain_text(self):
        with patch.object(maya_utils, "get_attribute", return_value="textures/髪.png"):
            self.assertEqual(maya_utils.get_mmd_original_texture_path("file1"), "textures/髪.png")

    def test_shared_toon_file_node_is_not_classified_for_path_resolve(self):
        with patch.object(maya_utils, "get_attribute", return_value="shared_toon"):
            self.assertIsNone(maya_utils.classify_mmd_texture_file_node("file1"))


if __name__ == "__main__":
    unittest.main()
