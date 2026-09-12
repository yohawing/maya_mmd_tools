"""Probe VP2-managed transparent material ordering without product drawing code.

The probe deliberately keeps Maya in charge of geometry.  It compares object
and component object-set filtering, view/alpha changes, outline depth, pass
count scaling, and a two-panel authoring contract.  Failure to isolate faces
of one unified mesh is recorded as a capability result rather than hidden by
splitting the mesh.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
import traceback
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.viewport.maya_e2e_harness import run_maya_e2e  # noqa: E402
from tools.render_override.common import capture_view, write_report  # noqa: E402
from tools.render_override.render_override_visual_gate import read_png_rgb  # noqa: E402

MARKER = "VP2 TRANSPARENCY CHECKS FINISHED"
PASS_COUNTS = (1, 2, 4, 8, 16)


def changed_pixels(first: list[tuple[int, int, int]], second: list[tuple[int, int, int]]) -> int:
    """Count changed RGB pixels in two equally sized captures."""

    if len(first) != len(second):
        raise ValueError("capture sizes differ")
    return sum(a != b for a, b in zip(first, second))


def timing_summary(samples: list[float]) -> dict[str, Any]:
    """Summarize one non-empty refresh sample without inventing a percentile."""

    if not samples:
        raise ValueError("timing samples must not be empty")
    ordered = sorted(samples)
    p95_index = max(0, (95 * len(ordered) + 99) // 100 - 1)
    return {
        "samples": samples,
        "medianMs": statistics.median(samples),
        "meanMs": statistics.mean(samples),
        "p95Ms": ordered[p95_index],
    }


def capability_decision(report: dict[str, Any]) -> dict[str, Any]:
    """Produce the fail-closed R3 decision from directly observed checks."""

    unified = report["unified"]
    split = report["split"]
    contract = report["productContract"]
    return {
        "splitObjectOrdering": bool(split["orderChangedPixels"] > 100),
        "unifiedFaceIsolation": bool(unified["faceIsolationChangedPixels"] > 100),
        "unifiedFaceOrdering": bool(unified["frontOrderChangedPixels"] > 100),
        "viewReverseControllable": bool(unified["reverseOrderChangedPixels"] > 100),
        "materialMorphValueConsumed": bool(report["materialMorph"]["imageChangedPixels"] > 100),
        "twoPanelSourceVisibilityPreserved": bool(contract["sourceVisibilityUnchanged"]),
        "standardSurfaceCanonicalStatePreserved": bool(contract["canonicalStateUnchanged"]),
        "rawDx11ComparisonEligible": bool(report["comparison"]["eligible"]),
    }


def make_override():
    """Create a small scene-operation override that never owns geometry buffers."""

    from maya.api import OpenMaya as om
    from maya.api import OpenMayaRender as omr

    class Scene(omr.MSceneRender):
        def __init__(self, name, members, first, shader=None):
            super().__init__(name)
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
            mask = omr.MClearOperation.kClearAll if self.first else omr.MClearOperation.kClearNone
            self.mClearOperation.setMask(mask)
            self.mClearOperation.setClearGradient(False)
            self.mClearOperation.setClearColor((0.0, 0.0, 0.0, 1.0))
            return self.mClearOperation

    class Override(omr.MRenderOverride):
        def __init__(self):
            super().__init__("vp2TransparencyChecks")
            self.operations = []
            self.index = 0

        def configure(self, groups, shaders=None):
            shaders = shaders or [None] * len(groups)
            self.operations = [
                Scene(f"vp2TransparencyScene{index}", members, index == 0, shaders[index])
                for index, members in enumerate(groups)
            ]
            self.operations.append(omr.MPresentTarget("vp2TransparencyPresent"))

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
            return "VP2 transparency checks"

    return Override()


def _canonical_state(cmds, shaders):
    return {
        shader: {
            "diffuse": list(cmds.getAttr(shader + ".mmdCanonicalDiffuse")[0]),
            "alpha": cmds.getAttr(shader + ".mmdCanonicalAlpha"),
        }
        for shader in shaders
    }


def run_probe(output: str, baseline_report: str = "") -> None:
    """Schedule the generator so commandPort does not block viewport drawing."""

    try:
        from PySide6.QtCore import QTimer
    except ImportError:
        from PySide2.QtCore import QTimer
    steps = probe_steps(Path(output), Path(baseline_report) if baseline_report else None)

    def advance():
        try:
            next(steps)
        except StopIteration:
            return
        QTimer.singleShot(100, advance)

    advance()


def probe_steps(out: Path, baseline_report: Path | None):
    """Run the synthetic R3 capability matrix in a real DX11 viewport."""

    from maya import cmds, utils
    from maya.api import OpenMayaRender as omr

    report: dict[str, Any] = {
        "status": "fail",
        "scope": "synthetic VP2 transparency capability; not full MMD visual parity",
        "probeSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "images": {},
        "notRun": {
            "pmxTextureParity": "not_run; synthetic colors only",
            "gpuTimestamp": "not_run",
        },
    }
    override = None
    outline = None
    candidate_panel = None
    try:
        cmds.file(new=True, force=True)
        if omr.MRenderer.drawAPI() != omr.MRenderer.kDirectX11:
            raise RuntimeError("probe requires DirectX 11")
        report["mayaVersion"] = cmds.about(version=True)
        report["device"] = cmds.ogs(deviceInformation=True)
        cmds.colorManagementPrefs(edit=True, cmEnabled=False)
        cmds.setAttr("hardwareRenderingGlobals.multiSampleEnable", False)
        cmds.setAttr("hardwareRenderingGlobals.transparencyAlgorithm", 0)

        window = cmds.window(widthHeight=(1320, 540))
        pane = cmds.paneLayout(configuration="vertical2")
        edit_panel = cmds.modelPanel(parent=pane)
        candidate_panel = cmds.modelPanel(parent=pane)
        cmds.showWindow(window)
        camera, camera_shape = cmds.camera()
        cmds.setAttr(camera + ".translateZ", 8)
        cmds.setAttr(camera_shape + ".orthographic", True)
        cmds.setAttr(camera_shape + ".orthographicWidth", 6)
        for panel in (edit_panel, candidate_panel):
            cmds.modelEditor(
                panel, edit=True, rendererName="vp2Renderer", camera=camera,
                displayAppearance="smoothShaded", displayTextures=True, grid=False,
                displayLights="default", shadows=True,
            )
        override = make_override()
        omr.MRenderer.registerOverride(override)

        def material(name, color, alpha):
            shader = cmds.shadingNode("standardSurface", asShader=True, name=name)
            cmds.setAttr(shader + ".baseColor", *color, type="double3")
            cmds.setAttr(shader + ".specular", 0.0)
            cmds.setAttr(shader + ".opacity", *([alpha] * 3), type="double3")
            cmds.addAttr(shader, longName="mmdCanonicalDiffuse", attributeType="double3")
            for channel in "RGB":
                cmds.addAttr(
                    shader, longName="mmdCanonicalDiffuse" + channel,
                    attributeType="double", parent="mmdCanonicalDiffuse",
                )
            cmds.setAttr(shader + ".mmdCanonicalDiffuse", *color, type="double3")
            cmds.addAttr(shader, longName="mmdCanonicalAlpha", attributeType="double")
            cmds.setAttr(shader + ".mmdCanonicalAlpha", alpha)
            sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=name + "SG")
            cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader")
            return shader, sg

        red_shader, red_sg = material("r3Red", (1.0, 0.0, 0.0), 0.5)
        blue_shader, blue_sg = material("r3Blue", (0.0, 0.0, 1.0), 0.5)
        meshes = []
        for name, x, z, sg in (
            ("r3RedPlane", -0.4, 0.15, red_sg),
            ("r3BluePlane", 0.4, 0.0, blue_sg),
        ):
            mesh = cmds.polyPlane(
                name=name, width=3, height=3, subdivisionsX=1, subdivisionsY=1,
                axis=(0, 0, 1), constructionHistory=False,
            )[0]
            cmds.move(x, 0, z, mesh)
            cmds.sets(mesh, edit=True, forceElement=sg)
            meshes.append(mesh)
        visibility_before = {mesh: cmds.getAttr(mesh + ".visibility") for mesh in meshes}
        canonical_before = _canonical_state(cmds, (red_shader, blue_shader))
        cmds.select(clear=True)
        yield

        def capture(label, groups=None, shaders=None, panel=None):
            panel = panel or candidate_panel
            cmds.modelEditor(panel, edit=True, rendererOverrideName="")
            if groups is not None:
                override.configure(groups, shaders)
                cmds.modelEditor(
                    panel, edit=True, rendererOverrideName="vp2TransparencyChecks"
                )
            for _ in range(3):
                cmds.refresh(force=True)
                utils.processIdleEvents()
            path = capture_view(cmds, out / (label + ".png"), panel, 640, 480)
            width, height, pixels = read_png_rgb(path)
            report["images"][label] = {
                "path": str(path), "size": [width, height],
                "center": list(pixels[(height // 2) * width + width // 2]),
            }
            return pixels

        split_ab = capture("split_ab", [[meshes[0]], [meshes[1]]])
        split_ba = capture("split_ba", [[meshes[1]], [meshes[0]]])
        split_red = capture("split_red", [[meshes[0]]])
        split_blue = capture("split_blue", [[meshes[1]]])
        report["split"] = {
            "orderChangedPixels": changed_pixels(split_ab, split_ba),
            "isolationChangedPixels": changed_pixels(split_red, split_blue),
        }

        cmds.modelEditor(candidate_panel, edit=True, rendererOverrideName="")
        unified = cmds.polyUnite(meshes, constructionHistory=False, name="r3Unified")[0]
        unified_visibility_before = cmds.getAttr(unified + ".visibility")
        face0, face1 = unified + ".f[0]", unified + ".f[1]"
        cmds.select(clear=True)
        yield
        front_ab = capture("unified_front_ab", [[face0], [face1]])
        front_ba = capture("unified_front_ba", [[face1], [face0]])
        only0 = capture("unified_face0", [[face0]])
        only1 = capture("unified_face1", [[face1]])
        cmds.setAttr(camera + ".translateZ", -8)
        cmds.setAttr(camera + ".rotateY", 180)
        reverse_ab = capture("unified_reverse_ab", [[face0], [face1]])
        reverse_ba = capture("unified_reverse_ba", [[face1], [face0]])
        report["unified"] = {
            "frontOrderChangedPixels": changed_pixels(front_ab, front_ba),
            "faceIsolationChangedPixels": changed_pixels(only0, only1),
            "reverseOrderChangedPixels": changed_pixels(reverse_ab, reverse_ba),
            "frontVsReversePixels": changed_pixels(front_ab, reverse_ab),
        }
        cmds.setAttr(camera + ".translateZ", 8)
        cmds.setAttr(camera + ".rotateY", 0)

        # Consume a real mmdMaterialMorphEval output while keeping canonical
        # values on the StandardSurface node unchanged.
        cmds.loadPlugin(str(ROOT / "plug-ins" / "mmd_tools_plugin.py"), quiet=True)
        evaluator = cmds.createNode("mmdMaterialMorphEval", name="r3MaterialMorphEval")
        for channel in "RGB":
            cmds.connectAttr(
                red_shader + ".mmdCanonicalDiffuse" + channel,
                evaluator + ".baseDiffuse" + channel,
            )
        cmds.connectAttr(red_shader + ".mmdCanonicalAlpha", evaluator + ".baseDiffuseA")
        cmds.setAttr(evaluator + ".contribution[0].operationType", 1)
        cmds.setAttr(evaluator + ".contribution[0].diffuseOffset", 0, 1, 0, 0.35, type="double4")
        for source, destination in zip(
            ("outputDiffuseR", "outputDiffuseG", "outputDiffuseB"),
            ("baseColorR", "baseColorG", "baseColorB"),
        ):
            cmds.connectAttr(evaluator + "." + source, red_shader + "." + destination, force=True)
        for channel in "RGB":
            cmds.connectAttr(
                evaluator + ".outputDiffuseAlpha", red_shader + ".opacity" + channel,
                force=True,
            )
        cmds.setAttr(evaluator + ".contribution[0].weight", 0.0)
        morph_zero = capture("morph_zero", [[face0], [face1]])
        cmds.setAttr(evaluator + ".contribution[0].weight", 1.0)
        morph_one = capture("morph_one", [[face0], [face1]])
        report["materialMorph"] = {
            "nodeType": cmds.nodeType(evaluator),
            "weightZeroAlpha": 0.5,
            "weightOneAlpha": cmds.getAttr(evaluator + ".outputDiffuseAlpha"),
            "imageChangedPixels": changed_pixels(morph_zero, morph_one),
            "canonicalStateUnchanged": canonical_before
            == _canonical_state(cmds, (red_shader, blue_shader)),
        }

        manager = omr.MRenderer.getShaderManager()
        outline = manager.getEffectsFileShader(
            str(Path(__file__).with_name("vp2_probe_outline.fx")), "Outline"
        )
        if outline is None:
            raise RuntimeError("outline effect did not compile")
        outline_sphere = cmds.polySphere(
            name="r3OutlineSphere", radius=1.1, subdivisionsX=32, subdivisionsY=24,
            constructionHistory=False,
        )[0]
        cmds.sets(outline_sphere, edit=True, forceElement=red_sg)
        glass_screen = cmds.polyPlane(
            name="r3GlassScreen", width=2.5, height=2.5, subdivisionsX=1,
            subdivisionsY=1, axis=(0, 0, 1), constructionHistory=False,
        )[0]
        cmds.move(0.7, 0, 1.5, glass_screen)
        cmds.sets(glass_screen, edit=True, forceElement=blue_sg)
        body = capture("outline_body", [[outline_sphere], [glass_screen]])
        edge_before = capture(
            "outline_before_body",
            [[outline_sphere], [outline_sphere], [glass_screen]],
            [None, outline, None],
        )
        edge_after = capture(
            "outline_after_body",
            [[outline_sphere], [glass_screen], [outline_sphere]],
            [None, None, outline],
        )
        report["outlineDepth"] = {
            "outlineChangedPixels": changed_pixels(body, edge_before),
            "operationOrderChangedPixels": changed_pixels(edge_before, edge_after),
            "scope": "synthetic hull and semitransparent body; PMX edge width is not tested",
        }

        # Pass-count slope deliberately repeats one mesh.  Geometry complexity
        # stays fixed, isolating scene-operation and transparent overdraw cost.
        timing_cases = []
        for pass_count in PASS_COUNTS:
            groups = [[unified]] * pass_count
            cmds.modelEditor(candidate_panel, edit=True, rendererOverrideName="")
            override.configure(groups)
            cmds.modelEditor(
                candidate_panel, edit=True, rendererOverrideName="vp2TransparencyChecks"
            )
            for _ in range(4):
                cmds.refresh(force=True)
            samples = []
            for _ in range(12):
                started = time.perf_counter()
                cmds.refresh(force=True)
                samples.append((time.perf_counter() - started) * 1000.0)
            timing_cases.append({"transparentPassCount": pass_count, **timing_summary(samples)})
        report["passScaling"] = {
            "geometry": "same unified mesh repeated; isolates pass and overdraw cost",
            "cases": timing_cases,
            "medianSlopeMsPerPass": (
                timing_cases[-1]["medianMs"] - timing_cases[0]["medianMs"]
            ) / (PASS_COUNTS[-1] - PASS_COUNTS[0]),
        }

        cmds.setAttr(outline_sphere + ".visibility", False)
        cmds.setAttr(glass_screen + ".visibility", False)
        stock = capture("authoring_panel", None, panel=edit_panel)
        candidate = capture("candidate_panel", [[unified]], panel=candidate_panel)
        unified_visibility_after = cmds.getAttr(unified + ".visibility")
        canonical_after = _canonical_state(cmds, (red_shader, blue_shader))
        report["productContract"] = {
            "sameMeshInBothPanels": True,
            "editPanelOverride": cmds.modelEditor(
                edit_panel, query=True, rendererOverrideName=True
            ),
            "candidatePanelOverride": cmds.modelEditor(
                candidate_panel, query=True, rendererOverrideName=True
            ),
            "sourceVisibilityBeforeUnite": visibility_before,
            "unifiedSourceVisibilityBefore": unified_visibility_before,
            "unifiedSourceVisibilityAfter": unified_visibility_after,
            "sourceVisibilityUnchanged": (
                bool(unified_visibility_before) == bool(unified_visibility_after)
            ),
            "canonicalStateUnchanged": canonical_before == canonical_after,
            "panelImageDifferencePixels": changed_pixels(stock, candidate),
            "centerPixelEqual": (
                report["images"]["authoring_panel"]["center"]
                == report["images"]["candidate_panel"]["center"]
            ),
            "imageDifferenceScope": (
                "full-frame pixels include the stock gray and override black clear colors"
            ),
        }
        report["route"] = {
            "geometryOwner": "Maya Viewport 2.0",
            "customVertexOrIndexBuffers": False,
            "mmdRenderShapeCount": len(cmds.ls(type="mmdRenderShape") or []),
            "candidateRendererOverride": "vp2TransparencyChecks",
        }

        comparison = {
            "eligible": False,
            "reason": (
                "no raw-DX11 baseline supplied; R4 must compare an identical model, camera, "
                "viewport, warmup, frames, and repeats"
            ),
        }
        if baseline_report:
            baseline = json.loads(baseline_report.read_text(encoding="utf-8"))
            comparison["baselinePath"] = str(baseline_report)
            comparison["baselineStatus"] = baseline.get("status")
            comparison["reason"] = (
                "external baseline retained for R4, but synthetic fixture identity cannot "
                "match a product raw-DX11 PMX run"
            )
        report["comparison"] = comparison
        report["capabilities"] = capability_decision(report)
        report["status"] = "pass"
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        try:
            if candidate_panel:
                cmds.modelEditor(candidate_panel, edit=True, rendererOverrideName="")
            if override:
                omr.MRenderer.deregisterOverride(override)
                override.operations = []
            if outline:
                omr.MRenderer.getShaderManager().releaseShader(outline)
        except Exception:
            report["status"] = "fail"
            report["cleanupError"] = traceback.format_exc()
        write_report(out / "report.json", report)
        (out / "probe.log").write_text(MARKER + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", choices=("2024", "2026"), default="2026")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=7771)
    parser.add_argument("--baseline-report", type=Path)
    args = parser.parse_args(argv)
    out = args.out_dir.resolve()
    if not out.is_relative_to((ROOT / "build").resolve()):
        parser.error("--out-dir must be inside the repository build directory")
    if args.baseline_report and not args.baseline_report.is_file():
        parser.error("--baseline-report must be a readable JSON file")
    report = run_maya_e2e(
        project_root=ROOT, version=args.maya, out_dir=out, port=args.port, timeout=240,
        log_path=out / "probe.log", report_path=out / "report.json",
        command=(
            "from maya import cmds\n"
            "from tools.render_override.vp2_transparency_checks import run_probe\n"
            f"cmds.evalDeferred(lambda: run_probe({str(out)!r}, "
            f"{str(args.baseline_report.resolve()) if args.baseline_report else ''!r}), "
            "lowestPriority=True)"
        ),
        marker=MARKER, send_label="vp2-transparency-checks",
        stale_paths=(out / "probe.log", out / "report.json"),
        env_overrides={
            "MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11",
            "MAYA_SKIP_USERSETUP_PY": "1",
            "MMD_TOOLS_SKIP_SHADER_OVERRIDE": "1",
        },
    )
    print(json.dumps({key: value for key, value in report.items() if key != "images"}, indent=2))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
