"""Maya standalone parity smoke for native Material outline writes."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path

plugin = os.environ.get("MMD_TOOLS_CPP_PLUGIN")
if not plugin:
    version = os.environ.get("MAYA_VERSION", "2024")
    config = os.environ.get("MMD_TOOLS_CPP_CONFIG", "Debug")
    plugin = os.path.abspath(os.path.join("plug-ins", version, config, "mmd_tools_cpp.mll"))
os.environ["PATH"] = os.path.dirname(plugin) + os.pathsep + os.environ.get("PATH", "")
os.environ.setdefault("MAYA_VP2_DEVICE_OVERRIDE", "VirtualDeviceDx11")
_dll_directory = (
    os.add_dll_directory(os.path.dirname(plugin))
    if hasattr(os, "add_dll_directory")
    else None
)

import maya.standalone  # noqa: E402

maya.standalone.initialize(name="python")
from maya import cmds  # noqa: E402
from maya.api import OpenMaya as om  # noqa: E402

from mmd_tools.adapters.maya_authoring_factory import build_maya_authoring_composition  # noqa: E402
from mmd_tools.converters.mesh_converter import expected_shader_outline_preview  # noqa: E402
from mmd_tools.core import settings  # noqa: E402
from mmd_tools.io.mmd_importer import import_mmd_file  # noqa: E402
from tests.common.maya_plugin_setup import load_mmd_tools_plugin  # noqa: E402
from tools.smoke.authoring_command_support import (  # noqa: E402
    MayaCommandRecorder,
    measure_case,
)


def stable(value):
    if isinstance(value, dict):
        return {key: stable(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [stable(item) for item in value]
    if isinstance(value, float):
        return round(value, 6)
    return value


def equal(left, right):
    return stable(left.to_mapping()) == stable(right.to_mapping())


def snapshot(shader):
    result = {}
    for attr in (
        "technique",
        "EdgeSize",
        "mmd_shader_outline_enabled",
        "mmdDoubleSided",
        "mmdTransparencyMode",
    ):
        exists = bool(cmds.attributeQuery(attr, node=shader, exists=True))
        result[attr] = {
            "exists": exists,
            "value": stable(cmds.getAttr("{}.{}".format(shader, attr))) if exists else None,
        }
    return result


repo_root = Path(__file__).resolve().parents[2]
load_mmd_tools_plugin(repo_root, cmds_module=cmds)
cmds.loadPlugin("dx11Shader", quiet=True)
cmds.loadPlugin(plugin, quiet=True)
assert callable(getattr(cmds, "mmdAuthoringSetMaterialOutline", None))
cmds.file(new=True, force=True)
cmds.undoInfo(state=True)
previous_create = settings.get("import.model.create_mmd_shaders")
previous_backend = settings.get("import.model.mmd_shader_backend")
try:
    settings.set("import.model.create_mmd_shaders", True)
    settings.set("import.model.mmd_shader_backend", "dx11")
    root = import_mmd_file(
        str(repo_root / "tests" / "data" / "yw_test_model_control_rig_bone_morph.pmx"),
        options={
            "scale": 1.0,
            "import_physics": False,
            "setup_rig": False,
            "setup_bone_orientation": False,
            "create_mmd_control_rig": False,
            "import_morphs": True,
            "create_mmd_shaders": True,
            "use_cpp_fast_load": False,
            "use_native_pmx_parse": False,
            "require_native_pmx_parse": False,
        },
    )
finally:
    settings.set("import.model.create_mmd_shaders", previous_create)
    settings.set("import.model.mmd_shader_backend", previous_backend)
root = str(cmds.ls(str(root), long=True)[0])
composition = build_maya_authoring_composition(cmds)
coordinator = composition.coordinator
material = coordinator.read_spec(root).materials[0]
shader = str(material.binding_identity)
# mayapy's OpenGL VP2 policy deliberately falls back from requested DX11
# import. Build an equivalent registry-owned dx11Shader fixture explicitly;
# this tests the command without claiming an interactive DX11 render gate.
if cmds.nodeType(shader) != "dx11Shader":
    from mmd_tools.converters.mesh_converter import apply_shader_outline
    from mmd_tools.core import model_registry

    old_shader = shader
    shader = str(cmds.shadingNode("dx11Shader", asShader=True, name="OutlineSmokeMaterial"))
    shading_group = str(
        cmds.sets(
            renderable=True,
            noSurfaceShader=True,
            empty=True,
            name="OutlineSmokeMaterialSG",
        )
    )
    cmds.connectAttr("{}.outColor".format(shader), "{}.surfaceShader".format(shading_group), force=True)
    material = replace(material, binding_identity=shader)
    for texture_slot in ("MainTexture", "SphereTexture"):
        if not cmds.attributeQuery(texture_slot, node=shader, exists=True):
            cmds.addAttr(shader, longName=texture_slot, attributeType="message")
    composition.material_authoring._write_material_attrs(
        shader, material, bind_texture_graph=False
    )
    if not cmds.attributeQuery("EdgeSize", node=shader, exists=True):
        cmds.addAttr(shader, longName="EdgeSize", attributeType="float")
    cmds.setAttr("{}.EdgeSize".format(shader), material.edge_size)
    if not cmds.attributeQuery("mmdTransparencyMode", node=shader, exists=True):
        cmds.addAttr(shader, longName="mmdTransparencyMode", dataType="string")
    cmds.setAttr("{}.mmdTransparencyMode".format(shader), "opaque", type="string")
    apply_shader_outline(shader, False, material.edge_size, cmds_module=cmds)
    registry = model_registry.ensure_model_registry(root)
    model_registry.unregister_model_members(registry, "material", [old_shader])
    model_registry.register_model_members(registry, "material", [shader])
    composition = build_maya_authoring_composition(cmds)
    coordinator = composition.coordinator
    material = coordinator.read_spec(root).materials[0]
    shader = str(material.binding_identity)
assert cmds.nodeType(shader) == "dx11Shader"

# Python and native modes must produce the same semantic and preview target,
# with one exact Undo/Redo item each.
initial = coordinator.read_material_value(root, material.index, shader)
initial_outline = snapshot(shader)
target = replace(initial, name_english="Outline parity", edge_size=1.25)
os.environ["MMD_AUTHORING_MATERIAL_OUTLINE_MODE"] = "python"
python_result = coordinator.apply_material_value_patch(root, target, outline_enabled=True)
python_outline = snapshot(shader)
assert equal(python_result, target)
cmds.undo()
assert equal(coordinator.read_material_value(root, material.index, shader), initial)
assert snapshot(shader) == initial_outline
cmds.redo()
assert equal(coordinator.read_material_value(root, material.index, shader), target)
assert snapshot(shader) == python_outline
cmds.undo()

os.environ["MMD_AUTHORING_MATERIAL_OUTLINE_MODE"] = "native"
native_result = coordinator.apply_material_value_patch(root, target, outline_enabled=True)
assert equal(native_result, target)
assert snapshot(shader) == python_outline
cmds.undo()
assert equal(coordinator.read_material_value(root, material.index, shader), initial)
assert snapshot(shader) == initial_outline
cmds.redo()
assert equal(coordinator.read_material_value(root, material.index, shader), target)
assert snapshot(shader) == python_outline
cmds.undo()

authoring = composition.material_authoring
preimage = authoring._capture_material_outline(shader)
transparency = preimage["mmdTransparencyMode"]
outline_target = expected_shader_outline_preview(
    str(preimage["technique"]["value"] or ""),
    transparency["value"] if transparency["exists"] else None,
    target.draw_flags,
    True,
    target.edge_size,
    edge_size_exists=bool(preimage["EdgeSize"]["exists"]),
)
updates = authoring._material_value_updates(shader, initial, target)
payload = {
    "version": 1,
    "root": root,
    "shader": shader,
    "material_index": material.index,
    "updates": updates,
    "outline_preimage": preimage,
    "outline_target": outline_target,
}


def raw(value):
    return json.loads(
        cmds.mmdAuthoringSetMaterialOutline(
            payload=json.dumps(value, separators=(",", ":"))
        )
    )


unchanged = (coordinator.read_material_value(root, material.index, shader), snapshot(shader))
bad_preimage = json.loads(json.dumps(payload))
bad_preimage["outline_preimage"]["technique"]["value"] = "forged"
assert raw(bad_preimage)["error"]["code"] == "outline_preimage_mismatch"
assert equal(coordinator.read_material_value(root, material.index, shader), unchanged[0])
assert snapshot(shader) == unchanged[1]
bad_target = {**payload, "outline_target": {**outline_target, "unknown": 1}}
assert raw(bad_target)["error"]["code"] == "invalid_outline_target"
duplicate = {**payload, "updates": updates + updates[:1]}
assert raw(duplicate)["error"]["code"] == "duplicate_field"
assert raw({**payload, "outline_target": {**outline_target, "technique": "Injected"}})["error"]["code"] == "invalid_outline_target"
if "EdgeSize" in outline_target:
    assert raw({**payload, "outline_target": {**outline_target, "EdgeSize": 2.01}})["error"]["code"] == "invalid_outline_target"

registry = cmds.listConnections(
    "{}.mmd_model_registry".format(root), source=True, destination=False
)[0]
forged_root = str(cmds.ls(cmds.createNode("transform", name="forgedOutline_root"), long=True)[0])
cmds.addAttr(forged_root, longName="mmd_model_registry", attributeType="message")
cmds.connectAttr("{}.message".format(registry), "{}.mmd_model_registry".format(forged_root))
ownership_before = (coordinator.read_material_value(root, material.index, shader), snapshot(shader))
cross_root = raw({**payload, "root": forged_root})
assert cross_root["error"]["code"] == "material_not_owned", cross_root
assert equal(coordinator.read_material_value(root, material.index, shader), ownership_before[0])
assert snapshot(shader) == ownership_before[1]
schema_plug = "{}.mmd_model_registry_schema".format(registry)
cmds.setAttr(schema_plug, "invalid", type="string")
bad_schema = raw(payload)
assert bad_schema["error"]["code"] == "material_not_owned", bad_schema
cmds.setAttr(schema_plug, "1", type="string")
member_array = "{}.materialMembers".format(registry)
indices = cmds.getAttr(member_array, multiIndices=True) or []
duplicate_member = "{}[{}]".format(member_array, max(indices, default=-1) + 1)
cmds.connectAttr("{}.message".format(shader), duplicate_member)
duplicate_ownership = raw(payload)
assert duplicate_ownership["error"]["code"] == "material_not_owned", duplicate_ownership
cmds.disconnectAttr("{}.message".format(shader), duplicate_member)
cmds.removeMultiInstance(duplicate_member, b=True)

cmds.undoInfo(stateWithoutFlush=False)
disabled_before = coordinator.read_material_value(root, material.index, shader)
try:
    coordinator.apply_material_value_patch(root, target, outline_enabled=True)
except Exception as error:
    assert "undo must be enabled" in str(error)
else:
    raise AssertionError("native outline route accepted disabled Maya undo")
assert equal(coordinator.read_material_value(root, material.index, shader), disabled_before)
cmds.undoInfo(stateWithoutFlush=True)

technique_plug = "{}.technique".format(shader)
cmds.setAttr(technique_plug, lock=True)
assert raw(payload)["error"]["code"] == "plug_not_settable"
cmds.setAttr(technique_plug, lock=False)

# Force a failure after the first semantic write; native reverse rollback must
# restore both semantic and preview preimages.
name_plug = "{}.mmd_material_name_en".format(shader)
before = (cmds.getAttr(name_plug), snapshot(shader))
selection = om.MSelectionList()
selection.add(shader)
shader_object = selection.getDependNode(0)
callback_fired = [False]


def lock_technique_after_name(msg, plug, _other_plug, _client_data):
    if (
        not callback_fired[0]
        and msg & om.MNodeMessage.kAttributeSet
        and plug.partialName(useLongNames=True) == "mmd_material_name_en"
    ):
        callback_fired[0] = True
        cmds.setAttr(technique_plug, lock=True)


callback_id = om.MNodeMessage.addAttributeChangedCallback(
    shader_object, lock_technique_after_name
)
mid_write = raw(payload)
om.MMessage.removeCallback(callback_id)
assert mid_write["error"]["code"] == "write_or_verify_failed", mid_write
assert (cmds.getAttr(name_plug), snapshot(shader)) == before
cmds.setAttr(technique_plug, lock=False)
cmds.flushUndo()

# Undo-time failure also restores the exact post-command target; the payload
# preimage is only a precondition, never the rollback authority.
undo_target = raw(payload)
assert undo_target["ok"] is True, undo_target
target_state = (cmds.getAttr(name_plug), snapshot(shader))
callback_fired[0] = False
callback_id = om.MNodeMessage.addAttributeChangedCallback(
    shader_object, lock_technique_after_name
)
try:
    cmds.undo()
except RuntimeError:
    pass
om.MMessage.removeCallback(callback_id)
assert callback_fired[0] is True
assert (cmds.getAttr(name_plug), snapshot(shader)) == target_state
cmds.setAttr(technique_plug, lock=False)
cmds.flushUndo()


def measure(mode):
    os.environ["MMD_AUTHORING_MATERIAL_OUTLINE_MODE"] = mode
    recorder = MayaCommandRecorder(cmds)
    active = {"coordinator": coordinator}
    state = {"current": coordinator.read_material_value(root, material.index, shader)}
    holders = {"before": {}, "target": {}, "outline_before": {}, "outline_target": {}}

    def prepare_cold():
        active["coordinator"] = build_maya_authoring_composition(cmds).coordinator

    def action(index):
        current = state["current"]
        next_value = replace(
            current,
            name_english="Outline {}".format(index % 2),
            edge_size=1.25 if index % 2 else 0.75,
        )
        holders["before"][index] = current
        holders["outline_before"][index] = snapshot(shader)
        holders["target"][index] = next_value
        state["current"] = active["coordinator"].apply_material_value_patch(
            root, next_value, outline_enabled=bool(index % 2)
        )

    def verify(index):
        assert equal(
            active["coordinator"].read_material_value(root, material.index, shader),
            holders["target"][index],
        )
        holders["outline_target"][index] = snapshot(shader)

    def undo_redo(index):
        cmds.undo()
        assert equal(
            active["coordinator"].read_material_value(root, material.index, shader),
            holders["before"][index],
        )
        assert snapshot(shader) == holders["outline_before"][index]
        cmds.redo()
        assert equal(
            active["coordinator"].read_material_value(root, material.index, shader),
            holders["target"][index],
        )
        assert snapshot(shader) == holders["outline_target"][index]
        state["current"] = holders["target"][index]

    return measure_case(
        name="material_outline_n7_{}".format(mode),
        recorder=recorder,
        action=action,
        verify_target=verify,
        verify_undo_redo=undo_redo,
        iterations=7,
        semantic_field_count=2,
        prepare_cold=prepare_cold,
    )


python_metrics = measure("python")
native_metrics = measure("native")
assert python_metrics["status"] == native_metrics["status"] == "pass"
improved = all(
    native_metrics[temperature][percentile] < python_metrics[temperature][percentile]
    for temperature in ("cold_timing_ns", "warm_timing_ns")
    for percentile in ("p50_ns", "p95_ns")
)
report = {
    "schema_version": 1,
    "maya": str(cmds.about(version=True)),
    "python": python_metrics,
    "native": native_metrics,
    "native_improved_cold_warm_p50_p95": improved,
    "default_decision": "native" if improved else "python",
}
out_dir = repo_root / "build" / "reports" / "material_outline_command"
out_dir.mkdir(parents=True, exist_ok=True)
(out_dir / "maya{}.json".format(os.environ.get("MAYA_VERSION", "unknown"))).write_text(
    json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
print("MATERIAL OUTLINE COMMAND METRICS " + json.dumps(report, sort_keys=True))
print("MATERIAL OUTLINE COMMAND SMOKE PASS")
