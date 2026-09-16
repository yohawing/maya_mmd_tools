"""MMD Render options backed by the existing scene light attributes."""

from maya import cmds, mel

from mmd_tools.converters.light_converter import (
    MMD_SELF_SHADOW_DISTANCE_ATTR,
    MMD_SELF_SHADOW_MODE_ATTR,
    create_mmd_light_controller,
    ensure_mmd_light_shadow_attrs,
)

WINDOW = "mmdRenderSettingsWindow"
CONTENT = "mmdRenderSettingsContent"


def install():
    """Expose Maya's standard Renderer-menu option-box entry point."""
    mel.eval('global proc mmdOrderedOptionBox() { python("from mmd_tools.ui.render_settings import show; show()"); }')


def _prepare_light(light=None):
    if light is None:
        light = create_mmd_light_controller()
    ensure_mmd_light_shadow_attrs(light)
    show()


def _light_state():
    """Describe only the scene changes that affect available controls."""
    return tuple(sorted(
        (node, all(cmds.attributeQuery(attr, node=node, exists=True)
                   for attr in (MMD_SELF_SHADOW_MODE_ATTR, MMD_SELF_SHADOW_DISTANCE_ATTR)))
        for node in (cmds.ls("*.mmd_light", objectsOnly=True, long=True) or [])
        if cmds.nodeType(node) == "transform" and cmds.getAttr(node + ".mmd_light")
    ))


def _watch_lights(state, parent):
    """Coalesce scene notifications and release watchers with the window."""
    pending = False
    jobs = []

    def refresh():
        nonlocal pending
        pending = False
        if jobs and cmds.scriptJob(exists=jobs[0]) and _light_state() != state:
            show()

    def queue_refresh(*_):
        nonlocal pending
        if not pending:
            pending = True
            # A new transform receives its MMD attributes later in the command.
            cmds.evalDeferred(refresh, lowestPriority=True)

    for event in ("DagObjectCreated", "Undo", "Redo", "SceneOpened", "NewSceneOpened"):
        jobs.append(cmds.scriptJob(event=[event, queue_refresh], parent=parent))
    for light, _ready in state:
        cmds.scriptJob(nodeDeleted=[light, queue_refresh], parent=parent)


def show():
    """Open scene-wide render settings without changing viewport preferences."""
    if cmds.window(WINDOW, exists=True):
        window = WINDOW
        # Older versions used unnamed layouts. Clear every direct child of
        # this window, including those left by an in-session module reload.
        for layout in cmds.lsUI(controlLayouts=True, long=True) or []:
            if layout.rpartition("|")[0] == window:
                cmds.deleteUI(layout, layout=True)
    else:
        window = cmds.window(WINDOW, title="MMD Render", widthHeight=(300, 116),
                             sizeable=False, retain=False)
    frame = cmds.formLayout(CONTENT, parent=window)
    content = cmds.columnLayout(adjustableColumn=True, rowSpacing=8)
    cmds.formLayout(frame, edit=True, attachForm=[
        (content, "top", 12), (content, "left", 12), (content, "right", 12),
        (content, "bottom", 12),
    ])
    state = _light_state()
    lights = [node for node, _ready in state]
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
    _watch_lights(state, frame)
    return window
