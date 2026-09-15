"""Exercise editing and MMD Render together in an isolated Maya GUI."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.viewport.maya_e2e_harness import run_maya_e2e  # noqa: E402
from tools.render_override.common import capture_view, require_requested_plugin  # noqa: E402
from tools.render_override.render_override_visual_gate import read_png_rgb, write_png_rgb  # noqa: E402

MARKER = "MMD RENDER SEPARATION FINISHED"


def run_probe(output, plugin, split=False, migrate_legacy=False, textured=False):
    # Each user operation returns to Maya's event loop before the next capture.
    # A monolithic commandPort call suppresses deferred DG/VP2 notifications.
    try:
        from PySide6.QtCore import QTimer
    except ImportError:
        from PySide2.QtCore import QTimer
    steps = _probe_steps(output, plugin, split, migrate_legacy, textured)
    initializing = True

    def advance():
        nonlocal initializing
        try:
            next(steps)
        except StopIteration:
            return
        if initializing:
            initializing = False
            # SceneOpened renderer setup can remain queued while Maya is in
            # the background. Drain it before any tested edit/Undo operation.
            cmds.evalDeferred(lambda: QTimer.singleShot(100, advance), lowestPriority=True)
        else:
            QTimer.singleShot(100, advance)

    # commandPort can open before Maya finishes deferred startup plug-ins.
    # Start after that queue, so startup commands cannot consume test Undo.
    from maya import cmds
    cmds.evalDeferred(advance, lowestPriority=True)


def _probe_steps(output, plugin, split=False, migrate_legacy=False, textured=False):
    from maya import cmds
    from mmd_tools.converters import MorphConverter
    from mmd_tools.converters.export_scene_collector import _collect_mmd_material_dict
    from mmd_tools.converters.material_morph_runtime import build_material_morph_graph, _collect_shaders_by_material_index
    from mmd_tools.core.pmx_data.morph import PmxMorph, PmxMorphType
    from mmd_tools.io.mmd_importer import import_mmd_file

    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    report = {"status": "fail", "splitMaterials": split}
    try:
        cmds.file(new=True, force=True)
        loaded = require_requested_plugin(cmds, Path(plugin), print)
        report["plugin"] = str(loaded)
        report["pluginSha256"] = hashlib.sha256(loaded.read_bytes()).hexdigest()
        report["mayaVersion"] = cmds.about(version=True)
        report["evaluation"] = cmds.evaluationManager(query=True, mode=True)
        report["pythonExecutable"] = sys.executable
        report["importer"] = sys.modules[import_mmd_file.__module__].__file__
        cmds.loadPlugin(str(ROOT / "plug-ins/mmd_tools_plugin.py"), quiet=True)
        panels = cmds.getPanel(type="modelPanel")
        original_panels = {p: cmds.modelEditor(p, q=True, rendererOverrideName=True) for p in panels}
        model = ROOT / "tests/data/test_morph_model.pmx"
        if textured:
            # The legacy parser's writable data objects are used only to stage
            # this fixture; the actual import still uses the native parser.
            from mmd_tools.core.pmx_data import PmxData

            fixture = PmxData().parse_file(str(model))
            texture = out / "checker.png"
            write_png_rgb(texture, 8, 8, [(230, 210, 160) if (x // 2 + y // 2) % 2 else (20, 80, 230)
                                         for y in range(8) for x in range(8)])
            fixture.textures = [str(texture)]
            for material in fixture.materials:
                material.texture_index = 0
            model = out / "textured.pmx"
            fixture.write_file(str(model))
        report["model"] = str(model)
        report["modelSha256"] = hashlib.sha256(model.read_bytes()).hexdigest()
        from mmd_tools.core.settings import settings
        from mmd_tools.services.settings_service import SettingsService

        # This runner owns an isolated Maya profile. Exercise the normal UI
        # defaults instead of bypassing routing with explicit native flags.
        settings.reset()
        settings.set("import.model.separate_meshes_by_material", split)
        settings.set("import.physics.import_physics", False)
        options = SettingsService().build_pmx_import_options()
        assert not SettingsService().is_development_mode()
        assert options["use_cpp_fast_load"] and options["use_cpp_vp2_ownership"]
        report["importOptions"] = options
        root = import_mmd_file(str(model), options=options)
        assert root, "import failed"
        assert original_panels == {p: cmds.modelEditor(p, q=True, rendererOverrideName=True) for p in panels}
        proxies = cmds.listRelatives(root, ad=True, type="mmdRenderShape", fullPath=True)
        assert len(proxies) == (2 if split else 1), proxies
        sources = [cmds.listConnections(p + ".inputMesh", s=True, d=False, shapes=True)[0] for p in proxies]
        for source in sources:
            assert cmds.getAttr(source + ".visibility")
            assert not cmds.listConnections(source + ".visibility", s=True, d=False)
        shaders = _collect_shaders_by_material_index(root)
        assert len(shaders) == 2
        assert all(cmds.nodeType(s) == "standardSurface" for s in shaders.values())
        assert not cmds.ls(type="dx11Shader") and not cmds.ls(type="GLSLShader")

        window = cmds.window(widthHeight=(1300, 520))
        pane = cmds.paneLayout(configuration="vertical2")
        edit = cmds.modelPanel(label="Standard VP2 Edit", parent=pane)
        render = cmds.modelPanel(label="MMD Render", parent=pane)
        assert cmds.paneLayout(pane, q=True, childArray=True) == [edit, render]
        cmds.showWindow(window)
        for panel in (edit, render):
            cmds.modelEditor(panel, e=True, rendererName="vp2Renderer", displayAppearance="smoothShaded",
                             displayTextures=True, camera="persp", grid=False, twoSidedLighting=True)
        cmds.modelEditor(render, e=True, rendererOverrideName="mmdOrdered")
        cmds.setAttr("persp.translate", 17, 17, 17, type="double3")
        cmds.setAttr("persp.rotate", -27, 45, 0, type="double3")
        # Illuminate both sides of the fixture's opposing PMX normals.
        cmds.directionalLight(name="previewKey", rotation=(0, 0, 0), intensity=1.0)
        cmds.directionalLight(name="previewFill", rotation=(0, 180, 0), intensity=1.0)
        for panel in (edit, render):
            cmds.modelEditor(panel, e=True, displayLights="all")
        cmds.select(clear=True)

        def capture(label):
            from maya import utils
            cmds.undoInfo(stateWithoutFlush=False)
            try:
                # Stock VP2 compiles new shader graphs asynchronously. Drain
                # the initial idle/refresh work before testing pixel equality.
                for _ in range(3):
                    cmds.refresh(force=True)
                    utils.processIdleEvents()
                    time.sleep(0.1)
                return {name: read_png_rgb(capture_view(cmds, out / f"{label}_{name}.png", panel, 640, 480))[2]
                        for name, panel in (("edit", edit), ("render", render))}
            finally:
                cmds.undoInfo(stateWithoutFlush=True)

        def changed(before, after):
            counts = {name: sum(max(abs(a - b) for a, b in zip(left, right)) > 8
                                for left, right in zip(before[name], after[name])) for name in before}
            assert all(count > 100 for count in counts.values()), counts
            return counts

        yield
        initial = capture("initial")
        # Green is Maya's unsupported stock shader-network marker, not this
        # fixture's authored color. Catch it independently of DG numeric tests.
        assert sum(g > 200 and r < 10 and b < 10 for r, g, b in initial["edit"]) == 0
        if textured:
            from tools.render_override.display_checks import check_edit_render_display

            report["displayModes"] = yield from check_edit_render_display(
                cmds, root, (edit, render), capture, initial, changed,
            )
        from tools.render_override.material_cache_checks import check_material_cache

        report["materialCache"] = yield from check_material_cache(cmds, proxies, capture, initial)
        controller = cmds.listConnections(root + ".mmd_morph_controller", s=True, d=False)[0]
        weight = controller + ".inputWeight[0]"
        cmds.setAttr(weight, 1.0)
        deformed = capture("deformed")
        report["deformationPixels"] = changed(initial, deformed)
        cmds.undo()
        assert capture("deformation_undo") == initial
        cmds.redo()
        assert capture("deformation_redo") == deformed
        cmds.setAttr(weight, 0.0)

        cmds.setAttr(root + ".visibility", False)
        report["hidePixels"] = changed(initial, capture("hidden"))
        cmds.undo()
        assert capture("hide_undo") == initial
        cmds.setAttr(sources[0] + ".visibility", False)
        report["sourceShapeHidePixels"] = changed(initial, capture("source_shape_hidden"))
        cmds.undo()
        assert capture("source_shape_hide_undo") == initial
        shader = shaders[0]
        authored_before = _collect_mmd_material_dict(shader)
        # Legacy stage materials may omit the optional edge_flag mirror.
        from dataclasses import replace
        from mmd_tools.adapters.maya_material_authoring import MayaMaterialAuthoring
        from mmd_tools.core.model_authoring_spec import MmdMaterialSpec

        had_edge_flag = cmds.attributeQuery("edge_flag", node=shader, exists=True)
        if had_edge_flag:
            cmds.deleteAttr(shader + ".edge_flag")
        flags = cmds.getAttr(shader + ".mmd_draw_flags")
        old_material = MmdMaterialSpec("shadow_flag_probe", draw_flags=flags)
        new_material = replace(old_material, draw_flags=flags ^ 0x04)
        updates = MayaMaterialAuthoring._material_value_updates(
            None, shader, old_material, new_material,
        )
        report["shadowFlagWithoutEdge"] = json.loads(cmds.mmdAuthoringSetMaterialValues(payload=json.dumps({
            "version": 1, "root": cmds.ls(root, long=True)[0], "shader": shader,
            "material_index": 0, "updates": updates,
        })))
        assert report["shadowFlagWithoutEdge"]["ok"], report["shadowFlagWithoutEdge"]
        assert cmds.getAttr(shader + ".mmd_draw_flags") == flags ^ 0x04
        assert not cmds.attributeQuery("edge_flag", node=shader, exists=True)
        cmds.undo()
        assert cmds.getAttr(shader + ".mmd_draw_flags") == flags
        cmds.redo()
        assert cmds.getAttr(shader + ".mmd_draw_flags") == flags ^ 0x04
        cmds.undo()
        if had_edge_flag:
            cmds.undo()  # Restore only the test's attribute deletion.
        assert _collect_mmd_material_dict(shader) == authored_before
        report["materialAuthoring"] = json.loads(cmds.mmdAuthoringSetMaterialValues(payload=json.dumps({
            "version": 1, "root": cmds.ls(root, long=True)[0], "shader": shader,
            "material_index": 0, "updates": [
                {"field": "diffuse_color", "value": [0.05, 0.8, 0.1]},
                {"field": "diffuse_alpha", "value": 0.35},
            ],
        })))
        assert report["materialAuthoring"]["ok"], report["materialAuthoring"]
        yield
        authored_preview = capture("authored_material")
        report["materialAuthoringPixels"] = changed(initial, authored_preview)
        cmds.undo()
        yield
        assert capture("authored_material_undo") == initial
        assert _collect_mmd_material_dict(shader) == authored_before
        cmds.redo()
        yield
        assert capture("authored_material_redo") == authored_preview
        cmds.undo()

        morph = PmxMorph(1, 1, 1, 1, 1)
        morph.name = "PreviewMaterialMorph"
        morph.morph_type = PmxMorphType.MaterialMorph
        morph.offsets = [{"material_index": 0, "operation_type": 1,
                          "diffuse": (-0.2, 0.3, 0.0, -0.4),
                          "specular": (0, 0, 0), "specular_coefficient": 0,
                          "ambient": (0, 0, 0), "edge_color": (0, 0, 0, 0), "edge_size": 0,
                          "texture_factor": (0, 0, 0, 0), "sphere_texture_factor": (0, 0, 0, 0),
                          "toon_texture_factor": (0, 0, 0, 0)}]
        material_nodes = MorphConverter().convert_pmx_morphs(
            SimpleNamespace(morphs=[morph], materials=[], faces=[]), sources[0],
        )["material_morph_nodes"]
        from mmd_tools.core.pmx_data import PmxData
        next_morph_index = len(PmxData().parse_file(str(model)).morphs)
        for node in material_nodes:
            cmds.setAttr(node + ".mmd_morph_index", next_morph_index)
            if not cmds.attributeQuery("mmd_model_root", node=node, exists=True):
                cmds.addAttr(node, longName="mmd_model_root", attributeType="message")
            cmds.connectAttr(root + ".message", node + ".mmd_model_root", force=True)
        from mmd_tools.core.model_registry import get_model_registry, register_model_members, REGISTRY_CATEGORY_MORPH
        register_model_members(get_model_registry(root), REGISTRY_CATEGORY_MORPH, material_nodes)
        report["materialGraph"] = build_material_morph_graph(root)
        assert report["materialGraph"]["success"]
        assert report["materialGraph"]["evaluator_nodes"], report["materialGraph"]
        yield
        assert capture("material_graph_zero") == initial
        # The native authoring command must also edit the new proxy storage.
        report["proxiedMaterialAuthoring"] = json.loads(cmds.mmdAuthoringSetMaterialValues(payload=json.dumps({
            "version": 1, "root": cmds.ls(root, long=True)[0], "shader": shaders[0],
            "material_index": 0, "updates": [
                {"field": "diffuse_color", "value": [0.05, 0.8, 0.1]},
                {"field": "diffuse_alpha", "value": 0.35},
            ],
        })))
        assert report["proxiedMaterialAuthoring"]["ok"], report["proxiedMaterialAuthoring"]
        yield
        assert capture("proxied_material") == authored_preview
        cmds.undo()
        yield
        assert capture("proxied_material_undo") == initial
        cmds.redo()
        yield
        assert capture("proxied_material_redo") == authored_preview
        cmds.undo()
        authored = {index: _collect_mmd_material_dict(shader) for index, shader in shaders.items()}
        cmds.setAttr(material_nodes[0] + ".weight", 1.0)
        report["materialValues"] = {shader: {"baseColor": cmds.getAttr(shader + ".baseColor"),
                                           "opacity": cmds.getAttr(shader + ".opacity")}
                                    for shader in shaders.values()}
        yield
        material_changed = capture("material_morph")
        report["materialMorphPixels"] = changed(initial, material_changed)
        if migrate_legacy:
            from tools.render_override.migration_checks import check_legacy_migration

            report["migration"] = check_legacy_migration(cmds, root, proxies, sources, shaders, out)
            yield
            assert capture("migrated") == material_changed, "migration changed either preview"
        assert build_material_morph_graph(root)["success"]
        yield
        assert capture("material_rebuild") == material_changed
        assert authored == {index: _collect_mmd_material_dict(shader) for index, shader in shaders.items()}

        scene = out / "scene.ma"
        cmds.file(rename=str(scene))
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(str(scene), open=True, force=True)
        yield
        reloaded = capture("reloaded")
        assert reloaded == material_changed, "both panels must survive scene reload"
        assert authored == {index: _collect_mmd_material_dict(shader) for index, shader in shaders.items()}
        from mmd_tools.actions.export_model_action import ExportModelAction, ExportModelRequest
        from mmd_tools.core.pmx_data import PmxData

        exported_path = out / "roundtrip.pmx"
        export_result = ExportModelAction().execute(ExportModelRequest(
            file_path=str(exported_path), options={"export_format": "pmx", "target_model": root},
        ))
        assert export_result.succeeded, export_result
        exported = PmxData().parse_file(str(exported_path))
        assert len(exported.materials) == len(authored)
        for index, material in enumerate(exported.materials):
            expected = authored[index]
            assert all(abs(a - b) < 1e-6 for a, b in zip(material.diffuse, expected["diffuse"]))
            assert material.draw_flag == expected["draw_flag"]
            if textured:
                assert material.texture_index >= 0 and exported.textures[material.texture_index]
        report["pmxExport"] = {"path": str(exported_path), "materials": len(exported.materials),
                               "authoredDiffusePreserved": True}
        report["ordered"] = json.loads(cmds.mmdOrderedRenderWitness(shadowDepth=True))
        assert report["ordered"]["state"] == "active", report["ordered"]
        control = cmds.polySphere(name="nonMmdControl", radius=1.5)[0]
        cmds.setAttr(control + ".translate", 4, 3, 5, type="double3")
        cmds.select(clear=True)
        yield
        with_control = capture("non_mmd_control")
        report["nonMmdPixels"] = changed(reloaded, with_control)
        cmds.select(sources[0] + ".f[0]", replace=True)
        yield
        selected = capture("component_selection")
        report["componentSelectionPixels"] = changed(with_control, selected)
        assert cmds.filterExpand(selectionMask=34), "source mesh face is not selectable"
        cmds.select(clear=True)
        assert capture("selection_cleared") == with_control
        cmds.select(sources[0], replace=True)
        from maya import mel
        mel.eval(f'enableIsolateSelect("{render}", 1)')
        assert cmds.isolateSelect(render, q=True, state=True)
        report["isolateMembers"] = cmds.sets(cmds.isolateSelect(render, q=True, viewObjects=True), q=True)
        report["sourceNames"] = sources
        cmds.select(clear=True)
        yield
        capture("isolated_source")
        report["isolateOverride"] = cmds.modelEditor(render, q=True, rendererOverrideName=True)
        report["renderPanel"] = render
        from maya.api import OpenMayaUI as omui
        omui.M3dView.getM3dViewFromModelPanel(render).refresh(False, True)
        report["panelOverrides"] = {p: cmds.modelEditor(p, q=True, rendererOverrideName=True)
                                    for p in cmds.getPanel(type="modelPanel")}
        report["isolatedSource"] = json.loads(cmds.mmdOrderedRenderWitness())
        assert report["isolatedSource"]["panel"] == render
        assert report["isolatedSource"]["drawCount"] == (1 if split else 2)
        mel.eval(f'enableIsolateSelect("{render}", 0)')
        assert capture("isolate_cleared") == with_control
        from tools.render_override.performance_checks import check_camera_updates
        current_proxy = cmds.listRelatives(root, ad=True, type="mmdRenderShape", fullPath=True)[0]
        report["cameraPerformance"] = check_camera_updates(cmds, current_proxy, render, capture=capture)
        from tools.smoke.maya_fast_import_authoring import _viewport
        report["authoringViewport"] = _viewport(cmds, root, out)
        report["status"] = "pass"
    except Exception:
        report["error"] = traceback.format_exc()
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / "probe.log").write_text(MARKER, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--port", type=int, default=7741)
    parser.add_argument("--evaluation", choices=("off", "serial", "parallel"))
    parser.add_argument("--split-materials", action="store_true")
    parser.add_argument("--migrate-legacy", action="store_true")
    parser.add_argument("--textured", action="store_true")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    out = args.out_dir.resolve()
    plugin = ROOT / f"plug-ins/{args.maya}/Release/mmd_tools_cpp.mll"
    report = run_maya_e2e(
        project_root=ROOT, version=args.maya, out_dir=out, port=args.port, timeout=180,
        log_path=out / "probe.log", report_path=out / "report.json",
        command=((f"from maya import cmds\ncmds.evaluationManager(mode={args.evaluation!r})\n"
                  if args.evaluation else "") +
                 "from tools.render_override.separation_e2e import run_probe\n"
                 f"run_probe({str(out)!r}, {str(plugin)!r}, {args.split_materials!r}, "
                 f"{args.migrate_legacy!r}, {args.textured!r})"),
        marker=MARKER, send_label="mmd-render-separation",
        stale_paths=(out / "probe.log", out / "report.json"),
        env_overrides={"MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11", "MMD_TOOLS_CPP_PLUGIN": str(plugin),
                       "PATH": str(plugin.parent) + os.pathsep + os.environ.get("PATH", "")},
    )
    print(json.dumps(report, indent=2))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
