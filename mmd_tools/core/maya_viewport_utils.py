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


def setup_mmd_hardware_viewport() -> int:
    """Enable the viewport state required by MMD hardware shaders.

    Maya's ``displayTextures`` toggle controls whether hardware shader texture
    samplers are evaluated in VP2.  A material imported with a DX11/GLSL MMD
    shader therefore needs every model panel to have textures enabled.  The
    user's existing shading appearance is preserved.  Panel failures are
    isolated so a stale/unsupported panel cannot make the model import fail.

    Returns:
        int: Number of model panels whose display state was changed.
    """
    try:
        panels = cmds.getPanel(type="modelPanel") or []
    except Exception:
        logger.debug("Failed to enumerate model panels for MMD hardware viewport setup", exc_info=True)
        return 0

    changed = 0
    for panel_name in panels:
        try:
            display_textures = cmds.modelEditor(panel_name, query=True, displayTextures=True)
            if bool(display_textures):
                continue
            cmds.modelEditor(panel_name, edit=True, displayTextures=True)
            changed += 1
        except Exception:
            logger.debug(
                "Failed to configure model panel for MMD hardware viewport: %s",
                panel_name,
                exc_info=True,
            )

    logger.debug(
        "MMD hardware viewport setup changed %d/%d model panel(s)",
        changed,
        len(panels),
    )
    return changed


def setup_mmd_color_management(
    rendering_space="scene-linear Rec.709-sRGB",
    view_transform="Un-tone-mapped (sRGB)",
):
    """Color Management を MMD 向けに整える（CM の有効/無効は変更しない）。

    MMD シェーダーは出口で de-gamma して view transform の sRGB encode を相殺し、
    MMD のガンマ空間ルックを CM ON のまま再現する。これが**厳密に**成立するには:

    - **Rendering space = scene-linear Rec.709-sRGB**: 既定の ACEScg のままだと
      view transform に AP1→Rec.709 の primaries 変換行列が混ざり、出口 de-gamma
      （転送関数のみ）では打ち消せず**彩度がズレる**。sRGB プライマリの線形空間に
      すれば view transform は純ガンマだけになり相殺が厳密になる。
    - **View transform = Un-tone-mapped (sRGB)**: 既定の ACES filmic はトーンマップで
      白く眠くなる。純 sRGB encode にする。

    ACES で見たい人は後から戻せる。CM の enable 状態はユーザー設定を尊重。

    Returns:
        bool: いずれかを設定できたら True。
    """
    changed = False
    try:
        spaces = cmds.colorManagementPrefs(q=True, renderingSpaceNames=True) or []
        if rendering_space in spaces:
            current = cmds.colorManagementPrefs(q=True, renderingSpaceName=True)
            if current != rendering_space:
                cmds.colorManagementPrefs(e=True, renderingSpaceName=rendering_space)
                logger.info("Set rendering space for MMD: %s (previous: %s)", rendering_space, current)
            changed = True
        else:
            logger.debug("Rendering space '%s' is unavailable. Skipping", rendering_space)
    except Exception:
        logger.debug("Failed to set rendering space", exc_info=True)

    try:
        transforms = cmds.colorManagementPrefs(q=True, viewTransformNames=True) or []
        if view_transform in transforms:
            current = cmds.colorManagementPrefs(q=True, viewTransformName=True)
            if current != view_transform:
                cmds.colorManagementPrefs(e=True, viewTransformName=view_transform)
                logger.info("Set View Transform for MMD: %s (previous: %s)", view_transform, current)
            changed = True
        else:
            logger.debug("View Transform '%s' is unavailable. Skipping", view_transform)
    except Exception:
        logger.debug("Failed to set View Transform", exc_info=True)

    return changed


# Viewport 2.0 transparency algorithm enum (hardwareRenderingGlobals):
#   0 Simple / 1 Object Sorting / 2 Weighted Average / 3 Depth Peeling / 5 Alpha Cut
TRANSPARENCY_ALGORITHM_DEPTH_PEELING = 3


def setup_mmd_transparency(algorithm=TRANSPARENCY_ALGORITHM_DEPTH_PEELING):
    """VP2 の透過アルゴリズムを MMD 向け（Depth Peeling / OIT）に設定する。

    既定の Object Sorting は**オブジェクト/レンダーアイテムを距離順**で並べるため、
    スカートのように近接した別マテリアルどうしだと並びが逆転する（MMD のマテリアル
    順にならない）。Depth Peeling は**画素単位の順序非依存合成**なので、距離が近い
    透過マテリアルでも正しく重なる。グローバル設定なので全ビューポートに効く（性能
    負荷あり）。設定キー ``import.view.setup_transparency`` で opt-out 可。

    Returns:
        bool: 設定できたら True。
    """
    try:
        node = "hardwareRenderingGlobals"
        attr = f"{node}.transparencyAlgorithm"
        if not cmds.objExists(node) or not cmds.attributeQuery("transparencyAlgorithm", node=node, exists=True):
            logger.debug("transparencyAlgorithm attribute is unavailable. Skipping")
            return False
        current = cmds.getAttr(attr)
        if current != algorithm:
            cmds.setAttr(attr, algorithm)
            logger.info("Set transparency algorithm for MMD: %s (previous: %s)", algorithm, current)
        return True
    except Exception:
        logger.debug("Failed to set transparency algorithm", exc_info=True)
        return False
