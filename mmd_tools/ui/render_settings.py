"""MMD Render options backed by the existing scene light attributes."""

from maya import cmds, mel

from mmd_tools.converters.light_converter import (
    MMD_SELF_SHADOW_DISTANCE_ATTR,
    MMD_SELF_SHADOW_MODE_ATTR,
    create_mmd_light_controller,
    ensure_mmd_light_shadow_attrs,
)

WINDOW = "mmdRenderSettingsWindow"


def install():
    """Expose Maya's standard Renderer-menu option-box entry point."""
    mel.eval('global proc mmdOrderedOptionBox() { python("from mmd_tools.ui.render_settings import show; show()"); }')


def _prepare_light(light=None):
    if light is None:
        light = create_mmd_light_controller()
    ensure_mmd_light_shadow_attrs(light)
    show()


def show():
    """Open scene-wide render settings without changing viewport preferences."""
    if cmds.window(WINDOW, exists=True):
        cmds.deleteUI(WINDOW)
    window = cmds.window(WINDOW, title="MMD Render", widthHeight=(300, 116),
                         sizeable=False, retain=False)
    frame = cmds.formLayout()
    content = cmds.columnLayout(adjustableColumn=True, rowSpacing=8)
    cmds.formLayout(frame, edit=True, attachForm=[
        (content, "top", 12), (content, "left", 12), (content, "right", 12),
        (content, "bottom", 12),
    ])
    lights = [node for node in (cmds.ls("*.mmd_light", objectsOnly=True, long=True) or [])
              if cmds.nodeType(node) == "transform" and cmds.getAttr(node + ".mmd_light")]
    if len(lights) == 1:
        light = lights[0]
        if all(cmds.attributeQuery(attr, node=light, exists=True)
               for attr in (MMD_SELF_SHADOW_MODE_ATTR, MMD_SELF_SHADOW_DISTANCE_ATTR)):
            cmds.rowLayout(numberOfColumns=2, columnWidth2=(140, 130), adjustableColumn=2)
            cmds.text(label="セルフ影", align="right", width=130)
            cmds.attrEnumOptionMenu("mmdRenderShadowMode",
                                   annotation="シーン共通の設定です。MMDライトに保存します。",
                                   attribute=light + "." + MMD_SELF_SHADOW_MODE_ATTR)
            cmds.setParent("..")
            cmds.attrControlGrp("mmdRenderShadowDistance", label="影距離（保存値）",
                              annotation="保存・モーション用の値です。描画への反映は未対応です。",
                              attribute=light + "." + MMD_SELF_SHADOW_DISTANCE_ATTR)
        else:
            cmds.button(label="このライトにセルフ影設定を追加",
                        command=lambda *_: _prepare_light(light))
    elif not lights:
        cmds.button(label="MMDライトを作成", command=lambda *_: _prepare_light())
    else:
        cmds.text(label="MMDライトが複数あります。\nシーン内で1つに整理してください。", align="left")
    cmds.button(label="再読込", command=lambda *_: show())
    cmds.window(WINDOW, edit=True, resizeToFitChildren=True)
    cmds.showWindow(window)
    return window
