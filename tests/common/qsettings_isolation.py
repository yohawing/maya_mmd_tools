"""Process-level QSettings isolation for UI test runners.

Windows uses the native registry for ``QSettings(organization, application)``
even after ``setDefaultFormat(IniFormat)``.  UI tests therefore need a
test-owned constructor boundary, not a best-effort snapshot/restore of the
user store.  This module is imported by the runner before production widgets
are constructed and redirects organization/application stores to a temporary
INI directory for the lifetime of the process.
"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple

_PRODUCTION_SCOPES: Tuple[Tuple[str, str], ...] = (
    ("yohawing", "maya_mmd_tools"),
    ("maya_mmd_tools", "ImportExportTab"),
)
_ACTIVE = None


def _stable_host_value(value: Any) -> Any:
    """Normalize registry/plist values without importing Qt."""

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, (list, tuple)):
        return [_stable_host_value(item) for item in value]
    return repr(value)


def _read_registry_key(key, prefix=""):
    """Read one QSettings registry subtree using only the Windows stdlib."""

    import winreg

    values = {}
    value_count, subkey_count, _ = winreg.QueryInfoKey(key)
    for index in range(value_count):
        try:
            name, value, kind = winreg.EnumValue(key, index)
        except OSError as exc:
            # A concurrent native-settings writer can invalidate the count
            # between QueryInfoKey and enumeration.  Treat the end marker as
            # an empty tail, while surfacing other read failures to the host.
            if getattr(exc, "winerror", None) == 259:
                break
            raise
        values[f"{prefix}value:{name}"] = {
            "kind": kind,
            "value": _stable_host_value(value),
        }
    for index in range(subkey_count):
        try:
            name = winreg.EnumKey(key, index)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 259:
                break
            raise
        with winreg.OpenKey(key, name, 0, winreg.KEY_READ) as child:
            values.update(_read_registry_key(child, f"{prefix}{name}/"))
    return values


def _host_native_scope_payload(organization: str, application: str):
    """Read a native QSettings scope without loading PySide or Qt."""

    if os.name == "nt":
        import winreg

        registry_path = rf"Software\{organization}\{application}"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_path, 0, winreg.KEY_READ) as key:
                return _read_registry_key(key)
        except FileNotFoundError:
            return {}

    if sys.platform == "darwin":
        # Qt's macOS NativeFormat uses the reverse-DNS preference domain for
        # these organization/application names.
        path = Path.home() / "Library" / "Preferences" / f"com.{organization}.{application}.plist"
        if not path.exists():
            return {}
        raw = path.read_bytes()
        try:
            return _stable_host_value(plistlib.loads(raw))
        except (plistlib.InvalidFileException, ValueError, TypeError):
            return {"raw": raw.hex()}

    # This fallback keeps the host probe dependency-free for CI on Linux.  It
    # is not used for the supported Windows native registry boundary, but
    # allows the runner's report tests to execute on all development hosts.
    candidates = (
        Path.home() / ".config" / organization / f"{application}.conf",
        Path.home() / ".config" / organization / f"{application}.ini",
        Path.home() / ".config" / f"{organization}.{application}.conf",
    )
    return {
        str(path): path.read_bytes().hex()
        for path in candidates
        if path.is_file()
    }


def host_native_qsettings_fingerprints(
    scopes: Tuple[Tuple[str, str], ...] = _PRODUCTION_SCOPES,
) -> Dict[str, str]:
    """Fingerprint native user stores without requiring PySide/Maya."""

    fingerprints: Dict[str, str] = {}
    for organization, application in scopes:
        payload = _host_native_scope_payload(organization, application)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        fingerprints[f"{organization}/{application}"] = hashlib.sha256(
            encoded.encode("utf-8")
        ).hexdigest()
    return fingerprints


def _safe_component(value: str) -> str:
    """Return a filesystem-safe component while retaining readable names."""

    component = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")
    return component or "default"


def _store_path(root: Path, organization: str, application: str) -> Path:
    """Return the isolated INI path for one organization/application pair."""

    directory = root / _safe_component(organization)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / (_safe_component(application) + ".ini")


def _is_organization_application_constructor(args: tuple, kwargs: Mapping[str, Any]) -> bool:
    """Identify the production ``QSettings(org, app[, parent])`` overload."""

    if any(key != "parent" for key in kwargs) or len(args) < 2:
        return False
    return isinstance(args[0], str) and isinstance(args[1], str)


def _build_redirected_qsettings(original, root: Path):
    """Build a transparent constructor shim around the real Qt QSettings type."""

    class RedirectedQSettings:
        """Keep Qt's API constants while routing org/app stores to INI files."""

        NativeFormat = original.NativeFormat
        IniFormat = original.IniFormat
        UserScope = original.UserScope
        SystemScope = original.SystemScope

        def __new__(cls, *args, **kwargs):
            if _is_organization_application_constructor(args, kwargs):
                organization, application = args[:2]
                path = _store_path(root, organization, application)
                parent = kwargs.get("parent", args[2] if len(args) > 2 else None)
                if parent is None:
                    return original(str(path), original.IniFormat)
                return original(str(path), original.IniFormat, parent)
            if not args and not kwargs:
                return original(str(root / "default.ini"), original.IniFormat)
            return original(*args, **kwargs)

        @staticmethod
        def setDefaultFormat(format_value):
            return original.setDefaultFormat(format_value)

        @staticmethod
        def defaultFormat():
            return original.defaultFormat()

        @staticmethod
        def setPath(format_value, scope, path):
            return original.setPath(format_value, scope, path)

        @staticmethod
        def registerFormat(*args, **kwargs):
            return original.registerFormat(*args, **kwargs)

    RedirectedQSettings.__name__ = "QSettings"
    RedirectedQSettings.__qualname__ = "QSettings"
    return RedirectedQSettings


def activate_qsettings_isolation():
    """Activate the temporary QSettings backend once per test process.

    The process intentionally keeps the redirected backend active.  Restoring
    the native constructor before Maya/Qt teardown would reopen a race where a
    late signal or destructor writes the user's registry after a test fails.
    A killed process can only leave files below the OS temporary directory.
    """

    global _ACTIVE
    if _ACTIVE is not None:
        return _ACTIVE

    # Keep this import inside activation: the outer host runner must be able
    # to fingerprint Windows stores before Maya/PySide is available.
    from mmd_tools.ui import qt_compat

    original = qt_compat.QSettings
    root = Path(tempfile.mkdtemp(prefix="maya_mmd_tools_qsettings_"))
    user_path = root / "user"
    system_path = root / "system"
    user_path.mkdir()
    system_path.mkdir()

    # Explicit org/app constructors are native on Windows, so setPath alone
    # is insufficient.  Keep both the default format and both fallback scopes
    # pointed at test-owned directories for any non-production constructor too.
    original.setDefaultFormat(original.IniFormat)
    original.setPath(original.IniFormat, original.UserScope, str(user_path))
    original.setPath(original.IniFormat, original.SystemScope, str(system_path))
    redirected = _build_redirected_qsettings(original, root)

    # Patch references already imported by a runner, while qt_compat covers
    # production modules imported after this bootstrap.  This remains test
    # infrastructure only; production files and their signal paths are intact.
    for module_name in (
        "mmd_tools.ui.qt_compat",
        "mmd_tools.ui.import_export_view_state",
        "mmd_tools.ui.main_window",
        "mmd_tools.ui.animator_toolset_window",
    ):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "QSettings"):
            setattr(module, "QSettings", redirected)

    qt_compat.QSettings = redirected
    _ACTIVE = {
        "root": root,
        "user_path": user_path,
        "system_path": system_path,
        "original": original,
        "redirected": redirected,
    }
    return _ACTIVE


def isolated_settings_store(organization: str, application: str):
    """Return the real Qt store used by the redirected constructor."""

    state = activate_qsettings_isolation()
    return state["original"](
        str(_store_path(state["root"], organization, application)),
        state["original"].IniFormat,
    )


def reset_isolated_qsettings() -> None:
    """Clear test-owned settings between GUI batch cases.

    This reset is deliberately separate from native-store safety: the native
    backend remains redirected for the whole process, while each case starts
    with an empty temporary store so one case cannot observe another's data.
    """

    root = activate_qsettings_isolation()["root"]
    for path in root.rglob("*"):
        if path.is_file():
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def isolated_qsettings_root() -> Path:
    """Return the active temporary root for runner diagnostics and tests."""

    return activate_qsettings_isolation()["root"]
