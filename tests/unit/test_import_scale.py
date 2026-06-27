"""Unit tests for model import scale helper behavior."""

import unittest
from unittest.mock import MagicMock, call, patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.io.import_scale import apply_import_scale  # noqa: E402


class TestImportScale(unittest.TestCase):
    """Scale application should not make import fail on locked attrs."""

    def test_locked_scale_attrs_are_temporarily_unlocked_and_restored(self):
        logger = MagicMock()

        with patch("mmd_tools.io.import_scale.cmds") as cmds:
            cmds.getAttr.return_value = True

            result = apply_import_scale("model_root", 2.0, logger)

        self.assertTrue(result)
        cmds.getAttr.assert_has_calls(
            [
                call("model_root.scaleX", lock=True),
                call("model_root.scaleY", lock=True),
                call("model_root.scaleZ", lock=True),
            ]
        )
        cmds.makeIdentity.assert_called_once_with("model_root", apply=True, scale=True)
        for attr in ("model_root.scaleX", "model_root.scaleY", "model_root.scaleZ"):
            self.assertIn(call(attr, lock=False), cmds.setAttr.call_args_list)
            self.assertIn(call(attr, 2.0), cmds.setAttr.call_args_list)
            self.assertIn(call(attr, lock=True), cmds.setAttr.call_args_list)

    def test_make_identity_failure_is_non_fatal(self):
        logger = MagicMock()

        with patch("mmd_tools.io.import_scale.cmds") as cmds:
            cmds.getAttr.return_value = False
            cmds.makeIdentity.side_effect = RuntimeError("locked scale")

            result = apply_import_scale("model_root", 2.0, logger)

        self.assertFalse(result)
        logger.warning.assert_called_once()
        cmds.setAttr.assert_any_call("model_root.scaleX", 2.0)
        cmds.setAttr.assert_any_call("model_root.scaleY", 2.0)
        cmds.setAttr.assert_any_call("model_root.scaleZ", 2.0)


if __name__ == "__main__":
    unittest.main()
