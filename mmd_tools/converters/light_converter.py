# -*- coding: utf-8 -*-
"""インポート時に作る MMD ライトコントローラ。

MMD のライトはモデル全体に1つの平行光源。これを Maya 上で**操作可能なヌル
（draw 付き・MMD パラメータ付き）**として作る。

構成（get-or-create。既に MMD ライトがあれば再利用）:
- コントローラ用の transform（ヌル）= 操作ハンドル。`ATTR_MMD_LIGHT` タグ付き。
- その下に **directionalLight シェイプを再ペアレント**。こうすると VMD ライト
  アニメ（`*.mmd_light` ノードを探し、その配下の directionalLight シェイプに
  color、transform に rotate をキーする）が**無改修で動く**。
- 方向を示す **矢印 NURBS カーブ**を draw として同じ transform 配下に持つ。
- MMD パラメータ `mmd_light_color` をキー可能アトリビュートで持ち、ライトの
  color に接続。

シェーダーへの結線は `wire_dx11_shaders_to_mmd_light` が担当する。dx11Shader は
Maya のシーンライト自動バインド（DIRECTION/LIGHTCOLOR）を使わず、コントローラの
worldMatrix（→ `MMDLightDirection`）と `mmd_light_color`（→ `MMDLightColor`）
だけを光源として参照する。ヌルを回すと MMD ライトベクトルが Viewport 2.0 で
ライブに変わる。
"""

from __future__ import annotations

import math

from maya import cmds

from ..core.constants import ATTR_MMD_LIGHT, DEFAULT_LIGHT_NAME
from ..core.logger import get_logger

logger = get_logger("mmd_tools.converters.light_converter")

# MMD 既定のライト進行方向（MMD/マニフェスト空間）。MMD(左手系)→Maya(右手系) は
# (-x, y, -z)（ノート §13、GoldenOracle ゴールデン検証済みの harness と同一規約）。
_MMD_DEFAULT_DIRECTION = (0.5, -1.0, 0.5)


def _direction_to_euler(dx: float, dy: float, dz: float):
    """Maya ライトのローカル -Z を (dx,dy,dz) へ向ける (rx, ry, 0) を degrees で返す。

    VmdConverter._convert_light_animation と同じ式。
    """
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    if length < 1e-10:
        return (0.0, 0.0, 0.0)
    dx, dy, dz = dx / length, dy / length, dz / length
    rx = math.asin(max(-1.0, min(1.0, dy)))
    cos_rx = math.cos(rx)
    ry = math.atan2(-dx / cos_rx, -dz / cos_rx) if abs(cos_rx) > 1e-10 else 0.0
    return (math.degrees(rx), math.degrees(ry), 0.0)


def find_mmd_light():
    """既存の MMD ライトコントローラ transform を返す（無ければ None）。"""
    existing = cmds.ls(f"*.{ATTR_MMD_LIGHT}", objectsOnly=True) or []
    return existing[0] if existing else None


def _add_arrow_shape(parent: str, length: float = 8.0) -> None:
    """-Z を指す矢印カーブを作り、シェイプを *parent* 配下へ移す。"""
    h = length
    head = length * 0.22
    pts = [
        (0, 0, 0), (0, 0, -h),
        (head, 0, -h + head), (0, 0, -h), (-head, 0, -h + head),
        (0, 0, -h), (0, head, -h + head), (0, 0, -h), (0, -head, -h + head),
    ]
    crv = cmds.curve(degree=1, point=pts, name=f"{DEFAULT_LIGHT_NAME}_dir")
    shape = cmds.listRelatives(crv, shapes=True)[0]
    cmds.parent(shape, parent, shape=True, relative=True)
    cmds.delete(crv)


def create_mmd_light_controller() -> str:
    """MMD ライトコントローラを get-or-create し、transform 名を返す。"""
    existing = find_mmd_light()
    if existing:
        logger.debug("Reusing existing MMD light controller: %s", existing)
        return existing

    # 操作ハンドルとなるヌル。
    ctrl = cmds.group(empty=True, name=DEFAULT_LIGHT_NAME)

    # directionalLight を作り、シェイプをコントローラ配下へ再ペアレント。
    light_shape = cmds.directionalLight()
    tmp_transform = cmds.listRelatives(light_shape, parent=True)[0]
    light_shape = cmds.parent(light_shape, ctrl, shape=True, relative=True)[0]
    cmds.delete(tmp_transform)

    # 方向を示す矢印 draw。
    _add_arrow_shape(ctrl)

    # MMD パラメータ / タグ。
    cmds.addAttr(ctrl, longName=ATTR_MMD_LIGHT, attributeType="bool")
    cmds.setAttr(f"{ctrl}.{ATTR_MMD_LIGHT}", True)
    cmds.addAttr(ctrl, longName="mmd_light_color", usedAsColor=True, attributeType="float3")
    cmds.addAttr(ctrl, longName="mmd_light_colorR", attributeType="float", parent="mmd_light_color")
    cmds.addAttr(ctrl, longName="mmd_light_colorG", attributeType="float", parent="mmd_light_color")
    cmds.addAttr(ctrl, longName="mmd_light_colorB", attributeType="float", parent="mmd_light_color")
    cmds.setAttr(f"{ctrl}.mmd_light_color", 1.0, 1.0, 1.0, type="float3")
    try:
        cmds.connectAttr(f"{ctrl}.mmd_light_color", f"{light_shape}.color", force=True)
    except Exception:
        logger.debug("Failed to connect mmd_light_color", exc_info=True)

    # MMD 既定方向へ向ける（進行方向を Maya 空間 (-x, y, -z) に変換して -Z を合わせる）。
    rx, ry, rz = _direction_to_euler(
        -_MMD_DEFAULT_DIRECTION[0], _MMD_DEFAULT_DIRECTION[1], -_MMD_DEFAULT_DIRECTION[2]
    )
    cmds.setAttr(f"{ctrl}.rotateX", rx)
    cmds.setAttr(f"{ctrl}.rotateY", ry)
    cmds.setAttr(f"{ctrl}.rotateZ", rz)
    # 平行光源なので位置はシェーディングに無関係。掴みやすいようモデル上方へ。
    cmds.setAttr(f"{ctrl}.translateY", 30.0)

    logger.info("Created MMD light controller: %s (rx=%.1f, ry=%.1f)", ctrl, rx, ry)
    return ctrl


def set_mmd_light_direction(direction, color=None) -> str:
    """MMD ライトコントローラの向き（と色）を *direction* に合わせる。

    *direction* は「光が進む向き」（Maya 空間のベクトル）。コントローラのワールド
    -Z をそれに向けると、結線済みの vectorProduct 経由で各シェーダーの
    ``MMDLightDirection`` がライブに更新される（= 本番と同じ駆動経路）。

    シェーダー側の ``MMDLightDirection`` は結線済みで setAttr 不可なので、向きは
    必ずコントローラ経由で与えること。色は ``mmd_light_color`` 経由。

    Returns:
        str | None: コントローラ transform 名（無ければ None）。
    """
    ctrl = find_mmd_light()
    if not ctrl or not cmds.objExists(ctrl):
        return None
    rx, ry, rz = _direction_to_euler(direction[0], direction[1], direction[2])
    cmds.setAttr(f"{ctrl}.rotateX", rx)
    cmds.setAttr(f"{ctrl}.rotateY", ry)
    cmds.setAttr(f"{ctrl}.rotateZ", rz)
    if color is not None:
        try:
            cmds.setAttr(f"{ctrl}.mmd_light_color", float(color[0]), float(color[1]), float(color[2]), type="float3")
        except Exception:
            logger.debug("Failed to set mmd_light_color", exc_info=True)
    return ctrl


def _get_or_create_light_direction_node(ctrl: str) -> str:
    """*ctrl* のワールド -Z 方向を出力する vectorProduct を get-or-create する。

    directionalLight はローカル -Z へ照射する。シェーダーの ``MMDLightDirection``
    は「光が進む向き」（= ライトのワールド -Z）を期待し、内部で
    ``lightDir = -normalize(MMDLightDirection)`` として面→光ベクトルに直す。
    そこで ``vectorProduct`` の Vector Matrix Product で ``(0,0,-1)`` を
    ``ctrl.worldMatrix`` で変換し、ワールド -Z を得る。ヌルを回すと出力が
    ライブに変わるので、結線するだけでコントローラがライト方向を駆動できる。
    """
    node_name = f"{DEFAULT_LIGHT_NAME}_dirVP"
    # 既に ctrl.worldMatrix を入力に持つ vectorProduct があれば再利用。
    existing = (
        cmds.listConnections(f"{ctrl}.worldMatrix[0]", type="vectorProduct", source=False, destination=True)
        or []
    )
    if existing:
        return existing[0]

    vp = cmds.createNode("vectorProduct", name=node_name)
    # operation 3 = Vector Matrix Product（平行移動を無視＝方向ベクトル用）。
    # 4 の Point Matrix Product だとヌルの translate が方向に足し込まれてしまう。
    cmds.setAttr(f"{vp}.operation", 3)
    cmds.setAttr(f"{vp}.normalizeOutput", 1)
    cmds.setAttr(f"{vp}.input1", 0.0, 0.0, -1.0, type="double3")
    cmds.connectAttr(f"{ctrl}.worldMatrix[0]", f"{vp}.matrix", force=True)
    return vp


def wire_dx11_shaders_to_mmd_light(shaders, ctrl: str = None) -> int:
    """各 dx11Shader を MMD ライトコントローラに結線し、結線した数を返す。

    シェーダーは Maya のシーンライト自動バインド（DIRECTION/LIGHTCOLOR）を
    使わず、``MMDLightDirection`` / ``MMDLightColor`` uniform のみを光源として
    参照する。これらをコントローラから**明示結線**することで、ビューポートの
    ライトモード（Use Default/All Lights）に依存せずコントローラが効く。

    GUI の dx11Shader は .fx 評価後（``cmds.refresh`` 後）に uniform 属性を
    生成するため、本関数は refresh / uniform sync の後に呼ぶこと。
    """
    if not shaders:
        return 0
    if ctrl is None:
        ctrl = find_mmd_light()
    if not ctrl or not cmds.objExists(ctrl):
        logger.debug("No MMD light found; skipping wiring")
        return 0

    try:
        vp = _get_or_create_light_direction_node(ctrl)
    except Exception:
        logger.debug("Failed to create light direction node", exc_info=True)
        return 0

    wired = 0
    for shader in shaders:
        if not shader or not cmds.objExists(shader) or cmds.nodeType(shader) != "dx11Shader":
            continue
        try:
            if cmds.attributeQuery("MMDLightDirection", node=shader, exists=True):
                cmds.connectAttr(f"{vp}.output", f"{shader}.MMDLightDirection", force=True)
            if cmds.attributeQuery("MMDLightColor", node=shader, exists=True):
                cmds.connectAttr(f"{ctrl}.mmd_light_color", f"{shader}.MMDLightColor", force=True)
            wired += 1
        except Exception:
            logger.debug("Failed to wire light: %s", shader, exc_info=True)

    if wired:
        logger.info("Wired MMD light to %d dx11Shader nodes", wired)
    return wired
