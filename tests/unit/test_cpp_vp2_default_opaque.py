"""Contracts for the stable opaque default of the native VP2 import route."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_native_vp2_import_forces_opaque_without_texture_alpha_classification():
    source = (ROOT / "cpp" / "src" / "mmdFastLoad.cpp").read_text(encoding="utf-8")
    vp2_start = source.index("MStatus MmdFastLoad::loadVp2Ownership")
    vp2_source = source[vp2_start:]

    assert 'input.transparencyMode = "opaque";' in vp2_source
    assert "classifyMmdTextureAlpha" not in vp2_source
    assert "loadTextureAlpha" not in vp2_source
