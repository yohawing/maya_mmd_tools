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
    window = cmds.window(WINDOW, title="MMD Render", widthHeight=(450, 330))
    cmds.columnLayout(adjustableColumn=True, rowSpacing=10)
    cmds.text(label="セルフ影：シーン共通 / MMDライトに保存", align="left")
    lights = [node for node in (cmds.ls("*.mmd_light", objectsOnly=True, long=True) or [])
              if cmds.nodeType(node) == "transform" and cmds.getAttr(node + ".mmd_light")]
    if len(lights) == 1:
        light = lights[0]
        cmds.text(label=light, align="left")
        if all(cmds.attributeQuery(attr, node=light, exists=True)
               for attr in (MMD_SELF_SHADOW_MODE_ATTR, MMD_SELF_SHADOW_DISTANCE_ATTR)):
            cmds.attrEnumOptionMenu("mmdRenderShadowMode", label="セルフ影",
                                    attribute=light + "." + MMD_SELF_SHADOW_MODE_ATTR)
            cmds.attrControlGrp("mmdRenderShadowDistance", label="MMD影距離（保存値）",
                              attribute=light + "." + MMD_SELF_SHADOW_DISTANCE_ATTR)
        else:
            cmds.button(label="このライトにセルフ影設定を追加",
                        command=lambda *_: _prepare_light(light))
    elif not lights:
        cmds.text(label="MMDライトがありません。", align="left")
        cmds.button(label="MMDライトを作成", command=lambda *_: _prepare_light())
    else:
        cmds.text(label="MMDライトが複数あります。シーン内で1つに整理してください。", align="left")
    cmds.text(label="影距離は保存・モーション用の値です。描画への反映は未対応です。", align="left")
    cmds.separator(style="in")
    cmds.text(label="輪郭・影の受け渡しは MMD Editor の材質設定で変更します。", align="left")
    cmds.text(label="表示の切替はパネルごと。材質とライトの値はシーンに保存されます。", align="left")
    cmds.text(label="4：元メッシュのワイヤー / 5：テクスチャOFF / 6：テクスチャON", align="left")
    cmds.text(label="MMD Render は DirectX 11 用の描画確認です。", align="left")
    cmds.button(label="再読込", command=lambda *_: show())
    cmds.showWindow(window)
    return window
