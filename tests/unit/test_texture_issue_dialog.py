"""Texture issue dialog update helpers for headless unit tests."""

from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.texture_issue_dialog import (  # noqa: E402
    TextureIssueDialog,
    mark_texture_resolution_failed,
    update_texture_issues_from_resolution_results,
)


class TestTextureIssueDialogUpdates(unittest.TestCase):
    def test_resolved_result_updates_current_path_and_count(self):
        issues = [
            {
                "file_node": "file1",
                "current_path": "F:/model/纹理.png",
                "reason": "non_ascii_path",
                "source_reason": "non_ascii_path",
                "resolvable": True,
            }
        ]
        results = [
            SimpleNamespace(
                file_node="file1",
                status="resolved",
                reason="",
                file_texture_path="F:/workspace/sourceimages/cache.png",
                cache_path="F:/workspace/sourceimages/cache.png",
            )
        ]

        resolved = update_texture_issues_from_resolution_results(issues, results)

        self.assertEqual(resolved, 1)
        self.assertEqual(issues[0]["current_path"], "F:/workspace/sourceimages/cache.png")
        self.assertEqual(issues[0]["reason"], "resolved")
        self.assertEqual(issues[0]["source_reason"], "resolved")
        self.assertFalse(issues[0]["resolvable"])

    def test_unrecoverable_result_updates_reason_and_redraw_state(self):
        issues = [
            {
                "file_node": "file1",
                "current_path": "F:/model/纹理.png",
                "reason": "non_ascii_path",
                "source_reason": "non_ascii_path",
                "resolvable": True,
            }
        ]
        results = [
            SimpleNamespace(
                file_node="file1",
                status="unrecoverable",
                reason="cache_copy_failed",
                file_texture_path="F:/model/纹理.png",
                source_path="F:/model/纹理.png",
            )
        ]

        resolved = update_texture_issues_from_resolution_results(issues, results)

        self.assertEqual(resolved, 0)
        self.assertEqual(issues[0]["reason"], "cache_copy_failed")
        self.assertEqual(issues[0]["source_reason"], "cache_copy_failed")
        self.assertFalse(issues[0]["resolvable"])

    def test_mark_texture_resolution_failed_only_marks_resolvable_issues(self):
        issues = [
            {"file_node": "file1", "reason": "non_ascii_path", "resolvable": True},
            {"file_node": "file2", "reason": "resolved", "resolvable": False},
        ]

        updated = mark_texture_resolution_failed(issues, "cache_copy_failed")

        self.assertEqual(updated, 1)
        self.assertEqual(issues[0]["reason"], "cache_copy_failed")
        self.assertEqual(issues[0]["source_reason"], "cache_copy_failed")
        self.assertFalse(issues[0]["resolvable"])
        self.assertEqual(issues[1]["reason"], "resolved")

    def test_resolve_all_catches_exception_updates_reason_and_repopulates(self):
        dialog = object.__new__(TextureIssueDialog)
        dialog.issues = [
            {
                "file_node": "file1",
                "current_path": "F:/model/纹理.png",
                "reason": "non_ascii_path",
                "source_reason": "non_ascii_path",
                "resolvable": True,
            }
        ]
        dialog.tr = lambda key, category="texture_issues": "Fixed {count} texture(s)"
        dialog._emit_status = MagicMock()
        dialog._populate = MagicMock()

        with patch(
            "mmd_tools.ui.texture_issue_dialog.maya_utils.resolve_scene_mmd_textures",
            side_effect=PermissionError("denied"),
        ):
            dialog.resolve_all()

        self.assertEqual(dialog.issues[0]["reason"], "cache_copy_failed")
        self.assertEqual(dialog.issues[0]["source_reason"], "cache_copy_failed")
        self.assertFalse(dialog.issues[0]["resolvable"])
        dialog._emit_status.assert_called_once_with("Fixed 0 texture(s)")
        dialog._populate.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
