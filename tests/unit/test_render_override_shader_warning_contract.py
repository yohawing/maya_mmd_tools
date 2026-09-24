"""Contracts for visible native RenderOverride fallback diagnostics."""

from pathlib import Path


SOURCE = (
    Path(__file__).resolve().parents[2]
    / "cpp"
    / "src"
    / "MmdOrderedRenderOverride.cpp"
)
FAST_LOAD_SOURCE = SOURCE.with_name("mmdFastLoad.cpp")


def test_vp2_source_mesh_has_a_neutral_unsupported_api_fallback() -> None:
    """The visible OpenGL fallback must not use Maya's green error material."""
    source = FAST_LOAD_SOURCE.read_text(encoding="utf-8")
    vp2_loader = source[source.index("MStatus MmdFastLoad::loadVp2Ownership(") :]

    assert "assignInitialShadingGroup(sourceMeshObject)" in vp2_loader
    assert 'selection.add("initialShadingGroup")' in source
    assert "shadingSet.addMember(meshPath)" in source
