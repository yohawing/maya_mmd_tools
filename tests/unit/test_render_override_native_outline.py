"""Regression contracts for native VP2 inverted-hull outline culling."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SHADER = ROOT / "mmd_tools" / "shaders" / "MMDNativeShader.fx"


def _technique(source: str, name: str) -> str:
    """Return one technique11 body from the native effect source."""
    match = re.search(
        rf"technique11\s+{re.escape(name)}\s*\{{(?P<body>.*?)\n\}}",
        source,
        re.S,
    )
    assert match is not None, f"missing technique: {name}"
    return match.group("body")


def test_single_sided_native_outline_culls_opposite_side_from_body() -> None:
    """The expanded hull must not repaint the body interior with edge color."""
    source = SHADER.read_text(encoding="utf-8")

    assert "SetRasterizerState(CullFront)" in _technique(
        source, "MMDNativeOpaque"
    )
    for name in ("MMDNativeOutline", "MMDNativeOutlineTranslucent"):
        outline = _technique(source, name)
        assert "SetRasterizerState(CullBack)" in outline
        assert "SetRasterizerState(CullFront)" not in outline


def test_opaque_body_can_replace_equal_depth_outline_without_changing_other_passes() -> None:
    """Equal-depth planes remain visible while opaque hulls still occlude the neck."""
    source = SHADER.read_text(encoding="utf-8")
    state = re.search(
        r"DepthStencilState\s+NativeOpaqueBodyDepth\s*\{(?P<body>.*?)\};",
        source, re.S,
    )
    assert state is not None
    body = state.group("body")
    assert "DepthEnable = TRUE;" in body
    assert "DepthWriteMask = ALL;" in body
    assert "DepthFunc = LESS_EQUAL;" in body
    for name in ("MMDNativeOpaque", "MMDNativeOpaqueDoubleSided"):
        assert "SetDepthStencilState(NativeOpaqueBodyDepth, 0);" in _technique(source, name)
    assert source.count("SetDepthStencilState(NativeOpaqueBodyDepth, 0);") == 2
    for name in ("MMDNativeOutline", "MMDNativeOutlineDoubleSided",
                 "MMDNativeTranslucent", "MMDNativeTranslucentDoubleSided"):
        assert "SetDepthStencilState(EnableDepth, 0);" in _technique(source, name)
    for name in ("MMDNativeOutlineTranslucent", "MMDNativeOutlineTranslucentDoubleSided"):
        assert "SetDepthStencilState(EdgeDepthReadOnly, 0);" in _technique(source, name)
