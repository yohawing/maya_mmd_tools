"""Shared host-side helpers for Maya GUI commandPort probes."""

from __future__ import annotations

import os
import platform
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

try:
    from .maya_location import maya_binary
except ImportError:
    from maya_location import maya_binary


def maya_exe(version: str) -> Path:
    """Return the Maya GUI executable for *version*."""
    return maya_binary(version, "maya")


def is_port_open(port: int) -> bool:
    """Return whether a local commandPort is already accepting connections."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            return True
    except OSError:
        return False


def wait_for_port(port: int, timeout: float, process: Optional[subprocess.Popen] = None) -> None:
    """Wait until a local Maya commandPort accepts TCP connections."""
    start = time.time()
    while time.time() - start < timeout:
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"Maya exited before commandPort opened: {process.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return
        except OSError:
            time.sleep(1.0)
    raise TimeoutError(f"Timed out waiting for commandPort :{port}")


def wait_for_port_close(port: int, timeout: float) -> None:
    """Wait until the owned Maya commandPort is no longer accepting connections."""
    start = time.time()
    while time.time() - start < timeout:
        if not is_port_open(port):
            return
        time.sleep(0.25)
    raise TimeoutError(f"Timed out waiting for commandPort :{port} to close")


def send_python(port: int, code: str, label: str = "<maya-commandport>") -> None:
    """Send Python code through commandPort as one compiled exec payload.

    Maya's commandPort bridge is fragile with multi-line raw payloads and
    non-ASCII Windows paths.  Wrapping the script in repr() keeps the TCP payload
    one statement, while compile() gives Script Editor errors a useful label.
    """
    payload = (
        "_mmt_cp_ns = {'__name__': '__maya_commandport__'}; "
        f"exec(compile({code!r}, {label!r}, 'exec'), _mmt_cp_ns, _mmt_cp_ns)\n"
    )
    with socket.create_connection(("127.0.0.1", port), timeout=10.0) as sock:
        sock.sendall(payload.encode("utf-8"))
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def quit_maya(port: int) -> None:
    """Ask Maya to quit through commandPort."""
    try:
        send_python(port, "import maya.cmds as cmds\ncmds.quit(force=True)\n", label="<maya-quit>")
    except OSError:
        pass


def remove_stale_logs(paths: list[Path], retries: int = 5) -> None:
    """Delete stale output files before sending Maya-side work."""
    for path in paths:
        for attempt in range(retries):
            try:
                path.unlink()
                break
            except FileNotFoundError:
                break
            except PermissionError:
                if attempt == retries - 1:
                    raise
                time.sleep(0.2)


def tail_until_marker(log_path: Path, marker: str, timeout: float) -> bool:
    """Print *log_path* until *marker* appears without creating the file first."""
    start = time.time()
    while not log_path.exists() and time.time() - start < timeout:
        time.sleep(0.5)
    if not log_path.exists():
        return False
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        while time.time() - start < timeout:
            line = handle.readline()
            if line:
                print(line, end="")
                if marker in line:
                    return True
            else:
                time.sleep(0.5)
    return False


def _prepend_env_path(env: dict[str, str], name: str, path: Path) -> None:
    existing = env.get(name)
    env[name] = str(path) if not existing else f"{path}{os.pathsep}{existing}"


def launch_maya(
    *,
    version: str,
    project_root: Path,
    output_dir: Path,
    port: int,
    launch_mode: str = "explorer" if platform.system() == "Windows" else "direct",
    env_overrides: Optional[dict[str, str]] = None,
) -> Optional[subprocess.Popen]:
    """Launch Maya GUI with a Python commandPort.

    On Windows, ``explorer`` opens a temporary BAT that starts Maya with a MEL
    commandPort script. This detaches Maya from the automation console and is
    the stable local route for Autodesk license checkout.
    """
    executable = maya_exe(version)
    if not executable.is_file():
        raise FileNotFoundError(f"maya.exe not found: {executable}")

    output_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    _prepend_env_path(env, "PYTHONPATH", project_root)
    _prepend_env_path(env, "MAYA_MODULE_PATH", project_root)
    if env_overrides:
        env.update(env_overrides)

    if platform.system() == "Windows" and launch_mode == "direct":
        raise ValueError(
            'launch_mode="direct" is unsupported on Windows; use "explorer" '
            "to avoid Maya license-checkout exit 253"
        )

    command = [str(executable), "-command", f'commandPort -name ":{port}" -sourceType "python";']
    if platform.system() == "Windows" and launch_mode == "explorer":
        mel_path = (output_dir / f"commandport_{port}.mel").resolve()
        bat_path = (output_dir / f"launch_maya_{version}_{port}.bat").resolve()
        mel_path.write_text(f'commandPort -name ":{port}" -sourceType "python";\n', encoding="utf-8")
        env_lines = [f'set "{name}={env[name]}"' for name in ("PYTHONPATH", "MAYA_MODULE_PATH")]
        env_lines.extend(f'set "{name}={value}"' for name, value in (env_overrides or {}).items())
        bat_lines = [
            "@echo off",
            *env_lines,
            f'start "" /D "{project_root}" "{executable}" -script "{mel_path}"',
        ]
        bat_path.write_text("\r\n".join(bat_lines) + "\r\n", encoding="utf-8")
        # Explorer may return 1 even after successfully opening the BAT (the
        # stable signal is the commandPort becoming reachable, not its status).
        subprocess.run(["explorer.exe", str(bat_path)], cwd=str(project_root), check=False)
        return None
    if platform.system() == "Windows" and launch_mode == "powershell":
        escaped_args = "@(" + ", ".join("'" + arg.replace("'", "''") + "'" for arg in command[1:]) + ")"
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"Start-Process -FilePath '{str(executable).replace(chr(39), chr(39) + chr(39))}' -ArgumentList {escaped_args}",
            ],
            cwd=str(project_root),
            check=True,
            env=env,
        )
        return None

    stdout = (output_dir / "maya_stdout.log").open("w", encoding="utf-8", errors="replace")
    stderr = (output_dir / "maya_stderr.log").open("w", encoding="utf-8", errors="replace")
    process = subprocess.Popen(command, cwd=str(project_root), env=env, stdout=stdout, stderr=stderr)
    process._mmt_stdout = stdout  # type: ignore[attr-defined]
    process._mmt_stderr = stderr  # type: ignore[attr-defined]
    return process


def close_process_logs(process: Optional[subprocess.Popen]) -> None:
    """Close log handles attached by launch_maya()."""
    if process is None:
        return
    for attr in ("_mmt_stdout", "_mmt_stderr"):
        handle = getattr(process, attr, None)
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass
