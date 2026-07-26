"""GUI Viewport 2.0 A/B/C gate for authored PMX normals and GPU skinning.

The capture is deliberately run in a real Maya GUI process.  A, B and C use
the same imported scene, camera and light:

* A: Maya's ``deformer`` evaluator disabled (CPU/reference path).
* B: evaluator enabled and every skinCluster ``blockGPU`` set to ``False``.
* C: evaluator enabled and every skinCluster ``blockGPU`` set to ``True``.

The gate requires non-empty ``deformerEvaluator`` diagnostics for B and a DX11
or OpenGL VP2 device.  Thus a standalone/offscreen capture cannot accidentally
claim that the GPU path was tested.  A and C must be pixel-equivalent while B
must differ on the known authored-normal fixture.  Scene reopen and authored
normal/lock checks are included to catch import-time or serialization loss.

Run with system Python (not mayapy) so a GUI Maya process can be launched::

    python tests/viewport/custom_normals_gpu_override_capture.py --maya 2024
    python tests/viewport/custom_normals_gpu_override_capture.py --maya 2026 \
        --out build/captures/custom-normals-gpu --no-reopen
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import struct
import sys
import time
import traceback
import zlib
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.common import maya_commandport


DEFAULT_MAYA_VERSION = "2024"
DEFAULT_PORT = 7723
DEFAULT_MODEL = "tests/data/for_unit_test/test_1bone_cube.pmx"
COMPLETION_MARKER = "//-- CUSTOM NORMALS GPU OVERRIDE FINISHED --//"
CAPTURE_TIMEOUT = 600
LOGGER = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default=DEFAULT_MAYA_VERSION)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--out", default="build/captures/custom-normals-gpu")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=768)
    parser.add_argument("--launch-mode", choices=("explorer", "powershell", "direct"), default="explorer")
    parser.add_argument("--no-reopen", action="store_true", help="Skip save/reopen verification.")
    parser.add_argument(
        "--allow-skip",
        action="store_true",
        help="Return success when B cannot prove GPU activity (report still says skipped).",
    )
    return parser.parse_args()


def _png_rgb(path: Path) -> tuple[int, int, bytes]:
    """Decode an 8-bit RGB/RGBA PNG using only the standard library."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"not a PNG: {path}")
    pos = 8
    width = height = depth = color_type = None
    compressed = bytearray()
    while pos + 8 <= len(data):
        size = struct.unpack(">I", data[pos : pos + 4])[0]
        kind = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + size]
        pos += size + 12
        if kind == b"IHDR":
            width, height, depth, color_type = struct.unpack(">IIBB", chunk[:10])
        elif kind == b"IDAT":
            compressed.extend(chunk)
        elif kind == b"IEND":
            break
    if depth != 8 or color_type not in (2, 6) or width is None or height is None:
        raise ValueError(f"unsupported PNG format: {path} depth={depth} type={color_type}")
    channels = 4 if color_type == 6 else 3
    raw = zlib.decompress(bytes(compressed))
    stride = width * channels
    previous = bytearray(stride)
    offset = 0
    rgb = bytearray(width * height * 3)

    def paeth(a: int, b: int, c: int) -> int:
        p = a + b - c
        pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
        return a if pa <= pb and pa <= pc else (b if pb <= pc else c)

    out_offset = 0
    for _ in range(height):
        filter_type = raw[offset]
        offset += 1
        encoded = raw[offset : offset + stride]
        offset += stride
        row = bytearray(stride)
        for index, value in enumerate(encoded):
            left = row[index - channels] if index >= channels else 0
            up = previous[index]
            up_left = previous[index - channels] if index >= channels else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = up
            elif filter_type == 3:
                predictor = (left + up) // 2
            elif filter_type == 4:
                predictor = paeth(left, up, up_left)
            else:
                raise ValueError(f"unsupported PNG filter {filter_type}: {path}")
            row[index] = (value + predictor) & 255
        for index in range(0, stride, channels):
            rgb[out_offset : out_offset + 3] = row[index : index + 3]
            out_offset += 3
        previous = row
    return width, height, bytes(rgb)


def _pixel_delta(first: Path, second: Path) -> dict[str, float | int]:
    width_a, height_a, pixels_a = _png_rgb(first)
    width_b, height_b, pixels_b = _png_rgb(second)
    if (width_a, height_a) != (width_b, height_b):
        raise ValueError(f"capture dimensions differ: {first}={width_a}x{height_a}, {second}={width_b}x{height_b}")
    deltas = [abs(a - b) for a, b in zip(pixels_a, pixels_b)]
    changed = sum(1 for value in deltas if value > 2)
    return {
        "width": width_a,
        "height": height_a,
        "mae": sum(deltas) / max(len(deltas), 1),
        "max": max(deltas, default=0),
        "changedChannelFraction": changed / max(len(deltas), 1),
    }


def _resolve_png(path: Path) -> Path:
    candidates = [path, path.with_suffix(".png")]
    candidates.extend(sorted(path.parent.glob(path.stem + "*.png"), key=lambda item: item.stat().st_mtime, reverse=True))
    for candidate in candidates:
        if candidate.is_file() and candidate.stat().st_size:
            return candidate
    raise FileNotFoundError(f"capture missing: {path}")


# ---------------------------------------------------------------------------
# Maya-side implementation (called through commandPort)
# ---------------------------------------------------------------------------
def run_capture(
    log_path: str,
    model_path: str,
    output_dir: str,
    width: int = 1024,
    height: int = 768,
    reopen: bool = True,
) -> None:
    """Run the A/B/C gate inside a live Maya GUI and write ``report.json``."""
    import contextlib
    import io

    import maya.cmds as cmds
    from maya.api import OpenMaya as om

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    log = Path(log_path)
    report_path = output / "report.json"
    report: dict = {
        "kind": "custom-normals-gpu-override",
        "status": "failed",
        "model": str(model_path),
        "captures": {},
        "errors": [],
    }

    def note(message: object) -> None:
        text = str(message)
        print(text)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")
            handle.flush()

    def write_report() -> None:
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    saved_eval_mode = (cmds.evaluationManager(query=True, mode=True) or ["parallel"])[0]
    deformer_loaded = bool(cmds.pluginInfo("deformerEvaluator", query=True, loaded=True))
    saved_deformer = None
    saved_block_gpu: dict[str, bool] = {}
    skin_clusters: list[str] = []
    root_node = None
    scene_path = output / "custom_normals_gpu_override_reopen.ma"
    try:
        if not deformer_loaded:
            try:
                cmds.loadPlugin("deformerEvaluator", quiet=True)
                note("loaded deformerEvaluator plugin")
            except Exception as exc:
                raise RuntimeError(f"deformerEvaluator plugin unavailable: {exc}") from exc
        try:
            saved_deformer = bool(cmds.evaluator(name="deformer", query=True, enable=True))
        except Exception as exc:
            raise RuntimeError(f"deformer evaluator query unavailable: {exc}") from exc

        cmds.file(new=True, force=True)
        try:
            cmds.loadPlugin("dx11Shader", quiet=True)
        except Exception as exc:
            note(f"dx11Shader load warning: {exc}")
        from mmd_tools.core import settings
        from mmd_tools.io.mmd_importer import import_mmd_file

        settings.set("import.model.create_mmd_shaders", True)
        settings.set("import.model.mmd_shader_backend", "dx11")
        note(f"VP2 device: {cmds.ogs(deviceInformation=True)}")
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            root_node = import_mmd_file(str(model_path), options={"import_physics": False})
        if captured.getvalue().strip():
            note("IMPORT OUTPUT >>>\n" + captured.getvalue() + "<<< IMPORT OUTPUT")
        if not root_node or not cmds.objExists(root_node):
            raise RuntimeError(f"import_mmd_file returned no root: {root_node!r}")
        meshes = cmds.listRelatives(root_node, allDescendents=True, type="mesh", fullPath=True) or []
        if not meshes:
            raise RuntimeError(f"import produced no mesh under {root_node}")
        for shape in meshes:
            for node in cmds.listHistory(shape, pruneDagObjects=True) or []:
                if cmds.nodeType(node) == "skinCluster" and node not in skin_clusters:
                    skin_clusters.append(node)
        if not skin_clusters:
            raise RuntimeError("A/B/C gate requires at least one skinCluster")
        for cluster in skin_clusters:
            if cmds.attributeQuery("blockGPU", node=cluster, exists=True):
                saved_block_gpu[cluster] = bool(cmds.getAttr(f"{cluster}.blockGPU"))
        if not saved_block_gpu:
            raise RuntimeError("skinClusters do not expose blockGPU")

        # Verify the imported authored normal and lock state before any capture.
        selection = om.MSelectionList()
        selection.add(meshes[0])
        mesh_fn = om.MFnMesh(selection.getDagPath(0))
        _, normal_ids = mesh_fn.getNormalIds()
        normals = mesh_fn.getNormals(om.MSpace.kObject)
        locked = [int(index) for index in set(normal_ids) if mesh_fn.isNormalLocked(int(index))]
        authored_report = {"normalCount": len(normals), "lockedCount": len(locked), "first": None, "knownFixtureMatch": None}
        if not locked:
            raise RuntimeError("imported mesh has no locked authored normals")
        first = normals[locked[0]]
        authored_report["first"] = [float(first.x), float(first.y), float(first.z)]
        if Path(model_path).name.lower() == "test_1bone_cube.pmx":
            expected = (-0.8164966, -0.4082483, 0.4082483)
            authored_report["knownFixtureMatch"] = all(
                math.isclose(float(actual), target, rel_tol=0.0, abs_tol=1.0e-4)
                for actual, target in zip((first.x, first.y, first.z), expected)
            )
            if not authored_report["knownFixtureMatch"]:
                raise RuntimeError(f"known authored normal mismatch: {authored_report['first']}")
        report["authoredNormal"] = authored_report

        influences = cmds.skinCluster(skin_clusters[0], query=True, influence=True) or []
        if not influences:
            raise RuntimeError("A/B/C gate requires a skinned joint influence")
        driven_joint = influences[-1]
        cmds.setAttr(f"{driven_joint}.rotate", 0.0, 0.0, 0.0, type="double3")
        cmds.setKeyframe(driven_joint, attribute="rotate", time=0.0)
        cmds.setAttr(f"{driven_joint}.rotate", 23.0, -17.0, 11.0, type="double3")
        cmds.setKeyframe(driven_joint, attribute="rotate", time=1.0)
        report["deformation"] = {
            "joint": driven_joint,
            "rotate": [23.0, -17.0, 11.0],
        }

        # Fixed camera/light and mesh-only VP2 model panel shared by all cases.
        bbox = cmds.exactWorldBoundingBox(meshes)
        center = [(bbox[i] + bbox[i + 3]) * 0.5 for i in range(3)]
        radius = max(math.sqrt(sum((bbox[i + 3] - bbox[i]) ** 2 for i in range(3))) * 0.5, 1.0)
        distance = max(radius * 2.6, 5.0)
        cmds.setAttr("persp.translateX", center[0] + distance * 0.85)
        cmds.setAttr("persp.translateY", center[1] + distance * 0.60)
        cmds.setAttr("persp.translateZ", center[2] + distance * 0.85)
        cmds.setAttr("persp.rotateX", -24.0)
        cmds.setAttr("persp.rotateY", 43.0)
        cmds.setAttr("persp.rotateZ", 0.0)
        cmds.setAttr("perspShape.focalLength", 52.0)
        light_shape = cmds.directionalLight(name="customNormalsLight", intensity=1.0, rgb=(1.0, 1.0, 1.0))
        light_transform = cmds.listRelatives(light_shape, parent=True)[0]
        cmds.setAttr(f"{light_transform}.rotateX", -48.0)
        cmds.setAttr(f"{light_transform}.rotateY", -32.0)
        panels = cmds.getPanel(type="modelPanel") or []
        panel = panels[0] if panels else None
        if panel:
            cmds.modelEditor(panel, edit=True, camera="persp", rendererName="vp2Renderer", displayAppearance="smoothShaded", displayTextures=True)
            cmds.modelEditor(panel, edit=True, joints=False, locators=False, nurbsCurves=False, handles=False, ikHandles=False, deformers=False, dynamics=False, follicles=False, cameras=False, lights=False, grid=False, headsUpDisplay=False)
            try:
                cmds.setFocus(panel)
            except Exception:
                pass
        note(f"panel={panel} skinClusters={skin_clusters}")

        def diagnostics() -> dict:
            raw = cmds.evaluator(name="deformer", query=True, info=True)
            parsed = raw
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                except (TypeError, ValueError):
                    parsed = {"raw": raw}
            groups = []
            if isinstance(parsed, dict):
                payload = parsed.get("deformerEvaluator", parsed)
                if isinstance(payload, dict):
                    groups = payload.get("groups") or []
            return {
                "deviceInformation": str(cmds.ogs(deviceInformation=True)),
                "evaluationMode": (cmds.evaluationManager(query=True, mode=True) or [None])[0],
                "deformerEnabled": bool(cmds.evaluator(name="deformer", query=True, enable=True)),
                "info": parsed,
                "groups": groups,
                "gpuActiveEvidence": bool(groups),
            }

        def set_case(name: str, evaluator_enabled: bool, block_gpu: bool) -> dict:
            cmds.evaluator(name="deformer", enable=evaluator_enabled)
            for cluster in skin_clusters:
                if cmds.attributeQuery("blockGPU", node=cluster, exists=True):
                    cmds.setAttr(f"{cluster}.blockGPU", block_gpu)
            cmds.evaluationManager(mode="parallel")
            cmds.currentTime(1)
            cmds.dgdirty(allPlugs=True)
            cmds.refresh(force=True)
            time.sleep(1.5)
            base = output / f"{name}.png"
            for old in base.parent.glob(base.stem + "*.png"):
                try:
                    old.unlink()
                except OSError:
                    pass
            cmds.playblast(filename=str(base.with_suffix("")), frame=1, format="image", compression="png", offScreen=True, offScreenViewportUpdate=True, viewer=False, width=width, height=height, forceOverwrite=True, showOrnaments=False, percent=100)
            actual = _resolve_png(base)
            result = diagnostics()
            result.update({"path": str(actual), "bytes": actual.stat().st_size, "blockGPU": block_gpu})
            report["captures"][name] = result
            note(f"{name}: {json.dumps(result, ensure_ascii=False)}")
            return result

        capture_a = set_case("A_cpu_reference", False, False)
        capture_b = set_case("B_gpu_enabled", True, False)
        capture_c = set_case("C_gpu_blocked", True, True)
        if not capture_b["gpuActiveEvidence"]:
            report["status"] = "skipped"
            report["skipReason"] = "deformerEvaluator diagnostics contained no active groups for B; GPU path was not proven"
            raise RuntimeError(report["skipReason"])
        report["A_vs_C"] = _pixel_delta(Path(capture_a["path"]), Path(capture_c["path"]))
        report["B_vs_A"] = _pixel_delta(Path(capture_b["path"]), Path(capture_a["path"]))
        parity = report["A_vs_C"]
        difference = report["B_vs_A"]
        report["gate"] = {
            "A_C_pixelParity": bool(parity["mae"] <= 1.0 and parity["changedChannelFraction"] <= 0.01),
            "B_differs": bool(difference["mae"] >= 0.5 and difference["changedChannelFraction"] >= 0.005),
        }
        if not report["gate"]["A_C_pixelParity"]:
            raise RuntimeError(f"A/C pixel parity failed: {parity}")
        if not report["gate"]["B_differs"]:
            raise RuntimeError(f"B/A difference was not measurable: {difference}")

        if reopen:
            cmds.file(rename=str(scene_path))
            cmds.file(save=True, type="mayaAscii", force=True)
            cmds.file(str(scene_path), open=True, force=True)
            reopened_meshes = cmds.ls(type="mesh", long=True) or []
            if not reopened_meshes:
                raise RuntimeError("scene reopen produced no mesh")
            selection = om.MSelectionList()
            selection.add(reopened_meshes[0])
            reopened_fn = om.MFnMesh(selection.getDagPath(0))
            _, reopened_ids = reopened_fn.getNormalIds()
            reopened_locked = sum(1 for index in set(reopened_ids) if reopened_fn.isNormalLocked(int(index)))
            if reopened_locked <= 0:
                raise RuntimeError("scene reopen lost authored normal locks")
            report["sceneReopen"] = {"path": str(scene_path), "lockedCount": reopened_locked}

        report["status"] = "passed"
        note("CUSTOM NORMALS GPU OVERRIDE: PASS")
    except Exception as exc:
        report["errors"].append(str(exc))
        note("EXCEPTION:\n" + traceback.format_exc())
    finally:
        # Restore all mutable evaluator and skinCluster state even when a gate
        # fails.  Names can change during scene reopen, so only restore nodes
        # that still exist.
        try:
            if saved_deformer is not None:
                cmds.evaluator(name="deformer", enable=saved_deformer)
        except Exception as exc:
            report["errors"].append(f"deformer evaluator restore failed: {exc}")
        try:
            cmds.evaluationManager(mode=saved_eval_mode)
        except Exception as exc:
            report["errors"].append(f"evaluationManager restore failed: {exc}")
        for cluster, value in saved_block_gpu.items():
            if cmds.objExists(cluster) and cmds.attributeQuery("blockGPU", node=cluster, exists=True):
                try:
                    cmds.setAttr(f"{cluster}.blockGPU", value)
                except Exception as exc:
                    report["errors"].append(f"{cluster}.blockGPU restore failed: {exc}")
        write_report()
        note(f"REPORT: {report_path}")
        note(COMPLETION_MARKER)


def main() -> int:
    """Launch Maya GUI, send the short commandPort call, and validate report."""
    args = _parse_args()
    project_root = _PROJECT_ROOT
    model_path = Path(args.model)
    if not model_path.is_absolute():
        model_path = (project_root / model_path).resolve()
    if not model_path.is_file():
        raise FileNotFoundError(model_path)
    output = Path(args.out)
    if not output.is_absolute():
        output = (project_root / output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    log_path = output / "capture.log"
    report_path = output / "report.json"
    for stale in (log_path, report_path):
        if stale.exists():
            stale.unlink()
    if maya_commandport.is_port_open(args.port):
        raise RuntimeError(f"commandPort :{args.port} already open; choose another --port")
    process = maya_commandport.launch_maya(
        version=args.maya,
        project_root=project_root,
        output_dir=output,
        port=args.port,
        launch_mode=args.launch_mode,
        env_overrides={"MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11"},
    )
    try:
        maya_commandport.wait_for_port(args.port, timeout=120, process=process)
        command = (
            "import sys\n"
            f"sys.path.insert(0, {str(project_root)!r})\n"
            "from tests.viewport.custom_normals_gpu_override_capture import run_capture\n"
            f"run_capture({str(log_path)!r}, {str(model_path)!r}, {str(output)!r}, {args.width}, {args.height}, reopen={not args.no_reopen})\n"
        )
        maya_commandport.send_python(args.port, command, label="<custom-normals-gpu-override>")
        if not maya_commandport.tail_until_marker(log_path, COMPLETION_MARKER, CAPTURE_TIMEOUT):
            raise TimeoutError(f"capture did not finish within {CAPTURE_TIMEOUT}s: {log_path}")
        if not report_path.is_file():
            raise RuntimeError(f"capture report missing: {report_path}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("status") == "skipped":
            message = f"GPU gate skipped: {report.get('skipReason', report.get('errors'))}"
            if args.allow_skip:
                LOGGER.warning(message)
                return 0
            raise RuntimeError(message)
        if report.get("status") != "passed":
            raise RuntimeError(f"custom normals GPU gate failed: {report.get('errors')}")
        LOGGER.info("custom normals GPU A/B/C gate passed: %s", report_path)
        return 0
    finally:
        maya_commandport.quit_maya(args.port)
        try:
            if process is not None:
                process.wait(timeout=30)
        except Exception:
            if process is not None:
                process.kill()
        maya_commandport.close_process_logs(process)


if __name__ == "__main__":
    raise SystemExit(main())
