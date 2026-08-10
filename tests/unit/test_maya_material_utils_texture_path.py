"""Texture-path diagnostics must not depend on the process stdout codec."""

from unittest.mock import patch

from mmd_tools.core import maya_material_utils


def test_missing_non_ascii_texture_uses_safe_logger_without_printing():
    texture_name = "tex/颜.png"

    with (
        patch.object(maya_material_utils.os.path, "exists", return_value=False),
        patch.object(maya_material_utils.logger, "warning") as warning,
        patch("builtins.print", side_effect=AssertionError("stdout must not be used")),
    ):
        result = maya_material_utils.sanitize_texture_path(texture_name, "C:/model")

    assert result is None
    warning.assert_called_once_with(
        "Texture file not found: %s",
        maya_material_utils.os.path.normpath("C:/model/tex/颜.png"),
    )
