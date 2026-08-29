"""Maya attribute helper APIs used by importers, converters, and presenters."""

import json

from maya import cmds
from maya.api import OpenMaya as om

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
