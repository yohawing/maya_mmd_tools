"""Prepare one immutable Bake Timeline VMD export snapshot.

The preparation boundary is deliberately small and Maya-independent.  A Maya
adapter owns target discovery, scene collection, and revision watching; this
module only orders those callbacks and publishes a token when the collected
payload still belongs to the discovered scene.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import math
import time
from types import MappingProxyType
from typing import Any, Optional, Protocol, Tuple

from ..validation.snapshot import fingerprint_payload
from ..validation.export_validator import ExportValidationReport
from ..validation.vmd_validator import VMD_EXPORT_BAKE_TIMELINE, verify_vmd_output_streaming
from .prepared_vmd_artifact import (
    PreparedVmdArtifactReceipt,
    PreparedVmdStageSession,
)


PREPARED_VMD_EXPORT_SCHEMA_VERSION = 1

_IGNORED_REQUEST_KEYS = frozenset(
    {
        "ack",
        "ack_warning",
        "ack_warnings",
        "acknowledge",
        "acknowledge_warnings",
        "file_path",
        "output",
        "output_path",
        "report",
        "report_dir",
        "report_path",
        "validation_report_dir",
        "validation_report_evidence",
        "validation_report_path",
        "validation_report_provenance",
    }
)


class PrepareVmdExportError(ValueError):
    """Raised when a safe Bake Timeline preparation cannot be published."""


class PrepareVmdExportRaceError(PrepareVmdExportError):
    """Raised when the scene changes while a payload is being collected."""


class VmdExportPreparationBoundary(Protocol):
    """Maya-owned boundary for one complete preparation lifecycle."""

    def discover(self, request: Any) -> Any:
        """Return a route/dependency descriptor for the requested target."""

    def supports_streaming(self) -> bool:
        """Explicitly opt into sink-based collection."""

    def collect_to_sink(self, request: Any, sink: Any) -> Mapping[str, Any]:
        """Collect directly into a bounded VMD stream sink."""

    def arm(self, request: Any, discovery: Any) -> Any:
        """Arm a watcher before collection begins."""

    def current_revision(self, request: Any, discovery: Any) -> Any:
        """Return the current non-null scene revision."""

    def close(self) -> Any:
        """Close the active scene watch and release host-side state."""


@dataclass(frozen=True)
class PrepareVmdExportRequest:
    """Canonical request fields used by the preparation cache key.

    ``options`` may contain UI/workflow options.  Output, report, and warning
    acknowledgement fields are intentionally excluded from the semantic
    request fingerprint.
    """

    target_uuid: Optional[str] = None
    target_identity: Optional[str] = None
    scene_session_id: Optional[str] = None
    export_strategy: str = VMD_EXPORT_BAKE_TIMELINE
    frame_range: Tuple[float, float] = (0.0, 0.0)
    frame_step: float = 1.0
    scale: float = 1.0
    apply_scale: bool = True
    options: Mapping[str, Any] = field(default_factory=dict)

    def as_mapping(self) -> Mapping[str, Any]:
        """Return a JSON-shaped semantic view without output-only fields."""

        return {
            "target_uuid": self.target_uuid,
            "target_identity": self.target_identity,
            "scene_session_id": self.scene_session_id,
            "export_strategy": self.export_strategy,
            "frame_range": tuple(self.frame_range),
            "frame_step": self.frame_step,
            "scale": self.scale,
            "apply_scale": self.apply_scale,
            "options": _filter_request_value(self.options),
        }


@dataclass(frozen=True)
class VmdExportDiscovery:
    """Normalized route and dependency descriptor returned by discovery."""

    scene_session_id: str
    target_uuid: str
    target_identity: str
    dependency_closure_fingerprint: str
    cache_id: str = ""
    schema_version: int = PREPARED_VMD_EXPORT_SCHEMA_VERSION
    route: Any = None
    model_name: str = ""


@dataclass(frozen=True)
class PreparedVmdExportToken:
    """Opaque immutable receipt handle for one safely staged Bake Timeline export."""

    schema_version: int
    cache_id: str
    scene_session_id: str
    revision: str
    target_uuid: str
    target_identity: str
    export_strategy: str
    frame_range: Tuple[float, float]
    frame_step: float
    semantic_options_fingerprint: str
    payload_fingerprint: str
    dependency_closure_fingerprint: str
    staged_artifact: PreparedVmdArtifactReceipt = field(compare=True, hash=False)
    combined_validation_report: ExportValidationReport = field(compare=True, hash=False)

    @property
    def stage_receipt(self) -> PreparedVmdArtifactReceipt:
        """Return the verified private VMD stage owned by this token."""

        return self.staged_artifact

    @property
    def validation_report(self) -> ExportValidationReport:
        """Return the cached payload plus output verification report."""

        return self.combined_validation_report


@dataclass(frozen=True)
class PrepareVmdExportResult:
    """Result envelope; failed and partial results never contain a token."""

    status: str
    token: Optional[PreparedVmdExportToken] = None
    error: Optional[Exception] = None
    failure_report: Optional[ExportValidationReport] = None

    @property
    def succeeded(self) -> bool:
        return self.status == "published" and self.token is not None

    @property
    def report(self) -> Optional[ExportValidationReport]:
        """Expose the published or structured failure report."""

        if self.token is not None:
            return self.token.combined_validation_report
        return self.failure_report


@dataclass(frozen=True)
class PrepareVmdExportDiagnostics:
    """Small immutable timing/evidence envelope for one prepare attempt.

    The payload itself is intentionally absent.  This envelope is kept even
    when preparation fails so a smoke runner can distinguish a slow
    discovery, collector, or stage boundary without logging per-frame data.
    ``phase_timing`` is a mapping of stable phase names to wall-clock seconds.
    """

    status: str = "not_started"
    phase_timing: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))
    request_fingerprint: Optional[str] = None
    payload_fingerprint: Optional[str] = None
    error: Optional[str] = None
    backend: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-shaped copy for report writers."""

        return {
            "status": self.status,
            "phase_timing": dict(self.phase_timing),
            "request_fingerprint": self.request_fingerprint,
            "payload_fingerprint": self.payload_fingerprint,
            "error": self.error,
            "backend": _copy_diagnostics(self.backend),
        }


def _normal_key(key: Any) -> str:
    return str(key).strip().lower().replace("-", "_")


def _filter_request_value(value: Any) -> Any:
    """Drop only output/report/ack controls from a semantic request view."""

    if isinstance(value, Mapping):
        return {
            str(key): _filter_request_value(item)
            for key, item in value.items()
            if _normal_key(key) not in _IGNORED_REQUEST_KEYS
        }
    if isinstance(value, (list, tuple)):
        return tuple(_filter_request_value(item) for item in value)
    return value


def request_fingerprint(request: Any) -> str:
    """Fingerprint semantic request options, excluding output-only controls."""

    if isinstance(request, PrepareVmdExportRequest):
        semantic = request.as_mapping()
    elif isinstance(request, Mapping):
        semantic = _filter_request_value(request)
    else:
        semantic = {}
        for name in (
            "target_uuid",
            "target_identity",
            "scene_session_id",
            "export_strategy",
            "frame_range",
            "frame_step",
            "scale",
            "apply_scale",
            "options",
        ):
            if hasattr(request, name):
                semantic[name] = _filter_request_value(getattr(request, name))
        if hasattr(request, "file_path"):
            # Explicitly document that an ExportVmdRequest path is not a cache key.
            pass
        if not semantic and hasattr(request, "__dict__"):
            semantic = _filter_request_value(vars(request))
    return fingerprint_payload(_canonical_value(semantic))


def _read_field(value: Any, name: str, *aliases: str) -> Any:
    names = (name,) + aliases
    if isinstance(value, Mapping):
        for candidate in names:
            if candidate in value:
                return value[candidate]
        normalized = {_normal_key(key): item for key, item in value.items()}
        for candidate in names:
            if _normal_key(candidate) in normalized:
                return normalized[_normal_key(candidate)]
    else:
        for candidate in names:
            if hasattr(value, candidate):
                return getattr(value, candidate)
    options = value.get("options") if isinstance(value, Mapping) else getattr(value, "options", None)
    if isinstance(options, Mapping):
        return _read_field(options, name, *aliases)
    return None


def _require_identity(value: Any, name: str) -> str:
    if value is None or str(value).strip() == "":
        raise PrepareVmdExportError(f"{name} is required for a prepared VMD export")
    return str(value)


def _normalize_discovery(value: Any, request: Any) -> VmdExportDiscovery:
    if isinstance(value, VmdExportDiscovery):
        descriptor = value
    else:
        dependencies = _read_field(
            value,
            "dependency_closure_fingerprint",
            "closure_fingerprint",
            "dependency_fingerprint",
        )
        if dependencies is None:
            dependencies = _read_field(value, "dependencies", "dependency_closure")
            if dependencies is not None:
                dependencies = fingerprint_payload(_canonical_value(dependencies))
        descriptor = VmdExportDiscovery(
            scene_session_id=_require_identity(
                _read_field(value, "scene_session_id", "session_id")
                or _read_field(request, "scene_session_id", "session_id"),
                "scene_session_id",
            ),
            target_uuid=_require_identity(
                _read_field(value, "target_uuid", "target_id", "uuid")
                or _read_field(request, "target_uuid", "target_id", "uuid"),
                "target_uuid",
            ),
            target_identity=_require_identity(
                _read_field(value, "target_identity", "canonical_identity", "identity")
                or _read_field(request, "target_identity", "canonical_identity", "identity"),
                "target_identity",
            ),
            dependency_closure_fingerprint=_require_identity(
                dependencies,
                "dependency_closure_fingerprint",
            ),
            cache_id=str(_read_field(value, "cache_id") or ""),
            schema_version=int(
                _read_field(value, "schema_version", "schema")
                or PREPARED_VMD_EXPORT_SCHEMA_VERSION
            ),
            route=_read_field(value, "route", "target_route"),
            model_name=str(
                _read_field(value, "model_name", "vmd_model_name")
                or _read_field(request, "model_name", "vmd_model_name")
                or ""
            ),
        )
    _require_identity(descriptor.scene_session_id, "scene_session_id")
    _require_identity(descriptor.target_uuid, "target_uuid")
    _require_identity(descriptor.target_identity, "target_identity")
    _require_identity(
        descriptor.dependency_closure_fingerprint,
        "dependency_closure_fingerprint",
    )
    if descriptor.schema_version <= 0:
        raise PrepareVmdExportError("discovery schema_version must be positive")
    return descriptor


def _normalize_frame_options(request: Any) -> Tuple[Tuple[float, float], float, float, str]:
    export_strategy = str(
        _read_field(request, "export_strategy") or VMD_EXPORT_BAKE_TIMELINE
    ).lower()
    if export_strategy != VMD_EXPORT_BAKE_TIMELINE:
        raise PrepareVmdExportError("prepared VMD export supports Bake Timeline only")
    frame_range = _read_field(request, "frame_range")
    if frame_range is None:
        start = _read_field(request, "frame_start")
        end = _read_field(request, "frame_end")
        if start is not None and end is not None:
            frame_range = (start, end)
    if frame_range is None:
        frame_range = (0.0, 0.0)
    if isinstance(frame_range, (str, bytes)) or not isinstance(frame_range, Sequence) or len(frame_range) != 2:
        raise PrepareVmdExportError("frame_range must contain exactly two numbers")
    try:
        normalized_range = (float(frame_range[0]), float(frame_range[1]))
        raw_frame_step = _read_field(request, "frame_step", "step")
        raw_scale = _read_field(request, "scale", "apply_scale_value")
        frame_step = float(1.0 if raw_frame_step is None else raw_frame_step)
        scale = float(1.0 if raw_scale is None else raw_scale)
    except (TypeError, ValueError) as exc:
        raise PrepareVmdExportError("frame range, frame step, and scale must be numeric") from exc
    if not all(math.isfinite(value) for value in normalized_range + (frame_step, scale)):
        raise PrepareVmdExportError("frame range, frame step, and scale must be finite")
    if normalized_range[0] > normalized_range[1] or frame_step <= 0.0:
        raise PrepareVmdExportError("frame range must be ordered and frame_step must be positive")
    return normalized_range, frame_step, scale, export_strategy


def _canonical_value(value: Any) -> Any:
    """Convert JSON-shaped and VMD-like values into deterministic data."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("fingerprint input must contain finite numbers")
        return value
    if isinstance(value, bytes):
        return {"__bytes__": value.hex()}
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    attributes = getattr(value, "__dict__", None)
    if attributes is not None:
        return {
            "__type__": f"{type(value).__module__}.{type(value).__qualname__}",
            "attributes": _canonical_value(attributes),
        }
    raise TypeError(f"cannot fingerprint value of type {type(value).__name__}")


def _copy_diagnostics(value: Any) -> Any:
    """Recursively copy report-safe diagnostics without exposing live state."""

    if isinstance(value, Mapping):
        return {str(key): _copy_diagnostics(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_copy_diagnostics(item) for item in value]
    return value


def _freeze_diagnostics(value: Any) -> Any:
    """Recursively freeze diagnostics kept on the action."""

    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_diagnostics(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_diagnostics(item) for item in value)
    return value


def _revision_method(boundary: Any, request: Any, discovery: VmdExportDiscovery) -> Any:
    method = getattr(boundary, "current_revision", None)
    if not callable(method):
        raise PrepareVmdExportError(
            "preparation boundary must expose current_revision(request, discovery)"
        )
    return method(request, discovery)


def _arm_boundary(boundary: Any, request: Any, discovery: VmdExportDiscovery) -> None:
    boundary.arm(request, discovery)


def _prepare_boundary_for_collection(boundary: Any, request: Any) -> Any:
    """Run an optional host lifecycle before the first discovery.

    Maya may need to move an authoring Control Rig into its MMD-owned state
    before the dependency route is discovered.  Keeping this hook optional
    preserves the small, Maya-independent contract used by headless tests and
    non-Maya callers.
    """

    prepare = getattr(boundary, "prepare_for_collection", None)
    if not callable(prepare):
        return None
    return prepare(request)


def _restore_boundary_after_collection(boundary: Any, context: Any) -> Optional[Exception]:
    """Close the temporary watch and restore a host-side collection lifecycle.

    Restoration is attempted even when closing the old watch fails.  The
    caller treats either failure as fatal, so a partially restored scene can
    never be published through a prepared token.
    """

    close_error: Optional[Exception] = None
    close = getattr(boundary, "close", None)
    if callable(close):
        try:
            close()
        except Exception as exc:
            close_error = exc
    restore = getattr(boundary, "restore_after_collection", None)
    if not callable(restore):
        restore_error: Optional[Exception] = PrepareVmdExportError(
            "preparation boundary cannot restore temporary collection state"
        )
    else:
        try:
            restore(context)
        except Exception as exc:
            restore_error = exc
        else:
            restore_error = None
    if close_error is None:
        return restore_error
    if restore_error is None:
        return PrepareVmdExportError(
            f"closing the temporary collection watch failed: {close_error}"
        )
    return PrepareVmdExportError(
        "temporary collection cleanup failed: "
        f"watch close: {close_error}; scene restore: {restore_error}"
    )


class PrepareVmdExportAction:
    """Prepare one Bake Timeline payload through injected production boundaries."""

    def __init__(
        self,
        boundary: VmdExportPreparationBoundary,
    ):
        required_methods = (
            "discover",
            "supports_streaming",
            "collect_to_sink",
            "arm",
            "current_revision",
            "close",
        )
        if boundary is None or any(
            not callable(getattr(boundary, name, None)) for name in required_methods
        ):
            raise TypeError(
                "boundary must expose discover(request), supports_streaming(), "
                "collect_to_sink(request, sink), arm(request, discovery), "
                "current_revision(request, discovery), and close()"
            )
        if not bool(boundary.supports_streaming()):
            raise TypeError("boundary must support streaming VMD preparation")
        self._boundary = boundary
        # The action owns exactly one prepared approval.  Keeping this as an
        # identity (rather than a cache id or revision string) prevents a
        # discarded token from becoming valid again when Maya happens to
        # report the same scene revision later.
        self._active_token: Optional[PreparedVmdExportToken] = None
        self._pending_stage_session: Optional[PreparedVmdStageSession] = None
        self._boundary_open = False
        self._diagnostics = PrepareVmdExportDiagnostics()

    @property
    def active_token(self) -> Optional[PreparedVmdExportToken]:
        """Return the one token currently owned by this action, if any."""

        return self._active_token

    @property
    def diagnostics(self) -> PrepareVmdExportDiagnostics:
        """Return immutable diagnostics for the most recent prepare attempt."""

        return self._diagnostics

    @property
    def diagnostics_copy(self) -> dict[str, Any]:
        """Return a detached report-writer copy of :attr:`diagnostics`."""

        return self._diagnostics.as_dict()

    def can_prepare_for_collection(self, request: Any) -> bool:
        """Report whether the host can perform a temporary collection bake."""

        capability = getattr(self._boundary, "can_prepare_for_collection", None)
        if not callable(capability):
            return False
        return bool(capability(request))

    def invalidate(self, token: Optional[PreparedVmdExportToken] = None) -> bool:
        """Discard a token and close its host-side revision watch.

        Passing a token is an ownership check: a stale token from an earlier
        preparation must not be able to close or replace the current token.
        With no argument this closes the current boundary, making the method
        useful for presenter teardown and idempotent re-prepare cleanup.
        """

        if token is not None and self._active_token is not token:
            return False
        active_token = self._active_token
        owned = self._active_token is not None
        pending_session = self._pending_stage_session
        boundary_open = self._boundary_open
        if not owned and pending_session is None and not boundary_open:
            return False
        self._active_token = None
        self._pending_stage_session = None
        if pending_session is not None:
            pending_session.cleanup()
        if active_token is not None:
            active_token.staged_artifact.cleanup()
        close = getattr(self._boundary, "close", None)
        if callable(close):
            close()
        self._boundary_open = False
        return owned or pending_session is not None or boundary_open

    def close(self) -> bool:
        """Close the action boundary; repeated calls are safe."""

        return self.invalidate()

    @staticmethod
    def _stream_expected_range(metadata: Any) -> Tuple[int, int]:
        if not isinstance(metadata, Mapping):
            raise PrepareVmdExportError(
                "streaming VMD metadata must include a converted frame range"
            )
        value = metadata.get("validation_frame_range")
        if (
            not isinstance(value, (tuple, list))
            or len(value) != 2
            or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
            or value[0] < 0
            or value[1] < value[0]
            or value[1] > 0xFFFFFFFF
        ):
            raise PrepareVmdExportError(
                "streaming VMD metadata has an invalid converted frame range"
            )
        return value[0], value[1]

    @staticmethod
    def _validate_stream_counts(metadata: Mapping[str, Any], summary: Any) -> None:
        expected = metadata.get("section_counts")
        actual = getattr(summary, "counts", None)
        if not isinstance(expected, Mapping) or not isinstance(actual, Mapping):
            raise PrepareVmdExportError("streaming VMD section counts are unavailable")
        for section in ("bones", "morphs", "cameras", "lights", "shadows", "ik"):
            value = expected.get(section)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                or value != actual.get(section)
            ):
                raise PrepareVmdExportError(
                    "streaming VMD section count mismatch for {}".format(section)
                )

    def execute(self, request: Any) -> PrepareVmdExportResult:
        """Collect once and publish only when the scene stayed unchanged."""

        started = time.perf_counter()
        phase_timing: dict[str, float] = {}
        options_fingerprint: Optional[str] = None
        payload_fingerprint: Optional[str] = None
        staged_artifact: Optional[PreparedVmdArtifactReceipt] = None
        stream_metadata: Mapping[str, Any] = {}
        stream_session: Optional[PreparedVmdStageSession] = None
        lifecycle_context: Any = None
        lifecycle_active = False
        temporary_lifecycle = False
        status = "failed"
        error_text: Optional[str] = None

        def timed(name: str, begin: float) -> None:
            phase_timing[name] = round(time.perf_counter() - begin, 6)

        # A new preparation always supersedes the previous one.  This also
        # closes the old Maya revision watch before discovery/arm starts.
        self._diagnostics = PrepareVmdExportDiagnostics(status="running")
        self.invalidate()

        def restore_temporary_collection() -> Optional[Exception]:
            """Restore an automatic Control Rig bake exactly once."""

            nonlocal lifecycle_active, lifecycle_context
            if not lifecycle_active:
                return None
            context = lifecycle_context
            lifecycle_active = False
            lifecycle_context = None
            result = _restore_boundary_after_collection(self._boundary, context)
            self._boundary_open = False
            return result

        try:
            fingerprint_begin = time.perf_counter()
            frame_range, frame_step, scale, export_strategy = _normalize_frame_options(request)
            options_fingerprint = request_fingerprint(request)
            timed("request_fingerprint", fingerprint_begin)

            lifecycle_context = _prepare_boundary_for_collection(self._boundary, request)
            lifecycle_active = lifecycle_context is not None
            temporary_lifecycle = lifecycle_active

            discovery_begin = time.perf_counter()
            first = _normalize_discovery(self._boundary.discover(request), request)
            timed("first_discovery", discovery_begin)
            requested_uuid = _read_field(request, "target_uuid")
            requested_identity = _read_field(request, "target_identity")
            if requested_uuid is not None and str(requested_uuid) != first.target_uuid:
                raise PrepareVmdExportError("requested target_uuid does not match discovered target")
            if requested_identity is not None and str(requested_identity) != first.target_identity:
                raise PrepareVmdExportError("requested target_identity does not match discovered target")
            arm_begin = time.perf_counter()
            _arm_boundary(self._boundary, request, first)
            self._boundary_open = True
            timed("watcher_arm", arm_begin)

            revision_before_begin = time.perf_counter()
            revision_before = _revision_method(self._boundary, request, first)
            revision_before = _require_identity(revision_before, "revision_before")
            timed("revision_before", revision_before_begin)

            collect_begin = time.perf_counter()
            model_name = first.model_name or str(_read_field(request, "model_name") or "")
            stream_session = PreparedVmdStageSession(
                model_name,
                export_strategy=VMD_EXPORT_BAKE_TIMELINE,
                output_verifier=verify_vmd_output_streaming,
            )
            self._pending_stage_session = stream_session
            stream_metadata_value = self._boundary.collect_to_sink(request, stream_session)
            if stream_metadata_value is not None and not isinstance(
                stream_metadata_value, Mapping
            ):
                raise PrepareVmdExportError(
                    "streaming VMD backend returned invalid bounded metadata"
                )
            stream_metadata = stream_metadata_value or {}
            expected_range = self._stream_expected_range(stream_metadata)
            stream_session.set_expected_frame_range(expected_range)
            stream_summary = stream_session.finish_collection()
            self._validate_stream_counts(stream_metadata, stream_summary)
            timed("backend_collect", collect_begin)

            # A temporary Control Rig bake intentionally changes the route and
            # revision while collecting.  Restore it before the final
            # discovery, then arm a fresh watch so the token describes the
            # live EDIT/CONTROL_OWNED scene rather than the transient BAKED one.
            restoration_error = restore_temporary_collection()
            if restoration_error is not None:
                raise PrepareVmdExportError(
                    "automatic Control Rig bake could not be restored: "
                    f"{restoration_error}"
                )

            discovery_begin = time.perf_counter()
            second = _normalize_discovery(self._boundary.discover(request), request)
            timed("second_discovery", discovery_begin)
            if lifecycle_context is not None:
                raise PrepareVmdExportError("temporary collection lifecycle remained active")
            if temporary_lifecycle:
                # Re-arm after a temporary lifecycle.  The first watch belongs
                # to the transient BAKED graph and was closed before restore.
                _arm_boundary(self._boundary, request, second)
                self._boundary_open = True

            revision_after_begin = time.perf_counter()
            revision_after = _revision_method(self._boundary, request, second)
            revision_after = _require_identity(revision_after, "revision_after")
            timed("revision_after", revision_after_begin)
            if not temporary_lifecycle and revision_before != revision_after:
                raise PrepareVmdExportRaceError(
                    f"scene revision changed during VMD collection ({revision_before} -> {revision_after})"
                )
            if (
                first.scene_session_id != second.scene_session_id
                or first.target_uuid != second.target_uuid
                or first.target_identity != second.target_identity
                or first.model_name != second.model_name
            ):
                raise PrepareVmdExportRaceError("VMD route or dependency closure changed during collection")
            if not temporary_lifecycle and (
                first.dependency_closure_fingerprint != second.dependency_closure_fingerprint
            ):
                raise PrepareVmdExportRaceError("VMD route or dependency closure changed during collection")

            stage_begin = time.perf_counter()
            raw_loss_warning_required = bool(stream_metadata.get("raw_provenance"))
            staged_artifact = stream_session.promote(
                raw_loss_warning_required=raw_loss_warning_required
            )
            if not isinstance(staged_artifact, PreparedVmdArtifactReceipt):
                raise PrepareVmdExportError("VMD stream session returned an invalid receipt")
            staged_artifact.validate_identity()
            self._pending_stage_session = None
            combined_validation_report = staged_artifact.output_validation_report
            payload_fingerprint = staged_artifact.sha256
            timed("artifact_stage_verify", stage_begin)
            authority = second
            cache_id = authority.cache_id or _cache_id(
                authority.scene_session_id,
                authority.target_uuid,
                options_fingerprint or request_fingerprint(request),
                authority.dependency_closure_fingerprint,
            )
            token = PreparedVmdExportToken(
                schema_version=authority.schema_version,
                cache_id=cache_id,
                scene_session_id=authority.scene_session_id,
                revision=revision_after,
                target_uuid=authority.target_uuid,
                target_identity=authority.target_identity,
                export_strategy=export_strategy,
                frame_range=frame_range,
                frame_step=frame_step,
                semantic_options_fingerprint=options_fingerprint,
                payload_fingerprint=payload_fingerprint,
                dependency_closure_fingerprint=authority.dependency_closure_fingerprint,
                staged_artifact=staged_artifact,
                combined_validation_report=combined_validation_report,
            )
            self._active_token = token
            status = "published"
            return PrepareVmdExportResult(status="published", token=token)
        except PrepareVmdExportRaceError as exc:
            status = "partial"
            restoration_error = restore_temporary_collection()
            if restoration_error is not None:
                exc = PrepareVmdExportError(
                    f"{exc}; automatic Control Rig bake restoration failed: {restoration_error}"
                )
            error_text = f"{type(exc).__name__}: {exc}"
            if staged_artifact is not None:
                staged_artifact.cleanup()
            self.invalidate()
            return PrepareVmdExportResult(status="partial", error=exc)
        except Exception as exc:
            restoration_error = restore_temporary_collection()
            if restoration_error is not None:
                exc = PrepareVmdExportError(
                    f"{exc}; automatic Control Rig bake restoration failed: {restoration_error}"
                )
            error_text = f"{type(exc).__name__}: {exc}"
            if staged_artifact is not None:
                staged_artifact.cleanup()
            self.invalidate()
            return PrepareVmdExportResult(status="failed", error=exc)
        except BaseException:
            # Cancellation and host-level interrupts must preserve their type
            # while still releasing any private writer and revision watch.
            restoration_error = restore_temporary_collection()
            if staged_artifact is not None:
                staged_artifact.cleanup()
            self.invalidate()
            if restoration_error is not None:
                raise PrepareVmdExportError(
                    "automatic Control Rig bake restoration failed during cancellation: "
                    f"{restoration_error}"
                ) from restoration_error
            raise
        finally:
            phase_timing["total"] = round(time.perf_counter() - started, 6)
            backend_diagnostics = getattr(self._boundary, "diagnostics_copy", None)
            if callable(backend_diagnostics):
                backend_diagnostics = backend_diagnostics()
            elif backend_diagnostics is None:
                backend_diagnostics = getattr(self._boundary, "diagnostics", {})
            if not isinstance(backend_diagnostics, Mapping):
                backend_diagnostics = {}
            self._diagnostics = PrepareVmdExportDiagnostics(
                status=status,
                phase_timing=MappingProxyType(dict(phase_timing)),
                request_fingerprint=options_fingerprint,
                payload_fingerprint=payload_fingerprint,
                error=error_text,
                backend=_freeze_diagnostics(backend_diagnostics or {}),
            )

    def prepare(self, request: Any) -> PreparedVmdExportToken:
        """Return a token or raise the preparation error for direct callers."""

        result = self.execute(request)
        if result.succeeded:
            return result.token  # type: ignore[return-value]
        if result.error is not None:
            raise result.error
        raise PrepareVmdExportError("VMD preparation did not publish a token")

    def validate_token(self, request: Any, token: PreparedVmdExportToken) -> None:
        """Assert that a prepared payload still belongs to the live scene.

        An active token deliberately performs discovery and a revision read,
        but never collects.  ``current_revision`` is the Maya adapter's
        flush/read boundary, so a key edit queued by Maya cannot be mistaken
        for a fresh token.  A token is an approval for one semantic request
        only; output and report paths remain excluded by
        :func:`request_fingerprint`.
        """

        def stale(reason: str) -> None:
            # A token that reached the scene boundary but no longer matches
            # must not become valid again if the scene later returns to the
            # same revision.  Preserve a copied/tampered token for diagnostics
            # while closing only this action's currently owned token.
            if self._active_token is token:
                self.invalidate(token)
            raise PrepareVmdExportError(f"prepared VMD export token is stale: {reason}")

        if not isinstance(token, PreparedVmdExportToken):
            stale("token type is invalid")
        if self._active_token is not token:
            stale("token is not active")

        try:
            frame_range, frame_step, _scale, export_strategy = _normalize_frame_options(request)
            discovery = _normalize_discovery(self._boundary.discover(request), request)
            revision = _require_identity(
                _revision_method(self._boundary, request, discovery),
                "revision",
            )
        except PrepareVmdExportError as exc:
            stale(str(exc))
        except Exception as exc:
            stale(f"scene route could not be rediscovered: {type(exc).__name__}: {exc}")

        if token.schema_version != PREPARED_VMD_EXPORT_SCHEMA_VERSION:
            stale("schema version does not match")
        if token.scene_session_id != discovery.scene_session_id:
            stale("scene session does not match")
        if token.target_uuid != discovery.target_uuid:
            stale("target UUID does not match")
        if token.target_identity != discovery.target_identity:
            stale("target identity does not match")
        if token.dependency_closure_fingerprint != discovery.dependency_closure_fingerprint:
            stale("dependency closure does not match")
        if token.revision != revision:
            stale("scene revision does not match")
        if token.export_strategy != export_strategy:
            stale("export strategy does not match")
        if tuple(token.frame_range) != frame_range:
            stale("frame range does not match")
        if token.frame_step != frame_step:
            stale("frame step does not match")
        if token.semantic_options_fingerprint != request_fingerprint(request):
            stale("semantic request does not match")
        if not isinstance(token.staged_artifact, PreparedVmdArtifactReceipt):
            stale("staged artifact type is invalid")
        if token.payload_fingerprint != token.staged_artifact.sha256:
            stale("payload fingerprint does not match staged artifact")
        if not isinstance(token.combined_validation_report, ExportValidationReport):
            stale("validation report type is invalid")
        try:
            token.staged_artifact.validate_identity()
        except Exception as exc:
            stale(f"staged artifact identity is invalid: {exc}")


def _cache_id(*parts: str) -> str:
    value = "|".join(parts).encode("utf-8")
    return f"vmd-bake-timeline-cache:{hashlib.sha256(value).hexdigest()}"


__all__ = [
    "PREPARED_VMD_EXPORT_SCHEMA_VERSION",
    "PrepareVmdExportAction",
    "PrepareVmdExportDiagnostics",
    "PrepareVmdExportError",
    "PrepareVmdExportRequest",
    "PrepareVmdExportResult",
    "PrepareVmdExportRaceError",
    "PreparedVmdArtifactReceipt",
    "PreparedVmdExportToken",
    "VmdExportDiscovery",
    "VmdExportPreparationBoundary",
    "request_fingerprint",
]
