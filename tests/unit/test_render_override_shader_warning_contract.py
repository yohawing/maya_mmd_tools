"""Contracts for visible native RenderOverride fallback diagnostics."""

from pathlib import Path


SOURCE = (
    Path(__file__).resolve().parents[2]
    / "cpp"
    / "src"
    / "MmdRenderGeometryOverride.cpp"
)
FAST_LOAD_SOURCE = SOURCE.with_name("mmdFastLoad.cpp")


def test_shader_setup_failures_report_gray_fallback_as_errors() -> None:
    """Fatal shader setup failures must explain the visible gray fallback."""
    source = SOURCE.read_text(encoding="utf-8")
    update_items = source[source.index("void MmdRenderGeometryOverride::updateRenderItems(") :]
    update_items = update_items[
        : update_items.index("void MmdRenderGeometryOverride::populateGeometry(")
    ]

    assert update_items.count("MGlobal::displayError(") == 7
    assert "MGlobal::displayWarning(" not in update_items
    assert "Maya shader manager is unavailable." in update_items
    assert "Could not create a native wireframe " in update_items
    assert "render item. Showing the gray source-mesh fallback." in update_items
    assert "Native wireframe shader is unavailable." in update_items
    assert "Could not create material render item" in update_items
    assert "Native MMD shader is unavailable:" in update_items
    assert "Failed to bind material parameters to" in update_items
    assert "Failed to bind material shader to" in update_items
    assert update_items.count("Showing the gray source-mesh fallback.") == 7
    assert update_items.count("recordRenderFallbackReason(") == 7


def test_geometry_population_failures_remain_errors() -> None:
    """Missing geometry is structural and must not be downgraded to a warning."""
    source = SOURCE.read_text(encoding="utf-8")
    populate = source[source.index("void MmdRenderGeometryOverride::populateGeometry(") :]

    assert "MGlobal::displayError(" in populate
    assert "Geometry population failed:" in populate


def test_hlsl_native_shader_is_advertised_only_to_directx11() -> None:
    """OpenGL must keep the source-mesh fallback instead of compiling HLSL."""
    source = SOURCE.read_text(encoding="utf-8")
    api_block = source[source.index("MHWRender::DrawAPI MmdRenderGeometryOverride::supportedDrawAPIs()") :]
    api_block = api_block[: api_block.index("bool MmdRenderGeometryOverride::hasUIDrawables()")]

    assert "kDirectX11" in api_block
    assert "kOpenGL" not in api_block
    assert "kOpenGLCoreProfile" not in api_block


def test_vp2_source_mesh_has_a_neutral_unsupported_api_fallback() -> None:
    """The visible OpenGL fallback must not use Maya's green error material."""
    source = FAST_LOAD_SOURCE.read_text(encoding="utf-8")
    vp2_loader = source[source.index("MStatus MmdFastLoad::loadVp2Ownership(") :]

    assert "assignInitialShadingGroup(sourceMeshObject)" in vp2_loader
    assert 'selection.add("initialShadingGroup")' in source
    assert "shadingSet.addMember(meshPath)" in source
