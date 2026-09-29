"""Extend the real-model binding probe with GUI, deformation and PMX roundtrip."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MARKER = "MORPH_ROUNDTRIP_COMPLETE"


def _material_deformation(cmds, pmx, controller):
    """Exercise a real material morph and preserve its authored base through Undo."""
    from mmd_tools.converters.export_scene_collector import _collect_mmd_material_dict

    index = next((i for i, morph in enumerate(pmx.morphs)
                  if int(morph.morph_type) == 8 and morph.offsets), None)
    if index is None:
        return None
    evaluators = cmds.ls(type="mmdMaterialMorphEval") or []
    assert evaluators, "material morph has no evaluator"
    shaders = [s for s in cmds.ls(type="standardSurface")
               if cmds.attributeQuery("diffuse_color", node=s, exists=True)]
    authored = {s: _collect_mmd_material_dict(s) for s in shaders}

    def evaluated():
        return {node: [cmds.getAttr(node + "." + attr) for attr in (
            "outputDiffuse", "outputDiffuseAlpha", "outputSpecular", "outputAmbient", "outputEdgeColor",
            "outputSpecularCoefficient", "outputEdgeSize")]
                for node in evaluators}

    plug = f"{controller}.outputWeight[{index}]"
    cmds.setAttr(plug, 0.0)
    zero = evaluated()
    cmds.setAttr(plug, 1.0)
    one = evaluated()
    assert one != zero, f"material morph {index} did not change evaluator output"
    assert {s: _collect_mmd_material_dict(s) for s in shaders} == authored
    cmds.undo()
    assert evaluated() == zero
    cmds.redo()
    assert evaluated() == one
    cmds.setAttr(plug, 0.0)
    assert evaluated() == zero
    return {"index": index, "evaluators": len(evaluators), "zero": zero, "one": one}


def _deformation(cmds, pmx, root, controller, index):
    """Check weight propagation and one source vertex through its weld mapping."""
    from mmd_tools.core.morph_delta_mapping import collect_morph_delta
    from tools.local.morph_binding_identity import _mapping_evidence

    plug = f"{controller}.outputWeight[{index}]"
    destinations = cmds.listConnections(plug, source=False, destination=True, plugs=True) or []
    assert destinations, f"morph {index} has no destination"
    deltas = collect_morph_delta(pmx.morphs[index], index, len(pmx.vertices))
    mapping = []
    if deltas:
        vertex = next(iter(deltas))
        mapping = [item for item in _mapping_evidence(cmds, root, vertex, deltas) if item["status"] == "mapped"]
        assert mapping and not any(item["weldConflict"] for item in mapping)
    cmds.setAttr(plug, 0.0)
    zero = [cmds.pointPosition(f'{item["mesh"]}.vtx[{item["localVertex"]}]', local=True) for item in mapping]
    cmds.setAttr(plug, 1.0)
    one = [cmds.pointPosition(f'{item["mesh"]}.vtx[{item["localVertex"]}]', local=True) for item in mapping]
    assert all(abs(cmds.getAttr(destination) - 1.0) < 1e-6 for destination in destinations)
    distances = [math.dist(a, b) for a, b in zip(zero, one)]
    assert not mapping or any(distance > 1e-5 for distance in distances), distances
    cmds.setAttr(plug, 0.0)
    return {"index": index, "destinations": len(destinations), "distances": distances}


def run_probe(config_path):
    """Validate both mesh layouts in the same isolated GUI host."""
    from maya import cmds
    from maya.api import OpenMaya as om
    from mmd_tools.actions.export_model_action import ExportModelAction, ExportModelRequest
    from mmd_tools.core.mmd_parser import parse_pmx_file
    from mmd_tools.ui.main_window import MainWindow
    from mmd_tools.ui.qt_compat import QApplication, Qt
    from tools.local.morph_binding_identity import run_probe as binding_probe

    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    out = Path(config["out"])
    report = {"status": "fail", "cases": []}
    window = None
    try:
        for split in (False, True):
            case_out = out / ("split" if split else "unified")
            case_out.mkdir(parents=True, exist_ok=True)
            case_config = {**config, "out": str(case_out), "split": split}
            case_path = case_out / "input.json"
            case_path.write_text(json.dumps(case_config, ensure_ascii=False), encoding="utf-8")
            binding_probe(str(case_path))
            base = json.loads((case_out / "report.json").read_text(encoding="utf-8"))
            assert base["status"] == "pass", base.get("error")
            root = base["binding"]["root"]
            controller = base["binding"]["controllerPlug"].split(".outputWeight", 1)[0]
            pmx = parse_pmx_file(config["model"], use_native_pmx_parse=False)
            window = MainWindow()
            window.show_window(dockable=False)
            row = {"split": split, "source": base["source"], "stages": []}
            for reopened in (False, True):
                if reopened:
                    scene_format = config.get("sceneFormat", "mayaAscii")
                    scene = case_out / ("roundtrip.mb" if scene_format == "mayaBinary" else "roundtrip.ma")
                    cmds.file(rename=str(scene))
                    cmds.file(save=True, type=scene_format, force=True)
                    errors = []
                    callback = om.MCommandMessage.addCommandOutputCallback(
                        lambda message, kind, *_: errors.append(message) if kind == om.MCommandMessage.kError else None
                    )
                    try:
                        cmds.file(str(scene), open=True, force=True)
                    finally:
                        om.MMessage.removeCallback(callback)
                    row["reloadErrors"] = errors
                    assert not errors, errors[:5]
                window.app_state.refresh_model_list()
                window.app_state.current_model_root = root
                window.morph_presenter.load_morphs()
                window.tab_widget.setCurrentWidget(window.morph_tab)
                QApplication.processEvents()
                widget = window.morph_tab.morph_list
                indices = [window.morph_presenter.morph_data[widget.item(i).data(Qt.UserRole)]["index"]
                           for i in range(widget.count())]
                assert sorted(indices) == list(range(len(pmx.morphs)))
                selected = config["morphIndex"]
                widget.setCurrentRow(indices.index(selected))
                QApplication.processEvents()
                assert window.grab().save(str(case_out / f"morph-{reopened}.png"))
                # An empty indexed morph still needs a nonempty companion for motion proof.
                indices_to_test = [selected]
                if not pmx.morphs[selected].offsets:
                    indices_to_test.append(next(i for i, morph in enumerate(pmx.morphs)
                                                if int(morph.morph_type) == 1 and morph.offsets))
                measured = [_deformation(cmds, pmx, root, controller, index) for index in indices_to_test]
                material = _material_deformation(cmds, pmx, controller)
                row["stages"].append({"reopened": reopened, "listCount": widget.count(),
                                      "deformation": measured, "material": material})
            assert row["stages"][0]["material"] == row["stages"][1]["material"]
            before, after = (stage["deformation"] for stage in row["stages"])
            assert len(before) == len(after)
            for left, right in zip(before, after):
                assert left["index"] == right["index"] and left["destinations"] == right["destinations"]
                assert len(left["distances"]) == len(right["distances"])
                assert all(math.isclose(a, b, rel_tol=1e-5, abs_tol=1e-5)
                           for a, b in zip(left["distances"], right["distances"]))
            output = case_out / "exported.pmx"
            result = ExportModelAction().execute(ExportModelRequest(
                file_path=str(output), options={"export_format": "pmx", "target_model": root}))
            assert result.succeeded, str(result.error)
            exported = parse_pmx_file(str(output), use_native_pmx_parse=False)
            assert len(exported.morphs) == len(pmx.morphs)
            index = config["morphIndex"]
            assert exported.morphs[index].name == pmx.morphs[index].name
            assert int(exported.morphs[index].morph_type) == int(pmx.morphs[index].morph_type)
            assert bool(exported.morphs[index].offsets) == bool(pmx.morphs[index].offsets)
            row["export"] = {"morphCount": len(exported.morphs), "offsetCount": len(exported.morphs[index].offsets)}
            report["cases"].append(row)
            window.close()
            window = None
        report["status"] = "pass"
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if window is not None:
            window.close()
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "probe.log").write_text(MARKER + "\n", encoding="utf-8")
        cmds.quit(force=True)


def main():
    """Read asset inputs from UTF-8 JSON and launch a disposable Maya profile."""
    from tests.viewport.maya_e2e_harness import run_maya_e2e

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="model, morphIndex and sourceVertex JSON")
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--scene-format", choices=("mayaAscii", "mayaBinary"), default="mayaAscii")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=7793)
    args = parser.parse_args()
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    plugin = ROOT / f"plug-ins/{args.maya}/Release/mmd_tools_cpp.mll"
    config = out / "input.json"
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    payload.update(out=str(out), plugin=str(plugin), sceneFormat=args.scene_format)
    config.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    result = run_maya_e2e(
        project_root=ROOT, version=args.maya, out_dir=out, port=args.port, timeout=900,
        log_path=out / "probe.log", report_path=out / "report.json", marker=MARKER,
        command=("from maya import cmds\nfrom tools.smoke.maya_morph_binding_roundtrip import run_probe\n"
                 f"cmds.evalDeferred(lambda: run_probe({str(config)!r}), lowestPriority=True)"),
        send_label="morph-roundtrip", stale_paths=(out / "probe.log", out / "report.json"),
        env_overrides={"MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11", "MMD_TOOLS_CPP_PLUGIN": str(plugin),
                       "PATH": str(plugin.parent) + os.pathsep + os.environ.get("PATH", "")},
    )
    print(json.dumps({"status": result["status"], "error": result.get("error")}))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
