"""Failed full imports must release only their native geometry."""

import unittest
from pathlib import Path
from unittest.mock import patch

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.io import cpp_fast_importer  # noqa: E402


class TestFastImportCleanup(unittest.TestCase):
    def test_split_failure_reclaims_adopted_sources_and_proxy_parents(self):
        rejection = RuntimeError("split authoring rejected")
        for vp2, adopted in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(vp2=vp2, adopted=adopted):
                sources = ["|native|sourceA", "|native|sourceB"]
                proxies = ["|native|sourceA|proxyA", "|native|sourceB|proxyB"] if vp2 else []
                source_ids = ["source-a-id", "source-b-id"]
                proxy_ids = ["proxy-a-id", "proxy-b-id"] if vp2 else []
                remaining = ["|model|renamedA", "|model|renamedB"] if adopted else ["|native", *sources]
                live_proxies = ["|model|drawA|proxyA", "|model|drawB|proxyB"] if adopted else proxies
                parents = ["|model|drawA", "|model|drawB"] if adopted else sources

                def lookup(nodes, **kwargs):
                    if kwargs.get("uuid"):
                        if nodes == "native":
                            return ["group-id"]
                        if nodes == sources:
                            return source_ids.copy()
                        if nodes == proxies and proxies:
                            return proxy_ids.copy()
                    elif nodes == ["group-id", *source_ids]:
                        return remaining.copy()
                    elif nodes == proxy_ids and proxy_ids:
                        return live_proxies.copy()
                    self.fail(f"unexpected lookup: {nodes}, {kwargs}")

                def relatives(node, **kwargs):
                    if node == "native":
                        return proxies if kwargs.get("allDescendents") else sources
                    return [parents[live_proxies.index(node)]]

                with patch.object(cpp_fast_importer, "_candidate_plugin_paths", return_value=[Path("plugin.mll")]), patch.object(
                    Path, "exists", return_value=True
                ), patch.object(cpp_fast_importer.cpp_plugin_locator, "is_plugin_loaded", return_value=True), patch.object(
                    cpp_fast_importer, "parse_pmx_native", return_value=object()
                ), patch.object(cpp_fast_importer, "_require_dx11_for_vp2_ownership"), patch(
                    "maya.cmds.mmdFastLoad", create=True, return_value=["native"]
                ), patch("maya.cmds.ls", side_effect=lookup), patch(
                    "maya.cmds.listRelatives", side_effect=relatives
                ), patch("maya.cmds.delete") as delete, patch(
                    "mmd_tools.io.pmx_importer.import_pmx_file", side_effect=rejection
                ):
                    with self.assertRaises(RuntimeError) as caught:
                        cpp_fast_importer.fast_import(
                            "model.pmx", mesh_only=False, vp2_ownership=vp2,
                            options={"separate_meshes_by_material": True},
                        )
                    self.assertIs(caught.exception, rejection)
                expected = remaining + (parents if vp2 and adopted else [])
                delete.assert_called_once_with(expected)

    def test_authoring_failure_tracks_renamed_or_adopted_geometry_by_uuid(self):
        rejection = RuntimeError("authoring rejected")
        for remaining, cleanup_error in ((["|renamed"], None), ([], None), (["|renamed"], RuntimeError("delete failed"))):
            with self.subTest(remaining=remaining, cleanup_error=cleanup_error), patch.object(
                cpp_fast_importer, "_candidate_plugin_paths", return_value=[Path("plugin.mll")]
            ), patch.object(Path, "exists", return_value=True), patch.object(
                cpp_fast_importer.cpp_plugin_locator, "is_plugin_loaded", return_value=True
            ), patch.object(cpp_fast_importer, "parse_pmx_native", return_value=object()), patch(
                "maya.cmds.mmdFastLoad", create=True, return_value=["native", "shape"]
            ), patch("maya.cmds.ls", side_effect=[["native-uuid"], remaining]) as lookup, patch(
                "maya.cmds.delete", side_effect=cleanup_error
            ) as delete, patch("mmd_tools.io.pmx_importer.import_pmx_file", side_effect=rejection):
                with self.assertRaises(RuntimeError) as caught:
                    cpp_fast_importer.fast_import(
                        "model.pmx", mesh_only=False, options={"separate_meshes_by_material": False}
                    )
                self.assertIs(caught.exception, rejection)
                lookup.assert_any_call(["native-uuid"], long=True)
                if remaining:
                    delete.assert_called_once_with(remaining)
                else:
                    delete.assert_not_called()
