"""Focused presentation contracts for the shared authoring icon toolbar."""

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from mmd_tools.ui.qt_compat import QApplication
    from mmd_tools.ui.components import symbol_tool_button
    from mmd_tools.ui.components.authoring_toolbar import (
        ACTION_SYMBOLS,
        AuthoringToolbar,
        ordered_actions,
    )
    from mmd_tools.ui.components.symbol_tool_button import (
        MaterialSymbolToolButton,
        SymbolToolButton,
    )
except ImportError as exc:  # pragma: no cover - local Maya installs provide Qt.
    pytest.skip(f"Qt binding unavailable: {exc}", allow_module_level=True)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_canonical_order_and_symbol_ids():
    assert ordered_actions(("move_down", "refresh", "move_up", "refresh", "extra")) == (
        "refresh",
        "move_up",
        "move_down",
        "extra",
    )
    assert ACTION_SYMBOLS["create"] == "create"
    assert ACTION_SYMBOLS["duplicate"] == "duplicate"
    assert ACTION_SYMBOLS["delete"] == "delete"
    assert ACTION_SYMBOLS["reset"] == "restart_alt"


def test_authoring_action_symbols_use_white_fill():
    symbol_dir = Path(symbol_tool_button.__file__).resolve().parents[1] / "assets" / "symbols"
    for symbol in set(ACTION_SYMBOLS.values()):
        svg = (symbol_dir / f"{symbol}.svg").read_text(encoding="utf-8")
        assert 'fill="#ffffff"' in svg, symbol


def test_toolbar_uses_32px_icon_buttons_and_focus(qapp):
    toolbar = AuthoringToolbar()
    for action, button in toolbar.buttons.items():
        assert button.width() == 32
        assert button.height() == 32
        assert not button.icon().isNull(), action
        policy = button.focusPolicy()
        assert int(getattr(policy, "value", policy)) & 0x1


def test_missing_svg_keeps_text_fallback(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(symbol_tool_button, "_SYMBOL_DIR", tmp_path)
    button = SymbolToolButton("does_not_exist", "Fallback label")
    assert button.icon().isNull()
    assert button.text() == "Fallback label"
    assert button.toolTip() == "Fallback label"
    assert button.accessibleName() == "Fallback label"


def test_disabled_reason_and_retranslate_preserve_accessibility(qapp):
    toolbar = AuthoringToolbar(actions=("delete",), labels={"delete": "Delete"})
    button = toolbar.button("delete")
    toolbar.set_action_enabled(
        "delete", False, "Select an item first", "authoring_selection_required"
    )
    assert "Select an item first" in button.toolTip()
    assert button.disabled_reason_key == "authoring_selection_required"
    assert button.accessibleName() == "Delete"
    toolbar.retranslate(
        {"delete": "削除"},
        reason_resolver=lambda key: f"translated:{key}",
    )
    assert button.text() == ""
    assert button.toolTip().startswith("削除")
    assert "translated:authoring_selection_required" in button.toolTip()
    assert button.accessibleName() == "削除"


def test_material_name_is_compatibility_alias():
    assert MaterialSymbolToolButton is SymbolToolButton


def test_authoring_tabs_expose_shared_icon_operations(qapp):
    from mmd_tools.ui.tabs.bone_tab import BoneTab
    from mmd_tools.ui.tabs.display_pane_tab import DisplayPaneTab
    from mmd_tools.ui.tabs.material_tab import MaterialTab
    from mmd_tools.ui.tabs.morph_tab import MorphTab

    tabs = (MaterialTab(), BoneTab(), MorphTab(), DisplayPaneTab())
    buttons = (
        (tabs[0].refresh_btn, tabs[0].create_btn, tabs[0].duplicate_btn, tabs[0].delete_btn,
         tabs[0].reindex_up_btn, tabs[0].reindex_down_btn),
        (tabs[1].refresh_btn, tabs[1].reindex_up_btn, tabs[1].reindex_down_btn,
         tabs[1].reset_authoring_btn),
        (tabs[2].refresh_morphs_btn, tabs[2].create_morph_btn, tabs[2].delete_morph_btn,
         tabs[2].move_morph_up_btn, tabs[2].move_morph_down_btn),
        (tabs[3].add_frame_btn, tabs[3].delete_frame_btn, tabs[3].move_frame_up_btn,
         tabs[3].move_frame_down_btn, tabs[3].refresh_btn),
    )
    assert all(button.width() == 32 and button.height() == 32 for row in buttons for button in row)
    assert all(not button.icon().isNull() for row in buttons for button in row)


def test_disabled_reason_retranslates_and_display_add_actions_fail_closed(qapp):
    from mmd_tools.ui.tabs.display_pane_tab import DisplayPaneTab
    from mmd_tools.ui.tabs.material_tab import MaterialTab
    from mmd_tools.ui.translations import UITranslator

    translator = UITranslator.instance()
    original = translator.get_language()
    try:
        translator.set_language("en")
        material = MaterialTab()
        assert "Authoring coordinator is not available" in material.create_btn.toolTip()
        translator.set_language("ja")
        material.retranslateUi()
        assert "編集コーディネーターを利用できません" in material.create_btn.toolTip()

        display = DisplayPaneTab()
        for button in (
            display.add_frame_btn,
            display.add_bone_btn,
            display.add_morph_btn,
        ):
            assert not button.isEnabled()
            assert "先に項目を選択してください" in button.toolTip()
    finally:
        translator.set_language(original)
