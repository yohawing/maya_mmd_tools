"""Validate bundled Maya plugins and runtime DLLs from their release paths."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

MAYA_VERSIONS = ("2024", "2025", "2026", "2027")
CURRENT_ABI_VERSION = 3
REQUIRED_FEATURE_FLAGS = 0x3
RUNTIME_NAME = "mmd_runtime_ffi.dll"
PLUGIN_NAME = "mmd_tools_cpp.mll"


def mayapy_for_version(version: str) -> Path:
    """Resolve mayapy without importing the tests package and initializing Maya early."""
    location = os.environ.get(f"MAYA_LOCATION_{version}") or os.environ.get("MAYA_LOCATION")
    root = Path(location) if location else Path(f"C:/Program Files/Autodesk/Maya{version}")
    return root / "bin" / "mayapy.exe"


def bundled_cases(root: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return the complete release plugin and runtime path matrix."""
    plugin_cases = [
        {
            "name": f"maya-{version}-plugin",
            "maya": version,
            "path": str((root / "plug-ins" / version / "Release" / PLUGIN_NAME).resolve()),
        }
        for version in MAYA_VERSIONS
    ]
    runtime_paths = [root / "mmd_tools" / "native" / "win64" / RUNTIME_NAME]
    runtime_paths.extend(root / "plug-ins" / version / "Release" / RUNTIME_NAME for version in MAYA_VERSIONS)
    runtime_cases = [
        {"name": f"runtime-{index}", "path": str(path.resolve())}
        for index, path in enumerate(runtime_paths)
    ]
    return plugin_cases, runtime_cases


def summarize(results: list[dict[str, object]]) -> dict[str, int | str]:
    """Return a fail-closed summary for bundled release cases."""
    passed = sum(result.get("status") == "pass" for result in results)
    failed = len(results) - passed
    if not results:
        failed = 1
    return {"status": "fail" if failed else "pass", "passed": passed, "failed": failed}


def _runtime_probe(path: Path) -> dict[str, object]:
    """Load one exact DLL and query its ABI and feature contract."""
    if not path.is_file():
        return {"status": "fail", "path": str(path), "detail": "runtime DLL not found"}
    try:
        library = ctypes.WinDLL(str(path), winmode=0x1100)
        library.mmd_runtime_abi_version.restype = ctypes.c_uint32
        library.mmd_runtime_feature_flags.restype = ctypes.c_uint64
        abi = int(library.mmd_runtime_abi_version())
        flags = int(library.mmd_runtime_feature_flags())
    except Exception as exc:
        return {"status": "fail", "path": str(path), "detail": f"load failed: {exc}"}
    loaded_path = _windows_module_path(RUNTIME_NAME)
    expected = os.path.normcase(str(path.resolve()))
    actual = os.path.normcase(str(Path(loaded_path).resolve())) if loaded_path else ""
    ok = (
        abi == CURRENT_ABI_VERSION
        and (flags & REQUIRED_FEATURE_FLAGS) == REQUIRED_FEATURE_FLAGS
        and actual == expected
    )
    return {
        "status": "pass" if ok else "fail",
        "path": str(path),
        "loadedPath": loaded_path,
        "abi": abi,
        "expectedAbi": CURRENT_ABI_VERSION,
        "featureFlags": flags,
        "requiredFeatureFlags": REQUIRED_FEATURE_FLAGS,
    }


def _windows_module_path(name: str) -> str:
    """Return the loaded Windows module path for an exact module basename."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    kernel32.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
    kernel32.GetModuleFileNameW.restype = ctypes.c_uint32
    handle = kernel32.GetModuleHandleW(name)
    if not handle:
        return ""
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel32.GetModuleFileNameW(handle, buffer, len(buffer))
    return buffer.value if length else ""


def _plugin_probe(plugin: Path, expected_version: str) -> dict[str, object]:
    """Load one exact plugin in mayapy and verify its path, version, and runtime."""
    runtime = plugin.parent / RUNTIME_NAME
    if not plugin.is_file() or not runtime.is_file():
        return {
            "status": "fail",
            "path": str(plugin),
            "detail": "plugin or adjacent runtime DLL not found",
        }
    dll_handle = os.add_dll_directory(str(plugin.parent)) if hasattr(os, "add_dll_directory") else None
    try:
        runtime_library = ctypes.WinDLL(str(runtime), winmode=0x1100)
        preloaded_runtime_path = _windows_module_path(RUNTIME_NAME)
        if os.path.normcase(str(Path(preloaded_runtime_path).resolve())) != os.path.normcase(str(runtime.resolve())):
            return {
                "status": "fail",
                "path": str(plugin),
                "detail": "adjacent runtime DLL was not loaded from its fixed path",
                "runtimePath": preloaded_runtime_path,
                "expectedRuntimePath": str(runtime),
            }
        import maya.standalone

        maya.standalone.initialize(name="python")
        from maya import cmds

        loaded_name = cmds.loadPlugin(str(plugin), quiet=True)[0]
        loaded_path = str(cmds.pluginInfo(loaded_name, query=True, path=True))
        version = str(cmds.pluginInfo(loaded_name, query=True, version=True))
        runtime_path = _windows_module_path(RUNTIME_NAME)
        _ = runtime_library
        ok = (
            os.path.normcase(str(Path(loaded_path).resolve())) == os.path.normcase(str(plugin.resolve()))
            and version == expected_version
            and os.path.normcase(str(Path(runtime_path).resolve())) == os.path.normcase(str(runtime.resolve()))
        )
        return {
            "status": "pass" if ok else "fail",
            "path": str(plugin),
            "loadedPath": loaded_path,
            "version": version,
            "expectedVersion": expected_version,
            "runtimePath": runtime_path,
            "expectedRuntimePath": str(runtime),
        }
    except Exception as exc:
        return {"status": "fail", "path": str(plugin), "detail": f"plugin load failed: {exc}"}
    finally:
        if dll_handle is not None:
            dll_handle.close()


def _run_probe(command: list[str], env: dict[str, str] | None = None) -> dict[str, object]:
    """Run an isolated probe and parse its final JSON line."""
    completed = subprocess.run(command, check=False, capture_output=True, text=True, env=env)
    try:
        result = json.loads(completed.stdout.splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        result = {"status": "fail", "detail": "probe produced no JSON result"}
    result["returncode"] = completed.returncode
    if completed.stderr.strip():
        result["stderr"] = completed.stderr.strip()
    if completed.returncode != 0:
        result["status"] = "fail"
    return result


def run(root: Path, expected_version: str) -> dict[str, object]:
    """Run all bundled runtime and Maya-version probes."""
    plugin_cases, runtime_cases = bundled_cases(root)
    results: list[dict[str, object]] = []
    script = Path(__file__).resolve()
    for case in runtime_cases:
        result = _run_probe([sys.executable, str(script), "--probe-runtime", case["path"]])
        results.append({"name": case["name"], **result})
    for case in plugin_cases:
        executable = mayapy_for_version(case["maya"])
        if not executable.is_file():
            results.append({"name": case["name"], "status": "fail", "detail": f"mayapy not found: {executable}"})
            continue
        plugin_dir = str(Path(case["path"]).parent)
        env = os.environ.copy()
        env["PATH"] = os.pathsep.join([plugin_dir, env.get("PATH", "")])
        result = _run_probe(
            [str(executable), str(script), "--probe-plugin", case["path"], "--expected-version", expected_version],
            env=env,
        )
        results.append({"name": case["name"], **result})
    return {"status": summarize(results)["status"], "summary": summarize(results), "results": results}


def write_reports(payload: dict[str, object], json_path: Path, markdown_path: Path) -> None:
    """Write detailed JSON and Markdown evidence for the bundled smoke."""
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = payload["summary"]
    lines = [
        "# Bundled Native Smoke",
        "",
        f"- Status: {payload['status']}",
        f"- Summary: pass={summary['passed']}, fail={summary['failed']}",
        "",
        "| Case | Status | Path | Evidence |",
        "| --- | --- | --- | --- |",
    ]
    for result in payload["results"]:
        evidence = result.get("detail") or f"ABI={result.get('abi', '-')}, version={result.get('version', '-')}, loaded={result.get('loadedPath', result.get('runtimePath', '-'))}"
        lines.append(f"| {result['name']} | {result['status']} | `{result.get('path', '-')}` | {evidence} |")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    parser.add_argument("--expected-version")
    parser.add_argument("--out-json", type=Path)
    parser.add_argument("--out-md", type=Path)
    parser.add_argument("--probe-runtime", type=Path)
    parser.add_argument("--probe-plugin", type=Path)
    args = parser.parse_args()
    if args.probe_runtime:
        result = _runtime_probe(args.probe_runtime.resolve())
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "pass" else 1
    if args.probe_plugin:
        result = _plugin_probe(args.probe_plugin.resolve(), args.expected_version)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "pass" else 1
    started = time.time()
    payload = run(args.root.resolve(), args.expected_version)
    payload["durationSec"] = round(time.time() - started, 3)
    write_reports(payload, args.out_json.resolve(), args.out_md.resolve())
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
