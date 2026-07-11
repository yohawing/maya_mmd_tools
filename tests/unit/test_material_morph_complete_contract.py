"""Complete PMX material-morph shader contract guards."""

from pathlib import Path
import re

from mmd_tools.converters.material_shader_parameters import iter_hardware_shader_values


ROOT = Path(__file__).resolve().parents[2]


def _main_alpha_contract_holds(source):
    factor = "texColor = texColor * MainTextureMultiply + MainTextureAdd"
    opacity = "texColor.a * DiffuseColorA * Opacity"
    factor_position = source.find(factor)
    opacity_position = source.find(opacity)
    neutral_opacity = bool(
        re.search(r"uniform\s+float\s+Opacity\s*=\s*1\.0\s*;", source)
        or re.search(r"float\s+Opacity\s*:\s*OPACITY\s*<.*?>\s*=\s*1\.0f\s*;", source, re.S)
    )
    return 0 <= factor_position < opacity_position and neutral_opacity


def test_shared_mapping_exposes_all_texture_factor_uniforms():
    values = {
        "texture_multiply": (1.0, 1.0, 1.0, 1.0),
        "texture_add": (0.0, 0.0, 0.0, 0.0),
        "sphere_texture_multiply": (1.0, 1.0, 1.0, 1.0),
        "sphere_texture_add": (0.0, 0.0, 0.0, 0.0),
        "toon_texture_multiply": (1.0, 1.0, 1.0, 1.0),
        "toon_texture_add": (0.0, 0.0, 0.0, 0.0),
    }
    expected = {
        "MainTextureMultiply", "MainTextureAdd",
        "SphereTextureMultiply", "SphereTextureAdd",
        "ToonTextureMultiply", "ToonTextureAdd",
    }
    for shader_type in ("dx11Shader", "GLSLShader"):
        actual = {binding.attribute for binding, _ in iter_hardware_shader_values(values, shader_type)}
        assert actual == expected


def test_both_shader_sources_apply_rgba_factors_before_final_opacity():
    sources = {
        "dx11": (ROOT / "mmd_tools/shaders/MMDShader.fx").read_text(encoding="utf-8"),
        "glsl": (ROOT / "mmd_tools/shaders/MMDShader.ogsfx").read_text(encoding="utf-8"),
    }
    for source in sources.values():
        for prefix in ("Main", "Sphere", "Toon"):
            assert f"{prefix}TextureMultiply" in source
            assert f"{prefix}TextureAdd" in source
        assert "texColor.a * DiffuseColorA * Opacity" in source
        assert "texColor = texColor * MainTextureMultiply + MainTextureAdd" in source
        assert "sphereSample * SphereTextureMultiply + SphereTextureAdd" in source
        assert "toonSample * ToonTextureMultiply + ToonTextureAdd" in source
        assert _main_alpha_contract_holds(source)


def test_contract_guard_rejects_unfactorized_or_late_main_alpha():
    source = (ROOT / "mmd_tools/shaders/MMDShader.ogsfx").read_text(encoding="utf-8")
    assert not _main_alpha_contract_holds(
        source.replace("texColor = texColor * MainTextureMultiply + MainTextureAdd;", "")
    )
    factor = "texColor = texColor * MainTextureMultiply + MainTextureAdd;"
    mutated = source.replace(factor, "").replace(
        "float opacity = texColor.a * DiffuseColorA * Opacity;",
        "float opacity = texColor.a * DiffuseColorA * Opacity;\n" + factor,
    )
    assert not _main_alpha_contract_holds(mutated)
    assert not _main_alpha_contract_holds(
        source.replace("uniform float Opacity = 1.0;", "uniform float Opacity = 0.4;")
    )
