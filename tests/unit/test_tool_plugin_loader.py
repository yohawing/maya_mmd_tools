"""Contracts for the generic Tools menu plug-in boundary."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from mmd_tools.tools import discover_tool_plugins, install_tool_plugins


def test_discovery_finds_public_tool_scripts_without_host_registration() -> None:
    assert discover_tool_plugins() == (
        "mmd_tools.tools.translate_names",
    )


def test_install_uses_generic_host_callbacks_and_preserves_fixed_menu_id() -> None:
    cmds = SimpleNamespace(menuItem=MagicMock())
    on_applied = MagicMock()

    installed = install_tool_plugins(
        "MMDToolsSubMenu",
        cmds_module=cmds,
        on_applied=on_applied,
    )

    assert installed == ("MMDTranslateNamesMenuItem",)
    call = cmds.menuItem.call_args
    assert call.args == ("MMDTranslateNamesMenuItem",)
    assert call.kwargs["label"] == "Translate MMD Names"
    assert call.kwargs["parent"] == "MMDToolsSubMenu"
    assert callable(call.kwargs["command"])
