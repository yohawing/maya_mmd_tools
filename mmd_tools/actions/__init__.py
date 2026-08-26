"""User action boundaries for UI presenter workflows.

The package exports action classes lazily so host-only validation code can use
pure action modules without importing Maya-dependent VMD and pose workflows.
"""

from importlib import import_module
from typing import Any


_EXPORT_MODULES = {
    "ExportModelAction": ".export_model_action",
    "ExportModelRequest": ".export_model_action",
    "ExportModelResult": ".export_model_action",
    "ExportVmdResult": ".bake_timeline_vmd_export_action",
    "ImportModelAction": ".import_model_action",
    "ImportModelRequest": ".import_model_action",
    "ImportModelResult": ".import_model_action",
    "ImportVmdAction": ".import_vmd_action",
    "ImportVmdRequest": ".import_vmd_action",
    "ImportVmdResult": ".import_vmd_action",
    "BakeTimelineVmdExportAction": ".bake_timeline_vmd_export_action",
    "BakeTimelineVmdExportError": ".bake_timeline_vmd_export_action",
    "BakeTimelineVmdExportCancelled": ".bake_timeline_vmd_export_action",
    "BakeTimelineVmdExportRaceError": ".bake_timeline_vmd_export_action",
    "VmdExportDiscovery": ".bake_timeline_vmd_export_action",
    "VmdExportPreparationBoundary": ".bake_timeline_vmd_export_action",
    "apply_sphere_map": ".material_shader_action",
    "BakeAnimationAction": ".pose_actions",
    "BakeAnimationRequest": ".pose_actions",
    "BakeAnimationResult": ".pose_actions",
    "CleanCurvesAction": ".pose_actions",
    "CleanCurvesRequest": ".pose_actions",
    "CleanCurvesResult": ".pose_actions",
    "CopyPoseAction": ".pose_actions",
    "CopyPoseRequest": ".pose_actions",
    "CopyPoseResult": ".pose_actions",
    "MirrorPoseAction": ".pose_actions",
    "MirrorPoseRequest": ".pose_actions",
    "MirrorPoseResult": ".pose_actions",
    "PastePoseAction": ".pose_actions",
    "PastePoseRequest": ".pose_actions",
    "PastePoseResult": ".pose_actions",
    "PoseTransform": ".pose_actions",
    "ResetPoseAction": ".pose_actions",
    "ResetPoseRequest": ".pose_actions",
    "ResetPoseResult": ".pose_actions",
}

__all__ = list(_EXPORT_MODULES)


def __getattr__(name: str) -> Any:
    """Load one public action symbol only when a caller requests it."""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose lazy public symbols to introspection tools."""
    return sorted(set(globals()) | set(__all__))
