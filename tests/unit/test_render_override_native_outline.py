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
