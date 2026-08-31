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


def test_proxy_readiness_is_a_nonpersistent_dg_output():
    header = SHAPE_HEADER.read_text(encoding="utf-8")
    source = SHAPE_SOURCE.read_text(encoding="utf-8")
    override = OVERRIDE_SOURCE.read_text(encoding="utf-8")

    assert "static MObject aSourceVisibility;" in header
    assert "static MObject aProxyReady;" in header
    assert '"proxyReady", "pr", MFnNumericData::kBoolean, false' in source
    assert '"sourceVisibility", "sv", MFnNumericData::kBoolean, true' in source
    assert "numericAttribute.setStorable(false);" in source
    assert "numericAttribute.setWritable(false);" in source
    assert "numericAttribute.setHidden(true);" in source
    assert "attributeAffects(aProxyReady, aSourceVisibility);" in source
    assert "output.setBool(!proxyReady);" in source
    assert "must be deleted before plugin unload" in source

    readiness_helper = source[source.index("bool MmdRenderShape::setProxyReady") :]
    assert "MPlug readiness(thisMObject(), aProxyReady);" in readiness_helper
    assert "readiness.setBool(nextReady)" in readiness_helper
    assert "sourceVisibility.setBool" not in readiness_helper

    cleanup = override[override.index("void MmdRenderGeometryOverride::cleanUp()") :]
    assert "setProxyReady(false)" not in cleanup

    plugin_main = (ROOT / "cpp" / "src" / "pluginMain.cpp").read_text(encoding="utf-8")
    unload = plugin_main[plugin_main.index("MStatus uninitializePlugin") :]
    assert unload.index("MmdRenderShape::prepareForPluginUnload()") < unload.index(
        'plugin.deregisterCommand("mmdVmdBatchSample")'
    )


def test_proxy_ready_is_published_only_after_committed_geometry():
    override = OVERRIDE_SOURCE.read_text(encoding="utf-8")

    populate = override[override.index("void MmdRenderGeometryOverride::populateGeometry") :]
    assert "buffer->commit(destination);" in populate
    assert "indexBuffer->commit(destination);" in populate
    assert "shape_->recordGeometryWitness(" in populate
    assert "shape_->recordRenderItemWitness(geometry.renderQueue);" in populate
    assert "shape_->setProxyReady(true)" in populate
    assert populate.index("indexBuffer->commit(destination);") < populate.index(
        "shape_->setProxyReady(true)"
    )
    assert "shape_->clearRenderItemWitness();" in populate


def test_wireframe_item_uses_mesh_object_selection_without_component_mapping():
    shape = SHAPE_SOURCE.read_text(encoding="utf-8")
    override = OVERRIDE_SOURCE.read_text(encoding="utf-8")

    assert '"_wire"' in override
    assert "MGeometry::kWireframe" in override
    assert "MShaderManager::k3dSolidShader" in override
    assert "wireItem->setSelectionMask(" in override
    assert "MSelectionMask::kSelectMeshes" in override
    assert "No component" in override
    assert "renderItemName(candidate, queueIndex, false, true)" in override
    assert "MSelectionMask(MSelectionMask::kSelectMeshes)" in shape


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
