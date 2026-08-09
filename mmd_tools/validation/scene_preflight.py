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


MODEL_FORMATS = frozenset({"pmx"})
VMD_FORMATS = frozenset({"vmd"})
SUPPORTED_FORMATS = MODEL_FORMATS | VMD_FORMATS


@dataclass(frozen=True)
class ScenePreflightResult:
    """Scene facts and the deterministic report produced by preflight."""

    report: ExportValidationReport
    metadata: Dict[str, Any]

    @property
    def valid(self) -> bool:
        """Return whether scene collection may proceed."""
        return self.report.valid


def _issue(code: str, path: str, message: str) -> ExportValidationIssue:
    """Create one blocking scene-boundary issue."""
    return ExportValidationIssue(code, "fatal", True, path, message)


def _normalize_format(options: Mapping[str, Any]) -> str:
    """Resolve the requested format from options or output suffix."""
    explicit = str(options.get("export_format") or "").lower().lstrip(".")
    if explicit:
        return explicit
    return Path(str(options.get("file_path") or "")).suffix.lower().lstrip(".")


def _normalize_mode(export_format: str, options: Mapping[str, Any]) -> str:
    """Resolve canonical report mode names without exposing Mode B."""
    if export_format != "vmd":
        return "model"
    value = str(options.get("vmd_mode", options.get("mode", "C")) or "").upper()
    return {"VMD_MODE_A": "A", "VMD_MODE_C": "C"}.get(value, value)


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
        mode = _normalize_mode(export_format, options)
        issues = []
        target = _resolve_target(options, self._scene_service)
        require_current_model = bool(options.get("require_current_model", False))
        require_target = bool(options.get("require_target", True)) or require_current_model

        if export_format not in SUPPORTED_FORMATS:
            issues.append(
                _issue(
                    "SCENE_FORMAT_UNSUPPORTED",
                    "export_format",
                    f"export format {export_format or 'empty'} is not supported by the export workflow",
                )
            )

        if require_target and not target:
            issues.append(
                _issue(
                    "SCENE_TARGET_MISSING",
                    "target",
                    (
                        "export requires a live Current Model"
                        if require_current_model
                        else "export requires a live Maya model, mesh, or animation target"
                    ),
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
                            "SCENE_TARGET_STALE",
                            "target",
                            f"export target {target!r} no longer exists in the Maya scene",
                        )
                    )

        frame_range = _normalize_frame_range(options)
        if any(key in options for key in ("frame_range", "frame_start", "frame_end", "start_frame", "end_frame")):
            if frame_range is None or frame_range[0] < 0 or frame_range[1] < frame_range[0]:
                issues.append(
                    _issue(
                        "SCENE_FRAME_RANGE_INVALID",
                        "frame_range",
                        "frame range must contain non-negative ordered start and end frames",
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
                        "SCENE_FRAME_STEP_INVALID",
                        "frame_step",
                        "frame step must be a finite positive number",
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
                        "SCENE_SCALE_INVALID",
                        "scale",
                        "export scale must be a finite positive number",
                    )
                )

        output_path = Path(str(options.get("file_path") or ""))
        if not str(options.get("file_path") or "").strip():
            issues.append(_issue("SCENE_OUTPUT_PATH_INVALID", "file_path", "export output path is required"))
        elif output_path.exists() and output_path.is_dir():
            issues.append(_issue("SCENE_OUTPUT_PATH_INVALID", "file_path", "export output path is a directory"))
        expected_extension = export_format if export_format in SUPPORTED_FORMATS else None
        if expected_extension and output_path.suffix.lower().lstrip(".") != expected_extension:
            issues.append(
                _issue(
                    "SCENE_OUTPUT_EXTENSION_MISMATCH",
                    "file_path",
                    f"output extension must be .{expected_extension} for {expected_extension.upper()} export",
                )
            )
        source_path = str(options.get("source_path") or "")
        if source_path and output_path and Path(source_path).absolute() == output_path.absolute():
            issues.append(
                _issue(
                    "SCENE_OUTPUT_SAME_AS_SOURCE",
                    "file_path",
                    "export output must not replace the imported source asset",
                )
            )

        ownership: Mapping[str, Any] = {}
        if target:
            try:
                ownership = self._ownership_checker(target) or {}
            except Exception as exc:
                issues.append(
                    _issue(
                        "SCENE_OWNER_QUERY_FAILED",
                        "ownership",
                        f"scene ownership could not be inspected: {type(exc).__name__}",
                    )
                )
        control_rig = ownership.get("control_rig") if isinstance(ownership, Mapping) else None
        if isinstance(control_rig, Mapping):
            owner = str(control_rig.get("owner") or "").upper()
            state = str(control_rig.get("state") or "").upper()
            if owner == "CONTROL_OWNED" or state in {"EDIT", "CONVERTING"}:
                issues.append(
                    _issue(
                        "SCENE_OWNER_CONTROL_RIG",
                        "ownership.control_rig",
                        "Control Rig owns the authoring path; bake or restore to MMD Rig before export",
                    )
                )
        humanik = ownership.get("humanik") if isinstance(ownership, Mapping) else None
        blocked = getattr(humanik, "blocked", None)
        if blocked is None and isinstance(humanik, Mapping):
            blocked = humanik.get("blocked")
        if blocked:
            character = getattr(humanik, "character", None)
            if character is None and isinstance(humanik, Mapping):
                character = humanik.get("character")
            issues.append(
                _issue(
                    "SCENE_OWNER_HUMANIK",
                    "ownership.humanik",
                    f"HumanIK owns the export pose ({blocked}{f' on {character}' if character else ''}); bake or restore MMD Rig first",
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
            "mode": mode,
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
            report=ExportValidationReport(export_format or None, tuple(issues), mode=mode),
            metadata=metadata,
        )


__all__ = [
    "MODEL_FORMATS",
    "VMD_FORMATS",
    "SUPPORTED_FORMATS",
    "ScenePreflight",
    "ScenePreflightResult",
]
