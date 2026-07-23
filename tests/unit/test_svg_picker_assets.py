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
    assert {"lower_body", "left_ik", "right_ik"} <= set(region_ids)
    assert {"下半身", "左足ＩＫ", "右足ＩＫ"} <= set(bone_names)


def test_finger_svg_shape_order_matches_32_semantic_regions():
    root = _svg_root("animpicker_finger.svg")
    shapes = [
        element
        for element in root.iter()
        if _local_name(element.tag) in {"path", "rect", "polygon"}
        and element.get("id") != "canvas-background"
    ]

    assert len(shapes) == 32
    assert len(_FINGER_SHAPE_REGION_IDS) == 32
    assert len(set(_FINGER_SHAPE_REGION_IDS)) == 32
    assert {region["id"] for region in _FINGER_REGIONS} == set(_FINGER_SHAPE_REGION_IDS)


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
