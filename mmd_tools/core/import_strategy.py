"""Import path strategy resolution for model and VMD animation imports."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from mmd_tools.core import settings, settings_keys

SettingsGetter = Callable[[str, Any], Any]
PathExists = Callable[[str], bool]


@dataclass(frozen=True)
class ModelImportStrategy:
    """Resolved model import path choices and their diagnostic reason."""

    suffix: str
    use_cpp_fast_load: bool
    cpp_fast_load_reason: str
    use_native_pmx_parse: Optional[bool]
    require_native_pmx_parse: bool


@dataclass(frozen=True)
class VmdRuntimeBakeStrategy:
    """Resolved VMD bake path choice and its diagnostic reason."""

    use_runtime_bake: bool
    reason: str


def resolve_model_import_strategy(
    filepath: str,
    options: Mapping[str, Any],
    settings_get: SettingsGetter = settings.get,
) -> ModelImportStrategy:
    """Resolve model import path flags without mutating import state."""
    suffix = Path(filepath).suffix.lower()
    requested_fast_load = bool(
        options.get(
            "use_cpp_fast_load",
            settings_get(settings_keys.IMPORT_NATIVE_USE_CPP_FAST_LOAD, True),
        )
    )
    if suffix != ".pmx":
        use_fast_load = False
        fast_reason = f"disabled: suffix {suffix or '<none>'} is not .pmx"
    elif requested_fast_load:
        use_fast_load = True
        fast_reason = "enabled by option/settings"
    else:
        use_fast_load = False
        fast_reason = "disabled by option/settings"

    return ModelImportStrategy(
        suffix=suffix,
        use_cpp_fast_load=use_fast_load,
        cpp_fast_load_reason=fast_reason,
        use_native_pmx_parse=options.get("use_native_pmx_parse"),
        require_native_pmx_parse=bool(
            options.get(
                "require_native_pmx_parse",
                settings_get(settings_keys.IMPORT_NATIVE_REQUIRE_NATIVE_PMX_PARSE, False),
            )
        ),
    )


def resolve_vmd_runtime_bake_strategy(
    vmd_bytes: Optional[bytes],
    pmx_bytes: Optional[bytes],
    pmx_path: Optional[str],
    has_runtime: bool,
    runtime_available: Callable[[], bool],
    bake_mode: bool = False,
    path_exists: PathExists = os.path.exists,
) -> VmdRuntimeBakeStrategy:
    """Resolve whether VMD import should use the mmd-anim runtime bake path."""
    if not bake_mode:
        return VmdRuntimeBakeStrategy(False, "disabled: bake mode is off")
    if not has_runtime:
        return VmdRuntimeBakeStrategy(False, "disabled: mmd-anim runtime symbols unavailable")
    if not runtime_available():
        return VmdRuntimeBakeStrategy(False, "disabled: mmd-anim runtime library unavailable")
    if not bool(vmd_bytes):
        return VmdRuntimeBakeStrategy(False, "disabled: missing VMD bytes")

    if bool(pmx_bytes):
        return VmdRuntimeBakeStrategy(True, "enabled: PMX bytes provided")

    if pmx_path and Path(pmx_path).suffix.lower() == ".pmx" and path_exists(pmx_path):
        return VmdRuntimeBakeStrategy(True, "enabled: PMX path provided")

    return VmdRuntimeBakeStrategy(False, "disabled: missing PMX bytes/path")
