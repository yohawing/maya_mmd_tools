"""Contracts for the generic Tools menu plug-in boundary."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from importlib import import_module
import sys

from mmd_tools import tools as tool_plugins
from mmd_tools.tools import install_tool_plugins


def test_install_discovers_tool_script_and_preserves_fixed_menu_id() -> None:
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


def test_menu_registration_does_not_require_scene_display_service(monkeypatch):
    """The menu remains available before the dialog's Maya service can import."""
    name = "mmd_tools.tools.translate_names"
    import_module(name)
    monkeypatch.delitem(sys.modules, name)
    monkeypatch.delattr(tool_plugins, "translate_names")
    monkeypatch.setitem(sys.modules, "mmd_tools.services.scene_model_service", None)
    monkeypatch.setattr(tool_plugins, "_candidate_module_names", lambda: (name,))
    cmds = SimpleNamespace(menuItem=MagicMock())

    assert install_tool_plugins("Tools", cmds_module=cmds) == ("MMDTranslateNamesMenuItem",)
    assert cmds.menuItem.call_args.kwargs["parent"] == "Tools"


def test_import_failure_is_reported_and_other_tools_still_install(monkeypatch):
    good = SimpleNamespace(
        __name__="good",
        MENU_LABEL="Good tool", MENU_ITEM_ID="GoodMenuItem",
        install_menu_item=MagicMock(return_value="GoodMenuItem"),
    )

    def load(name):
        if name == "broken":
            raise ImportError("dependency unavailable")
        return good

    monkeypatch.setattr(tool_plugins, "_candidate_module_names", lambda: ("broken", "good"))
    monkeypatch.setattr(tool_plugins, "import_module", load)
    on_error = MagicMock()
    cmds = SimpleNamespace(menuItem=MagicMock())

    assert install_tool_plugins("Tools", cmds_module=cmds, on_error=on_error) == ("GoodMenuItem",)
    on_error.assert_called_once()
    assert "broken" in on_error.call_args.args[0]
    assert "dependency unavailable" in on_error.call_args.args[0]


def test_import_failure_is_logged_without_error_callback(monkeypatch, caplog):
    monkeypatch.setattr(tool_plugins, "_candidate_module_names", lambda: ("broken",))
    monkeypatch.setattr(tool_plugins, "import_module", MagicMock(side_effect=ImportError("missing dependency")))

    assert install_tool_plugins("Tools", cmds_module=SimpleNamespace()) == ()
    assert "broken" in caplog.text
    assert "missing dependency" in caplog.text
