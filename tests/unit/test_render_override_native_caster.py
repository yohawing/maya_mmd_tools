"""Contract checks for the bounded native caster capability spike."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CPP = ROOT / "cpp" / "src"


def test_native_caster_sources_are_registered() -> None:
    header = (CPP / "MmdRenderOverride.h").read_text(encoding="utf-8")
    source = (CPP / "MmdRenderOverride.cpp").read_text(encoding="utf-8")
    plugin = (CPP / "pluginMain.cpp").read_text(encoding="utf-8")
    cmake = (CPP / "CMakeLists.txt").read_text(encoding="utf-8")

    assert "class MmdNativeCasterRenderOverride" in header
    assert "MmdRenderOverride.cpp" in cmake
    assert "sMmdNativeCasterOverride = new MmdNativeCasterRenderOverride()" in plugin
    assert "registerOverride(sMmdNativeCasterOverride)" in plugin
    assert "delete sMmdNativeCasterOverride" in plugin
    assert "!MmdNativeCasterRenderOverride::shutdownReady()" in plugin
    assert 'registerCommand(\n            "mmdNativeCasterWitness"' in plugin
    assert "MRenderOverride::setup" not in source
    assert "MMDNativeCaster" in source
    assert "MRenderTargetManager" in source
    assert "registerReceiverShader" in source
    assert "beginReceiverShaderRetire" in source
    assert "finishReceiverShaderRetire" in source
    assert "shutdownReady" in source
    assert "gReceiverPins" in source
    assert "gReceiverCv" in source
    assert "gOverrideSetup" in source
    assert "MRenderTargetAssignment" in source
    assert "receiverAssignmentFailure" in source
    assert "receiverLiveAssignmentOwners" in source
    assert "receiverTargetsRetained" in source
    assert "MRenderTargetAssignment assignment{nullptr}" not in source
    assert "NativeCasterHardShadow" in source
    assert "NativeCasterShadowBias" in source
    assert 'addFlag("-hc", "-hardShadowCompare"' in source
    assert 'addFlag("-hb", "-hardShadowBias"' in source
    assert "failClosedHardShadowDisableAttempted" in source
    assert "disableHardShadowForFailClosed" in source
    assert "mutually exclusive" in source
    assert "requestedReceiverProbe && requestedHardShadow" in source


def test_geometry_override_registers_only_enabled_body_receivers() -> None:
    source = (CPP / "MmdRenderGeometryOverride.cpp").read_text(encoding="utf-8")

    receiver_start = source.index("const bool receiverEligible =")
    receiver_end = source.index("item->setTreatAsTransparent", receiver_start)
    receiver_block = source[receiver_start:receiver_end]
    assert "!outline && queueGeometry.material.selfShadow" in receiver_block
    assert "if (receiverEligible)" in receiver_block
    assert "registerReceiverShader" in receiver_block
    assert "if (!outline)" not in receiver_block

    cache_start = source.index("std::string nativeShaderCacheKey(")
    cache_end = source.index("std::string nativeSharedToonPath", cache_start)
    cache_block = source[cache_start:cache_end]
    assert "geometry.material.materialIndex" in cache_block

    destructor_start = source.index("MmdRenderGeometryOverride::~")
    destructor_end = source.index("MHWRender::DrawAPI", destructor_start)
    destructor = source[destructor_start:destructor_end]
    release_index = destructor.index("shaderManager->releaseShader")
    assert destructor.index("beginReceiverShaderRetire") < release_index
    assert destructor.index("finishReceiverShaderRetire") > release_index


def test_native_caster_uses_fixed_targets_and_occupancy_witness() -> None:
    """Validate target/depth contracts from the structured probe report."""
    from tools.render_override_native_caster_e2e import (
        _caster_depth_distribution_passes,
        _caster_target_witness_passes,
    )

    witness = {
        "colorTargetAcquired": True,
        "colorTarget": {
            "width": 2048,
            "height": 2048,
            "format": 41,
            "name": "__mmdNativeCasterColorTarget__",
        },
        "depthTargetAcquired": True,
        "depthTarget": {
            "width": 2048,
            "height": 2048,
            "format": 2,
            "name": "__mmdNativeCasterDepthTarget__",
        },
        "writtenDepthFinite": True,
        "writtenDepthInRange": True,
        "writtenOutOfRangeSamples": 0,
        "writtenMin": 0.2,
        "writtenMax": 0.8,
    }

    assert _caster_target_witness_passes(witness, "color")
    assert _caster_target_witness_passes(witness, "depth")
    assert _caster_depth_distribution_passes(witness)

    witness["depthTarget"]["format"] = 41
    witness["writtenDepthInRange"] = False
    assert not _caster_target_witness_passes(witness, "depth")
    assert not _caster_depth_distribution_passes(witness)


def test_native_caster_shader_stays_out_of_product_shader() -> None:
    native_shader = (ROOT / "mmd_tools" / "shaders" / "MMDNativeShader.fx").read_text(
        encoding="utf-8"
    )
    product_shader = (ROOT / "mmd_tools" / "shaders" / "MMDShader.fx").read_text(
        encoding="utf-8"
    )

    assert "technique11 MMDNativeCaster" in native_shader
    assert "NativeCasterDepthTexture" in native_shader
    assert "NativeCasterDepthTexture" not in product_shader


def test_native_material_initializes_diagnostic_flags_off() -> None:
    source = (CPP / "MmdRenderGeometryOverride.cpp").read_text(encoding="utf-8")

    assert 'shader->setParameter("NativeCasterProbe", 0)' in source
    assert 'shader->setParameter("NativeCasterHardShadow", 0)' in source
    assert '"NativeCasterShadowBias"' in source


def test_native_caster_e2e_records_negative_control() -> None:
    runner = (ROOT / "tools" / "render_override_native_caster_e2e.py").read_text(
        encoding="utf-8"
    )

    assert 'modelEditor -edit -rnm "vp2Renderer" -rom' in runner
    assert '_set_panel_override(mel, current, "mmdNativeCaster")' in runner
    assert '_set_panel_override(mel, current, "")' in runner
    assert '"activeWitness"' in runner
    assert '"disabledWitness"' in runner
    assert "pluginLoadedAfterUnload" in runner
    assert "standardPresentDiff" in runner
    assert "scenePixelSha256" in runner
    assert "depthBias" in runner
    assert "depthBiasAba" in runner
    assert "writtenFootprintHash" in runner
    assert "receiverProbe" in runner
    assert "receiverProbeAba" in runner
    assert "lightRotationAba" in runner
    assert "cameraInvariant" in runner
    assert "dagTransformAba" in runner
    assert "missingLightFailClosed" in runner
    assert "receiverTargetsRetained" in runner
    assert "postResetWitness" in runner
    assert "activeSceneUnloadRejected" in runner
    assert "renderItemWitnesses" in runner
    assert "actualCasterItemsExact" in runner
    assert "casterItemCategoriesPresent" in runner
    assert "--control-model" in runner
    assert 'report.get("pluginUnloadError") is None' in runner


def test_depth_bias_aba_gate_checks_bias_and_footprint() -> None:
    from tools.render_override_native_caster_e2e import _depth_bias_aba_passes

    baseline = {
        "depthBias": 0.35,
        "writtenDepthFinite": True,
        "writtenDepthInRange": True,
        "writtenOutOfRangeSamples": 0,
        "writtenSamples": 4,
        "writtenFootprintHash": "0xsame",
        "writtenMin": 0.20,
        "writtenMax": 0.40,
        "writtenMean": 0.30,
    }
    control = {
        **baseline,
        "depthBias": 0.55,
        "writtenMin": 0.40,
        "writtenMax": 0.60,
        "writtenMean": 0.50,
    }
    restored = {**baseline}

    assert _depth_bias_aba_passes(baseline, control, restored)
    control["depthBias"] = 0.50
    assert not _depth_bias_aba_passes(baseline, control, restored)
    control.update(
        depthBias=0.55,
        writtenMin=0.36,
        writtenMax=0.56,
        writtenMean=0.46,
    )
    assert not _depth_bias_aba_passes(baseline, control, restored)


def test_visible_shape_mask_rejects_identical_frames() -> None:
    from tools.render_override_native_caster_e2e import _visible_shape_mask

    frame = bytes([255, 255, 255, 255] * 4)
    result = _visible_shape_mask(frame, frame, 2, 2)

    assert not result["pass"]
    assert result["rawPixels"] == 0
    assert result["pixels"] == 0
