# -*- coding: utf-8 -*-
"""インポート時に作る MMD ライトコントローラ。

MMD のライトはモデル全体に1つの平行光源。これを Maya 上で**操作可能なヌル
（draw 付き・MMD パラメータ付き）**として作る。

構成（get-or-create。既に MMD ライトがあれば再利用）:
- コントローラ用の transform（ヌル）= 操作ハンドル。`ATTR_MMD_LIGHT` タグ付き。
- その下に **directionalLight シェイプを再ペアレント**。こうすると:
    * VP2 がシーンライトとして dx11Shader の Light0Dir/DIRECTION セマンティクスに
      自動バインドする（UseFixedLight=0 の通常インポート経路で効く）。
    * VMD ライトアニメ（`*.mmd_light` ノードを探し、その配下の directionalLight
      シェイプに color、transform に rotate をキーする）が**無改修で動く**。
- 方向を示す **矢印 NURBS カーブ**を draw として同じ transform 配下に持つ。
- MMD パラメータ `mmd_light_color` をキー可能アトリビュートで持ち、ライトの
  color に接続。

ヌルを回すと MMD ライトベクトルが Viewport 2.0 でライブに変わる。
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
        logger.debug("既存の MMD ライトコントローラを再利用: %s", existing)
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
        logger.debug("mmd_light_color の接続に失敗", exc_info=True)

    # MMD 既定方向へ向ける（進行方向を Maya 空間 (-x, y, -z) に変換して -Z を合わせる）。
    rx, ry, rz = _direction_to_euler(
        -_MMD_DEFAULT_DIRECTION[0], _MMD_DEFAULT_DIRECTION[1], -_MMD_DEFAULT_DIRECTION[2]
    )
    cmds.setAttr(f"{ctrl}.rotateX", rx)
    cmds.setAttr(f"{ctrl}.rotateY", ry)
    cmds.setAttr(f"{ctrl}.rotateZ", rz)
    # 平行光源なので位置はシェーディングに無関係。掴みやすいようモデル上方へ。
    cmds.setAttr(f"{ctrl}.translateY", 30.0)

    logger.info("MMD ライトコントローラを作成: %s (rx=%.1f, ry=%.1f)", ctrl, rx, ry)
    return ctrl
