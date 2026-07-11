"""Capture PMX material morph weight pairs through a Maya GUI commandPort.

The host process launches (or attaches to) Maya, imports one exact PMX path,
selects material morph network nodes by their PMX-global ``mmd_morph_index``,
and records weight 0/1 viewport captures plus DG/shader diagnostics.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.common import maya_commandport

COMPLETION_MARKER = "//-- MAYA MATERIAL MORPH E2E FINISHED --//"
DEFAULT_MORPHS = ((158, "制服"), (31, "瞳消し"), (143, "照れ"))
BACKENDS = {
    "dx11": {"shader": "dx11Shader", "plugin": "dx11Shader", "device": "VirtualDeviceDx11"},
    "glsl": {"shader": "GLSLShader", "plugin": "glslShader", "device": "VirtualDeviceGLCore"},
}
LOGGER = logging.getLogger(__name__)
MAX_RGBA_BYTES = 256 * 1024 * 1024


def exception_summary(exc: BaseException) -> str:
    """Format exceptions so empty-message errors such as MemoryError stay useful."""
    message = str(exc)
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def mimage_rgba_buffer(pixels, width: int, height: int):
    """Adapt Maya MImage buffer/pointer results to a bounded byte view."""
    width = int(width)
    height = int(height)
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid MImage dimensions: {width}x{height}")
    expected = width * height * 4
    if expected > MAX_RGBA_BYTES:
        raise ValueError(f"unreasonable MImage byte size: {expected} > {MAX_RGBA_BYTES}")
    if isinstance(pixels, int):
        if pixels <= 0:
            raise ValueError("MImage.pixels() returned a null pointer")
        import ctypes

        return ctypes.string_at(pixels, expected)
    view = memoryview(pixels).cast("B")
    if view.nbytes < expected:
        raise ValueError(f"RGBA buffer too short: expected {expected}, got {view.nbytes}")
    return view[:expected]


def rgba_pixel_stats(buffer, width: int, height: int) -> dict:
    """Compute RGB statistics from exactly one RGBA8 image without copies."""
    expected = int(width) * int(height) * 4
    view = memoryview(buffer).cast("B")
    if view.nbytes < expected:
        raise ValueError(f"RGBA buffer too short: expected {expected}, got {view.nbytes}")
    view = view[:expected]
    minimum = 255
    maximum = 0
    nonzero = 0
    for offset in range(0, expected, 4):
        for channel in range(3):
            component = view[offset + channel]
            minimum = min(minimum, component)
            maximum = max(maximum, component)
            nonzero += component != 0
    return {"rgbMin": minimum, "rgbMax": maximum, "rgbRange": maximum - minimum, "nonzeroRgb": nonzero}


def trace_weight_source_chains(
    start_plug: str,
    target_plug: str,
    upstream,
    node_type,
    *,
    max_depth: int = 8,
) -> list[list[str]]:
    """Trace a morph weight through a bounded set of effective-weight helpers."""
    allowed_helpers = {"plusMinusAverage", "multiplyDivide", "unitConversion"}
    matches: list[list[str]] = []

    def visit(plug: str, chain: list[str], seen: set[str], depth: int) -> None:
        if plug == target_plug:
            matches.append(chain)
            return
        if depth >= max_depth or plug in seen:
            return
        seen = seen | {plug}
        for source in upstream(plug):
            if source == target_plug:
                matches.append([*chain, source])
                continue
            source_node = source.rsplit(".", 1)[0]
            if node_type(source_node) not in allowed_helpers:
                continue
            visit(source, [*chain, source], seen, depth + 1)

    visit(start_plug, [start_plug], set(), 0)
    return matches


def safe_capture_dir(output_root: Path, index: int, label: str) -> Path:
    """Return a deterministic capture directory confined below *output_root*."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(label))
    cleaned = re.sub(r"\.{2,}", "_", cleaned).strip(" ._")
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned)[:64].rstrip(" ._")
    if not cleaned:
        cleaned = "material-morph"
    root = output_root.resolve()
    target = (root / f"{index:03d}_{cleaned}").resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"capture directory escaped output root: {target}") from exc
    return target


def is_diffuse_alpha_only_offsets(items: list[dict]) -> bool:
    """Return whether offsets change diffuse alpha and no other PMX channel.

    PMX additive offsets use zero as the neutral value; multiplicative offsets
    use one.  Edge alpha is deliberately treated as a separate non-diffuse
    channel, as are all texture factors.
    """
    changed_alpha = False
    channel_sizes = {
        "specular": 3,
        "ambient": 3,
        "edge_color": 4,
        "texture_factor": 4,
        "sphere_texture_factor": 4,
        "toon_texture_factor": 4,
    }
    for item in items:
        neutral = 1.0 if int(item.get("operation_type", 1)) == 0 else 0.0
        diffuse = item.get("diffuse", [neutral] * 4)
        if len(diffuse) != 4 or any(abs(float(value) - neutral) > 1e-6 for value in diffuse[:3]):
            return False
        changed_alpha |= abs(float(diffuse[3]) - neutral) > 1e-6
        for name, size in channel_sizes.items():
            values = item.get(name, [neutral] * size)
            if len(values) != size or any(abs(float(value) - neutral) > 1e-6 for value in values):
                return False
        for name in ("specular_coefficient", "edge_size"):
            value = item.get(name, neutral)
            if isinstance(value, (list, tuple)):
                if len(value) != 1:
                    return False
                value = value[0]
            if abs(float(value) - neutral) > 1e-6:
                return False
    return bool(items) and changed_alpha


def parse_morph(value: str) -> tuple[int, str]:
    """Parse an ``INDEX[:LABEL]`` CLI material morph selector."""
    index_text, separator, label = value.partition(":")
    try:
        index = int(index_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid morph index: {index_text!r}") from exc
    if index < 0:
        raise argparse.ArgumentTypeError("morph index must be non-negative")
    return index, label if separator else ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Exact PMX model path to import.")
    parser.add_argument("--maya", default="2026")
    parser.add_argument("--backend", choices=sorted(BACKENDS), default="dx11")
    parser.add_argument("--port", type=int, default=7731)
    parser.add_argument("--out", default="build/captures/material-morph-e2e")
    parser.add_argument("--morph", action="append", type=parse_morph, default=None, metavar="INDEX[:LABEL]")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=640)
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument(
        "--attach-existing",
        action="store_true",
        help="Use an existing commandPort; a modified scene is refused, while a clean scene is reset for import.",
    )
    parser.add_argument("--leave-open", action="store_true", help="Never ask Maya to quit after the run.")
    parser.add_argument("--cleanup-launch-files", action="store_true")
    parser.add_argument(
        "--launch-mode",
        choices=("direct", "powershell", "explorer"),
        default="explorer" if os.name == "nt" else "direct",
    )
    return parser


def _resolve(path: str, root: Path) -> Path:
    candidate = Path(path)
    return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()


def _maya_code(payload: dict) -> str:
    encoded = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
    return f'''\
import base64, json, sys, traceback
from pathlib import Path
import maya.cmds as cmds
import maya.api.OpenMaya as om

P = json.loads(base64.b64decode({encoded!r}).decode("utf-8"))
OUT, LOG = Path(P["out"]), Path(P["log"])
OUT.mkdir(parents=True, exist_ok=True)

def log(value):
    text = str(value)
    print(text)
    with LOG.open("a", encoding="utf-8") as stream:
        stream.write(text + "\\n")
        stream.flush()

def attr(node, name, default=None):
    try:
        return cmds.getAttr(node + "." + name) if cmds.attributeQuery(name, node=node, exists=True) else default
    except Exception as exc:
        return "ERR: " + str(exc)

def values(node, names):
    return {{name: attr(node, name) for name in names if cmds.attributeQuery(name, node=node, exists=True)}}

def png_stats(path):
    from tests.viewport.material_morph_e2e import mimage_rgba_buffer, rgba_pixel_stats
    image = om.MImage()
    image.readFromFile(str(path))
    width, height = image.getSize()
    pixels = mimage_rgba_buffer(image.pixels(), width, height)
    stats = rgba_pixel_stats(pixels, width, height)
    stats.update({{"width": width, "height": height, "bytes": Path(path).stat().st_size}})
    return stats

def device_matches(device):
    text = str(device).lower()
    return ("directx" in text or "dx11" in text) if P["backend"] == "dx11" else ("opengl" in text or "glcore" in text or "gl core" in text)

def find_morph(index):
    matches = []
    for node in cmds.ls(type="network") or []:
        if attr(node, "mmd_morph_index") == index and cmds.attributeQuery("mmd_material_morph_offsets_json", node=node, exists=True):
            matches.append(node)
    if len(matches) != 1:
        raise RuntimeError("material morph index {{}} matched {{}} nodes: {{}}".format(index, len(matches), matches))
    return matches[0]

def offsets(node):
    raw = attr(node, "mmd_material_morph_offsets_json", "[]") or "[]"
    return json.loads(raw)

EVAL_OUTPUTS = ["outputDiffuse", "outputDiffuseAlpha", "outputSpecular", "outputSpecularCoefficient",
                "outputAmbient", "outputEdgeColor", "outputEdgeSize", "outputTextureMultiply", "outputTextureAdd",
                "outputSphereTextureMultiply", "outputSphereTextureAdd", "outputToonTextureMultiply", "outputToonTextureAdd"]
SHADER_CHANNELS = ["Diffuse", "DiffuseAlpha", "diffuse", "transparency", "MMDMaterialDiffuse", "MMDMaterialAlpha",
                   "MaterialDiffuse", "MaterialAlpha", "Ambient", "Specular", "SpecularPower", "EdgeColor", "EdgeSize"]

def diagnostics(morph):
    evaluators = []
    shaders = {{}}
    from tests.viewport.material_morph_e2e import trace_weight_source_chains

    def helper_inputs(plug):
        direct = cmds.listConnections(plug, source=True, destination=False, plugs=True) or []
        if direct:
            return direct
        node, attribute = plug.rsplit(".", 1)
        kind = cmds.nodeType(node)
        candidates = []
        if kind == "plusMinusAverage" and attribute.startswith(("output1D", "output2D", "output3D")):
            for base in ("input1D", "input2D", "input3D"):
                for index in cmds.getAttr(node + "." + base, multiIndices=True) or []:
                    candidates.append("{{}}.{{}}[{{}}]".format(node, base, index))
        elif kind == "multiplyDivide" and attribute.startswith("output"):
            candidates.extend(node + "." + name for name in ("input1", "input1X", "input1Y", "input1Z", "input2", "input2X", "input2Y", "input2Z"))
        elif kind == "unitConversion" and attribute.startswith("output"):
            candidates.append(node + ".input")
        return [candidate for candidate in candidates if cmds.objExists(candidate)]

    for evaluator in cmds.ls(type="mmdMaterialMorphEval") or []:
        chains = []
        for index in cmds.getAttr(evaluator + ".contribution", multiIndices=True) or []:
            weight_plug = "{{}}.contribution[{{}}].weight".format(evaluator, index)
            chains.extend(trace_weight_source_chains(weight_plug, morph + ".weight", helper_inputs, cmds.nodeType))
        if not chains:
            continue
        target = attr(evaluator, "mmd_target_shader", "")
        evaluators.append({{"node": evaluator, "targetShader": target, "outputs": values(evaluator, EVAL_OUTPUTS),
                            "weightSourceChains": chains}})
        if target and cmds.objExists(target):
            shaders[target] = {{"nodeType": cmds.nodeType(target), "uniforms": values(target, SHADER_CHANNELS),
                                "incoming": cmds.listConnections(target, source=True, destination=False, plugs=True, connections=True) or []}}
    return {{"evaluators": evaluators, "shaders": shaders}}

def capture(panel, morph, index, label, weight):
    cmds.setAttr(morph + ".weight", weight)
    cmds.dgdirty(allPlugs=True)
    cmds.refresh(force=True)
    from tests.viewport.material_morph_e2e import safe_capture_dir
    case_dir = safe_capture_dir(OUT, index, label or attr(morph, "mmd_morph_name", ""))
    case_dir.mkdir(parents=True, exist_ok=True)
    png = case_dir / ("weight-{{}}.png".format(int(weight)))
    cmds.playblast(completeFilename=str(png), forceOverwrite=True, format="image", compression="png",
                   width=P["width"], height=P["height"], percent=100, showOrnaments=False,
                   viewer=False, frame=cmds.currentTime(query=True), editorPanelName=panel)
    stats = png_stats(png)
    if stats["bytes"] <= 0 or stats["nonzeroRgb"] <= 0 or stats["rgbRange"] <= 0:
        raise RuntimeError("blank/non-varying capture: " + str(stats))
    return {{"weight": weight, "png": str(png), "pixels": stats, "diagnostics": diagnostics(morph)}}

def flat_outputs(sample):
    result = {{}}
    for evaluator in sample["diagnostics"]["evaluators"]:
        result[evaluator["node"]] = evaluator["outputs"]
    return result

report = {{"schemaVersion": 1, "kind": "maya-material-morph-e2e", "model": P["model"],
          "maya": P["maya"], "backend": P["backend"], "results": [], "errors": []}}
_settings_impl = None
_setting_optionvars = {{}}
_setting_memory_values = {{}}
_changed_setting_keys = ("import.model.create_mmd_shaders", "import.model.mmd_shader_backend")

def snapshot_settings(settings_impl):
    # Settings.set() persists every scalar in Settings.data, so snapshot every
    # optionVar it can touch rather than only the two logical keys overridden.
    snapshot = {{}}
    for key, value in settings_impl._flatten_dict(settings_impl.data).items():
        if not isinstance(value, (bool, int, float, str)):
            continue
        option_key = settings_impl.get_option_var_key(key)
        existed = bool(cmds.optionVar(exists=option_key))
        snapshot[option_key] = {{"existed": existed, "value": cmds.optionVar(query=option_key) if existed else None}}
    return snapshot

def restore_settings(settings_impl, optionvars, memory_values):
    # Restore in-memory values without calling set()/save(), then restore the
    # exact persistent existence/value state (including previously absent vars).
    for key_path, value in memory_values.items():
        keys = key_path.split(".")
        target = settings_impl.data
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
    for option_key, prior in optionvars.items():
        if not prior["existed"]:
            if cmds.optionVar(exists=option_key):
                cmds.optionVar(remove=option_key)
            continue
        value = prior["value"]
        if isinstance(value, bool) or isinstance(value, int):
            cmds.optionVar(intValue=(option_key, int(value)))
        elif isinstance(value, float):
            cmds.optionVar(floatValue=(option_key, value))
        else:
            cmds.optionVar(stringValue=(option_key, str(value)))

try:
    if P["project_root"] not in sys.path:
        sys.path.insert(0, P["project_root"])
    from tests.viewport.material_morph_e2e import is_diffuse_alpha_only_offsets
    if P["attach_existing"] and cmds.file(query=True, modified=True):
        raise RuntimeError("refusing to reset modified scene attached through commandPort; save or discard it explicitly first")
    from mmd_tools.core.settings import get_settings, settings
    from mmd_tools.io.mmd_importer import import_mmd_file
    _settings_impl = get_settings()
    _setting_optionvars = snapshot_settings(_settings_impl)
    _setting_memory_values = {{key: settings.get(key) for key in _changed_setting_keys}}
    cmds.file(new=True, force=True)
    settings.set("import.model.create_mmd_shaders", True)
    settings.set("import.model.mmd_shader_backend", P["backend"])
    cmds.loadPlugin(P["plugin"], quiet=True)
    root = import_mmd_file(P["model"])
    if root is None:
        raise RuntimeError("import_mmd_file returned None")
    from mmd_tools.converters.material_morph_runtime import detect_effective_vp2_draw_api
    device = cmds.ogs(deviceInformation=True)
    runtime_api = detect_effective_vp2_draw_api()
    report["deviceInformation"] = device
    report["deviceValid"] = device_matches(device)
    report["effectiveVp2DrawApi"] = runtime_api
    expected_api = "directx11" if P["backend"] == "dx11" else "openglcore"
    report["apiValid"] = runtime_api == expected_api
    if not report["deviceValid"]:
        raise RuntimeError("VP2 backend mismatch: " + str(device))
    if not report["apiValid"]:
        raise RuntimeError("effective VP2 API mismatch: expected {{}} got {{}}".format(expected_api, runtime_api))
    panel = cmds.modelPanel(label="MaterialMorphE2E")
    cmds.modelEditor(panel, edit=True, displayAppearance="smoothShaded", displayTextures=True, allObjects=True, grid=False)
    cmds.setFocus(panel)
    # GLSLShader uniforms may not materialize until a VP2 panel exists.  The
    # importer schedules one lowest-priority idle retry for that case; process
    # the idle queue here so this fresh-import gate verifies the real contract.
    from maya import utils as maya_utils
    maya_utils.processIdleEvents()
    camera = cmds.modelPanel(panel, query=True, camera=True)
    cmds.viewFit(camera, all=True)
    cmds.currentTime(0)
    for selector in P["morphs"]:
        node = find_morph(int(selector[0]))
        item_offsets = offsets(node)
        samples = [capture(panel, node, int(selector[0]), selector[1], weight) for weight in (0.0, 1.0)]
        invariant = {{"kind": "diffuse-alpha-only", "applicable": is_diffuse_alpha_only_offsets(item_offsets),
                     "passed": None, "failures": []}}
        if invariant["applicable"]:
            zero, one = flat_outputs(samples[0]), flat_outputs(samples[1])
            common = sorted(set(zero) & set(one))
            for evaluator in common:
                rgb0, rgb1 = zero[evaluator].get("outputDiffuse"), one[evaluator].get("outputDiffuse")
                alpha0, alpha1 = zero[evaluator].get("outputDiffuseAlpha"), one[evaluator].get("outputDiffuseAlpha")
                if rgb0 != rgb1:
                    invariant["failures"].append("diffuse RGB changed on " + evaluator)
                if alpha0 == alpha1:
                    invariant["failures"].append("diffuse alpha did not change on " + evaluator)
            if not common:
                invariant["failures"].append("no connected evaluator outputs")
            invariant["passed"] = not invariant["failures"]
            if not invariant["passed"]:
                raise RuntimeError("alpha-only invariant failed for {{}}: {{}}".format(node, invariant["failures"]))
        report["results"].append({{"index": int(selector[0]), "label": selector[1], "node": node,
                                  "metadataName": attr(node, "mmd_morph_name", ""), "offsets": item_offsets,
                                  "alphaOnlyInvariant": invariant, "samples": samples}})
except Exception as exc:
    from tests.viewport.material_morph_e2e import exception_summary
    report["errors"].append({{"error": exception_summary(exc), "traceback": traceback.format_exc()}})
finally:
    if _settings_impl is not None:
        try:
            restore_settings(_settings_impl, _setting_optionvars, _setting_memory_values)
            report["settingsRestored"] = True
        except Exception as restore_exc:
            from tests.viewport.material_morph_e2e import exception_summary
            report["settingsRestored"] = False
            report["errors"].append({{"error": "settings restoration failed: " + exception_summary(restore_exc),
                                     "traceback": traceback.format_exc()}})
    with (OUT / "material-morph-report.json").open("w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, default=str)
    log({COMPLETION_MARKER!r})
'''


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = _PROJECT_ROOT
    model = _resolve(args.model, root)
    if not model.is_file():
        raise FileNotFoundError(f"PMX model not found: {model}")
    out = _resolve(args.out, root)
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "material-morph-e2e.log"
    report_path = out / "material-morph-report.json"
    maya_commandport.remove_stale_logs([log_path, report_path])
    morphs = args.morph or list(DEFAULT_MORPHS)
    proc: subprocess.Popen | None = None
    launched = not args.attach_existing
    try:
        if launched:
            proc = maya_commandport.launch_maya(
                version=args.maya,
                project_root=root,
                output_dir=out,
                port=args.port,
                launch_mode=args.launch_mode,
                env_overrides={"MAYA_VP2_DEVICE_OVERRIDE": BACKENDS[args.backend]["device"]},
            )
        maya_commandport.wait_for_port(args.port, args.timeout, proc)
        payload = {
            "project_root": str(root), "model": str(model), "out": str(out), "log": str(log_path),
            "maya": args.maya, "backend": args.backend, "plugin": BACKENDS[args.backend]["plugin"],
            "width": args.width, "height": args.height, "morphs": morphs,
            "attach_existing": args.attach_existing,
        }
        maya_commandport.send_python(args.port, _maya_code(payload), label="<material-morph-e2e>")
        if not maya_commandport.tail_until_marker(log_path, COMPLETION_MARKER, args.timeout):
            raise TimeoutError(f"completion marker not found in {log_path}")
    finally:
        if launched and not args.leave_open:
            maya_commandport.quit_maya(args.port)
        maya_commandport.close_process_logs(proc)
        if args.cleanup_launch_files:
            for path in (out / f"commandport_{args.port}.mel", out / f"launch_maya_{args.maya}_{args.port}.bat"):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
    if not report_path.is_file():
        raise RuntimeError(f"Maya report missing: {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("errors"):
        raise RuntimeError(f"material morph E2E failed: {report['errors'][0]['error']}")
    if len(report.get("results", [])) != len(morphs):
        raise RuntimeError("not all requested material morphs were captured")
    LOGGER.info("Material morph report: %s", report_path)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(main())
