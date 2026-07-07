import json

from maya import cmds
from maya.api import OpenMaya as om



from . import maya_animation_utils as _maya_animation_utils
from . import maya_material_utils as _maya_material_utils
from . import maya_mesh_utils as _maya_mesh_utils
from . import maya_physics_utils as _maya_physics_utils
from . import maya_transform_utils as _maya_transform_utils
from . import maya_viewport_utils as _maya_viewport_utils
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


set_viewport_backface_culling = _maya_viewport_utils.set_viewport_backface_culling


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


create_matrix_from_axes = _maya_transform_utils.create_matrix_from_axes
matrix_to_euler = _maya_transform_utils.matrix_to_euler

create_animation_curves = _maya_animation_utils.create_animation_curves
set_keyframes_batch = _maya_animation_utils.set_keyframes_batch



def find_all_mmd_models():
    """
    シーン内のすべてのMMDモデルのルートノードを検索します。

    Returns:
        list: MMDモデルのルートノード名のリスト
    """
    return _scene_model_service().list_mmd_models()


def get_parent_mmd_root(node_name):
    """
    指定されたノードの親階層からMMDモデルのルートノードを検索します。

    Args:
        node_name (str): 検索開始ノード名

    Returns:
        str: MMDモデルのルートノード名。見つからない場合はNone
    """
    return _scene_model_service().get_parent_mmd_root(node_name)


def get_mmd_model_display_name(root_node):
    """
    MMDモデルの表示名を取得します。

    Args:
        root_node (str): MMDモデルのルートノード名

    Returns:
        str: 表示名（日本語名があれば優先、なければノード名）
    """
    return _scene_model_service().get_model_display_name(root_node)


def _scene_model_service():
    """Return a service bound to this module's patchable Maya cmds object."""
    from ..services.scene_model_service import SceneModelService

    return SceneModelService(cmds_module=cmds)


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


def rebind_resolved_mmd_dx11_texture(file_node):
    """Compatibility wrapper for the material texture rebinding helper."""
    return _maya_material_utils.rebind_resolved_mmd_dx11_texture(file_node)


def rebind_resolved_scene_mmd_dx11_textures(results):
    """Compatibility wrapper for scene-level material texture rebinding."""
    return _maya_material_utils.rebind_resolved_scene_mmd_dx11_textures(results)


def resolve_scene_mmd_textures(workspace_root=None):
    """Compatibility wrapper for scene-level material texture resolution."""
    return _maya_material_utils.resolve_scene_mmd_textures(workspace_root=workspace_root)
