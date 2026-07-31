"""Shared outcome and warning classification for model and motion imports."""

from typing import Any, Dict, List


OUTCOME_SUCCESS = "success"
OUTCOME_PARTIAL = "partial"
OUTCOME_FATAL = "fatal"


def classify_import_outcome(root_node: Any, warnings: List[Any]) -> str:
    """Classify an import as success, partial, or fatal."""
    if not root_node:
        return OUTCOME_FATAL
    if warnings:
        return OUTCOME_PARTIAL
    return OUTCOME_SUCCESS


def warnings_from_options(options: Dict[str, Any]) -> List[Any]:
    """Collect structured warnings accumulated in an import profile."""
    profile = options.get("profile") if isinstance(options, dict) else None
    if not isinstance(profile, dict):
        return []
    warnings = list(profile.get("warnings") or [])
    warnings.extend(profile.get("vmd_converter", {}).get("warnings") or [])
    warnings.extend(profile.get("bone_converter", {}).get("warnings") or [])
    warnings.extend(profile.get("bone_converter", {}).get("rig_converter", {}).get("warnings") or [])
    warnings.extend(profile.get("bone_morph_runtime", {}).get("warnings") or [])
    warnings.extend(profile.get("texture_issues") or [])
    warnings.extend(profile.get("mesh_converter", {}).get("unresolved_textures") or [])
    return warnings
