"""Contracts for the Illustrator-authored Animator Toolset picker assets."""

from pathlib import Path
import struct
import xml.etree.ElementTree as ET

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.widgets.body_picker_widget import _BODY_REGIONS, _BODY_SOURCES  # noqa: E402
from mmd_tools.ui.widgets.finger_picker_widget import (  # noqa: E402
    _FINGER_REGIONS,
    _FINGER_SHAPE_REGION_IDS,
)
from mmd_tools.ui.widgets.svg_picker_widget import _renderer_bytes  # noqa: E402

ASSET_DIR = Path(__file__).resolve().parents[2] / "mmd_tools" / "ui" / "assets" / "animator_toolset"


def _svg_root(name: str):
    return ET.fromstring((ASSET_DIR / name).read_text(encoding="utf-8"))


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def test_body_svg_contains_every_mapped_source_once():
    root = _svg_root("animpicker_body.svg")
    ids = [element.get("id") for element in root.iter() if element.get("id")]

    assert len(ids) == len(set(ids))
    assert {source.element_id for source in _BODY_SOURCES} <= set(ids)
    assert len({source.region_id for source in _BODY_SOURCES}) == len(_BODY_SOURCES)


def test_body_semantic_regions_have_unique_ids_and_bone_names():
    region_ids = [region["id"] for region in _BODY_REGIONS]
    bone_names = [region["bone_name"] for region in _BODY_REGIONS]

    assert len(region_ids) == len(set(region_ids))
    assert len(bone_names) == len(set(bone_names))
    assert {"lower_body", "left_ik", "right_ik", "left_toe_ik", "right_toe_ik"} <= set(
        region_ids
    )
    assert {"下半身", "左足ＩＫ", "右足ＩＫ", "左つま先ＩＫ", "右つま先ＩＫ"} <= set(
        bone_names
    )


def test_body_torso_art_maps_chest_to_upper_body_2_and_abdomen_to_upper_body():
    source_map = {source.element_id: source.region_id for source in _BODY_SOURCES}

    assert source_map["upper_body"] == "upper_body_2"
    assert source_map["upper_body_2"] == "upper_body"
    assert source_map["lower_body"] == "lower_body"
    assert source_map["left_toe_IK"] == "left_toe_ik"
    assert source_map["right_toe_IK"] == "right_toe_ik"
    assert source_map["mirror_sel-2"] == "ik_enable_left"
    assert source_map["mirror_sel-3"] == "ik_enable_right"


def test_renderer_can_remove_fk_art_without_removing_ik_art():
    svg_text = (ASSET_DIR / "animpicker_body.svg").read_text(encoding="utf-8")
    root = ET.fromstring(
        _renderer_bytes(svg_text, {"left_upper_leg", "left_lower_leg", "left_foot"})
    )
    ids = {element.get("id") for element in root.iter() if element.get("id")}

    assert "left_upper_leg" not in ids
    assert "left_lower_leg" not in ids
    assert "left_foot" not in ids
    assert "left_ik" in ids
    assert "right_upper_leg" not in ids  # authored right leg uses the -2 element IDs
    assert "left_upper_leg-2" in ids


def test_finger_svg_shape_order_matches_bones_and_embedded_navigation():
    root = _svg_root("animpicker_finger.svg")
    shapes = [
        element
        for element in root.iter()
        if _local_name(element.tag) in {"path", "rect", "polygon"}
        and element.get("id") != "canvas-background"
    ]

    assert len(shapes) == 33
    assert len(_FINGER_SHAPE_REGION_IDS) == 33
    assert len(set(_FINGER_SHAPE_REGION_IDS)) == 33
    assert {region["id"] for region in _FINGER_REGIONS} == (
        set(_FINGER_SHAPE_REGION_IDS) - {"back_to_body"}
    )
    assert _FINGER_SHAPE_REGION_IDS[0] == "left_palm"
    assert _FINGER_SHAPE_REGION_IDS[16] == "right_palm"
    assert _FINGER_SHAPE_REGION_IDS[-1] == "back_to_body"


def test_picker_background_is_high_dpi_2x_canvas():
    png_path = ASSET_DIR / "animpicker_bg.png"
    with png_path.open("rb") as stream:
        signature = stream.read(8)
        chunk_length = struct.unpack(">I", stream.read(4))[0]
        chunk_type = stream.read(4)
        width, height = struct.unpack(">II", stream.read(8))

    assert signature == b"\x89PNG\r\n\x1a\n"
    assert chunk_length == 13
    assert chunk_type == b"IHDR"
    assert (width, height) == (536, 756)
