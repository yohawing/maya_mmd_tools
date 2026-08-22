"""Private, verified VMD artifacts owned by a prepared export token.

The artifact is intentionally independent from the public export path.  A
Bake Timeline preparation can therefore pay the writer and verifier cost once while
the later Workflow export only needs to consume an identity-checked file.
"""

from __future__ import annotations

from array import array
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import shutil
import struct
import tempfile
from types import MappingProxyType
from typing import Any, Optional, Tuple

from ..validation.export_validator import ExportValidationReport
from ..validation.vmd_validator import VMD_EXPORT_BAKE_TIMELINE, verify_vmd_output_streaming
from ..core.native.mmd_anim_runtime_export import export_vmd_from_parts


PREPARED_VMD_ARTIFACT_SCHEMA_VERSION = 1

_STREAM_SECTION_TO_RECEIPT = {
    "bones": "bone_frames",
    "morphs": "morph_frames",
    "cameras": "camera_frames",
    "lights": "light_frames",
    "shadows": "shadow_frames",
    "ik": "ik_show_hide_frames",
}

class PreparedVmdArtifactError(ValueError):
    """Raised when a private staged VMD artifact cannot be trusted."""


DEFAULT_BONE_INTERPOLATION = b"\x14" * 64


def _field(value: Any, names: tuple[str, ...], default: Any = None) -> Any:
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
    return default


def _u32(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise PreparedVmdArtifactError("{} must be an unsigned integer".format(label))
    try:
        integer = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PreparedVmdArtifactError("{} must be an unsigned integer".format(label)) from exc
    if integer != value or integer < 0 or integer > 0xFFFFFFFF:
        raise PreparedVmdArtifactError("{} is outside the u32 range".format(label))
    return integer


def _f32(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PreparedVmdArtifactError("{} must be a finite number".format(label)) from exc
    if not math.isfinite(result):
        raise PreparedVmdArtifactError("{} must be a finite number".format(label))
    try:
        struct.pack("<f", result)
    except (OverflowError, struct.error) as exc:
        raise PreparedVmdArtifactError("{} does not fit an f32".format(label)) from exc
    return result


def _vector(value: Any, length: int, label: str) -> tuple[float, ...]:
    try:
        values = tuple(value)
    except TypeError as exc:
        raise PreparedVmdArtifactError("{} must contain {} values".format(label, length)) from exc
    if len(values) != length:
        raise PreparedVmdArtifactError("{} must contain {} values".format(label, length))
    return tuple(_f32(item, "{}[{}]".format(label, index)) for index, item in enumerate(values))


def _fixed_bytes(value: Any, length: int, label: str) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise PreparedVmdArtifactError("{} must be bytes-like".format(label))
    raw = bytes(value)
    if len(raw) != length:
        raise PreparedVmdArtifactError("{} must contain exactly {} bytes".format(label, length))
    return raw


def _perspective_byte(value: Any) -> int:
    if isinstance(value, bool):
        return 0 if value else 1
    if isinstance(value, int) and value in (0, 1):
        return value
    raise PreparedVmdArtifactError("perspective must be bool or integer 0/1")


@dataclass(frozen=True)
class _VmdFrameBounds:
    minimum: Optional[int]
    maximum: Optional[int]


@dataclass(frozen=True)
class _VmdStreamSummary:
    path: str
    size: int
    counts: Mapping[str, int]
    frame_bounds: Mapping[str, _VmdFrameBounds]
    sha256: str

    @property
    def min_frame(self) -> Optional[int]:
        values = [bound.minimum for bound in self.frame_bounds.values() if bound.minimum is not None]
        return min(values) if values else None

    @property
    def max_frame(self) -> Optional[int]:
        values = [bound.maximum for bound in self.frame_bounds.values() if bound.maximum is not None]
        return max(values) if values else None


class _VmdPartsSink:
    """Collect compact typed channels for the native VMD from-parts ABI."""

    _SECTIONS = ("bones", "morphs", "cameras", "lights", "shadows", "ik")
    _ALIASES = {
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
        "property": "ik",
        "properties": "ik",
        "ik_frames": "ik",
        "ik_show_hide_frames": "ik",
    }

    def __init__(self, model_name: str) -> None:
        # Match the public VMD section contract: the Bone section is
        # open immediately, so callers may explicitly close an empty leading
        # Bone section before writing Morph frames.
        self._section_index = 0
        self._model_name, self._model_name_bytes = self._name(model_name, 20, "model_name")
        self._bone_names: list[dict[str, Any]] = []
        self._morph_names: list[dict[str, Any]] = []
        self._bone_name_by_bytes: dict[bytes, int] = {}
        self._morph_name_by_bytes: dict[bytes, int] = {}
        # Keep the high-density channels in contiguous C-width storage.  A
        # Python list would turn every interpolation byte and float into an
        # individual Python object, defeating the from-parts memory boundary.
        self._bone_name_indices = array("I")
        self._bone_frames = array("I")
        self._bone_translations = array("f")
        self._bone_rotations = array("f")
        self._bone_interpolations = array("B")
        self._morph_name_indices = array("I")
        self._morph_frames = array("I")
        self._morph_weights = array("f")
        if array("I").itemsize != 4 or array("f").itemsize != 4:
            raise PreparedVmdArtifactError("native VMD SoA scalar widths are unsupported")
        self._camera_frames: list[dict[str, Any]] = []
        self._light_frames: list[dict[str, Any]] = []
        self._shadow_frames: list[dict[str, Any]] = []
        self._property_frames: list[dict[str, Any]] = []
        self._counts = {section: 0 for section in self._SECTIONS}
        self._minimums = {section: None for section in self._SECTIONS}
        self._maximums = {section: None for section in self._SECTIONS}

    @staticmethod
    def _name(value: Any, width: int, label: str) -> tuple[str, bytes]:
        if isinstance(value, (bytes, bytearray, memoryview)):
            encoded = bytes(value)
            try:
                display_name = encoded.decode("cp932")
            except UnicodeDecodeError as exc:
                raise PreparedVmdArtifactError("{} contains invalid CP932 bytes".format(label)) from exc
        elif isinstance(value, str):
            try:
                encoded = value.encode("cp932")
            except UnicodeEncodeError as exc:
                raise PreparedVmdArtifactError("{} cannot be encoded as CP932".format(label)) from exc
            display_name = value
        else:
            raise PreparedVmdArtifactError("{} must be a string or bytes-like value".format(label))
        if b"\x00" in encoded:
            raise PreparedVmdArtifactError("{} raw bytes contain NUL".format(label))
        # Preserve the legacy writer's byte-boundary truncation.  mmd-anim
        # accepts raw bytes only when the truncated field is itself valid
        # CP932; if the boundary leaves a lead byte, an empty raw field tells
        # its writer to encode ``display_name`` and apply the same byte cut.
        truncated = encoded[:width]
        try:
            truncated.decode("cp932")
        except UnicodeDecodeError:
            truncated = b""
        return display_name, truncated

    @classmethod
    def _canonical(cls, section: str) -> str:
        canonical = cls._ALIASES.get(str(section), str(section))
        if canonical not in cls._SECTIONS:
            raise PreparedVmdArtifactError("unknown VMD section: {}".format(section))
        return canonical

    def begin_section(self, section: str) -> None:
        canonical = self._canonical(section)
        index = self._SECTIONS.index(canonical)
        if index < self._section_index:
            raise PreparedVmdArtifactError("VMD sections must be written in canonical order")
        self._section_index = index

    def end_section(self) -> None:
        if self._section_index >= len(self._SECTIONS) - 1:
            raise PreparedVmdArtifactError("cannot end an unopened or final VMD section")
        self._section_index += 1

    def _record(self, section: str, frame: int) -> None:
        self.begin_section(section)
        count = self._counts[section]
        if count >= 0xFFFFFFFF:
            raise PreparedVmdArtifactError("{} section contains too many frames".format(section))
        self._counts[section] = count + 1
        self._minimums[section] = frame if self._minimums[section] is None else min(self._minimums[section], frame)
        self._maximums[section] = frame if self._maximums[section] is None else max(self._maximums[section], frame)

    def _name_index(self, value: Any, *, bone: bool) -> int:
        label = "bone_name" if bone else "morph_name"
        width = 15
        name, raw = self._name(value, width, label)
        table = self._bone_names if bone else self._morph_names
        by_bytes = self._bone_name_by_bytes if bone else self._morph_name_by_bytes
        index = by_bytes.get(raw)
        if index is None:
            index = len(table)
            table.append({"name": name, "nameBytes": list(raw)})
            by_bytes[raw] = index
        return index

    def write_frame(self, section: str, frame: Any) -> None:
        section = self._canonical(section)
        if section == "bones":
            self._write_bone(frame)
        elif section == "morphs":
            self._write_morph(frame)
        elif section == "cameras":
            self._write_camera(frame)
        elif section == "lights":
            self._write_light(frame)
        elif section == "shadows":
            self._write_shadow(frame)
        else:
            self._write_property(frame)

    def _write_bone(self, frame: Any) -> None:
        frame_number = _u32(_field(frame, ("frame_number", "frame"), 0), "frame_number")
        name = _field(frame, ("bone_name", "boneName", "name"))
        position = _vector(_field(frame, ("position", "translation"), (0.0, 0.0, 0.0)), 3, "position")
        rotation = _vector(_field(frame, ("rotation",), (0.0, 0.0, 0.0, 1.0)), 4, "rotation")
        interpolation = _fixed_bytes(_field(frame, ("interpolation",), DEFAULT_BONE_INTERPOLATION), 64, "interpolation")
        self._record("bones", frame_number)
        self._bone_name_indices.append(self._name_index(name, bone=True))
        self._bone_frames.append(frame_number)
        self._bone_translations.extend(position)
        self._bone_rotations.extend(rotation)
        self._bone_interpolations.extend(interpolation)

    def _write_morph(self, frame: Any) -> None:
        frame_number = _u32(_field(frame, ("frame_number", "frame"), 0), "frame_number")
        weight = _f32(_field(frame, ("value", "weight"), 0.0), "value")
        self._record("morphs", frame_number)
        self._morph_name_indices.append(self._name_index(_field(frame, ("morph_name", "morphName", "name")), bone=False))
        self._morph_frames.append(frame_number)
        self._morph_weights.append(weight)

    def _write_camera(self, frame: Any) -> None:
        frame_number = _u32(_field(frame, ("frame_number", "frame"), 0), "frame_number")
        interpolation = _fixed_bytes(_field(frame, ("interpolation",), b"\x14" * 24), 24, "interpolation")
        record = {
            "frame": frame_number,
            "distance": _f32(_field(frame, ("distance",), 0.0), "distance"),
            "position": list(_vector(_field(frame, ("position",), (0.0, 0.0, 0.0)), 3, "position")),
            "rotation": list(_vector(_field(frame, ("rotation",), (0.0, 0.0, 0.0)), 3, "rotation")),
            "interpolation": list(interpolation),
            "fov": _u32(_field(frame, ("viewing_angle", "view_angle", "fov"), 0), "viewing_angle"),
            "perspective": bool(_perspective_byte(_field(frame, ("perspective",), 0)) == 0),
        }
        self._record("cameras", frame_number)
        self._camera_frames.append(record)

    def _write_light(self, frame: Any) -> None:
        frame_number = _u32(_field(frame, ("frame_number", "frame"), 0), "frame_number")
        record = {
            "frame": frame_number,
            "color": list(_vector(_field(frame, ("color",), (0.0, 0.0, 0.0)), 3, "color")),
            "direction": list(_vector(_field(frame, ("position", "direction"), (0.0, 0.0, 0.0)), 3, "direction")),
        }
        self._record("lights", frame_number)
        self._light_frames.append(record)

    def _write_shadow(self, frame: Any) -> None:
        frame_number = _u32(_field(frame, ("frame_number", "frame"), 0), "frame_number")
        mode = _u32(_field(frame, ("mode",), 0), "mode")
        if mode > 0xFF:
            raise PreparedVmdArtifactError("mode is outside the u8 range")
        record = {"frame": frame_number, "mode": mode, "distance": _f32(_field(frame, ("distance",), 0.0), "distance")}
        self._record("shadows", frame_number)
        self._shadow_frames.append(record)

    def _write_property(self, frame: Any) -> None:
        frame_number = _u32(_field(frame, ("frame_number", "frame"), 0), "frame_number")
        states = _field(frame, ("ik_states", "ikStates", "states"), ())
        encoded_states = []
        for state in tuple(states):
            if isinstance(state, Mapping):
                value = _field(state, ("bone_name", "boneName", "name"))
                enabled = _field(state, ("enabled", "show_flag", "show"), False)
            else:
                value, enabled = tuple(state)
            name, raw = self._name(value, 20, "ik_name")
            encoded_states.append({"boneName": name, "boneNameBytes": list(raw), "enabled": bool(enabled)})
        record = {"frame": frame_number, "visible": bool(_field(frame, ("visible", "show"), 0)), "ikStates": encoded_states}
        self._record("ik", frame_number)
        self._property_frames.append(record)

    @property
    def counts(self) -> dict[str, int]:
        return dict(self._counts)

    @property
    def frame_bounds(self) -> dict[str, _VmdFrameBounds]:
        return {section: _VmdFrameBounds(self._minimums[section], self._maximums[section]) for section in self._SECTIONS}

    def finish(self) -> bytes:
        metadata = {
            "schema": "mmd-anim-vmd-parts",
            "version": 1,
            "modelName": self._model_name,
            "modelNameBytes": list(self._model_name_bytes),
            "boneNames": self._bone_names,
            "morphNames": self._morph_names,
            "cameraFrames": self._camera_frames,
            "lightFrames": self._light_frames,
            "selfShadowFrames": self._shadow_frames,
            "propertyFrames": self._property_frames,
        }
        return export_vmd_from_parts(
            metadata,
            self._bone_name_indices,
            self._bone_frames,
            self._bone_translations,
            self._bone_rotations,
            self._bone_interpolations,
            self._morph_name_indices,
            self._morph_frames,
            self._morph_weights,
        )

    def abort(self) -> None:
        """Drop accumulated compact arrays after a failed/cancelled export."""

        self._bone_name_indices.clear()
        self._bone_frames.clear()
        self._bone_translations.clear()
        self._bone_rotations.clear()
        self._bone_interpolations.clear()
        self._morph_name_indices.clear()
        self._morph_frames.clear()
        self._morph_weights.clear()
        self._camera_frames.clear()
        self._light_frames.clear()
        self._shadow_frames.clear()
        self._property_frames.clear()


def _digest_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class PreparedVmdArtifactReceipt:
    """Immutable identity and lifecycle handle for one private VMD stage."""

    schema_version: int
    stage_directory: str
    file_path: str
    sha256: str
    size: int
    section_counts: Mapping[str, int]
    frame_bounds: Optional[Tuple[int, int]]
    output_validation_report: ExportValidationReport

    def __post_init__(self) -> None:
        """Freeze mappings even when a caller supplied a mutable dictionary."""

        object.__setattr__(self, "section_counts", MappingProxyType(dict(self.section_counts)))
        if not isinstance(self.output_validation_report, ExportValidationReport):
            raise TypeError("output_validation_report must be ExportValidationReport")

    def validate_identity(self) -> bool:
        """Verify that the owned stage still matches its published receipt."""

        if self.schema_version != PREPARED_VMD_ARTIFACT_SCHEMA_VERSION:
            raise PreparedVmdArtifactError("staged VMD artifact schema version is unsupported")
        if not self.sha256 or len(self.sha256) != 64:
            raise PreparedVmdArtifactError("staged VMD artifact digest is invalid")
        path = Path(self.file_path)
        stage_directory = Path(self.stage_directory)
        if path.parent != stage_directory:
            raise PreparedVmdArtifactError("staged VMD artifact path escaped its private directory")
        if path.is_symlink() or not path.is_file():
            raise PreparedVmdArtifactError("staged VMD artifact is missing")
        actual_size = path.stat().st_size
        if actual_size != self.size:
            raise PreparedVmdArtifactError("staged VMD artifact size changed")
        if _digest_file(path) != self.sha256:
            raise PreparedVmdArtifactError("staged VMD artifact digest changed")
        return True

    def cleanup(self) -> bool:
        """Remove the exact stage file and its private temporary directory."""

        removed = False
        path = Path(self.file_path)
        directory = Path(self.stage_directory)
        if path.parent != directory:
            return False
        try:
            if path.exists() or path.is_symlink():
                path.unlink()
                removed = True
        except FileNotFoundError:
            pass
        except OSError:
            return False
        try:
            directory.rmdir()
        except (FileNotFoundError, OSError):
            pass
        return removed


class PreparedVmdStageSession:
    """Own one incremental private VMD stage until it is promoted.

    The session is deliberately a small adapter around the mmd-anim typed-parts
    exporter.  It keeps only compact typed channels and low-density metadata,
    and exposes only ordered section writes.  A
    successful ``finish`` transfers the private directory to the returned
    :class:`PreparedVmdArtifactReceipt`; every other exit path removes it.
    """

    def __init__(
        self,
        model_name: str = "",
        *,
        export_strategy: str = VMD_EXPORT_BAKE_TIMELINE,
        output_verifier: Any = verify_vmd_output_streaming,
        expected_frame_range: Optional[Tuple[int, int]] = None,
    ) -> None:
        self._export_strategy = export_strategy
        self._output_verifier = output_verifier
        self._expected_frame_range = expected_frame_range
        self._stage_directory = Path(tempfile.mkdtemp(prefix="mmd-vmd-stage-"))
        self._file_path = self._stage_directory / "prepared.vmd"
        self._writer: Optional[Any] = None
        self._summary: Optional[Any] = None
        self._receipt: Optional[PreparedVmdArtifactReceipt] = None
        self._cleaned = False
        try:
            self._writer = _VmdPartsSink(model_name)
        except BaseException:
            self._cleanup()
            raise

    @property
    def stage_directory(self) -> str:
        """Return the private stage directory, including after promotion."""

        return str(self._stage_directory)

    @property
    def file_path(self) -> str:
        """Return the staged VMD path."""

        return str(self._file_path)

    def __enter__(self) -> "PreparedVmdStageSession":
        if self._cleaned and self._receipt is None:
            raise PreparedVmdArtifactError("VMD stage session is closed")
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        if self._receipt is None:
            self._cleanup()
        return False

    def _cleanup(self) -> None:
        """Release writer handles and remove this session's private stage."""

        if self._receipt is not None or self._cleaned:
            return
        self._cleaned = True
        writer = self._writer
        self._writer = None
        if writer is not None and self._summary is None:
            try:
                writer.abort()
            except BaseException:
                # Preserve the original write/finalize/cancellation exception.
                pass
        try:
            shutil.rmtree(str(self._stage_directory), ignore_errors=True)
        except BaseException:
            # Temporary-stage cleanup must not replace the triggering error.
            pass

    def cleanup(self) -> bool:
        """Abort this pending stage; repeated calls are harmless."""

        was_pending = self._receipt is None and not self._cleaned
        self._cleanup()
        return was_pending

    def _handle_failure(self) -> None:
        self._cleanup()

    def _writer_or_fail(self) -> Any:
        writer = self._writer
        if writer is None:
            raise PreparedVmdArtifactError("VMD stage session is not writable")
        return writer

    def begin_section(self, section: str) -> None:
        try:
            self._writer_or_fail().begin_section(section)
        except BaseException:
            self._handle_failure()
            raise

    def end_section(self) -> None:
        try:
            self._writer_or_fail().end_section()
        except BaseException:
            self._handle_failure()
            raise

    def write_frame(self, section: str, frame: Any) -> None:
        """Write one frame in canonical VMD section order."""

        try:
            self._writer_or_fail().write_frame(section, frame)
        except BaseException:
            self._handle_failure()
            raise

    def set_expected_frame_range(self, frame_range: Tuple[int, int]) -> None:
        """Set converted VMD bounds before the writer is finalized."""

        if self._summary is not None or self._receipt is not None or self._cleaned:
            raise PreparedVmdArtifactError(
                "VMD stage frame range cannot change after collection"
            )
        self._expected_frame_range = frame_range

    def finish_collection(self) -> Any:
        """Flush the writer and return its bounded summary.

        This does not promote the stage.  A context that exits after this
        method without calling ``promote`` still removes the private stage.
        """

        if self._summary is not None:
            return self._summary
        try:
            sink = self._writer_or_fail()
            payload = sink.finish()
            with self._file_path.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            digest = _digest_file(self._file_path)
            summary = _VmdStreamSummary(
                str(self._file_path),
                len(payload),
                MappingProxyType(sink.counts),
                MappingProxyType(sink.frame_bounds),
                digest,
            )
            self._writer = None
            self._summary = summary
            return summary
        except BaseException:
            self._handle_failure()
            raise

    def _verification_kwargs(self, summary: Any) -> dict[str, Any]:
        result = {
            "expected_counts": summary.counts,
            "expected_bounds": summary.frame_bounds,
            "expected_sha256": summary.sha256,
            "expected_size": summary.size,
        }
        if self._expected_frame_range is not None:
            result["expected_frame_range"] = self._expected_frame_range
        return result

    def _verify(self, summary: Any) -> ExportValidationReport:
        verifier = self._output_verifier
        if verifier is None:
            verifier = verify_vmd_output_streaming
        report = verifier(
            str(self._file_path),
            self._export_strategy,
            **self._verification_kwargs(summary),
        )
        if not isinstance(report, ExportValidationReport):
            raise PreparedVmdArtifactError("VMD output verifier returned no validation report")
        if report.is_blocking or report.valid is False:
            raise PreparedVmdArtifactError(
                "staged VMD output verification blocked: {}".format(report)
            )
        return report

    def _assert_summary_identity(self, summary: Any) -> None:
        """Reject tampering before or during the bounded verification pass."""

        if not self._file_path.is_file() or self._file_path.stat().st_size != summary.size:
            raise PreparedVmdArtifactError("staged VMD output changed before promotion")
        if _digest_file(self._file_path) != summary.sha256:
            raise PreparedVmdArtifactError("staged VMD output changed before promotion")

    def promote(self) -> PreparedVmdArtifactReceipt:
        """Verify and transfer stage ownership to an immutable receipt."""

        if self._receipt is not None:
            return self._receipt
        try:
            summary = self._summary
            if summary is None:
                raise PreparedVmdArtifactError("VMD collection has not been finished")
            self._assert_summary_identity(summary)
            report = self._verify(summary)
            self._assert_summary_identity(summary)
            counts = {
                receipt_name: int(summary.counts.get(stream_name, 0))
                for stream_name, receipt_name in _STREAM_SECTION_TO_RECEIPT.items()
            }
            frame_bounds = None
            if summary.min_frame is not None and summary.max_frame is not None:
                frame_bounds = (summary.min_frame, summary.max_frame)
            receipt = PreparedVmdArtifactReceipt(
                schema_version=PREPARED_VMD_ARTIFACT_SCHEMA_VERSION,
                stage_directory=str(self._stage_directory),
                file_path=str(self._file_path),
                sha256=summary.sha256,
                size=summary.size,
                section_counts=MappingProxyType(counts),
                frame_bounds=frame_bounds,
                output_validation_report=report,
            )
            self._receipt = receipt
            self._writer = None
            self._cleaned = True
            return receipt
        except BaseException:
            self._handle_failure()
            raise

__all__ = [
    "PREPARED_VMD_ARTIFACT_SCHEMA_VERSION",
    "PreparedVmdArtifactError",
    "PreparedVmdArtifactReceipt",
    "PreparedVmdStageSession",
]
