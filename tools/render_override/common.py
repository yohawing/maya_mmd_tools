"""Small, Maya-facing helpers shared by render-override harness entry points.

The helpers in this module intentionally have no scenario knowledge.  They
cover stable artifact/report plumbing and the commandPort operations shared by
the MMD Render probes.  Scenario-specific orchestration
and report schemas remain in their original entry points.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Callable, Dict, Tuple

from tests.common.maya_location import mayapy as _mayapy_for_version


def png_size(path: Path) -> Tuple[int, int]:
    """Read dimensions from a PNG IHDR without requiring Pillow."""

    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError(f"not a PNG: {path}")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid PNG dimensions: {path}")
    return width, height


def resolve_mayapy(maya: str) -> Path:
    """Resolve an installed Maya version through the project helper."""

    candidate = _mayapy_for_version(str(maya))
    if candidate.is_file():
        return candidate.resolve()
    raise FileNotFoundError(f"could not resolve mayapy for Maya {maya}: {candidate}")


def write_report(path: Path, report: Dict[str, Any]) -> None:
    """Persist a UTF-8 indented JSON report for a commandPort probe."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def capture_view(
    cmds: Any,
    destination: Path,
    panel: str,
    width: int,
    height: int,
    frame: int = 1,
) -> Path:
    """Capture a GUI viewport and return the fresh PNG produced by playblast."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    previous_files = {}
    for path in destination.parent.glob(f"{destination.stem}*.png"):
        stat = path.stat()
        previous_files[path] = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
    result = cmds.playblast(
        filename=str(destination.with_suffix("")),
        frame=frame,
        format="image",
        compression="png",
        viewer=False,
        showOrnaments=False,
        forceOverwrite=True,
        offScreen=False,
        percent=100,
        width=width,
        height=height,
        editorPanelName=panel,
    )
    candidates = (
        destination,
        destination.with_suffix(".png"),
        destination.parent / f"{destination.stem}.0000.png",
        destination.parent / f"{destination.stem}.0001.png",
    )

    def is_fresh(path: Path) -> bool:
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        stat = path.stat()
        current = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
        return current != previous_files.get(path)

    for candidate in candidates:
        if is_fresh(candidate):
            return candidate
    generated = sorted(
        (
            path
            for path in destination.parent.glob(f"{destination.stem}*.png")
            if is_fresh(path)
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if generated:
        return generated[0]
    raise RuntimeError(f"playblast did not create a PNG: {result!r}")


def require_requested_plugin(cmds: Any, plugin_path: str, log: Callable[[str], None]) -> Path:
    """Ensure Maya's canonical plugin registration matches the requested binary."""

    requested = Path(plugin_path).resolve()
    requested_text = str(requested)
    loaded = bool(cmds.pluginInfo(requested_text, query=True, loaded=True))
    if not loaded:
        try:
            loaded = bool(cmds.pluginInfo("mmd_tools_cpp", query=True, loaded=True))
        except RuntimeError:
            loaded = False
    if not loaded:
        cmds.loadPlugin(requested_text, quiet=False)

    actual_raw = cmds.pluginInfo("mmd_tools_cpp", query=True, path=True)
    if isinstance(actual_raw, (list, tuple)):
        if len(actual_raw) != 1:
            raise RuntimeError(
                "could not determine a single loaded mmd_tools_cpp plug-in path: "
                f"{actual_raw!r}"
            )
        actual_raw = actual_raw[0]
    if not actual_raw:
        raise RuntimeError("loaded mmd_tools_cpp plug-in did not report its path")
    actual = Path(str(actual_raw)).resolve()
    log(f"requested plugin: {requested}")
    log(f"loaded canonical plugin: {actual}")
    if os.path.normcase(str(actual)) != os.path.normcase(requested_text):
        raise RuntimeError(
            "loaded mmd_tools_cpp plug-in differs from requested --plugin: "
            f"requested={requested}; loaded={actual}"
        )
    return actual


def _camera_vector(value: object, name: str) -> tuple[float, float, float]:
    """Validate and normalize a camera position/target/up vector."""
    if isinstance(value, (str, bytes)):
        raise ValueError(f"camera {name} must be a three-number sequence")
    try:
        values = tuple(float(component) for component in value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"camera {name} must be a three-number sequence") from exc
    if len(values) != 3 or not all(math.isfinite(component) for component in values):
        raise ValueError(f"camera {name} must be a finite three-number sequence")
    return values


def _configure_camera(cmds: Any, panels: list[str], camera_config: dict) -> dict[str, object]:
    """Create the explicit Oracle camera and assign it to every model panel."""
    if not isinstance(camera_config, dict):
        raise ValueError("camera configuration must be a JSON object")
    position = _camera_vector(camera_config.get("position"), "position")
    target = _camera_vector(camera_config.get("target"), "target")
    up_value = camera_config.get("up")
    up = _camera_vector(up_value, "up") if up_value is not None else None
    fov = float(camera_config.get("fov", 25.0))
    near = float(camera_config.get("near", 0.1))
    far = float(camera_config.get("far", 1000.0))
    if not math.isfinite(fov) or not 0.0 < fov < 180.0:
        raise ValueError(f"camera fov must be finite and between 0 and 180, got {fov}")
    if not math.isfinite(near) or not math.isfinite(far) or near <= 0.0 or far <= near:
        raise ValueError(f"camera clip planes must satisfy 0 < near < far, got {near}, {far}")

    camera, shape = cmds.camera(name="renderOverrideOracleCam")
    cmds.xform(camera, worldSpace=True, translation=position)
    target_locator = cmds.spaceLocator(name="__render_override_camera_target__")[0]
    cmds.xform(target_locator, worldSpace=True, translation=target)
    if up is None:
        constraint = cmds.aimConstraint(
            target_locator,
            camera,
            aimVector=(0, 0, -1),
            upVector=(0, 1, 0),
            worldUpType="scene",
        )[0]
    else:
        constraint = cmds.aimConstraint(
            target_locator,
            camera,
            aimVector=(0, 0, -1),
            upVector=(0, 1, 0),
            worldUpType="vector",
            worldUpVector=up,
        )[0]
    cmds.delete(constraint, target_locator)
    aperture = cmds.getAttr(f"{shape}.horizontalFilmAperture")
    focal_length = (aperture * 25.4 * 0.5) / math.tan(math.radians(fov) * 0.5)
    cmds.setAttr(f"{shape}.focalLength", focal_length)
    cmds.setAttr(f"{shape}.nearClipPlane", near)
    cmds.setAttr(f"{shape}.farClipPlane", far)
    for panel in panels:
        cmds.lookThru(panel, camera)
    return {
        "node": camera,
        "shape": shape,
        "position": list(position),
        "target": list(target),
        "up": list(up) if up is not None else None,
        "fov": fov,
        "near": near,
        "far": far,
    }


def make_parity_camera(cmds: Any, camera: Dict[str, Any]) -> str:
    """Create the manifest camera used by the visual capture harness."""
    configured = _configure_camera(cmds, [], camera)
    return str(configured["node"])
