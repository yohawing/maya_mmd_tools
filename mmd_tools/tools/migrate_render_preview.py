"""Explicit Tools-menu migration for one selected legacy MMD model."""

from mmd_tools.core.logger import get_logger

MENU_LABEL = "選択モデルをMMD Render用に移行"
MENU_ITEM_ID = "MMDMigrateRenderPreviewMenuItem"
logger = get_logger(__name__)


def migrate_selected(*, cmds_module, on_applied=None):
    from mmd_tools.core.name_translation import resolve_model_root
    from mmd_tools.converters.render_preview_migration import migrate_model_render_preview

    try:
        root = resolve_model_root(cmds_module=cmds_module)
        result = migrate_model_render_preview(root)
    except Exception as exc:
        logger.error("MMD Render migration failed", exc_info=True)
        cmds_module.warning(f"MMD Renderへの移行に失敗しました: {exc}")
        return None
    try:
        if callable(on_applied):
            on_applied()
        cmds_module.inViewMessage(
            amg=f"MMD Render: 材質 {result['materials']} / 元メッシュ {result['sources']} を移行しました。Undoで戻せます。",
            position="midCenter", fade=True,
        )
    except Exception:
        logger.warning("MMD Render migration completed, but the UI could not refresh", exc_info=True)
    return result


def install_menu_item(*, parent, cmds_module, on_applied=None):
    cmds_module.menuItem(
        MENU_ITEM_ID, label=MENU_LABEL, parent=parent,
        annotation="旧MMD材質と元メッシュの表示接続を移行します。シーンのUndoに対応します。",
        command=lambda *_: migrate_selected(cmds_module=cmds_module, on_applied=on_applied),
    )
    return MENU_ITEM_ID
