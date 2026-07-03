"""Maya nDynamics helpers used by MMD physics conversion."""

from maya import cmds
from maya import mel


def find_or_create_nucleus_solver(name="mmd_nucleus"):
    """既存のNucleusソルバーを検索または新規作成"""
    nucleus_nodes = cmds.ls(type="nucleus")
    if nucleus_nodes:
        return nucleus_nodes[0]
    return cmds.createNode("nucleus", name=name)


def create_collision_primitive(shape_type, size, name="collision"):
    """形状タイプに応じたコリジョン用プリミティブを作成"""
    if shape_type == 0:  # 箱
        obj = cmds.polyCube(name=name, width=size[0] * 2, height=size[1] * 2, depth=size[2] * 2)[0]
    elif shape_type == 1:  # 球
        obj = cmds.polySphere(name=name, radius=size[0])[0]
    elif shape_type == 2:  # カプセル（円柱で近似）
        obj = cmds.polyCylinder(name=name, radius=size[0], height=size[1] * 2)[0]
    else:
        raise ValueError(f"Unknown shape type: {shape_type}")
    return obj


def apply_ncloth_to_mesh(mesh, nucleus_solver=None):
    """メッシュにnClothを適用"""
    cmds.select(mesh)
    ncloth_shape = mel.eval("createNCloth 0;")

    if nucleus_solver and ncloth_shape:
        ncloth_nodes = cmds.ls(type="nCloth")
        if ncloth_nodes:
            index = len([i for i in cmds.listConnections(nucleus_solver + ".inputActive") or [] if i])
            cmds.connectAttr(
                f"{ncloth_shape[0]}.currentState",
                f"{nucleus_solver}.inputActive[{index}]",
            )
            cmds.connectAttr(
                f"{ncloth_shape[0]}.startState",
                f"{nucleus_solver}.inputActiveStart[{index}]",
            )

    return ncloth_shape[0] if ncloth_shape else None


def apply_nrigid_to_mesh(obj, is_dynamic=True):
    """オブジェクトにnRigidを適用"""
    cmds.select(obj)
    nrigid = mel.eval("makeCollideNCloth;")

    if nrigid:
        cmds.setAttr(f"{nrigid[0]}.isDynamic", 1 if is_dynamic else 0)
        return nrigid[0]

    return None


def create_dynamic_curve(points, name="dynamic_curve"):
    """ダイナミックカーブを作成"""
    curve = cmds.curve(d=1, p=points, name=name)
    return curve


def apply_nhair_to_curve(curve):
    """カーブにnHairシステムを適用"""
    cmds.select(curve)
    mel.eval('makeCurvesDynamic 2 { "1", "0", "1", "1", "0"};')

    hair_systems = cmds.ls(type="hairSystem")
    if hair_systems:
        return hair_systems[-1]

    return None
