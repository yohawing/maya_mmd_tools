"""Complete PMX material-morph shader contract guards."""

from pathlib import Path
import re
from types import SimpleNamespace

from mmd_tools.converters.material_shader_parameters import (
    hardware_morph_route_for_uniform,
    hardware_morph_routes,
    iter_hardware_shader_values,
    material_base_parameter_values,
)


ROOT = Path(__file__).resolve().parents[2]


def _shader_uniform_values(material, shader_type):
    values = material_base_parameter_values(material)
    return {
        binding.attribute: value
        for binding, value in iter_hardware_shader_values(values, shader_type)
        if binding.attribute in {"DiffuseColorA", "Opacity"}
    }


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


def test_route_contract_owns_uniform_base_and_output_mappings():
    common_expected = {
        "DiffuseColorRGB": ("diffuse_rgb", "baseDiffuse", "outputDiffuse", 3),
        "DiffuseColorA": ("diffuse_alpha", "baseDiffuseA", "outputDiffuseAlpha", 1),
        "SpecularColor": ("specular", "baseSpecular", "outputSpecular", 3),
        "Shininess": (
            "specular_power",
            "baseSpecularCoefficient",
            "outputSpecularCoefficient",
            1,
        ),
        "AmbientColor": ("ambient", "baseAmbient", "outputAmbient", 3),
        "EdgeSize": ("edge_size", "baseEdgeSize", "outputEdgeSize", 1),
    }
    for shader_type in ("dx11Shader", "GLSLShader"):
        routes = hardware_morph_routes(shader_type)
        assert len({route.uniform for route in routes}) == len(routes)
        for uniform, expected in common_expected.items():
            route = hardware_morph_route_for_uniform(uniform, shader_type)
            assert route is not None
            assert (
                route.semantic,
                route.evaluator_base,
                route.evaluator_output,
                route.size,
            ) == expected

    assert hardware_morph_route_for_uniform("EdgeColorRGB", "dx11Shader") is not None
    assert hardware_morph_route_for_uniform("EdgeColorA", "dx11Shader") is not None
    assert hardware_morph_route_for_uniform("EdgeColor", "GLSLShader") is not None


def test_dx11_three_component_edge_color_defaults_alpha_to_one():
    actual = {
        binding.attribute: value
        for binding, value in iter_hardware_shader_values(
            {"edge_color": (0.1, 0.2, 0.3)}, "dx11Shader"
        )
    }
    assert actual == {
        "EdgeColorRGB": [0.1, 0.2, 0.3],
        "EdgeColorA": 1.0,
    }


def test_hardware_alpha_contract_keeps_opacity_neutral_for_both_backends():
    """PMX alpha is bound once while texture alpha remains multiplicative."""
    for pmx_alpha in (0.0, 0.58, 1.0):
        material = SimpleNamespace(diffuse=(0.8, 0.7, 0.6, pmx_alpha))
        for shader_type in ("dx11Shader", "GLSLShader"):
            uniforms = _shader_uniform_values(material, shader_type)
            assert uniforms["DiffuseColorA"] == pmx_alpha
            assert uniforms["Opacity"] == 1.0
            for texture_alpha in (0.0, 0.25, 0.58, 1.0):
                expected = texture_alpha * pmx_alpha
                actual = texture_alpha * uniforms["DiffuseColorA"] * uniforms["Opacity"]
                assert abs(actual - expected) <= 1.0e-12


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


def test_sphere_mapping_uses_half_range_view_normal_projection_in_both_backends():
    """Sphere UVs use the normative x/y 0.5 projection coefficients."""
    sources = {
        "dx11": (ROOT / "mmd_tools/shaders/MMDShader.fx").read_text(encoding="utf-8"),
        "glsl": (ROOT / "mmd_tools/shaders/MMDShader.ogsfx").read_text(encoding="utf-8"),
    }
    assert "sphereUV.x = sphereNormal.x * 0.5 + 0.5;" in sources["dx11"]
    assert "sphereUV.y = sphereNormal.y * -0.5 + 0.5;" in sources["dx11"]
    assert (
        "vec2 sphereUV = vec2(sphereNormal.x * 0.5 + 0.5, sphereNormal.y * -0.5 + 0.5);"
        in sources["glsl"]
    )
    for source in sources.values():
        assert "sphereNormal" in source
        assert "* 0.35" not in source


def test_specular_power_gate_matches_mmd_contract_in_both_backends():
    """Non-positive PMX specular power produces no highlight."""
    sources = {
        "dx11": (ROOT / "mmd_tools/shaders/MMDShader.fx").read_text(encoding="utf-8"),
        "glsl": (ROOT / "mmd_tools/shaders/MMDShader.ogsfx").read_text(encoding="utf-8"),
    }
    for source in sources.values():
        assert "if (Shininess > 0.0)" in source
        assert "max(Shininess, 1.0)" not in source
        assert "step(0.0, NdotL)" not in source
        assert "step(0.0, ndotl)" not in source
    assert "float UIMin = 0.0;" in sources["dx11"]


def test_toon_coordinate_matches_full_shader_half_lambert_in_both_backends():
    """Both backends express FullShader's V=0.5-0.5*N.L directly."""
    dx11 = (ROOT / "mmd_tools/shaders/MMDShader.fx").read_text(encoding="utf-8")
    glsl = (ROOT / "mmd_tools/shaders/MMDShader.ogsfx").read_text(encoding="utf-8")
    assert "float toonV = saturate(0.5 - NdotL * 0.5);" in dx11
    assert "float2(0.5, toonV)" in dx11
    assert "float toonV = clamp(0.5 - ndotl * 0.5, 0.0, 1.0);" in glsl
    assert "vec2(0.5, toonV)" in glsl
    for source in (dx11, glsl):
        assert "1.0 - rampCoord" not in source


def test_surface_composition_matches_full_shader_sphere_toon_specular_order():
    """Sphere affects the surface before toon while specular remains untinted."""
    sources = {
        "dx11": (ROOT / "mmd_tools/shaders/MMDShader.fx").read_text(encoding="utf-8"),
        "glsl": (ROOT / "mmd_tools/shaders/MMDShader.ogsfx").read_text(encoding="utf-8"),
    }
    for source in sources.values():
        sphere_multiply_position = source.index("surfaceColor *= sphereColor")
        sphere_add_position = source.index("surfaceColor += sphereColor")
        toon_position = source.index("surfaceColor *= toonColor")
        specular_position = source.index("diffuse + specular")
        assert sphere_multiply_position < toon_position < specular_position
        assert sphere_add_position < toon_position < specular_position
        assert "lighting *= sphereColor" not in source
        assert "litColor *= sphereColor" not in source


def test_dx11_effect_has_only_sidedness_techniques_with_optional_edge_output():
    """Transparency and no-edge variants do not multiply effect techniques."""
    source = (ROOT / "mmd_tools/shaders/MMDShader.fx").read_text(encoding="utf-8")
    assert re.findall(r"technique11\s+(\w+)", source) == [
        "MMDTechnique",
        "MMDTechniqueDoubleSided",
    ]
    assert "BlendEnable[0] = TRUE" not in source
    assert "int isTransparent = 1" not in source
    assert "clip(EdgeSize - 1.0e-5);" in source
    assert source.count("pass EdgePass") == 2
    assert "SetRasterizerState(CullFront)" in source
    assert "SetRasterizerState(CullNone)" in source
    assert "screenNormal / (safeScreenSize * 0.5) * EdgeSize * clipPos.w" in source
    assert "EdgeSize * 4.0" not in source


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
