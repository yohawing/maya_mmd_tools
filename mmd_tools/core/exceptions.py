class MMDParseException(Exception):
    """
    Custom exception for MMD parsing errors.
    """

    pass


class MMDImportException(Exception):
    """Custom exception for MMD import failures.

    Args:
        message: Human-readable failure description.
        reason_code: Optional stable, machine-readable reason code (for
            example ``"import_blocked_target_preview"``) so callers can
            classify the failure without parsing ``str(exception)``. Used by
            the HumanIK VMD import gate
            (``mmd_tools.converters.vmd_converter._enforce_humanik_import_gate``)
            to mirror the ``importLock.reasonCode`` values
            ``mmd_tools.core.humanik_frontend.describe_frontend_state``
            exposes to the UI (``REASON_IMPORT_BLOCKED_TARGET_PREVIEW`` /
            ``REASON_IMPORT_BLOCKED_CONTROL_RIG``). ``None`` for every other
            (non-HumanIK) import failure.
    """

    def __init__(self, message: str = "", reason_code=None):
        super().__init__(message)
        self.reason_code = reason_code


class MMDExportException(Exception):
    """Custom exception for MMD export failures."""

    pass
