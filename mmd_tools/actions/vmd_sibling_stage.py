"""Ephemeral sibling staging for one VMD export operation."""

from __future__ import annotations

from array import array
from collections.abc import Mapping
from dataclasses import dataclass
import math
import os
from pathlib import Path
import struct
import tempfile
from types import MappingProxyType
from typing import Any, Optional, Tuple

from ..core.native.mmd_anim_runtime_export import export_vmd_from_parts
from ..validation.export_validator import ExportValidationReport
from ..validation.vmd_validator import VMD_EXPORT_BAKE_TIMELINE, verify_vmd_output_streaming


class VmdSiblingStageError(ValueError):
    """Raised when the temporary VMD cannot be safely produced or verified."""


DEFAULT_BONE_INTERPOLATION = b"\x14" * 64
_SECTIONS = ("bones", "morphs", "cameras", "lights", "shadows", "ik")
_RECEIPT_COUNTS = {
    "bones": "bone_frames", "morphs": "morph_frames", "cameras": "camera_frames",
    "lights": "light_frames", "shadows": "shadow_frames", "ik": "ik_show_hide_frames",
}


def _field(value: Any, names: tuple[str, ...], default: Any = None) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    else:
        for name in names:
            if hasattr(value, name):
                return getattr(value, name)
    return default


def _u32(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise VmdSiblingStageError(f"{label} must be an unsigned integer")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise VmdSiblingStageError(f"{label} must be an unsigned integer") from exc
    if number != value or not 0 <= number <= 0xFFFFFFFF:
        raise VmdSiblingStageError(f"{label} is outside the u32 range")
    return number


def _f32(value: Any, label: str) -> float:
    try:
        number = float(value)
        struct.pack("<f", number)
    except (TypeError, ValueError, OverflowError, struct.error) as exc:
        raise VmdSiblingStageError(f"{label} must be a finite f32") from exc
    if not math.isfinite(number):
        raise VmdSiblingStageError(f"{label} must be a finite f32")
    return number


def _vector(value: Any, length: int, label: str) -> tuple[float, ...]:
    try:
        values = tuple(value)
    except TypeError as exc:
        raise VmdSiblingStageError(f"{label} must contain {length} values") from exc
    if len(values) != length:
        raise VmdSiblingStageError(f"{label} must contain {length} values")
    return tuple(_f32(item, f"{label}[{index}]") for index, item in enumerate(values))


def _fixed_bytes(value: Any, length: int, label: str) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise VmdSiblingStageError(f"{label} must be bytes-like")
    result = bytes(value)
    if len(result) != length:
        raise VmdSiblingStageError(f"{label} must contain exactly {length} bytes")
    return result


@dataclass(frozen=True)
class _Bounds:
    minimum: Optional[int]
    maximum: Optional[int]


@dataclass(frozen=True)
class VmdSiblingStageSummary:
    """Actual temporary output facts, valid only during the owning call."""

    file_path: str
    size: int
    counts: Mapping[str, int]
    frame_bounds: Mapping[str, _Bounds]

    @property
    def min_frame(self) -> Optional[int]:
        values = [item.minimum for item in self.frame_bounds.values() if item.minimum is not None]
        return min(values) if values else None

    @property
    def max_frame(self) -> Optional[int]:
        values = [item.maximum for item in self.frame_bounds.values() if item.maximum is not None]
        return max(values) if values else None

    @property
    def section_counts(self) -> Mapping[str, int]:
        return MappingProxyType({target: int(self.counts[source]) for source, target in _RECEIPT_COUNTS.items()})


class _VmdPartsSink:
    """Collect compact typed channels for the native VMD from-parts ABI."""

    _ALIASES = {
        "bone": "bones", "bone_frames": "bones", "morph": "morphs", "morph_frames": "morphs",
        "camera": "cameras", "camera_frames": "cameras", "light": "lights", "light_frames": "lights",
        "shadow": "shadows", "shadow_frames": "shadows", "self_shadow": "shadows",
        "self_shadow_frames": "shadows", "self_shadows": "shadows", "property": "ik",
        "properties": "ik", "ik_frames": "ik", "ik_show_hide_frames": "ik",
    }

    def __init__(self, model_name: str) -> None:
        self._section_index = 0
        self._model_name, self._model_name_bytes = self._name(model_name, 20, "model_name")
        self._bone_names: list[dict[str, Any]] = []
        self._morph_names: list[dict[str, Any]] = []
        self._bone_name_by_bytes: dict[bytes, int] = {}
        self._morph_name_by_bytes: dict[bytes, int] = {}
        self._bone_name_indices, self._bone_frames = array("I"), array("I")
        self._bone_translations, self._bone_rotations = array("f"), array("f")
        self._bone_interpolations = array("B")
        self._morph_name_indices, self._morph_frames, self._morph_weights = array("I"), array("I"), array("f")
        if array("I").itemsize != 4 or array("f").itemsize != 4:
            raise VmdSiblingStageError("native VMD SoA scalar widths are unsupported")
        self._camera_frames: list[dict[str, Any]] = []
        self._light_frames: list[dict[str, Any]] = []
        self._shadow_frames: list[dict[str, Any]] = []
        self._property_frames: list[dict[str, Any]] = []
        self._counts = {section: 0 for section in _SECTIONS}
        self._minimums = {section: None for section in _SECTIONS}
        self._maximums = {section: None for section in _SECTIONS}

    @staticmethod
    def _name(value: Any, width: int, label: str) -> tuple[str, bytes]:
        if isinstance(value, (bytes, bytearray, memoryview)):
            encoded = bytes(value)
            try:
                display = encoded.decode("cp932")
            except UnicodeDecodeError as exc:
                raise VmdSiblingStageError(f"{label} contains invalid CP932 bytes") from exc
        elif isinstance(value, str):
            try:
                encoded, display = value.encode("cp932"), value
            except UnicodeEncodeError as exc:
                raise VmdSiblingStageError(f"{label} cannot be encoded as CP932") from exc
        else:
            raise VmdSiblingStageError(f"{label} must be a string or bytes-like value")
        if b"\x00" in encoded:
            raise VmdSiblingStageError(f"{label} raw bytes contain NUL")
        result = encoded[:width]
        while result:
            try:
                result.decode("cp932")
            except UnicodeDecodeError:
                # Fixed-width VMD fields may end after the first byte of a
                # CP932 character.  Keep the largest decodable prefix rather
                # than silently turning the whole track name into an empty
                # string.
                result = result[:-1]
            else:
                break
        return display, result

    @classmethod
    def _canonical(cls, section: str) -> str:
        result = cls._ALIASES.get(str(section), str(section))
        if result not in _SECTIONS:
            raise VmdSiblingStageError(f"unknown VMD section: {section}")
        return result

    def begin_section(self, section: str) -> None:
        index = _SECTIONS.index(self._canonical(section))
        if index < self._section_index:
            raise VmdSiblingStageError("VMD sections must be written in canonical order")
        self._section_index = index

    def end_section(self) -> None:
        if self._section_index >= len(_SECTIONS) - 1:
            raise VmdSiblingStageError("cannot end an unopened or final VMD section")
        self._section_index += 1

    def _record(self, section: str, frame: int) -> None:
        self.begin_section(section)
        if self._counts[section] >= 0xFFFFFFFF:
            raise VmdSiblingStageError(f"{section} section contains too many frames")
        self._counts[section] += 1
        previous_minimum, previous_maximum = self._minimums[section], self._maximums[section]
        self._minimums[section] = frame if previous_minimum is None else min(previous_minimum, frame)
        self._maximums[section] = frame if previous_maximum is None else max(previous_maximum, frame)

    def _name_index(self, value: Any, bone: bool) -> int:
        name, raw = self._name(value, 15, "bone_name" if bone else "morph_name")
        table = self._bone_names if bone else self._morph_names
        lookup = self._bone_name_by_bytes if bone else self._morph_name_by_bytes
        index = lookup.get(raw)
        if index is None:
            index = len(table)
            table.append({"name": name, "nameBytes": list(raw)})
            lookup[raw] = index
        return index

    def write_frame(self, section: str, frame: Any) -> None:
        section = self._canonical(section)
        if section == "bones":
            number = _u32(_field(frame, ("frame_number", "frame"), 0), "frame_number")
            self._record(section, number)
            self._bone_name_indices.append(self._name_index(_field(frame, ("bone_name", "boneName", "name")), True))
            self._bone_frames.append(number)
            self._bone_translations.extend(_vector(_field(frame, ("position", "translation"), (0.0, 0.0, 0.0)), 3, "position"))
            self._bone_rotations.extend(_vector(_field(frame, ("rotation",), (0.0, 0.0, 0.0, 1.0)), 4, "rotation"))
            self._bone_interpolations.extend(_fixed_bytes(_field(frame, ("interpolation",), DEFAULT_BONE_INTERPOLATION), 64, "interpolation"))
        elif section == "morphs":
            number = _u32(_field(frame, ("frame_number", "frame"), 0), "frame_number")
            self._record(section, number)
            self._morph_name_indices.append(self._name_index(_field(frame, ("morph_name", "morphName", "name")), False))
            self._morph_frames.append(number)
            self._morph_weights.append(_f32(_field(frame, ("value", "weight"), 0.0), "value"))
        elif section == "cameras":
            number = _u32(_field(frame, ("frame_number", "frame"), 0), "frame_number")
            perspective = _field(frame, ("perspective",), 0)
            if isinstance(perspective, bool):
                perspective = 0 if perspective else 1
            if perspective not in (0, 1):
                raise VmdSiblingStageError("perspective must be bool or integer 0/1")
            self._record(section, number)
            self._camera_frames.append({"frame": number, "distance": _f32(_field(frame, ("distance",), 0.0), "distance"), "position": list(_vector(_field(frame, ("position",), (0.0, 0.0, 0.0)), 3, "position")), "rotation": list(_vector(_field(frame, ("rotation",), (0.0, 0.0, 0.0)), 3, "rotation")), "interpolation": list(_fixed_bytes(_field(frame, ("interpolation",), b"\x14" * 24), 24, "interpolation")), "fov": _u32(_field(frame, ("viewing_angle", "view_angle", "fov"), 0), "viewing_angle"), "perspective": perspective == 0})
        elif section == "lights":
            number = _u32(_field(frame, ("frame_number", "frame"), 0), "frame_number")
            self._record(section, number)
            self._light_frames.append({"frame": number, "color": list(_vector(_field(frame, ("color",), (0.0, 0.0, 0.0)), 3, "color")), "direction": list(_vector(_field(frame, ("position", "direction"), (0.0, 0.0, 0.0)), 3, "direction"))})
        elif section == "shadows":
            number, mode = _u32(_field(frame, ("frame_number", "frame"), 0), "frame_number"), _u32(_field(frame, ("mode",), 0), "mode")
            if mode > 0xFF:
                raise VmdSiblingStageError("mode is outside the u8 range")
            self._record(section, number)
            self._shadow_frames.append({"frame": number, "mode": mode, "distance": _f32(_field(frame, ("distance",), 0.0), "distance")})
        else:
            number = _u32(_field(frame, ("frame_number", "frame"), 0), "frame_number")
            states = []
            for state in tuple(_field(frame, ("ik_states", "ikStates", "states"), ())):
                name, enabled = (_field(state, ("bone_name", "boneName", "name")), _field(state, ("enabled", "show_flag", "show"), False)) if isinstance(state, Mapping) else tuple(state)
                display, raw = self._name(name, 20, "ik_name")
                states.append({"boneName": display, "boneNameBytes": list(raw), "enabled": bool(enabled)})
            self._record(section, number)
            self._property_frames.append({"frame": number, "visible": bool(_field(frame, ("visible", "show"), 0)), "ikStates": states})

    @property
    def counts(self) -> Mapping[str, int]:
        return dict(self._counts)

    @property
    def frame_bounds(self) -> Mapping[str, _Bounds]:
        return {section: _Bounds(self._minimums[section], self._maximums[section]) for section in _SECTIONS}

    def finish(self) -> bytes:
        return export_vmd_from_parts({"schema": "mmd-anim-vmd-parts", "version": 1, "modelName": self._model_name, "modelNameBytes": list(self._model_name_bytes), "boneNames": self._bone_names, "morphNames": self._morph_names, "cameraFrames": self._camera_frames, "lightFrames": self._light_frames, "selfShadowFrames": self._shadow_frames, "propertyFrames": self._property_frames}, self._bone_name_indices, self._bone_frames, self._bone_translations, self._bone_rotations, self._bone_interpolations, self._morph_name_indices, self._morph_frames, self._morph_weights)

    def abort(self) -> None:
        for items in (self._bone_name_indices, self._bone_frames, self._bone_translations, self._bone_rotations, self._bone_interpolations, self._morph_name_indices, self._morph_frames, self._morph_weights, self._camera_frames, self._light_frames, self._shadow_frames, self._property_frames):
            # ``array.clear`` is unavailable in the Maya 2024 Python runtime.
            # Slice deletion works for both the compact arrays and normal lists.
            del items[:]


class VmdSiblingStageSession:
    """Own one private sibling from collection until `os.replace` or cleanup."""

    def __init__(self, model_name: str, *, target_path: str, expected_frame_range: Optional[Tuple[int, int]] = None, output_verifier: Any = verify_vmd_output_streaming) -> None:
        self._target = Path(target_path).resolve(strict=False)
        self._target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=f".{self._target.stem}.", suffix=self._target.suffix or ".vmd", dir=str(self._target.parent))
        os.close(descriptor)
        self._path = Path(name)
        self._writer: Optional[_VmdPartsSink] = _VmdPartsSink(model_name)
        self._summary: Optional[VmdSiblingStageSummary] = None
        self._expected_frame_range = expected_frame_range
        self._output_verifier = output_verifier
        self._cleaned = False

    @property
    def file_path(self) -> str:
        return str(self._path)

    def __enter__(self) -> "VmdSiblingStageSession":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        self.cleanup()
        return False

    def _writer_or_fail(self) -> _VmdPartsSink:
        if self._writer is None:
            raise VmdSiblingStageError("VMD sibling stage is not writable")
        return self._writer

    def begin_section(self, section: str) -> None:
        self._writer_or_fail().begin_section(section)

    def end_section(self) -> None:
        self._writer_or_fail().end_section()

    def write_frame(self, section: str, frame: Any) -> None:
        self._writer_or_fail().write_frame(section, frame)

    def set_expected_frame_range(self, frame_range: Tuple[int, int]) -> None:
        if self._summary is not None:
            raise VmdSiblingStageError("VMD frame range cannot change after collection")
        self._expected_frame_range = frame_range

    def finish_collection(self, phase_callback: Any = None) -> VmdSiblingStageSummary:
        if self._summary is not None:
            return self._summary
        sink = self._writer_or_fail()
        if callable(phase_callback):
            phase_callback("encode", True)
        try:
            payload = sink.finish()
        except Exception:
            raise
        else:
            if callable(phase_callback):
                phase_callback("encode", False)
        if callable(phase_callback):
            phase_callback("flush", True)
        try:
            with self._path.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            raise
        else:
            if callable(phase_callback):
                phase_callback("flush", False)
        self._writer = None
        self._summary = VmdSiblingStageSummary(str(self._path), len(payload), MappingProxyType(dict(sink.counts)), MappingProxyType(dict(sink.frame_bounds)))
        return self._summary

    def verify(self) -> ExportValidationReport:
        summary = self._summary
        if summary is None:
            raise VmdSiblingStageError("VMD collection has not been finished")
        if not self._path.is_file() or self._path.stat().st_size != summary.size:
            raise VmdSiblingStageError("temporary VMD output changed before verification")
        kwargs: dict[str, Any] = {"expected_counts": summary.counts, "expected_bounds": summary.frame_bounds, "expected_size": summary.size}
        if self._expected_frame_range is not None:
            kwargs["expected_frame_range"] = self._expected_frame_range
        report = self._output_verifier(str(self._path), VMD_EXPORT_BAKE_TIMELINE, **kwargs)
        if not isinstance(report, ExportValidationReport):
            raise VmdSiblingStageError("VMD output verifier returned no validation report")
        if report.is_blocking or report.valid is False:
            raise VmdSiblingStageError(f"temporary VMD output verification blocked: {report}")
        return report

    def cleanup(self) -> bool:
        if self._cleaned:
            return False
        self._cleaned = True
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.abort()
        try:
            if self._path.exists() or self._path.is_symlink():
                self._path.unlink()
                return True
        except OSError:
            return False
        return False


__all__ = ["VmdSiblingStageError", "VmdSiblingStageSession", "VmdSiblingStageSummary"]
