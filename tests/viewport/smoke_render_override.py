"""Maya standalone smoke for the opt-in R1 passthrough render override.

This is an offscreen registration/lifecycle check, not a replacement for the
Maya GUI / commandPort gate. Batch mayapy cannot activate a model-panel
override, so it verifies registration, ordinary capture, scene -> HUD ->
present ordering, and teardown without selecting the override.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import maya.api.OpenMayaRender as omr
import maya.cmds as cmds
import maya.standalone


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MMD Tools R1 render override smoke.")
    parser.add_argument(
        "--out",
        default="build/captures/render_override_smoke.png",
        help="Output PNG path. Default: build/captures/render_override_smoke.png",
    )
    parser.add_argument("--frame", type=int, default=1)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    return parser.parse_args()


def _normalise_override_name(name):
    return name or None


def _active_override_name():
    active = omr.MRenderer.activeRenderOverride()
    if active is None:
        return None
    if isinstance(active, str):
        return _normalise_override_name(active)
    name = active.name() if callable(getattr(active, "name", None)) else None
    return _normalise_override_name(name)


def _resolve_actual_png(requested: Path, frame: int) -> Path:
    requested = requested.resolve()
    candidates = [
        requested,
        requested.with_suffix(".png"),
        requested.parent / f"{requested.stem}.{frame:04d}.png",
        requested.parent / f"{requested.stem}.{frame:03d}.png",
        requested.parent / f"{requested.stem}.{frame:02d}.png",
        requested.parent / f"{requested.stem}.{frame}.png",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    generated = sorted(
        requested.parent.glob(f"{requested.stem}*.png"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if generated:
        return generated[0]
    return requested.with_suffix(".png")


def _assert_operation_order(override) -> None:
    expected_roles = ("scene", "hud", "present")
    if tuple(override.operation_roles) != expected_roles:
        raise AssertionError(
            f"unexpected R1 operation roles: {override.operation_roles!r}"
        )
    if not override.startOperationIterator():
        raise AssertionError("R1 operation iterator did not start")

    operations = []
    while True:
        operation = override.renderOperation()
        if operation is None:
            break
        operations.append(operation)
        if not override.nextRenderOperation():
            break
    operation_types = [operation.operationType() for operation in operations]
    expected_types = [
        omr.MSceneRender.kSceneRender,
        omr.MHUDRender.kHUDRender,
        omr.MPresentTarget.kPresentTarget,
    ]
    if operation_types != expected_types:
        raise AssertionError(
            f"unexpected R1 operation order: {operation_types!r} != {expected_types!r}"
        )
    override.cleanup()
    if override.renderOperation() is not None:
        raise AssertionError("R1 cleanup did not reset operation iterator")


def main() -> int:
    args = _parse_args()
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    previous_enable = os.environ.get("MMD_TOOLS_ENABLE_RENDER_OVERRIDE")
    previous_skip_shader = os.environ.get("MMD_TOOLS_SKIP_SHADER_OVERRIDE")
    previous_target_probe = os.environ.get(
        "MMD_TOOLS_ENABLE_RENDER_OVERRIDE_TARGET_PROBE"
    )
    plugin_path = root / "plug-ins" / "mmd_tools_plugin.py"
    plugin_name = None
    plugin_loaded = False
    previous_active_name = None

    os.environ["MMD_TOOLS_ENABLE_RENDER_OVERRIDE"] = "1"
    # Keep this R1 smoke independent from the legacy MPxShaderOverride path.
    os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = "1"
    # The standalone smoke cannot prove VP2 target occupancy; keep R2 opt-in.
    os.environ["MMD_TOOLS_ENABLE_RENDER_OVERRIDE_TARGET_PROBE"] = "0"
    maya.standalone.initialize(name="python")
    try:
        cmds.file(new=True, force=True)
        previous_active_name = _active_override_name()
        loaded = cmds.loadPlugin(str(plugin_path), quiet=True)
        plugin_name = loaded[0] if loaded else plugin_path.stem
        plugin_loaded = True
        if not cmds.pluginInfo(plugin_name, query=True, loaded=True):
            raise RuntimeError(f"MMD Tools plugin did not remain loaded: {plugin_name}")

        from mmd_tools.view import render_override

        override = render_override.registered_override()
        if override is None:
            override = omr.MRenderer.findRenderOverride(
                render_override.RENDER_OVERRIDE_NAME
            )
        if override is None:
            raise RuntimeError("R1 render override was not registered")
        if _active_override_name() != previous_active_name:
            raise AssertionError("plugin load changed Maya's active render override")
        _assert_operation_order(override)

        # ``setRenderOverrideName`` is a batch-render setting.  GUI activation
        # is verified by tools/render_override_e2e.py through modelEditor.
        # This standalone smoke intentionally verifies registration only.

        cube = cmds.polyCube(name="renderOverrideCube", width=2.0, height=2.0, depth=2.0)[0]
        cmds.setAttr(f"{cube}.translateY", 0.5)
        light_shape = cmds.directionalLight(name="renderOverrideLight", intensity=1.0)
        light_xform = cmds.listRelatives(light_shape, parent=True)[0]
        cmds.setAttr(f"{light_xform}.rotateX", -45.0)
        cmds.setAttr(f"{light_xform}.rotateY", -30.0)
        try:
            cmds.setAttr("persp.translateX", 5.0)
            cmds.setAttr("persp.translateY", 4.0)
            cmds.setAttr("persp.translateZ", 5.0)
            cmds.setAttr("persp.rotateX", -30.0)
            cmds.setAttr("persp.rotateY", 45.0)
        except Exception:
            pass

        for old_png in out_path.parent.glob(f"{out_path.stem}*.png"):
            try:
                old_png.unlink()
            except OSError:
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
        if not actual.exists() or actual.stat().st_size <= 0:
            raise RuntimeError(f"R1 render override capture was not produced: {actual}")
        print(f"OK: R1 passthrough render override -> {actual} ({actual.stat().st_size} bytes)")
        return 0
    finally:
        try:
            if _active_override_name() != previous_active_name:
                raise RuntimeError(
                    "active render override restoration failed: "
                    f"expected {previous_active_name!r}, got {_active_override_name()!r}"
                )
        finally:
            if plugin_loaded:
                try:
                    cmds.unloadPlugin(plugin_name or str(plugin_path), force=True)
                except Exception as exc:
                    print(f"warning: plugin unload failed: {exc}", file=sys.stderr)
            maya.standalone.uninitialize()
            if previous_enable is None:
                os.environ.pop("MMD_TOOLS_ENABLE_RENDER_OVERRIDE", None)
            else:
                os.environ["MMD_TOOLS_ENABLE_RENDER_OVERRIDE"] = previous_enable
            if previous_skip_shader is None:
                os.environ.pop("MMD_TOOLS_SKIP_SHADER_OVERRIDE", None)
            else:
                os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = previous_skip_shader
            if previous_target_probe is None:
                os.environ.pop("MMD_TOOLS_ENABLE_RENDER_OVERRIDE_TARGET_PROBE", None)
            else:
                os.environ["MMD_TOOLS_ENABLE_RENDER_OVERRIDE_TARGET_PROBE"] = previous_target_probe


if __name__ == "__main__":
    raise SystemExit(main())
