"""Smoke test for offscreen viewport capture (playblast) under mayapy.

This script is a minimal CLI smoke that:
- Runs under mayapy (standalone, no GUI required)
- Initializes Maya standalone
- Builds a trivial scene (polyCube + camera + directional light + basic shader)
- Captures one frame to PNG via cmds.playblast with offScreen=True / offScreenViewportUpdate=True / viewer=False
- Detects the actual output file (handles frame-padded names that playblast may emit)
- Verifies the PNG exists and has non-zero size

Intended only as a smoke to confirm Maya can produce viewport-like PNG from command line.
No dependency on mmd_tools package or the C++ plugin.
Launched by the `maya_viewport_capture` Nox session (or directly via mayapy ...).

Usage (direct):
    mayapy tests/viewport/smoke_viewport_capture.py --out build/captures/viewport_smoke.png --frame 1 --width 640 --height 480

The script never opens viewers or external windows.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import maya.cmds as cmds
import maya.standalone


def _parse_args() -> argparse.Namespace:
    """Parse command line options for the capture smoke."""
    parser = argparse.ArgumentParser(
        description="Minimal mayapy offscreen viewport capture smoke."
    )
    parser.add_argument(
        "--out",
        default="build/captures/viewport_smoke.png",
        help="Output PNG path (parent dirs created automatically). Default: build/captures/viewport_smoke.png",
    )
    parser.add_argument(
        "--frame",
        type=int,
        default=1,
        help="Frame number to capture (also used for time). Default: 1",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=640,
        help="Capture width in pixels. Default: 640",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=480,
        help="Capture height in pixels. Default: 480",
    )
    return parser.parse_args()


def _resolve_actual_png(requested: Path, frame: int) -> Path:
    """Return the actual written PNG path.

    playblast with format=image + compression=png can emit:
      - exact requested name + .png
      - <stem>.<frame>.png (padding varies: 1 or 4 digits common)
      - <stem>.png
    We also fall back to "most recent *.png in the output dir" because the smoke
    controls an empty target directory.
    """
    requested = requested.resolve()
    out_dir = requested.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = requested.stem
    # Common patterns produced by playblast for single-frame image export.
    candidates: list[Path] = [
        requested,
        requested.with_suffix(".png"),
        out_dir / f"{stem}.png",
        out_dir / f"{stem}.{frame:04d}.png",
        out_dir / f"{stem}.{frame:03d}.png",
        out_dir / f"{stem}.{frame:02d}.png",
        out_dir / f"{stem}.{frame}.png",
    ]

    for cand in candidates:
        if cand.exists() and cand.stat().st_size > 0:
            return cand

    # Fallback: any PNG with the requested stem that appeared in our controlled output dir.
    png_files = sorted(
        out_dir.glob(f"{stem}*.png"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if png_files:
        return png_files[0]

    # Return the most likely expected path; caller will fail the size/exists check.
    return requested.with_suffix(".png")


def main() -> int:
    """Execute the capture smoke and return process exit code."""
    args = _parse_args()

    out_path = Path(args.out).resolve()
    frame = args.frame
    width = args.width
    height = args.height

    # Ensure output location exists (script owns this for the smoke).
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Make project root importable (harmless; script has no project imports).
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    maya.standalone.initialize(name="python")
    try:
        # Fresh scene.
        cmds.file(new=True, force=True)

        # Minimal visible content: a cube.
        cube = cmds.polyCube(name="smokeCube", width=2.0, height=2.0, depth=2.0)[0]

        # Camera positioned to see the cube.
        cam_transform = cmds.camera(name="smokeCam", focalLength=35.0)[0]
        cmds.setAttr(f"{cam_transform}.translateX", 6.0)
        cmds.setAttr(f"{cam_transform}.translateY", 5.0)
        cmds.setAttr(f"{cam_transform}.translateZ", 6.0)
        cmds.setAttr(f"{cam_transform}.rotateX", -30.0)
        cmds.setAttr(f"{cam_transform}.rotateY", 45.0)
        cmds.setAttr(f"{cam_transform}.rotateZ", 0.0)

        # Directional light to illuminate the cube (default lighting may be off in standalone).
        # directionalLight() returns the *shape*; rotation lives on its parent transform.
        light_shape = cmds.directionalLight(name="smokeLight", intensity=1.0)
        light_xform = cmds.listRelatives(light_shape, parent=True)[0]
        cmds.setAttr(f"{light_xform}.rotateX", -55.0)
        cmds.setAttr(f"{light_xform}.rotateY", -35.0)
        cmds.setAttr(f"{light_xform}.rotateZ", 0.0)

        # Simple colored lambert so the cube has obvious shading (not just default gray).
        shader = cmds.shadingNode("lambert", asShader=True, name="smokeLambert")
        cmds.setAttr(f"{shader}.color", 0.3, 0.65, 0.95, type="double3")
        cmds.select(cube, replace=True)
        cmds.hyperShade(assign=shader)

        # Position the default 'persp' camera. We avoid the playblast 'camera'
        # kwarg because it triggers "invalid flag" in this mayapy standalone context.
        try:
            cmds.setAttr("persp.translateX", 5.0)
            cmds.setAttr("persp.translateY", 4.0)
            cmds.setAttr("persp.translateZ", 5.0)
            cmds.setAttr("persp.rotateX", -30.0)
            cmds.setAttr("persp.rotateY", 45.0)
            cmds.setAttr("persp.rotateZ", 0.0)
        except Exception:
            pass

        cmds.currentTime(frame)
        try:
            cmds.playbackOptions(minTime=frame, maxTime=frame)
        except Exception:
            pass

        # Some scenes benefit from an explicit refresh before offscreen capture.
        try:
            cmds.refresh()
        except Exception:
            pass

        # Clean any prior outputs for this stem so resolve picks exactly our capture.
        for old_png in out_path.parent.glob(f"{out_path.stem}*.png"):
            try:
                old_png.unlink()
            except Exception:
                pass

        # Perform the offscreen capture.
        # Use singular 'frame' (not startFrame/endFrame) + omit 'camera' kwarg:
        # those two families of flags have been observed to raise "invalid flag"
        # in Maya 2024 mayapy standalone (no active editor/panel). The minimal
        # flag set below (proven via discovery) produces a valid PNG.
        # viewer=False prevents any external viewer.
        playblast_result = cmds.playblast(
            filename=str(out_path.with_suffix("")),
            frame=frame,
            format="image",
            compression="png",
            offScreen=True,
            offScreenViewportUpdate=True,
            viewer=False,
            width=width,
            height=height,
            forceOverwrite=True,
            showOrnaments=False,
            percent=100,
        )

        print(f"playblast returned: {playblast_result!r}")

        # Resolve whatever file Maya actually wrote (handles .####.png cases).
        actual = _resolve_actual_png(out_path, frame)

        if not actual.exists():
            # Extra diagnostics for smoke failure diagnosis (only this dir is ours).
            try:
                contents = list(actual.parent.iterdir()) if actual.parent.exists() else []
            except Exception as e:
                contents = [f"<iterdir failed: {e}>"]
            print(f"Output dir contents: {contents}")
            raise FileNotFoundError(
                f"Viewport capture PNG not produced. "
                f"Requested base: {out_path}, frame: {frame}. "
                f"Checked candidates around {actual}"
            )

        size = actual.stat().st_size
        if size <= 0:
            raise RuntimeError(f"Captured PNG is zero bytes: {actual}")

        print(f"OK: viewport capture -> {actual} (size={size} bytes, frame={frame}, {width}x{height})")
        return 0

    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
