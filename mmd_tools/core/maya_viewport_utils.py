"""Viewport helpers for Maya panel state."""

from maya import cmds

from .logger import get_logger

logger = get_logger(__name__)


def set_viewport_backface_culling(enabled=True, panel_name=None) -> bool:
    """
    ビューポートのバックフェイスカリングを設定する。

    Args:
        enabled (bool): バックフェイスカリングを有効にするかどうか
        panel_name (str): 対象のパネル名。Noneの場合はアクティブなパネルを使用

    Returns:
        bool: 設定が成功したかどうか
    """
    try:
        if panel_name is None:
            panel_name = cmds.getPanel(withFocus=True)

            if not cmds.getPanel(typeOf=panel_name) == "modelPanel":
                panels = cmds.getPanel(type="modelPanel")
                if panels:
                    panel_name = panels[0]
                else:
                    logger.warning("No model panels found")
                    return False

        cmds.modelEditor(panel_name, edit=True, backfaceCulling=enabled)

        logger.info(f"Backface culling {'enabled' if enabled else 'disabled'} for panel: {panel_name}")
        return True

    except Exception as e:
        logger.error(f"Failed to set backface culling: {e}")
        return False
