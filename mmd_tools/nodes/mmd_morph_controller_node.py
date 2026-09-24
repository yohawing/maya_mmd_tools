"""Model-scoped, type-agnostic PMX Group morph weight expansion."""

import maya.api.OpenMaya as om

from mmd_tools.core.morph_topology import TOPOLOGY_VERSION, parse_group_topology


class MmdMorphControllerNode(om.MPxNode):
    """Expand authored morph weights through a fixed, flattened Group topology."""

    kTypeName = "mmdMorphController"
    kTypeId = om.MTypeId(0x0012800B)
    kClassify = "utility/general"
    kTopologyVersion = TOPOLOGY_VERSION

    aInputWeight = None
    aTopologyVersion = None
    aGroupTopology = None
    aOutputWeight = None

    def __init__(self):
        super().__init__()
        self._topology_cache_key = None
        self._topology_cache = {}
        self._topology_dependents = None

    def setDependentsDirty(self, plug, affected_plugs):
        """Dirty only cached topology dependents, falling back when state is uncertain."""
        N = type(self)
        if plug.attribute() not in (N.aInputWeight, N.aTopologyVersion, N.aGroupTopology):
            return

        node_fn = om.MFnDependencyNode(self.thisMObject())
        output_array = node_fn.findPlug(N.aOutputWeight, False)

        if plug.attribute() in (N.aTopologyVersion, N.aGroupTopology):
            self._invalidate_topology_cache()
            self._dirty_all_existing_outputs(output_array, affected_plugs)
            return

        if not plug.isElement or self._topology_dependents is None:
            self._dirty_all_existing_outputs(output_array, affected_plugs)
            return

        try:
            source_index = plug.logicalIndex()
            target_indices = {source_index}
            target_indices.update(self._topology_dependents.get(source_index, ()))
            existing_indices = set(output_array.getExistingArrayAttributeIndices())
        except (AttributeError, TypeError, ValueError):
            self._invalidate_topology_cache()
            self._dirty_all_existing_outputs(output_array, affected_plugs)
            return

        for logical_index in sorted(target_indices.intersection(existing_indices)):
            affected_plugs.append(output_array.elementByLogicalIndex(logical_index))

    @staticmethod
    def _dirty_all_existing_outputs(output_array, affected_plugs):
        """Preserve the conservative dirty contract for an uncertain topology."""
        affected_plugs.append(output_array)
        for logical_index in output_array.getExistingArrayAttributeIndices():
            affected_plugs.append(output_array.elementByLogicalIndex(logical_index))

    def _invalidate_topology_cache(self):
        """Prevent partial dirty propagation until compute parses topology again."""
        self._topology_cache_key = None
        self._topology_cache = {}
        self._topology_dependents = None

    def compute(self, plug, data):
        N = type(self)
        if plug.attribute() != N.aOutputWeight or not plug.isElement:
            return None

        topology_version = data.inputValue(N.aTopologyVersion).asInt()
        topology_source = data.inputValue(N.aGroupTopology).asString() or "{}"
        cache_key = (topology_version, topology_source)
        if cache_key != self._topology_cache_key or self._topology_dependents is None:
            self._invalidate_topology_cache()
            topology = self._parse_topology(topology_version, topology_source)
            dependents = {}
            for target_index, sources in topology.items():
                for source_index, _rate in sources:
                    dependents.setdefault(source_index, set()).add(target_index)
            self._topology_cache = topology
            self._topology_dependents = {
                source_index: tuple(sorted(target_indices))
                for source_index, target_indices in dependents.items()
            }
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
        return {
            int(target): tuple(sources)
            for target, sources in parse_group_topology(version, source).items()
        }


def creator():
    return MmdMorphControllerNode()


def initialize():
    N = MmdMorphControllerNode
    numeric = om.MFnNumericAttribute()
    typed = om.MFnTypedAttribute()

    N.aInputWeight = numeric.create("inputWeight", "iw", om.MFnNumericData.kDouble, 0.0)
    numeric.setMin(0.0)
    numeric.setMax(1.0)
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

    # Weight dependencies are element-specific in setDependentsDirty. A static
    # array-to-array edge also dirties every unrelated morph and its consumers.
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
