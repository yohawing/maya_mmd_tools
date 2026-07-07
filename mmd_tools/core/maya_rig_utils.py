"""Rig, IK, and joint helpers for Maya scene construction."""

from maya import cmds
from maya.api import OpenMaya as om

from .logger import get_logger

logger = get_logger(__name__)


def _set_double3_attribute(object_name, attr_name, attr_value):
    """Set a double3 plug while preserving Maya's angle-unit behavior."""
    try:
        selection_list = om.MSelectionList()
        selection_list.add(object_name)
        node_obj = selection_list.getDependNode(0)
        depend_fn = om.MFnDependencyNode(node_obj)
        plug = depend_fn.findPlug(attr_name, False)
        for i, value in enumerate(attr_value):
            child_plug = plug.child(i)
            child_plug.setDouble(value)
    except Exception as e:
        logger.error(f"Failed to set attribute value '{attr_name}' on '{object_name}': {e}")


def create_ik_handle(start_joint, end_joint, solver="ikRPsolver", name=None):
    """
    IKハンドルを作成する。

    Args:
        start_joint (str): IKチェーンの開始ジョイント名
        end_joint (str): IKチェーンの終了ジョイント名
        solver (str): 使用するIKソルバー ("ikRPsolver", "ikSCsolver", "ikSplineSolver")
        name (str): IKハンドルの名前（Noneの場合は自動生成）

    Returns:
        tuple: (ik_handle, effector) IKハンドル名とエフェクター名のタプル

    Raises:
        ValueError: ジョイントが存在しない場合やソルバーが無効な場合
    """
    if not cmds.objExists(start_joint):
        raise ValueError(f"Start joint '{start_joint}' does not exist")
    if not cmds.objExists(end_joint):
        raise ValueError(f"End joint '{end_joint}' does not exist")

    valid_solvers = ["ikRPsolver", "ikSCsolver", "ikSplineSolver"]
    if solver not in valid_solvers:
        raise ValueError(f"Invalid solver '{solver}'. Must be one of: {valid_solvers}")

    try:
        ik_handle_result = cmds.ikHandle(
            startJoint=start_joint,
            endEffector=end_joint,
            solver=solver,
            name=name if name else f"{end_joint}_ikHandle",
        )

        ik_handle = ik_handle_result[0]
        effector = ik_handle_result[1]

        logger.info(f"Created IK handle '{ik_handle}' from '{start_joint}' to '{end_joint}'")
        return ik_handle, effector

    except Exception as e:
        logger.error(f"Failed to create IK handle: {e}")
        raise


def set_joint_limits(joint, limit_min=None, limit_max=None, enable_limits=True):
    """
    ジョイントの回転制限を設定する。

    Args:
        joint (str): ジョイント名
        limit_min (list): 最小回転制限 [x, y, z] ラジアン単位
        limit_max (list): 最大回転制限 [x, y, z] ラジアン単位
        enable_limits (bool): 制限を有効にするかどうか
    """

    if limit_min:
        _set_double3_attribute(joint, "minRotLimit", limit_min)

    if limit_max:
        _set_double3_attribute(joint, "maxRotLimit", limit_max)

    if limit_min:
        cmds.setAttr(f"{joint}.minRotXLimitEnable", enable_limits)
        cmds.setAttr(f"{joint}.minRotYLimitEnable", enable_limits)
        cmds.setAttr(f"{joint}.minRotZLimitEnable", enable_limits)

    if limit_max:
        cmds.setAttr(f"{joint}.maxRotXLimitEnable", enable_limits)
        cmds.setAttr(f"{joint}.maxRotYLimitEnable", enable_limits)
        cmds.setAttr(f"{joint}.maxRotZLimitEnable", enable_limits)


def create_pole_vector_constraint(ik_handle, pole_vector_object, maintain_offset=True):
    """
    IKハンドルにポールベクターコンストレイントを作成する。

    Args:
        ik_handle (str): IKハンドル名
        pole_vector_object (str): ポールベクターコントロールオブジェクト名
        maintain_offset (bool): オフセットを維持するかどうか

    Returns:
        str: 作成されたコンストレイントノード名
    """
    if not cmds.objExists(ik_handle):
        raise ValueError(f"IK handle '{ik_handle}' does not exist")
    if not cmds.objExists(pole_vector_object):
        raise ValueError(f"Pole vector object '{pole_vector_object}' does not exist")

    try:
        constraint = cmds.poleVectorConstraint(pole_vector_object, ik_handle, maintainOffset=maintain_offset)[0]

        logger.info(f"Created pole vector constraint from '{pole_vector_object}' to '{ik_handle}'")
        return constraint

    except Exception as e:
        logger.error(f"Failed to create pole vector constraint: {e}")
        raise
