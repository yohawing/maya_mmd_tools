"""Session implementations for Maya-hosted fixture and plugin smokes."""

from __future__ import annotations

from pathlib import Path


def run_cpp_plugin_smoke(
    session,
    *,
    posargs: list[str],
    option,
    default_maya_version: str,
    default_config: str,
    root: Path,
    mayapy,
    mayapy_env,
    mayapy_arg_path,
    mayapy_script,
    scripts: tuple[str, ...],
    require_plugin: bool,
) -> None:
    """Run one or more mayapy probes with the selected C++ plugin environment."""
    version = option(posargs, "--maya", default_maya_version)
    config = option(posargs, "--config", default_config)
    mayapy_path = mayapy(version)
    if not mayapy_path.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy_path}")

    env_values = {"MAYA_VERSION": version, "MMD_TOOLS_CPP_CONFIG": config}
    if require_plugin:
        plugin = root / "plug-ins" / version / config / "mmd_tools_cpp.mll"
        if not plugin.exists():
            session.error(
                f"C++ plugin not found at {plugin}; run 'uvx nox -s cpp_build "
                f"-- --maya {version} --config {config}' first."
            )
        env_values["MMD_TOOLS_CPP_PLUGIN"] = mayapy_arg_path(mayapy_path, plugin)
    env = mayapy_env(mayapy_path, **env_values)
    for script in scripts:
        session.run(
            str(mayapy_path),
            mayapy_script(mayapy_path, script),
            env=env,
            external=True,
        )
