"""Contracts for non-fatal native material shader setup failures."""

from pathlib import Path


SOURCE = (
    Path(__file__).resolve().parents[2]
    / "cpp"
    / "src"
    / "MmdRenderGeometryOverride.cpp"
)


def test_shader_setup_failures_warn_without_becoming_import_errors() -> None:
    """A created render shape must survive shader setup failures as a warning."""
    source = SOURCE.read_text(encoding="utf-8")
    update_items = source[source.index("void MmdRenderGeometryOverride::updateRenderItems(") :]
    update_items = update_items[
        : update_items.index("void MmdRenderGeometryOverride::populateGeometry(")
    ]

    assert "MGlobal::displayError(" not in update_items
    assert update_items.count("MGlobal::displayWarning(") == 4
    assert "Maya shader manager is unavailable." in update_items
    assert "Native MMD shader is unavailable:" in update_items
    assert "Failed to bind material parameters to" in update_items
    assert "Failed to bind material shader to" in update_items


def test_geometry_population_failures_remain_errors() -> None:
    """Missing geometry is structural and must not be downgraded to a warning."""
    source = SOURCE.read_text(encoding="utf-8")
    populate = source[source.index("void MmdRenderGeometryOverride::populateGeometry(") :]

    assert "MGlobal::displayError(" in populate
    assert "Geometry population failed:" in populate
