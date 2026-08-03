"""
Validation module for maya_mmd_tools.

This module provides validation utilities for MMD models,
including bone structure validation and naming convention checks.
"""

from .bone_validator import BoneValidator
from .export_validator import (
    ExportValidationAcknowledgementRequired,
    ExportValidationError,
    ExportValidationIssue,
    ExportValidationReport,
    PMD_MAX_VERTEX_COUNT,
    validate_export_model,
    validate_model_data,
)
from .issue_catalog import (
    ISSUE_CATALOG,
    IssueCatalogEntry,
    UnknownValidationIssueError,
    get_issue_catalog_entry,
    validate_issue_catalog,
)
from .mmd_anim_verifier import verify_mmd_anim_asset
from .mmd_anim_binding_verifier import verify_mmd_anim_binding_asset
from .output_verifier import verify_model_output
from .report_artifacts import ValidationReportArtifactPaths, write_validation_report_artifacts
from .snapshot import ExportValidationSnapshot, fingerprint_payload
from .scene_preflight import ScenePreflight, ScenePreflightResult
from .vmd_validator import VMD_MODE_A, VMD_MODE_C, validate_vmd_data, verify_vmd_output

__all__ = [
    "BoneValidator",
    "ISSUE_CATALOG",
    "ExportValidationError",
    "ExportValidationAcknowledgementRequired",
    "ExportValidationIssue",
    "ExportValidationReport",
    "ExportValidationSnapshot",
    "ScenePreflight",
    "ScenePreflightResult",
    "IssueCatalogEntry",
    "PMD_MAX_VERTEX_COUNT",
    "UnknownValidationIssueError",
    "ValidationReportArtifactPaths",
    "VMD_MODE_A",
    "VMD_MODE_C",
    "get_issue_catalog_entry",
    "fingerprint_payload",
    "validate_export_model",
    "validate_issue_catalog",
    "validate_model_data",
    "verify_model_output",
    "verify_mmd_anim_asset",
    "verify_mmd_anim_binding_asset",
    "validate_vmd_data",
    "verify_vmd_output",
    "write_validation_report_artifacts",
]
