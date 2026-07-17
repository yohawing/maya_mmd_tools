"""Single GUI DX11 viewport snapshot of a PMX model.

Why this exists: mayapy/offscreen cannot create the dx11Shader draw override, and
the earlier before/after harness sent its whole Maya-side program over commandPort
(unreliable -- the program never executed). This tool instead copies the *proven*
delivery shape used by tests/run_gui_tests.py: the heavy Maya-side logic lives in an
importable module function (`run_snapshot`), and the only thing crossing the
commandPort is a short ``import ...; run_snapshot(...)`` call.

Host side (run under any Python with Maya on disk, e.g. mayapy via PowerShell):
  launch Maya GUI with a commandPort -> wait for the port -> send the short call
  -> tail the log for the completion marker -> quit Maya.

Maya side (run_snapshot, executes inside the live GUI / real VP2 DX11 device):
  fresh scene -> load dx11Shader -> import PMX with dx11 backend -> frame camera on
  the model bbox -> add a directional light -> configure a clean mesh-only panel ->
  playblast one PNG.

Usage:
    mayapy tests/viewport/gui_snapshot.py --maya 2026 \
        --model "F:/MMD/pmx/.../model.pmx" --out build/captures/snapshot.png
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.common import maya_commandport

DEFAULT_MAYA_VERSION = "2026"  # the user's DX11-enabled Maya
COMMAND_PORT = 7722
COMPLETION_MARKER = "//-- SNAPSHOT FINISHED --//"
CAPTURE_TIMEOUT = 600  # seconds (full character models are slow to import)
LOG_POLL_INTERVAL = 1  # second

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# ===================================================================
# Maya-side: runs inside the live Maya GUI (imported via commandPort)
# ===================================================================
def run_snapshot(
    log_path: str,
    model_path: str,
    out_png: str,
    width: int = 800,
    height: int = 1066,
    view_transforms=None,
    view_dir=(0.0, 0.05, 1.0),
    light_dir=(0.5, -1.0, 0.5),
) -> None:
    """Import *model_path* with the dx11 shader and playblast one PNG to *out_png*.

    Everything is wrapped so that a completion marker is always written to
    *log_path*; the host tails that file and quits Maya once it appears.
    """
    import math
    import traceback

    import maya.cmds as cmds

    def _log(msg: str) -> None:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(str(msg) + "\n")
        try:
            print(msg)
        except Exception:
            pass

    # Mirror mmd_tools logging into our log file so import failures (which
    # import_mmd_file swallows and turns into a None return) become visible.
    import logging as _logging

    _cap = _logging.FileHandler(log_path, encoding="utf-8")
    _cap.setLevel(_logging.DEBUG)
    _cap.setFormatter(_logging.Formatter("LOG %(levelname)s %(name)s: %(message)s"))
    _logging.getLogger().addHandler(_cap)
    _logging.getLogger().setLevel(_logging.DEBUG)

    saved_backend = None
    try:
        _log("=== snapshot begin ===")
        cmds.file(new=True, force=True)
        try:
            cmds.loadPlugin("dx11Shader", quiet=True)
            _log("dx11Shader plugin loaded")
        except Exception as exc:
            _log(f"WARN loadPlugin dx11Shader: {exc}")

        root = Path(__file__).resolve().parents[2]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))

        from mmd_tools.core import settings
        from mmd_tools.io.mmd_importer import import_mmd_file

        settings.set("import.model.create_mmd_shaders", True)
        saved_backend = settings.get("import.model.mmd_shader_backend", "auto")
        # Deliberately reproduce the stale preference reported by users.  The
        # runtime resolver must select DX11 without rewriting this preference.
        settings.set("import.model.mmd_shader_backend", "glsl")
        _log(f"vp2 device: {cmds.ogs(deviceInformation=True)}")
        _log(f"configured backend before import: {settings.get('import.model.mmd_shader_backend')}")

        _log(f"importing {model_path}")
        import contextlib
        import io as _io

        _buf = _io.StringIO()
        with contextlib.redirect_stdout(_buf), contextlib.redirect_stderr(_buf):
            # Physics import is irrelevant to look/shading and currently aborts
            # this model (angularDamping > 1 + a fatal logging error); skip it so
            # the snapshot reflects the same physics-off config the GUI defaults to.
            root_node = import_mmd_file(str(model_path), options={"import_physics": False})
        _captured = _buf.getvalue()
        if _captured.strip():
            _log("IMPORT OUTPUT >>>\n" + _captured + "\n<<< IMPORT OUTPUT")
        _log(f"imported root: {root_node}")
        if not root_node:
            _log("ERROR: import returned None")
            _log(COMPLETION_MARKER)
            return

        hardware = (cmds.ls(type="dx11Shader") or []) + (cmds.ls(type="GLSLShader") or [])
        inventory = []
        for shader in hardware:
            shader_type = cmds.nodeType(shader)
            effect = cmds.getAttr(f"{shader}.shader") if cmds.attributeQuery("shader", node=shader, exists=True) else ""
            technique = cmds.getAttr(f"{shader}.technique") if cmds.attributeQuery("technique", node=shader, exists=True) else ""
            inventory.append((shader, shader_type, effect, technique))
        _log(f"shader inventory after import: {inventory}")
        _log(f"configured backend after import: {settings.get('import.model.mmd_shader_backend')}")
        if not hardware or any(cmds.nodeType(shader) != "dx11Shader" for shader in hardware):
            raise RuntimeError(f"DirectX11 import created a non-dx11 hardware shader: {inventory}")
        if any(str(effect).lower().endswith(".ogsfx") for _, _, effect, _ in inventory):
            raise RuntimeError(f"DirectX11 import assigned an OGSFX effect: {inventory}")

        # Reproduce an existing-scene mismatch without ever assigning OGSFX to
        # D3DCompiler: swap one imported material to an unconfigured GLSL node,
        # then let the real Presenter Apply path replace it safely.
        from mmd_tools.converters.mesh_converter import _copy_shader_backend_state

        cmds.loadPlugin("glslShader", quiet=True)
        source_shader = hardware[0]
        mismatch = cmds.shadingNode("GLSLShader", asShader=True, name=f"{source_shader}__mismatch")
        _copy_shader_backend_state(source_shader, mismatch)
        destinations = cmds.listConnections(
            f"{source_shader}.outColor", source=False, destination=True, plugs=True
        ) or []
        for destination in destinations:
            if destination.endswith(".surfaceShader"):
                cmds.connectAttr(f"{mismatch}.outColor", destination, force=True)
        old_dx = cmds.rename(source_shader, f"{source_shader}__old_dx11")
        mismatch = cmds.rename(mismatch, source_shader)
        cmds.delete(old_dx)
        _log(f"existing-scene mismatch before Apply: material={mismatch} type={cmds.nodeType(mismatch)} effect=<unset>")

        # Exercise the real Material Presenter path: select one imported MMD
        # material, edit a visible numeric value, then invoke Apply.
        from mmd_tools.ui.application_state import ApplicationState
        from mmd_tools.ui.presenters.material_presenter import MaterialPresenter
        from mmd_tools.ui.tabs.material_tab import MaterialTab

        material_view = MaterialTab()
        material_state = ApplicationState()
        presenter = MaterialPresenter(material_view, material_state)
        material_state.current_model_root = root_node
        presenter.load_materials()
        if material_view.material_list.count() < 1:
            raise RuntimeError("Material Presenter found no imported MMD materials")
        material_view.material_list.setCurrentRow(0)
        before_value = material_view.specular_coefficient_spin.value()
        delta = -0.01 if before_value >= material_view.specular_coefficient_spin.maximum() else 0.01
        material_view.specular_coefficient_spin.setValue(before_value + delta)
        if material_view.specular_coefficient_spin.value() == before_value:
            raise RuntimeError("Material Presenter test edit was clamped and did not change the value")
        presenter.apply_changes()
        applied = presenter.current_material
        applied_type = cmds.nodeType(applied)
        applied_effect = cmds.getAttr(f"{applied}.shader") if cmds.attributeQuery("shader", node=applied, exists=True) else ""
        _log(
            f"Material Presenter Apply: material={applied} type={applied_type} "
            f"effect={applied_effect} value={before_value}->{material_view.specular_coefficient_spin.value()}"
        )
        if applied_type != "dx11Shader" or str(applied_effect).lower().endswith(".ogsfx"):
            raise RuntimeError("Material Presenter Apply violated the DirectX11 shader contract")

        # Copy MMD custom attrs into the VP2-generated effect attrs (GUI dx11Shader
        # only creates DiffuseColorRGB etc. after the .fx is evaluated).
        try:
            from mmd_tools.converters.mesh_converter import sync_dx11_generated_uniforms

            sync_dx11_generated_uniforms()
            _log("synced dx11 generated uniforms")
        except Exception as exc:
            _log(f"uniform sync skipped: {exc}")

        # -- Camera framing from the model bounding box --
        meshes = cmds.listRelatives(root_node, allDescendents=True, type="mesh") or []
        bb = cmds.exactWorldBoundingBox(meshes) if meshes else [-5, -5, -5, 5, 5, 5]
        center = [(bb[0] + bb[3]) / 2, (bb[1] + bb[4]) / 2, (bb[2] + bb[5]) / 2]
        radius = math.sqrt((bb[3] - bb[0]) ** 2 + (bb[4] - bb[1]) ** 2 + (bb[5] - bb[2]) ** 2) * 0.5

        fov = 30.0
        tan_hf = math.tan(math.radians(fov) * 0.5)
        dist = max(radius / (0.7 * tan_hf), radius * 2.0, 5.0)
        v = list(view_dir)
        vlen = math.sqrt(sum(x * x for x in v)) or 1.0
        v = [x / vlen for x in v]
        cam = [center[i] + v[i] * dist for i in range(3)]
        cmds.setAttr("persp.translateX", cam[0])
        cmds.setAttr("persp.translateY", cam[1])
        cmds.setAttr("persp.translateZ", cam[2])
        dx, dy, dz = center[0] - cam[0], center[1] - cam[1], center[2] - cam[2]
        L = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
        cmds.setAttr("persp.rotateX", math.degrees(math.atan2(dy, -dz)))
        cmds.setAttr("persp.rotateY", math.degrees(math.asin(max(-1.0, min(1.0, -dx / L)))))
        cmds.setAttr("persp.rotateZ", 0.0)
        cmds.setAttr("perspShape.focalLength", 18.0 / tan_hf)
        cmds.setAttr("perspShape.nearClipPlane", max(0.01, dist * 0.01))
        cmds.setAttr("perspShape.farClipPlane", dist + radius * 4 + 100)
        _log(f"camera framed: center={center} radius={radius:.2f} dist={dist:.2f}")

        # -- Directional light --
        # Prefer the MMD light controller that import now creates; only add a
        # fallback snapLight when no MMD light exists, so this capture reflects
        # the controller-driven lighting.
        _mmd_light = cmds.ls("*.mmd_light", objectsOnly=True) or []
        if _mmd_light:
            _log(f"using MMD light controller: {_mmd_light[0]} (skipping snapLight)")
        else:
            try:
                lshape = cmds.directionalLight(name="snapLight", intensity=1.0, rgb=(1, 1, 1))
                lx = cmds.listRelatives(lshape, parent=True)[0]
                ld = list(light_dir)
                cmds.setAttr(f"{lx}.rotateX", math.degrees(math.atan2(-ld[1], math.sqrt(ld[0] ** 2 + ld[2] ** 2))))
                cmds.setAttr(f"{lx}.rotateY", math.degrees(math.atan2(ld[0], ld[2])))
                _log(f"light: {lx}")
            except Exception as exc:
                _log(f"light warn: {exc}")

        # -- Clean mesh-only model panel --
        panels = cmds.getPanel(type="modelPanel") or []
        panel = "modelPanel4" if "modelPanel4" in panels else (panels[0] if panels else None)
        if panel:
            cmds.modelEditor(panel, e=True, camera="persp")
            cmds.modelEditor(panel, e=True, rendererName="vp2Renderer")
            cmds.modelEditor(panel, e=True, displayAppearance="smoothShaded", displayTextures=True)
            # Hide rig helpers so only the mesh is captured (keeps polymeshes on).
            cmds.modelEditor(
                panel, e=True, joints=False, locators=False, nurbsCurves=False,
                handles=False, ikHandles=False, deformers=False, dynamics=False,
                follicles=False, cameras=False, lights=False, grid=False,
                headsUpDisplay=False,
            )
            try:
                cmds.setFocus(panel)
            except Exception:
                pass
        _log(f"panel: {panel}")

        cmds.currentTime(0)
        # Deselect so the capture is not covered by the green selection wireframe
        # (import leaves the model selected).
        try:
            cmds.select(clear=True)
        except Exception:
            pass
        try:
            cmds.refresh(force=True)
        except Exception:
            pass
        time.sleep(2.5)  # let VP2 compile the dx11 effect

        out = Path(out_png)
        out.parent.mkdir(parents=True, exist_ok=True)

        # Render one PNG per color-management View Transform so the MMD-faithful
        # un-tone-mapped look can be compared against Maya's default ACES filmic.
        if not view_transforms:
            view_transforms = ["ACES 1.0 SDR-video (sRGB)", "Un-tone-mapped (sRGB)"]
        available = cmds.colorManagementPrefs(q=True, viewTransformNames=True) or []
        try:
            cmds.colorManagementPrefs(e=True, cmEnabled=True)
        except Exception:
            pass

        def _slug(name):
            return "".join(c if c.isalnum() else "_" for c in name).strip("_").lower()[:24]

        for vt in view_transforms:
            if vt not in available:
                _log(f"SKIP view transform (unavailable): {vt}")
                continue
            try:
                cmds.colorManagementPrefs(e=True, viewTransformName=vt)
            except Exception as exc:
                _log(f"set view transform '{vt}' failed: {exc}")
                continue
            variant = out.with_name(f"{out.stem}__{_slug(vt)}.png")
            for old in variant.parent.glob(variant.stem + "*.png"):
                try:
                    old.unlink()
                except Exception:
                    pass
            try:
                cmds.refresh(force=True)
            except Exception:
                pass
            time.sleep(1.0)
            try:
                cmds.playblast(
                    filename=str(variant.with_suffix("")), frame=0, format="image",
                    compression="png", offScreen=True, viewer=False,
                    width=width, height=height, forceOverwrite=True,
                    showOrnaments=False, percent=100,
                )
            except Exception as exc:
                _log(f"playblast failed for '{vt}': {exc}")
                continue
            cand = list(variant.parent.glob(variant.stem + "*.png"))
            actual = max(cand, key=lambda p: p.stat().st_mtime) if cand else None
            size = actual.stat().st_size if actual else 0
            _log(f"OUTPUT_PNG[{vt}]: {actual} size={size}")
        _log(COMPLETION_MARKER)
    except Exception:
        _log("EXCEPTION:\n" + traceback.format_exc())
        _log(COMPLETION_MARKER)
    finally:
        if saved_backend is not None:
            try:
                settings.set("import.model.mmd_shader_backend", saved_backend)
            except Exception:
                _log("WARN: could not restore saved shader backend preference")


# ===================================================================
# Host-side
# ===================================================================
def main() -> int:
    """Launch Maya GUI, drive a single snapshot via commandPort, then quit."""
    import io

    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    ap = argparse.ArgumentParser(description="Single GUI DX11 viewport snapshot")
    ap.add_argument("--maya", default=DEFAULT_MAYA_VERSION)
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", default="build/captures/snapshot.png")
    ap.add_argument("--port", type=int, default=COMMAND_PORT)
    ap.add_argument("--width", type=int, default=800)
    ap.add_argument("--height", type=int, default=1066)
    ap.add_argument("--launch-mode", choices=("direct", "powershell", "explorer"), default="explorer")
    ap.add_argument(
        "--view-transforms",
        default="ACES 1.0 SDR-video (sRGB),Un-tone-mapped (sRGB)",
        help="Comma-separated color-management View Transforms; one PNG is rendered per entry.",
    )
    args = ap.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = (project_root / out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = out_path.parent / "gui_snapshot.log"
    if log_path.exists():
        log_path.unlink()

    model_posix = Path(args.model).resolve().as_posix()
    vt_list = [s.strip() for s in args.view_transforms.split(",") if s.strip()]
    maya_exe = maya_commandport.maya_exe(args.maya)
    logger.info("Maya executable: %s", maya_exe)

    # Capture Maya GUI's native stdout/stderr to files so plugin/MEL/VP2/import
    # errors are not lost (Maya GUI detaches its console on Windows). Their tails
    # are echoed to the CLI on exit -- see the finally block.
    proc = maya_commandport.launch_maya(
        version=args.maya,
        project_root=project_root,
        output_dir=out_path.parent,
        port=args.port,
        launch_mode=args.launch_mode,
        env_overrides={"MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11"},
    )
    maya_out_path = out_path.parent / "maya_stdout.log"
    maya_err_path = out_path.parent / "maya_stderr.log"
    try:
        maya_commandport.wait_for_port(args.port, timeout=120, process=proc)
        logger.info("commandPort :%d open", args.port)

        # -- send the short import+call (proven run_gui_tests.py shape) --
        command = (
            "import sys\n"
            "from pathlib import Path\n"
            f"project_root = Path(r'{project_root.as_posix()}')\n"
            "if str(project_root) not in sys.path:\n"
            "    sys.path.insert(0, str(project_root))\n"
            "\n"
            "from tests.viewport.gui_snapshot import run_snapshot\n"
            f"run_snapshot(r'{log_path.as_posix()}', r'{model_posix}', "
            f"r'{out_path.as_posix()}', {args.width}, {args.height}, "
            f"view_transforms={vt_list!r})\n"
        )
        maya_commandport.send_python(args.port, command, label="<gui-snapshot-command>")
        logger.info("snapshot command sent (%d bytes)", len(command))

        # -- tail the log for the completion marker --
        if not log_path.exists():
            log_path.touch()
        start = time.time()
        done = False
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)
            while time.time() - start < CAPTURE_TIMEOUT:
                line = f.readline()
                if line:
                    print(line, end="")
                    if COMPLETION_MARKER in line:
                        done = True
                        break
                else:
                    time.sleep(LOG_POLL_INTERVAL)
        if not done:
            raise TimeoutError(f"snapshot did not finish within {CAPTURE_TIMEOUT}s")
        gate_log = log_path.read_text(encoding="utf-8", errors="replace")
        forbidden = ("EXCEPTION:", "error X3000", "unrecognized identifier 'mat4'", "effect compile error")
        found = [token for token in forbidden if token.lower() in gate_log.lower()]
        if found:
            raise RuntimeError(f"snapshot semantic gate failed; found {found} in {log_path}")
        logger.info("snapshot finished; PNG at %s", out_path)
        return 0
    finally:
        maya_commandport.quit_maya(args.port)
        try:
            if proc is not None:
                proc.wait(timeout=30)
        except Exception:
            if proc is not None:
                proc.kill()
        # Surface Maya's native stdout/stderr in the CLI so plugin/MEL/VP2 errors
        # are never lost (this is what was invisible before).
        maya_commandport.close_process_logs(proc)
        for label, path in (("MAYA STDOUT", maya_out_path), ("MAYA STDERR", maya_err_path)):
            try:
                text = path.read_text(encoding="utf-8", errors="replace").strip()
            except Exception:
                text = ""
            if text:
                tail = "\n".join(text.splitlines()[-40:])
                print(f"\n===== {label} (tail) =====\n{tail}\n===== end {label} =====")


if __name__ == "__main__":
    raise SystemExit(main())
