"""Pure contracts for Control Rig VMD import parity report helpers."""

from __future__ import annotations

from types import SimpleNamespace

from tests.viewport import mmd_control_rig_vmd_import_parity as parity
from mmd_tools.core.pmx_data.bone import PmxBoneFlag
from mmd_tools.core.pmx_data.morph import PmxMorphType


def _bone_frame(name: str, frame: int) -> SimpleNamespace:
    return SimpleNamespace(bone_name=name, frame_number=frame, interpolation=bytes(64))


def _morph_frame(name: str, frame: int, value: float) -> SimpleNamespace:
    return SimpleNamespace(morph_name=name, frame_number=frame, value=value)


def test_cpp_plugin_path_prefers_maya_version_override(tmp_path):
    version_path = tmp_path / "maya2026.mll"
    general_path = tmp_path / "general.mll"

    result = parity._resolve_cpp_plugin_path(
        "2026",
        env={
            "MMD_TOOLS_CPP_PLUGIN_2026": str(version_path),
            "MMD_TOOLS_CPP_PLUGIN": str(general_path),
        },
        root=tmp_path,
    )

    assert result == version_path.resolve()


def test_cpp_plugin_path_uses_general_override_for_other_versions(tmp_path):
    general_path = tmp_path / "general.mll"

    result = parity._resolve_cpp_plugin_path(
        "2024",
        env={"MMD_TOOLS_CPP_PLUGIN": str(general_path)},
        root=tmp_path,
    )

    assert result == general_path.resolve()


def test_cpp_plugin_path_defaults_to_debug_binary(tmp_path):
    result = parity._resolve_cpp_plugin_path("2024", env={}, root=tmp_path)

    assert result == tmp_path / "plug-ins" / "2024" / "Debug" / "mmd_tools_cpp.mll"


def test_external_oracle_summary_keeps_route_results_independent():
    legacy_oracle = {"status": "pass", "attempted": True, "pass": True}
    direct_oracle = {"status": "fail", "attempted": True, "pass": False}

    result = parity._external_oracle_summary(
        {
            "legacy": {"externalOracle": legacy_oracle},
            "controlRigDirect": {"externalOracle": direct_oracle},
        },
        {
            "status": "ready",
            "frames": [0, 2],
            "provenance": {
                "status": "ready",
                "runtimePath": "C:/runtime/mmd_runtime_ffi.dll",
                "runtimeSha256": "a" * 64,
                "runtimeAbi": 3,
            },
        },
    )

    assert result["status"] == "fail"
    assert result["pass"] is False
    assert result["routes"]["legacy"]["status"] == "pass"
    assert result["routes"]["controlRigDirect"]["status"] == "fail"
    assert result["oraclePreparation"]["runtimeProvenance"]["runtimeAbi"] == 3
    assert result["runtimeProvenance"]["runtimeSha256"] == "a" * 64


def test_external_oracle_not_run_is_explicit_and_not_numeric_zero():
    result = parity._external_oracle_not_run("FFI unavailable")

    assert result["status"] == "not_run"
    assert result["attempted"] is False
    assert result["pass"] is False
    assert "max" not in result
    assert result["reason"] == "FFI unavailable"


def test_bone_morph_coverage_requires_pmx_type_and_authored_nonzero_weight():
    vmd = SimpleNamespace(
        bone_frames=[_bone_frame("センター", 0)],
        morph_frames=[_morph_frame("CR_BoneMorph", 0, 0.0)],
        ik_show_hide_frames=[],
    )
    pmx = SimpleNamespace(
        morphs=[
            SimpleNamespace(
                name="CR_BoneMorph",
                name_english="CR_BoneMorph",
                morph_type=PmxMorphType.BoneMorph,
                offsets=[{"bone_index": 2, "translation": (0.25, 0.0, 0.0), "rotation": (0.0, 0.0, 0.0, 1.0)}],
            )
        ]
    )
    result = parity._coverage(vmd, pmx, routes=None, interpolation_probe={"frames": [1]})
    item = result["items"]["boneMorph"]
    assert item["fixturePresent"] is True
    assert item["status"] == "missing"
    assert "all zero" in " ".join(item["reasons"])


def test_vmd_roundtrip_compares_morph_key_values():
    exported = SimpleNamespace(
        bone_frames=[_bone_frame("右足", 0)],
        morph_frames=[_morph_frame("CR_BoneMorph", 10, 0.75)],
        ik_show_hide_frames=[],
    )
    fresh = SimpleNamespace(
        bone_frames=[_bone_frame("右足", 0)],
        morph_frames=[_morph_frame("CR_BoneMorph", 10, 0.5)],
        ik_show_hide_frames=[],
    )
    result = parity._compare_vmd_roundtrip(exported, fresh)
    assert result["morphKeyValues"]["pass"] is False
    assert result["firstDivergence"]["category"] == "export_fresh_morph_keys"


def test_un_authored_structural_bone_morph_does_not_fail_export_presence():
    pmx = SimpleNamespace(
        morphs=[
            SimpleNamespace(
                name="CR_BoneMorph",
                name_english="CR_BoneMorph",
                morph_type=PmxMorphType.BoneMorph,
                offsets=[{"bone_index": 2, "translation": (0.25, 0.0, 0.0), "rotation": (0.0, 0.0, 0.0, 1.0)}],
            )
        ]
    )
    result = parity._compare_exported_morph_presence(
        SimpleNamespace(morph_frames=[]),
        pmx,
    )
    assert result["pass"] is True
    assert result["expectedNames"] == []


def test_exported_morph_presence_fails_when_authored_bone_morph_is_dropped():
    pmx = SimpleNamespace(
        morphs=[
            SimpleNamespace(
                name="CR_BoneMorph",
                name_english="CR_BoneMorph",
                morph_type=PmxMorphType.BoneMorph,
                offsets=[{"bone_index": 2, "translation": (0.25, 0.0, 0.0), "rotation": (0.0, 0.0, 0.0, 1.0)}],
            )
        ]
    )
    result = parity._compare_exported_morph_presence(
        SimpleNamespace(morph_frames=[]),
        pmx,
        authored_morph_names={"CR_BoneMorph"},
    )
    assert result["pass"] is False
    assert result["missingNames"] == ["CR_BoneMorph"]


def test_fresh_key_times_compare_bone_union_not_constant_components():
    exported = SimpleNamespace(
        bone_frames=[_bone_frame("右足", 0), _bone_frame("右足", 1)],
        ik_show_hide_frames=[],
    )
    fresh_rows = [
        {"boneName": "右足", "channel": "rotateX", "times": [0]},
        {"boneName": "右足", "channel": "translateX", "times": [0, 1]},
    ]

    result = parity._compare_fresh_bone_key_times(exported, fresh_rows)

    assert result["pass"] is True
    assert result["mismatchCount"] == 0


def test_fresh_key_times_fail_when_bone_frame_is_missing():
    exported = SimpleNamespace(
        bone_frames=[_bone_frame("右足", 0), _bone_frame("右足", 1)],
        ik_show_hide_frames=[],
    )

    result = parity._compare_fresh_bone_key_times(
        exported,
        [{"boneName": "右足", "channel": "translateX", "times": [0]}],
    )

    assert result["pass"] is False
    assert result["firstMismatch"] == {
        "boneName": "右足",
        "exported": [0, 1],
        "fresh": [0],
    }


def test_fresh_key_times_fail_on_unexpected_bone_keys():
    exported = SimpleNamespace(
        bone_frames=[_bone_frame("右足", 0)],
        ik_show_hide_frames=[],
    )

    result = parity._compare_fresh_bone_key_times(
        exported,
        [
            {"boneName": "右足", "times": [0]},
            {"boneName": "余分", "times": [0]},
        ],
    )

    assert result["pass"] is False
    assert result["firstMismatch"]["boneName"] == "余分"


def test_ik_state_compare_requires_observed_matching_nodes():
    matching = [{"frame": 0, "states": [{"boneName": "左足ＩＫ", "enabled": True}]}]

    assert parity._compare_ik_state_inventory(matching, matching)["pass"] is True
    assert parity._compare_ik_state_inventory([], [])["pass"] is False


def test_interpolation_probe_selects_earliest_widest_integer_gap():
    result = parity._select_interpolation_probe([0, 5, 10])

    assert result["status"] == "covered"
    assert result["frames"] == [2]
    assert result["leftKey"] == 0
    assert result["rightKey"] == 5
    assert result["frameIsAuthored"] is False


def test_interpolation_probe_is_not_applicable_without_integer_gap():
    result = parity._select_interpolation_probe([0, 1, 2])

    assert result["status"] == "not_applicable"
    assert result["frames"] == []
    assert result["frameIsAuthored"] is None


def test_stale_export_artifacts_are_removed_or_fail_closed(tmp_path):
    stale_file = tmp_path / "motion.vmd"
    stale_file.write_bytes(b"old")
    parity._remove_stale_artifacts([stale_file, tmp_path / "missing.vmd"])
    assert not stale_file.exists()

    directory = tmp_path / "not-a-vmd"
    directory.mkdir()
    try:
        parity._remove_stale_artifacts([directory])
    except RuntimeError as exc:
        assert "stale VMD cleanup failed" in str(exc)
    else:
        raise AssertionError("directory cleanup must fail closed")


def test_append_coverage_does_not_infer_from_vmd_name_substrings():
    vmd = SimpleNamespace(
        bone_frames=[_bone_frame("fake_append_name", 0), _bone_frame("fake_append_name", 5)],
        morph_frames=[],
        ik_show_hide_frames=[],
    )

    result = parity._coverage(vmd)

    assert result["items"]["append"]["status"] == "missing"
    assert result["items"]["append"]["fixturePresent"] is False
    assert "name inference is disabled" in result["items"]["append"]["reasons"][0]


def test_append_coverage_resolves_grant_indices_and_requires_route_evidence():
    flags = int(PmxBoneFlag.GRANT_PARENT_ROTATE)
    pmx = SimpleNamespace(
        bones=[
            SimpleNamespace(name="source", name_english="", bone_flag=0),
            SimpleNamespace(
                name="target",
                name_english="",
                bone_flag=flags,
                grant_parent_bone_index=0,
                grant_rate=0.5,
            ),
        ]
    )
    vmd = SimpleNamespace(
        bone_frames=[_bone_frame("source", 0), _bone_frame("source", 5)],
        morph_frames=[],
        ik_show_hide_frames=[],
    )
    probe = parity._select_interpolation_probe([0, 5])

    result = parity._coverage(vmd, pmx, routes=None, interpolation_probe=probe)
    append = result["items"]["append"]

    assert append["fixturePresent"] is True
    assert append["roles"] == ["target"]
    assert append["grants"][0]["targetIndex"] == 1
    assert append["grants"][0]["sourceIndex"] == 0
    assert append["grants"][0]["sourceAuthoredFrames"] == [0, 5]
    assert append["status"] == "missing"
    assert any("route evidence is unavailable" in reason for reason in append["reasons"])


def test_append_target_observable_requires_change_from_baseline():
    identity = [1.0 if index % 5 == 0 else 0.0 for index in range(16)]
    moved = list(identity)
    moved[12] = 0.25
    route = {
        "observables": {
            "0": {"1": {"worldMatrix": identity, "skinMatrices": []}},
            "2": {"1": {"worldMatrix": moved, "skinMatrices": []}},
        }
    }

    result = parity._route_target_observable_evidence(route, 1, 2)

    assert result["pass"] is True
    assert result["delta"] == 0.25


def test_append_coverage_fails_closed_for_invalid_grant_metadata():
    flags = int(PmxBoneFlag.GRANT_PARENT_MOVE)
    pmx = SimpleNamespace(
        bones=[
            SimpleNamespace(name="source", name_english="", bone_flag=0),
            SimpleNamespace(
                name="target",
                name_english="",
                bone_flag=flags,
                grant_parent_bone_index=-1,
                grant_rate=float("nan"),
            ),
        ]
    )
    vmd = SimpleNamespace(bone_frames=[_bone_frame("source", 0), _bone_frame("source", 5)], morph_frames=[], ik_show_hide_frames=[])

    append = parity._coverage(vmd, pmx, routes={}, interpolation_probe={"frames": [2]})["items"]["append"]

    assert append["status"] == "missing"
    assert any("invalid grant source index" in reason for reason in append["grants"][0]["reasons"])
    assert any("grant rate is not finite" in reason for reason in append["grants"][0]["reasons"])


def test_append_writer_evidence_fails_when_target_has_no_mmd_append(monkeypatch):
    class _Cmds:
        def listConnections(self, *_args, **_kwargs):
            return []

    monkeypatch.setattr(parity, "cmds", _Cmds())
    route = {"importStatus": "pass", "records": {"0": {"joint": "source"}, "1": {"joint": "target"}}}
    grant = {"targetIndex": 1, "sourceIndex": 0, "affectRotation": True, "affectTranslation": False}

    result = parity._scene_append_writer_evidence(route, grant)

    assert result["pass"] is False
    assert "target has no mmdAppend output writer" in result["reasons"]
