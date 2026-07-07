"""Scene object selection, existence, parenting, and listing helpers."""

import fnmatch

from maya import cmds
from maya.api import OpenMaya as om

from .logger import get_logger

logger = get_logger(__name__)


def select_objects(objects=None, clear=True, add=False, replace=True):
    """
    OpenMaya API 2.0を使用してオブジェクトを選択します。
    cmds.select()の代替実装です。

    Args:
        objects (str or list, optional): 選択するオブジェクト。Noneの場合はクリアのみ
        clear (bool): 選択をクリアするかどうか
        add (bool): 既存の選択に追加するかどうか
        replace (bool): 既存の選択を置き換えるかどうか

    Returns:
        bool: 成功したかどうか
    """
    try:
        current_selection = om.MGlobal.getActiveSelectionList()

        if clear or replace:
            om.MGlobal.setActiveSelectionList(om.MSelectionList())

        if objects is None:
            return True

        new_selection = om.MSelectionList()

        if add and not clear and not replace:
            new_selection = om.MSelectionList(current_selection)

        if isinstance(objects, str):
            objects = [objects]

        for obj in objects:
            try:
                new_selection.add(obj)
            except Exception:
                logger.warning(f"Could not add '{obj}' to selection")

        om.MGlobal.setActiveSelectionList(new_selection)
        return True

    except Exception as e:
        logger.error(f"Failed to select objects: {e}")
        return False


def object_exists(object_name):
    """
    OpenMaya API 2.0を使用してオブジェクトの存在を確認します。
    cmds.objExists()の代替実装です。

    Args:
        object_name (str): 確認するオブジェクト名

    Returns:
        bool: オブジェクトが存在するかどうか
    """
    try:
        selection_list = om.MSelectionList()
        selection_list.add(object_name)
        return True
    except Exception:
        return False


def parent_objects(children, parent=None, world=False):
    """
    オブジェクトの親子関係を設定します。
    cmds.parent()のラッパー実装です。

    Args:
        children (str or list): 子オブジェクト
        parent (str, optional): 親オブジェクト。Noneまたはworld=Trueの場合はワールド空間へ
        world (bool): ワールド空間に親付けするかどうか

    Returns:
        list: 親付けされたオブジェクトのリスト
    """
    try:
        if isinstance(children, str):
            children = [children]

        if world or parent is None:
            result = cmds.parent(children, world=True)
        else:
            result = cmds.parent(children, parent)

        return result if isinstance(result, list) else [result]

    except Exception as e:
        logger.error(f"Failed to parent objects: {e}")
        return []


def list_objects(object_filter=None, type=None, fullPath=False):
    """
    OpenMaya API 2.0を使用してシーン内のオブジェクトをリストします。
    cmds.ls()の簡易版実装です。

    Args:
        object_filter (str, optional): オブジェクト名のフィルター（ワイルドカード対応）
        type (str, optional): オブジェクトタイプフィルター
        fullPath (bool, optional): フルパスで返すかどうか（デフォルト: False）

    Returns:
        list: マッチしたオブジェクトのリスト
    """
    try:
        result = []

        if type == "joint":
            it = om.MItDag(om.MItDag.kDepthFirst, om.MFn.kJoint)
        elif type == "mesh":
            it = om.MItDag(om.MItDag.kDepthFirst, om.MFn.kMesh)
        elif type == "transform":
            it = om.MItDag(om.MItDag.kDepthFirst, om.MFn.kTransform)
        elif type == "camera":
            it = om.MItDag(om.MItDag.kDepthFirst, om.MFn.kCamera)
        elif type == "blendShape":
            return _list_dg_nodes("blendShape", object_filter)
        elif type == "directionalLight":
            it = om.MItDag(om.MItDag.kDepthFirst, om.MFn.kDirectionalLight)
        else:
            it = om.MItDag(om.MItDag.kDepthFirst)

        while not it.isDone():
            try:
                dag_path = it.getPath()
                if fullPath:
                    node_name = dag_path.fullPathName()
                else:
                    node_name = dag_path.partialPathName()

                if object_filter and not fnmatch.fnmatch(node_name, object_filter):
                    it.next()
                    continue

                result.append(node_name)
            except Exception:
                pass

            it.next()

        return result

    except Exception as e:
        logger.error(f"Failed to list objects: {e}")
        return []


def _list_dg_nodes(node_type, object_filter=None):
    """
    DGノード（非DAGノード）をリストする内部ヘルパー関数。

    Args:
        node_type (str): ノードタイプ
        object_filter (str, optional): オブジェクト名のフィルター

    Returns:
        list: マッチしたオブジェクトのリスト
    """
    try:
        result = []
        it = om.MItDependencyNodes(om.MFn.kBlendShape)

        while not it.isDone():
            try:
                node = it.thisNode()
                fn_node = om.MFnDependencyNode(node)
                node_name = fn_node.name()

                if object_filter and not fnmatch.fnmatch(node_name, object_filter):
                    it.next()
                    continue

                result.append(node_name)
            except Exception:
                pass

            it.next()

        return result
    except Exception as e:
        logger.error(f"Failed to list DG nodes: {e}")
        return []
