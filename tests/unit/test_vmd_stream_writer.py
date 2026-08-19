"""Focused byte/lifecycle contracts for the incremental VMD writer."""

from __future__ import annotations

import hashlib
import struct

import pytest

from mmd_tools.core.vmd_data import VmdData
from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame
from mmd_tools.io.vmd_stream_writer import (
    DEFAULT_BONE_INTERPOLATION,
    DEFAULT_CAMERA_INTERPOLATION,
    VMD_MAGIC,
    VmdStreamWriter,
    VmdStreamWriterError,
)


def _fixed_name(value: str, length: int) -> bytes:
    return value.encode("cp932")[:length].ljust(length, b"\0")


def _reference_bytes():
    bone = {
        "bone_name": "日本語骨ABCDEFGHIJKLM",
        "frame": 3,
        "position": (1.0, -2.0, 3.5),
        "rotation": (0.1, 0.2, 0.3, 0.4),
        "interpolation": bytes(range(64)),
    }
    morph = {"morph_name": "表情ABCDEFGHIJK", "frame": 8, "value": 0.75}
    camera = {
        "frame": 13,
        "distance": 12.5,
        "position": (1.0, 2.0, 3.0),
        "rotation": (-0.1, -0.2, -0.3),
        "interpolation": bytes(range(24)),
        "fov": 45,
        # Rust's JSON writer writes 0 for native perspective=true and 1 for
        # false; the VmdData field is the inverse byte representation.
        "perspective": 0,
    }
    light = {"frame": 21, "color": (0.1, 0.2, 0.3), "position": (4.0, 5.0, 6.0)}
    shadow = {"frame": 34, "mode": 2, "distance": 8.5}
    ik = {
        "frame": 55,
        "visible": True,
        "ik_states": [("左足IK", True), {"boneName": "右足IK", "enabled": False}],
    }

    output = bytearray(VMD_MAGIC)
    output.extend(_fixed_name("モデル名", 20))
    output.extend(struct.pack("<I", 1))
    output.extend(_fixed_name(bone["bone_name"], 15))
    output.extend(struct.pack("<I", bone["frame"]))
    output.extend(struct.pack("<fff", *bone["position"]))
    output.extend(struct.pack("<ffff", *bone["rotation"]))
    output.extend(bone["interpolation"])
    output.extend(struct.pack("<I", 1))
    output.extend(_fixed_name(morph["morph_name"], 15))
    output.extend(struct.pack("<I", morph["frame"]))
    output.extend(struct.pack("<f", morph["value"]))
    output.extend(struct.pack("<I", 1))
    output.extend(struct.pack("<I", camera["frame"]))
    output.extend(struct.pack("<f", camera["distance"]))
    output.extend(struct.pack("<fff", *camera["position"]))
    output.extend(struct.pack("<fff", *camera["rotation"]))
    output.extend(camera["interpolation"])
    output.extend(struct.pack("<I", camera["fov"]))
    output.extend(b"\0")
    output.extend(struct.pack("<I", 1))
    output.extend(struct.pack("<I", light["frame"]))
    output.extend(struct.pack("<fff", *light["color"]))
    output.extend(struct.pack("<fff", *light["position"]))
    output.extend(struct.pack("<I", 1))
    output.extend(struct.pack("<I", shadow["frame"]))
    output.extend(struct.pack("<B", shadow["mode"]))
    output.extend(struct.pack("<f", shadow["distance"]))
    output.extend(struct.pack("<I", 1))
    output.extend(struct.pack("<I", ik["frame"]))
    output.extend(struct.pack("<B", 1))
    output.extend(struct.pack("<I", 2))
    output.extend(_fixed_name("左足IK", 20))
    output.extend(b"\1")
    output.extend(_fixed_name("右足IK", 20))
    output.extend(b"\0")
    return bytes(output), (bone, morph, camera, light, shadow, ik)


def test_all_sections_match_native_writer_bytes_and_parse(tmp_path):
    expected, frames = _reference_bytes()
    path = tmp_path / "motion.vmd"
    writer = VmdStreamWriter(path, "モデル名")
    writer.write_bone(frames[0])
    writer.write_morph(frames[1])
    writer.write_camera(frames[2])
    writer.write_light(frames[3])
    writer.write_shadow(frames[4])
    writer.write_ik(frames[5])
    summary = writer.finish()

    assert path.read_bytes() == expected
    assert summary.size == len(expected)
    assert summary.sha256 == hashlib.sha256(expected).hexdigest()
    assert dict(summary.counts) == {
        "bones": 1,
        "morphs": 1,
        "cameras": 1,
        "lights": 1,
        "shadows": 1,
        "ik": 1,
    }
    assert summary.min_frame == 3
    assert summary.max_frame == 55
    assert summary.frame_bounds["ik"].min_frame == 55

    parsed = VmdData().parse_file(path)
    assert len(parsed.bone_frames) == 1
    assert parsed.bone_frames[0].bone_name == "日本語骨ABCDEFG"
    assert parsed.bone_frames[0].interpolation == bytes(range(64))
    assert parsed.morph_frames[0].morph_name == "表情ABCDEFGHIJK"
    assert parsed.camera_frames[0].perspective == 0
    assert parsed.ik_show_hide_frames[0].ik_count == 2
    assert parsed.ik_show_hide_frames[0].ik_states == [("左足IK", 1), ("右足IK", 0)]


def test_default_interpolation_and_empty_ik_count_are_serialized(tmp_path):
    path = tmp_path / "empty-ik.vmd"
    writer = VmdStreamWriter(path)
    writer.write_bone({"name": "bone", "frame": 0})
    writer.write_camera({"frame": 1})
    writer.finish()
    data = path.read_bytes()
    parsed = VmdData().parse_file(path)
    assert parsed.bone_frames[0].interpolation == DEFAULT_BONE_INTERPOLATION
    assert parsed.camera_frames[0].interpolation == DEFAULT_CAMERA_INTERPOLATION
    assert parsed.light_frames == []
    assert parsed.shadow_frames == []
    assert parsed.ik_show_hide_frames == []
    # Header + six count slots and the two records; every optional count exists.
    assert len(data) > 50 + 6 * 4


def test_fixed_name_boundaries_are_byte_truncated_and_zero_padded(tmp_path):
    path = tmp_path / "name-boundaries.vmd"
    writer = VmdStreamWriter(path, "M" * 20 + "overflow")
    writer.write_bone({"name": "B" * 15 + "overflow", "frame": 0})
    writer.write_ik(
        {
            "frame": 1,
            "visible": True,
            "ik_states": [("I" * 20 + "overflow", True)],
        }
    )
    writer.finish()
    data = path.read_bytes()
    assert data[30:50] == b"M" * 20
    assert data[54:69] == b"B" * 15
    ik_offset = 50 + 5 * 4 + 111 + 4
    assert data[ik_offset + 9 : ik_offset + 29] == b"I" * 20


def test_explicit_section_lifecycle_backpatches_counts(tmp_path):
    path = tmp_path / "explicit.vmd"
    with VmdStreamWriter(path, "model") as writer:
        writer.begin_section("bones")
        writer.write_bone({"name": "bone", "frame": 10})
        writer.end_section()
        writer.begin_section("morphs")
        writer.write_morph({"name": "morph", "frame": 4, "value": 0.5})
        # finish reserves/backpatches cameras, lights, shadows, and IK.
        summary = writer.finish()
    assert summary.counts["bones"] == 1
    assert summary.counts["morphs"] == 1
    assert all(summary.counts[name] == 0 for name in ("cameras", "lights", "shadows", "ik"))
    assert struct.unpack_from("<I", path.read_bytes(), 30 + 20)[0] == 1


def test_invalid_order_removes_owned_private_output(tmp_path):
    path = tmp_path / "bad-order.vmd"
    writer = VmdStreamWriter(path)
    writer.write_morph({"name": "morph", "frame": 1})
    with pytest.raises(VmdStreamWriterError):
        writer.write_bone({"name": "late", "frame": 2})
    assert not path.exists()


def test_invalid_frame_and_exception_cleanup(tmp_path):
    path = tmp_path / "bad-frame.vmd"
    writer = VmdStreamWriter(path)
    with pytest.raises(VmdStreamWriterError):
        writer.write_camera({"frame": 1, "position": (float("nan"), 0, 0)})
    assert not path.exists()

    path = tmp_path / "context-failure.vmd"
    with pytest.raises(RuntimeError):
        with VmdStreamWriter(path) as failed:
            failed.write_bone({"name": "bone", "frame": 1})
            raise RuntimeError("cancel")
    assert not path.exists()


@pytest.mark.parametrize(
    ("perspective", "expected"),
    ((True, 0), (False, 1), (0, 0), (1, 1)),
)
def test_camera_perspective_matches_rust_bool_and_vmddata_byte_semantics(
    tmp_path, perspective, expected
):
    path = tmp_path / "perspective-{}.vmd".format(repr(perspective).replace("'", ""))
    frame = VmdCameraFrame()
    frame.perspective = perspective
    writer = VmdStreamWriter(path)
    writer.write_camera(frame)
    writer.finish()
    parsed = VmdData().parse_file(path)
    assert parsed.camera_frames[0].perspective == expected


def test_camera_perspective_rejects_non_bool_non_binary_int(tmp_path):
    path = tmp_path / "invalid-perspective.vmd"
    writer = VmdStreamWriter(path)
    with pytest.raises(VmdStreamWriterError):
        writer.write_camera({"perspective": 2})
    assert not path.exists()


@pytest.mark.parametrize("cancel", (KeyboardInterrupt, SystemExit))
def test_direct_write_cancellation_removes_partial_output(tmp_path, cancel):
    class CancelFrame:
        @property
        def bone_name(self):
            raise cancel()

    path = tmp_path / "cancel-write.vmd"
    writer = VmdStreamWriter(path)
    with pytest.raises(cancel):
        writer.write_frame("bones", CancelFrame())
    assert not path.exists()


@pytest.mark.parametrize("cancel", (KeyboardInterrupt, SystemExit))
def test_finish_cancellation_removes_partial_output(tmp_path, cancel):
    path = tmp_path / "cancel-finish.vmd"
    writer = VmdStreamWriter(path)

    def raise_cancel():
        raise cancel()

    writer._close_section = raise_cancel
    with pytest.raises(cancel):
        writer.finish()
    assert not path.exists()


def test_double_finish_is_fail_closed(tmp_path):
    path = tmp_path / "double-finish.vmd"
    writer = VmdStreamWriter(path)
    writer.finish()
    assert path.exists()
    with pytest.raises(VmdStreamWriterError):
        writer.finish()
    assert not path.exists()
