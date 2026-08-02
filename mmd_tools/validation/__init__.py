"""
Validation module for maya_mmd_tools.

This module provides validation utilities for MMD models,
including bone structure validation and naming convention checks.
"""

from .bone_validator import BoneValidator
from .export_validator import (
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
from .output_verifier import verify_model_output

__all__ = [
    "BoneValidator",
    "ISSUE_CATALOG",
    "ExportValidationError",
    "ExportValidationIssue",
    "ExportValidationReport",
    "IssueCatalogEntry",
    "PMD_MAX_VERTEX_COUNT",
    "UnknownValidationIssueError",
    "get_issue_catalog_entry",
    "validate_export_model",
    "validate_issue_catalog",
    "validate_model_data",
    "verify_model_output",
]
