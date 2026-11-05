"""Check PMX morph state across ASCII/Binary saves and fresh Maya processes.

The UTF-8 --config JSON requires model, plugin and out; optional split and
options select the import configuration. Each phase uses an isolated GUI host.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MARKER = "SCENE_RELOAD_COMPLETE"


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _points(root):
    from maya import cmds
    from maya.api import OpenMaya as om

    result = {}
    for shape in sorted(cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []):
        if cmds.getAttr(shape + ".intermediateObject"):
            continue
        selection = om.MSelectionList()
        selection.add(shape)
        result[shape] = [list(point)[:3] for point in om.MFnMesh(selection.getDagPath(0)).getPoints()]
    if not result:
        raise AssertionError("Imported model has no non-intermediate mesh")
    return result


def _material_samples(root):
    """Exercise four authored RGB families and record their rendered values."""
    from maya import cmds
    from maya.api import OpenMaya as om
    from mmd_tools.converters import material_morph_runtime as runtime
    from mmd_tools.converters.material_shader_parameters import hardware_morph_routes

    shaders = runtime._collect_shaders_by_material_index(root)
    shaders = {index: shader for index, shader in shaders.items() if cmds.nodeType(shader) == "standardSurface"}
    render_shapes = runtime._collect_native_render_shapes(root)
    if not render_shapes:
        raise AssertionError("Imported model has no native render shapes")
    attributes = [runtime._AUTHORED_MATERIAL_ATTRIBUTES[uniform]
                  for uniform in ("DiffuseColorRGB", "SpecularColor", "AmbientColor", "EdgeColorRGB")]

    def values():
        authored, rendered = {}, {}
        for shader in sorted(set(shaders.values())):
            for attribute in attributes:
                plug = shader + "." + attribute
                children = cmds.attributeQuery(attribute, node=shader, listChildren=True) or []
                family = [plug] + [shader + "." + child for child in children]
                flags = []
                for member in family:
                    selection = om.MSelectionList()
                    selection.add(member)
                    flags.append(om.MFnAttribute(selection.getPlug(0).attribute()).isProxyAttribute)
                authored[plug] = {"value": cmds.getAttr(plug), "proxy": flags,
                                  "incoming": [cmds.connectionInfo(member, sourceFromDestination=True) for member in family]}
            authored[shader + ".baseColor"] = cmds.getAttr(shader + ".baseColor")
        for shape in render_shapes:
            for index in runtime._native_shape_shaders(shape, shaders):
                for route in hardware_morph_routes("dx11Shader"):
                    if route.size == 3:
                        for plug in runtime._native_material_value_plugs(shape, index, route.uniform, route.size):
                            rendered[plug] = cmds.getAttr(plug)
        if not rendered:
            raise AssertionError("Imported model has no native RGB material routes")
        return {"authored": authored, "rendered": rendered}

    samples = {"saved": values()}
    if not shaders:
        raise AssertionError("Imported model has no MMD standardSurface materials")
    shader = shaders[min(shaders)]
    for attribute in attributes:
        plug = shader + "." + attribute
        original = cmds.getAttr(plug)[0]
        parent_value = [0.23, 0.47, 0.61]
        if not _compare(original, parent_value, 1e-6):
            parent_value = [0.31, 0.53, 0.67]
        before = values()
        try:
            cmds.setAttr(plug, *parent_value, type="double3")
            if _compare(parent_value, cmds.getAttr(plug)[0], 1e-6):
                raise AssertionError("Parent RGB edit did not reach " + plug)
            samples[attribute + ".parent"] = values()
            if not _compare(before["rendered"], samples[attribute + ".parent"]["rendered"], 1e-6):
                raise AssertionError("Parent RGB edit did not change native render values: " + plug)
            child = cmds.attributeQuery(attribute, node=shader, listChildren=True)[0]
            cmds.setAttr(shader + "." + child, 0.79)
            if abs(cmds.getAttr(plug)[0][0] - 0.79) > 1e-6:
                raise AssertionError("Child RGB edit did not reach " + plug)
            samples[attribute + ".child"] = values()
            if not _compare(samples[attribute + ".parent"]["rendered"],
                            samples[attribute + ".child"]["rendered"], 1e-6):
                raise AssertionError("Child RGB edit did not change native render values: " + plug)
        finally:
            cmds.setAttr(plug, *original, type="double3")
    return samples


def _snapshot(root, edit_plug):
    """Capture serialized channels and the same reversible morph edit."""
    from maya import cmds

    blends = {}
    for node in sorted(cmds.ls(type="blendShape") or []):
        indices = cmds.getAttr(node + ".weight", multiIndices=True) or []
        aliases = cmds.aliasAttr(node, query=True) or []
        for index in indices:
            targets = {f"weight[{index}]", f"w[{index}]"}
            if sum(target in targets for target in aliases[1::2]) != 1:
                raise AssertionError(f"Expected one alias for {node}.weight[{index}]")
        blends[node] = {
            "indices": indices,
            "values": {str(i): cmds.getAttr(f"{node}.weight[{i}]") for i in indices},
            "aliases": dict(zip(aliases[::2], aliases[1::2])),
            "incoming": {str(i): cmds.connectionInfo(f"{node}.weight[{i}]", sourceFromDestination=True)
                         for i in indices},
        }
    controllers = {}
    for node in sorted(cmds.ls(type="mmdMorphController") or []):
        indices = cmds.getAttr(node + ".inputWeight", multiIndices=True) or []
        controllers[node] = {str(i): cmds.getAttr(f"{node}.inputWeight[{i}]") for i in indices}
    saved_value = cmds.getAttr(edit_plug)
    samples = {"saved": _points(root)}
    try:
        for value in (0.0, 1.0):
            cmds.setAttr(edit_plug, value)
            samples[str(value)] = _points(root)
    finally:
        cmds.setAttr(edit_plug, saved_value)
    return {"blendShapes": blends, "controllers": controllers, "samples": samples,
            "materials": _material_samples(root)}


def _compare(expected, actual, tolerance, path="snapshot"):
    """Return a bounded list of structural/value differences, including NaNs."""
    failures = []

    def visit(a, b, location):
        if len(failures) >= 20:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            if a.keys() != b.keys():
                failures.append(location + ": keys differ")
            for key in a.keys() & b.keys():
                visit(a[key], b[key], location + "." + str(key))
        elif isinstance(a, (tuple, list)) and isinstance(b, (tuple, list)):
            if len(a) != len(b):
                failures.append(location + ": lengths differ")
            for index, (left, right) in enumerate(zip(a, b)):
                visit(left, right, location + f"[{index}]")
        elif isinstance(a, (float, int)) and isinstance(b, (float, int)):
            if not math.isfinite(a) or not math.isfinite(b) or abs(a - b) > tolerance:
                failures.append(f"{location}: {a!r} != {b!r}")
        elif a != b:
            failures.append(f"{location}: {a!r} != {b!r}")

    visit(expected, actual, path)
    return failures


def probe(config_path, phase):
    """Run one phase inside Maya; the host controls process lifetime."""
    from maya import cmds
    from maya.api import OpenMaya as om
    from tools.render_override.common import require_requested_plugin

    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    output = Path(config["out"])
    phase_output = output / phase
    report = {"status": "fail", "phase": phase, "maya": cmds.about(version=True),
              "messages": [], "checks": {}, "errors": []}
    stage = "setup"

    def message(text, kind, *_):
        if kind in (om.MCommandMessage.kError, om.MCommandMessage.kWarning):
            report["messages"].append({"stage": stage, "kind": "error" if kind == om.MCommandMessage.kError else "warning",
                                       "text": text})

    callback = om.MCommandMessage.addCommandOutputCallback(message)
    try:
        require_requested_plugin(cmds, config["plugin"], print)
        cmds.loadPlugin(str(ROOT / "mmd_tools/plugin_main.py"), quiet=True)
        tolerance = float(config.get("tolerance", 1e-5))
        if phase == "save":
            from mmd_tools.core.mmd_parser import parse_pmx_file
            from mmd_tools.io.mmd_importer import import_mmd_file
            from mmd_tools.services.settings_service import SettingsService

            cmds.file(new=True, force=True)
            stage = "import"
            options = SettingsService().build_pmx_import_options()
            options.update(import_physics=False, separate_meshes_by_material=bool(config.get("split", False)))
            options.update(config.get("options", {}))
            root = import_mmd_file(config["model"], options=options)
            root = cmds.ls(root, long=True)[0]
            controller = (cmds.listConnections(root + ".mmd_morph_controller", source=True, destination=False) or [])[0]
            pmx = parse_pmx_file(config["model"])
            index = next(i for i, morph in enumerate(pmx.morphs) if int(morph.morph_type) == 1 and morph.offsets)
            edit_plug = f"{controller}.inputWeight[{index}]"
            cmds.setAttr(edit_plug, 0.35)
            stage = "before_save"
            before = _snapshot(root, edit_plug)
            if not _compare(before["samples"]["0.0"], before["samples"]["1.0"], tolerance):
                raise AssertionError("Representative vertex morph did not change evaluated mesh points")
            _write(output / "before.json", before)
            _write(output / "identity.json", {"root": root, "edit_plug": edit_plug, "morph_index": index})
            for suffix, kind in (("ma", "mayaAscii"), ("mb", "mayaBinary")):
                stage = "save_" + suffix
                cmds.file(rename=str(output / ("scene." + suffix)))
                cmds.file(save=True, type=kind, force=True)
                stage = "after_save_" + suffix
                after = _snapshot(root, edit_plug)
                _write(phase_output / ("after_" + suffix + ".json"), after)
                report["checks"][suffix] = _compare(before, after, tolerance)
        else:
            identity = json.loads((output / "identity.json").read_text(encoding="utf-8"))
            before = json.loads((output / "before.json").read_text(encoding="utf-8"))
            stage = "open_" + phase
            try:
                cmds.file(str(output / ("scene." + phase)), open=True, force=True)
            except RuntimeError:
                # Maya can finish opening a damaged ASCII scene and then raise.
                # Preserve its resulting state as evidence, while keeping failure.
                report["errors"].append(traceback.format_exc())
            stage = "after_open_" + phase
            after = _snapshot(identity["root"], identity["edit_plug"])
            _write(phase_output / "after.json", after)
            report["checks"][phase] = _compare(before, after, tolerance)
        # Warnings remain visible failures rather than silently granting full
        # acceptance to an import which already dropped a morph or connection.
        report["status"] = "pass" if not report["messages"] and not report["errors"] and not any(report["checks"].values()) else "fail"
    except Exception:
        report["errors"].append(traceback.format_exc())
    finally:
        om.MMessage.removeCallback(callback)
        _write(phase_output / "report.json", report)
        (phase_output / "run.log").write_text(MARKER + "\n", encoding="utf-8")


def main():
    from tests.viewport.maya_e2e_harness import run_maya_e2e

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--port", type=int, default=7813)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    for key in ("model", "plugin", "out"):
        config[key] = str(Path(config[key]).resolve())
    output, plugin = Path(config["out"]), Path(config["plugin"])
    output.mkdir(parents=True, exist_ok=True)
    resolved_config = output / "config.json"
    _write(resolved_config, config)
    # A failed save must never feed a previous run's scene to the reload phases.
    for name in ("scene.ma", "scene.mb", "identity.json", "before.json"):
        (output / name).unlink(missing_ok=True)
    report = {"status": "fail", "config": config, "phases": {},
              "plugin_sha256": hashlib.sha256(plugin.read_bytes()).hexdigest(),
              "model_sha256": hashlib.sha256(Path(config["model"]).read_bytes()).hexdigest()}
    for phase in ("save", "ma", "mb"):
        phase_output = output / phase
        phase_output.mkdir(parents=True, exist_ok=True)
        if phase != "save" and not (output / ("scene." + phase)).exists():
            report["phases"][phase] = {"status": "not_run", "reason": "save produced no scene"}
            continue
        command = ("from maya import cmds\nfrom tools.smoke.maya_scene_reload import probe\n"
                   f"cmds.evalDeferred(lambda: probe({str(resolved_config)!r}, {phase!r}), lowestPriority=True)")
        try:
            report["phases"][phase] = run_maya_e2e(
                project_root=ROOT, version=args.maya, out_dir=phase_output, port=args.port,
                timeout=float(config.get("timeout", 600)), log_path=phase_output / "run.log",
                report_path=phase_output / "report.json", command=command, marker=MARKER,
                send_label="scene-reload-" + phase,
                stale_paths=(phase_output / "run.log", phase_output / "report.json"),
                env_overrides={"MAYA_SKIP_USERSETUP_PY": "1", "MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11",
                               "MMD_TOOLS_CPP_PLUGIN": str(plugin),
                               "PATH": str(plugin.parent) + os.pathsep + os.environ.get("PATH", "")},
            )
        except Exception:
            report["phases"][phase] = {"status": "fail", "host_error": traceback.format_exc()}
        _write(output / "report.json", report)
    report["status"] = "pass" if all(p["status"] == "pass" for p in report["phases"].values()) else "fail"
    _write(output / "report.json", report)
    print(json.dumps({"status": report["status"], "phases": {k: v["status"] for k, v in report["phases"].items()}}))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
