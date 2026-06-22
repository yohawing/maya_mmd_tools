"""E2E verification: native rig primitive integration in Maya.

Host-side (any Python): launch Maya GUI with commandPort -> send test -> tail log.
Maya-side (run_e2e_check): import PMX -> verify native rig path was taken.

Usage:
    python tests/viewport/e2e_native_rig.py --maya 2026 --model "path/to/model.pmx"
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

COMMAND_PORT = 7724
COMPLETION_MARKER = "//-- NATIVE_RIG_E2E_DONE --//"
MAYA_START_TIMEOUT = 120
TEST_TIMEOUT = 300
LOG_POLL_INTERVAL = 1

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ===================================================================
# Maya-side: runs inside the live Maya GUI
# ===================================================================
def run_e2e_check(log_path: str, model_path: str) -> None:
    import traceback

    import maya.cmds as cmds

    def _log(msg: str) -> None:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(str(msg) + "\n")
        try:
            print(msg)
        except Exception:
            pass

    results = {
        "status": "error",
        "rig_primitive_available": False,
        "native_rig_used": False,
        "ik_chain_count": 0,
        "append_solver_count": 0,
        "native_ik_joints": [],
        "native_append_joints": [],
        "grant_constraints": 0,
        "ik_handles": 0,
        "manifest_bone_count": 0,
        "manifest_ik_count": 0,
        "manifest_grant_count": 0,
        "joint_count": 0,
        "errors": [],
    }

    try:
        _log("=== Native Rig E2E Check ===")

        # 1. Check DLL availability
        from mmd_tools.core.native import is_rig_primitive_available
        avail = is_rig_primitive_available()
        results["rig_primitive_available"] = avail
        _log(f"is_rig_primitive_available: {avail}")

        if not avail:
            results["errors"].append("rig primitive DLL not available in Maya")
            _log("FAIL: DLL not available")
            _log(f"RESULT_JSON: {json.dumps(results)}")
            _log(COMPLETION_MARKER)
            return

        # 2. Test MmdRigSpec directly
        from mmd_tools.core.native import MmdRigSpec
        pmx_bytes = Path(model_path).read_bytes()
        spec = MmdRigSpec.from_pmx_bytes(pmx_bytes)
        if spec is None:
            results["errors"].append("MmdRigSpec.from_pmx_bytes returned None")
            _log("FAIL: rig spec creation failed")
        else:
            manifest = spec.manifest_json()
            if manifest:
                results["manifest_bone_count"] = manifest.get("boneCount", 0)
                results["manifest_ik_count"] = manifest.get("ikChainCount", 0)
                results["manifest_grant_count"] = manifest.get("grantCount", 0)
                _log(f"manifest: bones={results['manifest_bone_count']}, "
                     f"IK={results['manifest_ik_count']}, grants={results['manifest_grant_count']}")
            spec.free()

        # 3. Import PMX and verify native rig path
        _log(f"importing: {model_path}")
        cmds.file(new=True, force=True)

        from mmd_tools.io.mmd_importer import import_mmd_file
        result = import_mmd_file(model_path)

        if result is None:
            results["errors"].append("import_mmd_file returned None")
            _log("FAIL: import returned None")
        else:
            _log(f"import result type: {type(result)}")

        # 4. Check joints for native rig metadata
        joints = cmds.ls(type="joint")
        results["joint_count"] = len(joints)
        _log(f"joints created: {len(joints)}")

        native_ik = []
        native_append = []
        for j in joints:
            if cmds.attributeQuery("mmd_ik_native", node=j, exists=True):
                if cmds.getAttr(f"{j}.mmd_ik_native"):
                    native_ik.append(j)
            if cmds.attributeQuery("mmd_append_native", node=j, exists=True):
                if cmds.getAttr(f"{j}.mmd_append_native"):
                    native_append.append(j)

        results["native_ik_joints"] = native_ik
        results["native_append_joints"] = native_append
        _log(f"native IK joints: {len(native_ik)} {native_ik}")
        _log(f"native append joints: {len(native_append)} {native_append}")

        # 5. Check DG constraints (grant constraints + IK handles)
        orient_constraints = cmds.ls(type="orientConstraint") or []
        point_constraints = cmds.ls(type="pointConstraint") or []
        grant_constraints = [
            c for c in orient_constraints + point_constraints
            if cmds.attributeQuery("mmd_grant_constraint", node=c, exists=True)
        ]
        results["grant_constraints"] = len(grant_constraints)
        _log(f"grant constraints: {len(grant_constraints)}")

        ik_handles = cmds.ls(type="ikHandle") or []
        native_ik_handles = [
            h for h in ik_handles
            if cmds.attributeQuery("mmd_ik_native_handle", node=h, exists=True)
        ]
        results["ik_handles"] = len(native_ik_handles)
        _log(f"native IK handles: {len(native_ik_handles)} {native_ik_handles}")

        # 6. Determine pass/fail
        native_used = len(native_ik) > 0 or len(native_append) > 0
        has_dg = len(grant_constraints) > 0 or len(native_ik_handles) > 0
        results["native_rig_used"] = native_used
        results["ik_chain_count"] = len(native_ik)
        results["append_solver_count"] = len(native_append)

        if native_used and has_dg:
            results["status"] = "pass"
            _log("PASS: native rig path used with DG constraints + IK handles")
        elif native_used and not has_dg:
            results["status"] = "warn"
            results["errors"].append("native metadata present but no DG constraints created")
            _log("WARN: native metadata without DG constraints")
        elif not native_used and has_dg:
            results["status"] = "fallback"
            _log("INFO: Python constraint fallback path used")
        else:
            results["status"] = "empty"
            _log("INFO: no rig constraints at all (model may have no IK/grants)")

        _log(f"RESULT_JSON: {json.dumps(results)}")
        _log(COMPLETION_MARKER)

    except Exception:
        results["errors"].append(traceback.format_exc())
        _log(f"EXCEPTION:\n{traceback.format_exc()}")
        _log(f"RESULT_JSON: {json.dumps(results)}")
        _log(COMPLETION_MARKER)


# ===================================================================
# Host-side
# ===================================================================
def _find_maya(version: str) -> str:
    loc = os.environ.get(f"MAYA_LOCATION_{version}") or os.environ.get("MAYA_LOCATION")
    cands = []
    if loc:
        cands.append(Path(loc) / "bin" / "maya.exe")
    for base in [
        os.environ.get("ProgramFiles", "C:/Program Files"),
        os.environ.get("ProgramW6432", "C:/Program Files"),
    ]:
        cands.append(Path(base) / f"Autodesk/Maya{version}" / "bin" / "maya.exe")
    for c in cands:
        if c.is_file():
            return str(c)
    raise FileNotFoundError(f"Maya {version} not found")


def main() -> int:
    import io
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    ap = argparse.ArgumentParser(description="E2E native rig primitive check")
    ap.add_argument("--maya", default="2026")
    ap.add_argument("--model", required=True)
    ap.add_argument("--port", type=int, default=COMMAND_PORT)
    args = ap.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    log_dir = project_root / "build" / "e2e"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "native_rig_e2e.log"
    if log_path.exists():
        log_path.unlink()

    model_posix = Path(args.model).resolve().as_posix()
    maya_exe = _find_maya(args.maya)
    logger.info("Maya: %s", maya_exe)

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{project_root};{env.get('PYTHONPATH', '')}"

    maya_out = log_dir / "maya_stdout.log"
    maya_err = log_dir / "maya_stderr.log"
    out_fh = open(maya_out, "w", encoding="utf-8", errors="replace")
    err_fh = open(maya_err, "w", encoding="utf-8", errors="replace")

    proc = subprocess.Popen(
        [maya_exe, "-command", f'commandPort -name ":{args.port}" -sourceType "python";'],
        env=env, stdout=out_fh, stderr=err_fh,
    )

    try:
        start = time.time()
        opened = False
        while time.time() - start < MAYA_START_TIMEOUT:
            if proc.poll() is not None:
                raise RuntimeError(f"Maya exited early ({proc.returncode})")
            try:
                with socket.create_connection(("127.0.0.1", args.port), timeout=1):
                    opened = True
                    break
            except (socket.timeout, ConnectionRefusedError):
                time.sleep(1)
        if not opened:
            raise TimeoutError(f"commandPort :{args.port} never opened")
        logger.info("commandPort :%d ready", args.port)

        command = (
            "import sys\n"
            "from pathlib import Path\n"
            f"project_root = Path(r'{project_root.as_posix()}')\n"
            "if str(project_root) not in sys.path:\n"
            "    sys.path.insert(0, str(project_root))\n"
            "\n"
            "from tests.viewport.e2e_native_rig import run_e2e_check\n"
            f"run_e2e_check(r'{log_path.as_posix()}', r'{model_posix}')\n"
        )
        with socket.create_connection(("127.0.0.1", args.port), timeout=10) as sock:
            sock.sendall(command.encode("utf-8"))
        logger.info("command sent (%d bytes)", len(command))

        if not log_path.exists():
            log_path.touch()
        start = time.time()
        done = False
        result_json = None
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)
            while time.time() - start < TEST_TIMEOUT:
                line = f.readline()
                if line:
                    print(line, end="")
                    if line.strip().startswith("RESULT_JSON:"):
                        result_json = json.loads(line.strip().split("RESULT_JSON:", 1)[1])
                    if COMPLETION_MARKER in line:
                        done = True
                        break
                else:
                    time.sleep(LOG_POLL_INTERVAL)

        if not done:
            raise TimeoutError(f"E2E check did not finish within {TEST_TIMEOUT}s")

        if result_json:
            logger.info("=== RESULT ===")
            logger.info("status: %s", result_json.get("status"))
            logger.info("native rig used: %s", result_json.get("native_rig_used"))
            logger.info("IK joints: %d", len(result_json.get("native_ik_joints", [])))
            logger.info("append joints: %d", len(result_json.get("native_append_joints", [])))
            logger.info("Python constraints: %d", result_json.get("python_grant_constraints", -1))
            if result_json.get("errors"):
                for e in result_json["errors"]:
                    logger.error("  error: %s", e[:200])
            return 0 if result_json.get("status") in ("pass", "empty") else 1
        return 1

    finally:
        try:
            with socket.create_connection(("127.0.0.1", args.port), timeout=5) as sock:
                sock.sendall(b"import maya.cmds as cmds; cmds.quit(force=True)\n")
        except Exception:
            pass
        time.sleep(3)
        if proc.poll() is None:
            proc.terminate()
        out_fh.close()
        err_fh.close()

        for lf in [maya_out, maya_err]:
            if lf.exists() and lf.stat().st_size > 0:
                lines = lf.read_text(encoding="utf-8", errors="replace").splitlines()
                tail = lines[-20:] if len(lines) > 20 else lines
                logger.info("--- %s (last %d lines) ---", lf.name, len(tail))
                for ln in tail:
                    print(f"  {ln}")


if __name__ == "__main__":
    sys.exit(main())
