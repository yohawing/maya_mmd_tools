"""Host-neutral contracts for the local representative asset selector."""

from types import SimpleNamespace

from tools.local_asset_manifest import (
    _match_ratio,
    _oracle_frames,
    _pair_score,
    _pmx_descriptor,
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
    assert descriptor["_bone_names"] == {"センター", "左腕"}


def test_pair_score_uses_exact_normalized_track_coverage():
    vmd = {"_bone_names": {"センター", "左腕"}, "_morph_names": {"笑顔"}}
    pmx = {"_bone_names": {"センター", "左腕", "右腕"}, "_morph_names": {"笑顔", "怒り"}}

    assert _pair_score(vmd, pmx) == {
        "bone_match_ratio": 1.0,
        "morph_match_ratio": 1.0,
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


def test_oracle_frames_are_unique_quarter_span_samples():
    assert _oracle_frames({"frame_start": 3, "frame_end": 13}) == [3, 6, 8, 11, 13]
    assert _oracle_frames({"frame_start": None, "frame_end": 13}) == []
