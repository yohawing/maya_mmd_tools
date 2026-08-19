"""Incremental, native-compatible VMD byte writer.

The Mode C prepare path must not build a Python object graph containing every
sampled frame.  :class:`VmdStreamWriter` accepts one frame at a time, writes it
immediately, and keeps only section counters and frame bounds in memory.  The
on-disk layout intentionally mirrors ``mmd-anim-format``'s VMD writer rather
than ``VmdData.write_file``: all six section counts are present, including an
empty property/IK section, and fixed names use CP932 bytes.

The output path is owned by the writer.  Any invalid frame, lifecycle error,
or I/O failure removes the private partial file so callers can safely discard
the candidate without accidentally publishing it.
"""

from __future__ import annotations

import hashlib
import math
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, BinaryIO, Dict, Mapping, Optional, Sequence, Tuple


VMD_MAGIC = b"Vocaloid Motion Data 0002\x00\x00\x00\x00\x00"
DEFAULT_BONE_INTERPOLATION = b"\x14" * 64
DEFAULT_CAMERA_INTERPOLATION = b"\x14" * 24

_SECTION_NAMES = ("bones", "morphs", "cameras", "lights", "shadows", "ik")
_SECTION_ALIASES = {
    "bone": "bones",
    "bone_frames": "bones",
    "morph": "morphs",
    "morph_frames": "morphs",
    "camera": "cameras",
    "camera_frames": "cameras",
    "light": "lights",
    "light_frames": "lights",
    "shadow": "shadows",
    "shadow_frames": "shadows",
    "self_shadow": "shadows",
    "self_shadow_frames": "shadows",
    "self_shadows": "shadows",
    "ik_frames": "ik",
    "ik_show_hide_frames": "ik",
    "properties": "ik",
    "property": "ik",
}


class VmdStreamWriterError(Exception):
    """Raised when a VMD stream cannot be completed safely."""


@dataclass(frozen=True)
class VmdFrameBounds:
    """Inclusive frame-number bounds for one section."""

    minimum: Optional[int]
    maximum: Optional[int]

    @property
    def min_frame(self) -> Optional[int]:
        return self.minimum

    @property
    def max_frame(self) -> Optional[int]:
        return self.maximum

    @property
    def frame_min(self) -> Optional[int]:
        return self.minimum

    @property
    def frame_max(self) -> Optional[int]:
        return self.maximum


@dataclass(frozen=True)
class VmdStreamSummary:
    """Immutable receipt returned after a complete VMD stream is flushed."""

    path: str
    size: int
    counts: Mapping[str, int]
    frame_bounds: Mapping[str, VmdFrameBounds]
    sha256: str

    @property
    def section_counts(self) -> Mapping[str, int]:
        return self.counts

    @property
    def output_path(self) -> str:
        return self.path

    @property
    def bytes_written(self) -> int:
        return self.size

    @property
    def digest(self) -> str:
        return self.sha256

    @property
    def min_frame(self) -> Optional[int]:
        values = [bound.minimum for bound in self.frame_bounds.values() if bound.minimum is not None]
        return min(values) if values else None

    @property
    def max_frame(self) -> Optional[int]:
        values = [bound.maximum for bound in self.frame_bounds.values() if bound.maximum is not None]
        return max(values) if values else None


_MISSING = object()


def _field(value: Any, names: Sequence[str], default: Any = _MISSING) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    else:
        for name in names:
            try:
                return getattr(value, name)
            except AttributeError:
                continue
    if default is not _MISSING:
        return default
    raise ValueError("missing required VMD frame field: {}".format(names[0]))


def _u32(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError("{} must be an unsigned integer".format(label))
    try:
        integer = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("{} must be an unsigned integer".format(label)) from exc
    try:
        if integer != value or integer < 0 or integer > 0xFFFFFFFF:
            raise ValueError("{} is outside the u32 range".format(label))
    except (TypeError, ValueError, OverflowError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(label):
            raise
        raise ValueError("{} must be an unsigned integer".format(label)) from exc
    return integer


def _f32(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("{} must be a finite number".format(label)) from exc
    if not math.isfinite(result):
        raise ValueError("{} must be a finite number".format(label))
    try:
        struct.pack("<f", result)
    except (OverflowError, struct.error) as exc:
        raise ValueError("{} does not fit an f32".format(label)) from exc
    return result


def _vector(value: Any, length: int, label: str) -> Tuple[float, ...]:
    try:
        values = tuple(value)
    except TypeError as exc:
        raise ValueError("{} must contain {} values".format(label, length)) from exc
    if len(values) != length:
        raise ValueError("{} must contain {} values".format(label, length))
    return tuple(_f32(item, "{}[{}]".format(label, index)) for index, item in enumerate(values))


def _fixed_bytes(value: Any, length: int, label: str) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise ValueError("{} must be bytes-like".format(label))
    raw = bytes(value)
    if len(raw) != length:
        raise ValueError("{} must contain exactly {} bytes".format(label, length))
    return raw


def _fixed_name(value: Any, length: int, label: str) -> bytes:
    if not isinstance(value, str):
        raise ValueError("{} must be a string".format(label))
    try:
        raw = value.encode("cp932")
    except UnicodeEncodeError as exc:
        raise ValueError("{} cannot be encoded as CP932".format(label)) from exc
    # This deliberately follows Rust's write_fixed_bytes: a byte boundary,
    # not a character boundary, determines truncation.
    return raw[:length].ljust(length, b"\x00")


def _pack_values(fmt: str, values: Sequence[Any]) -> bytes:
    return struct.pack(fmt, *values)


def _perspective_byte(value: Any) -> int:
    """Convert Rust's semantic bool or legacy VMD byte to the wire byte."""
    if isinstance(value, bool):
        return 0 if value else 1
    if isinstance(value, int) and value in (0, 1):
        return value
    raise ValueError("perspective must be bool or integer 0/1")


class VmdStreamWriter:
    """Write a complete VMD file incrementally to an owned private path.

    Section methods can be called directly in canonical order, or callers may
    use :meth:`begin_section`/:meth:`end_section` around batches.  A section is
    advanced automatically when the next section's write method is called.
    """

    def __init__(self, output_path: os.PathLike, model_name: str = "") -> None:
        self._path = Path(os.fspath(output_path)).absolute()
        self._file: Optional[BinaryIO] = None
        self._finished = False
        self._section_index = -1
        self._count_position: Optional[int] = None
        self._counts: Dict[str, int] = {name: 0 for name in _SECTION_NAMES}
        self._minimums: Dict[str, Optional[int]] = {name: None for name in _SECTION_NAMES}
        self._maximums: Dict[str, Optional[int]] = {name: None for name in _SECTION_NAMES}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._file = open(str(self._path), "w+b")
            self._file.write(VMD_MAGIC)
            self._file.write(_fixed_name(model_name, 20, "model_name"))
            self._begin_section(0)
        except BaseException as exc:
            self._cleanup()
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(exc, Exception):
                raise VmdStreamWriterError("failed to initialize VMD stream") from exc
            raise

    @property
    def path(self) -> str:
        return str(self._path)

    @property
    def current_section(self) -> Optional[str]:
        if self._section_index < 0 or self._section_index >= len(_SECTION_NAMES):
            return None
        return _SECTION_NAMES[self._section_index]

    def __enter__(self) -> "VmdStreamWriter":
        self._require_open()
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        if exc_type is not None or not self._finished:
            self._cleanup()
        return False

    def _require_open(self) -> BinaryIO:
        if self._finished:
            raise VmdStreamWriterError("VMD stream has already finished")
        if self._file is None or self._file.closed:
            raise VmdStreamWriterError("VMD stream is closed")
        return self._file

    def _cleanup(self) -> None:
        handle = self._file
        self._file = None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            # A Windows handle held by another process must not be mistaken
            # for a successful cleanup; the caller still receives the failure.
            pass

    def abort(self) -> None:
        """Close and remove the owned partial output."""
        if self._finished:
            raise VmdStreamWriterError("cannot abort a finished VMD stream")
        self._cleanup()

    def _begin_section(self, index: int) -> None:
        handle = self._require_open()
        if index < 0 or index >= len(_SECTION_NAMES):
            raise VmdStreamWriterError("invalid VMD section index")
        self._section_index = index
        self._count_position = handle.tell()
        handle.write(b"\x00\x00\x00\x00")

    def _close_section(self) -> None:
        handle = self._require_open()
        if self._count_position is None:
            raise VmdStreamWriterError("VMD section is not open")
        section = _SECTION_NAMES[self._section_index]
        end = handle.tell()
        handle.seek(self._count_position)
        handle.write(struct.pack("<I", self._counts[section]))
        handle.seek(end)
        self._count_position = None

    def _advance_to(self, section: str) -> None:
        canonical = _SECTION_ALIASES.get(section, section)
        if canonical not in _SECTION_NAMES:
            raise VmdStreamWriterError("unknown VMD section: {}".format(section))
        target = _SECTION_NAMES.index(canonical)
        self._require_open()
        if target < self._section_index:
            raise VmdStreamWriterError(
                "VMD sections must be written in canonical order ({} after {})".format(
                    canonical, self.current_section
                )
            )
        if target == self._section_index:
            return
        self._close_section()
        for index in range(self._section_index + 1, target + 1):
            self._begin_section(index)

    def begin_section(self, section: str) -> None:
        """Move to ``section`` in canonical order, reserving its count."""
        try:
            self._advance_to(section)
        except BaseException as exc:
            self._cleanup()
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(exc, VmdStreamWriterError):
                raise
            if isinstance(exc, Exception):
                raise VmdStreamWriterError("failed to begin VMD section") from exc
            raise

    def end_section(self) -> None:
        """Close the current section and reserve the next one."""
        try:
            self._require_open()
            if self._section_index >= len(_SECTION_NAMES) - 1:
                raise VmdStreamWriterError("cannot end the final VMD section before finish")
            self._close_section()
            self._begin_section(self._section_index + 1)
        except BaseException as exc:
            self._cleanup()
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(exc, VmdStreamWriterError):
                raise
            if isinstance(exc, Exception):
                raise VmdStreamWriterError("failed to end VMD section") from exc
            raise

    def _record_frame(self, section: str, frame_number: Any) -> int:
        self._advance_to(section)
        frame = _u32(frame_number, "frame_number")
        count = self._counts[section]
        if count >= 0xFFFFFFFF:
            raise ValueError("{} section contains too many frames".format(section))
        self._counts[section] = count + 1
        minimum = self._minimums[section]
        maximum = self._maximums[section]
        self._minimums[section] = frame if minimum is None else min(minimum, frame)
        self._maximums[section] = frame if maximum is None else max(maximum, frame)
        return frame

    def write_bone(self, frame: Any) -> None:
        try:
            self._require_open()
            name = _fixed_name(_field(frame, ("bone_name", "boneName", "name")), 15, "bone_name")
            frame_number = self._record_frame("bones", _field(frame, ("frame_number", "frame"), 0))
            position = _vector(_field(frame, ("position", "translation"), (0.0, 0.0, 0.0)), 3, "position")
            rotation = _vector(_field(frame, ("rotation",), (0.0, 0.0, 0.0, 1.0)), 4, "rotation")
            interpolation = _fixed_bytes(
                _field(frame, ("interpolation",), DEFAULT_BONE_INTERPOLATION), 64, "interpolation"
            )
            handle = self._require_open()
            handle.write(name)
            handle.write(_pack_values("<I", (frame_number,)))
            handle.write(_pack_values("<fff", position))
            handle.write(_pack_values("<ffff", rotation))
            handle.write(interpolation)
        except BaseException as exc:
            self._cleanup()
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(exc, VmdStreamWriterError):
                raise
            if isinstance(exc, Exception):
                raise VmdStreamWriterError("failed to write bone frame") from exc
            raise

    write_bone_frame = write_bone

    def write_morph(self, frame: Any) -> None:
        try:
            self._require_open()
            name = _fixed_name(_field(frame, ("morph_name", "morphName", "name")), 15, "morph_name")
            frame_number = self._record_frame("morphs", _field(frame, ("frame_number", "frame"), 0))
            value = _f32(_field(frame, ("value", "weight"), 0.0), "value")
            handle = self._require_open()
            handle.write(name)
            handle.write(_pack_values("<I", (frame_number,)))
            handle.write(_pack_values("<f", (value,)))
        except BaseException as exc:
            self._cleanup()
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(exc, VmdStreamWriterError):
                raise
            if isinstance(exc, Exception):
                raise VmdStreamWriterError("failed to write morph frame") from exc
            raise

    write_morph_frame = write_morph

    def write_camera(self, frame: Any) -> None:
        try:
            self._require_open()
            frame_number = self._record_frame("cameras", _field(frame, ("frame_number", "frame"), 0))
            distance = _f32(_field(frame, ("distance",), 0.0), "distance")
            position = _vector(_field(frame, ("position",), (0.0, 0.0, 0.0)), 3, "position")
            rotation = _vector(_field(frame, ("rotation",), (0.0, 0.0, 0.0)), 3, "rotation")
            interpolation = _fixed_bytes(
                _field(frame, ("interpolation",), DEFAULT_CAMERA_INTERPOLATION), 24, "interpolation"
            )
            fov = _u32(_field(frame, ("viewing_angle", "view_angle", "fov"), 0), "viewing_angle")
            perspective = _perspective_byte(_field(frame, ("perspective",), 0))
            handle = self._require_open()
            handle.write(_pack_values("<I", (frame_number,)))
            handle.write(_pack_values("<f", (distance,)))
            handle.write(_pack_values("<fff", position))
            handle.write(_pack_values("<fff", rotation))
            handle.write(interpolation)
            handle.write(_pack_values("<I", (fov,)))
            handle.write(_pack_values("<B", (perspective,)))
        except BaseException as exc:
            self._cleanup()
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(exc, VmdStreamWriterError):
                raise
            if isinstance(exc, Exception):
                raise VmdStreamWriterError("failed to write camera frame") from exc
            raise

    write_camera_frame = write_camera

    def write_light(self, frame: Any) -> None:
        try:
            self._require_open()
            frame_number = self._record_frame("lights", _field(frame, ("frame_number", "frame"), 0))
            color = _vector(_field(frame, ("color",), (0.0, 0.0, 0.0)), 3, "color")
            position = _vector(_field(frame, ("position", "direction"), (0.0, 0.0, 0.0)), 3, "position")
            handle = self._require_open()
            handle.write(_pack_values("<I", (frame_number,)))
            handle.write(_pack_values("<fff", color))
            handle.write(_pack_values("<fff", position))
        except BaseException as exc:
            self._cleanup()
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(exc, VmdStreamWriterError):
                raise
            if isinstance(exc, Exception):
                raise VmdStreamWriterError("failed to write light frame") from exc
            raise

    write_light_frame = write_light

    def write_shadow(self, frame: Any) -> None:
        try:
            self._require_open()
            frame_number = self._record_frame("shadows", _field(frame, ("frame_number", "frame"), 0))
            mode = _u32(_field(frame, ("mode",), 0), "mode")
            if mode > 0xFF:
                raise ValueError("mode is outside the u8 range")
            distance = _f32(_field(frame, ("distance",), 0.0), "distance")
            handle = self._require_open()
            handle.write(_pack_values("<I", (frame_number,)))
            handle.write(_pack_values("<B", (mode,)))
            handle.write(_pack_values("<f", (distance,)))
        except BaseException as exc:
            self._cleanup()
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(exc, VmdStreamWriterError):
                raise
            if isinstance(exc, Exception):
                raise VmdStreamWriterError("failed to write shadow frame") from exc
            raise

    write_shadow_frame = write_shadow
    write_self_shadow = write_shadow
    write_self_shadow_frame = write_shadow

    def write_ik(self, frame: Any) -> None:
        try:
            self._require_open()
            frame_number = self._record_frame("ik", _field(frame, ("frame_number", "frame"), 0))
            visible = 1 if bool(_field(frame, ("visible", "show"), 0)) else 0
            states = _field(frame, ("ik_states", "ikStates", "states"), ())
            try:
                state_values = tuple(states)
            except TypeError as exc:
                raise ValueError("ik_states must be iterable") from exc
            if len(state_values) > 0xFFFFFFFF:
                raise ValueError("ik_states contains too many entries")
            encoded_states = []
            for state in state_values:
                if isinstance(state, Mapping):
                    state_name = _field(state, ("bone_name", "boneName", "name"))
                    enabled = _field(state, ("enabled", "show_flag", "show"), False)
                else:
                    try:
                        state_name, enabled = tuple(state)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("each IK state must contain name and enabled") from exc
                encoded_states.append((_fixed_name(state_name, 20, "ik_name"), 1 if bool(enabled) else 0))
            handle = self._require_open()
            handle.write(_pack_values("<I", (frame_number,)))
            handle.write(_pack_values("<B", (visible,)))
            handle.write(_pack_values("<I", (len(encoded_states),)))
            for name, enabled in encoded_states:
                handle.write(name)
                handle.write(_pack_values("<B", (enabled,)))
        except BaseException as exc:
            self._cleanup()
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(exc, VmdStreamWriterError):
                raise
            if isinstance(exc, Exception):
                raise VmdStreamWriterError("failed to write IK frame") from exc
            raise

    write_ik_frame = write_ik
    write_ik_show_hide = write_ik
    write_ik_show_hide_frame = write_ik
    write_property = write_ik
    write_property_frame = write_ik

    def write_frame(self, section: str, frame: Any) -> None:
        """Write ``frame`` through the method matching ``section``."""
        try:
            canonical = _SECTION_ALIASES.get(section, section)
            methods = {
                "bones": self.write_bone,
                "morphs": self.write_morph,
                "cameras": self.write_camera,
                "lights": self.write_light,
                "shadows": self.write_shadow,
                "ik": self.write_ik,
            }
            method = methods[canonical]
            method(frame)
        except BaseException as exc:
            self._cleanup()
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(exc, VmdStreamWriterError):
                raise
            if isinstance(exc, Exception):
                raise VmdStreamWriterError("unknown or invalid VMD section") from exc
            raise

    def finish(self) -> VmdStreamSummary:
        """Backpatch all section counts, flush, and return an immutable receipt."""
        if self._finished:
            self._cleanup()
            raise VmdStreamWriterError("VMD stream has already finished")
        try:
            handle = self._require_open()
            self._close_section()
            for index in range(self._section_index + 1, len(_SECTION_NAMES)):
                self._begin_section(index)
                self._close_section()
            handle.flush()
            os.fsync(handle.fileno())
            size = os.fstat(handle.fileno()).st_size
            handle.close()
            self._file = None
            digest = hashlib.sha256()
            with open(str(self._path), "rb") as readable:
                for chunk in iter(lambda: readable.read(1024 * 1024), b""):
                    digest.update(chunk)
            self._finished = True
            counts = MappingProxyType(dict(self._counts))
            bounds = MappingProxyType(
                {
                    name: VmdFrameBounds(self._minimums[name], self._maximums[name])
                    for name in _SECTION_NAMES
                }
            )
            return VmdStreamSummary(str(self._path), size, counts, bounds, digest.hexdigest())
        except BaseException as exc:
            self._cleanup()
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(exc, VmdStreamWriterError):
                raise
            if isinstance(exc, Exception):
                raise VmdStreamWriterError("failed to finish VMD stream") from exc
            raise

    close = finish
    finalize = finish


__all__ = [
    "VMD_MAGIC",
    "VmdFrameBounds",
    "VmdStreamSummary",
    "VmdStreamWriter",
    "VmdStreamWriterError",
]
