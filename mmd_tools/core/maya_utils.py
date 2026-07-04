import json

from maya import cmds
from maya.api import OpenMaya as om
from maya.api import OpenMayaAnim as oma


from mmd_tools.core.constants import (
    ATTR_MMD_MODEL_NAME,
    ATTR_MMD_MODEL_NAME_EN,
)

from . import maya_material_utils as _maya_material_utils
from . import maya_mesh_utils as _maya_mesh_utils
from . import maya_physics_utils as _maya_physics_utils
from . import utils
from .logger import get_logger

logger = get_logger(__name__)


def _is_non_bool_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _infer_sequence_attribute_type(attr_value):
    if not all(_is_non_bool_number(x) for x in attr_value):
        return type(attr_value).__name__
    if len(attr_value) == 3:
        return "double3"
    if len(attr_value) == 4:
        return "double4"
    return "doubleArray"


def _add_compound_attribute_with_cmds(object_name, attr_name, attr_type):
    compound_specs = {
        "double3": (3, "double"),
        "long3": (3, "long"),
        "double4": (4, "double"),
    }
    if attr_type not in compound_specs:
        return

    child_count, child_type = compound_specs[attr_type]
    try:
        cmds.addAttr(object_name, ln=attr_name, at=attr_type)
        for suffix in ("X", "Y", "Z", "W")[:child_count]:
            cmds.addAttr(object_name, ln=f"{attr_name}{suffix}", at=child_type, p=attr_name)
    except Exception as e:
        logger.error(f"Failed to add typed attribute '{attr_name}' to '{object_name}': {e}")


def _ensure_compound_attribute_created(object_name, attr_name, attr_type):
    if attr_type not in {"double3", "long3", "double4"}:
        return
    if not cmds.attributeQuery(attr_name, node=object_name, exists=True):
        _add_compound_attribute_with_cmds(object_name, attr_name, attr_type)


def _set_compound_attribute_with_cmds(object_name, attr_name, attr_value, attr_type):
    cmds.setAttr(f"{object_name}.{attr_name}", *attr_value, type=attr_type)


def sanitize_text(name):
    """
    Maya用に名前をサニタイズする。
    日本語などのマルチバイト文字をASCII文字に変換し、Maya互換の名前にする。

    Args:
        name (str): 元の名前

    Returns:
        str: Maya互換の名前
    """
    if not name:
        return "unnamed"

    converted_name = utils.convert_utf8_to_ascii(name)
    return converted_name or "default_name"


def sanitize_bone_name(name):
    """Maya用にMMD/PMXボーン名をサニタイズする。"""
    if not name:
        return "unnamed"

    from .mmd_bone_names import convert_mmd_bone_name_to_ascii

    converted_name = convert_mmd_bone_name_to_ascii(name)
    if converted_name and converted_name[0].isdigit():
        return f"bone_{converted_name}"
    return converted_name or "default_name"


def set_custom_attributes(object_name, attributes):
    """
    Mayaオブジェクトにカスタムアトリビュートを設定します。
    存在しないアトリビュートは自動的に作成されます。
    cmds.setAttrの代わりにOpenMaya APIを使用して高速化します。

    Args:
        object_name (str): カスタムアトリビュートを設定するオブジェクトの名前。
        attributes (dict): 属性名と値の辞書。

    Example:
        set_custom_attributes("pCube1", {
            "customAttr1": 1.0,
            "customAttr2": "example",
            "customAttr3": [0.5, 0.5, 0.5],
        })
    """
    for attr_name, attr_value in attributes.items():
        attr_type = type(attr_value).__name__
        actual_attr_type = attr_type  # 実際に使用する型を保存
        if attr_type in ["list", "tuple"]:
            # リストやタプルの場合は型を指定
            actual_attr_type = _infer_sequence_attribute_type(attr_value)

        # アトリビュートが存在しない場合は作成
        if not cmds.attributeQuery(attr_name, node=object_name, exists=True):
            if attr_type in ["int", "float", "bool"]:
                add_numeric_attribute(object_name, attr_name, attr_type)
            elif attr_type in ["str", "bytes"]:
                add_typed_attribute(object_name, attr_name, attr_type)
            elif attr_type in ["list", "tuple"]:
                add_typed_attribute(object_name, attr_name, actual_attr_type)
                _ensure_compound_attribute_created(object_name, attr_name, actual_attr_type)

        # 値を設定（既存・新規両方に対応）
        try:
            if attr_type in ["int", "float", "bool", "str", "bytes"]:
                set_attribute(object_name, attr_name, attr_value, attr_type)
            elif attr_type in ["list", "tuple"]:
                set_attribute(object_name, attr_name, attr_value, actual_attr_type)
        except Exception as e:
            logger.warning(f"Failed to set attribute {attr_name} on {object_name}: {e}")


def add_numeric_attribute(object_name, attr_name, attr_type):
    """
    OpenMaya API 2.0を使用して数値型のアトリビュートを追加します。

    Args:
        object_name (str): オブジェクト名
        attr_name (str): アトリビュート名
        attr_type (str): アトリビュートタイプ (long, float, bool)
    """
    try:
        # オブジェクトのMObjectを取得
        selection_list = om.MSelectionList()
        selection_list.add(object_name)
        node_obj = selection_list.getDependNode(0)
        depend_fn = om.MFnDependencyNode(node_obj)

        # 数値アトリビュートを作成
        attr = om.MFnNumericAttribute()

        if attr_type == "int":
            attr_obj = attr.create(attr_name, attr_name, om.MFnNumericData.kInt)
        elif attr_type == "float":
            attr_obj = attr.create(attr_name, attr_name, om.MFnNumericData.kFloat)
        elif attr_type == "bool":
            attr_obj = attr.create(attr_name, attr_name, om.MFnNumericData.kBoolean)
        else:
            raise ValueError(f"Unsupported numeric attribute type: {attr_type}")

        # アトリビュートを追加
        depend_fn.addAttribute(attr_obj)

    except Exception as e:
        logger.error(f"Failed to add numeric attribute '{attr_name}' to '{object_name}': {e}")


def add_typed_attribute(object_name, attr_name, attr_type):
    """
    OpenMaya API 2.0を使用して型付きアトリビュートを追加します。

    Args:
        object_name (str): オブジェクト名
        attr_name (str): アトリビュート名
        attr_type (str): アトリビュートタイプ (string, double3, long3, double4, doubleArray, longArray)
    """
    try:
        # オブジェクトのMObjectを取得
        selection_list = om.MSelectionList()
        selection_list.add(object_name)
        node_obj = selection_list.getDependNode(0)
        depend_fn = om.MFnDependencyNode(node_obj)

        if attr_type == "str":
            # 文字列アトリビュート
            attr = om.MFnTypedAttribute()
            attr_obj = attr.create(attr_name, attr_name, om.MFnData.kString)
        elif attr_type == "bytes":
            # バイトアトリビュート
            attr = om.MFnTypedAttribute()
            attr_obj = attr.create(attr_name, attr_name, om.MFnData.kString)
        elif attr_type == "double3":
            # 3つのdouble値を持つアトリビュート
            attr = om.MFnNumericAttribute()
            attr_obj = attr.create(attr_name, attr_name, om.MFnNumericData.k3Double)
        elif attr_type == "long3":
            # 3つのint値を持つアトリビュート
            attr = om.MFnNumericAttribute()
            attr_obj = attr.create(attr_name, attr_name, om.MFnNumericData.k3Int)
        elif attr_type == "double4":
            # 4つのdouble値を持つアトリビュート
            attr = om.MFnNumericAttribute()
            attr_obj = attr.create(attr_name, attr_name, om.MFnNumericData.k4Double)
        elif attr_type == "doubleArray":
            # double配列アトリビュート
            attr = om.MFnTypedAttribute()
            attr_obj = attr.create(attr_name, attr_name, om.MFnData.kDoubleArray)
        elif attr_type == "longArray":
            # long配列アトリビュート
            attr = om.MFnTypedAttribute()
            attr_obj = attr.create(attr_name, attr_name, om.MFnData.kIntArray)
        else:
            raise ValueError(f"Unsupported typed attribute type: {attr_type}")

        # アトリビュートを追加
        depend_fn.addAttribute(attr_obj)

    except Exception as e:
        logger.error(f"Failed to add typed attribute '{attr_name}' to '{object_name}': {e}")


def _set_string_plug(plug, object_name, attr_name, value):
    """
    文字列アトリビュートを日本語・特殊文字でも安全に設定する。

    Maya 2024 on Windows では ``cmds.setAttr(..., type="string")`` が
    fileTextureName のような既存 string attr に CP932 非対応文字を含む
    パスを書き込むと ``?`` に置換することがある。OpenMaya API 2.0 の
    ``MPlug.setString()`` は同じ値を保持できるため、こちらを優先する。

    Args:
        plug (om.MPlug): 対象プラグ
        object_name (str): オブジェクト名
        attr_name (str): アトリビュート名
        value (str): 設定する文字列
    """
    text = "" if value is None else str(value)
    try:
        plug.setString(text)
        try:
            if plug.asString() == text:
                return
        except Exception:
            return
    except Exception as exc:
        logger.debug(f"MPlug.setString failed to set string; falling back to cmds.setAttr for '{attr_name}': {exc}")
    cmds.setAttr(f"{object_name}.{attr_name}", text, type="string")


_FBX_UTF8_AS_CP932_MARKERS = ("繧", "繝", "縺", "荳", "譁", "蜷", "髮", "驥")


def repair_fbx_mojibake_string(value):
    """Repair UTF-8 text imported by FBX as CP932 mojibake when detectable."""
    if not isinstance(value, str) or not value:
        return value
    if not any(marker in value for marker in _FBX_UTF8_AS_CP932_MARKERS):
        return value
    try:
        repaired = value.encode("cp932").decode("utf-8")
    except UnicodeError:
        return value
    if repaired == value:
        return value
    if any("\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff" for ch in repaired):
        return repaired
    return value


def set_attribute(object_name, attr_name, attr_value, attr_type):
    """
    OpenMaya API 2.0を使用してアトリビュート値を設定します。
    cmds.setAttrの代わりに使用します。

    Args:
        object_name (str): オブジェクト名
        attr_name (str): アトリビュート名
        attr_value: 設定する値
        attr_type (str, optional): アトリビュートタイプ（配列の場合に必要）

    Example:
        set_attribute("pCube1", "customAttr1", 1.0, "float")
        set_attribute("pCube1", "customAttr2", "example", "str")
        set_attribute("pCube1", "customAttr3", [0.5, 0.5, 0.5], "double3")
    """
    try:
        # オブジェクトのMObjectを取得
        selection_list = om.MSelectionList()
        selection_list.add(object_name)
        node_obj = selection_list.getDependNode(0)
        depend_fn = om.MFnDependencyNode(node_obj)

        # プラグを取得
        plug = depend_fn.findPlug(attr_name, False)

        # 値の型に応じて設定
        if attr_type == "bool":
            plug.setBool(attr_value)
        elif attr_type == "int" or attr_type == "long":
            plug.setInt(attr_value)
        elif attr_type == "float":
            plug.setFloat(attr_value)
        elif attr_type == "double":
            plug.setDouble(attr_value)
        elif attr_type == "str" or attr_type == "string":
            _set_string_plug(plug, object_name, attr_name, attr_value)
        elif attr_type == "bytes":
            # バイトデータは文字列として設定
            _set_string_plug(plug, object_name, attr_name, attr_value.decode("utf-8"))
        elif attr_type == "double3" and len(attr_value) == 3:
            # 3要素のベクトル値
            try:
                for i, value in enumerate(attr_value):
                    child_plug = plug.child(i)
                    child_plug.setDouble(value)
            except Exception:
                _set_compound_attribute_with_cmds(object_name, attr_name, attr_value, attr_type)
        elif attr_type == "long3" and len(attr_value) == 3:
            # 3要素の整数値
            try:
                for i, value in enumerate(attr_value):
                    child_plug = plug.child(i)
                    child_plug.setInt(value)
            except Exception:
                _set_compound_attribute_with_cmds(object_name, attr_name, attr_value, attr_type)
        elif attr_type == "double4" and len(attr_value) == 4:
            # 4要素のベクトル値
            try:
                for i, value in enumerate(attr_value):
                    child_plug = plug.child(i)
                    child_plug.setDouble(value)
            except Exception:
                _set_compound_attribute_with_cmds(object_name, attr_name, attr_value, attr_type)
        elif attr_type == "doubleArray":
            double_array_data = om.MFnDoubleArrayData()
            double_array_obj = double_array_data.create()
            double_array = om.MDoubleArray(attr_value)
            double_array_data.set(double_array)
            plug.setMObject(double_array_obj)
        elif attr_type == "longArray":
            int_array_data = om.MFnIntArrayData()
            int_array_obj = int_array_data.create()
            int_array = om.MIntArray(attr_value)
            int_array_data.set(int_array)
            plug.setMObject(int_array_obj)
        else:
            logger.warning(f"Unsupported attribute value type: {type(attr_value)}")

    except Exception as e:
        logger.error(f"Failed to set attribute value '{attr_name}' on '{object_name}': {e}")


def get_attribute(object_name, attr_name):
    """
    OpenMaya API 2.0を使用してアトリビュート値を取得します。
    cmds.getAttrの代わりに使用します。

    Args:
        object_name (str): オブジェクト名
        attr_name (str): アトリビュート名

    Returns:
        The value of the attribute, or None if it does not exist.
    """
    try:
        # オブジェクトのMObjectを取得
        selection_list = om.MSelectionList()
        selection_list.add(object_name)
        node_obj = selection_list.getDependNode(0)
        depend_fn = om.MFnDependencyNode(node_obj)

        # プラグを取得（存在しない場合は例外が発生）
        try:
            plug = depend_fn.findPlug(attr_name, False)
        except Exception:
            # アトリビュートが存在しない場合
            return None

        if plug.isNull:
            return None

        # 複合アトリビュートの場合
        if plug.isCompound:
            num_children = plug.numChildren()
            if num_children == 0:
                return None
            # 数値型の複合アトリビュート (double3, float3など)
            return tuple(plug.child(i).asDouble() for i in range(num_children))

        # 配列アトリビュートの場合
        if plug.isArray:
            return [plug.elementByLogicalIndex(i).asDouble() for i in range(plug.numElements)]

        # 単一アトリビュートの場合、タイプに応じて適切なメソッドを使用
        obj = plug.attribute()

        # 数値型チェック
        if obj.hasFn(om.MFn.kNumericAttribute):
            attr_fn = om.MFnNumericAttribute(obj)
            numeric_type = attr_fn.numericType()

            if numeric_type == om.MFnNumericData.kBoolean:
                return plug.asBool()
            elif numeric_type in [
                om.MFnNumericData.kInt,
                om.MFnNumericData.kLong,
                om.MFnNumericData.kByte,
                om.MFnNumericData.kShort,
            ]:
                return plug.asInt()
            elif numeric_type == om.MFnNumericData.kFloat:
                return plug.asFloat()
            elif numeric_type == om.MFnNumericData.kDouble:
                return plug.asDouble()

        # 型付きアトリビュート（文字列など）
        if obj.hasFn(om.MFn.kTypedAttribute):
            attr_fn = om.MFnTypedAttribute(obj)
            attr_type = attr_fn.attrType()

            if attr_type == om.MFnData.kString:
                value = plug.asString()
                if attr_name.startswith("mmd_"):
                    return repair_fbx_mojibake_string(value)
                return value

        # その他の場合、型を推測して取得
        # まずdoubleとして取得を試みる
        try:
            return plug.asDouble()
        except Exception:
            # 失敗したら文字列として取得
            try:
                return plug.asString()
            except Exception:
                return None

    except Exception:
        # オブジェクトが存在しない、その他のエラー
        return None


def attribute_exists(node, attr):
    """Return whether a Maya node has an attribute, swallowing Maya command errors."""
    try:
        return bool(node and cmds.objExists(node) and cmds.attributeQuery(attr, node=node, exists=True))
    except Exception:
        return False


def get_attr_safe(node, attr, default=None, cast=None):
    """Read ``node.attr`` with a default fallback and optional type conversion."""
    if not attribute_exists(node, attr.split("[", 1)[0]):
        return default
    try:
        value = cmds.getAttr(f"{node}.{attr}")
    except Exception:
        return default
    if cast is None:
        return value
    try:
        return cast(value)
    except (TypeError, ValueError):
        return default


def read_json_attr(node, attr, default=None):
    """Read a string JSON attribute, returning ``default`` when absent or invalid."""
    fallback = default if default is not None else {}
    raw_value = get_attr_safe(node, attr, default="")
    if not raw_value:
        return fallback
    try:
        return json.loads(raw_value)
    except (TypeError, ValueError):
        return fallback


def write_json_attr(node, attr, value, *, ensure_attr=True, separators=(",", ":")):
    """Write JSON data to a string attribute and optionally create the attribute."""
    if not attribute_exists(node, attr):
        if not ensure_attr:
            return False
        try:
            cmds.addAttr(node, longName=attr, dataType="string")
        except Exception:
            return False
    try:
        cmds.setAttr(
            f"{node}.{attr}",
            json.dumps(value, ensure_ascii=False, separators=separators),
            type="string",
        )
        return True
    except Exception:
        return False


def find_tagged_nodes(attr):
    """Return nodes that have a boolean-like tag attribute enabled."""
    nodes = []
    for node in cmds.ls(f"*.{attr}", objectsOnly=True) or []:
        if get_attr_safe(node, attr, default=False, cast=bool):
            nodes.append(node)
    return nodes


def mark_bool_tag(node, attr, value=True):
    """Ensure a bool tag attribute exists and set its value."""
    if not attribute_exists(node, attr):
        try:
            cmds.addAttr(node, longName=attr, attributeType="bool")
        except Exception:
            return False
    try:
        cmds.setAttr(f"{node}.{attr}", bool(value))
        return True
    except Exception:
        return False


def disconnect_sources(destination_plug):
    """Disconnect every source plug feeding ``destination_plug``."""
    disconnected = 0
    for source_plug in cmds.listConnections(destination_plug, source=True, destination=False, plugs=True) or []:
        try:
            if cmds.isConnected(source_plug, destination_plug):
                cmds.disconnectAttr(source_plug, destination_plug)
                disconnected += 1
        except Exception:
            continue
    return disconnected


def connect_if_needed(source_plug, destination_plug, *, force=False):
    """Connect two plugs when not already connected."""
    try:
        if cmds.isConnected(source_plug, destination_plug):
            return True
        if force:
            disconnect_sources(destination_plug)
        cmds.connectAttr(source_plug, destination_plug, force=force)
        return True
    except Exception:
        return False


def get_int_array_attribute(object_name, attr_name):
    """OpenMaya typed intArray attribute を Python の int list として取得する。"""
    try:
        selection_list = om.MSelectionList()
        selection_list.add(object_name)
        node_obj = selection_list.getDependNode(0)
        depend_fn = om.MFnDependencyNode(node_obj)
        plug = depend_fn.findPlug(attr_name, False)
        if plug.isNull:
            return None

        data_obj = plug.asMObject()
        if data_obj.isNull() or not data_obj.hasFn(om.MFn.kIntArrayData):
            return None

        int_array = om.MFnIntArrayData(data_obj).array()
        return [int(int_array[i]) for i in range(len(int_array))]
    except Exception:
        return None


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
        # パネル名が指定されていない場合、アクティブなパネルを取得
        if panel_name is None:
            panel_name = cmds.getPanel(withFocus=True)

            # アクティブパネルがモデルパネルでない場合、デフォルトのパネルを使用
            if not cmds.getPanel(typeOf=panel_name) == "modelPanel":
                panels = cmds.getPanel(type="modelPanel")
                if panels:
                    panel_name = panels[0]
                else:
                    logger.warning("No model panels found")
                    return False

        # バックフェイスカリングを設定
        cmds.modelEditor(panel_name, edit=True, backfaceCulling=enabled)

        logger.info(f"Backface culling {'enabled' if enabled else 'disabled'} for panel: {panel_name}")
        return True

    except Exception as e:
        logger.error(f"Failed to set backface culling: {e}")
        return False


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
    # ジョイントの存在確認
    if not cmds.objExists(start_joint):
        raise ValueError(f"Start joint '{start_joint}' does not exist")
    if not cmds.objExists(end_joint):
        raise ValueError(f"End joint '{end_joint}' does not exist")

    # ソルバーの妥当性確認
    valid_solvers = ["ikRPsolver", "ikSCsolver", "ikSplineSolver"]
    if solver not in valid_solvers:
        raise ValueError(f"Invalid solver '{solver}'. Must be one of: {valid_solvers}")

    try:
        # IKハンドルの作成
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

    # 回転制限の設定（ラジアンから度数に変換）
    if limit_min:
        set_attribute(joint, "minRotLimit", limit_min, "double3")

    if limit_max:
        set_attribute(joint, "maxRotLimit", limit_max, "double3")

    # 制限の有効化/無効化
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


def create_matrix_from_axes(x_axis, y_axis, z_axis):
    """
    3つの軸ベクトルから回転行列を作成する。

    Args:
        x_axis (list): X軸ベクトル [x, y, z]
        y_axis (list): Y軸ベクトル [x, y, z]
        z_axis (list): Z軸ベクトル [x, y, z]

    Returns:
        om.MMatrix: 回転行列
    """
    matrix = om.MMatrix()
    matrix.setElement(0, 0, x_axis[0])
    matrix.setElement(0, 1, x_axis[1])
    matrix.setElement(0, 2, x_axis[2])
    matrix.setElement(1, 0, y_axis[0])
    matrix.setElement(1, 1, y_axis[1])
    matrix.setElement(1, 2, y_axis[2])
    matrix.setElement(2, 0, z_axis[0])
    matrix.setElement(2, 1, z_axis[1])
    matrix.setElement(2, 2, z_axis[2])
    return matrix


def matrix_to_euler(matrix):
    """
    回転行列をオイラー角に変換する。

    Args:
        matrix (om.MMatrix): 回転行列

    Returns:
        list: オイラー角 [x, y, z] 度数法
    """
    transform_matrix = om.MTransformationMatrix(matrix)
    euler = transform_matrix.rotation(asQuaternion=False)
    # ラジアンから度に変換
    import math

    return [math.degrees(euler.x), math.degrees(euler.y), math.degrees(euler.z)]


def _parse_array_attribute_part(attr_part):
    if not attr_part.endswith("]") or "[" not in attr_part:
        return attr_part, None
    attr_name, _, index_text = attr_part[:-1].partition("[")
    try:
        return attr_name, int(index_text)
    except ValueError:
        return attr_part, None


def _find_plug(fn_depend, attr):
    """Find a simple, array element, or compound-array child plug."""
    try:
        return fn_depend.findPlug(attr, False)
    except Exception:
        pass

    plug = None
    for part in attr.split("."):
        attr_name, logical_index = _parse_array_attribute_part(part)
        if plug is None:
            plug = fn_depend.findPlug(attr_name, False)
        else:
            child = None
            for child_index in range(plug.numChildren()):
                candidate = plug.child(child_index)
                if candidate.partialName(useLongNames=True) == attr_name:
                    child = candidate
                    break
            if child is None:
                raise AttributeError(f"Attribute child '{attr_name}' not found")
            plug = child

        if logical_index is not None:
            plug = plug.elementByLogicalIndex(logical_index)

    if plug is None:
        raise AttributeError(f"Attribute '{attr}' not found")
    return plug


def _layer_curve_for_blend_attr(blend_node, attr, layer_curves):
    """Return the animLayer curve connected to the blend input for attr."""
    axis = attr[-1] if attr and attr[-1] in "XYZ" else ""
    candidate_inputs = []
    if axis:
        candidate_inputs.extend((f"inputB{axis}", f"inputB.inputB{axis}"))
    candidate_inputs.append("inputB")

    for input_attr in candidate_inputs:
        plug = f"{blend_node}.{input_attr}"
        if not cmds.objExists(plug):
            continue
        input_curves = cmds.listConnections(plug, source=True, destination=False, type="animCurve") or []
        for curve_name in input_curves:
            if curve_name in layer_curves:
                return curve_name

    return None


def create_animation_curves(
    node_name,
    attributes,
    tangent_type=oma.MFnAnimCurve.kTangentLinear,
    animation_layer=None,
    seed_values=None,
):
    """
    指定したノードの属性にアニメーションカーブを作成する。
    アニメーションレイヤーが指定されている場合は、レイヤー用のカーブを作成する。

    Args:
        node_name (str): ノード名
        attributes (list): アトリビュート名のリスト
        tangent_type: タンジェントタイプ（デフォルト: 線形）
        animation_layer (str, optional): アニメーションレイヤー名

    Returns:
        dict: アトリビュート名をキー、MFnAnimCurveオブジェクトを値とする辞書
    """
    # ノードを取得
    sel_list = om.MSelectionList()
    sel_list.add(node_name)
    node = sel_list.getDependNode(0)
    fn_depend = om.MFnDependencyNode(node)

    # アニメーションレイヤーが指定されている場合
    if animation_layer and cmds.animLayer(animation_layer, query=True, exists=True):
        for attr in attributes:
            base_attr = attr.split("[", 1)[0]
            if cmds.objExists(f"{node_name}.{attr}") or cmds.attributeQuery(base_attr, node=node_name, exists=True):
                cmds.animLayer(animation_layer, edit=True, attribute=f"{node_name}.{attr}")

    # 既存のアニメーションカーブをクリア（レイヤーモードでない場合のみ）
    if not animation_layer:
        for attr in attributes:
            connections = cmds.listConnections(f"{node_name}.{attr}", source=True, destination=False)
            if connections:
                cmds.delete(connections)

    # アニメーションカーブを作成
    curves = {}
    for attr in attributes:
        if animation_layer:
            # レイヤーが有効な場合は、cmds.setKeyframeを使って初期カーブを作成
            key_args = {"attribute": attr, "animLayer": animation_layer}
            if seed_values and attr in seed_values:
                key_args["value"] = float(seed_values[attr])
            cmds.setKeyframe(node_name, **key_args)
            # 作成されたカーブを取得。node.attr から直近の animBlendNode を辿ると、
            # レイヤー全体の blendNodes を毎属性スキャンせずに目的の curve へ到達できる。
            # 既存 base curve がある場合もあるため、返す curve は layer 所属に限定する。
            layer_curves = set(cmds.animLayer(animation_layer, query=True, animCurves=True) or [])
            blend_nodes = cmds.listConnections(
                f"{node_name}.{attr}",
                source=True,
                destination=False,
            ) or []
            for blend_node in blend_nodes:
                curve_name = _layer_curve_for_blend_attr(blend_node, attr, layer_curves)
                if curve_name:
                    curve_sel = om.MSelectionList()
                    curve_sel.add(curve_name)
                    curve_obj = curve_sel.getDependNode(0)
                    curves[attr] = oma.MFnAnimCurve(curve_obj)
                    break

                if attr and attr[-1] in "XYZ":
                    continue

                input_curves = cmds.listConnections(blend_node, source=True, type="animCurve") or []
                for curve_name in input_curves:
                    if curve_name not in layer_curves:
                        continue
                    curve_sel = om.MSelectionList()
                    curve_sel.add(curve_name)
                    curve_obj = curve_sel.getDependNode(0)
                    curves[attr] = oma.MFnAnimCurve(curve_obj)
                    break
                if attr in curves:
                    break
        else:
            # 通常のアニメーションカーブ作成
            curve = oma.MFnAnimCurve()
            plug = _find_plug(fn_depend, attr)
            curve.create(plug)
            curves[attr] = curve

    return curves


def set_keyframes_batch(
    curves,
    frame_data_list,
    value_generator_func,
    tangent_type=oma.MFnAnimCurve.kTangentLinear,
):
    """
    複数のアニメーションカーブに一括でキーフレームを設定する。
    アニメーションレイヤーのカーブも正しく処理される。

    Args:
        curves (dict): アトリビュート名をキー、MFnAnimCurveオブジェクトを値とする辞書
        frame_data_list (list): フレームデータのリスト
        value_generator_func: フレームデータから値を生成する関数
                             (frame_data) -> dict[attr_name, value]
        tangent_type: タンジェントタイプ（デフォルト: 線形）
    """
    import math

    # まず全てのキーを追加（高速化のため）
    for frame_data in frame_data_list:
        # 値を生成
        values = value_generator_func(frame_data)

        # フレーム番号を取得
        if hasattr(frame_data, "frame_number"):
            frame_num = frame_data.frame_number
        else:
            frame_num = frame_data.get("frame_number", 0)

        # MTimeオブジェクトを作成
        time = om.MTime(frame_num, om.MTime.uiUnit())

        # 各カーブにキーを設定
        for attr_name, curve in curves.items():
            if attr_name in values:
                value = values[attr_name]

                # 回転属性の場合はラジアンに変換
                if attr_name in ["rotateX", "rotateY", "rotateZ"]:
                    value = math.radians(value)

                try:
                    curve.addKey(time, value, tangent_type, tangent_type)
                except Exception:
                    # エラーが発生した場合はスキップ（レイヤーカーブの場合など）
                    logger.debug(f"Failed to add key for {attr_name} at frame {frame_num}")
                    pass


def find_all_mmd_models():
    """
    シーン内のすべてのMMDモデルのルートノードを検索します。

    Returns:
        list: MMDモデルのルートノード名のリスト
    """
    from ..core.constants import SCENE_ROOT_SUFFIX

    # *_rootという名前のトランスフォームノードを検索
    # namespace対応のため、ワイルドカードパターンを使用
    all_transforms = cmds.ls("*:*{}".format(SCENE_ROOT_SUFFIX), type="transform") + cmds.ls(
        "*{}".format(SCENE_ROOT_SUFFIX), type="transform"
    )

    # 重複を削除
    all_transforms = list(set(all_transforms))

    mmd_models = []
    for transform in all_transforms:
        # MMD関連のアトリビュートがあるか確認
        if cmds.attributeQuery(ATTR_MMD_MODEL_NAME, node=transform, exists=True) or cmds.attributeQuery(
            ATTR_MMD_MODEL_NAME_EN, node=transform, exists=True
        ):
            mmd_models.append(transform)

    return sorted(mmd_models)  # 名前順でソート


def get_parent_mmd_root(node_name):
    """
    指定されたノードの親階層からMMDモデルのルートノードを検索します。

    Args:
        node_name (str): 検索開始ノード名

    Returns:
        str: MMDモデルのルートノード名。見つからない場合はNone
    """
    from ..core.constants import SCENE_ROOT_SUFFIX

    try:
        # 現在のノードから親を辿る
        current = node_name
        while current:
            # ルートサフィックスを持ち、MMDアトリビュートがあるか確認
            if current.endswith(SCENE_ROOT_SUFFIX) and (
                cmds.attributeQuery(ATTR_MMD_MODEL_NAME, node=current, exists=True)
                or cmds.attributeQuery(ATTR_MMD_MODEL_NAME_EN, node=current, exists=True)
            ):
                return current

            # 親ノードを取得
            parents = cmds.listRelatives(current, parent=True, fullPath=True)
            if parents:
                current = parents[0]
            else:
                break

    except Exception as e:
        logger.warning(f"Failed to find parent MMD root for {node_name}: {e}")

    return None


def get_mmd_model_display_name(root_node):
    """
    MMDモデルの表示名を取得します。

    Args:
        root_node (str): MMDモデルのルートノード名

    Returns:
        str: 表示名（日本語名があれば優先、なければノード名）
    """
    try:
        if cmds.attributeQuery(ATTR_MMD_MODEL_NAME, node=root_node, exists=True):
            name_jp = cmds.getAttr(f"{root_node}.{ATTR_MMD_MODEL_NAME}")
            if name_jp:
                return name_jp

        if cmds.attributeQuery(ATTR_MMD_MODEL_NAME_EN, node=root_node, exists=True):
            name_en = cmds.getAttr(f"{root_node}.{ATTR_MMD_MODEL_NAME_EN}")
            if name_en:
                return name_en

    except Exception:
        pass

    # アトリビュートがない場合はノード名から_rootを除いて返す
    from ..core.constants import SCENE_ROOT_SUFFIX

    return root_node.replace(SCENE_ROOT_SUFFIX, "")


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
        # 現在の選択を取得
        current_selection = om.MGlobal.getActiveSelectionList()

        if clear or replace:
            # 選択をクリア
            om.MGlobal.setActiveSelectionList(om.MSelectionList())

        if objects is None:
            return True

        # 新しい選択リストを作成
        new_selection = om.MSelectionList()

        # 追加モードの場合は現在の選択を保持
        if add and not clear and not replace:
            new_selection = om.MSelectionList(current_selection)

        # オブジェクトを追加
        if isinstance(objects, str):
            objects = [objects]

        for obj in objects:
            try:
                new_selection.add(obj)
            except Exception:
                logger.warning(f"Could not add '{obj}' to selection")

        # 選択を設定
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
            # ワールド空間へ親付け
            result = cmds.parent(children, world=True)
        else:
            # 指定された親へ親付け
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

        # タイプに応じたイテレータを作成
        if type == "joint":
            it = om.MItDag(om.MItDag.kDepthFirst, om.MFn.kJoint)
        elif type == "mesh":
            it = om.MItDag(om.MItDag.kDepthFirst, om.MFn.kMesh)
        elif type == "transform":
            it = om.MItDag(om.MItDag.kDepthFirst, om.MFn.kTransform)
        elif type == "camera":
            it = om.MItDag(om.MItDag.kDepthFirst, om.MFn.kCamera)
        elif type == "blendShape":
            # blendShapeはDGノードなので別の方法で取得
            return _list_dg_nodes("blendShape", object_filter)
        elif type == "directionalLight":
            it = om.MItDag(om.MItDag.kDepthFirst, om.MFn.kDirectionalLight)
        else:
            # 全てのDAGオブジェクト
            it = om.MItDag(om.MItDag.kDepthFirst)

        while not it.isDone():
            try:
                dag_path = it.getPath()
                # fullPathフラグに応じて名前を取得
                if fullPath:
                    node_name = dag_path.fullPathName()
                else:
                    node_name = dag_path.partialPathName()

                # フィルターチェック
                if object_filter:
                    import fnmatch

                    if not fnmatch.fnmatch(node_name, object_filter):
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
    DGノード（非DAGノード）をリストする内部ヘルパー関数

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

                # フィルターチェック
                if object_filter:
                    import fnmatch

                    if not fnmatch.fnmatch(node_name, object_filter):
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


DX11_TEXTURE_SLOTS = _maya_material_utils.DX11_TEXTURE_SLOTS
ATTR_MMD_TEXTURE_SOURCE_KIND = _maya_material_utils.ATTR_MMD_TEXTURE_SOURCE_KIND
ATTR_MMD_SHARED_TOON_ID = _maya_material_utils.ATTR_MMD_SHARED_TOON_ID

sanitize_texture_path = _maya_material_utils.sanitize_texture_path
mark_mmd_texture_file_node = _maya_material_utils.mark_mmd_texture_file_node
get_mmd_original_texture_path = _maya_material_utils.get_mmd_original_texture_path
is_mmd_file_node_unreadable = _maya_material_utils.is_mmd_file_node_unreadable
find_material_texture_file_node = _maya_material_utils.find_material_texture_file_node
classify_mmd_texture_file_node = _maya_material_utils.classify_mmd_texture_file_node
resolve_mmd_texture_file_node = _maya_material_utils.resolve_mmd_texture_file_node
bind_dx11_texture_file_node = _maya_material_utils.bind_dx11_texture_file_node
create_material = _maya_material_utils.create_material
assign_material = _maya_material_utils.assign_material
assign_material_to_faces = _maya_material_utils.assign_material_to_faces

create_mesh_with_uvs = _maya_mesh_utils.create_mesh_with_uvs
split_mesh_by_material = _maya_mesh_utils.split_mesh_by_material
get_materials_from_mesh = _maya_mesh_utils.get_materials_from_mesh
apply_vertex_weights = _maya_mesh_utils.apply_vertex_weights
find_or_create_blendshape_node = _maya_mesh_utils.find_or_create_blendshape_node

find_or_create_nucleus_solver = _maya_physics_utils.find_or_create_nucleus_solver
create_dynamic_curve = _maya_physics_utils.create_dynamic_curve
apply_nhair_to_curve = _maya_physics_utils.apply_nhair_to_curve
apply_ncloth_to_mesh = _maya_physics_utils.apply_ncloth_to_mesh
create_collision_primitive = _maya_physics_utils.create_collision_primitive
apply_nrigid_to_mesh = _maya_physics_utils.apply_nrigid_to_mesh


def resolve_mmd_material_texture(material, workspace_root=None):
    """Resolve the selected material's base texture file node, if present."""
    file_node = find_material_texture_file_node(material)
    if not file_node:
        return None
    return resolve_mmd_texture_file_node(file_node, workspace_root=workspace_root)


def _dx11_texture_slot_from_attr(attr_name):
    for texture_attr, has_attr in DX11_TEXTURE_SLOTS.values():
        if attr_name == texture_attr:
            return texture_attr, has_attr
    return None


def _connected_dx11_texture_slot(file_node):
    connections = cmds.listConnections(
        f"{file_node}.outColor",
        source=False,
        destination=True,
        plugs=True,
    ) or []
    if not isinstance(connections, (list, tuple, set)):
        return None
    for plug in connections:
        if "." not in plug:
            continue
        shader, attr_name = plug.rsplit(".", 1)
        slot = _dx11_texture_slot_from_attr(attr_name)
        if slot and cmds.objExists(shader):
            return shader, slot[0], slot[1]
    return None


def _infer_dx11_texture_slot_from_file_node(file_node):
    sorted_slots = sorted(DX11_TEXTURE_SLOTS.items(), key=lambda item: len(item[0]), reverse=True)
    for suffix, (texture_attr, has_attr) in sorted_slots:
        if not file_node.endswith(suffix):
            continue
        shader = file_node[: -len(suffix)]
        if cmds.objExists(shader):
            return shader, texture_attr, has_attr
    return None


def rebind_resolved_mmd_dx11_texture(file_node):
    """Reconnect one file node to its dx11Shader texture slot through compatibility names."""
    target = _connected_dx11_texture_slot(file_node) or _infer_dx11_texture_slot_from_file_node(file_node)
    if not target:
        return {"status": "skipped", "reason": "dx11_texture_slot_not_found"}

    shader, texture_attr, has_attr = target
    if cmds.nodeType(shader) != "dx11Shader":
        return {"status": "skipped", "reason": "not_dx11_shader"}
    if not cmds.attributeQuery(texture_attr, node=shader, exists=True):
        return {"status": "skipped", "reason": "texture_attr_missing"}
    if not cmds.attributeQuery(has_attr, node=shader, exists=True):
        return {"status": "skipped", "reason": "has_attr_missing"}

    if not bind_dx11_texture_file_node(
        shader,
        file_node,
        texture_attr,
        has_attr,
        cmds_module=cmds,
        set_attribute_func=set_attribute,
    ):
        return {
            "status": "failed",
            "reason": "connect_failed",
            "shader": shader,
            "texture_attr": texture_attr,
            "has_attr": has_attr,
        }

    return {
        "status": "rebound",
        "reason": "connected",
        "shader": shader,
        "texture_attr": texture_attr,
        "has_attr": has_attr,
    }


def rebind_resolved_scene_mmd_dx11_textures(results):
    """Rebind resolved scene texture results through maya_utils-compatible names."""
    rebound = 0
    skipped = 0
    failed = 0
    for result in results:
        if getattr(result, "status", None) != "resolved":
            continue
        file_node = getattr(result, "file_node", None)
        if not file_node:
            setattr(result, "rebind_status", "skipped")
            setattr(result, "rebind_reason", "missing_file_node")
            skipped += 1
            continue
        rebind = rebind_resolved_mmd_dx11_texture(file_node)
        setattr(result, "rebind_status", rebind["status"])
        setattr(result, "rebind_reason", rebind["reason"])
        for key in ("shader", "texture_attr", "has_attr"):
            if key in rebind:
                setattr(result, f"rebind_{key}", rebind[key])
        if rebind["status"] == "rebound":
            rebound += 1
        elif rebind["status"] == "failed":
            failed += 1
        else:
            skipped += 1
    return {"rebound": rebound, "skipped": skipped, "failed": failed}


def resolve_scene_mmd_textures(workspace_root=None):
    """Resolve broken MMD file nodes in the current scene through compatibility names."""
    results = []
    for file_node in cmds.ls(type="file") or []:
        if not cmds.attributeQuery(
            _maya_material_utils.ATTR_MMD_ORIGINAL_TEXTURE_PATH,
            node=file_node,
            exists=True,
        ):
            continue
        classification = classify_mmd_texture_file_node(file_node)
        if classification and classification.status == "resolvable":
            resolution = resolve_mmd_texture_file_node(file_node, workspace_root=workspace_root)
            if resolution is not None and not getattr(resolution, "file_node", None):
                resolution.file_node = file_node
            results.append(resolution)
        elif classification:
            classification.file_node = file_node
            results.append(classification)
    rebind_summary = rebind_resolved_scene_mmd_dx11_textures(results)
    if rebind_summary["rebound"]:
        cmds.refresh(force=True)
    return results
