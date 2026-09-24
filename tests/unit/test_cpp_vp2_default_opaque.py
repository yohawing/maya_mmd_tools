"""Contracts for native VP2 transparency classification boundaries."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_native_vp2_import_restores_authored_and_texture_alpha_classification():
    source = (ROOT / "cpp" / "src" / "mmdFastLoad.cpp").read_text(encoding="utf-8")
    helper_start = source.index("void classifyMaterialTextureAlpha")
    vp2_start = source.index("MStatus MmdFastLoad::loadVp2Ownership")
    helper_source = source[helper_start:vp2_start]
    vp2_source = source[vp2_start:]

    assert "input.transparencyMode = materialTransparencyMode(material);" in vp2_source
    assert "classifyMaterialTextureAlpha" in vp2_source
    assert "loadTextureAlpha" in helper_source
    assert "mmd::classifyMmdTextureAlpha" in helper_source
    assert "soft-alpha textures" in vp2_source


def test_non_vp2_split_import_preserves_authored_transparency_via_shared_classifier():
    source = (ROOT / "cpp" / "src" / "mmdFastLoad.cpp").read_text(encoding="utf-8")
    split_start = source.index("MStatus MmdFastLoad::loadSplit")
    vp2_start = source.index("MStatus MmdFastLoad::loadVp2Ownership")
    split_source = source[split_start:vp2_start]

    assert "input.transparencyMode = materialTransparencyMode(material);" in split_source
    assert "classifyMaterialTextureAlpha" in split_source
    assert "mmd::classifyMmdTextureAlpha" not in split_source
