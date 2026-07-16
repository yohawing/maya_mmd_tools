"""Model-scoped, type-agnostic PMX Group morph weight expansion."""

import json

import maya.api.OpenMaya as om


class MmdMorphControllerNode(om.MPxNode):
    """Expand authored morph weights through a fixed, flattened Group topology."""

    kTypeName = "mmdMorphController"
    kTypeId = om.MTypeId(0x0012800B)
    kClassify = "utility/general"
    kTopologyVersion = 1

    aInputWeight = None
    aTopologyVersion = None
    aGroupTopology = None
    aOutputWeight = None

    def __init__(self):
        super().__init__()
        self._topology_cache_key = None
        self._topology_cache = {}

    def setDependentsDirty(self, plug, affected_plugs):
        """Dirty the output array and all existing elements without evaluating DG state."""
        N = type(self)
        if plug.attribute() not in (N.aInputWeight, N.aTopologyVersion, N.aGroupTopology):
            return

        node_fn = om.MFnDependencyNode(self.thisMObject())
        output_array = node_fn.findPlug(N.aOutputWeight, False)
        affected_plugs.append(output_array)
        for logical_index in output_array.getExistingArrayAttributeIndices():
            affected_plugs.append(output_array.elementByLogicalIndex(logical_index))

    def compute(self, plug, data):
        N = type(self)
        if plug.attribute() != N.aOutputWeight or not plug.isElement:
            return None

        topology_version = data.inputValue(N.aTopologyVersion).asInt()
        topology_source = data.inputValue(N.aGroupTopology).asString() or "{}"
        cache_key = (topology_version, topology_source)
        if cache_key != self._topology_cache_key:
            self._topology_cache = self._parse_topology(topology_version, topology_source)
            self._topology_cache_key = cache_key

        inputs = data.inputArrayValue(N.aInputWeight)

        def input_weight(logical_index):
            try:
                inputs.jumpToLogicalElement(logical_index)
            except RuntimeError:
                return 0.0
            return inputs.inputValue().asDouble()

        output_index = plug.logicalIndex()
        value = input_weight(output_index)
        value += sum(
            input_weight(group_index) * rate
            for group_index, rate in self._topology_cache.get(output_index, ())
        )

        outputs = data.outputArrayValue(N.aOutputWeight)
        builder = outputs.builder()
        builder.addElement(output_index).setDouble(value)
        outputs.set(builder)
        data.setClean(plug)

    @classmethod
    def _parse_topology(cls, version, source):
        if version != cls.kTopologyVersion:
            return {}
        try:
            parsed = json.loads(source)
            return {
                int(target): [(int(group), float(rate)) for group, rate in sources]
                for target, sources in parsed.items()
            }
        except (AttributeError, TypeError, ValueError):
            return {}


def creator():
    return MmdMorphControllerNode()


def initialize():
    N = MmdMorphControllerNode
    numeric = om.MFnNumericAttribute()
    typed = om.MFnTypedAttribute()

    N.aInputWeight = numeric.create("inputWeight", "iw", om.MFnNumericData.kDouble, 0.0)
    numeric.array = True
    numeric.usesArrayDataBuilder = True
    numeric.keyable = True
    N.addAttribute(N.aInputWeight)

    N.aTopologyVersion = numeric.create(
        "topologyVersion", "tv", om.MFnNumericData.kInt, N.kTopologyVersion
    )
    numeric.keyable = False
    N.addAttribute(N.aTopologyVersion)

    N.aGroupTopology = typed.create("groupTopology", "gt", om.MFnData.kString)
    typed.keyable = False
    N.addAttribute(N.aGroupTopology)

    N.aOutputWeight = numeric.create("outputWeight", "ow", om.MFnNumericData.kDouble, 0.0)
    numeric.array = True
    numeric.usesArrayDataBuilder = True
    numeric.writable = False
    numeric.storable = False
    N.addAttribute(N.aOutputWeight)

    N.attributeAffects(N.aInputWeight, N.aOutputWeight)
    N.attributeAffects(N.aTopologyVersion, N.aOutputWeight)
    N.attributeAffects(N.aGroupTopology, N.aOutputWeight)


def register(plugin_fn):
    plugin_fn.registerNode(
        MmdMorphControllerNode.kTypeName,
        MmdMorphControllerNode.kTypeId,
        creator,
        initialize,
        om.MPxNode.kDependNode,
        MmdMorphControllerNode.kClassify,
    )


def deregister(plugin_fn):
    try:
        plugin_fn.deregisterNode(MmdMorphControllerNode.kTypeId)
    except Exception:
        pass
