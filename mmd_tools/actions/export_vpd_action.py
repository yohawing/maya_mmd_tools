"""Action boundary for one-shot current-pose VPD export."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import tempfile
from typing import Any, List, Optional

from ..core.logger import get_logger
from ..validation.export_validator import (
    ExportValidationError,
    ExportValidationIssue,
    ExportValidationReport,
)
from ..validation.snapshot import fingerprint_payload


logger = get_logger(__name__)
_DEFAULT_COLLECTOR = object()


@dataclass
class ExportVpdRequest:
    """Request data for current-pose VPD export."""

    file_path: str
    options: dict


@dataclass
class ExportVpdResult:
    """Result data returned by current-pose VPD export."""

    exported_path: Optional[str] = None
    succeeded: bool = False
    status_message: str = ""
    error: Optional[Exception] = None
    warnings: List[Any] = field(default_factory=list)
    validation_report: Optional[ExportValidationReport] = None
    payload_fingerprint: Optional[str] = None
    cancelled: bool = False


class _ExportVpdCancelled(RuntimeError):
    """Internal control flow for a requested pre-publication cancellation."""


def _pose_payload(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return a stable, JSON-like fingerprint view of a VPD payload."""
    return [
        {
            "name": str(pose["name"]),
            "translation": [float(value) for value in pose["translation"]],
            "rotation": [float(value) for value in pose["rotation"]],
        }
        for pose in data["bones"]
    ]


def _poses_equivalent(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Compare a writer roundtrip within VPD's six-decimal precision."""
    left_bones = left.get("bones") or []
    right_bones = right.get("bones") or []
    if len(left_bones) != len(right_bones):
        return False
    for left_pose, right_pose in zip(left_bones, right_bones):
        if str(left_pose.get("name")) != str(right_pose.get("name")):
            return False
        for left_values, right_values in (
            (left_pose.get("translation") or [], right_pose.get("translation") or []),
            (left_pose.get("rotation") or [], right_pose.get("rotation") or []),
        ):
            if len(left_values) != len(right_values):
                return False
            if any(abs(float(a) - float(b)) > 1.0e-5 for a, b in zip(left_values, right_values)):
                return False
    return True


def _validate_vpd_data(data: Any) -> ExportValidationReport:
    """Validate the writer-facing VPD shape before creating a temporary file."""
    issues = []
    if not isinstance(data, Mapping):
        issues.append(
            ExportValidationIssue(
                "INPUT_INVALID", "fatal", True, "bones",
                "VPD payload must be an mmd-anim JSON object",
                details={"aggregation_discriminator": "payload_shape"},
            )
        )
        return ExportValidationReport("vpd", tuple(issues), mode="current_pose")
    bones = data.get("bones")
    if not isinstance(bones, Sequence) or isinstance(bones, (str, bytes, bytearray)):
        bones = []
        issues.append(
            ExportValidationIssue(
                "INPUT_INVALID", "fatal", True, "bones",
                "VPD bones must be an array",
                details={"aggregation_discriminator": "payload_shape"},
            )
        )
    if not bones:
        issues.append(
            ExportValidationIssue(
                "INPUT_INVALID", "fatal", True, "bones",
                "VPD current-pose export requires at least one MMD bone pose",
                details={"aggregation_discriminator": "payload_shape"},
            )
        )
    bone_count = data.get("boneCount")
    if isinstance(bone_count, bool) or not isinstance(bone_count, int) or bone_count != len(bones):
        issues.append(
            ExportValidationIssue(
                "INPUT_INVALID", "fatal", True, "boneCount",
                "VPD boneCount must match the bones array",
                details={"aggregation_discriminator": "payload_shape"},
            )
        )
    seen_names = {}
    for index, pose in enumerate(bones):
        path = f"bones[{index}]"
        if not isinstance(pose, Mapping):
            issues.append(
                ExportValidationIssue(
                    "INPUT_INVALID", "fatal", True, path,
                    "VPD bone pose must be an object",
                    details={"aggregation_discriminator": "bone"},
                )
            )
            continue
        name = pose.get("name")
        if not isinstance(name, str) or not name:
            issues.append(
                ExportValidationIssue(
                    "INPUT_INVALID", "fatal", True, f"{path}.name",
                    "VPD bone name must be a non-empty string",
                    details={"aggregation_discriminator": "bone"},
                )
            )
        else:
            try:
                name.encode("shift_jis")
            except UnicodeEncodeError:
                issues.append(
                    ExportValidationIssue(
                        "INPUT_INVALID", "fatal", True, f"{path}.name",
                        "VPD bone name cannot be represented in Shift-JIS",
                        details={"aggregation_discriminator": "bone"},
                    )
                )
            prior = seen_names.get(name)
            if prior is not None:
                issues.append(
                    ExportValidationIssue(
                        "REFERENCE_INVALID", "fatal", True, f"{path}.name",
                        f"duplicate VPD bone name {name!r}",
                        details={"aggregation_discriminator": "reference"},
                    )
                )
            seen_names[name] = index
        for field_name, expected_length in (("translation", 3), ("rotation", 4)):
            value = pose.get(field_name)
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or len(value) != expected_length:
                issues.append(
                    ExportValidationIssue(
                        "INPUT_INVALID", "fatal", True, f"{path}.{field_name}",
                        f"VPD {field_name} must contain {expected_length} numeric values",
                        details={"aggregation_discriminator": "bone"},
                    )
                )
                continue
            try:
                values = [float(item) for item in value]
            except (TypeError, ValueError, OverflowError):
                values = []
            if len(values) != expected_length or not all(math.isfinite(item) for item in values):
                issues.append(
                    ExportValidationIssue(
                        "INPUT_INVALID", "fatal", True, f"{path}.{field_name}",
                        f"VPD {field_name} must contain finite numeric values",
                        details={"aggregation_discriminator": "bone"},
                    )
                )
    return ExportValidationReport("vpd", tuple(issues), mode="current_pose")


def _append_report(
    report: ExportValidationReport, issue: ExportValidationIssue
) -> ExportValidationReport:
    """Keep earlier diagnostics while adding one terminal action issue."""
    return ExportValidationReport(
        "vpd",
        tuple(report.issues) + (issue,),
        mode="current_pose",
    )


class ExportVpdAction:
    """Write, parse-verify, and atomically publish one current pose."""

    def __init__(
        self,
        collector: Any = _DEFAULT_COLLECTOR,
        *,
        encoder=None,
        parser=None,
    ):
        if collector is _DEFAULT_COLLECTOR:
            from ..converters.vpd_scene_collector import VpdSceneCollector

            collector = VpdSceneCollector().collect
        if encoder is None or parser is None:
            from ..core.native import export_vpd_pose_json, parse_vpd_pose_json

            encoder = encoder or export_vpd_pose_json
            parser = parser or parse_vpd_pose_json
        self._collector = collector
        self._encoder = encoder
        self._parser = parser

    def can_prepare_for_collection(self, options: dict) -> bool:
        """Return whether the collector can resolve the requested owner route."""

        owner = getattr(self._collector, "__self__", None)
        can_collect = getattr(owner, "can_collect", None)
        return bool(can_collect(dict(options or {}))) if callable(can_collect) else False

    def execute(self, request: ExportVpdRequest) -> ExportVpdResult:
        """Run current-pose collection and atomic VPD publication."""
        request = ExportVpdRequest(
            str(Path(request.file_path).expanduser().resolve(strict=False)),
            dict(request.options or {}),
        )
        report = None
        temporary_path = None
        phase_callback = request.options.get("_phase_callback")
        cancel_requested = request.options.get("_cancel_requested")
        failure_stage = "collection"

        def phase(name: str, started: bool) -> None:
            if callable(phase_callback):
                phase_callback(name, started)

        def require_active() -> None:
            if callable(cancel_requested) and bool(cancel_requested()):
                raise _ExportVpdCancelled("VPD export cancelled")

        try:
            export_format = str(request.options.get("export_format") or "vpd").lower().lstrip(".")
            if export_format != "vpd":
                report = ExportValidationReport(
                    export_format or None,
                    (
                        ExportValidationIssue(
                            "EXPORT_OPTIONS_INVALID", "fatal", True, "export_format",
                            f"pose export format {export_format or 'empty'} is not supported",
                            details={"format": export_format or "empty"},
                        ),
                    ),
                    mode="current_pose",
                )
                raise ExportValidationError(report)
            phase("collect", True)
            require_active()
            try:
                data = request.options.get("vpd_data")
                if data is None:
                    if self._collector is None:
                        raise ValueError("VPD export requires a current-pose collector")
                    data = self._collector(dict(request.options))
            finally:
                phase("collect", False)
            report = _validate_vpd_data(data)
            if report.is_blocking:
                raise ExportValidationError(report)
            payload_fingerprint = fingerprint_payload(_pose_payload(data))
            require_active()

            target_path = Path(request.file_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_fd, temporary_path = tempfile.mkstemp(
                prefix=f".{target_path.stem}.", suffix=".vpd", dir=str(target_path.parent)
            )
            os.close(temporary_fd)
            failure_stage = "encode"
            phase("encode", True)
            require_active()
            try:
                encoded = self._encoder(data)
                if not isinstance(encoded, bytes) or not encoded:
                    raise ValueError("mmd-anim returned empty VPD output")
                with open(temporary_path, "wb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                phase("encode", False)
            failure_stage = "output_verify"
            phase("output_verify", True)
            require_active()
            try:
                with open(temporary_path, "rb") as handle:
                    reparsed = self._parser(handle.read())
                output_report = _validate_vpd_data(reparsed)
                if output_report.is_blocking:
                    report = _append_report(
                        report,
                        ExportValidationIssue(
                            "OUTPUT_VERIFY_FAILED", "fatal", True, "output",
                            "mmd-anim reparsed VPD output failed validation",
                            details={"aggregation_discriminator": "output_parse"},
                        ),
                    )
                    raise ExportValidationError(output_report)
                if not _poses_equivalent(reparsed, data):
                    report = ExportValidationReport(
                        "vpd",
                        tuple(report.issues)
                        + (
                            ExportValidationIssue(
                                "OUTPUT_VERIFY_FAILED", "fatal", True, "bones",
                                "reparsed VPD pose differs from the collected current pose",
                                details={"aggregation_discriminator": "output_parse"},
                            ),
                        ),
                        mode="current_pose",
                    )
                    raise ExportValidationError(report)
            finally:
                phase("output_verify", False)
            if not os.path.isfile(temporary_path) or os.path.getsize(temporary_path) == 0:
                raise FileNotFoundError("VPD writer did not create a non-empty temporary output")
            failure_stage = "replace"
            phase("replace", True)
            require_active()
            try:
                os.replace(temporary_path, request.file_path)
            finally:
                phase("replace", False)
            temporary_path = None
            return ExportVpdResult(
                exported_path=request.file_path,
                succeeded=True,
                status_message=f"Export complete: {request.file_path}",
                validation_report=report,
                payload_fingerprint=payload_fingerprint,
            )
        except _ExportVpdCancelled:
            return ExportVpdResult(
                status_message="Export cancelled",
                validation_report=report
                or ExportValidationReport("vpd", (), mode="current_pose"),
                cancelled=True,
            )
        except Exception as exc:
            if report is None:
                report = ExportValidationReport(
                    "vpd",
                    (
                        ExportValidationIssue(
                            "COLLECTION_FAILED", "fatal", True, "collector",
                            f"VPD current-pose collection failed: {type(exc).__name__}: {exc}",
                            details={"phase": "collection", "aggregation_discriminator": "collection"},
                        ),
                    ),
                    mode="current_pose",
                )
            elif not report.is_blocking:
                if failure_stage == "output_verify":
                    report = _append_report(
                        report,
                        ExportValidationIssue(
                            "OUTPUT_VERIFY_FAILED", "fatal", True, "output",
                            f"VPD output verification failed: {type(exc).__name__}: {exc}",
                            details={
                                "phase": failure_stage,
                                "exception_type": type(exc).__name__,
                                "aggregation_discriminator": "output_parse",
                            },
                        ),
                    )
                elif failure_stage in {"encode", "replace"}:
                    report = _append_report(
                        report,
                        ExportValidationIssue(
                            "OUTPUT_WRITE_FAILED", "fatal", True, "output",
                            f"VPD output write failed: {type(exc).__name__}: {exc}",
                            details={
                                "phase": failure_stage,
                                "exception_type": type(exc).__name__,
                                "aggregation_discriminator": "write",
                            },
                        ),
                    )
            return ExportVpdResult(
                status_message=f"Export failed: {exc}",
                error=exc,
                warnings=list(report.issues),
                validation_report=report,
            )
        finally:
            if temporary_path is not None:
                phase("cleanup", True)
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
                finally:
                    phase("cleanup", False)


__all__ = ["ExportVpdAction", "ExportVpdRequest", "ExportVpdResult"]
