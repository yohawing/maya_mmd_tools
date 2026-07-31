"""Animation curve helpers for Maya API keying paths."""

import math

from maya import cmds
from maya.api import OpenMaya as om
from maya.api import OpenMayaAnim as oma

from .logger import get_logger

logger = get_logger(__name__)


def is_plug_animated_or_driven(plug, *, cmds_module=None):
    """Return whether a Maya plug has an incoming driver or keyed samples.

    Args:
        plug (str): Fully qualified Maya plug (for example ``node.rotateX``).
        cmds_module: Optional Maya ``cmds``-compatible facade used by tests.

    Returns:
        bool: ``True`` when an incoming connection or one or more key times
        are present; ``False`` when neither can be observed.
    """

    maya_cmds = cmds if cmds_module is None else cmds_module
    try:
        incoming = maya_cmds.listConnections(
            plug,
            source=True,
            destination=False,
            plugs=True,
        ) or []
    except Exception:
        try:
            incoming = maya_cmds.listConnections(
                plug,
                source=True,
                destination=False,
            ) or []
        except Exception:
            incoming = []
    if incoming:
        return True

    try:
        keyed = maya_cmds.keyframe(plug, query=True, timeChange=True) or []
    except Exception:
        keyed = []
    if keyed:
        return True
    try:
        node, attribute = str(plug).split(".", 1)
        keyed = maya_cmds.keyframe(
            node,
            attribute=attribute,
            query=True,
            timeChange=True,
        ) or []
    except Exception:
        keyed = []
    return bool(keyed)


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
                candidate_name = candidate.partialName(useLongNames=True)
                # Array-element child plugs include their full parent path in
                # ``partialName`` (for example
                # ``inputRotate[6].inputRotateElementX``).  Compare the
                # terminal attribute segment so nested compound-array paths
                # resolve consistently across Maya 2024/2026.
                candidate_name = candidate_name.rsplit(".", 1)[-1].split("[", 1)[0]
                if candidate_name == attr_name:
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
    sel_list = om.MSelectionList()
    sel_list.add(node_name)
    node = sel_list.getDependNode(0)
    fn_depend = om.MFnDependencyNode(node)

    if animation_layer and cmds.animLayer(animation_layer, query=True, exists=True):
        for attr in attributes:
            base_attr = attr.split("[", 1)[0]
            if cmds.objExists(f"{node_name}.{attr}") or cmds.attributeQuery(base_attr, node=node_name, exists=True):
                cmds.animLayer(animation_layer, edit=True, attribute=f"{node_name}.{attr}")

    curves = {}
    for attr in attributes:
        if animation_layer:
            key_args = {"attribute": attr, "animLayer": animation_layer}
            if seed_values and attr in seed_values:
                key_args["value"] = float(seed_values[attr])
            cmds.setKeyframe(node_name, **key_args)
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
            destination = f"{node_name}.{attr}"
            source_plugs = cmds.listConnections(
                destination,
                source=True,
                destination=False,
                plugs=True,
                skipConversionNodes=False,
            ) or []
            if source_plugs:
                source_nodes = list(dict.fromkeys(str(plug).split(".", 1)[0] for plug in source_plugs))
                if len(source_nodes) != 1 or not str(cmds.nodeType(source_nodes[0])).startswith("animCurve"):
                    raise RuntimeError(
                        f"cannot replace non-animCurve input on {destination}: {source_plugs!r}"
                    )
                curve_sel = om.MSelectionList()
                curve_sel.add(source_nodes[0])
                curves[attr] = oma.MFnAnimCurve(curve_sel.getDependNode(0))
                continue
            curve = oma.MFnAnimCurve()
            plug = _find_plug(fn_depend, attr)
            # Passing only an MPlug is ambiguous in Maya 2024's Python API:
            # the overload dispatcher may select ``create(animCurveType)``
            # and attempt to marshal the plug as an integer.  Supplying the
            # explicit ``Unknown`` type selects the documented MPlug
            # overload while retaining Maya's destination-driven inference.
            curve.create(plug, oma.MFnAnimCurve.kAnimCurveUnknown)
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
    for frame_data in frame_data_list:
        values = value_generator_func(frame_data)

        if hasattr(frame_data, "frame_number"):
            frame_num = frame_data.frame_number
        else:
            frame_num = frame_data.get("frame_number", 0)

        time = om.MTime(frame_num, om.MTime.uiUnit())

        for attr_name, curve in curves.items():
            if attr_name in values:
                value = values[attr_name]

                if attr_name in ["rotateX", "rotateY", "rotateZ"]:
                    value = math.radians(value)

                try:
                    curve.addKey(time, value, tangent_type, tangent_type)
                except Exception:
                    logger.debug(f"Failed to add key for {attr_name} at frame {frame_num}")
                    pass
