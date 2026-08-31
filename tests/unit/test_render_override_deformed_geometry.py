"""Source-level contract tests for evaluated RenderOverride geometry."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SHAPE_HEADER = ROOT / "cpp" / "src" / "MmdRenderShape.h"
SHAPE_SOURCE = ROOT / "cpp" / "src" / "MmdRenderShape.cpp"
OVERRIDE_SOURCE = ROOT / "cpp" / "src" / "MmdRenderGeometryOverride.cpp"


def test_render_shape_exposes_storable_mesh_input_and_source_mapping():
    header = SHAPE_HEADER.read_text(encoding="utf-8")
    source = SHAPE_SOURCE.read_text(encoding="utf-8")

    assert "static MObject aInputMesh;" in header
    assert '"inputMesh", "in", MFnData::kMesh' in source
    assert "typedAttribute.setStorable(true);" in source
    assert "typedAttribute.setWritable(true);" in source
    assert "sourceVertexIndices" in header
    assert "queueInputs, scale, {}" in source
    assert "mismatched source-index data" in source


def test_update_dg_reads_evaluated_mesh_and_fails_closed():
    shape = SHAPE_SOURCE.read_text(encoding="utf-8")
    override = OVERRIDE_SOURCE.read_text(encoding="utf-8")

    assert "bool MmdRenderShape::updateEvaluatedMesh" in shape
    assert "getPoints(points, MSpace::kObject)" in shape
    assert "getVertexNormals(true, normals, MSpace::kObject)" in shape
    assert "source mapping index exceeds input mesh vertex count" in shape
    assert "geometryValid_ = false;" in shape
    assert "staticPositions_" in shape
    assert "staticNormals_" in shape
    assert "geometry_.positions = std::move(nextPositions);" in shape
    assert "geometry_.normals = std::move(nextNormals);" in shape
    assert "static_cast<std::size_t>(points.length()) < expectedSourceVertexCount" in shape

    update_dg = override[override.index("void MmdRenderGeometryOverride::updateDG()") :]
    assert "MmdRenderShape::aInputMesh" in update_dg
    assert "inputPlug.asMDataHandle" in update_dg
    assert "inputHandle.asMesh()" in update_dg
    assert "shape_->updateEvaluatedMesh(meshObject);" in update_dg
    assert "shape_->useStaticGeometry();" in update_dg
    assert "disableItems(list);" in override
    assert "if (!shape_->hasValidGeometry())" in override


def test_static_geometry_path_keeps_queue_streams_unchanged():
    header = SHAPE_HEADER.read_text(encoding="utf-8")
    source = SHAPE_SOURCE.read_text(encoding="utf-8")

    geometry_start = source.index("bool MmdRenderShape::updateEvaluatedMesh")
    geometry_update = source[geometry_start : source.index(
        "bool MmdRenderShape::updateMaterialAlpha", geometry_start
    )]

    assert "geometry_.queueInputs" not in geometry_update
    assert "geometry_.renderQueue" not in geometry_update
    assert "geometry_.queueGeometry" not in geometry_update
    assert "std::vector<float> uvs;" in header
    assert "std::vector<mmd::MmdRenderQueueEntry> renderQueue;" in header
    assert "void MmdRenderShape::useStaticGeometry()" in source
    assert "geometry_.positions.swap(restoredPositions);" in source
    assert "geometry_.normals.swap(restoredNormals);" in source
