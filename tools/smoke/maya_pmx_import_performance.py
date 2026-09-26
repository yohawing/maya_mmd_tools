"""Measure GUI PMX imports with optional saved-source A/B and reload checks.

The UTF-8 config supplies out, plugin, cases (model, label, split, repeats),
and optional source_dir containing saved versions of the profiled modules.
Bone/mesh converters and texture_path_cache may also be supplied for full-import A/B.
Verification runs outside the measured import interval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
import time
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MARKER = "PMX_IMPORT_PERFORMANCE_COMPLETE"


def _snapshot(root, shaders_for_shape):
    """Read deformed points and the native values of actually used materials."""
    from maya import cmds
    from maya.api import OpenMaya as om
    from mmd_tools.converters import material_morph_runtime as runtime
    from mmd_tools.converters.material_shader_parameters import hardware_morph_routes

    points, materials = {}, {}
    for shape in sorted(cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []):
        if cmds.getAttr(shape + ".intermediateObject"):
            continue
        selection = om.MSelectionList()
        selection.add(shape)
        points[shape] = [tuple(p)[:3] for p in om.MFnMesh(selection.getDagPath(0)).getPoints()]
    shaders = runtime._collect_shaders_by_material_index(root)
    for shape in runtime._collect_native_render_shapes(root):
        for index in shaders_for_shape(shape, shaders):
            plugs = [f"{shape}.materialAlpha[{index}]"]
            for route in hardware_morph_routes("dx11Shader"):
                if route.uniform != "DiffuseColorA":
                    plugs.extend(runtime._native_material_value_plugs(shape, index, route.uniform, route.size))
            materials.update((plug, cmds.getAttr(plug)) for plug in plugs)
    return {"points": points, "materials": materials}


def _verify(root, case, out, shaders_for_shape):
    from maya import cmds
    from mmd_tools.actions.export_model_action import ExportModelAction, ExportModelRequest
    from mmd_tools.core.pmx_data import PmxData

    controller = (cmds.listConnections(root + ".mmd_morph_controller", source=True, destination=False) or [])[0]
    indices = case.get("verify_morphs", [0])

    def samples():
        result = {"base": _snapshot(root, shaders_for_shape)}
        for index in indices:
            plug = f"{controller}.inputWeight[{index}]"
            cmds.setAttr(plug, 1.0)
            result[str(index)] = _snapshot(root, shaders_for_shape)
            cmds.setAttr(plug, 0.0)
        return result

    before = samples()
    scene = out / (case["label"] + ".mb")
    cmds.file(rename=str(scene))
    cmds.file(save=True, type="mayaBinary", force=True)
    cmds.file(new=True, force=True)
    cmds.file(str(scene), open=True, force=True)
    after = samples()
    # Binary scene storage should retain evaluated geometry/material values.
    def close(a, b):
        if isinstance(a, dict):
            return a.keys() == b.keys() and all(close(a[k], b[k]) for k in a)
        if isinstance(a, (tuple, list)):
            return len(a) == len(b) and all(close(x, y) for x, y in zip(a, b))
        if isinstance(a, (float, int)):
            return abs(a - b) <= 1e-5
        return a == b
    assert close(before, after), "Evaluated points/materials changed after reload"
    snapshot = out / (case["label"] + "-values.json")
    snapshot.write_text(json.dumps(before), encoding="utf-8")
    export_path = out / (case["label"] + ".pmx")
    exported = ExportModelAction().execute(ExportModelRequest(
        file_path=str(export_path), options={"export_format": "pmx", "target_model": root},
    ))
    assert exported.succeeded, str(exported.error)
    pmx = PmxData().parse_file(str(export_path))
    source = PmxData().parse_file(case["model"])
    assert len(pmx.morphs) == len(source.morphs), "Morph count changed on export"
    empty = [i for i, morph in enumerate(source.morphs) if int(morph.morph_type) == 1 and not morph.offsets]
    assert all(not pmx.morphs[i].offsets for i in empty), "Empty morph changed on export"
    return {"reload": "pass", "export": "pass", "morph_count": len(pmx.morphs),
            "empty_morphs": empty, "samples": str(snapshot),
            "export_warnings": [str(w) for w in exported.warnings]}


def probe(config_path):
    from maya import cmds
    from tools.render_override.common import require_requested_plugin
    from mmd_tools.services.settings_service import SettingsService
    from mmd_tools.io import pmx_importer
    from mmd_tools.io.mmd_importer import import_mmd_file
    from mmd_tools.converters.material_morph_runtime import _native_shape_shaders

    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    out = Path(config["out"])
    plugin = Path(config["plugin"])
    report = {"status": "running", "cases": [], "config": config,
              "maya": cmds.about(version=True), "pluginSha256": hashlib.sha256(plugin.read_bytes()).hexdigest()}
    report["sourceSha256"] = {
        name: hashlib.sha256((ROOT / "mmd_tools" / folder / name).read_bytes()).hexdigest()
        for folder, name in (("converters", "material_morph_runtime.py"),
                             ("converters", "morph_converter.py"), ("core", "morph_delta_mapping.py"),
                             ("converters", "bone_converter.py"), ("converters", "mesh_converter.py"),
                             ("core", "texture_path_cache.py"))
    }
    try:
        require_requested_plugin(cmds, plugin, print)
        cmds.loadPlugin(str(ROOT / "plug-ins/mmd_tools_plugin.py"), quiet=True)
        window = None
        if config.get("verify_progress"):
            from mmd_tools.ui.main_window import MainWindow
            window = MainWindow()
            window.show()
        converter, material_graph = pmx_importer.MorphConverter, pmx_importer.build_material_morph_graph
        bone_converter, mesh_converter = pmx_importer.BoneConverter, pmx_importer.MeshConverter
        if config.get("source_dir"):
            saved = Path(config["source_dir"])
            mapping = runpy.run_path(str(saved / "morph_delta_mapping.py"))
            morph = runpy.run_path(str(saved / "morph_converter.py"))
            converter = morph["MorphConverter"]
            converter._mapped_vertex_morph_deltas.__globals__["map_morph_deltas_to_local"] = mapping["map_morph_deltas_to_local"]
            material_graph = runpy.run_path(str(saved / "material_morph_runtime.py"))["build_material_morph_graph"]
            if (saved / "bone_converter.py").exists():
                bone_converter = runpy.run_path(
                    str(saved / "bone_converter.py"), run_name="mmd_tools.converters._benchmark_bone_converter",
                )["BoneConverter"]
            if (saved / "mesh_converter.py").exists():
                mesh = runpy.run_path(
                    str(saved / "mesh_converter.py"), run_name="mmd_tools.converters._benchmark_mesh_converter",
                )
                mesh_converter = mesh["MeshConverter"]
                if (saved / "texture_path_cache.py").exists():
                    cache = runpy.run_path(str(saved / "texture_path_cache.py"))
                    mesh_converter._setup_standard_shader.__globals__["resolve_texture_to_cache"] = cache["resolve_texture_to_cache"]
            report["sourceSha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in saved.glob("*.py")}
        with (
            patch.object(pmx_importer, "MorphConverter", converter),
            patch.object(pmx_importer, "build_material_morph_graph", material_graph),
            patch.object(pmx_importer, "BoneConverter", bone_converter),
            patch.object(pmx_importer, "MeshConverter", mesh_converter),
        ):
            for case in config["cases"]:
                for repeat in range(case.get("repeats", 1)):
                    cmds.file(new=True, force=True)
                    options = SettingsService().build_pmx_import_options()
                    options.update(separate_meshes_by_material=case["split"], profile={})
                    started = time.perf_counter()
                    progress = []
                    def update_progress(value):
                        window.app_state.emit_progress(value)
                        progress.append({"value": value, "seconds": time.perf_counter() - started,
                                         "displayed": window.progress_bar.value()})
                    kwargs = {"progress_callback": update_progress} if window is not None else {}
                    root = import_mmd_file(case["model"], options=options, **kwargs)
                    elapsed = time.perf_counter() - started
                    assert root and cmds.objExists(root)
                    profile = options["profile"]
                    graph = profile["material_morph_runtime"]
                    assert graph["success"], graph.get("skipped")
                    native = graph["native_alpha"]
                    native = native if isinstance(native, list) else [native]
                    row = {"label": case["label"], "split": case["split"], "repeat": repeat,
                           "import_seconds": elapsed, "phases": profile["phase_timings"],
                           "morph_profile": profile["morph_converter"], "morph_result": profile["morph_result"],
                           "native_material_bindings": sum(len(item["bindings"]) for item in native)}
                    if window is not None:
                        values = [event["value"] for event in progress]
                        assert values == sorted(set(values)), values
                        assert all(event["value"] == event["displayed"] for event in progress)
                        assert len([value for value in values if 35 < value < 50]) >= 10, values
                        assert values[-1] < 100, "Importer must not report UI completion early"
                        window.app_state.emit_progress(100)
                        assert window.progress_bar.isHidden()
                        row["progress"] = progress
                        row["progress_ui"] = "pass"
                    report["cases"].append(row)
                    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                    if case.get("verify") and repeat == case.get("repeats", 1) - 1:
                        row["verification"] = _verify(root, case, out, _native_shape_shaders)
                    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        report["status"] = "pass"
    except Exception:
        report.update(status="fail", error=traceback.format_exc())
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / "probe.log").write_text(MARKER + "\n", encoding="utf-8")
    cmds.quit(force=True)


def main():
    from tests.viewport.maya_e2e_harness import run_maya_e2e

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--port", type=int, default=7802)
    args = parser.parse_args()
    config_path = args.config.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    out, plugin = Path(config["out"]), Path(config["plugin"])
    out.mkdir(parents=True, exist_ok=True)
    command = (f"import runpy\nns=runpy.run_path({str(Path(__file__).resolve())!r})\n"
               f"import maya.cmds as cmds\ncmds.evalDeferred(lambda: ns['probe']({str(config_path)!r}),lowestPriority=True)")
    result = run_maya_e2e(
        project_root=ROOT, version=args.maya, out_dir=out, port=args.port, timeout=1800,
        log_path=out / "probe.log", report_path=out / "report.json", command=command,
        marker=MARKER, send_label="pmx-import-performance", stale_paths=(out / "probe.log", out / "report.json"),
        env_overrides={"MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11", "MMD_TOOLS_CPP_PLUGIN": str(plugin),
                       "PATH": str(plugin.parent) + os.pathsep + os.environ.get("PATH", "")},
    )
    print(json.dumps({"status": result["status"], "cases": len(result["cases"]), "error": result.get("error")}))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
