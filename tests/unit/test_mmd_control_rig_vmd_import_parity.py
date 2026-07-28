"""Pure contracts for Control Rig VMD import parity report helpers."""

from __future__ import annotations

from types import SimpleNamespace

from tests.viewport import mmd_control_rig_vmd_import_parity as parity


def _bone_frame(name: str, frame: int) -> SimpleNamespace:
    return SimpleNamespace(bone_name=name, frame_number=frame, interpolation=bytes(64))


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
