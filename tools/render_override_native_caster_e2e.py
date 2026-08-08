"""Exercise the opt-in native caster override in a live Maya viewport.

This probe intentionally uses the settings-backed UI import route, then toggles
``mmdNativeCaster`` through ``MRenderer``.  It records the native witness while
the override is active, captures the ordinary viewport before/after disabling
it, and verifies that disabling retains the private targets while borrowed body
shaders are alive.  Scene reset retires those owners before unload, allowing
the override destructor to release the targets safely.
The R32F target contains the rasterized caster clip depth.  The deterministic
depth-bias A/B/A control checks that bias changes only depth statistics, not
the pixel footprint.  When ``--control-model`` is supplied, structured render
item diagnostics are matched exactly against the caster shader's real draw
callback batch; the fixture must include caster-on, caster-off, transparent,
and outline items.  This remains a caster capability witness, not a shadow-
composition, alpha-cutout sampling, or GoldenOracle parity claim.  The
frame-local matrix authority (one tagged ``mmd_light`` plus selected shape
bounds) is exercised directly, including light rotation and missing-authority
fail-closed cases.  ``--expect-receiver-probe-static`` is an opt-in negative
receiver-filter mode for fixtures whose visible body has ``selfShadow`` off:
the receiver shader/target lifetime gates remain required while the body
probe's A/B/A images must be byte-identical.

The hard-shadow witness is a separate OFF/ON/OFF pass on the active override.
It validates compare request/effective state, same-frame receiver target
assignment, and a visible body-only change.  The ON image is also required to
contain both flat lit (green-dominant) and occluded (blue-dominant) states
when the fixture is expected to exercise a shadow receiver.  This remains a
native capability witness, not a full MMD shadow-parity claim.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.viewport.maya_e2e_harness import run_maya_e2e  # noqa: E402
from tools.render_override_vp2_ownership_e2e import (  # noqa: E402
    _capture_view,
    _configure_camera,
    _configure_oracle_color_environment,
    _read_material_binding_diagnostics,
    _require_requested_plugin,
)


COMPLETION_MARKER = "//-- RENDER OVERRIDE NATIVE CASTER FINISHED --//"
DEFAULT_PORT = 7738
DEFAULT_TIMEOUT = 240.0
LOGGER = logging.getLogger(__name__)
DEPTH_BIAS_BASELINE = 0.35
DEPTH_BIAS_CONTROL = 0.55
DEPTH_BIAS_TOLERANCE = 1.0e-5
HARD_SHADOW_BIAS = 0.001
HARD_SHADOW_BIAS_TOLERANCE = 1.0e-7
DEFAULT_CAMERA: Dict[str, Any] = {
    "position": [0.04, 0.58, 3.4],
    "target": [0.04, 0.58, 0.0],
    "fov": 28.0,
    "near": 0.1,
    "far": 20.0,
}


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _query_witness(cmds: Any, log: Any) -> Dict[str, Any]:
    raw = str(cmds.mmdNativeCasterWitness())
    log(f"native caster witness: {raw}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"native caster witness is not JSON: {raw!r}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"native caster witness is not an object: {value!r}")
    return value


def _set_depth_bias(cmds: Any, log: Any, value: float) -> Dict[str, Any]:
    """Set the native caster clip-Z bias for the next setup/frame."""
    raw = str(cmds.mmdNativeCasterWitness(depthBias=float(value)))
    log(f"native caster depth bias={value}: {raw}")
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"depth-bias command returned non-JSON: {raw!r}") from exc
    if not isinstance(result, dict):
        raise RuntimeError(f"depth-bias command returned non-object: {result!r}")
    return result


def _set_receiver_probe(cmds: Any, log: Any, enabled: bool) -> Dict[str, Any]:
    """Toggle the default-off same-frame body receiver diagnostic probe."""
    raw = str(cmds.mmdNativeCasterWitness(receiverProbe=bool(enabled)))
    log(f"native caster receiverProbe={enabled}: {raw}")
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"receiver-probe command returned non-JSON: {raw!r}") from exc
    if not isinstance(result, dict):
        raise RuntimeError(f"receiver-probe command returned non-object: {result!r}")
    return result


def _set_hard_shadow(
    cmds: Any, log: Any, compare: bool, bias: float = HARD_SHADOW_BIAS
) -> Dict[str, Any]:
    """Set native hard-shadow compare state and receiver bias."""
    raw = str(
        cmds.mmdNativeCasterWitness(
            hardShadowCompare=bool(compare), hardShadowBias=float(bias)
        )
    )
    log(
        "native caster hardShadowCompare=%s hardShadowBias=%s: %s"
        % (bool(compare), float(bias), raw)
    )
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"hard-shadow command returned non-JSON: {raw!r}") from exc
    if not isinstance(result, dict):
        raise RuntimeError(f"hard-shadow command returned non-object: {result!r}")
    return result


def _hard_shadow_witness_passes(
    witness: Dict[str, Any],
    expected_compare: bool,
    expected_bias: float = HARD_SHADOW_BIAS,
) -> bool:
    """Validate hard-shadow state and same-frame receiver target ownership."""
    try:
        actual_bias = float(witness.get("hardShadowBias"))
    except (TypeError, ValueError):
        return False
    registered = int(witness.get("receiverShaderRegistered", 0))
    assignment_success = int(witness.get("receiverAssignmentSuccess", 0))
    return (
        bool(witness.get("frameComplete"))
        and bool(witness.get("hardShadowRequested")) is bool(expected_compare)
        and bool(witness.get("hardShadowFrameEffective")) is bool(expected_compare)
        # MRenderOverride::cleanup runs before the command query and must
        # disable the live shader flag while retaining frame evidence above.
        and not bool(witness.get("hardShadowEffective"))
        and abs(actual_bias - expected_bias) <= HARD_SHADOW_BIAS_TOLERANCE
        and bool(witness.get("hardShadowBindSuccess"))
        and bool(witness.get("matrixHash"))
        and witness.get("matrixHash") == witness.get("casterMatrixHash")
        and witness.get("matrixHash") == witness.get("receiverMatrixHash")
        and registered > 0
        and assignment_success >= registered
        and int(witness.get("receiverAssignmentFailure", 1)) == 0
        and bool(witness.get("receiverTargetResourceHandleNonNull"))
        and bool(witness.get("receiverTargetSameFrame"))
        and bool(witness.get("receiverTargetsRetained"))
        and not bool(witness.get("released"))
    )


def _hard_shadow_body_states(
    buffer: bytes,
    width: int,
    height: int,
    body_mask: bytes,
) -> Dict[str, Any]:
    """Count lit/occluded hard-shadow states inside the visible body mask.

    Classify the RGBA8 readback by dominant green (lit) versus blue (occluded)
    channel and ignore antialiased edge pixels.
    """
    expected = width * height * 4
    if len(buffer) < expected or len(body_mask) < width * height:
        return {"pass": False, "reason": "hard-shadow state buffers are truncated"}

    lit = 0
    occluded = 0
    body_pixels = 0
    for index in range(width * height):
        if not body_mask[index]:
            continue
        body_pixels += 1
        offset = index * 4
        red, green, blue, _ = buffer[offset : offset + 4]
        if green > red and green >= blue:
            lit += 1
        elif blue > red and blue > green:
            occluded += 1
    minimum = max(8, int(body_pixels * 0.005))
    return {
        "pass": lit >= minimum and occluded >= minimum,
        "source": "image",
        "bodyPixels": body_pixels,
        "minimumStatePixels": minimum,
        "litPixels": lit,
        "occludedPixels": occluded,
    }


def _viewport_probe_aba_passes(
    baseline: bytes,
    control: bytes,
    restored: bytes,
    width: int,
    height: int,
    body_mask: bytes,
    *,
    expect_static: bool = False,
) -> Dict[str, Any]:
    """Compare probe A/B/A pixels and keep changes inside visible body data.

    ``body_mask`` comes from a same-camera visible-vs-hidden shape capture, so
    a non-black viewport background or HUD cannot be mistaken for body pixels.
    In the optional static mode, the fixture is expected to have no visible
    receiver-enabled body; both A/B and A/restored must therefore be exact
    pixel matches rather than a waived A/B gate.
    """
    expected = width * height * 4
    if any(len(buffer) < expected for buffer in (baseline, control, restored)):
        return {"pass": False, "reason": "probe viewport buffers are truncated"}
    if len(body_mask) < width * height:
        return {"pass": False, "reason": "shape mask is truncated"}

    def compare(left: bytes, right: bytes) -> Dict[str, Any]:
        differing = 0
        body_differing = 0
        outside_body_differing = 0
        min_x, min_y = width, height
        max_x = max_y = -1
        for index in range(width * height):
            offset = index * 4
            if left[offset : offset + 4] == right[offset : offset + 4]:
                continue
            differing += 1
            if body_mask[index]:
                body_differing += 1
            else:
                outside_body_differing += 1
            x = index % width
            y = height - 1 - (index // width)
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)
        return {
            "differingPixels": differing,
            "bodyDifferingPixels": body_differing,
            "outsideBodyDifferingPixels": outside_body_differing,
            "bbox": None if max_x < 0 else [min_x, min_y, max_x, max_y],
        }

    a_to_b = compare(baseline, control)
    a_to_a = compare(baseline, restored)
    if expect_static:
        passed = (
            a_to_b["differingPixels"] == 0
            and a_to_a["differingPixels"] == 0
        )
    else:
        passed = (
            a_to_b["differingPixels"] > 0
            and a_to_b["bodyDifferingPixels"] > 0
            and a_to_b["outsideBodyDifferingPixels"] == 0
            and a_to_a["differingPixels"] == 0
        )
    return {
        "pass": passed,
        "expectStatic": bool(expect_static),
        "width": width,
        "height": height,
        "bodyMaskPixels": sum(body_mask),
        "aToB": a_to_b,
        "aToRestored": a_to_a,
    }


def _receiver_eligible_non_outline_count(
    render_item_witnesses: Dict[str, Any],
) -> int:
    """Count structured non-outline body items with the receiver flag set."""
    return sum(
        1
        for witness in render_item_witnesses.values()
        if isinstance(witness, dict)
        for item in witness.get("items", [])
        if isinstance(item, dict)
        and bool(item.get("selfShadow"))
        and not bool(item.get("outline"))
    )


def _visible_shape_mask(
    visible: bytes, hidden: bytes, width: int, height: int
) -> Dict[str, Any]:
    """Build a small dilated mask from visible-vs-hidden same-camera readback."""
    expected = width * height * 4
    if len(visible) < expected or len(hidden) < expected:
        return {"mask": bytes(width * height), "pixels": 0, "pass": False}
    base = bytearray(width * height)
    for index in range(width * height):
        offset = index * 4
        if visible[offset : offset + 4] != hidden[offset : offset + 4]:
            base[index] = 1
    # Include antialiased edge pixels that can move by one sample when the
    # receiver probe rewrites the BODY color.
    dilated = bytearray(base)
    for index, value in enumerate(base):
        if not value:
            continue
        x = index % width
        y = index // width
        for dy in (-2, -1, 0, 1, 2):
            for dx in (-2, -1, 0, 1, 2):
                nx = x + dx
                ny = y + dy
                if 0 <= nx < width and 0 <= ny < height:
                    dilated[ny * width + nx] = 1
    return {
        "mask": bytes(dilated),
        "pixels": sum(dilated),
        "rawPixels": sum(base),
        "pass": any(base),
    }


def _depth_bias_aba_passes(
    baseline: Dict[str, Any], control: Dict[str, Any], restored: Dict[str, Any]
) -> bool:
    """Validate finite [0,1] depth and an invariant A/B/A raster footprint."""
    witnesses = (baseline, control, restored)
    expected_biases = (
        DEPTH_BIAS_BASELINE,
        DEPTH_BIAS_CONTROL,
        DEPTH_BIAS_BASELINE,
    )
    for witness, expected_bias in zip(witnesses, expected_biases):
        try:
            actual_bias = float(witness["depthBias"])
        except (KeyError, TypeError, ValueError):
            return False
        if abs(actual_bias - expected_bias) > DEPTH_BIAS_TOLERANCE:
            return False
    if any(
        not witness.get("writtenDepthFinite")
        or not witness.get("writtenDepthInRange")
        or int(witness.get("writtenOutOfRangeSamples", 1)) != 0
        or int(witness.get("writtenSamples", 0)) <= 0
        for witness in witnesses
    ):
        return False
    counts = [int(witness["writtenSamples"]) for witness in witnesses]
    footprints = [str(witness["writtenFootprintHash"]) for witness in witnesses]
    if counts[0] != counts[1] or counts[0] != counts[2]:
        return False
    if footprints[0] != footprints[1] or footprints[0] != footprints[2]:
        return False
    baseline_mean = float(baseline["writtenMean"])
    control_mean = float(control["writtenMean"])
    restored_mean = float(restored["writtenMean"])
    control_shift = control_mean - baseline_mean
    if abs(control_shift - (DEPTH_BIAS_CONTROL - DEPTH_BIAS_BASELINE)) > (
        DEPTH_BIAS_TOLERANCE
    ):
        return False
    if abs(restored_mean - baseline_mean) > DEPTH_BIAS_TOLERANCE:
        return False
    for key in ("writtenMin", "writtenMax"):
        if (
            abs(
                float(control[key])
                - float(baseline[key])
                - (DEPTH_BIAS_CONTROL - DEPTH_BIAS_BASELINE)
            )
            > DEPTH_BIAS_TOLERANCE
        ):
            return False
        if abs(float(restored[key]) - float(baseline[key])) > DEPTH_BIAS_TOLERANCE:
            return False
    return True


def _wait_for_active_witness(cmds: Any, log: Any) -> Dict[str, Any]:
    witness: Dict[str, Any] = {}
    for attempt in range(30):
        cmds.refresh(force=True)
        time.sleep(0.3)
        witness = _query_witness(cmds, log)
        if witness.get("setup") or (
            witness.get("drawAttempted") and witness.get("frameComplete")
        ):
            return witness
        log(f"native caster setup pending (attempt {attempt + 1})")
    return witness


def _set_panel_override(mel: Any, panel: str, override_name: str) -> None:
    """Select a registered VP2 override for one model panel.

    ``MRenderer.setRenderOverrideName`` is batch-only in Maya 2024; viewport
    panels use the modelEditor ``-rom`` flag instead.
    """
    safe_panel = panel.replace('"', "")
    safe_name = override_name.replace('"', "")
    mel.eval(
        f'modelEditor -edit -rnm "vp2Renderer" -rom "{safe_name}" "{safe_panel}"'
    )


def _capture_active_viewport_buffer(
    cmds: Any, output_path: Path, panel: str, require_nonuniform: bool = True
) -> tuple[Path, Dict[str, Any], bytes]:
    """Read the active VP2 color buffer while the caster override is selected."""
    import maya.api.OpenMaya as om
    import maya.api.OpenMayaUI as omui

    cmds.setFocus(panel)
    cmds.refresh(force=True)
    time.sleep(0.3)
    view = omui.M3dView.active3dView()
    image = om.MImage()
    view.readColorBuffer(image, True)
    width, height = [int(value) for value in image.getSize()]
    if width <= 0 or height <= 0:
        raise RuntimeError(f"active viewport readback has invalid size {width}x{height}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Maya can lazily materialize the readback storage; writing once before
    # asking for the raw pointer makes the subsequent CPU stats observe the
    # same pixels that are persisted in the PNG (rather than a uniform stale
    # clear buffer on the first DX11 refresh).
    image.writeToFile(str(output_path), "png")
    expected = width * height * 4
    pixels = image.pixels()
    if isinstance(pixels, int):
        if pixels <= 0:
            raise RuntimeError("active viewport readback returned a null pixel pointer")
        buffer = ctypes.string_at(pixels, expected)
    else:
        view = memoryview(pixels).cast("B")
        if view.nbytes < expected:
            raise RuntimeError(
                f"active viewport readback buffer too short: {view.nbytes} < {expected}"
            )
        buffer = view[:expected].tobytes()
    # Sample the RGBA8 readback to prove that the active present contains a
    # non-empty, non-uniform scene rather than a clear/private target.
    # A linear stride can accidentally skip a compact model when its period
    # aligns with the viewport dimensions.  Use a fixed 64x64 stratified grid
    # so the primary present gate samples the center/body as well as corners.
    grid_width = min(width, 64)
    grid_height = min(height, 64)
    sample_indices = [
        (row * height // grid_height) * width + (column * width // grid_width)
        for row in range(grid_height)
        for column in range(grid_width)
    ]
    non_black = 0
    distinct = set()
    samples = 0
    for pixel_index in sample_indices:
        offset = pixel_index * 4
        red, green, blue, _ = buffer[offset : offset + 4]
        samples += 1
        if max(red, green, blue) > 8:
            non_black += 1
        if len(distinct) < 32:
            distinct.add((red, green, blue))
    if samples <= 0 or non_black == 0 or (require_nonuniform and len(distinct) < 2):
        raise RuntimeError(
            "active viewport readback is empty or uniform "
            f"(samples={samples}, nonBlack={non_black}, distinct={len(distinct)})"
        )
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise RuntimeError("active viewport readback did not create a PNG")
    stats = {
        "width": width,
        "height": height,
        "samples": samples,
        "nonBlackSamples": non_black,
        "distinctSamples": len(distinct),
        "pixelSha256": hashlib.sha256(buffer).hexdigest(),
        "scenePixelSha256": hashlib.sha256(
            b"".join(
                buffer[row * width * 4 : (row + 1) * width * 4]
                # MImage rows are bottom-origin while the written PNG is
                # top-origin; display-space y<90% therefore maps to raw rows
                # y>=10%.
                for row in range(max(1, int(height * 0.1)), height)
            )
        ).hexdigest(),
        "sceneRegionDisplayY": [0, int(height * 0.9)],
    }
    return output_path, stats, buffer


def run_probe(
    log_path: str,
    report_path: str,
    out_dir: str,
    model_path: str,
    plugin_path: str,
    control_model_path: Optional[str] = None,
    width: int = 640,
    height: int = 480,
    camera_config: Optional[Dict[str, Any]] = None,
    frame: int = 0,
    expect_receiver_probe_static: bool = False,
) -> None:
    """Run the Maya-side caster probe and always write a structured report."""
    import maya.cmds as cmds
    import maya.mel as mel
    import maya.api.OpenMayaRender as omr

    log_file = Path(log_path)
    report_file = Path(report_path)
    output_dir = Path(out_dir)
    report: dict[str, Any] = {
        "status": "fail",
        "claim": "native-caster-depth-witness-only",
        "model": str(model_path),
        "controlModel": str(control_model_path or ""),
        "expectReceiverProbeStatic": bool(expect_receiver_probe_static),
        "plugin": str(plugin_path),
        "captures": {},
        "errors": [],
    }

    def log(message: object) -> None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as stream:
            stream.write(f"{message}\n")
        try:
            print(message)
        except Exception:
            pass

    def run_depth_bias_frame(value: float) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """Render one visible caster frame while retaining private targets."""
        _set_depth_bias(cmds, log, value)
        for current in panels:
            _set_panel_override(mel, current, "mmdNativeCaster")
        active = _wait_for_active_witness(cmds, log)
        for current in panels:
            _set_panel_override(mel, current, "")
        cmds.refresh(force=True)
        time.sleep(0.5)
        return active, _query_witness(cmds, log)

    def wait_for_receiver_probe_witness() -> Dict[str, Any]:
        """Wait until caster draw and body target assignment share a frame."""
        witness: Dict[str, Any] = {}
        for attempt in range(60):
            cmds.refresh(force=True)
            time.sleep(0.3)
            witness = _query_witness(cmds, log)
            if (
                witness.get("frameComplete")
                and int(witness.get("receiverShaderRegistered", 0)) > 0
                and int(witness.get("receiverAssignmentSuccess", 0)) > 0
                and int(witness.get("receiverAssignmentSuccess", 0))
                >= int(witness.get("receiverShaderRegistered", 0))
                and int(witness.get("receiverAssignmentFailure", 1)) == 0
                and witness.get("receiverTargetSameFrame")
                and witness.get("receiverTargetsRetained")
                and not witness.get("released")
            ):
                return witness
            log(f"native receiver probe pending (attempt {attempt + 1})")
        return witness

    def wait_for_hard_shadow_witness(expected_compare: bool) -> Dict[str, Any]:
        """Wait for a live frame publishing the requested hard-shadow state."""
        witness: Dict[str, Any] = {}
        for attempt in range(60):
            cmds.refresh(force=True)
            time.sleep(0.3)
            witness = _query_witness(cmds, log)
            if (
                bool(witness.get("hardShadowRequested")) is bool(expected_compare)
                and bool(witness.get("hardShadowFrameEffective"))
                is bool(expected_compare)
                and not bool(witness.get("hardShadowEffective"))
                and witness.get("frameComplete")
            ):
                return witness
            log(f"native hard-shadow state pending (attempt {attempt + 1})")
        return witness

    def run_receiver_probe_frame(
        value: float, label: str
    ) -> tuple[Dict[str, Any], Dict[str, Any], bytes, Dict[str, Any]]:
        """Render one same-frame depth probe image with retained target."""
        _set_depth_bias(cmds, log, value)
        _set_receiver_probe(cmds, log, True)
        for current in panels:
            _set_panel_override(mel, current, "mmdNativeCaster")
        active = wait_for_receiver_probe_witness()
        path, stats, buffer = _capture_active_viewport_buffer(
            cmds,
            output_dir / f"native_receiver_probe_{label}.png",
            panel,
        )
        report["captures"][f"receiverProbe{label.title()}"] = str(path)
        report["captures"][f"receiverProbe{label.title()}Stats"] = stats
        for current in panels:
            _set_panel_override(mel, current, "")
        cmds.refresh(force=True)
        time.sleep(0.5)
        disabled = _query_witness(cmds, log)
        return active, disabled, buffer, stats

    def run_hard_shadow_frame(
        compare: bool, label: str
    ) -> tuple[Dict[str, Any], bytes, Dict[str, Any]]:
        """Capture one hard-shadow state while the active override stays live."""
        _set_hard_shadow(cmds, log, compare, HARD_SHADOW_BIAS)
        active = wait_for_hard_shadow_witness(compare)
        path, stats, buffer = _capture_active_viewport_buffer(
            cmds,
            output_dir / f"native_hard_shadow_{label}.png",
            panel,
        )
        report["captures"][f"hardShadow{label.title()}"] = str(path)
        report["captures"][f"hardShadow{label.title()}Stats"] = stats
        return active, buffer, stats

    try:
        log("=== native caster probe begin ===")
        cmds.file(new=True, force=True)
        loaded_plugin = _require_requested_plugin(cmds, plugin_path, log)
        report["loadedPluginPath"] = str(loaded_plugin)
        report["vp2Device"] = str(cmds.ogs(deviceInformation=True))
        report["rendererOverrideCount"] = int(omr.MRenderer.renderOverrideCount())
        try:
            report["rendererFindOverride"] = bool(
                omr.MRenderer.findRenderOverride("mmdNativeCaster")
            )
        except Exception as exc:
            report["rendererFindOverride"] = f"ERR: {exc}"

        from mmd_tools.converters.light_converter import (
            create_mmd_light_controller,
            set_mmd_light_direction,
        )
        from mmd_tools.io.mmd_importer import import_mmd_file
        from mmd_tools.services.settings_service import SettingsService

        def import_native_fixture(path: str, namespace: str) -> tuple[str, list[str]]:
            ui_options = SettingsService().build_pmx_import_options(
                custom_namespace=namespace
            )
            ui_options.update(
                import_physics=False,
                use_cpp_fast_load=True,
                use_cpp_vp2_ownership=True,
                cpp_fast_load_mesh_only=True,
            )
            root_result = import_mmd_file(
                str(Path(path).resolve()), options=ui_options
            )
            if not root_result:
                raise RuntimeError(f"UI import returned no root: {path}")
            root = str(root_result)
            native_shapes = [
                str(item)
                for item in (
                    cmds.listRelatives(
                        root, allDescendents=True, fullPath=True
                    )
                    or []
                )
                if cmds.nodeType(item) == "mmdRenderShape"
            ]
            return root, native_shapes

        root_name, shapes = import_native_fixture(
            model_path, "native_caster_e2e"
        )
        control_root = None
        control_shapes: list[str] = []
        if control_model_path:
            control_root, control_shapes = import_native_fixture(
                control_model_path, "native_caster_controls_e2e"
            )
            shapes.extend(control_shapes)
        if not shapes:
            raise RuntimeError(f"UI import did not create mmdRenderShape: {root_name}")
        light_controller = create_mmd_light_controller()
        if not light_controller:
            raise RuntimeError("failed to create tagged mmd_light authority")
        light_direction_a = (-0.5, -1.0, -1.0)
        light_direction_b = (0.65, -0.7, -1.0)
        set_mmd_light_direction(light_direction_a)
        report["root"] = root_name
        report["controlRoot"] = control_root
        report["shapes"] = shapes
        report["lightController"] = str(light_controller)
        log(f"UI import root={root_name} mmdRenderShape count={len(shapes)}")

        panels = [str(panel) for panel in (cmds.getPanel(type="modelPanel") or [])]
        if not panels:
            raise RuntimeError("Maya GUI has no modelPanel")
        camera = _configure_camera(cmds, panels, camera_config or DEFAULT_CAMERA)
        report["camera"] = camera
        parity_view = _configure_oracle_color_environment(cmds)
        report["parityView"] = parity_view
        if parity_view["errors"]:
            raise RuntimeError("color-management setup failed: " + "; ".join(parity_view["errors"]))
        for panel in panels:
            cmds.modelEditor(
                panel,
                edit=True,
                rendererName="vp2Renderer",
                displayAppearance="smoothShaded",
                displayTextures=True,
                wireframeOnShaded=False,
                grid=False,
                cameras=False,
                lights=False,
                locators=False,
                joints=False,
                ikHandles=False,
                deformers=False,
                dynamics=False,
                nurbsCurves=False,
                useDefaultMaterial=False,
                selectionHiliteDisplay=False,
            )
        panel = "modelPanel4" if "modelPanel4" in panels else panels[0]
        cmds.setFocus(panel)
        report["panels"] = {
            current: {
                "rendererName": cmds.modelEditor(current, query=True, rendererName=True),
                "headsUpDisplay": bool(
                    cmds.modelEditor(current, query=True, headsUpDisplay=True)
                ),
            }
            for current in panels
        }
        available_overrides: Dict[str, Any] = {}
        for current in panels:
            try:
                available_overrides[current] = cmds.modelEditor(
                    current, query=True, rol=True
                )
            except Exception as exc:
                available_overrides[current] = f"ERR: {exc}"
        report["availableOverrides"] = available_overrides

        # Rebind the panel override after late plug-in registration.  Maya can
        # retain the modelEditor override token from panel creation even when
        # ``rol`` has been refreshed; an explicit clear/set pair forces the
        # live panel to instantiate the newly registered operation list.
        for current in panels:
            _set_panel_override(mel, current, "")
        cmds.refresh(force=True)
        _set_depth_bias(cmds, log, DEPTH_BIAS_BASELINE)
        for current in panels:
            _set_panel_override(mel, current, "mmdNativeCaster")
        report["overrideEnableReturned"] = True
        report["activeOverride"] = str(omr.MRenderer.activeRenderOverride())
        active_witness = _wait_for_active_witness(cmds, log)
        report["activeWitness"] = active_witness
        if not active_witness.get("setup") and not active_witness.get("frameComplete"):
            raise RuntimeError("native caster setup did not become active")
        if not active_witness.get("selectedCount"):
            raise RuntimeError("native caster selected no mmdRenderShape nodes")
        # The caster callback proves VP2 has already materialized every render
        # item, avoiding a startup race where an early command result can be
        # empty before the first scene operation.
        render_item_witnesses = {
            shape: _read_material_binding_diagnostics(cmds, shape, log)
            for shape in shapes
        }
        report["renderItemWitnesses"] = render_item_witnesses
        active_viewport_buffer: Optional[bytes] = None
        active_viewport_stats: Dict[str, Any] = {}
        try:
            report["captures"]["casterEnabled"] = str(
                _capture_view(
                    cmds,
                    output_dir / "native_caster_enabled.png",
                    panel,
                    width,
                    height,
                    frame,
                )
            )
        except Exception as exc:
            # A private R32F caster target is not a presentable viewport color
            # target on every Maya playblast path.  Keep the failure explicit;
            # target occupancy and the post-disable ordinary present are still
            # checked below.
            report["captures"]["casterEnabledError"] = str(exc)
        try:
            viewport_path, viewport_stats, active_viewport_buffer = (
                _capture_active_viewport_buffer(
                    cmds,
                    output_dir / "native_caster_enabled_viewport.png",
                    panel,
                )
            )
            active_viewport_stats = viewport_stats
            report["captures"]["casterEnabledViewport"] = str(viewport_path)
            report["captures"]["casterEnabledViewportStats"] = viewport_stats
        except Exception as exc:
            report["captures"]["casterEnabledViewportError"] = str(exc)
        report["activePanel"] = {
            "panel": panel,
            "rendererName": cmds.modelEditor(
                panel, query=True, rendererName=True
            ),
            "headsUpDisplay": bool(
                cmds.modelEditor(panel, query=True, headsUpDisplay=True)
            ),
        }

        for current in panels:
            _set_panel_override(mel, current, "")
        report["overrideDisableReturned"] = True
        report["activeOverrideAfterDisable"] = str(omr.MRenderer.activeRenderOverride())
        cmds.refresh(force=True)
        time.sleep(0.5)
        disabled_witness = _query_witness(cmds, log)
        report["disabledWitness"] = disabled_witness
        report["captures"]["casterDisabled"] = str(
            _capture_view(
                cmds,
                output_dir / "native_caster_disabled.png",
                panel,
                width,
                height,
                frame,
            )
        )
        try:
            disabled_viewport_path, disabled_viewport_stats, disabled_viewport_buffer = (
                _capture_active_viewport_buffer(
                    cmds, output_dir / "native_caster_disabled_viewport.png", panel
                )
            )
            report["captures"]["casterDisabledViewport"] = str(
                disabled_viewport_path
            )
            report["captures"]["casterDisabledViewportStats"] = (
                disabled_viewport_stats
            )
        except Exception as exc:
            report["captures"]["casterDisabledViewportError"] = str(exc)
            disabled_viewport_buffer = None
        if active_viewport_buffer is not None and disabled_viewport_buffer is not None:
            active_width = int(active_viewport_stats.get("width", 0))
            active_height = int(active_viewport_stats.get("height", 0))
            disabled_stats = report["captures"].get(
                "casterDisabledViewportStats", {}
            )
            disabled_width = int(disabled_stats.get("width", 0))
            disabled_height = int(disabled_stats.get("height", 0))
            if (active_width, active_height) == (disabled_width, disabled_height):
                differing_pixels = 0
                min_x, min_y = active_width, active_height
                max_x = max_y = -1
                for pixel_index in range(active_width * active_height):
                    offset = pixel_index * 4
                    if active_viewport_buffer[offset : offset + 4] == (
                        disabled_viewport_buffer[offset : offset + 4]
                    ):
                        continue
                    differing_pixels += 1
                    x = pixel_index % active_width
                    y = pixel_index // active_width
                    display_y = active_height - 1 - y
                    min_x = min(min_x, x)
                    min_y = min(min_y, display_y)
                    max_x = max(max_x, x)
                    max_y = max(max_y, display_y)
                report["captures"]["standardPresentDiff"] = {
                    "width": active_width,
                    "height": active_height,
                    "differingPixels": differing_pixels,
                    "ratio": differing_pixels / float(active_width * active_height),
                    "bbox": None
                    if max_x < 0
                    else [min_x, min_y, max_x, max_y],
                }
        if disabled_witness.get("setup"):
            raise RuntimeError("native caster remained setup after disable")
        if not disabled_witness.get("receiverTargetsRetained") or disabled_witness.get(
            "released"
        ):
            raise RuntimeError(
                "disabled caster witness did not retain targets while body shaders live"
            )

        # Deterministic depth-only A/B/A: changing clip Z must shift the
        # written depth statistics by approximately +0.20 while preserving
        # the exact raster footprint.  The final A frame also proves the
        # baseline can be restored after the control.
        control_witness, control_disabled_witness = run_depth_bias_frame(
            DEPTH_BIAS_CONTROL
        )
        restored_witness, restored_disabled_witness = run_depth_bias_frame(
            DEPTH_BIAS_BASELINE
        )
        report["depthBiasAba"] = {
            "baseline": active_witness,
            "control": control_witness,
            "restored": restored_witness,
            "controlDisabled": control_disabled_witness,
            "restoredDisabled": restored_disabled_witness,
        }

        # Capture a same-camera visible-vs-hidden ordinary viewport before
        # enabling the receiver probe.  The resulting mask is the only region
        # allowed to change in the A/B comparison; background and HUD pixels
        # are therefore covered by an actual shape negative control.
        _set_receiver_probe(cmds, log, False)
        visible_mask_path, visible_mask_stats, visible_mask_buffer = (
            _capture_active_viewport_buffer(
                cmds,
                output_dir / "native_receiver_mask_visible.png",
                panel,
                require_nonuniform=False,
            )
        )
        for shape in shapes:
            cmds.setAttr(f"{shape}.visibility", False)
        cmds.refresh(force=True)
        time.sleep(0.5)
        hidden_mask_path, hidden_mask_stats, hidden_mask_buffer = (
            _capture_active_viewport_buffer(
                cmds,
                output_dir / "native_receiver_mask_hidden.png",
                panel,
                require_nonuniform=False,
            )
        )
        for shape in shapes:
            cmds.setAttr(f"{shape}.visibility", True)
        cmds.refresh(force=True)
        shape_mask = _visible_shape_mask(
            visible_mask_buffer,
            hidden_mask_buffer,
            int(visible_mask_stats.get("width", 0)),
            int(visible_mask_stats.get("height", 0)),
        )
        report["captures"]["receiverMaskVisible"] = str(visible_mask_path)
        report["captures"]["receiverMaskVisibleStats"] = visible_mask_stats
        report["captures"]["receiverMaskHidden"] = str(hidden_mask_path)
        report["captures"]["receiverMaskHiddenStats"] = hidden_mask_stats
        report["captures"]["receiverMask"] = {
            "pixels": shape_mask["pixels"],
            "rawPixels": shape_mask.get("rawPixels", 0),
            "pass": shape_mask["pass"],
        }
        if not shape_mask["pass"]:
            raise RuntimeError("visible-vs-hidden receiver shape mask is empty")

        # Same-frame receiver binding A/B/A: the borrowed BODY shader samples
        # the exact R32F target written by the preceding caster operation.
        # Capture the presented viewport for each probe value so a parameter
        # assignment without a real target read cannot pass this gate.
        (
            receiver_baseline,
            receiver_baseline_disabled,
            receiver_baseline_buffer,
            receiver_baseline_stats,
        ) = run_receiver_probe_frame(DEPTH_BIAS_BASELINE, "baseline")
        (
            receiver_control,
            receiver_control_disabled,
            receiver_control_buffer,
            receiver_control_stats,
        ) = run_receiver_probe_frame(DEPTH_BIAS_CONTROL, "control")
        (
            receiver_restored,
            receiver_restored_disabled,
            receiver_restored_buffer,
            receiver_restored_stats,
        ) = run_receiver_probe_frame(DEPTH_BIAS_BASELINE, "restored")
        _set_receiver_probe(cmds, log, False)
        probe_width = int(receiver_baseline_stats.get("width", 0))
        probe_height = int(receiver_baseline_stats.get("height", 0))
        receiver_probe_pixels = _viewport_probe_aba_passes(
            receiver_baseline_buffer,
            receiver_control_buffer,
            receiver_restored_buffer,
            probe_width,
            probe_height,
            shape_mask["mask"],
            expect_static=expect_receiver_probe_static,
        )
        report["receiverProbeAba"] = {
            "baseline": receiver_baseline,
            "control": receiver_control,
            "restored": receiver_restored,
            "baselineDisabled": receiver_baseline_disabled,
            "controlDisabled": receiver_control_disabled,
            "restoredDisabled": receiver_restored_disabled,
            "baselineStats": receiver_baseline_stats,
            "controlStats": receiver_control_stats,
            "restoredStats": receiver_restored_stats,
            "pixels": receiver_probe_pixels,
        }
        if not receiver_probe_pixels.get("pass"):
            if expect_receiver_probe_static:
                raise RuntimeError(
                    "receiver probe A/B/A changed pixels despite static expectation"
                )
            raise RuntimeError("receiver probe A/B/A did not change and restore body pixels")
        for witness in (
            receiver_baseline_disabled,
            receiver_control_disabled,
            receiver_restored_disabled,
        ):
            if witness.get("setup") or not witness.get("receiverTargetsRetained") or witness.get(
                "released"
            ):
                raise RuntimeError(
                    "receiver probe frame did not retain its caster target"
                )

        # Hard-shadow compare A/B/A stays on one active override instance.
        # This distinguishes a live receiver compare from a command-only
        # diagnostic toggle and keeps the existing target/lifetime gates in
        # force for every state.
        _set_hard_shadow(cmds, log, False, HARD_SHADOW_BIAS)
        for current in panels:
            _set_panel_override(mel, current, "mmdNativeCaster")
        hard_shadow_off, hard_shadow_off_buffer, hard_shadow_off_stats = (
            run_hard_shadow_frame(False, "off")
        )
        hard_shadow_on, hard_shadow_on_buffer, hard_shadow_on_stats = (
            run_hard_shadow_frame(True, "on")
        )
        hard_shadow_restored, hard_shadow_restored_buffer, hard_shadow_restored_stats = (
            run_hard_shadow_frame(False, "restored")
        )
        for current in panels:
            _set_panel_override(mel, current, "")
        cmds.refresh(force=True)
        time.sleep(0.5)
        hard_shadow_disabled = _query_witness(cmds, log)
        hard_width = int(hard_shadow_off_stats.get("width", 0))
        hard_height = int(hard_shadow_off_stats.get("height", 0))
        hard_shadow_pixels = _viewport_probe_aba_passes(
            hard_shadow_off_buffer,
            hard_shadow_on_buffer,
            hard_shadow_restored_buffer,
            hard_width,
            hard_height,
            shape_mask["mask"],
            expect_static=expect_receiver_probe_static,
        )
        hard_shadow_states = (
            {"pass": True, "source": "static-negative"}
            if expect_receiver_probe_static
            else _hard_shadow_body_states(
                hard_shadow_on_buffer,
                hard_width,
                hard_height,
                shape_mask["mask"],
            )
        )
        report["hardShadowAba"] = {
            "off": hard_shadow_off,
            "on": hard_shadow_on,
            "restored": hard_shadow_restored,
            "disabled": hard_shadow_disabled,
            "offStats": hard_shadow_off_stats,
            "onStats": hard_shadow_on_stats,
            "restoredStats": hard_shadow_restored_stats,
            "pixels": hard_shadow_pixels,
            "states": hard_shadow_states,
        }
        if not hard_shadow_pixels.get("pass"):
            if expect_receiver_probe_static:
                raise RuntimeError(
                    "hard-shadow OFF/ON/OFF changed pixels despite static expectation"
                )
            raise RuntimeError(
                "hard-shadow OFF/ON/OFF did not change and restore body pixels"
            )
        if not expect_receiver_probe_static and not hard_shadow_states.get("pass"):
            raise RuntimeError(
                "hard-shadow ON body capture lacks distinct lit/occluded states"
            )
        for compare, witness in (
            (False, hard_shadow_off),
            (True, hard_shadow_on),
            (False, hard_shadow_restored),
        ):
            if not _hard_shadow_witness_passes(witness, compare):
                raise RuntimeError(
                    "hard-shadow witness did not prove compare state, target assignment, "
                    f"or same-frame matrix (expected={compare})"
                )
        if hard_shadow_disabled.get("setup") or hard_shadow_disabled.get("released"):
            raise RuntimeError("hard-shadow disable did not retain live caster targets")

        # The caster matrix is frame-local to the tagged MMD light and shape
        # bounds, not to the viewport camera.  Rotate the light B/A and force
        # a fresh override setup to prove the matrix hash follows the light,
        # then returns exactly to the original authority.
        def run_light_frame(direction: tuple[float, float, float]) -> Dict[str, Any]:
            set_mmd_light_direction(direction)
            for current in panels:
                _set_panel_override(mel, current, "mmdNativeCaster")
            active = _wait_for_active_witness(cmds, log)
            for current in panels:
                _set_panel_override(mel, current, "")
            cmds.refresh(force=True)
            time.sleep(0.5)
            return active

        light_a_witness = _query_witness(cmds, log)
        light_b_witness = run_light_frame(light_direction_b)
        light_restored_witness = run_light_frame(light_direction_a)
        report["lightRotationAba"] = {
            "baseline": light_a_witness,
            "control": light_b_witness,
            "restored": light_restored_witness,
        }
        if (
            not light_a_witness.get("matrixHash")
            or light_a_witness.get("matrixHash")
            == light_b_witness.get("matrixHash")
            or light_a_witness.get("matrixHash")
            != light_restored_witness.get("matrixHash")
            or any(
                witness.get("matrixHash")
                != witness.get("casterMatrixHash")
                or witness.get("matrixHash")
                != witness.get("receiverMatrixHash")
                for witness in (
                    light_a_witness,
                    light_b_witness,
                    light_restored_witness,
                )
            )
            or not light_b_witness.get("occupied")
            or not light_b_witness.get("writtenDepthFinite")
            or not light_b_witness.get("writtenDepthInRange")
            or (
                light_a_witness.get("writtenFootprintHash")
                == light_b_witness.get("writtenFootprintHash")
                and light_a_witness.get("writtenMin")
                == light_b_witness.get("writtenMin")
                and light_a_witness.get("writtenMax")
                == light_b_witness.get("writtenMax")
                and light_a_witness.get("writtenMean")
                == light_b_witness.get("writtenMean")
            )
            or any(
                light_a_witness.get(key) != light_restored_witness.get(key)
                for key in (
                    "writtenFootprintHash",
                    "writtenSamples",
                    "writtenMin",
                    "writtenMax",
                    "writtenMean",
                )
            )
        ):
            raise RuntimeError("light rotation did not produce an A/B/A matrix witness")

        def depth_snapshot(witness: Dict[str, Any]) -> Dict[str, Any]:
            return {
                key: witness.get(key)
                for key in (
                    "matrixHash",
                    "writtenFootprintHash",
                    "writtenSamples",
                    "writtenMin",
                    "writtenMax",
                    "writtenMean",
                    "cornersInClip",
                    "occupied",
                )
            }

        # A camera-only move must not perturb a light-space caster matrix or
        # its private target rasterization.  Save and restore the transform so
        # subsequent DAG and teardown gates use the original camera.
        camera_node = str(camera.get("node", ""))
        camera_translation = cmds.xform(camera_node, query=True, worldSpace=True, translation=True)
        camera_baseline = light_restored_witness
        cmds.xform(
            camera_node,
            worldSpace=True,
            translation=(float(camera_translation[0]) + 0.35,
                         float(camera_translation[1]),
                         float(camera_translation[2])),
        )
        camera_moved_witness = run_light_frame(light_direction_a)
        cmds.xform(camera_node, worldSpace=True, translation=camera_translation)
        camera_restored_witness = run_light_frame(light_direction_a)
        report["cameraInvariant"] = {
            "baseline": depth_snapshot(camera_baseline),
            "moved": depth_snapshot(camera_moved_witness),
            "restored": depth_snapshot(camera_restored_witness),
        }
        if (
            depth_snapshot(camera_baseline) != depth_snapshot(camera_moved_witness)
            or depth_snapshot(camera_baseline) != depth_snapshot(camera_restored_witness)
        ):
            raise RuntimeError("camera-only move changed the caster matrix or target")

        # A DAG transform is part of the selected-bounds authority.  Translate
        # and non-uniformly scale the primary shape's parent, then restore it;
        # the fitted matrix must change while remaining finite/occupied and
        # return to the exact A hash/footprint afterwards.
        shape_parent = str(
            (cmds.listRelatives(shapes[0], parent=True, fullPath=True) or [""])[0]
        )
        if not shape_parent:
            raise RuntimeError("native caster shape has no transform parent")
        parent_translation = cmds.xform(
            shape_parent, query=True, worldSpace=True, translation=True
        )
        parent_scale = cmds.getAttr(f"{shape_parent}.scale")
        cmds.xform(
            shape_parent,
            worldSpace=True,
            translation=(float(parent_translation[0]) + 0.08,
                         float(parent_translation[1]),
                         float(parent_translation[2])),
        )
        cmds.setAttr(f"{shape_parent}.scale", 1.12, 0.87, 1.0, type="double3")
        dag_moved_witness = run_light_frame(light_direction_a)
        cmds.xform(shape_parent, worldSpace=True, translation=parent_translation)
        original_scale = (
            parent_scale[0]
            if isinstance(parent_scale, (tuple, list))
            and len(parent_scale) == 1
            and isinstance(parent_scale[0], (tuple, list))
            else parent_scale
        )
        cmds.setAttr(
            f"{shape_parent}.scale",
            float(original_scale[0]),
            float(original_scale[1]),
            float(original_scale[2]),
            type="double3",
        )
        dag_restored_witness = run_light_frame(light_direction_a)
        report["dagTransformAba"] = {
            "baseline": depth_snapshot(camera_restored_witness),
            "moved": depth_snapshot(dag_moved_witness),
            "restored": depth_snapshot(dag_restored_witness),
        }
        if (
            dag_moved_witness.get("matrixHash")
            == camera_restored_witness.get("matrixHash")
            or not dag_moved_witness.get("cornersInClip")
            or not dag_moved_witness.get("occupied")
            or dag_restored_witness.get("matrixHash")
            != camera_restored_witness.get("matrixHash")
            or dag_restored_witness.get("writtenFootprintHash")
            != camera_restored_witness.get("writtenFootprintHash")
            or dag_restored_witness.get("writtenSamples")
            != camera_restored_witness.get("writtenSamples")
        ):
            raise RuntimeError("DAG transform did not produce a reversible matrix witness")

        # Missing-light authority must fail closed while the body shaders are
        # still alive: no caster operation, no stale receiver probe, and the
        # ordinary scene remains available.  Recreate the authority before
        # continuing with the hidden-shape and teardown gates.
        _set_receiver_probe(cmds, log, False)
        missing_before_path, missing_before_stats, _ = _capture_active_viewport_buffer(
            cmds,
            output_dir / "native_missing_light_before.png",
            panel,
            require_nonuniform=False,
        )
        _set_receiver_probe(cmds, log, True)
        for current in panels:
            _set_panel_override(mel, current, "mmdNativeCaster")
        missing_probe_witness = wait_for_receiver_probe_witness()
        if not missing_probe_witness.get("receiverProbeEnabled"):
            raise RuntimeError("missing-light precondition did not enable receiver probe")
        for current in panels:
            _set_panel_override(mel, current, "")
        cmds.delete(light_controller)
        for current in panels:
            _set_panel_override(mel, current, "mmdNativeCaster")
        cmds.refresh(force=True)
        time.sleep(0.5)
        missing_light_witness = _query_witness(cmds, log)
        for current in panels:
            _set_panel_override(mel, current, "")
        missing_after_path, missing_after_stats, _ = _capture_active_viewport_buffer(
            cmds,
            output_dir / "native_missing_light_after.png",
            panel,
            require_nonuniform=False,
        )
        report["missingLightWitness"] = missing_light_witness
        report["missingLightProbeBefore"] = missing_probe_witness
        report["missingLightScene"] = {
            "before": str(missing_before_path),
            "beforeStats": missing_before_stats,
            "after": str(missing_after_path),
            "afterStats": missing_after_stats,
        }
        if missing_light_witness.get("setup") or missing_light_witness.get(
            "operationInsertedBeforeScene"
        ) or missing_light_witness.get("receiverProbeEnabled"):
            raise RuntimeError("missing-light caster did not fail closed")
        if (
            int(missing_light_witness.get("drawCallbackCount", 0)) != 0
            or any(
                missing_light_witness.get(key)
                for key in (
                    "drawnRenderItems",
                    "drawnRenderItemDagPaths",
                    "drawnRenderItemTypes",
                    "drawnRenderItemCastsShadows",
                )
            )
            or missing_light_witness.get("frameComplete")
            or missing_light_witness.get("colorTargetAcquired")
            or missing_light_witness.get("depthTargetAcquired")
            or missing_light_witness.get("colorTarget", {}).get("width", 0)
            or missing_light_witness.get("colorTarget", {}).get("height", 0)
            or missing_light_witness.get("colorTarget", {}).get("name")
            or missing_light_witness.get("colorTarget", {}).get("format", -1) != -1
            or missing_light_witness.get("depthTarget", {}).get("width", 0)
            or missing_light_witness.get("depthTarget", {}).get("height", 0)
            or missing_light_witness.get("depthTarget", {}).get("name")
            or missing_light_witness.get("depthTarget", {}).get("format", -1) != -1
            or missing_light_witness.get("matrixHash")
            or missing_light_witness.get("casterMatrixHash")
            or missing_light_witness.get("receiverMatrixHash")
            or not missing_light_witness.get("failClosedProbeDisableAttempted")
            or not missing_light_witness.get("failClosedProbeDisableSuccess")
            or int(missing_light_witness.get("failClosedProbeDisableFailureCount", 1)) != 0
        ):
            raise RuntimeError("missing-light fail-closed diagnostics retained stale caster state")
        light_controller = create_mmd_light_controller()
        set_mmd_light_direction(light_direction_a)

        # Negative control: keep the override path enabled but hide every
        # caster shape.  The next setup resets per-frame occupancy, so a clear
        # target after this frame proves the sample did not come from stale
        # diagnostics or the standard scene.
        for shape in shapes:
            cmds.setAttr(f"{shape}.visibility", False)
        for current in panels:
            _set_panel_override(mel, current, "mmdNativeCaster")
        hidden_active_witness = _wait_for_active_witness(cmds, log)
        report["hiddenActiveWitness"] = hidden_active_witness
        for current in panels:
            _set_panel_override(mel, current, "")
        cmds.refresh(force=True)
        time.sleep(0.5)
        hidden_disabled_witness = _query_witness(cmds, log)
        report["hiddenDisabledWitness"] = hidden_disabled_witness
        if hidden_disabled_witness.get("occupied") or hidden_disabled_witness.get(
            "nonClearSamples"
        ):
            raise RuntimeError("hidden-shape caster witness reported non-clear occupancy")
        if not hidden_disabled_witness.get("receiverTargetsRetained") or hidden_disabled_witness.get(
            "released"
        ):
            raise RuntimeError(
                "hidden-shape caster witness did not retain its caster target"
            )

        # Reject teardown before changing any registration while geometry-owned
        # receiver shaders are still alive.  This guards the partial-unload
        # boundary that previously ended in a Windows heap failure.
        active_scene_unload_error = None
        try:
            cmds.unloadPlugin("mmd_tools_cpp", force=True)
        except Exception as exc:
            active_scene_unload_error = str(exc)
        report["activeSceneUnloadError"] = active_scene_unload_error
        report["pluginLoadedAfterActiveSceneUnload"] = bool(
            cmds.pluginInfo("mmd_tools_cpp", query=True, loaded=True)
        )
        if active_scene_unload_error is None:
            raise RuntimeError("active receiver scene unexpectedly allowed plug-in unload")
        if not report["pluginLoadedAfterActiveSceneUnload"]:
            raise RuntimeError("active receiver unload failure left plug-in unregistered")

        # Destroy the borrowed geometry overrides before unloading the plug-in.
        # This makes the registry lifetime explicit: the final scene reset
        # retires every body shader while the persistent target is still live.
        log("native caster unload scene reset begin")
        cmds.file(new=True, force=True)
        cmds.refresh(force=True)
        report["unloadSceneReset"] = True
        log("native caster unload scene reset complete")
        post_reset_witness = _query_witness(cmds, log)
        report["postResetWitness"] = post_reset_witness
        if int(post_reset_witness.get("receiverShaderRegistered", 0)) != 0 or int(
            post_reset_witness.get("receiverLiveAssignmentOwners", 0)
        ) != 0:
            raise RuntimeError(
                "scene reset did not retire all borrowed body shader owners"
            )
        unload_error = None
        try:
            log("native caster unload begin")
            cmds.unloadPlugin("mmd_tools_cpp", force=True)
            log("native caster unload complete")
        except Exception as exc:  # Maya may keep a shape node alive until file close.
            unload_error = str(exc)
            cmds.file(new=True, force=True)
            try:
                cmds.unloadPlugin("mmd_tools_cpp", force=True)
            except Exception as retry_exc:
                unload_error = f"{unload_error}; retry: {retry_exc}"
        report["pluginUnloadError"] = unload_error
        report["pluginLoadedAfterUnload"] = bool(
            cmds.pluginInfo("mmd_tools_cpp", query=True, loaded=True)
        )
        depth_aba = report.get("depthBiasAba", {})
        baseline_depth = depth_aba.get("baseline", active_witness)
        control_depth = depth_aba.get("control", {})
        restored_depth = depth_aba.get("restored", {})
        receiver_aba = report.get("receiverProbeAba", {})
        receiver_baseline_witness = receiver_aba.get("baseline", {})
        receiver_control_witness = receiver_aba.get("control", {})
        receiver_restored_witness = receiver_aba.get("restored", {})
        hard_shadow_aba = report.get("hardShadowAba", {})
        hard_shadow_off_witness = hard_shadow_aba.get("off", {})
        hard_shadow_on_witness = hard_shadow_aba.get("on", {})
        hard_shadow_restored_witness = hard_shadow_aba.get("restored", {})
        hidden_clear_samples = int(hidden_disabled_witness.get("clearSamples", 0))
        render_items = [
            (shape, item)
            for shape, witness in report.get("renderItemWitnesses", {}).items()
            for item in witness.get("items", [])
            if isinstance(item, dict)
        ]
        eligible_item_identities = sorted(
            (str(shape), str(item.get("renderItemName", "")))
            for shape, item in render_items
            if item.get("casterEligible")
        )
        excluded_item_identities = {
            (str(shape), str(item.get("renderItemName", "")))
            for shape, item in render_items
            if not item.get("casterEligible")
        }
        drawn_item_identities = sorted(
            (str(path), str(name))
            for path, name in zip(
                active_witness.get("drawnRenderItemDagPaths", []),
                active_witness.get("drawnRenderItems", []),
            )
        )
        exclusion_reasons = {
            str(item.get("casterExclusionReason", ""))
            for _, item in render_items
        }
        expected_receiver_eligible_count = _receiver_eligible_non_outline_count(
            report.get("renderItemWitnesses", {})
        )
        receiver_registered_count = int(
            receiver_baseline_witness.get("receiverShaderRegistered", 0)
        )
        report["receiverFilterDiagnostics"] = {
            "expectedReceiverEligibleNonOutlineBodyCount": expected_receiver_eligible_count,
            "receiverShaderRegistered": receiver_registered_count,
            "matches": expected_receiver_eligible_count == receiver_registered_count,
        }
        checks = {
            "overrideRegistered": bool(active_witness.get("registered")),
            "casterSelectedMmdRenderShape": int(active_witness.get("selectedCount", 0)) > 0,
            "r32fTargetAcquired": bool(active_witness.get("colorTargetAcquired"))
            and int(active_witness.get("colorTarget", {}).get("width", 0)) == 2048
            and int(active_witness.get("colorTarget", {}).get("height", 0)) == 2048
            and int(active_witness.get("colorTarget", {}).get("format", -1)) == 41
            and active_witness.get("colorTarget", {}).get("name")
            == "__mmdNativeCasterColorTarget__",
            "d32TargetAcquired": bool(active_witness.get("depthTargetAcquired"))
            and int(active_witness.get("depthTarget", {}).get("width", 0)) == 2048
            and int(active_witness.get("depthTarget", {}).get("height", 0)) == 2048
            and int(active_witness.get("depthTarget", {}).get("format", -1)) == 2
            and active_witness.get("depthTarget", {}).get("name")
            == "__mmdNativeCasterDepthTarget__",
            "shaderBound": bool(active_witness.get("shaderAvailable")),
            "matrixBound": bool(active_witness.get("matrixBound")),
            "matrixValidated": bool(active_witness.get("matrixValidated"))
            and bool(control_depth.get("matrixValidated"))
            and bool(restored_depth.get("matrixValidated")),
            "matrixAuthority": active_witness.get("matrixSource")
            == "tagged_mmd_light_minus_z"
            and bool(active_witness.get("lightPath"))
            and bool(active_witness.get("cornersInClip"))
            and active_witness.get("matrixHash")
            == active_witness.get("casterMatrixHash")
            == active_witness.get("receiverMatrixHash"),
            "matrixAba": bool(
                report.get("lightRotationAba", {}).get("baseline", {}).get("matrixHash")
            )
            and report.get("lightRotationAba", {}).get("baseline", {}).get("matrixHash")
            != report.get("lightRotationAba", {}).get("control", {}).get("matrixHash")
            and report.get("lightRotationAba", {}).get("baseline", {}).get("matrixHash")
            == report.get("lightRotationAba", {}).get("restored", {}).get("matrixHash")
            and all(
                witness.get("matrixHash")
                == witness.get("casterMatrixHash")
                == witness.get("receiverMatrixHash")
                for witness in (
                    report.get("lightRotationAba", {}).get("baseline", {}),
                    report.get("lightRotationAba", {}).get("control", {}),
                    report.get("lightRotationAba", {}).get("restored", {}),
                )
            )
            and bool(
                report.get("lightRotationAba", {}).get("control", {}).get("occupied")
            )
            and report.get("lightRotationAba", {}).get("baseline", {}).get(
                "writtenFootprintHash"
            )
            != report.get("lightRotationAba", {}).get("control", {}).get(
                "writtenFootprintHash"
            )
            and report.get("lightRotationAba", {}).get("baseline", {}).get(
                "writtenFootprintHash"
            )
            == report.get("lightRotationAba", {}).get("restored", {}).get(
                "writtenFootprintHash"
            ),
            "cameraInvariant": bool(
                report.get("cameraInvariant", {}).get("baseline")
                == report.get("cameraInvariant", {}).get("moved")
                == report.get("cameraInvariant", {}).get("restored")
            ),
            "dagTransformAba": bool(
                report.get("dagTransformAba", {}).get("moved", {}).get("cornersInClip")
            )
            and bool(report.get("dagTransformAba", {}).get("moved", {}).get("occupied"))
            and report.get("dagTransformAba", {}).get("baseline", {}).get("matrixHash")
            != report.get("dagTransformAba", {}).get("moved", {}).get("matrixHash")
            and report.get("dagTransformAba", {}).get("baseline", {}).get("matrixHash")
            == report.get("dagTransformAba", {}).get("restored", {}).get("matrixHash")
            and report.get("dagTransformAba", {}).get("baseline", {}).get(
                "writtenFootprintHash"
            )
            == report.get("dagTransformAba", {}).get("restored", {}).get(
                "writtenFootprintHash"
            ),
            "missingLightFailClosed": not bool(
                report.get("missingLightWitness", {}).get("setup")
            )
            and not bool(
                report.get("missingLightWitness", {}).get(
                    "operationInsertedBeforeScene"
                )
            )
            and not bool(
                report.get("missingLightWitness", {}).get("receiverProbeEnabled")
            ),
            "missingLightDiagnosticsReset": int(
                report.get("missingLightWitness", {}).get("drawCallbackCount", 0)
            )
            == 0
            and not any(
                report.get("missingLightWitness", {}).get(key)
                for key in (
                    "drawnRenderItems",
                    "drawnRenderItemDagPaths",
                    "drawnRenderItemTypes",
                    "drawnRenderItemCastsShadows",
                )
            )
            and not bool(report.get("missingLightWitness", {}).get("frameComplete"))
            and not bool(
                report.get("missingLightWitness", {}).get("colorTargetAcquired")
            )
            and not bool(
                report.get("missingLightWitness", {}).get("depthTargetAcquired")
            )
            and not bool(report.get("missingLightWitness", {}).get("matrixHash"))
            and bool(
                report.get("missingLightWitness", {}).get(
                    "failClosedProbeDisableAttempted"
                )
            )
            and bool(
                report.get("missingLightWitness", {}).get(
                    "failClosedProbeDisableSuccess"
                )
            )
            and int(
                report.get("missingLightWitness", {}).get(
                    "failClosedProbeDisableFailureCount", 1
                )
            )
            == 0,
            "missingLightProbeTransition": bool(
                report.get("missingLightProbeBefore", {}).get("receiverProbeEnabled")
            )
            and bool(
                report.get("missingLightWitness", {}).get(
                    "failClosedProbeDisableAttempted"
                )
            )
            and bool(
                report.get("missingLightWitness", {}).get(
                    "failClosedProbeDisableSuccess"
                )
            ),
            "missingLightScenePreserved": bool(
                report.get("missingLightScene", {}).get("beforeStats", {}).get(
                    "scenePixelSha256"
                )
            )
            and report.get("missingLightScene", {}).get("beforeStats", {}).get(
                "scenePixelSha256"
            )
            == report.get("missingLightScene", {}).get("afterStats", {}).get(
                "scenePixelSha256"
            ),
            "depthBiasBound": bool(active_witness.get("depthBiasBound"))
            and bool(control_depth.get("depthBiasBound"))
            and bool(restored_depth.get("depthBiasBound")),
            "operationInsertedBeforeScene": bool(
                active_witness.get("operationInsertedBeforeScene")
            ),
            "occupancySupported": bool(active_witness.get("occupancySupported")),
            "occupied": bool(active_witness.get("occupied"))
            and int(active_witness.get("writtenSamples", 0)) > 0,
            "casterItemCategoriesPresent": bool(eligible_item_identities)
            and "selfShadowMapDisabled" in exclusion_reasons
            and (
                not control_model_path
                or {"outline", "transparent"}.issubset(exclusion_reasons)
            ),
            "actualCasterItemsExact": drawn_item_identities
            == eligible_item_identities
            and not excluded_item_identities.intersection(
                drawn_item_identities
            )
            and int(active_witness.get("drawCallbackCount", 0)) > 0,
            "actualCasterItemsMaterial": bool(drawn_item_identities)
            and all(
                item_type == "MaterialSceneItem"
                for item_type in active_witness.get("drawnRenderItemTypes", [])
            )
            and all(active_witness.get("drawnRenderItemCastsShadows", [])),
            "excludedItemTypesPreserveViewport": all(
                (
                    item.get("renderItemType") == "MaterialSceneItem"
                    if item.get("effectiveTransparent")
                    else item.get("renderItemType") == "NonMaterialSceneItem"
                )
                for _, item in render_items
                if not item.get("casterEligible")
            ),
            "depthDistribution": bool(active_witness.get("writtenDepthFinite"))
            and bool(active_witness.get("writtenDepthInRange"))
            and int(active_witness.get("writtenOutOfRangeSamples", 1)) == 0
                and float(active_witness.get("writtenMax", 0.0))
                > float(active_witness.get("writtenMin", 0.0)) + 1.0e-4,
            "depthBiasAba": _depth_bias_aba_passes(
                baseline_depth, control_depth, restored_depth
            ),
            "depthBiasTargetRetained": bool(
                depth_aba.get("controlDisabled", {}).get("receiverTargetsRetained")
            )
            and bool(
                depth_aba.get("restoredDisabled", {}).get(
                    "receiverTargetsRetained"
                )
            )
            and not bool(depth_aba.get("controlDisabled", {}).get("released"))
            and not bool(depth_aba.get("restoredDisabled", {}).get("released")),
            "receiverBodyShaderRegistered": int(
                receiver_baseline_witness.get("receiverShaderRegistered", 0)
            )
            > 0,
            "receiverFilterDiagnosticsMatch": (
                not expect_receiver_probe_static
                or (
                    expected_receiver_eligible_count > 0
                    and expected_receiver_eligible_count == receiver_registered_count
                )
            ),
            "receiverAssignmentSucceeded": int(
                receiver_baseline_witness.get("receiverAssignmentSuccess", 0)
            )
            >= int(receiver_baseline_witness.get("receiverShaderRegistered", 0))
            and int(receiver_baseline_witness.get("receiverAssignmentFailure", 1)) == 0
            and int(receiver_control_witness.get("receiverAssignmentFailure", 1)) == 0
            and int(receiver_restored_witness.get("receiverAssignmentFailure", 1)) == 0,
            "receiverResourceHandleNonNull": bool(
                receiver_baseline_witness.get(
                    "receiverTargetResourceHandleNonNull"
                )
            ),
            "receiverSameFrame": bool(
                receiver_baseline_witness.get("receiverTargetSameFrame")
            )
            and bool(receiver_control_witness.get("receiverTargetSameFrame"))
            and bool(receiver_restored_witness.get("receiverTargetSameFrame"))
            and bool(receiver_baseline_witness.get("operationInsertedBeforeScene")),
            "receiverProbeDisabledPresentUnchanged": bool(
                report["captures"].get("standardPresentDiff")
            )
            and float(
                report["captures"].get("standardPresentDiff", {}).get("ratio", 1.0)
            )
            <= 0.001
            and report["captures"].get("casterEnabledViewportStats", {}).get(
                "scenePixelSha256"
            )
            == report["captures"].get("casterDisabledViewportStats", {}).get(
                "scenePixelSha256"
            ),
            "receiverProbeAba": bool(
                receiver_aba.get("pixels", {}).get("pass")
            )
            and bool(receiver_baseline_witness.get("receiverProbeEnabled"))
            and bool(receiver_control_witness.get("receiverProbeEnabled"))
            and bool(receiver_restored_witness.get("receiverProbeEnabled")),
            "hardShadowWitnessOff": _hard_shadow_witness_passes(
                hard_shadow_off_witness, False
            ),
            "hardShadowWitnessOn": _hard_shadow_witness_passes(
                hard_shadow_on_witness, True
            ),
            "hardShadowWitnessRestored": _hard_shadow_witness_passes(
                hard_shadow_restored_witness, False
            ),
            "hardShadowAba": bool(hard_shadow_aba.get("pixels", {}).get("pass")),
            "hardShadowStates": bool(hard_shadow_aba.get("states", {}).get("pass")),
            "hardShadowDisabledRetained": bool(
                hard_shadow_aba.get("disabled", {}).get("receiverTargetsRetained")
            )
            and not bool(hard_shadow_aba.get("disabled", {}).get("released")),
            "receiverTargetsRetained": bool(
                receiver_baseline_witness.get("receiverTargetsRetained")
            )
            and bool(receiver_control_witness.get("receiverTargetsRetained"))
            and bool(receiver_restored_witness.get("receiverTargetsRetained"))
            and not bool(receiver_baseline_witness.get("released"))
            and not bool(receiver_control_witness.get("released"))
            and not bool(receiver_restored_witness.get("released")),
            "ordinarySceneAfterDisable": Path(report["captures"]["casterDisabled"]).is_file(),
            "activeViewportReadback": Path(
                report["captures"].get("casterEnabledViewport", "")
            ).is_file()
            and not bool(report["captures"].get("casterEnabledViewportError"))
            and bool(report["captures"].get("casterEnabledViewportStats"))
            and int(
                report["captures"].get("casterEnabledViewportStats", {}).get(
                    "distinctSamples", 0
                )
            )
            >= 2,
            "disabledViewportReadback": Path(
                report["captures"].get("casterDisabledViewport", "")
            ).is_file()
            and not bool(report["captures"].get("casterDisabledViewportError"))
            and bool(report["captures"].get("casterDisabledViewportStats"))
            and int(
                report["captures"].get("casterDisabledViewportStats", {}).get(
                    "distinctSamples", 0
                )
            )
            >= 2,
            "standardPresentPreserved": (
                bool(report["captures"].get("standardPresentDiff"))
                and float(
                    report["captures"]["standardPresentDiff"].get("ratio", 1.0)
                )
                <= 0.001
                and report["captures"].get("casterEnabledViewportStats", {}).get(
                    "scenePixelSha256"
                )
                == report["captures"].get("casterDisabledViewportStats", {}).get(
                    "scenePixelSha256"
                )
                and report.get("activePanel", {}).get("rendererName")
                == "vp2Renderer"
                and bool(report.get("activePanel", {}).get("headsUpDisplay"))
            ),
            "disableRetained": bool(
                hidden_disabled_witness.get("receiverTargetsRetained")
            )
            and not bool(hidden_disabled_witness.get("released")),
            "sceneResetRetiredOwners": int(
                report.get("postResetWitness", {}).get(
                    "receiverLiveAssignmentOwners", 1
                )
            )
            == 0
            and int(
                report.get("postResetWitness", {}).get(
                    "receiverShaderRegistered", 1
                )
            )
            == 0,
            "hiddenNegativeClear": not bool(hidden_disabled_witness.get("occupied"))
            and int(hidden_disabled_witness.get("writtenSamples", 0)) == 0
            and hidden_clear_samples == 2048 * 2048
            and int(hidden_disabled_witness.get("finiteSamples", 0))
            == hidden_clear_samples
            and int(hidden_disabled_witness.get("nonFiniteSamples", 1)) == 0,
            "activeSceneUnloadRejected": bool(
                report.get("activeSceneUnloadError")
            )
            and bool(report.get("pluginLoadedAfterActiveSceneUnload")),
            "unloaded": report.get("pluginUnloadError") is None
            and not report["pluginLoadedAfterUnload"],
        }
        report["checks"] = checks
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            raise RuntimeError("native caster checks failed: " + ", ".join(failed))
        report["status"] = "pass"
    except Exception as exc:
        report["errors"].append(str(exc))
        log("probe failed:")
        log(traceback.format_exc())
    finally:
        _write_report(report_file, report)
        log(f"RESULT_JSON: {json.dumps(report, ensure_ascii=False)}")
        log(COMPLETION_MARKER)


def main() -> int:
    """Launch Maya, run the native caster probe, and return its status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default="2024", help="Maya major version.")
    parser.add_argument("--model", type=Path, required=True, help="UI-import PMX fixture.")
    parser.add_argument(
        "--control-model",
        type=Path,
        default=None,
        help="Optional caster-off transparent/outline PMX control fixture.",
    )
    parser.add_argument("--plugin", type=Path, default=None)
    parser.add_argument(
        "--out-dir", type=Path, default=_ROOT / "build" / "render-override-native-caster"
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--frame", type=int, default=0)
    parser.add_argument("--camera-json", type=Path, default=None)
    parser.add_argument(
        "--expect-receiver-probe-static",
        action="store_true",
        help=(
            "Expect the receiver probe A/B/A body pixels to remain static; "
            "use for fixtures with no visible self-shadow receivers."
        ),
    )
    args = parser.parse_args()

    if not args.model.is_file():
        parser.error(f"model does not exist: {args.model}")
    if args.control_model is not None and not args.control_model.is_file():
        parser.error(f"control model does not exist: {args.control_model}")
    plugin = args.plugin or (
        _ROOT / "plug-ins" / str(args.maya) / "Debug" / "mmd_tools_cpp.mll"
    )
    if not plugin.is_file():
        parser.error(f"native plug-in does not exist: {plugin}")
    camera_config = DEFAULT_CAMERA
    if args.camera_json is not None:
        try:
            camera_config = json.loads(args.camera_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            parser.error(f"could not read --camera-json: {exc}")
        if not isinstance(camera_config, dict):
            parser.error("--camera-json must contain a JSON object")

    out_dir = args.out_dir.resolve()
    log_path = out_dir / f"native_caster_maya{args.maya}.log"
    report_path = out_dir / f"native_caster_maya{args.maya}.json"
    control_model = (
        str(args.control_model.resolve()) if args.control_model is not None else None
    )
    command = (
        "from tools.render_override_native_caster_e2e import run_probe\n"
        f"run_probe({str(log_path)!r}, {str(report_path)!r}, {str(out_dir)!r}, "
        f"{str(args.model.resolve())!r}, {str(plugin.resolve())!r}, "
        f"control_model_path={control_model!r}, "
        f"width={args.width}, height={args.height}, camera_config={camera_config!r}, "
        f"frame={args.frame}, "
        f"expect_receiver_probe_static={args.expect_receiver_probe_static!r})\n"
    )
    env_overrides = {
        "MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11",
        "MMD_TOOLS_CPP_PLUGIN": str(plugin.resolve()),
        "PATH": os.pathsep.join((str(plugin.parent), os.environ.get("PATH", ""))),
    }
    report = run_maya_e2e(
        project_root=_ROOT,
        version=str(args.maya),
        out_dir=out_dir,
        port=args.port,
        timeout=args.timeout,
        log_path=log_path,
        report_path=report_path,
        command=command,
        marker=COMPLETION_MARKER,
        send_label="<render-override-native-caster-command>",
        stale_paths=(
            log_path,
            report_path,
            out_dir / "native_caster_enabled.png",
            out_dir / "native_caster_enabled_viewport.png",
            out_dir / "native_caster_disabled.png",
            out_dir / "native_hard_shadow_off.png",
            out_dir / "native_hard_shadow_on.png",
            out_dir / "native_hard_shadow_restored.png",
        ),
        port_error=f"commandPort :{args.port} is already open; choose another --port",
        report_error=f"native caster report missing: {report_path}",
        log_ready=LOGGER,
        warn_detached=True,
        env_overrides=env_overrides,
    )
    LOGGER.info("Native caster E2E status: %s", report.get("status"))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
