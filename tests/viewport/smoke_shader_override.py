"""Smoke test for the legacy MMDShader VP2.0 shader override.

This script runs under mayapy and verifies the custom ``MMDShader`` node can be
registered through the Python plug-in, assigned to visible geometry, and captured
through an offscreen Viewport 2.0 playblast.  It is intentionally a narrow smoke:
pixel-perfect shader validation belongs to the dx11/static render harnesses.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import maya.cmds as cmds
import maya.standalone


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MMDShader override viewport smoke.")
    parser.add_argument(
        "--out",
        default="build/captures/shader_override_smoke.png",
        help="Output PNG path. Default: build/captures/shader_override_smoke.png",
    )
    parser.add_argument("--frame", type=int, default=1, help="Frame number to capture.")
    parser.add_argument("--width", type=int, default=640, help="Capture width in pixels.")
    parser.add_argument("--height", type=int, default=480, help="Capture height in pixels.")
    return parser.parse_args()


def _resolve_actual_png(requested: Path, frame: int) -> Path:
    requested = requested.resolve()
    out_dir = requested.parent
    stem = requested.stem
    candidates = [
        requested,
        requested.with_suffix(".png"),
        out_dir / f"{stem}.png",
        out_dir / f"{stem}.{frame:04d}.png",
        out_dir / f"{stem}.{frame:03d}.png",
        out_dir / f"{stem}.{frame:02d}.png",
        out_dir / f"{stem}.{frame}.png",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    png_files = sorted(
        out_dir.glob(f"{stem}*.png"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if png_files:
        return png_files[0]
    return requested.with_suffix(".png")


def main() -> int:
    args = _parse_args()
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    maya.standalone.initialize(name="python")
    try:
        cmds.file(new=True, force=True)
        plugin_path = root / "plug-ins" / "mmd_tools_plugin.py"
        cmds.loadPlugin(str(plugin_path), quiet=True)
        if "MMDShader" not in (cmds.allNodeTypes() or []):
            raise RuntimeError("MMDShader node type was not registered")

        cube = cmds.polyCube(name="shaderOverrideCube", width=2.0, height=2.0, depth=2.0)[0]
        shader = cmds.createNode("MMDShader", name="shaderOverrideMMDShader")
        cmds.setAttr(f"{shader}.diffuseColor", 0.95, 0.25, 0.15, type="float3")
        shading_group = cmds.sets(
            renderable=True,
            noSurfaceShader=True,
            empty=True,
            name="shaderOverrideSG",
        )
        cmds.connectAttr(f"{shader}.outColor", f"{shading_group}.surfaceShader", force=True)
        cmds.sets(cube, edit=True, forceElement=shading_group)

        light_shape = cmds.directionalLight(name="shaderOverrideLight", intensity=1.0)
        light_xform = cmds.listRelatives(light_shape, parent=True)[0]
        cmds.setAttr(f"{light_xform}.rotateX", -55.0)
        cmds.setAttr(f"{light_xform}.rotateY", -35.0)

        try:
            cmds.setAttr("persp.translateX", 5.0)
            cmds.setAttr("persp.translateY", 4.0)
            cmds.setAttr("persp.translateZ", 5.0)
            cmds.setAttr("persp.rotateX", -30.0)
            cmds.setAttr("persp.rotateY", 45.0)
            cmds.setAttr("persp.rotateZ", 0.0)
        except Exception:
            pass

        for old_png in out_path.parent.glob(f"{out_path.stem}*.png"):
            try:
                old_png.unlink()
            except Exception:
                pass

        cmds.currentTime(args.frame)
        try:
            cmds.refresh()
        except Exception:
            pass

        result = cmds.playblast(
            filename=str(out_path.with_suffix("")),
            frame=args.frame,
            format="image",
            compression="png",
            offScreen=True,
            offScreenViewportUpdate=True,
            viewer=False,
            width=args.width,
            height=args.height,
            forceOverwrite=True,
            showOrnaments=False,
            percent=100,
        )
        print(f"playblast returned: {result!r}")

        actual = _resolve_actual_png(out_path, args.frame)
        if not actual.exists():
            contents = list(actual.parent.iterdir()) if actual.parent.exists() else []
            raise FileNotFoundError(f"Shader override capture not produced. Contents: {contents}")
        size = actual.stat().st_size
        if size <= 0:
            raise RuntimeError(f"Shader override capture is zero bytes: {actual}")

        print(f"OK: MMDShader override smoke -> {actual} (size={size} bytes)")
        return 0
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
