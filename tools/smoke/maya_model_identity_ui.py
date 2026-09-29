"""Verify model-owned GUI lists across duplicate imports and scene reopen."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MARKER = "MODEL_IDENTITY_UI_COMPLETE"


def run_probe(config_path):
    """Read UTF-8 asset paths inside Maya and exercise the real Qt presenters."""
    from maya import cmds
    from mmd_tools.core.mmd_parser import parse_pmx_file
    from mmd_tools.io.mmd_importer import import_mmd_file
    from mmd_tools.services.scene_model_service import SceneModelService
    from mmd_tools.ui.main_window import MainWindow
    from mmd_tools.ui.qt_compat import QApplication, Qt
    from tools.render_override.common import require_requested_plugin

    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    out = Path(config["out"])
    report = {"status": "fail", "cases": [], "model": config["model"]}
    window = None
    try:
        plugin = Path(config["plugin"])
        require_requested_plugin(cmds, plugin, print)
        cmds.loadPlugin(str(ROOT / "plug-ins/mmd_tools_plugin.py"), quiet=True)
        report["pluginSha256"] = hashlib.sha256(plugin.read_bytes()).hexdigest()
        report["modelSha256"] = hashlib.sha256(Path(config["model"]).read_bytes()).hexdigest()
        pmx = parse_pmx_file(config["model"], use_native_pmx_parse=False)
        window = MainWindow()
        window.show_window(dockable=False)
        for split in (False, True):
            cmds.file(new=True, force=True)
            roots = []
            for namespace in ("identity_a", "identity_b"):
                cmds.namespace(add=namespace)
                cmds.namespace(set=namespace)
                try:
                    root = import_mmd_file(config["model"], options={
                        "import_morphs": True, "import_physics": False,
                        "separate_meshes_by_material": split,
                    })
                    roots.append(cmds.ls(root, long=True)[0])
                finally:
                    cmds.namespace(set=":")
            assert len(set(roots)) == 2, roots
            assert len(set(cmds.ls(roots, uuid=True))) == 2, roots
            scene = out / ("split.ma" if split else "unified.ma")
            for reopened in (False, True):
                if reopened:
                    cmds.file(rename=str(scene))
                    cmds.file(save=True, type="mayaAscii", force=True)
                    cmds.file(str(scene), open=True, force=True)
                window.app_state.refresh_model_list()
                for root in roots:
                    window.app_state.current_model_root = root
                    window.morph_presenter.load_morphs()
                    window.material_presenter.load_materials()
                    QApplication.processEvents()
                    morph_list = window.morph_tab.morph_list
                    material_list = window.material_presenter.view.material_list
                    info = SceneModelService().get_model_info(root)
                    assert info, "model overview is empty"
                    assert morph_list.count() == len(pmx.morphs), (root, morph_list.count(), len(pmx.morphs))
                    assert material_list.count() == len(pmx.materials), (root, material_list.count(), len(pmx.materials))
                    indices = []
                    for row in range(morph_list.count()):
                        key = morph_list.item(row).data(Qt.UserRole)
                        data = window.morph_presenter.morph_data[key]
                        indices.append(int(data["index"]))
                    assert sorted(indices) == list(range(len(pmx.morphs))), indices
                    materials = [material_list.item(row).data(Qt.UserRole) for row in range(material_list.count())]
                    assert all(cmds.objExists(node) for node in materials), materials
                    namespace = root.split("|")[-1].split(":")[0] + ":"
                    assert all(node.split("|")[-1].startswith(namespace) for node in materials), materials
                    meshes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
                    visible_meshes = [mesh for mesh in meshes if not cmds.getAttr(mesh + ".intermediateObject")]
                    expected_vertices = sum(cmds.polyEvaluate(mesh, vertex=True) for mesh in visible_meshes)
                    assert info["vertex_count"] == expected_vertices, (info, expected_vertices)
                    row = {"root": root, "split": split, "reopened": reopened,
                           "morphs": morph_list.count(), "materials": material_list.count(),
                           "vertices": expected_vertices, "intermediateShapes": len(meshes) - len(visible_meshes)}
                    report["cases"].append(row)
                    for tab_name, tab in (("morph", window.morph_tab), ("material", window.material_presenter.view)):
                        window.tab_widget.setCurrentWidget(tab)
                        QApplication.processEvents()
                        assert window.grab().save(str(out / f"ui-{tab_name}-{split}-{reopened}-{len(report['cases'])}.png"))
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
    """Launch an isolated GUI; only the ASCII config path crosses commandPort."""
    from tests.viewport.maya_e2e_harness import run_maya_e2e

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=7796)
    args = parser.parse_args()
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    plugin = ROOT / f"plug-ins/{args.maya}/Release/mmd_tools_cpp.mll"
    config = out / "input.json"
    config.write_text(json.dumps({"model": str(args.model.resolve()), "out": str(out),
                                 "plugin": str(plugin)}, ensure_ascii=False), encoding="utf-8")
    result = run_maya_e2e(
        project_root=ROOT, version=args.maya, out_dir=out, port=args.port, timeout=600,
        log_path=out / "probe.log", report_path=out / "report.json", marker=MARKER,
        command=("from maya import cmds\nfrom tools.smoke.maya_model_identity_ui import run_probe\n"
                 f"cmds.evalDeferred(lambda: run_probe({str(config)!r}), lowestPriority=True)"),
        send_label="model-identity-ui", stale_paths=(out / "probe.log", out / "report.json"),
        env_overrides={"MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11", "MMD_TOOLS_CPP_PLUGIN": str(plugin),
                       "PATH": str(plugin.parent) + os.pathsep + os.environ.get("PATH", "")},
    )
    print(json.dumps({"status": result["status"], "error": result.get("error")}))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
