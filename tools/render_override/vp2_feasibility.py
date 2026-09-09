"""Measure VP2 scene-pass filtering capabilities in an isolated Maya GUI.

This is a capability probe, not an MMD renderer or a visual parity gate.
All geometry drawing and buffers belong to Maya. No product plugin is loaded.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.viewport.maya_e2e_harness import run_maya_e2e  # noqa: E402
from tools.render_override.common import capture_view  # noqa: E402
from tools.render_override.render_override_visual_gate import read_png_rgb  # noqa: E402

MARKER = "VP2 FEASIBILITY FINISHED"


def make_override():
    """Build a scene-only override following Autodesk's object-set example."""
    from maya.api import OpenMaya as om
    from maya.api import OpenMayaRender as omr

    class Scene(omr.MSceneRender):
        def __init__(self, name, members, first, shader=None):
            super().__init__(name)
            self.members = None
            if members is not None:
                self.members = om.MSelectionList()
                for member in members:
                    self.members.add(member)
            self.first = first
            self.shader = shader

        def shaderOverride(self):
            return self.shader

        def objectSetOverride(self):
            return self.members

        def renderFilterOverride(self):
            return omr.MSceneRender.kRenderShadedItems

        def clearOperation(self):
            self.mClearOperation.setMask(
                omr.MClearOperation.kClearAll if self.first else omr.MClearOperation.kClearNone)
            self.mClearOperation.setClearGradient(False)
            self.mClearOperation.setClearColor((0.0, 0.0, 0.0, 1.0))
            return self.mClearOperation

    class Override(omr.MRenderOverride):
        def __init__(self):
            super().__init__("vp2Feasibility")
            self.operations = []
            self.index = 0

        def configure(self, groups, shaders=None):
            shaders = shaders or [None] * len(groups)
            self.operations = [Scene("probeScene%d" % i, members, i == 0, shaders[i])
                               for i, members in enumerate(groups)]
            self.operations.append(omr.MPresentTarget("probePresent"))

        def supportedDrawAPIs(self):
            return omr.MRenderer.kDirectX11

        def startOperationIterator(self):
            self.index = 0
            return True

        def renderOperation(self):
            return self.operations[self.index]

        def nextRenderOperation(self):
            self.index += 1
            return self.index < len(self.operations)

        def uiName(self):
            return "VP2 feasibility probe"

    return Override()


def run_probe(output):
    """Schedule captures outside the commandPort handler."""
    try:
        from PySide6.QtCore import QTimer
    except ImportError:
        from PySide2.QtCore import QTimer
    steps = probe_steps(Path(output))

    def advance():
        try:
            next(steps)
        except StopIteration:
            return
        QTimer.singleShot(100, advance)

    advance()


def probe_steps(out):
    """Compare split objects and face subsets without assuming support."""
    from maya import cmds, utils
    from maya.api import OpenMayaRender as omr

    report = {"status": "fail", "mayaVersion": cmds.about(version=True),
              "scope": "synthetic VP2 capability; not MMD parity", "images": {},
              "probeSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "effectSha256": hashlib.sha256(Path(__file__).with_name(
                  "vp2_probe_outline.fx").read_bytes()).hexdigest()}
    override = None
    outline = None
    panel = None
    try:
        cmds.file(new=True, force=True)
        assert omr.MRenderer.drawAPI() == omr.MRenderer.kDirectX11
        report["device"] = cmds.ogs(deviceInformation=True)
        cmds.colorManagementPrefs(edit=True, cmEnabled=False)
        cmds.setAttr("hardwareRenderingGlobals.multiSampleEnable", False)
        cmds.setAttr("hardwareRenderingGlobals.transparencyAlgorithm", 0)
        window = cmds.window(widthHeight=(660, 520))
        pane = cmds.paneLayout()
        panel = cmds.modelPanel(parent=pane)
        cmds.showWindow(window)
        camera, camera_shape = cmds.camera()
        cmds.setAttr(camera + ".translateZ", 8)
        cmds.setAttr(camera_shape + ".orthographic", True)
        cmds.setAttr(camera_shape + ".orthographicWidth", 6)
        cmds.modelEditor(panel, edit=True, rendererName="vp2Renderer", camera=camera,
                         displayAppearance="smoothShaded", grid=False, displayTextures=True,
                         displayLights="all", shadows=True)
        override = make_override()
        omr.MRenderer.registerOverride(override)

        def material(name, color, alpha):
            shader = cmds.shadingNode("surfaceShader", asShader=True, name=name)
            cmds.setAttr(shader + ".outColor", *color, type="double3")
            cmds.setAttr(shader + ".outTransparency", *([1 - alpha] * 3), type="double3")
            sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
            cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader")
            return sg

        red = material("red", (1, 0, 0), .5)
        blue = material("blue", (0, 0, 1), .5)
        meshes = []
        for name, x, z, sg in (("redPlane", -.4, .15, red), ("bluePlane", .4, 0, blue)):
            mesh = cmds.polyPlane(name=name, width=3, height=3, subdivisionsX=1,
                                  subdivisionsY=1, axis=(0, 0, 1), constructionHistory=False)[0]
            cmds.move(x, 0, z, mesh)
            cmds.sets(mesh, edit=True, forceElement=sg)
            meshes.append(mesh)
        cmds.select(clear=True)
        yield

        def capture(label, groups, shaders=None):
            if groups is None:
                cmds.modelEditor(panel, edit=True, rendererOverrideName="")
            else:
                # Configure only while this override is not executing.
                cmds.modelEditor(panel, edit=True, rendererOverrideName="")
                override.configure(groups, shaders)
                cmds.modelEditor(panel, edit=True, rendererOverrideName="vp2Feasibility")
            for _ in range(3):
                cmds.refresh(force=True)
                utils.processIdleEvents()
            path = capture_view(cmds, out / (label + ".png"), panel, 640, 480)
            width, height, pixels = read_png_rgb(path)
            report["images"][label] = {"path": str(path), "size": [width, height],
                "center": list(pixels[(height // 2) * width + width // 2])}
            return pixels

        def difference(a, b):
            assert len(a) == len(b)
            return sum(x != y for x, y in zip(a, b))

        capture("split_stock", None)
        ab = capture("split_ab", [[meshes[0]], [meshes[1]]])
        ba = capture("split_ba", [[meshes[1]], [meshes[0]]])
        red_only = capture("split_red", [[meshes[0]]])
        blue_only = capture("split_blue", [[meshes[1]]])
        report["split"] = {"orderChangedPixels": difference(ab, ba),
                           "isolationChangedPixels": difference(red_only, blue_only)}
        assert report["split"]["isolationChangedPixels"] > 100, "fixture did not render"
        report["split"]["orderedBlendObserved"] = (
            report["images"]["split_ab"]["center"] == [64, 0, 127]
            and report["images"]["split_ba"]["center"] == [127, 0, 64])
        cmds.modelEditor(panel, edit=True, rendererOverrideName="")
        unified = cmds.polyUnite(meshes, constructionHistory=False, name="unified")[0]
        cmds.select(clear=True)
        yield
        face0, face1 = unified + ".f[0]", unified + ".f[1]"
        uab = capture("unified_ab", [[face0], [face1]])
        uba = capture("unified_ba", [[face1], [face0]])
        u0 = capture("unified_face0", [[face0]])
        u1 = capture("unified_face1", [[face1]])
        report["unified"] = {"orderChangedPixels": difference(uab, uba),
                             "isolationChangedPixels": difference(u0, u1),
                             "splitAbChangedPixels": difference(ab, uab)}
        # A changed viewpoint and a vertex edit exercise native mesh updates.
        cmds.setAttr(camera + ".rotateY", 5)
        view_ab = capture("unified_view_ab", [[face0], [face1]])
        view_ba = capture("unified_view_ba", [[face1], [face0]])
        report["unified"]["viewOrderChangedPixels"] = difference(view_ab, view_ba)
        cmds.move(.3, .2, 0, unified + ".vtx[0]", relative=True)
        deformed = capture("unified_deformed", [[face0], [face1]])
        report["unified"]["deformationChangedPixels"] = difference(view_ab, deformed)

        # Apply a vertex shader to ordinary Maya geometry, without a custom VB/IB.
        cmds.modelEditor(panel, edit=True, rendererOverrideName="")
        cmds.delete(unified)
        cmds.setAttr(camera + ".rotateY", 0)
        sphere = cmds.polySphere(radius=1.1, subdivisionsX=32, subdivisionsY=24,
                                 constructionHistory=False)[0]
        cmds.sets(sphere, edit=True, forceElement=red)
        screen = cmds.polyPlane(width=2.5, height=2.5, subdivisionsX=1,
                                subdivisionsY=1, axis=(0, 0, 1), constructionHistory=False)[0]
        cmds.move(.7, 0, 1.5, screen)
        cmds.sets(screen, edit=True, forceElement=blue)
        cmds.select(clear=True)
        manager = omr.MRenderer.getShaderManager()
        outline = manager.getEffectsFileShader(str(Path(__file__).with_name("vp2_probe_outline.fx")),
                                               "Outline")
        assert outline is not None, "outline effect did not compile"
        yield
        body = capture("outline_off", [[sphere], [screen]])
        edge_first = capture("outline_before_glass", [[sphere], [sphere], [screen]],
                             [None, outline, None])
        edge_last = capture("outline_after_glass", [[sphere], [screen], [sphere]],
                            [None, None, outline])
        report["outline"] = {"enabledChangedPixels": difference(body, edge_first),
                             "orderChangedPixels": difference(edge_first, edge_last),
                             "scope": "object-space hull only; PMX edge width/flags not tested"}
        # Positive opaque control distinguishes hull support from transparent depth behavior.
        cmds.setAttr("red.outTransparency", 0, 0, 0, type="double3")
        opaque_body = capture("opaque_outline_off", [[sphere], [screen]])
        opaque_edge = capture("opaque_outline_before_glass", [[sphere], [sphere], [screen]],
                              [None, outline, None])
        report["outline"]["opaqueChangedPixels"] = difference(opaque_body, opaque_edge)

        # Native shadow A/B/A. This does not test MMD's shadow projection or modes.
        cmds.modelEditor(panel, edit=True, rendererOverrideName="")
        cmds.delete(screen)
        cmds.move(0, 1.2, 0, sphere)
        floor = cmds.polyPlane(width=7, height=7, subdivisionsX=1,
                               subdivisionsY=1, constructionHistory=False)[0]
        shader = cmds.shadingNode("standardSurface", asShader=True)
        cmds.setAttr(shader + ".baseColor", .7, .7, .7, type="double3")
        cmds.setAttr(shader + ".specular", 0)
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
        cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader")
        cmds.sets([sphere, floor], edit=True, forceElement=sg)
        light = cmds.directionalLight(rotation=(-45, -25, 0), intensity=1)
        cmds.setAttr(light + ".useDepthMapShadows", True)
        cmds.setAttr(light + ".dmapResolution", 1024)
        cmds.setAttr(camera + ".translate", 0, 5, 8, type="double3")
        cmds.setAttr(camera + ".rotateX", -30)
        cmds.setAttr(camera_shape + ".orthographicWidth", 8)
        cmds.select(clear=True)
        yield
        shadow_on = capture("shadow_on", [None])
        cmds.setAttr(light + ".useDepthMapShadows", False)
        shadow_off = capture("shadow_off", [None])
        cmds.setAttr(light + ".useDepthMapShadows", True)
        shadow_restore = capture("shadow_restored", [None])
        split_shadow = capture("shadow_filtered", [[sphere], [floor]])
        cmds.setAttr(light + ".useDepthMapShadows", False)
        split_off = capture("shadow_filtered_off", [[sphere], [floor]])
        report["shadow"] = {"enabledChangedPixels": difference(shadow_on, shadow_off),
                            "restoreChangedPixels": difference(shadow_on, shadow_restore),
                            "filteredChangedPixels": difference(shadow_on, split_shadow),
                            "filteredEnabledChangedPixels": difference(split_shadow, split_off),
                            "scope": "native VP2 shadows; MMD modes/alpha/receiver flags not tested"}
        report["capabilities"] = {
            "splitMaterialOrder": report["split"]["orderedBlendObserved"],
            "unifiedFaceIsolation": report["unified"]["isolationChangedPixels"] > 100,
            "unifiedMaterialOrder": report["unified"]["orderChangedPixels"] > 100,
            "nativeShadowDifference": report["shadow"]["enabledChangedPixels"] > 100,
            "mmdParity": "not_run",
        }
        report["loadedPlugins"] = cmds.pluginInfo(query=True, listPlugins=True) or []
        assert not any("mmd" in name.lower() for name in report["loadedPlugins"]), (
            "product plugin auto-loaded into the VP2-only probe")
        report["status"] = "pass"
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        try:
            if panel:
                cmds.modelEditor(panel, edit=True, rendererOverrideName="")
            if override:
                omr.MRenderer.deregisterOverride(override)
                override.operations = []
            if outline:
                omr.MRenderer.getShaderManager().releaseShader(outline)
        except Exception:
            report["status"] = "fail"
            report["cleanupError"] = traceback.format_exc()
        (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (out / "probe.log").write_text(MARKER + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default="2024", choices=("2024", "2026"))
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=7751)
    args = parser.parse_args()
    out = args.out_dir.resolve()
    if not out.is_relative_to(ROOT / "build"):
        parser.error("--out-dir must be inside this checkout's build directory")
    report = run_maya_e2e(
        project_root=ROOT, version=args.maya, out_dir=out, port=args.port, timeout=180,
        log_path=out / "probe.log", report_path=out / "report.json",
        command=("from maya import cmds\nfrom tools.render_override.vp2_feasibility import run_probe\n"
                 f"cmds.evalDeferred(lambda: run_probe({str(out)!r}), lowestPriority=True)"),
        marker=MARKER, send_label="vp2-feasibility",
        stale_paths=(out / "probe.log", out / "report.json"),
        env_overrides={"MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11",
                       "MAYA_SKIP_USERSETUP_PY": "1"},
    )
    print(json.dumps({key: value for key, value in report.items() if key != "images"}, indent=2))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
