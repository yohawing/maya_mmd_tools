"""Contract checks for the shared Ordered shadow shader."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CPP = ROOT / "cpp" / "src"


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
    source = (CPP / "MmdNativeMaterial.cpp").read_text(encoding="utf-8")

    assert 'shader->setParameter("NativeCasterProbe", 0)' in source
    assert 'shader->setParameter("NativeCasterHardShadow", 0)' in source
    assert '"NativeCasterShadowBias"' in source


def test_native_material_binding_has_no_dead_shape_diagnostic_dependency() -> None:
    header = (CPP / "MmdNativeMaterial.h").read_text(encoding="utf-8")
    source = (CPP / "MmdNativeMaterial.cpp").read_text(encoding="utf-8")
    ordered = (CPP / "MmdOrderedRenderOverride.cpp").read_text(encoding="utf-8")

    assert '#include "MmdRenderShape.h"' not in header
    assert "MaterialBindingDiagnostic" not in header
    assert "MaterialBindingDiagnostic" not in source
    call = ordered[ordered.index("mmd::bindNativeMaterialParameters(") :]
    call = call[: call.index(");")]
    assert "nullptr" not in call


def test_shadow_targets_outlive_borrowing_shaders():
    source = (CPP / "MmdRenderOverride.cpp").read_text(encoding="utf-8")
    ordered = (CPP / "MmdOrderedRenderOverride.cpp").read_text(encoding="utf-8")
    release = ordered[ordered.index("beginReceiverShaderRetire(") :]
    assert release.index("releaseShader(") < release.index("finishReceiverShaderRetire(")
    targets = source[source.index("bool MmdShadowResources::releaseTargets()") :]
    assert targets.index("if (!shutdownReady())") < targets.index("releaseRenderTarget(")
    assert "gReceiverShaders.empty() && gRetiringReceiverShaders.empty()" in source
