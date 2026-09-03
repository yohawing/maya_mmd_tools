"""Viewport helpers for Maya panel state."""

import math

from maya import cmds

from .logger import get_logger

logger = get_logger(__name__)
_LAST_DEVICE_PIXEL_RATIO = None


def get_device_pixel_ratio(view=None, default=1.0) -> float:
    """Return the active Maya viewport's physical-to-logical pixel ratio.

    Maya's shader ``ViewportPixelSize`` semantic reports physical pixels.  The
    ratio lets screen-space effects retain a logical-pixel width on HiDPI
    displays.  Headless sessions have no active view, so they use ``default``.

    Args:
        view: Optional ``M3dView`` instance, primarily for focused tests.
        default: Value returned when no valid viewport ratio is available.

    Returns:
        A finite positive device pixel ratio.
    """
    try:
        fallback = float(default)
    except (TypeError, ValueError):
        fallback = 1.0
    if not math.isfinite(fallback) or fallback <= 0.0:
        fallback = 1.0

    try:
        if view is None:
            from maya import OpenMayaUI as omui

            view = omui.M3dView.active3dView()
        ratio = float(view.devicePixelRatio())
        if math.isfinite(ratio) and ratio > 0.0:
            return ratio
    except Exception:
        logger.debug("Could not query the active viewport device pixel ratio", exc_info=True)
    return fallback


def sync_dx11_shader_device_pixel_ratio(force=False) -> int:
    """Synchronize logical-pixel scaling across existing MMD DX11 shaders.

    The cached ratio keeps frequent active-view callbacks cheap.  ``force`` is
    used after scene open and import because those operations can add shader
    nodes without changing the display ratio.

    Args:
        force: Update newly-created shaders even when the ratio is unchanged.

    Returns:
        Number of shader nodes whose ratio changed.
    """
    global _LAST_DEVICE_PIXEL_RATIO

    ratio = get_device_pixel_ratio()
    if not force and _LAST_DEVICE_PIXEL_RATIO is not None and math.isclose(
        ratio,
        _LAST_DEVICE_PIXEL_RATIO,
        rel_tol=0.0,
        abs_tol=1.0e-6,
    ):
        return 0

    try:
        shaders = cmds.ls(type="dx11Shader") or []
    except Exception:
        logger.debug("Could not enumerate DX11 shaders for DPI synchronization", exc_info=True)
        return 0

    changed = 0
    for shader in shaders:
        try:
            if not cmds.attributeQuery("DevicePixelRatio", node=shader, exists=True):
                continue
            current = float(cmds.getAttr(f"{shader}.DevicePixelRatio"))
            if math.isclose(current, ratio, rel_tol=0.0, abs_tol=1.0e-6):
                continue
            cmds.setAttr(f"{shader}.DevicePixelRatio", ratio)
            changed += 1
        except Exception:
            logger.debug("Could not synchronize DX11 shader DPI for '%s'", shader, exc_info=True)

    _LAST_DEVICE_PIXEL_RATIO = ratio
    return changed


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
                    try:
                        is_batch = bool(cmds.about(batch=True))
                    except Exception:
                        logger.warning(
                            "Could not determine Maya batch mode; no model panels found",
                            exc_info=True,
                        )
                    else:
                        if is_batch:
                            logger.debug("No model panels found in Maya batch mode")
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
    sync_dx11_shader_device_pixel_ratio(force=True)
    return changed


def setup_mmd_color_management(
    rendering_space="scene-linear Rec.709-sRGB",
    view_transform="Un-tone-mapped (sRGB)",
):
    """Color Management を Python MMD shader 向けに整える。

    MMD シェーダーは出口で de-gamma して view transform の sRGB encode を相殺し、
    MMD のガンマ空間ルックを CM ON のまま再現する。これが**厳密に**成立するには:

    - **Rendering space = scene-linear Rec.709-sRGB**: 既定の ACEScg のままだと
      view transform に AP1→Rec.709 の primaries 変換行列が混ざり、出口 de-gamma
      （転送関数のみ）では打ち消せず**彩度がズレる**。sRGB プライマリの線形空間に
      すれば view transform は純ガンマだけになり相殺が厳密になる。
    - **View transform = Un-tone-mapped (sRGB)**: 既定の ACES filmic はトーンマップで
      白く眠くなる。純 sRGB encode にする。

    ACES で見たい人は後から戻せる。Python shaderはlinear出力をMayaの
    view transformへ渡すため、CMを有効化する。

    Returns:
        bool: いずれかを設定できたら True。
    """
    changed = False
    try:
        if not bool(cmds.colorManagementPrefs(q=True, cmEnabled=True)):
            cmds.colorManagementPrefs(e=True, cmEnabled=True)
            logger.info("Enabled color management for Python MMD shader output")
        changed = True
    except Exception:
        logger.debug("Failed to enable color management", exc_info=True)

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


def setup_mmd_native_color_management() -> bool:
    """Disable Maya color management for the native gamma-space VP2 path.

    The native C++ MMD shader writes authored sRGB values directly so VP2 can
    perform MMD's legacy gamma-space alpha blend.  Maya's color-managed view
    would reinterpret those values as linear and apply another output
    transform.  This setting is intentionally separate from
    :func:`setup_mmd_color_management`, which is used by the Python
    ``dx11Shader`` path and keeps color management enabled.

    Returns:
        bool: Whether the color-management state was queried or changed.
    """
    try:
        current = bool(cmds.colorManagementPrefs(q=True, cmEnabled=True))
        if current:
            cmds.colorManagementPrefs(e=True, cmEnabled=False)
            logger.info("Disabled color management for native MMD VP2 output")
        return True
    except Exception:
        logger.debug("Failed to disable color management for native MMD VP2 output", exc_info=True)
        return False


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
