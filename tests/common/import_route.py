"""Shared PMX import entry points used by the Python/C++ parity contracts.

The parity tests deliberately vary only the import entry option.  Both routes
then pass through the production ``import_mmd_file`` entry point so the
assertions exercise the same authoring contract.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional
from unittest.mock import patch

from mmd_tools.io import mmd_importer
from mmd_tools.io.mmd_importer import import_mmd_file


class ImportRoute(str, Enum):
    """The two PMX import entries covered by the functional contract."""

    PYTHON = "python"
    CPP = "cpp"


def import_pmx_via_route(
    test_case: Any,
    filepath: str,
    route: ImportRoute,
    options: Optional[Dict[str, Any]] = None,
) -> str:
    """Import ``filepath`` through one route and fail closed for C++.

    ``fast_import`` historically returns ``None`` when the C++ plugin cannot
    be loaded, allowing the public entry point to fall back to Python.  That
    behaviour is useful for normal callers but would make a parity test pass
    without exercising native geometry.  The C++ lane therefore wraps the
    existing function and turns that result into an explicit test failure.
    """

    import_options = dict(options or {})
    import_options.setdefault("create_mmd_shaders", False)
    import_options.setdefault("setup_rig", False)
    import_options.setdefault("setup_bone_orientation", False)

    if route == ImportRoute.PYTHON:
        import_options.update(
            {
                "use_cpp_fast_load": False,
                "use_native_pmx_parse": False,
                "require_native_pmx_parse": False,
            }
        )
        root = import_mmd_file(filepath, options=import_options)
        if not root:
            test_case.fail("Python PMX importer returned no model root")
        return str(root)

    if route != ImportRoute.CPP:
        raise ValueError("unsupported import route: {!r}".format(route))

    import_options.update(
        {
            "use_cpp_fast_load": True,
            "cpp_fast_load_mesh_only": False,
            "use_native_pmx_parse": True,
            "require_native_pmx_parse": True,
        }
    )
    original_fast_import = mmd_importer.fast_import
    native_calls = []
    fast_import_calls = []

    # ``fast_import`` is the boundary under test.  Forwarding a spy through
    # the real Maya command gives the lane a native completion witness instead
    # of accepting a wrapper that was never invoked.
    from maya import cmds
    from mmd_tools.io import cpp_fast_importer

    plugin_path = cpp_fast_importer.cpp_plugin_locator.find_plugin_path(
        cpp_fast_importer._candidate_plugin_paths()
    )
    if plugin_path is None:
        test_case.fail("C++ PMX importer plugin artifact is unavailable")
    cpp_fast_importer._setup_plugin_directory(plugin_path.parent)
    cpp_fast_importer.cpp_plugin_locator.load_plugin(plugin_path, cmds, prepare=False)

    native_command = getattr(cmds, "mmdFastLoad", None)
    if not callable(native_command):
        test_case.fail("C++ PMX importer command mmdFastLoad is unavailable")
    native_results = []

    def _native_fast_load_spy(*args: Any, **kwargs: Any) -> Any:
        native_calls.append((args, kwargs))
        result = native_command(*args, **kwargs)
        native_results.append(result)
        return result

    def _strict_fast_import(*args: Any, **kwargs: Any) -> str:
        fast_import_calls.append((args, kwargs))
        root = original_fast_import(*args, **kwargs)
        if root is None:
            test_case.fail(
                "C++ PMX importer did not complete native geometry; "
                "the route returned None and would have fallen back to Python "
                "(fast_import_calls={}, native_calls={})".format(
                    len(fast_import_calls), len(native_calls)
                )
            )
        return str(root)

    with patch.object(cmds, "mmdFastLoad", side_effect=_native_fast_load_spy), patch.object(
        mmd_importer, "fast_import", side_effect=_strict_fast_import
    ):
        root = import_mmd_file(filepath, options=import_options)
    test_case.assertEqual(len(fast_import_calls), 1, "C++ fast_import call count")
    test_case.assertEqual(len(native_calls), 1, "native mmdFastLoad call count")
    test_case.assertTrue(native_results[0], "native mmdFastLoad completion result")
    if not root:
        test_case.fail("C++ PMX importer returned no model root")
    return str(root)
