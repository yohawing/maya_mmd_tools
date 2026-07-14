"""
Library discovery and load cache for the mmd-anim runtime FFI.

The high-level runtime wrapper delegates CDLL discovery and signature binding
here so handle classes do not also own platform-specific binary lookup state.
"""

from __future__ import annotations

import ctypes
import os
import platform
from ctypes import CDLL
from pathlib import Path
from typing import List, Optional, Union

from mmd_tools.core.logger import get_logger
from mmd_tools.core.native.mmd_anim_runtime_signatures import setup_function_signatures

logger = get_logger(__name__)

MMD_RUNTIME_ABI_VERSION = 2

if platform.system() == "Windows":
    _LIB_NAMES = ["mmd_runtime_ffi.dll", "mmd_anim_ffi.dll"]
elif platform.system() == "Darwin":
    _LIB_NAMES = [
        "libmmd_runtime_ffi.dylib",
        "mmd_runtime_ffi.dylib",
        "libmmd_anim_ffi.dylib",
        "mmd_anim_ffi.dylib",
    ]
else:
    _LIB_NAMES = ["libmmd_runtime_ffi.so", "mmd_runtime_ffi.so", "libmmd_anim_ffi.so", "mmd_anim_ffi.so"]

_THIS_FILE = Path(__file__).resolve()
_PACKAGE_ROOT = _THIS_FILE.parents[2]

_CANDIDATE_PATHS: List[Optional[Path]] = [
    Path(os.environ.get("MMD_ANIM_FFI_PATH", "")) if os.environ.get("MMD_ANIM_FFI_PATH") else None,
    _PACKAGE_ROOT.parent / "external" / "mmd-anim" / "target" / "release",
    _PACKAGE_ROOT / "native" / ("win64" if platform.system() == "Windows" else "macos" if platform.system() == "Darwin" else "linux"),
    Path.cwd(),
    Path("plug-ins"),
]

_runtime_lib: Union[Optional[CDLL], bool] = None
_runtime_lib_path: Optional[Path] = None


def find_library() -> Optional[Path]:
    """Find the mmd-anim FFI shared library from configured candidate paths."""
    for raw_base in _CANDIDATE_PATHS:
        if raw_base is None:
            continue
        base = Path(raw_base)
        if not base.exists():
            continue

        if base.is_file():
            if base.name in _LIB_NAMES:
                return base.resolve()
            continue

        for name in _LIB_NAMES:
            candidate = base / name
            if candidate.exists():
                return candidate.resolve()
            for sub in ("", "debug", "release"):
                c2 = (base / sub / name) if sub else candidate
                if c2.exists():
                    return c2.resolve()

    return None


def get_mmd_runtime_library() -> Optional[CDLL]:
    """Return the cached runtime CDLL, loading and binding it when needed."""
    global _runtime_lib, _runtime_lib_path

    if _runtime_lib is not None:
        return _runtime_lib if _runtime_lib is not False else None

    path = find_library()
    if path is None:
        logger.info("mmd-anim runtime library was not found (check prebuilt binary placement)")
        _runtime_lib = False
        return None

    try:
        lib = ctypes.CDLL(str(path))
        setup_function_signatures(lib)

        abi = lib.mmd_runtime_abi_version()
        if abi != MMD_RUNTIME_ABI_VERSION:
            logger.error(
                "Rejected mmd-anim runtime library due to ABI version mismatch: path=%s, got=%s, expected=%s",
                path,
                abi,
                MMD_RUNTIME_ABI_VERSION,
            )
            _runtime_lib = False
            _runtime_lib_path = None
            return None
        else:
            logger.info("Loaded mmd-anim runtime library: %s (ABI %s)", path, abi)

        _runtime_lib = lib
        _runtime_lib_path = path
        return lib
    except Exception as exc:
        logger.error("Failed to load mmd-anim runtime library: %s - %s", path, exc, exc_info=True)
        _runtime_lib = False
        return None


def is_mmd_runtime_available() -> bool:
    """Return True when the mmd-anim runtime library can be loaded."""
    return get_mmd_runtime_library() is not None


def get_runtime_library_path() -> Optional[Path]:
    """Return the loaded runtime library path, triggering load discovery."""
    get_mmd_runtime_library()
    return _runtime_lib_path
