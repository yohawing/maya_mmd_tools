"""Maya-scene preflight for the shared export workflow boundary.

This module checks scene ownership, target liveness, export options, and the
output path.  It deliberately does not inspect PMX indices or VMD byte
payloads; those checks belong to the Maya-independent payload validators.
"""

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from .export_validator import ExportValidationIssue, ExportValidationReport
from .vmd_validator import VMD_EXPORT_BAKE_TIMELINE


MODEL_FORMATS = frozenset({"pmx"})
VMD_FORMATS = frozenset({"vmd"})
SUPPORTED_FORMATS = MODEL_FORMATS | VMD_FORMATS
VMD_EXPORT_TARGETS = frozenset({"character", "camera", "light", "camera+light"})


@dataclass(frozen=True)
class ScenePreflightResult:
    """Scene facts and the deterministic report produced by preflight."""

    report: ExportValidationReport
    metadata: Dict[str, Any]

    @property
    def valid(self) -> bool:
        """Return whether scene collection may proceed."""
        return self.report.valid


def _issue(
    code: str,
    path: str,
    message: str,
    action: str = "",
    *,
    details: Optional[Mapping[str, Any]] = None,
) -> ExportValidationIssue:
    """Create one blocking scene-boundary issue."""
    issue_details = dict(details or {})
    issue_details.setdefault("field", path)
    return ExportValidationIssue(
        code,
        "fatal",
        True,
        path,
        message,
        action=action,
        details=issue_details,
    )


def _normalize_format(options: Mapping[str, Any]) -> str:
    """Resolve the requested format from options or output suffix."""
    explicit = str(options.get("export_format") or "").lower().lstrip(".")
    if explicit:
        return explicit
    return Path(str(options.get("file_path") or "")).suffix.lower().lstrip(".")


def _normalize_export_strategy(export_format: str, options: Mapping[str, Any]) -> str:
    """Resolve the semantic strategy for a VMD export request."""
    if export_format != "vmd":
        return "model"
    return str(options.get("export_strategy") or VMD_EXPORT_BAKE_TIMELINE).lower()


def _normalize_vmd_export_target(options: Mapping[str, Any]) -> Optional[str]:
    """Return the explicit VMD target enum, defaulting to Character."""

    if "export_target" not in options:
        return "character"
    value = str(options.get("export_target") or "").strip().lower()
    if value == "camera_light":
        value = "camera+light"
    return value or None


def _resolve_target(options: Mapping[str, Any], scene_service: Any) -> Optional[str]:
    """Resolve an explicit target, then a live scene selection when available.

    The legacy fallback is retained for headless/import callers that still use
    ``target_model``.  The Export presenter always supplies an explicit
    ``current_model_root`` and sets ``require_current_model`` so selection can
    never become the export authority.
    """
    if "current_model_root" in options:
        value = options.get("current_model_root")
        return str(value) if value else None
    if options.get("require_current_model"):
        return None
    for key in ("target_model", "model_root", "target_mesh", "mesh", "target"):
        value = options.get(key)
        if value:
            return str(value)
    if scene_service is not None:
        get_selected_nodes = getattr(scene_service, "get_selected_nodes", None)
        if callable(get_selected_nodes):
            selected = get_selected_nodes() or []
            if selected:
                return str(selected[0])
    return None


def _namespace_for_target(target: Optional[str]) -> Optional[str]:
    """Extract the Maya namespace from a long DAG target name."""
    if not target or ":" not in target:
        return None
    namespace = target.rsplit(":", 1)[0]
    return namespace.split("|")[-1] or None


def _normalize_frame_range(options: Mapping[str, Any]) -> Optional[Tuple[int, int]]:
    """Read an explicit frame range from either supported option shape."""
    value = options.get("frame_range")
    if value is None and "frame_start" in options and "frame_end" in options:
        value = (options.get("frame_start"), options.get("frame_end"))
    if value is None and "start_frame" in options and "end_frame" in options:
        value = (options.get("start_frame"), options.get("end_frame"))
    if value is None:
        return None
    try:
        return int(value[0]), int(value[1])
    except (IndexError, KeyError, TypeError, ValueError, OverflowError):
        return None


def _default_ownership_checker(target: str) -> Dict[str, Any]:
    """Read known Control Rig and HumanIK ownership facts in Maya."""
    result: Dict[str, Any] = {}
    try:
        from ..core.mmd_control_rig_builder import read_mmd_control_rig_metadata

        result["control_rig"] = read_mmd_control_rig_metadata(target)
    except Exception:
        result["control_rig"] = None
    try:
        from ..core.humanik_retarget import describe_humanik_import_lock

        result["humanik"] = describe_humanik_import_lock(target)
    except Exception:
        result["humanik"] = None
    return result


class ScenePreflight:
    """Check Maya scene facts before collector and payload validation."""

    def __init__(
        self,
        *,
        scene_service: Any = None,
        ownership_checker: Optional[Callable[[str], Mapping[str, Any]]] = None,
        scene_revision_getter: Optional[Callable[[], Any]] = None,
        source_scene_getter: Optional[Callable[[], Any]] = None,
    ):
        self._scene_service = scene_service
        self._ownership_checker = ownership_checker or _default_ownership_checker
        self._scene_revision_getter = scene_revision_getter
        self._source_scene_getter = source_scene_getter

    def run(self, options: Mapping[str, Any]) -> ScenePreflightResult:
        """Return a report and scene provenance for one export request."""
        options = dict(options or {})
        export_format = _normalize_format(options)
        export_strategy = _normalize_export_strategy(export_format, options)
        issues = []
        export_target = _normalize_vmd_export_target(options) if export_format == "vmd" else None
        target = _resolve_target(options, self._scene_service)
        require_current_model = bool(options.get("require_current_model", False))
        require_target = bool(options.get("require_target", True)) or require_current_model

        if export_format not in SUPPORTED_FORMATS:
            issues.append(
                _issue(
                    "EXPORT_OPTIONS_INVALID",
                    "export_format",
                    f"export format {export_format or 'empty'} is not supported by the export workflow",
                    details={"format": export_format or "empty"},
                )
            )
        elif export_format == "vmd" and export_target not in VMD_EXPORT_TARGETS:
            issues.append(
                _issue(
                    "EXPORT_OPTIONS_INVALID",
                    "export_target",
                    f"VMD export target {export_target or 'empty'} is not supported",
                    "Choose Character, Camera, Light, or Camera+Light.",
                    details={"export_target": export_target},
                )
            )

        if require_target and not target:
            issues.append(
                _issue(
                    "SCENE_INVALID",
                    "target",
                    (
                        "export requires a live Current Model"
                        if require_current_model
                        else "export requires a live Maya model, mesh, or animation target"
                    ),
                    details={"target": target, "require_current_model": require_current_model},
                )
            )
        elif target and self._scene_service is not None:
            object_exists = getattr(self._scene_service, "object_exists", None)
            if callable(object_exists):
                try:
                    exists = bool(object_exists(target))
                except Exception:
                    exists = False
                if not exists:
                    issues.append(
                        _issue(
                            "STALE_STATE",
                            "target",
                            f"export target {target!r} no longer exists in the Maya scene",
                            details={"target": target},
                        )
                    )

        frame_range = _normalize_frame_range(options)
        if any(key in options for key in ("frame_range", "frame_start", "frame_end", "start_frame", "end_frame")):
            if frame_range is None or frame_range[0] < 0 or frame_range[1] < frame_range[0]:
                issues.append(
                    _issue(
                        "EXPORT_OPTIONS_INVALID",
                        "frame_range",
                        "frame range must contain non-negative ordered start and end frames",
                        details={
                            "frame_range": list(frame_range) if frame_range is not None else None,
                        },
                    )
                )

        if "frame_step" in options:
            try:
                frame_step = float(options.get("frame_step"))
            except (TypeError, ValueError, OverflowError):
                frame_step = 0.0
            if not math.isfinite(frame_step) or frame_step <= 0.0:
                issues.append(
                    _issue(
                        "EXPORT_OPTIONS_INVALID",
                        "frame_step",
                        "frame step must be a finite positive number",
                        details={"actual_value": repr(options.get("frame_step"))},
                    )
                )

        if "scale" in options or "apply_scale" in options:
            scale_value = options.get("scale", 1.0)
            try:
                scale = float(scale_value)
            except (TypeError, ValueError, OverflowError):
                scale = 0.0
            if not math.isfinite(scale) or scale <= 0.0:
                issues.append(
                    _issue(
                        "EXPORT_OPTIONS_INVALID",
                        "scale",
                        "export scale must be a finite positive number",
                        details={"actual_value": repr(scale_value)},
                    )
                )

        output_path_text = str(options.get("file_path") or "")
        output_path = Path(output_path_text)
        expected_extension = export_format if export_format in SUPPORTED_FORMATS else None
        if not output_path_text.strip():
            issues.append(
                _issue(
                    "EXPORT_OPTIONS_INVALID",
                    "file_path",
                    "export output path is required",
                    details={"path": output_path_text},
                )
            )
        elif output_path.exists() and output_path.is_dir():
            issues.append(
                _issue(
                    "EXPORT_OPTIONS_INVALID",
                    "file_path",
                    "export output path is a directory",
                    details={"path": output_path_text},
                )
            )
        elif expected_extension and output_path.suffix.lower().lstrip(".") != expected_extension:
            issues.append(
                _issue(
                    "EXPORT_OPTIONS_INVALID",
                    "file_path",
                    f"output extension must be .{expected_extension} for {expected_extension.upper()} export",
                    details={
                        "expected_suffix": f".{expected_extension}",
                        "actual_suffix": output_path.suffix.lower(),
                    },
                )
            )
        source_path = str(options.get("source_path") or "")
        if source_path and output_path and Path(source_path).absolute() == output_path.absolute():
            issues.append(
                _issue(
                        "EXPORT_OPTIONS_INVALID",
                    "file_path",
                    "export output must not replace the imported source asset",
                    details={"path": output_path_text, "source_path": source_path},
                )
            )

        ownership: Mapping[str, Any] = {}
        if target:
            try:
                ownership = self._ownership_checker(target) or {}
            except Exception as exc:
                issues.append(
                    _issue(
                        "COLLECTION_FAILED",
                        "ownership",
                        f"scene ownership could not be inspected: {type(exc).__name__}",
                        details={"phase": "ownership", "exception_type": type(exc).__name__},
                    )
                )
        # Ownership determines which animation path may be sampled. PMX model
        # export does not collect timeline motion, so an active authoring rig
        # must not block the model payload.
        if export_format == "vmd" and export_target == "character":
            control_rig = (
                ownership.get("control_rig")
                if isinstance(ownership, Mapping)
                else None
            )
            if isinstance(control_rig, Mapping):
                owner = str(control_rig.get("owner") or "").upper()
                state = str(control_rig.get("state") or "").upper()
                if owner == "CONTROL_OWNED" or state in {"EDIT", "CONVERTING"}:
                    issues.append(
                        _issue(
                            "OWNERSHIP_CONFLICT",
                            "ownership.control_rig",
                            "Control Rig owns the authoring path, but its direct VMD export route could not be resolved",
                            "Repair the Control Rig export mapping, or switch to MMD Rig only if direct export is not required.",
                            details={
                                "owner": "control_rig",
                                "aggregation_discriminator": "ownership_control_rig",
                            },
                        )
                    )
            humanik = (
                ownership.get("humanik")
                if isinstance(ownership, Mapping)
                else None
            )
            blocked = getattr(humanik, "blocked", None)
            if blocked is None and isinstance(humanik, Mapping):
                blocked = humanik.get("blocked")
            if blocked:
                character = getattr(humanik, "character", None)
                if character is None and isinstance(humanik, Mapping):
                    character = humanik.get("character")
                issues.append(
                    _issue(
                        "OWNERSHIP_CONFLICT",
                        "ownership.humanik",
                        f"HumanIK owns the export pose ({blocked}{f' on {character}' if character else ''}); bake or restore MMD Rig first",
                        details={
                            "owner": "humanik",
                            "aggregation_discriminator": "ownership_humanik",
                        },
                    )
                )

        scene_revision = options.get("scene_revision")
        if scene_revision is None and self._scene_revision_getter is not None:
            try:
                scene_revision = self._scene_revision_getter()
            except Exception:
                scene_revision = None
        source_scene = options.get("source_scene")
        if source_scene is None and self._source_scene_getter is not None:
            try:
                source_scene = self._source_scene_getter()
            except Exception:
                source_scene = None
        metadata = {
            "schema_version": 1,
            "format": export_format or None,
            "export_strategy": export_strategy,
            "export_target": export_target,
            "target_identity": target,
            "namespace": _namespace_for_target(target),
            "source_scene": str(source_scene) if source_scene else None,
            "scene_revision": str(scene_revision) if scene_revision is not None else None,
            "frame_range": list(frame_range) if frame_range is not None else None,
            "frame_step": options.get("frame_step", 1),
            "apply_scale": bool(options.get("apply_scale", True)),
            "output_path": str(output_path) if str(options.get("file_path") or "").strip() else None,
        }
        return ScenePreflightResult(
            report=ExportValidationReport(
                export_format or None,
                tuple(issues),
                mode=export_strategy,
            ),
            metadata=metadata,
        )


__all__ = [
    "MODEL_FORMATS",
    "VMD_FORMATS",
    "SUPPORTED_FORMATS",
    "VMD_EXPORT_TARGETS",
    "ScenePreflight",
    "ScenePreflightResult",
]
