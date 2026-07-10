"""
Model import scale helpers shared by PMX and PMD importers.
"""

from typing import Any, Dict, Optional

from maya import cmds


def apply_import_scale(root_group: str, scale: float, logger: Any) -> bool:
    """Apply and freeze import scale without making locked attrs fatal.

    Args:
        root_group (str): Imported model root transform.
        scale (float): Scale factor to apply.
        logger: Maya-aware logger instance.

    Returns:
        bool: True when scale was applied and frozen, False when skipped or
            only partially applied.
    """
    if not root_group or scale == 1.0:
        return False

    attrs = [f"{root_group}.scale{axis}" for axis in "XYZ"]
    original_locks = {}

    for attr in attrs:
        try:
            original_locks[attr] = bool(cmds.getAttr(attr, lock=True))
            if original_locks[attr]:
                cmds.setAttr(attr, lock=False)
        except Exception:
            original_locks[attr] = None
            logger.debug("Could not inspect or unlock scale attr: %s", attr, exc_info=True)

    try:
        logger.debug("Applying scale: %f", scale)
        for attr in attrs:
            cmds.setAttr(attr, scale)
    except Exception:
        logger.warning(
            "Failed to apply import scale to %s; continuing import",
            root_group,
            exc_info=True,
        )
        _restore_scale_locks(original_locks, logger)
        return False

    try:
        cmds.makeIdentity(root_group, apply=True, scale=True)
        return True
    except Exception:
        logger.warning(
            "Failed to freeze import scale on %s; continuing import",
            root_group,
            exc_info=True,
        )
        return False
    finally:
        _restore_scale_locks(original_locks, logger)


def _restore_scale_locks(original_locks: Dict[str, Optional[bool]], logger: Any) -> None:
    """Restore scale attr lock states captured by apply_import_scale."""
    for attr, locked in original_locks.items():
        if locked is None:
            continue
        try:
            cmds.setAttr(attr, lock=locked)
        except Exception:
            logger.debug("Could not restore scale attr lock: %s", attr, exc_info=True)
