"""Source-level contract tests for evaluated RenderOverride geometry."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SHAPE_HEADER = ROOT / "cpp" / "src" / "MmdRenderShape.h"
SHAPE_SOURCE = ROOT / "cpp" / "src" / "MmdRenderShape.cpp"
OVERRIDE_SOURCE = ROOT / "cpp" / "src" / "MmdOrderedRenderOverride.cpp"


def test_failed_geometry_and_publication_remain_retryable():
    source = SHAPE_SOURCE.read_text(encoding="utf-8")
    reject = source[source.index("auto reject = [this]") :]
    reject = reject[: reject.index("return false;")]
    assert "meshInputDirty_ = true;" in reject
    override = OVERRIDE_SOURCE.read_text(encoding="utf-8")
    assert "!shape->hasValidGeometry()" in override


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


def test_legacy_proxy_visibility_is_a_nonpersistent_compatibility_output():
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

    assert "setProxyReady(" not in header
    assert "setProxyReady(" not in source
    assert "setProxyReady(" not in override

    plugin_main = (ROOT / "cpp" / "src" / "pluginMain.cpp").read_text(encoding="utf-8")
    unload = plugin_main[plugin_main.index("MStatus uninitializePlugin") :]
    assert unload.index("MmdRenderShape::prepareForPluginUnload()") < unload.index(
        'plugin.deregisterCommand("mmdVmdBatchSample")'
    )


def test_render_shape_reports_transient_fallback_reason():
    header = SHAPE_HEADER.read_text(encoding="utf-8")
    source = SHAPE_SOURCE.read_text(encoding="utf-8")

    assert "bool recordRenderFallbackReason(const std::string& reason);" in header
    assert "std::string renderFallbackReason_;" in header
    assert 'return "failed reason=" + renderFallbackReason_;' in source
    assert 'jsonEscape(status) << ",\\\"fallbackReason\\\":"' in source
    assert "renderFallbackReason_.clear();" in source

    failure = source[
        source.index("bool MmdRenderShape::recordRenderFallbackReason") : source.index(
            "std::string MmdRenderShape::renderItemWitness"
        )
    ]
    assert "clearRenderItemWitness" not in failure
    assert "const bool changed = renderFallbackReason_ != reason;" in failure
    assert "renderFallbackReason_ = reason;" in failure
    assert "return changed;" in failure


def test_unpublished_render_witness_state_is_removed_but_json_command_remains():
    header = SHAPE_HEADER.read_text(encoding="utf-8")
    source = SHAPE_SOURCE.read_text(encoding="utf-8")
    retired = (
        "hasPassGeometry",
        "updateMainTextureAvailability",
        "recordRenderItemWitness",
        "recordGeometryWitness",
        "recordMaterialBindingDiagnostic",
        "MaterialBindingDiagnostic",
        "renderItemWitnessValid_",
        "renderItemWitnessEntries_",
        "geometryWitnessValid_",
        "geometryWitnessVertexCount_",
        "geometryWitnessIndexCount_",
        "geometryWitnessDescriptorSummary_",
        "bufferUploadCount_",
        "materialBindingDiagnostics_",
    )
    for token in retired:
        assert token not in header
        assert token not in source

    assert "clearRenderItemWitness" not in header
    assert "clearRenderItemWitness" not in source
    assert "std::string materialBindingDiagnosticsJson() const;" in header
    assert '<< ",\\\"geometryUpdates\\\":" << geometryUpdateCount_' in source
    assert '<< ",\\\"bufferUploads\\\":0"' in source
    assert '<< ",\\\"items\\\":[]}"' in source


def test_update_dg_reads_evaluated_mesh_and_fails_closed():
    shape = SHAPE_SOURCE.read_text(encoding="utf-8")
    override = OVERRIDE_SOURCE.read_text(encoding="utf-8")

    assert "bool MmdRenderShape::updateEvaluatedMesh" in shape
    assert "getPoints(points, MSpace::kObject)" in shape
    assert "getVertexNormals(true, normals, MSpace::kObject)" in shape
    assert "normalRepairRenderVertices" in shape
    assert "import-time static fallback" in shape
    assert "evaluatedNormalRepairWarningEmitted_" in shape
    assert "normal repair failed for source vertex" in shape
    assert "source mapping index exceeds input mesh vertex count" in shape
    assert "geometryValid_ = false;" in shape
    assert "recordRenderFallbackReason(reason);" in shape
    assert "staticPositions_" in shape
    assert "staticNormals_" in shape
    assert "geometry_.positions = std::move(nextPositions);" in shape
    assert "geometry_.normals = std::move(nextNormals);" in shape
    assert "static_cast<std::size_t>(points.length()) < expectedSourceVertexCount" in shape

    assert "shape->updateEvaluatedData();" in override
    shared_update = shape[shape.index("void MmdRenderShape::updateEvaluatedData()") :]
    assert "MmdRenderShape::aInputMesh" in shared_update
    assert "inputPlug.asMDataHandle" in shared_update
    assert "inputHandle.asMesh()" in shared_update
    assert "updateEvaluatedMesh(meshObject);" in shared_update
    assert "useStaticGeometry();" in shared_update
    assert "!shape->hasValidGeometry()" in override

    evaluated_update = shape[
        shape.index("bool MmdRenderShape::updateEvaluatedMesh") : shape.index(
            "void MmdRenderShape::useStaticGeometry"
        )
    ]
    assert "recordRenderFallbackReason(reason);" in evaluated_update
    assert "if (reasonChanged)" in evaluated_update
    assert "normalRepairCount > 0U" in evaluated_update
    assert "!evaluatedNormalRepairWarningEmitted_" in evaluated_update
    assert "evaluatedNormalRepairWarningEmitted_ = true;" in evaluated_update
    assert "staticNormals_" in evaluated_update
    assert "getVertexNormals(false" not in evaluated_update
    assert "getTriangles(" not in evaluated_update
    assert "input mesh contains a zero-length normal" not in evaluated_update
    assert "repairedNormals" in shape
    assert "staticNormalFallbacks" in shape


def test_ordered_witness_distinguishes_registered_from_selected_panel():
    override = OVERRIDE_SOURCE.read_text(encoding="utf-8")

    assert "bool isOrderedPanelSelected()" in override
    assert 'getPanel -type \\"modelPanel\\"' in override
    assert "rendererOverrideName" in override
    assert "if (!isOrderedPanelSelected())" in override
    assert '\\"state\\":\\"inactive\\"' in override


def test_normal_repair_warning_latches_until_geometry_is_rearmed():
    source = SHAPE_SOURCE.read_text(encoding="utf-8")

    evaluated_update = source[
        source.index("bool MmdRenderShape::updateEvaluatedMesh") : source.index(
            "void MmdRenderShape::useStaticGeometry"
        )
    ]
    warning_guard = evaluated_update[
        evaluated_update.index("if (normalRepairCount > 0U") : evaluated_update.index(
            "// Keep the latest counts in the diagnostic witness"
        )
    ]
    assert warning_guard.index("!evaluatedNormalRepairWarningEmitted_") < warning_guard.index(
        "MGlobal::displayWarning"
    )
    assert warning_guard.index("MGlobal::displayWarning") < warning_guard.index(
        "evaluatedNormalRepairWarningEmitted_ = true;"
    )
    assert evaluated_update.index(
        "evaluatedNormalRepairWarningEmitted_ = true;"
    ) < evaluated_update.index("evaluatedNormalRepairCount_ = normalRepairCount;")
    assert evaluated_update.index(
        "evaluatedNormalRepairCount_ = normalRepairCount;"
    ) < evaluated_update.index("evaluatedNormalStaticFallbackCount_ = staticFallbackCount;")
    assert evaluated_update.count(
        "evaluatedNormalRepairWarningEmitted_ = false;"
    ) == 1

    rebuilt_geometry = source[
        source.index("bool MmdRenderShape::setMaterialSplitGeometry") : source.index(
            "bool MmdRenderShape::updateEvaluatedMesh"
        )
    ]
    assert "evaluatedNormalRepairWarningEmitted_ = false;" in rebuilt_geometry

    reject = evaluated_update[
        evaluated_update.index("auto reject = [this]") : evaluated_update.index(
            "return false;"
        )
    ]
    assert "evaluatedNormalRepairWarningEmitted_ = false;" in reject

    restored_geometry = source[
        source.index("void MmdRenderShape::useStaticGeometry") : source.index(
            "bool MmdRenderShape::hasValidGeometry"
        )
    ]
    assert "evaluatedNormalRepairWarningEmitted_ = false;" in restored_geometry
    assert source.count("evaluatedNormalRepairWarningEmitted_ = false;") == 3


def test_static_geometry_path_keeps_queue_streams_unchanged():
    header = SHAPE_HEADER.read_text(encoding="utf-8")
    source = SHAPE_SOURCE.read_text(encoding="utf-8")

    geometry_start = source.index("bool MmdRenderShape::updateEvaluatedMesh")
    geometry_update = source[geometry_start : source.index(
        "bool MmdRenderShape::updateEvaluatedMaterialAlpha", geometry_start
    )]

    assert "geometry_.queueInputs" not in geometry_update
    assert "geometry_.renderQueue" not in geometry_update
    assert "geometry_.queueGeometry" not in geometry_update
    assert "std::vector<float> uvs;" in header
    assert "std::vector<mmd::MmdRenderQueueEntry> renderQueue;" in header
    assert "void MmdRenderShape::useStaticGeometry()" in source
    assert "geometry_.positions.swap(restoredPositions);" in source
    assert "geometry_.normals.swap(restoredNormals);" in source


def test_preflight_cache_tracks_structure_without_skipping_draw_checks():
    source = OVERRIDE_SOURCE.read_text(encoding="utf-8")

    assert "samePreflightMaterial" in source
    assert "samePreflightPlans" in source
    assert "bodyPreflightRequired" in source
    assert "casterPreflightRequired" in source
    assert "bodyPreflightPlans_ = plans;" in source
    assert "casterPreflightPlans_ = plans;" in source
    assert source.index("!preflight(plans, drawContext)") < source.index(
        "bodyPreflightPlans_ = plans;"
    )
    assert source.index("!preflightCasters(plans, drawContext)") < source.index(
        "casterPreflightPlans_ = plans;"
    )

    reset = source[source.index("void resetFrame()") : source.index("MStatus execute(")]
    assert "invalidatePreflightCache" not in reset
    release = source[
        source.index("bool releaseResourcesForUnload()") : source.index(
            "void releaseResources()"
        )
    ]
    assert "invalidatePreflightCache();" in release

    actual_draw = source[source.index("MStatus executePass") : source.index(
        "bool requiresResetDeviceStates"
    )]
    assert "setFrameParameters(shader, drawContext, plan.world)" in actual_draw
    assert "shader->updateParameters(drawContext)" in actual_draw
    assert "shader->activatePass(drawContext, 0U)" in actual_draw
    caster_draw = source[source.index("bool renderCasters") : source.index(
        "bool prepareFrame"
    )]
    assert "setCasterParameters(shader, plan)" in caster_draw
    assert "shader->updateParameters(drawContext)" in caster_draw
    assert "shader->activatePass(drawContext, 0U)" in caster_draw


def test_ordered_fallback_is_panel_local_and_latches_after_failed_retry():
    header = (ROOT / "cpp" / "src" / "MmdOrderedRenderOverride.h").read_text(
        encoding="utf-8"
    )
    source = OVERRIDE_SOURCE.read_text(encoding="utf-8")

    assert "std::map<std::string, FallbackState> fallbackStates_;" in header
    assert "bool retryPending = false;" in header
    assert "bool rawRetryActive = false;" in header
    assert "bool latched = false;" in header
    assert "activeDestination_ = destination.asChar();" in source
    fallback_setup = source[source.index("if (fallback.requested) {") :]
    fallback_setup = fallback_setup[: fallback_setup.index("MHWRender::MRenderer* renderer")]
    assert "fallback.frameActive = true;" in fallback_setup
    cleanup = source[source.index("MStatus MmdOrderedRenderOverride::cleanup()") :]
    cleanup = cleanup[: cleanup.index("const MString& MmdOrderedRenderOverride::overrideName")]
    assert "if (fallback.frameActive)" in cleanup
    assert "if (!fallback.latched)" in cleanup
    assert "fallback.retryPending = true;" in cleanup
    assert "fallback.rawRetryActive && !fallback.requested" in cleanup
    request = source[source.index("void MmdOrderedRenderOverride::requestFallback") :]
    request = request[: request.index("void MmdOrderedRenderOverride::clearFallback")]
    assert "if (fallback.retryPending)" in request
    assert "fallback.latched = true;" in request
    assert "fallback.frameActive = currentFrameUsesStandard;" in request
    raw_success = source[source.index("FallbackState& completed = activeFallbackState();") :]
    raw_success = raw_success[: raw_success.index("MStatus MmdOrderedRenderOverride::cleanup()")]
    assert "if (completed.retryPending)" in raw_success
    assert "completed.rawRetryActive = true;" in raw_success
    assert "fallbackStates_.erase(activeDestination_);" in source
