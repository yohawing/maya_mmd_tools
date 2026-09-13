"""Keep one test-owned Maya behind the user's foreground window.

Executed by the Explorer-side hidden launcher, not inside Maya. Windows focus
requests can still race the guard; every detected takeover is recorded.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
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


def main(config_path):
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    out = Path(config["output"])
    with (out / "background-launch.jsonl").open("w", encoding="utf-8", buffering=1) as log:
        def record(event, **fields):
            log.write(json.dumps({"event": event, "time": time.time(), **fields}) + "\n")

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
                    guard.tick()
                    time.sleep(0.05)
                record("exited", returncode=process.returncode)
                return process.returncode
        except Exception as error:
            record("error", message=str(error))
            # Never leave a launched test running after its guard has failed.
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            raise


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
