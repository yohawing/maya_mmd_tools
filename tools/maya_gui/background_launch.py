"""Keep one test-owned Maya behind the user's foreground window.

Executed by the Explorer-side hidden launcher, not inside Maya. Windows focus
requests can still race the guard; every detected takeover is recorded.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from contextlib import ExitStack
from pathlib import Path
import socket
import subprocess
import sys
import time


class Windows:
    """Small Win32 boundary; handles are pointer-sized on 64-bit Windows."""

    def __init__(self):
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        self.callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        signatures = {
            "GetForegroundWindow": ([], wintypes.HWND),
            "GetWindowThreadProcessId": ([wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            "EnumWindows": ([self.callback_type, wintypes.LPARAM], wintypes.BOOL),
            "GetWindowLongW": ([wintypes.HWND, ctypes.c_int], wintypes.LONG),
            "SetWindowLongW": ([wintypes.HWND, ctypes.c_int, wintypes.LONG], wintypes.LONG),
            "SetWindowPos": ([wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT], wintypes.BOOL),
            "SetForegroundWindow": ([wintypes.HWND], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes = arguments
            function.restype = result

    def foreground(self):
        return self.api.GetForegroundWindow()

    def pid(self, hwnd):
        pid = wintypes.DWORD()
        self.api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def windows(self):
        handles = []

        @self.callback_type
        def collect(hwnd, _):
            handles.append(hwnd)
            return True

        if not self.api.EnumWindows(collect, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return handles

    def background(self, hwnd):
        # WS_EX_NOACTIVATE and SWP_NOACTIVATE. Keep the render surface visible
        # and its size/position intact; only send the owned window behind others.
        style = self.api.GetWindowLongW(hwnd, -20)
        ctypes.set_last_error(0)
        previous = self.api.SetWindowLongW(hwnd, -20, style | 0x08000000)
        error = ctypes.get_last_error()
        if not previous and error:
            raise ctypes.WinError(error)
        if not self.api.SetWindowPos(hwnd, 1, 0, 0, 0, 0, 0x0010 | 0x0001 | 0x0002 | 0x0200 | 0x4000):
            raise ctypes.WinError(ctypes.get_last_error())

    def restore(self, hwnd):
        return bool(self.api.SetForegroundWindow(hwnd))


class FocusGuard:
    """Touch only the child PID; follow the user's latest foreground window."""

    def __init__(self, windows, pid, foreground, record):
        self.windows = windows
        self.pid = pid
        self.previous = foreground
        self.record = record
        self.prepared = set()

    def tick(self):
        owned = {hwnd for hwnd in self.windows.windows() if self.windows.pid(hwnd) == self.pid}
        self.prepared.intersection_update(owned)
        for hwnd in owned - self.prepared:
            try:
                self.windows.background(hwnd)
            except OSError:
                if self.windows.pid(hwnd) != self.pid:
                    continue  # A transient startup window closed during enumeration.
                raise
            self.prepared.add(hwnd)
            self.record("window", hwnd=hwnd)
        foreground = self.windows.foreground()
        if foreground and self.windows.pid(foreground) != self.pid:
            self.previous = foreground
        elif foreground and self.previous and self.windows.pid(self.previous) not in (0, self.pid):
            restored = self.windows.restore(self.previous)
            self.record("focus_takeover", hwnd=foreground, restored=restored)


class OwnerProcess:
    """Hold the runner's process handle so PID reuse cannot extend its lifetime."""

    def __init__(self, pid):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.api.OpenProcess.restype = wintypes.HANDLE
        self.api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.api.WaitForSingleObject.restype = wintypes.DWORD
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handle = self.api.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def alive(self):
        result = self.api.WaitForSingleObject(self.handle, 0)
        if result == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        return result == 258  # WAIT_TIMEOUT

    def close(self):
        self.api.CloseHandle(self.handle)


def stop_owned_process(process, port, record, timeout=30):
    """Request quit only in this child, then wait or terminate its Popen handle."""
    if process.poll() is not None:
        return
    # A reused/occupied port must never quit another Maya. Check inside Maya,
    # as well as retaining the original Popen handle for forced termination.
    payload = (
        "import os; import maya.cmds as cmds; "
        f"cmds.quit(force=True) if os.getpid() == {process.pid} else None\n"
    )
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
            sock.sendall(payload.encode("utf-8"))
        record("quit_requested", pid=process.pid)
    except OSError as error:
        record("quit_unavailable", message=str(error))
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        record("forced_termination", pid=process.pid)
        process.terminate()
        process.wait(timeout=10)
    record("exited", returncode=process.returncode)


def main(config_path):
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    out = Path(config["output"])
    with (out / "background-launch.jsonl").open("w", encoding="utf-8", buffering=1) as log:
        def record(event, **fields):
            log.write(json.dumps({"event": event, "time": time.time(), **fields}) + "\n")

        keep_open = config.get("keep_open", False)
        with ExitStack() as stack:
            # Legacy configs must opt into preview mode or supply an owner;
            # never guess ownership from an existing Maya process or port.
            owner = None
            if not keep_open:
                owner = OwnerProcess(config["owner_pid"])
                stack.callback(owner.close)
                if not owner.alive():
                    return 0
            return run_child(config, record, owner, keep_open)


def run_child(config, record, owner, keep_open):
    """Supervise one new child; explicitly retained previews outlive the runner."""
    out = Path(config["output"])
    process = None
    try:
        windows = Windows()
        foreground = windows.foreground()
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 4  # SW_SHOWNOACTIVATE, not hidden/minimized.
        env = os.environ.copy()
        env.update(config["env"])
        with (out / "maya_stdout.log").open("w", encoding="utf-8") as stdout, (
            out / "maya_stderr.log"
        ).open("w", encoding="utf-8") as stderr:
            process = subprocess.Popen(config["command"], cwd=config["cwd"], env=env,
                                       startupinfo=startup, stdout=stdout, stderr=stderr)
            record("started", pid=process.pid)
            guard = FocusGuard(windows, process.pid, foreground, record)
            while process.poll() is None:
                if owner is not None and not owner.alive():
                    record("owner_exited")
                    break
                guard.tick()
                time.sleep(0.05)
            if process.returncode is not None:
                record("exited", returncode=process.returncode)
    except BaseException as error:
        record("error", message=str(error))
        raise
    finally:
        if process is not None and not keep_open:
            stop_owned_process(process, config["port"], record)
    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
