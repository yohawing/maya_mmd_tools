"""Contracts for PMX material self-shadow caster flag propagation."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CPP = ROOT / "cpp" / "src"


def test_fast_load_reads_only_self_shadow_map_for_caster_eligibility() -> None:
    source = (CPP / "mmdFastLoad.cpp").read_text(encoding="utf-8")

    helper = source[source.index("bool materialSelfShadowMap(") :]
    helper = helper[: helper.index("\n}\n")]
    assert 'value("selfShadowMap", false)' in helper
    assert 'value("selfShadow",' not in helper
    assert "input.selfShadowMap = materialSelfShadowMap(material);" in source


def test_queue_and_structured_diagnostics_preserve_caster_eligibility() -> None:
    queue_header = (CPP / "MmdRenderQueue.h").read_text(encoding="utf-8")
    shape_header = (CPP / "MmdRenderShape.h").read_text(encoding="utf-8")
    shape_source = (CPP / "MmdRenderShape.cpp").read_text(encoding="utf-8")
    override_source = (CPP / "MmdRenderGeometryOverride.cpp").read_text(
        encoding="utf-8"
    )
    smoke = (CPP / "MmdRenderQueueSmoke.cpp").read_text(encoding="utf-8")

    assert "bool selfShadowMap = false;" in queue_header
    assert "bool selfShadowMap = false;" in shape_header
    assert (
        "diagnostic.selfShadowMap = queueGeometry.material.selfShadowMap;"
        in override_source
    )
    assert 'appendJsonBool(stream, "selfShadowMap", diagnostic.selfShadowMap' in shape_source
    assert "materialInput.selfShadowMap = true;" in smoke
    assert "secondMaterialInput.selfShadowMap = false;" in smoke
    assert "firstMaterial->selfShadowMap" in smoke
    assert "!secondMaterial->selfShadowMap" in smoke


def test_geometry_override_filters_only_native_caster_scene_items() -> None:
    geometry = (CPP / "MmdRenderGeometryOverride.cpp").read_text(encoding="utf-8")
    scene = (CPP / "MmdRenderOverride.cpp").read_text(encoding="utf-8")

    assert "queueGeometry.material.selfShadowMap &&" in geometry
    assert "!effectiveTransparent" in geometry
    assert "MRenderItem::NonMaterialSceneItem" in geometry
    assert 'diagnostic.casterExclusionReason' in geometry
    assert "kRenderOpaqueShadedItems" in scene
    assert "casterDrawCallback" in scene
    assert "drawnRenderItems" in scene
    assert "drawnRenderItemDagPaths" in scene
    assert "drawnRenderItemCastsShadows" in scene
