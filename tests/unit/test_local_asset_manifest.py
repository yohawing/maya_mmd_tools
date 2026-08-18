"""Host-neutral contracts for the local representative asset selector."""

from types import SimpleNamespace

from tools.local_asset_manifest import (
    _match_ratio,
    _deduplicate_case_names,
    _oracle_frames,
    _pair_score,
    _pmx_descriptor,
    _select_motion_cases,
    _select_pmx,
    _vmd_descriptor,
)


def _bone(name, frame, interpolation):
    return SimpleNamespace(
        bone_name=name,
        frame_number=frame,
        interpolation=interpolation,
    )


def test_vmd_metrics_record_density_tracks_and_interpolation_variants(tmp_path, monkeypatch):
    path = tmp_path / "motion.vmd"
    path.write_bytes(b"motion")
    data = SimpleNamespace(
        bone_frames=[
            _bone("センター", 0, bytes([0] * 64)),
            _bone("センター", 10, bytes([0] * 64)),
            _bone("左腕", 20, bytes([1] * 64)),
        ],
        morph_frames=[SimpleNamespace(morph_name="笑顔", frame_number=10, interpolation=None)],
        camera_frames=[],
        light_frames=[],
        shadow_frames=[],
        ik_show_hide_frames=[],
    )
    monkeypatch.setattr("mmd_tools.core.vmd_data.VmdData.parse_file", lambda _self, _path: data)

    descriptor = _vmd_descriptor(path)

    assert descriptor["metrics"]["total_keys"] == 4
    assert descriptor["metrics"]["active_tracks"] == 3
    assert descriptor["metrics"]["frame_start"] == 0
    assert descriptor["metrics"]["frame_end"] == 20
    assert descriptor["metrics"]["frame_span"] == 21
    assert descriptor["metrics"]["interpolation_variant_count"] == 2
    assert descriptor["metrics"]["duplicate_key_count"] == 0
    assert descriptor["_bone_names"] == {"センター", "左腕"}


def test_pair_score_uses_exact_normalized_track_coverage():
    vmd = {
        "_bone_names": {"センター", "左腕"},
        "_morph_names": {"笑顔"},
        "_ik_names": {"センター"},
    }
    pmx = {"_bone_names": {"センター", "左腕", "右腕"}, "_morph_names": {"笑顔", "怒り"}}

    assert _pair_score(vmd, pmx) == {
        "bone_match_ratio": 1.0,
        "morph_match_ratio": 1.0,
        "ik_match_ratio": 1.0,
        "combined_match_ratio": 1.0,
    }
    assert _match_ratio({"missing"}, {"known"}) == 0.0


def test_pmx_metrics_use_parser_name_fields_for_compatibility(tmp_path, monkeypatch):
    path = tmp_path / "model.pmx"
    path.write_bytes(b"model")
    data = SimpleNamespace(
        header=SimpleNamespace(model_name="sample"),
        vertices=[object()],
        faces=[object()],
        materials=[],
        bones=[SimpleNamespace(name="センター", name_english="Center")],
        morphs=[SimpleNamespace(name="笑顔", name_english="Smile")],
        display_frames=[],
        rigid_bodies=[],
        joints=[],
        soft_bodies=[],
    )
    monkeypatch.setattr("mmd_tools.core.mmd_parser.parse_pmx_file", lambda *_args, **_kwargs: data)

    descriptor = _pmx_descriptor(path)

    assert descriptor["_bone_names"] == {"センター"}
    assert descriptor["_morph_names"] == {"笑顔"}
    assert descriptor["metrics"]["invalid_local_axis_bones"] == 0
    assert descriptor["metrics"]["sdef_vertices"] == 0


def test_vmd_metrics_record_duplicate_section_track_frame(tmp_path, monkeypatch):
    path = tmp_path / "duplicate.vmd"
    path.write_bytes(b"motion")
    data = SimpleNamespace(
        bone_frames=[
            _bone("センター", 0, bytes([0] * 64)),
            _bone("センター", 0, bytes([1] * 64)),
        ],
        morph_frames=[],
        camera_frames=[SimpleNamespace(frame_number=5)],
        light_frames=[],
        shadow_frames=[],
        ik_show_hide_frames=[],
    )
    monkeypatch.setattr("mmd_tools.core.vmd_data.VmdData.parse_file", lambda _self, _path: data)

    descriptor = _vmd_descriptor(path)

    assert descriptor["metrics"]["duplicate_key_count"] == 1


def test_pmx_selection_keeps_structural_model_and_records_quality_flags(tmp_path, monkeypatch):
    from mmd_tools.core.pmx_data.bone import PmxBoneFlag

    path = tmp_path / "invalid.pmx"
    path.write_bytes(b"model")
    data = SimpleNamespace(
        header=SimpleNamespace(model_name="sample"),
        vertices=[SimpleNamespace(weight_transform_type=3)],
        faces=[object()],
        materials=[],
        bones=[
            SimpleNamespace(
                name="センター",
                name_english="Center",
                bone_flag=int(PmxBoneFlag.LOCAL_AXIS),
                x_axis_direction=(1.0, 0.0, 0.0),
                z_axis_direction=(2.0, 0.0, 0.0),
            )
        ],
        morphs=[],
        display_frames=[],
        rigid_bodies=[],
        joints=[],
        soft_bodies=[],
    )
    monkeypatch.setattr("mmd_tools.core.mmd_parser.parse_pmx_file", lambda *_args, **_kwargs: data)

    descriptor = _pmx_descriptor(path)

    assert descriptor["metrics"]["invalid_local_axis_bones"] == 1
    assert descriptor["metrics"]["sdef_vertices"] == 1
    assert _select_pmx([descriptor], 1) == [descriptor]


def test_vmd_selection_keeps_duplicate_keys_for_runtime_diagnostics(tmp_path):
    vmd = {
        "path": str(tmp_path / "duplicate.vmd"),
        "sha256": "vmd",
        "metrics": {
            "bone_frames": 20,
            "morph_frames": 0,
            "density": 1.0,
            "interpolation_variant_count": 2,
            "total_keys": 1_000,
            "active_tracks": 2,
            "frame_span": 10,
            "duplicate_key_count": 1,
        },
        "_bone_names": {"センター", "左腕"},
        "_morph_names": set(),
        "_ik_names": set(),
    }
    pmx = {
        "path": str(tmp_path / "model.pmx"),
        "sha256": "pmx",
        "_bone_names": {"センター", "左腕"},
        "_morph_names": set(),
    }

    cases = _select_motion_cases(
        [vmd],
        [pmx],
        dense_count=1,
        sparse_count=0,
        dense_density=0.2,
        sparse_density=0.05,
    )

    assert cases[0]["metrics"]["duplicate_key_count"] == 1


def test_vmd_selection_keeps_low_pair_score_for_diagnostics(tmp_path):
    vmd = {
        "path": str(tmp_path / "unmatched.vmd"),
        "sha256": "vmd",
        "metrics": {
            "bone_frames": 20,
            "morph_frames": 0,
            "density": 1.0,
            "interpolation_variant_count": 1,
            "total_keys": 1_000,
            "active_tracks": 2,
            "frame_span": 10,
            "duplicate_key_count": 0,
        },
        "_bone_names": {"missing_bone"},
        "_morph_names": set(),
        "_ik_names": set(),
    }
    pmx = {
        "path": str(tmp_path / "model.pmx"),
        "sha256": "pmx",
        "_bone_names": {"known_bone"},
        "_morph_names": set(),
    }

    cases = _select_motion_cases(
        [vmd],
        [pmx],
        dense_count=1,
        sparse_count=0,
        dense_density=0.2,
        sparse_density=0.05,
    )

    assert cases[0]["pmx"] == pmx["path"]
    assert cases[0]["metrics"]["bone_match_ratio"] == 0.0


def test_case_names_are_unique_after_path_sanitization():
    cases = _deduplicate_case_names(
        [
            {"name": "sparse_asset", "vmd_sha256": "abcdef0123456789"},
            {"name": "sparse_asset", "vmd_sha256": "1234567890abcdef"},
        ]
    )

    assert [case["name"] for case in cases] == [
        "sparse_asset",
        "sparse_asset_12345678",
    ]


def test_oracle_frames_are_unique_quarter_span_samples():
    assert _oracle_frames({"frame_start": 3, "frame_end": 13}) == [3, 6, 8, 11, 13]
    assert _oracle_frames({"frame_start": None, "frame_end": 13}) == []
