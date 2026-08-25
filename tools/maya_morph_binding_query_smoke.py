"""Maya standalone parity and performance smoke for native morph observations."""

from __future__ import annotations

import json
import os
import statistics
import time

plugin = os.environ.get("MMD_TOOLS_CPP_PLUGIN")
if not plugin:
    version = os.environ.get("MAYA_VERSION", "2024")
    config = os.environ.get("MMD_TOOLS_CPP_CONFIG", "Debug")
    plugin = os.path.abspath(os.path.join("plug-ins", version, config, "mmd_tools_cpp.mll"))
os.environ["PATH"] = os.path.dirname(plugin) + os.pathsep + os.environ.get("PATH", "")
_dll_directory = os.add_dll_directory(os.path.dirname(plugin)) if hasattr(os, "add_dll_directory") else None

import maya.standalone  # noqa: E402

maya.standalone.initialize(name="python")
from maya import cmds  # noqa: E402

from mmd_tools.adapters.maya_cmds_adapter import MayaCmdsAdapter  # noqa: E402
from mmd_tools.adapters.maya_morph_binding_query import resolve_maya_morph_binding  # noqa: E402
from mmd_tools.adapters.native_morph_binding_query import NativeMorphBindingQueryGateway  # noqa: E402
from mmd_tools.core.morph_binding_resolver import MorphBindingRequest  # noqa: E402


class CountingAdapter(MayaCmdsAdapter):
    def __init__(self, module):
        super().__init__(module)
        self.calls = 0

    def __getattribute__(self, name):
        value = super().__getattribute__(name)
        if name in {
            "list_connections", "ls", "node_type", "alias_attr", "attribute_exists", "get_attr",
            "command_exists", "invoke_native_command",
        } and callable(value):
            def counted(*args, **kwargs):
                self.calls += 1
                return value(*args, **kwargs)
            return counted
        return value


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round((len(ordered) - 1) * fraction)))]


cmds.loadPlugin(plugin, quiet=True)
assert callable(getattr(cmds, "mmdAuthoringQueryMorphBindings", None))
cmds.file(new=True, force=True)
controller = cmds.createNode("network", name="morphQueryController")
cmds.addAttr(controller, longName="outputWeight", attributeType="double", multi=True)
slot = 4
raw_name = "Smile"
global_index = 9
for mesh_index in range(12):
    base = cmds.polyCube(name="queryBase{}".format(mesh_index))[0]
    target = cmds.duplicate(base, name="queryTarget{}".format(mesh_index))[0]
    cmds.move(0.1, "{}.vtx[0]".format(target), relative=True, objectSpace=True)
    blend_shape = cmds.blendShape(target, base, name="queryBlendShape{}".format(mesh_index))[0]
    cmds.aliasAttr("smile{}".format(mesh_index), "{}.weight[0]".format(blend_shape))
    if mesh_index != 11:  # one legacy alias-only provider
        cmds.addAttr(blend_shape, longName="mmd_blendshape_morph_names_json", dataType="string")
        cmds.setAttr(
            "{}.mmd_blendshape_morph_names_json".format(blend_shape),
            json.dumps({"0": {"name": raw_name, "index": global_index}}, ensure_ascii=False),
            type="string",
        )
    else:
        cmds.aliasAttr(raw_name, "{}.weight[0]".format(blend_shape))
    cmds.connectAttr("{}.outputWeight[{}]".format(controller, slot), "{}.weight[0]".format(blend_shape), force=True)

request = MorphBindingRequest(raw_name, global_index, controller, slot)
python_adapter = CountingAdapter(cmds)
python_resolution = resolve_maya_morph_binding(python_adapter, request)
python_calls = python_adapter.calls
native_adapter = CountingAdapter(cmds)
native_gateway = NativeMorphBindingQueryGateway(native_adapter)
raw_native = json.loads(cmds.mmdAuthoringQueryMorphBindings(payload=json.dumps({"version": 1, "controller": controller, "slot": slot})))
assert raw_native.get("destinations"), raw_native
native_resolution = resolve_maya_morph_binding(native_adapter, request, native_query=native_gateway)
native_calls = native_adapter.calls
assert native_resolution == python_resolution
assert len(native_resolution.bindings) == 12
assert len(native_resolution.warnings) == 1

# Stale authoritative metadata must reach the same Python semantic policy.
cmds.setAttr(
    "queryBlendShape0.mmd_blendshape_morph_names_json",
    json.dumps({"0": {"name": "stale", "index": global_index}}),
    type="string",
)
for use_native in (False, True):
    try:
        resolve_maya_morph_binding(
            native_adapter,
            request,
            native_query=native_gateway if use_native else None,
        )
    except Exception as exc:
        assert "stale_raw_name_mapping" in str(exc)
    else:
        raise AssertionError("stale raw-name mapping was accepted")
cmds.setAttr(
    "queryBlendShape0.mmd_blendshape_morph_names_json",
    json.dumps({"0": {"name": raw_name, "index": global_index}}, ensure_ascii=False),
    type="string",
)

for _ in range(3):
    resolve_maya_morph_binding(native_adapter, request)
    resolve_maya_morph_binding(native_adapter, request, native_query=native_gateway)
python_times = []
native_times = []
for _ in range(21):
    started = time.perf_counter()
    resolve_maya_morph_binding(native_adapter, request)
    python_times.append((time.perf_counter() - started) * 1000.0)
    started = time.perf_counter()
    resolve_maya_morph_binding(native_adapter, request, native_query=native_gateway)
    native_times.append((time.perf_counter() - started) * 1000.0)

metrics = {
    "mesh_count": 12,
    "python_maya_calls": python_calls,
    "native_python_maya_calls": native_calls,
    "python_p50_ms": statistics.median(python_times),
    "python_p95_ms": percentile(python_times, 0.95),
    "native_p50_ms": statistics.median(native_times),
    "native_p95_ms": percentile(native_times, 0.95),
}
metrics["native_improved_both_percentiles"] = (
    metrics["native_p50_ms"] < metrics["python_p50_ms"]
    and metrics["native_p95_ms"] < metrics["python_p95_ms"]
)
assert native_calls < python_calls, metrics
assert metrics["native_improved_both_percentiles"], metrics
print("MORPH BINDING QUERY METRICS " + json.dumps(metrics, sort_keys=True))
print("MORPH BINDING QUERY SMOKE PASS")
