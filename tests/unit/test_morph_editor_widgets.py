"""Pure checks for bundled Animator morph editor type assets."""

from pathlib import Path

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.widgets.morph_editor_widgets import (  # noqa: E402
    MorphRowWidget,
    normalized_morph_type,
)


def test_morph_type_aliases_and_unknown_values_are_stable():
    assert normalized_morph_type("additionalUv1") == "additional_uv1"
    assert normalized_morph_type("Additional-UV4") == "additional_uv4"
    assert normalized_morph_type("unknownFutureType") == "unknownfuturetype"


def test_all_supported_morph_type_icons_are_bundled():
    icon_dir = (
        Path(__file__).resolve().parents[2]
        / "mmd_tools"
        / "ui"
        / "assets"
        / "morph_types"
    )
    expected = {
        "vertex",
        "bone",
        "uv",
        "additional_uv1",
        "additional_uv2",
        "additional_uv3",
        "additional_uv4",
        "material",
        "group",
        "flip",
        "impulse",
        "generic",
    }
    assert expected == {path.stem for path in icon_dir.glob("*.svg")}


def test_selected_morph_row_has_a_distinct_marker_and_label_weight():
    style = "".join(MorphRowWidget._STYLE_SHEET.split())

    assert "background:#24546b" in style
    assert "border-left:5pxsolid#79cfff" in style
    assert "font-weight:700" in style
