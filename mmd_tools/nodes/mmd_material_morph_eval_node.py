"""mmdMaterialMorphEval — PMX material morph offsets を shader パラメータに合成する DG ノード.

各 PMX マテリアルモーフ network node の weight と保存済み offset を contribution[]
として受け取り、baseDiffuse に加算/乗算した結果を outputDiffuse として出力する。
operation_type=0 は乗算 (lerp toward offset)、1 は加算。
"""

from __future__ import annotations

import maya.api.OpenMaya as om


def maya_useNewAPI():
    pass


class MmdMaterialMorphEvalNode(om.MPxNode):
    """PMX material morph contribution evaluator."""

    kTypeName = "mmdMaterialMorphEval"
    kTypeId = om.MTypeId(0x00128004)
    kClassify = "utility/general"

    # --- base inputs ---
    aBaseDiffuse = None
    aBaseDiffuseR = None
    aBaseDiffuseG = None
    aBaseDiffuseB = None

    # --- contribution array ---
    aContribution = None
    aContributionWeight = None
    aOperationType = None
    aMorphOrder = None
    aDiffuseOffsetR = None
    aDiffuseOffsetG = None
    aDiffuseOffsetB = None
    aDiffuseOffset = None

    # --- outputs ---
    aOutputDiffuse = None
    aOutputDiffuseR = None
    aOutputDiffuseG = None
    aOutputDiffuseB = None

    def compute(self, plug, data):
        is_output = (
            plug == self.aOutputDiffuse
            or plug == self.aOutputDiffuseR
            or plug == self.aOutputDiffuseG
            or plug == self.aOutputDiffuseB
        )
        if not is_output:
            return None

        r = data.inputValue(self.aBaseDiffuseR).asDouble()
        g = data.inputValue(self.aBaseDiffuseG).asDouble()
        b = data.inputValue(self.aBaseDiffuseB).asDouble()

        contributions = self._read_contributions(data)
        for c in contributions:
            w = c["weight"]
            op = c["op"]
            dr, dg, db = c["diffuse"]
            if op == 1:
                r += w * dr
                g += w * dg
                b += w * db
            else:
                r *= (1.0 - w) + w * dr
                g *= (1.0 - w) + w * dg
                b *= (1.0 - w) + w * db

        out = data.outputValue(self.aOutputDiffuse)
        out.set3Double(r, g, b)
        out.setClean()
        data.setClean(plug)

    def _read_contributions(self, data):
        contributions = []
        try:
            array_handle = data.inputArrayValue(self.aContribution)
        except Exception:
            return contributions

        while not array_handle.isDone():
            try:
                logical_index = array_handle.elementLogicalIndex()
                elem = array_handle.inputValue()
                weight = elem.child(self.aContributionWeight).asFloat()
                op = elem.child(self.aOperationType).asShort()
                morph_order = elem.child(self.aMorphOrder).asInt()
                diffuse = elem.child(self.aDiffuseOffset)
                dr = diffuse.child(self.aDiffuseOffsetR).asDouble()
                dg = diffuse.child(self.aDiffuseOffsetG).asDouble()
                db = diffuse.child(self.aDiffuseOffsetB).asDouble()
                contributions.append({
                    "logical_index": logical_index,
                    "morph_order": morph_order,
                    "weight": float(weight),
                    "op": int(op),
                    "diffuse": (dr, dg, db),
                })
            except Exception:
                pass
            array_handle.next()

        contributions.sort(key=lambda c: (c["morph_order"], c["logical_index"]))
        return contributions


def creator():
    return MmdMaterialMorphEvalNode()


def initialize():
    nAttr = om.MFnNumericAttribute()
    cAttr = om.MFnCompoundAttribute()
    eAttr = om.MFnEnumAttribute()

    # --- base diffuse ---
    N = MmdMaterialMorphEvalNode
    N.aBaseDiffuseR = nAttr.create("baseDiffuseR", "bdr", om.MFnNumericData.kDouble, 0.0)
    N.aBaseDiffuseG = nAttr.create("baseDiffuseG", "bdg", om.MFnNumericData.kDouble, 0.0)
    N.aBaseDiffuseB = nAttr.create("baseDiffuseB", "bdb", om.MFnNumericData.kDouble, 0.0)
    cAttr2 = om.MFnCompoundAttribute()
    N.aBaseDiffuse = cAttr2.create("baseDiffuse", "bd")
    cAttr2.addChild(N.aBaseDiffuseR)
    cAttr2.addChild(N.aBaseDiffuseG)
    cAttr2.addChild(N.aBaseDiffuseB)
    cAttr2.keyable = True
    N.addAttribute(N.aBaseDiffuse)

    # --- contribution compound array ---
    N.aContributionWeight = nAttr.create("weight", "w", om.MFnNumericData.kFloat, 0.0)
    nAttr.keyable = True

    N.aOperationType = eAttr.create("operationType", "ot", 1)
    eAttr.addField("multiply", 0)
    eAttr.addField("add", 1)

    N.aMorphOrder = nAttr.create("morphOrder", "mo", om.MFnNumericData.kInt, 0)

    N.aDiffuseOffsetR = nAttr.create("diffuseOffsetR", "dor", om.MFnNumericData.kDouble, 0.0)
    N.aDiffuseOffsetG = nAttr.create("diffuseOffsetG", "dog", om.MFnNumericData.kDouble, 0.0)
    N.aDiffuseOffsetB = nAttr.create("diffuseOffsetB", "dob", om.MFnNumericData.kDouble, 0.0)
    cAttr3 = om.MFnCompoundAttribute()
    N.aDiffuseOffset = cAttr3.create("diffuseOffset", "do")
    cAttr3.addChild(N.aDiffuseOffsetR)
    cAttr3.addChild(N.aDiffuseOffsetG)
    cAttr3.addChild(N.aDiffuseOffsetB)

    N.aContribution = cAttr.create("contribution", "ctb")
    cAttr.addChild(N.aContributionWeight)
    cAttr.addChild(N.aOperationType)
    cAttr.addChild(N.aMorphOrder)
    cAttr.addChild(N.aDiffuseOffset)
    cAttr.array = True
    cAttr.usesArrayDataBuilder = True
    N.addAttribute(N.aContribution)

    # --- output diffuse ---
    N.aOutputDiffuseR = nAttr.create("outputDiffuseR", "odr", om.MFnNumericData.kDouble, 0.0)
    nAttr.writable = False
    nAttr.storable = False
    N.aOutputDiffuseG = nAttr.create("outputDiffuseG", "odg", om.MFnNumericData.kDouble, 0.0)
    nAttr.writable = False
    nAttr.storable = False
    N.aOutputDiffuseB = nAttr.create("outputDiffuseB", "odb", om.MFnNumericData.kDouble, 0.0)
    nAttr.writable = False
    nAttr.storable = False
    cAttr4 = om.MFnCompoundAttribute()
    N.aOutputDiffuse = cAttr4.create("outputDiffuse", "od")
    cAttr4.writable = False
    cAttr4.storable = False
    cAttr4.addChild(N.aOutputDiffuseR)
    cAttr4.addChild(N.aOutputDiffuseG)
    cAttr4.addChild(N.aOutputDiffuseB)
    N.addAttribute(N.aOutputDiffuse)

    # --- attributeAffects ---
    output_attrs = (N.aOutputDiffuseR, N.aOutputDiffuseG, N.aOutputDiffuseB)
    for base_attr in (N.aBaseDiffuseR, N.aBaseDiffuseG, N.aBaseDiffuseB):
        for out_attr in output_attrs:
            N.attributeAffects(base_attr, out_attr)

    for contrib_attr in (
        N.aContribution,
        N.aContributionWeight,
        N.aOperationType,
        N.aMorphOrder,
        N.aDiffuseOffsetR,
        N.aDiffuseOffsetG,
        N.aDiffuseOffsetB,
    ):
        for out_attr in output_attrs:
            N.attributeAffects(contrib_attr, out_attr)


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
