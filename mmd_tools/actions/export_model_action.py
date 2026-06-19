"""Action boundary for PMX/PMD model export execution."""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..core.logger import get_logger

logger = get_logger(__name__)

_EXPORT_NOT_IMPLEMENTED_WARNING = "PMX export is not implemented yet (scene data collection missing)"
_EXPORT_NOT_IMPLEMENTED_STATUS = "PMX export is not implemented yet (scene data collection is unsupported)"


@dataclass
class ExportModelRequest:
    """Request data for exporting a PMX/PMD model."""

    file_path: str
    options: Dict[str, Any]


@dataclass
class ExportModelResult:
    """Result data returned by PMX/PMD model export."""

    exported_path: Optional[str] = None
    succeeded: bool = False
    status_message: str = ""
    error: Optional[Exception] = None


class ExportModelAction:
    """Represent the Maya-side PMX/PMD model export boundary.

    Scene data collection is not implemented yet, so this action deliberately
    does not call an exporter. It preserves the current honest UI behavior while
    providing the future insertion point for scene collection and export.
    """

    def execute(self, request: ExportModelRequest) -> ExportModelResult:
        """Report the current unsupported export state without exporting."""
        logger.warning(_EXPORT_NOT_IMPLEMENTED_WARNING)
        return ExportModelResult(status_message=_EXPORT_NOT_IMPLEMENTED_STATUS)
