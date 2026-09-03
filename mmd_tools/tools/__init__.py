"""Discovery boundary for optional MMD Tools menu scripts.

Every public module in this package may opt in as a menu tool by exposing
``MENU_LABEL``, ``MENU_ITEM_ID``, and ``install_menu_item``.  Diagnostic
scripts without that contract remain invisible.  The Maya host discovers this
package generically and never imports an individual tool by name.
"""

from __future__ import annotations

from importlib import import_module
from pkgutil import iter_modules
from typing import Callable, Optional, Tuple

from mmd_tools.core.logger import get_logger

logger = get_logger(__name__)

def _candidate_module_names() -> Tuple[str, ...]:
    return tuple(
        f"{__name__}.{module.name}"
        for module in sorted(iter_modules(__path__), key=lambda item: item.name)
        if not module.ispkg and not module.name.startswith("_")
    )


def install_tool_plugins(
    parent: str,
    *,
    cmds_module,
    on_applied: Optional[Callable] = None,
    on_error: Optional[Callable[[str], None]] = None,
) -> Tuple[str, ...]:
    """Discover and install tools without making the host know their names."""

    installed = []
    for module_name in _candidate_module_names():
        try:
            module = import_module(module_name)
            if not (
                str(getattr(module, "MENU_LABEL", "")).strip()
                and str(getattr(module, "MENU_ITEM_ID", "")).strip()
                and callable(getattr(module, "install_menu_item", None))
            ):
                continue
            menu_id = module.install_menu_item(
                parent=parent,
                cmds_module=cmds_module,
                on_applied=on_applied,
            )
            if not cmds_module.menuItem(menu_id, exists=True):
                raise RuntimeError(f"Maya did not create menu item {menu_id!r}")
            installed.append(str(menu_id))
        except Exception as exc:
            message = f"MMD tool script failed to load ({module_name}): {type(exc).__name__}: {exc}"
            if callable(on_error):
                on_error(message)
            else:
                logger.warning(message, exc_info=True)
    return tuple(installed)


__all__ = ["install_tool_plugins"]
