"""Prepare one immutable Mode C VMD export snapshot.

The preparation boundary is deliberately small and Maya-independent.  A Maya
adapter owns target discovery, scene collection, and revision watching; this
module only orders those callbacks and publishes a token when the collected
payload still belongs to the discovered scene.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import copy
import hashlib
import math
import time
from types import MappingProxyType
from typing import Any, Optional, Protocol, Tuple

from ..validation.snapshot import fingerprint_payload


VMD_MODE_C = "C"
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
    """Raised when a safe Mode C preparation cannot be published."""


class PrepareVmdExportRaceError(PrepareVmdExportError):
    """Raised when the scene changes while a payload is being collected."""


class VmdExportPreparationBackend(Protocol):
    """Maya-owned discovery and collection boundary."""

    def discover(self, request: Any) -> Any:
        """Return a route/dependency descriptor for the requested target."""

    def collect(self, request: Any) -> Any:
        """Collect one payload from the currently discovered scene."""


class VmdExportRevisionProvider(Protocol):
    """Maya-owned scene revision/watcher boundary."""

    def arm(self, request: Any, discovery: Any) -> Any:
        """Arm a watcher before collection begins."""

    def current_revision(self, request: Any, discovery: Any) -> Any:
        """Return the current non-null scene revision."""


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
    mode: str = VMD_MODE_C
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
            "mode": self.mode,
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


class FrozenVmdDataView:
    """Read-only deep snapshot of a VMD-like payload.

    ``VmdData`` and its frame classes are mutable Python objects.  Keeping
    those objects directly in a frozen token would therefore only freeze the
    token shell.  This view recursively freezes object attributes and exposes
    ``copy_for_export`` for the writer-owned mutable copy.
    """

    __slots__ = ("_value", "_fingerprint")

    def __init__(self, value: Any):
        object.__setattr__(self, "_value", _freeze_value(value))
        object.__setattr__(self, "_fingerprint", fingerprint_payload(_canonical_value(value)))

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("FrozenVmdDataView is immutable")

    def __getattr__(self, name: str) -> Any:
        value = self._value
        if isinstance(value, _FrozenObject):
            return getattr(value, name)
        raise AttributeError(name)

    def __getitem__(self, key: Any) -> Any:
        return self._value[key]

    def __repr__(self) -> str:
        return f"FrozenVmdDataView({self._value!r})"

    def __hash__(self) -> int:
        return hash(self._fingerprint)

    @property
    def fingerprint(self) -> str:
        """Return the deterministic digest of the frozen payload."""

        return self._fingerprint

    def copy_for_export(self) -> Any:
        """Return a new mutable payload owned by an exporter."""

        return _thaw_value(self._value)


@dataclass(frozen=True)
class PreparedVmdExportToken:
    """Opaque immutable handle for one safely collected Mode C payload."""

    schema_version: int
    cache_id: str
    scene_session_id: str
    revision: str
    target_uuid: str
    target_identity: str
    mode: str
    frame_range: Tuple[float, float]
    frame_step: float
    semantic_options_fingerprint: str
    prepared_payload: FrozenVmdDataView
    payload_fingerprint: str
    dependency_closure_fingerprint: str

    @property
    def payload(self) -> FrozenVmdDataView:
        """Alias used by action callers that treat the token as a payload view."""

        return self.prepared_payload

    @property
    def vmd_data(self) -> FrozenVmdDataView:
        """Explicit VmdData-view alias for exporter integrations."""

        return self.prepared_payload

    def copy_for_export(self) -> Any:
        """Return a writer-owned mutable copy of the prepared payload."""

        return self.prepared_payload.copy_for_export()


@dataclass(frozen=True)
class PrepareVmdExportResult:
    """Result envelope; failed and partial results never contain a token."""

    status: str
    token: Optional[PreparedVmdExportToken] = None
    error: Optional[Exception] = None

    @property
    def succeeded(self) -> bool:
        return self.status == "published" and self.token is not None

    @property
    def published(self) -> bool:
        return self.succeeded


@dataclass(frozen=True)
class PrepareVmdExportDiagnostics:
    """Small immutable timing/evidence envelope for one prepare attempt.

    The payload itself is intentionally absent.  This envelope is kept even
    when preparation fails so a smoke runner can distinguish a slow
    discovery, collector, or freeze boundary without logging per-frame data.
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
            "mode",
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
    mode = str(_read_field(request, "mode", "vmd_mode") or VMD_MODE_C).upper()
    if mode != VMD_MODE_C:
        raise PrepareVmdExportError("prepared VMD export supports Mode C only")
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
    return normalized_range, frame_step, scale, mode


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


class _FrozenObject:
    __slots__ = ("_type", "_attributes")

    def __init__(self, original_type: type, attributes: Mapping[str, Any]):
        object.__setattr__(self, "_type", original_type)
        object.__setattr__(self, "_attributes", MappingProxyType(dict(attributes)))

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("prepared VMD payload is immutable")

    def __getattr__(self, name: str) -> Any:
        try:
            return self._attributes[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __repr__(self) -> str:
        return f"_FrozenObject({self._type.__name__}, {dict(self._attributes)!r})"


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_value(item) for item in value)
    attributes = getattr(value, "__dict__", None)
    if attributes is not None:
        return _FrozenObject(type(value), {key: _freeze_value(item) for key, item in attributes.items()})
    return value


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


def _thaw_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_value(item) for item in value]
    if isinstance(value, _FrozenObject):
        result = object.__new__(value._type)
        for key, item in value._attributes.items():
            setattr(result, key, _thaw_value(item))
        return result
    return copy.deepcopy(value)


def _revision_method(provider: Any, request: Any, discovery: VmdExportDiscovery) -> Any:
    for method_name in ("current_revision", "revision", "get_revision", "read_revision"):
        method = getattr(provider, method_name, None)
        if callable(method):
            return method(request, discovery)
    if callable(provider):
        return provider(request, discovery)
    raise PrepareVmdExportError("revision provider must expose current_revision(request, discovery)")


def _arm_revision_provider(provider: Any, request: Any, discovery: VmdExportDiscovery) -> None:
    for method_name in ("arm", "arm_watcher", "watch"):
        method = getattr(provider, method_name, None)
        if callable(method):
            method(request, discovery)
            return
    # A revision-only provider is valid; the revision read still forms the
    # before/after TOCTOU boundary.


class PrepareVmdExportAction:
    """Prepare one Mode C payload through injected production boundaries."""

    def __init__(self, backend: VmdExportPreparationBackend, revision_provider: Any):
        if backend is None or not callable(getattr(backend, "discover", None)) or not callable(
            getattr(backend, "collect", None)
        ):
            raise TypeError("backend must expose discover(request) and collect(request)")
        if revision_provider is None:
            raise TypeError("revision_provider is required")
        self._backend = backend
        self._revision_provider = revision_provider
        # The action owns exactly one prepared approval.  Keeping this as an
        # identity (rather than a cache id or revision string) prevents a
        # discarded token from becoming valid again when Maya happens to
        # report the same scene revision later.
        self._active_token: Optional[PreparedVmdExportToken] = None
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

    def invalidate(self, token: Optional[PreparedVmdExportToken] = None) -> bool:
        """Discard a token and close its host-side revision watch.

        Passing a token is an ownership check: a stale token from an earlier
        preparation must not be able to close or replace the current token.
        With no argument this closes the current boundary, making the method
        useful for presenter teardown and idempotent re-prepare cleanup.
        """

        if token is not None and self._active_token is not token:
            return False
        owned = self._active_token is not None
        if not owned and not self._boundary_open:
            return False
        self._active_token = None
        closed: set[int] = set()
        for owner in (self._backend, self._revision_provider):
            close = getattr(owner, "close", None)
            if not callable(close) or id(owner) in closed:
                continue
            closed.add(id(owner))
            close()
        self._boundary_open = False
        return owned or bool(closed)

    def close(self) -> bool:
        """Close the action boundary; repeated calls are safe."""

        return self.invalidate()

    def execute(self, request: Any) -> PrepareVmdExportResult:
        """Collect once and publish only when the scene stayed unchanged."""

        started = time.perf_counter()
        phase_timing: dict[str, float] = {}
        options_fingerprint: Optional[str] = None
        payload_fingerprint: Optional[str] = None
        status = "failed"
        error_text: Optional[str] = None

        def timed(name: str, begin: float) -> None:
            phase_timing[name] = round(time.perf_counter() - begin, 6)

        # A new preparation always supersedes the previous one.  This also
        # closes the old Maya revision watch before discovery/arm starts.
        self._diagnostics = PrepareVmdExportDiagnostics(status="running")
        self.invalidate()
        try:
            fingerprint_begin = time.perf_counter()
            frame_range, frame_step, scale, mode = _normalize_frame_options(request)
            options_fingerprint = request_fingerprint(request)
            timed("request_fingerprint", fingerprint_begin)

            discovery_begin = time.perf_counter()
            first = _normalize_discovery(self._backend.discover(request), request)
            timed("first_discovery", discovery_begin)
            requested_uuid = _read_field(request, "target_uuid")
            requested_identity = _read_field(request, "target_identity")
            if requested_uuid is not None and str(requested_uuid) != first.target_uuid:
                raise PrepareVmdExportError("requested target_uuid does not match discovered target")
            if requested_identity is not None and str(requested_identity) != first.target_identity:
                raise PrepareVmdExportError("requested target_identity does not match discovered target")
            arm_begin = time.perf_counter()
            _arm_revision_provider(self._revision_provider, request, first)
            self._boundary_open = True
            timed("watcher_arm", arm_begin)

            revision_before_begin = time.perf_counter()
            revision_before = _revision_method(self._revision_provider, request, first)
            revision_before = _require_identity(revision_before, "revision_before")
            timed("revision_before", revision_before_begin)

            collect_begin = time.perf_counter()
            payload = self._backend.collect(request)
            timed("backend_collect", collect_begin)

            discovery_begin = time.perf_counter()
            second = _normalize_discovery(self._backend.discover(request), request)
            timed("second_discovery", discovery_begin)

            revision_after_begin = time.perf_counter()
            revision_after = _revision_method(self._revision_provider, request, second)
            revision_after = _require_identity(revision_after, "revision_after")
            timed("revision_after", revision_after_begin)
            if revision_before != revision_after:
                raise PrepareVmdExportRaceError(
                    f"scene revision changed during VMD collection ({revision_before} -> {revision_after})"
                )
            if (
                first.scene_session_id != second.scene_session_id
                or first.target_uuid != second.target_uuid
                or first.target_identity != second.target_identity
                or first.dependency_closure_fingerprint != second.dependency_closure_fingerprint
            ):
                raise PrepareVmdExportRaceError("VMD route or dependency closure changed during collection")

            freeze_begin = time.perf_counter()
            prepared_payload = FrozenVmdDataView(payload)
            payload_fingerprint = prepared_payload.fingerprint
            timed("payload_freeze_fingerprint", freeze_begin)
            cache_id = first.cache_id or _cache_id(
                first.scene_session_id,
                first.target_uuid,
                options_fingerprint or request_fingerprint(request),
                first.dependency_closure_fingerprint,
            )
            token = PreparedVmdExportToken(
                schema_version=first.schema_version,
                cache_id=cache_id,
                scene_session_id=first.scene_session_id,
                revision=revision_after,
                target_uuid=first.target_uuid,
                target_identity=first.target_identity,
                mode=mode,
                frame_range=frame_range,
                frame_step=frame_step,
                semantic_options_fingerprint=options_fingerprint,
                prepared_payload=prepared_payload,
                payload_fingerprint=payload_fingerprint,
                dependency_closure_fingerprint=first.dependency_closure_fingerprint,
            )
            self._active_token = token
            status = "published"
            return PrepareVmdExportResult(status="published", token=token)
        except PrepareVmdExportRaceError as exc:
            status = "partial"
            error_text = f"{type(exc).__name__}: {exc}"
            self.invalidate()
            return PrepareVmdExportResult(status="partial", error=exc)
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            self.invalidate()
            return PrepareVmdExportResult(status="failed", error=exc)
        finally:
            phase_timing["total"] = round(time.perf_counter() - started, 6)
            backend_diagnostics = getattr(self._backend, "diagnostics_copy", None)
            if callable(backend_diagnostics):
                backend_diagnostics = backend_diagnostics()
            elif backend_diagnostics is None:
                backend_diagnostics = getattr(self._backend, "diagnostics", {})
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
            frame_range, frame_step, _scale, mode = _normalize_frame_options(request)
            discovery = _normalize_discovery(self._backend.discover(request), request)
            revision = _require_identity(
                _revision_method(self._revision_provider, request, discovery),
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
        if token.mode != mode:
            stale("mode does not match")
        if tuple(token.frame_range) != frame_range:
            stale("frame range does not match")
        if token.frame_step != frame_step:
            stale("frame step does not match")
        if token.semantic_options_fingerprint != request_fingerprint(request):
            stale("semantic request does not match")
        if not isinstance(token.prepared_payload, FrozenVmdDataView):
            stale("prepared payload type is invalid")
        if token.prepared_payload.fingerprint != token.payload_fingerprint:
            stale("payload fingerprint does not match")


def _cache_id(*parts: str) -> str:
    value = "|".join(parts).encode("utf-8")
    return f"vmd-c-cache:{hashlib.sha256(value).hexdigest()}"


__all__ = [
    "FrozenVmdDataView",
    "PREPARED_VMD_EXPORT_SCHEMA_VERSION",
    "PrepareVmdExportAction",
    "PrepareVmdExportDiagnostics",
    "PrepareVmdExportError",
    "PrepareVmdExportRequest",
    "PrepareVmdExportResult",
    "PrepareVmdExportRaceError",
    "PreparedVmdExportToken",
    "VmdExportDiscovery",
    "VmdExportPreparationBackend",
    "VmdExportRevisionProvider",
    "request_fingerprint",
]
