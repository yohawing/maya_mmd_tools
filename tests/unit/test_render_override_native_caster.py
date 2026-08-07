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
    assert 'registerCommand(\n            "mmdNativeCasterWitness"' in plugin
    assert "MRenderOverride::setup" not in source
    assert "MMDNativeCaster" in source
    assert "MRenderTargetManager" in source


def test_native_caster_uses_fixed_targets_and_occupancy_witness() -> None:
    source = (CPP / "MmdRenderOverride.cpp").read_text(encoding="utf-8")
    shader = (ROOT / "mmd_tools" / "shaders" / "MMDShader.fx").read_text(
        encoding="utf-8"
    )

    assert "kTargetSize = 2048U" in (CPP / "MmdRenderOverride.h").read_text(
        encoding="utf-8"
    )
    assert "kR32_FLOAT" in source
    assert "kD32_FLOAT" in source
    assert "targetDescription(actualDescription)" in source
    assert "rawData(rowPitch, slicePitch)" in source
    assert "__mmdNativeCasterColorTarget__" in source
    assert "__mmdNativeCasterDepthTarget__" in source
    assert "kDirectX11" in source
    assert "kRenderShadedItems" in source
    assert "operationInsertedBeforeScene" in source
    assert "nonClearSamples" in source
    assert "writtenSamples" in source
    assert "writtenMean" in source
    assert "writtenFootprintHash" in source
    assert "matrixValidated" in source
    assert "depthBiasBound" in source
    assert 'addFlag("-db", "-depthBias"' in source
    assert "CasterLightViewProjection" in shader
    assert "row_major float4x4 CasterLightViewProjection" in shader
    assert "technique11 MMDNativeCaster" in shader
    assert "mul(worldPos, CasterLightViewProjection)" in shader
    assert "return input.position.z;" in shader
    assert ": SV_Depth" not in shader


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
