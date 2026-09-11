"""Contracts for the limited Maya VP2-native geometry profile runner."""

import json
from pathlib import Path

import pytest

from tools.render_override import vp2_native_geometry_profile as profile


def _config(tmp_path: Path) -> dict:
    model = tmp_path / "model.pmx"
    model.write_bytes(b"PMX ")
    return {
        "model": str(model),
        "output": str(tmp_path / "out"),
        "frames": 3,
        "warmup": 2,
        "repeats": 2,
        "evaluation": "parallel",
        "separateMeshesByMaterial": True,
        "shaderBackend": "dx11",
        "createMmdShaders": True,
        "playbackFrames": 0,
        "playbackWarmup": 10,
        "controlledDeformation": True,
        "animationProbeOffset": 10,
        "geometryRoute": "source-direct",
        "disableCache": False,
    }


def test_validate_config_requires_split_pmx_and_positive_samples(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert profile.validate_config(config) == config

    for key in ("frames", "warmup", "repeats"):
        invalid = dict(config, **{key: 0})
        with pytest.raises(ValueError, match=key):
            profile.validate_config(invalid)
    with pytest.raises(ValueError, match="separateMeshesByMaterial"):
        profile.validate_config(dict(config, separateMeshesByMaterial=False))
    wrong_model = tmp_path / "model.pmd"
    wrong_model.write_bytes(b"Pmd")
    with pytest.raises(ValueError, match="pmx"):
        profile.validate_config(dict(config, model=str(wrong_model)))


def test_validate_config_accepts_scene_only_with_existing_plugin(tmp_path: Path) -> None:
    config = _config(tmp_path)
    scene = tmp_path / "motion.ma"
    plugin = tmp_path / "mmd_tools_cpp.mll"
    scene.write_text("// Maya ASCII", encoding="ascii")
    plugin.write_bytes(b"dll")
    config.update(model=None, scene=str(scene), plugin=str(plugin), expectedMeshCount=48)
    config.update(playbackFrames=100, controlledDeformation=False)
    assert profile.validate_config(config)["scene"] == str(scene)
    with pytest.raises(ValueError, match="plugin"):
        profile.validate_config(dict(config, plugin=str(tmp_path / "missing.mll")))


def test_validate_config_limits_native_playback_to_scene(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with pytest.raises(ValueError, match="saved scene"):
        profile.validate_config(dict(config, playbackFrames=2))
    with pytest.raises(ValueError, match="playbackWarmup"):
        profile.validate_config(dict(config, playbackWarmup=-1))
    with pytest.raises(ValueError, match="animationProbeOffset"):
        profile.validate_config(dict(config, animationProbeOffset=0))
    with pytest.raises(ValueError, match="geometryRoute"):
        profile.validate_config(dict(config, geometryRoute="proxy"))
    with pytest.raises(ValueError, match="disableCache"):
        profile.validate_config(dict(config, disableCache="false"))


def test_p95_uses_observed_nearest_rank() -> None:
    assert profile._percentile95([1.0, 20.0, 3.0, 4.0]) == 20.0
    with pytest.raises(ValueError, match="at least one"):
        profile._percentile95([])


def test_sixteen_ms_acceptance_exposes_each_sub_gate() -> None:
    accepted = profile.sixteen_ms_acceptance(
        [{"meanMs": 15.4}, {"meanMs": 16.0}, {"meanMs": 15.8}],
        {"p95Ms": 16.67, "missingNotificationCount": 0},
        eligible=True,
    )
    assert accepted == {
        "repeatMeansAtMost16Ms": True,
        "pooledP95AtMost16_67Ms": True,
        "notificationCompleteness": True,
        "sixteenMsPassed": True,
    }
    rejected = profile.sixteen_ms_acceptance(
        [{"meanMs": 16.01}],
        {"p95Ms": 16.68, "missingNotificationCount": 1},
        eligible=True,
    )
    assert rejected["sixteenMsPassed"] is False
    assert not any(rejected.values())


def test_configure_cache_evaluator_records_disabled_acceptance() -> None:
    class FakeCmds:
        enabled = True

        def evaluator(self, *, name: str, enable=None, query=False):
            assert name == "cache"
            if query:
                return [self.enabled]
            self.enabled = bool(enable)

    report = {}
    profile._configure_cache_evaluator(FakeCmds(), {"disableCache": True}, report)
    assert report["cacheEvaluatorEnabled"] is False
    assert report["acceptance"]["sixteenMsEligible"] is True


def test_normalize_playback_callbacks_requires_n_plus_one_after_warmup() -> None:
    evidence = profile.normalize_playback_callbacks(
        [[0.0, 10], [0.01, 10], [0.02, 11], [0.04, 12], [0.07, 13]],
        first=10,
        warmup=1,
        interval_count=2,
    )
    assert evidence["rawCallbackCount"] == 5
    assert evidence["measuredFrameCallbackCount"] == 3
    assert evidence["intervalSampleCount"] == 2
    assert evidence["missingNotificationFrames"] == []


@pytest.mark.parametrize(
    ("callbacks", "message"),
    [
        ([[0.0, 10], [0.02, 11], [0.04, 13]], "incomplete"),
        ([[0.0, 10], [0.02, 12], [0.04, 11]], "backwards"),
        ([[0.02, 10], [0.01, 11], [0.04, 12]], "timestamps"),
        ([[0.0, 10], [0.02, 11], [0.04, 12]], "incomplete"),
    ],
)
def test_normalize_playback_callbacks_rejects_missing_order_and_n_samples(
    callbacks: list[list[float]], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        profile.normalize_playback_callbacks(
            callbacks, first=10, warmup=1, interval_count=2
        )


def test_runner_source_keeps_native_route_and_evidence_contract() -> None:
    source = Path(profile.__file__).read_text(encoding="utf-8")
    assert '"use_cpp_vp2_ownership": False' in source
    assert '"separate_meshes_by_material": True' in source
    assert 'rendererOverrideName=""' in source
    assert 'settings.set("import.model.create_mmd_shaders", True)' in source
    assert 'settings.set("import.model.mmd_shader_backend", "dx11")' in source
    assert 'settings.set("import.model.separate_meshes_by_material", True)' in source
    assert '"mmdRenderShape": len(proxies)' in source
    assert '"mmdRenderShapeNodesPresent": None' in source
    assert '"mmdRenderShapeUsedForDrawing": False' in source
    assert '"sceneGeometryRoute": config.get("geometryRoute", "source-direct")' in source
    assert '"additionalEvaluatedMeshConsumer"' in source
    assert '"mmdRenderShapeInputConsumersDetached"' in source
    assert 'source + ".outMesh", source=False, destination=True, type="mmdRenderShape"' in source
    assert '"profilerSampling": False' in source
    assert "add3dViewPostRenderMsgCallback" in source
    assert '"missingNotificationFrames"' in source
    assert '"intervalSampleCount"' in source
    assert '"nativePlaybackSummary"' in source
    assert '"p95Ms"' in source
    assert '"savedSceneAnimationEvidence"' in source
    assert "build_material_morph_graph(root)" in source
    assert '"materialContracts"' in source
    assert '"materialUniformSync"' in source
    assert '"sphereTexture": sphere_plan' in source
    assert '"toonTexture": toon_plan' in source
    assert '"SphereTexture", "HasSphereTexture"' in source
    assert '"ToonTexture", "HasToonTexture"' in source
    assert "mmd_resolved_sphere_texture_path" in source
    assert "mmd_resolved_toon_texture_path" in source
    assert "STANDARD_TOON_TEXTURE_DIR" in source
    assert "mark_mmd_texture_file_node" in source
    assert '"bindingValidated"] = True' in source
    assert '"validated": True' in source
    assert "sphereAndToonTextureConversion" not in source
    assert '"sceneArtifactIntegrity"' in source
    assert 'cmds.evaluator(name="cache", enable=False)' in source
    assert '"cacheEvaluatorEnabled"' in source
    assert '"sixteenMsEligible"' in source
    for event in profile.FORBIDDEN_CPU_GEOMETRY_EVENTS:
        assert event in source
    for missing_scope in (
        '"selfShadow": "not_run"',
        '"customOrderedParity": "not_run"',
        '"gpuTimestamp": "not_run"',
    ):
        assert missing_scope in source


def test_sha256_file_is_stable(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"vp2-native")
    first = profile.sha256_file(artifact)
    assert first == profile.sha256_file(artifact)
    assert len(first) == 64


def test_secondary_texture_plan_resolves_canonical_custom_textures(tmp_path: Path) -> None:
    texture = tmp_path / "effect.sph"
    texture.write_bytes(b"sphere")

    plan = profile.secondary_texture_plan(
        "sphere", str(texture), 3, sphere_mode=2
    )

    assert plan["status"] == "resolved"
    assert plan["path"] == str(texture.resolve())
    assert plan["canonicalAttributeValue"] == str(texture)
    assert plan["sourceKind"] == "pmx_texture"
    assert plan["sha256"] == profile.sha256_file(texture)


def test_secondary_texture_plan_resolves_shared_toon_from_index() -> None:
    plan = profile.secondary_texture_plan("toon", "", 0, shared_toon=True)

    assert plan["status"] == "resolved"
    assert plan["canonicalAttributeValue"] == ""
    assert plan["sourceKind"] == "shared_toon"
    assert Path(plan["path"]).name == "toon01.bmp"
    assert Path(plan["path"]).is_file()


@pytest.mark.parametrize(
    "plan",
    [
        pytest.param(("sphere", "", -1, {"sphere_mode": 0}), id="sphere"),
        pytest.param(("toon", "", -1, {}), id="custom-toon"),
    ],
)
def test_secondary_texture_plan_distinguishes_legitimate_unused_state(
    plan: tuple[str, str, int, dict],
) -> None:
    slot, path, index, options = plan
    result = profile.secondary_texture_plan(slot, path, index, **options)

    assert result["status"] == "unused"
    assert result["canonicalAttributeValue"] == ""
    assert "reason" in result


def test_disabled_sphere_validates_indexed_texture_without_binding(tmp_path: Path) -> None:
    texture = tmp_path / "disabled.sph"
    texture.write_bytes(b"sphere")

    plan = profile.secondary_texture_plan(
        "sphere", str(texture), 2, sphere_mode=0
    )

    assert plan["status"] == "unused"
    assert plan["canonicalTextureValidated"] is True
    assert plan["path"] == str(texture.resolve())


def test_sphere_subtexture_mode_is_explicitly_unsupported(tmp_path: Path) -> None:
    texture = tmp_path / "subtexture.spa"
    texture.write_bytes(b"sphere")
    with pytest.raises(ValueError, match="subtexture mode"):
        profile.secondary_texture_plan("sphere", str(texture), 1, sphere_mode=3)


@pytest.mark.parametrize(
    ("slot", "path", "index", "options", "message"),
    [
        ("sphere", "", 0, {"sphere_mode": 1}, "no canonical resolved path"),
        ("sphere", "stale.sph", -1, {"sphere_mode": 0}, "stale resolved path"),
        ("toon", "custom.bmp", 0, {"shared_toon": True}, "must not carry"),
        ("toon", "", 10, {"shared_toon": True}, "between 0 and 9"),
        ("toon", "", 0, {}, "no canonical resolved path"),
    ],
)
def test_secondary_texture_plan_rejects_inconsistent_canonical_state(
    slot: str, path: str, index: int, options: dict, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        profile.secondary_texture_plan(slot, path, index, **options)


def test_secondary_texture_plan_requires_resolved_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.bmp"
    with pytest.raises(ValueError, match="does not exist"):
        profile.secondary_texture_plan("toon", str(missing), 2)


def test_config_is_json_serializable(tmp_path: Path) -> None:
    assert json.loads(json.dumps(_config(tmp_path)))["shaderBackend"] == "dx11"


def test_nox_exposes_vp2_native_geometry_runner() -> None:
    nox_source = (profile.ROOT / "noxfile.py").read_text(encoding="utf-8")
    assert "def render_vp2_native_geometry(" in nox_source
    assert '"tools/render_override/vp2_native_geometry_profile.py"' in nox_source
