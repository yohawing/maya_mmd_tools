"""Maya GUI gate for the Animator Toolset tri-state visibility contract.

The probe builds a tiny parent-transform/polyCube scene in a real Maya GUI,
frames it in the active model panel, and performs an actual viewport hit test
through :class:`maya.api.OpenMaya.MGlobal`.  A normal display must be pickable;
after the parent receives ``overrideDisplayType=2`` (Reference), the same
surface hit must produce no selection while the shape remains visible and the
panel remains smooth shaded.  The JSON report is fail-closed and is written
under ``build/e2e`` by default.

Usage::

    python tests/viewport/animator_visibility_tristate_gate.py --maya 2024
    python tests/viewport/animator_visibility_tristate_gate.py --maya 2026
"""

from __future__ import annotations

import argparse
import ctypes
import io
import json
import logging
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.viewport.maya_e2e_harness import run_maya_e2e

COMMAND_PORT = 7768
COMPLETION_MARKER = "//-- ANIMATOR_VISIBILITY_TRISTATE_DONE --//"
TEST_TIMEOUT = 180.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _write_report(path: Path, report: Dict[str, Any]) -> None:
    """Write a deterministic UTF-8 JSON report, creating its parent first."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _active_model_panel(cmds: Any) -> str:
    """Focus and return a model panel, failing closed when Maya has none."""

    focused = str(cmds.getPanel(withFocus=True) or "")
    if focused and str(cmds.getPanel(typeOf=focused)) == "modelPanel":
        panel = focused
    else:
        panels = [str(value) for value in (cmds.getPanel(type="modelPanel") or [])]
        if not panels:
            raise RuntimeError("Maya has no modelPanel for viewport hit testing")
        panel = panels[0]
    cmds.setFocus(panel)
    return panel


def _viewport_center(omui: Any) -> List[int]:
    """Return the active model-panel center in viewport-local coordinates."""

    # The API view is the authoritative viewport surface used by MGlobal's
    # hit test; querying it after setFocus avoids window/control coordinate
    # conversions and works for both Maya 2024 and Maya 2026.
    view = omui.M3dView.active3dView()
    width = int(view.portWidth())
    height = int(view.portHeight())
    if width < 8 or height < 8:
        raise RuntimeError(f"active modelPanel has unusable viewport size {width}x{height}")
    return [width // 2, height // 2]


def _surface_hit(om: Any, x: int, y: int) -> Dict[str, Any]:
    """Perform one real surface hit test and return the resulting selection.

    ``cmds.select`` is intentionally not used: it bypasses the viewport and
    would make the reference/unselectable assertion meaningless.
    """

    om.MGlobal.clearSelectionList()
    om.MGlobal.selectFromScreen(
        int(x),
        int(y),
        int(x),
        int(y),
        om.MGlobal.kReplaceList,
        om.MGlobal.kSurfaceSelectMethod,
    )
    # Reading the active list through the API avoids a second, command-driven
    # selection path while retaining stable long DAG names in the report.
    selection = om.MGlobal.getActiveSelectionList()
    names: List[str] = []
    non_dag_count = 0
    for index in range(selection.length()):
        try:
            names.append(str(selection.getDagPath(index).fullPathName()))
        except (RuntimeError, TypeError):
            non_dag_count += 1
    return {
        "names": names,
        "rawLength": int(selection.length()),
        "nonDagCount": non_dag_count,
    }


def _rgba_bytes(image: Any, width: int, height: int) -> bytes:
    """Copy a bounded RGBA8 color buffer returned by ``MImage.pixels``."""

    expected = int(width) * int(height) * 4
    if expected <= 0 or expected > 64 * 1024 * 1024:
        raise ValueError(f"invalid viewport color buffer size: {width}x{height}")
    pixels = image.pixels()
    if isinstance(pixels, int):
        if pixels <= 0:
            raise ValueError("MImage.pixels() returned a null pointer")
        return ctypes.string_at(pixels, expected)
    view = memoryview(pixels).cast("B")
    if view.nbytes < expected:
        raise ValueError(f"RGBA buffer too short: expected {expected}, got {view.nbytes}")
    return view[:expected].tobytes()


def _center_roi(view: Any, om: Any, x: int, y: int, radius: int = 4) -> Dict[str, Any]:
    """Read a deterministic center ROI from the active viewport color buffer."""

    image = om.MImage()
    view.readColorBuffer(image, True)
    width, height = [int(value) for value in image.getSize()]
    pixels = _rgba_bytes(image, width, height)
    left = max(0, int(x) - radius)
    right = min(width - 1, int(x) + radius)
    bottom = max(0, int(y) - radius)
    top = min(height - 1, int(y) + radius)
    samples = []
    roi_bytes = bytearray()
    for row in range(bottom, top + 1):
        for column in range(left, right + 1):
            offset = (row * width + column) * 4
            sample = tuple(int(pixels[offset + channel]) for channel in range(4))
            samples.append(sample)
            roi_bytes.extend(sample)
    if not samples:
        raise ValueError(f"empty viewport center ROI for {width}x{height}")
    rgb_mean = [sum(sample[channel] for sample in samples) / len(samples) for channel in range(3)]
    return {
        "width": width,
        "height": height,
        "bounds": [left, bottom, right, top],
        "sampleCount": len(samples),
        "rgbMean": [round(value, 3) for value in rgb_mean],
        "rgbaCenter": list(samples[len(samples) // 2]),
        "_pixels": bytes(roi_bytes),
    }


def _roi_delta(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    """Compare two same-sized ROI buffers without making a full-image gate."""

    if (left["width"], left["height"], left["bounds"]) != (right["width"], right["height"], right["bounds"]):
        raise ValueError("viewport ROI dimensions changed between captures")
    differences = []
    for index in range(0, len(left["_pixels"]), 4):
        differences.append(max(abs(left["_pixels"][index + channel] - right["_pixels"][index + channel]) for channel in range(3)))
    return {
        "maxRgbDelta": max(differences),
        "meanRgbDelta": round(sum(differences) / len(differences), 3),
        "differentPixels": sum(value >= 8 for value in differences),
        "pixelCount": len(differences),
    }


def _public_roi(roi: Dict[str, Any]) -> Dict[str, Any]:
    """Drop the private byte payload before serializing the report."""

    return {key: value for key, value in roi.items() if key != "_pixels"}


def _refresh_view(cmds: Any) -> None:
    """Allow a newly launched GUI model panel to complete two redraw passes."""

    for _ in range(2):
        cmds.refresh(force=True)
        time.sleep(0.1)


def run_probe(log_path: str, report_path: str) -> None:
    """Run the Maya-side visibility and hit-test assertions."""

    import maya.api.OpenMaya as om
    import maya.api.OpenMayaUI as omui
    import maya.cmds as cmds

    log_file = Path(log_path)
    report_file = Path(report_path)
    report: Dict[str, Any] = {
        "kind": "animator-visibility-tristate",
        "status": "error",
        "mayaVersion": None,
        "panel": None,
        "viewport": {},
        "scene": {},
        "baseline": {},
        "reference": {},
        "checks": {},
        "errors": [],
    }

    def log(message: str) -> None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(str(message) + "\n")
        try:
            print(message)
        except Exception:
            pass

    try:
        report["mayaVersion"] = str(cmds.about(version=True))
        cmds.file(new=True, force=True)
        panel = _active_model_panel(cmds)
        report["panel"] = panel
        cmds.modelEditor(panel, edit=True, displayAppearance="smoothShaded", wireframeOnShaded=False, polymeshes=True)
        background_rgb = [0.1, 0.1, 0.1]
        for name in ("background", "backgroundTop", "backgroundBottom"):
            try:
                cmds.displayRGBColor(name, *background_rgb)
            except Exception:
                pass
        report["viewport"]["backgroundRgb"] = background_rgb

        root = cmds.group(empty=True, name="animatorVisibilityGate_root")
        cube, _history = cmds.polyCube(name="animatorVisibilityGate_cube", width=2.0, height=2.0, depth=2.0)
        cmds.parent(cube, root)
        shapes = [str(value) for value in (cmds.listRelatives(cube, shapes=True, fullPath=True) or [])]
        if not shapes:
            raise RuntimeError(f"cube transform {cube} has no shape")
        shape = shapes[0]
        shader = cmds.shadingNode("lambert", asShader=True, name="animatorVisibilityGate_lambert")
        cmds.setAttr(f"{shader}.color", 0.8, 0.2, 0.1, type="double3")
        shading_group = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="animatorVisibilityGate_SG")
        cmds.connectAttr(f"{shader}.outColor", f"{shading_group}.surfaceShader", force=True)
        cmds.sets(cube, edit=True, forceElement=shading_group)
        report["scene"] = {"root": str(root), "cube": str(cube), "shape": str(shape)}
        cmds.select(cube, replace=True)
        camera = str(cmds.modelEditor(panel, query=True, camera=True) or "")
        if not camera:
            raise RuntimeError(f"modelPanel {panel} has no active camera")
        cmds.viewFit(camera, all=False, fitFactor=0.8)
        cmds.select(clear=True)
        _refresh_view(cmds)

        x, y = _viewport_center(omui)
        report["viewport"].update(
            {
                "x": x,
                "y": y,
                "displayAppearance": str(cmds.modelEditor(panel, query=True, displayAppearance=True)),
            }
        )
        baseline_roi = _center_roi(omui.M3dView.active3dView(), om, x, y)
        baseline_selection = _surface_hit(om, x, y)
        report["baseline"] = {
            "surfaceHitSelection": baseline_selection["names"],
            "surfaceHitRawLength": baseline_selection["rawLength"],
            "surfaceHitNonDagCount": baseline_selection["nonDagCount"],
            "cubePicked": any(str(cube) in name for name in baseline_selection["names"]),
            "centerRoi": _public_roi(baseline_roi),
        }

        cmds.setAttr(f"{root}.overrideEnabled", 1)
        cmds.setAttr(f"{root}.overrideDisplayType", 2)
        _refresh_view(cmds)
        reference_roi = _center_roi(omui.M3dView.active3dView(), om, x, y)
        reference_selection = _surface_hit(om, x, y)
        reference_display_appearance = str(cmds.modelEditor(panel, query=True, displayAppearance=True))

        cmds.setAttr(f"{root}.visibility", 0)
        _refresh_view(cmds)
        empty_roi = _center_roi(omui.M3dView.active3dView(), om, x, y)
        cmds.setAttr(f"{root}.visibility", 1)
        _refresh_view(cmds)

        baseline_vs_empty = _roi_delta(baseline_roi, empty_roi)
        reference_vs_empty = _roi_delta(reference_roi, empty_roi)

        report["reference"] = {
            "overrideEnabled": bool(cmds.getAttr(f"{root}.overrideEnabled")),
            "overrideDisplayType": int(cmds.getAttr(f"{root}.overrideDisplayType")),
            "rootVisibility": bool(cmds.getAttr(f"{root}.visibility")),
            "cubeVisibility": bool(cmds.getAttr(f"{cube}.visibility")),
            "shapeVisibility": bool(cmds.getAttr(f"{shape}.visibility")),
            "surfaceHitSelection": reference_selection["names"],
            "surfaceHitRawLength": reference_selection["rawLength"],
            "surfaceHitNonDagCount": reference_selection["nonDagCount"],
            "selectionBlocked": reference_selection["rawLength"] == 0,
            "displayAppearance": reference_display_appearance,
            "centerRoi": _public_roi(reference_roi),
            "emptyCenterRoi": _public_roi(empty_roi),
            "roiVsEmpty": reference_vs_empty,
        }

        checks = {
            "smoothShadedPanel": report["viewport"]["displayAppearance"] in {"smoothShaded", "smoothShadedNoTexture"},
            "smoothShadedPanelAfterOverride": reference_display_appearance in {"smoothShaded", "smoothShadedNoTexture"},
            "baselineSurfaceHit": bool(report["baseline"]["cubePicked"]),
            "baselineRoiDiffersFromEmpty": baseline_vs_empty["differentPixels"] > 0 and baseline_vs_empty["maxRgbDelta"] >= 8,
            "referenceOverrideEnabled": report["reference"]["overrideEnabled"] is True,
            "referenceDisplayType": report["reference"]["overrideDisplayType"] == 2,
            "descendantVisible": report["reference"]["rootVisibility"] and report["reference"]["cubeVisibility"] and report["reference"]["shapeVisibility"],
            "referenceRoiDiffersFromEmpty": reference_vs_empty["differentPixels"] > 0 and reference_vs_empty["maxRgbDelta"] >= 8,
            "referenceSurfaceHitBlocked": report["reference"]["selectionBlocked"],
        }
        report["checks"] = checks
        if all(checks.values()):
            report["status"] = "pass"
            log("PASS: reference display preserves shaded visibility and blocks viewport hit selection")
        else:
            report["status"] = "fail"
            report["errors"].append("one or more tri-state viewport checks failed")
            log(f"FAIL: checks={checks}")
    except Exception:
        report["status"] = "error"
        report["errors"].append(traceback.format_exc())
        log(f"EXCEPTION:\n{traceback.format_exc()}")
    finally:
        _write_report(report_file, report)
        log(f"RESULT_JSON: {json.dumps(report, ensure_ascii=False, sort_keys=True)}")
        log(COMPLETION_MARKER)


def main() -> int:
    """Launch one isolated Maya GUI and return a process-style gate status."""

    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    parser = argparse.ArgumentParser(description="Animator Toolset tri-state visibility Maya GUI gate")
    parser.add_argument("--maya", default="2026", help="Maya major version (default: 2026)")
    parser.add_argument("--port", type=int, default=COMMAND_PORT)
    parser.add_argument("--out-dir", type=Path, default=_PROJECT_ROOT / "build" / "e2e")
    args = parser.parse_args()

    out_dir = args.out_dir.resolve()
    log_path = out_dir / f"animator_visibility_tristate_maya{args.maya}.log"
    report_path = out_dir / f"animator_visibility_tristate_maya{args.maya}.json"
    command = (
        "import sys\n"
        "from pathlib import Path\n"
        f"project_root = Path(r'{_PROJECT_ROOT.as_posix()}')\n"
        "if str(project_root) not in sys.path:\n"
        "    sys.path.insert(0, str(project_root))\n"
        "from tests.viewport.animator_visibility_tristate_gate import run_probe\n"
        f"run_probe(r'{log_path.as_posix()}', r'{report_path.as_posix()}')\n"
    )
    report = run_maya_e2e(
        project_root=_PROJECT_ROOT,
        version=str(args.maya),
        out_dir=out_dir,
        port=int(args.port),
        timeout=TEST_TIMEOUT,
        log_path=log_path,
        report_path=report_path,
        command=command,
        marker=COMPLETION_MARKER,
        send_label="<animator-visibility-tristate-command>",
        stale_paths=(log_path, report_path),
        port_error=f"commandPort :{args.port} is already open; choose another --port",
        report_error=f"visibility tri-state report missing: {report_path}",
        log_ready=logger,
        warn_detached=True,
    )
    logger.info("visibility tri-state status: %s", report.get("status"))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
