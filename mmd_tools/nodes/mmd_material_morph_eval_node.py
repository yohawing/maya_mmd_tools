"""PMX material morph の全数値 channel を合成する Maya DG node.

shader routing とは独立した evaluator で、material/scalar は Saba/MME と同じく
``base * multiply_product + add_sum``、texture 3 系は multiply/add を別々に出力する。
"""

from __future__ import annotations

import maya.api.OpenMaya as om


def maya_useNewAPI():
    pass


_MATERIAL_CHANNELS = {
    "diffuse": 4,
    "specular": 3,
    "specularCoefficient": 1,
    "ambient": 3,
    "edgeColor": 4,
    "edgeSize": 1,
}
_TEXTURE_CHANNELS = {
    "texture": 4,
    "sphereTexture": 4,
    "toonTexture": 4,
}


class MmdMaterialMorphEvalNode(om.MPxNode):
    """Deterministic PMX material morph contribution evaluator."""

    kTypeName = "mmdMaterialMorphEval"
    kTypeId = om.MTypeId(0x00128004)
    kClassify = "utility/general"

    aContribution = None
    aContributionWeight = None
    aOperationType = None
    aMorphOrder = None
    _base_attrs = {}
    _offset_attrs = {}
    _output_attrs = {}
    _base_children = {}
    _offset_children = {}
    _output_children = {}
    _all_output_attrs = ()

    @staticmethod
    def _plug_matches_any(plug, attributes):
        """Return whether *plug* matches any initialized output attribute."""
        for attr in attributes:
            if attr is None:
                continue
            try:
                if plug == attr or plug.attribute() == attr:
                    return True
            except Exception:
                pass
        return False

    @staticmethod
    def compose(base, contributions):
        """Compose plain numeric values; kept pure for semantic regression tests."""
        mul = {name: [1.0] * size for name, size in _MATERIAL_CHANNELS.items()}
        add = {name: [0.0] * size for name, size in _MATERIAL_CHANNELS.items()}
        tex_mul = {name: [1.0] * 4 for name in _TEXTURE_CHANNELS}
        tex_add = {name: [0.0] * 4 for name in _TEXTURE_CHANNELS}
        for contribution in sorted(
            contributions,
            key=lambda item: (item["morph_order"], item["logical_index"]),
        ):
            weight = contribution["weight"]
            target = add if contribution["op"] == 1 else mul
            texture_target = tex_add if contribution["op"] == 1 else tex_mul
            for name, size in _MATERIAL_CHANNELS.items():
                values = contribution[name]
                for index in range(size):
                    if contribution["op"] == 1:
                        target[name][index] += values[index] * weight
                    else:
                        target[name][index] *= 1.0 + (values[index] - 1.0) * weight
            for name in _TEXTURE_CHANNELS:
                values = contribution[name]
                for index in range(4):
                    if contribution["op"] == 1:
                        texture_target[name][index] += values[index] * weight
                    else:
                        texture_target[name][index] *= 1.0 + (values[index] - 1.0) * weight
        material = {
            name: tuple(base[name][i] * mul[name][i] + add[name][i] for i in range(size))
            for name, size in _MATERIAL_CHANNELS.items()
        }
        return material, tex_mul, tex_add

    def compute(self, plug, data):
        N = type(self)
        if not self._plug_matches_any(plug, N._all_output_attributes()):
            return None
        base = {
            name: self._read_value(
                data.inputValue(N._base_attrs[name]), size, N._base_children[name]
            )
            for name, size in _MATERIAL_CHANNELS.items()
        }
        material, tex_mul, tex_add = self.compose(base, self._read_contributions(data))
        for name, values in material.items():
            title = f"{name[0].upper()}{name[1:]}"
            if name == "diffuse":
                attr = N._output_attrs["outputDiffuse"]
                self._write_value(data.outputValue(attr), values[:3], N._output_children["outputDiffuse"])
                attr = N._output_attrs["outputDiffuseAlpha"]
                self._write_value(data.outputValue(attr), values[3:], N._output_children["outputDiffuseAlpha"])
            else:
                attr = N._output_attrs[f"output{title}"]
                self._write_value(data.outputValue(attr), values, N._output_children[f"output{title}"])
        for name in _TEXTURE_CHANNELS:
            title = f"{name[0].upper()}{name[1:]}"
            attr = N._output_attrs[f"output{title}Multiply"]
            self._write_value(data.outputValue(attr), tex_mul[name], N._output_children[f"output{title}Multiply"])
            attr = N._output_attrs[f"output{title}Add"]
            self._write_value(data.outputValue(attr), tex_add[name], N._output_children[f"output{title}Add"])
        data.setClean(plug)

    @staticmethod
    def _read_value(handle, size, children):
        if size == 1:
            return (handle.asDouble(),)
        return tuple(handle.child(child).asDouble() for child in children)

    @staticmethod
    def _write_value(handle, values, children):
        if len(values) == 1:
            handle.setDouble(values[0])
        elif len(values) == 3:
            handle.set3Double(*values)
        else:
            for child, value in zip(children, values):
                handle.child(child).setDouble(value)
        handle.setClean()

    def _read_contributions(self, data):
        N = type(self)
        result = []
        try:
            array_handle = data.inputArrayValue(N.aContribution)
        except Exception:
            return result
        while not array_handle.isDone():
            try:
                elem = array_handle.inputValue()
                item = {
                    "logical_index": array_handle.elementLogicalIndex(),
                    "morph_order": elem.child(N.aMorphOrder).asInt(),
                    "weight": float(elem.child(N.aContributionWeight).asFloat()),
                    "op": int(elem.child(N.aOperationType).asShort()),
                }
                for name, size in {**_MATERIAL_CHANNELS, **_TEXTURE_CHANNELS}.items():
                    attr = N._offset_attrs[name]
                    item[name] = self._read_value(elem.child(attr), size, N._offset_children[name])
                result.append(item)
            except Exception:
                pass
            array_handle.next()
        return result

    @classmethod
    def _all_output_attributes(cls):
        return cls._all_output_attrs


def _numeric_attr(name, short_name, size, default, *, output=False):
    n_attr = om.MFnNumericAttribute()
    if size == 1:
        attr = n_attr.create(name, short_name, om.MFnNumericData.kDouble, default)
        if output:
            n_attr.writable = False
            n_attr.storable = False
        return attr, ()
    children = []
    suffixes = "RGBA"[:size]
    for suffix in suffixes:
        child = n_attr.create(
            f"{name}{suffix}",
            f"{short_name}{suffix.lower()}",
            om.MFnNumericData.kDouble,
            default,
        )
        if output:
            n_attr.writable = False
            n_attr.storable = False
        children.append(child)
    c_attr = om.MFnCompoundAttribute()
    parent = c_attr.create(name, short_name)
    for child in children:
        c_attr.addChild(child)
    if output:
        c_attr.writable = False
        c_attr.storable = False
    return parent, tuple(children)


def creator():
    return MmdMaterialMorphEvalNode()


def initialize():
    N = MmdMaterialMorphEvalNode
    N._base_attrs = {}
    N._offset_attrs = {}
    N._output_attrs = {}
    N._base_children = {}
    N._offset_children = {}
    N._output_children = {}
    N._all_output_attrs = ()

    for name, size in _MATERIAL_CHANNELS.items():
        title = f"{name[0].upper()}{name[1:]}"
        attr, children = _numeric_attr(f"base{title}", f"base{title}", size, 0.0)
        N._base_attrs[name] = attr
        N._base_children[name] = children
        N.addAttribute(attr)

    n_attr = om.MFnNumericAttribute()
    e_attr = om.MFnEnumAttribute()
    c_attr = om.MFnCompoundAttribute()
    N.aContributionWeight = n_attr.create("weight", "w", om.MFnNumericData.kFloat, 0.0)
    N.aOperationType = e_attr.create("operationType", "ot", 1)
    e_attr.addField("multiply", 0)
    e_attr.addField("add", 1)
    N.aMorphOrder = n_attr.create("morphOrder", "mo", om.MFnNumericData.kInt, 0)
    offset_attrs = []
    for name, size in {**_MATERIAL_CHANNELS, **_TEXTURE_CHANNELS}.items():
        attr, children = _numeric_attr(f"{name}Offset", f"{name}Offset", size, 0.0)
        N._offset_attrs[name] = attr
        N._offset_children[name] = children
        offset_attrs.append(attr)
    N.aContribution = c_attr.create("contribution", "ctb")
    for attr in (N.aContributionWeight, N.aOperationType, N.aMorphOrder, *offset_attrs):
        c_attr.addChild(attr)
    c_attr.array = True
    c_attr.usesArrayDataBuilder = True
    N.addAttribute(N.aContribution)

    output_specs = dict(_MATERIAL_CHANNELS)
    output_specs["diffuse"] = 3
    output_specs["diffuseAlpha"] = 1
    for name in _TEXTURE_CHANNELS:
        title = f"{name[0].upper()}{name[1:]}"
        output_specs[f"{title}Multiply"] = 4
        output_specs[f"{title}Add"] = 4
    for name, size in output_specs.items():
        title = f"{name[0].upper()}{name[1:]}"
        attr, children = _numeric_attr(
            f"output{title}",
            f"output{title}",
            size,
            0.0,
            output=True,
        )
        output_name = f"output{title}"
        N._output_attrs[output_name] = attr
        N._output_children[output_name] = children
        N.addAttribute(attr)

    N._all_output_attrs = tuple(
        attr
        for name, parent in N._output_attrs.items()
        for attr in (parent, *N._output_children[name])
    )

    for source in (*N._base_attrs.values(), N.aContribution, N.aContributionWeight,
                   N.aOperationType, N.aMorphOrder, *N._offset_attrs.values()):
        for output in N._output_attrs.values():
            N.attributeAffects(source, output)


def register(plugin_fn):
    plugin_fn.registerNode(
        MmdMaterialMorphEvalNode.kTypeName,
        MmdMaterialMorphEvalNode.kTypeId,
        creator,
        initialize,
        om.MPxNode.kDependNode,
        MmdMaterialMorphEvalNode.kClassify,
    )


def deregister(plugin_fn):
    try:
        plugin_fn.deregisterNode(MmdMaterialMorphEvalNode.kTypeId)
    except Exception:
        pass
