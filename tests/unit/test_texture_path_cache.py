import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mmd_tools.core import texture_path_cache as cache


class TestTexturePathCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.model = self.root / "モデル.pmx"
        self.model.write_bytes(b"same model bytes")
        self.texture = self.root / "纹理.png"
        self.texture.write_bytes(b"texture bytes")
        self.ascii_texture = self.root / "plain.png"
        self.ascii_texture.write_bytes(b"ascii texture bytes")
        self.workspace = self.root / "workspace"

    def tearDown(self):
        self.tmp.cleanup()

    def test_original_path_encoding_round_trip_non_ascii(self):
        original = "textures/纹理_日本語.png"

        encoded = cache.encode_original_texture_path(original)

        self.assertTrue(encoded.isascii())
        self.assertEqual(cache.decode_original_texture_path(encoded), original)
        self.assertEqual(cache.decode_original_texture_path(original), original)
        self.assertEqual(cache.decode_original_texture_path("plain text"), "plain text")

    def test_unreadable_detection_missing_and_question_mark(self):
        self.assertTrue(cache.is_unreadable_file_texture_path(str(self.root / "missing.png")))
        self.assertTrue(cache.is_unreadable_file_texture_path(str(self.root / "????.png")))
        # ASCII path that exists must be readable on every platform/codepage.
        self.assertFalse(cache.is_unreadable_file_texture_path(str(self.ascii_texture)))

    def test_ansi_incompatible_path_with_injected_codepage(self):
        self.assertFalse(cache.is_ansi_incompatible_path("textures/日本語.png", encoding="cp932"))
        self.assertTrue(cache.is_ansi_incompatible_path("textures/颜.png", encoding="cp932"))
        self.assertTrue(cache.is_ansi_incompatible_path("textures/日本語.png", encoding="ascii"))
        self.assertFalse(cache.is_ansi_incompatible_path("textures/plain.png", encoding="ascii"))

    def test_non_ascii_path_detection(self):
        self.assertTrue(cache.is_non_ascii_path("textures/日本語.png"))
        self.assertFalse(cache.is_non_ascii_path("textures/plain.png"))
        self.assertFalse(cache.is_non_ascii_path(""))
        self.assertFalse(cache.is_non_ascii_path(None))

    def test_ansi_incompatible_path_ignores_unknown_codec(self):
        self.assertFalse(cache.is_ansi_incompatible_path("textures/颜.png", encoding="missing-codec"))

    def test_unreadable_detection_includes_ansi_incompatible_existing_path(self):
        with patch.object(cache, "is_non_ascii_path", return_value=False), patch.object(
            cache, "is_ansi_incompatible_path", return_value=True
        ):
            self.assertTrue(cache.is_unreadable_file_texture_path(str(self.texture)))

    def test_classify_unreadable_reason_can_inject_windows_codepage(self):
        with patch.object(cache, "is_non_ascii_path", return_value=False):
            self.assertEqual(
                cache.classify_unreadable_file_texture_path(str(self.texture), encoding="cp932"),
                "ansi_incompatible_path",
            )

    def test_classify_unreadable_reason_detects_non_ascii_existing_file(self):
        self.assertEqual(
            cache.classify_unreadable_file_texture_path(str(self.texture), encoding="cp932"),
            "non_ascii_path",
        )

    def test_classify_unreadable_reason_prioritizes_question_mark_over_non_ascii(self):
        self.assertEqual(
            cache.classify_unreadable_file_texture_path(str(self.root / "日本語????.png")),
            "question_mark_path",
        )

    def test_describe_texture_issue_is_plain_language(self):
        self.assertEqual(
            cache.describe_texture_issue("non_ascii_path"),
            "Maya may fail to display this texture path",
        )
        self.assertEqual(
            cache.describe_texture_issue("ansi_incompatible_path"),
            "Unsupported characters in path",
        )
        self.assertEqual(cache.describe_texture_issue("missing_file"), "File not found")
        self.assertEqual(cache.describe_texture_issue("cache_copy_failed"), "Failed to copy texture to cache")
        self.assertEqual(cache.describe_texture_issue(""), "Cannot be displayed")
        # Unknown codes fall back to the raw reason so nothing is hidden.
        self.assertEqual(cache.describe_texture_issue("brand_new_code"), "brand_new_code")

    def test_classification_resolvable_and_unrecoverable(self):
        resolvable = cache.classify_texture_resolution(
            original_path=self.texture.name,
            file_texture_path=str(self.root / "????.png"),
            model_path=self.model,
        )
        unrecoverable = cache.classify_texture_resolution(
            original_path="missing.png",
            file_texture_path=str(self.root / "????.png"),
            model_path=self.model,
        )

        self.assertEqual(resolvable.status, "resolvable")
        self.assertEqual(Path(resolvable.source_path), self.texture)
        self.assertEqual(unrecoverable.status, "unrecoverable")
        self.assertEqual(unrecoverable.reason, "source_not_found")

    def test_texture_source_candidates_report_checked_model_relative_path(self):
        candidates = cache.build_texture_source_candidates(self.texture.name, self.model)

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["kind"], "model_relative")
        self.assertEqual(Path(candidate["path"]), self.texture)
        self.assertTrue(candidate["accepted"])
        self.assertEqual(Path(candidate["resolved_path"]), self.texture)
        self.assertEqual(candidate["reason"], "")
        self.assertTrue(candidate["exists"])
        self.assertTrue(candidate["is_file"])
        json.dumps(candidates)

    def test_texture_path_diagnostics_reports_unicode_and_safety_flags(self):
        diagnostics = cache.build_texture_path_diagnostics(
            original_path="../纹理.png",
            file_texture_path=str(self.texture),
            model_path=self.model,
            encoding="ascii",
        )

        self.assertTrue(diagnostics["original_path_has_parent_traversal"])
        self.assertTrue(diagnostics["original_path_has_non_ascii"])
        self.assertTrue(diagnostics["original_path_ansi_incompatible"])
        self.assertTrue(diagnostics["current_path_has_non_ascii"])
        self.assertTrue(diagnostics["current_path_ansi_incompatible"])
        self.assertEqual(diagnostics["current_path_unreadable_reason"], "non_ascii_path")

    def test_safety_rejects_absolute_parent_traversal_outside_and_extension(self):
        bad_ext = self.root / "texture.exe"
        bad_ext.write_bytes(b"exe")
        outside = Path(self.tmp.name).parent / "outside_texture.png"
        outside.write_bytes(b"outside")
        self.addCleanup(lambda: outside.exists() and outside.unlink())

        cases = [
            (str(outside), "absolute_original_path_rejected"),
            ("../outside.png", "parent_traversal_rejected"),
            (r"..\outside.png", "parent_traversal_rejected"),
            ("texture.exe", "extension_rejected"),
        ]
        for original, reason in cases:
            with self.subTest(original=original):
                source, actual_reason = cache.find_resolvable_source(original, self.model)
                self.assertIsNone(source)
                self.assertEqual(actual_reason, reason)

    def test_absolute_original_under_model_parent_is_resolvable(self):
        source, reason = cache.find_resolvable_source(str(self.texture), self.model)

        self.assertEqual(source, self.texture)
        self.assertEqual(reason, "")

    def test_absolute_original_with_parent_traversal_outside_is_rejected(self):
        outside = self.root.parent / "outside_parent_traversal.png"
        outside.write_bytes(b"outside")
        self.addCleanup(lambda: outside.exists() and outside.unlink())
        original = str(self.root / ".." / outside.name)

        source, reason = cache.find_resolvable_source(original, self.model)

        self.assertIsNone(source)
        self.assertEqual(reason, "absolute_original_path_rejected")

    def test_unc_original_outside_model_parent_is_rejected_when_absolute(self):
        original = r"\\server\share\mmd_tools_unc_texture.png"
        if not Path(original).is_absolute():
            self.skipTest("UNC path is not absolute on this platform")

        source, reason = cache.find_resolvable_source(original, self.model)

        self.assertIsNone(source)
        self.assertEqual(reason, "absolute_original_path_rejected")

    def test_safety_rejects_symlink_when_supported(self):
        link = self.root / "linked.png"
        try:
            os.symlink(self.texture, link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")

        source, reason = cache.find_resolvable_source(link.name, self.model)

        self.assertIsNone(source)
        self.assertEqual(reason, "symlink_rejected")

    def test_cache_name_is_deterministic_from_original_path_and_overwrites(self):
        original = "textures/../纹理.png"
        expected_key = "纹理.png"
        expected_name = hashlib.sha256(expected_key.encode("utf-8")).hexdigest()[:16] + ".png"

        first = cache.copy_texture_to_cache(self.texture, self.workspace, self.model, original_path=original)
        self.texture.write_bytes(b"updated texture bytes")
        reused = cache.copy_texture_to_cache(self.texture, self.workspace, self.model, original_path=original)

        self.assertEqual(first, reused)
        self.assertTrue(first.name.isascii())
        self.assertEqual(first.name, expected_name)
        self.assertEqual(first.read_bytes(), b"updated texture bytes")

    def test_cache_key_relative_and_absolute_under_model_parent_match(self):
        relative = self.texture.name
        absolute = str(self.texture)

        rel_path = cache.cache_path_for_original_texture(relative, self.workspace, self.model, source_path=self.texture)
        abs_path = cache.cache_path_for_original_texture(absolute, self.workspace, self.model, source_path=self.texture)

        self.assertEqual(rel_path, abs_path)
        self.assertEqual(rel_path.suffix, ".png")

    def test_cache_name_uses_lowercase_suffix(self):
        source = self.root / "Upper.PNG"
        source.write_bytes(b"upper")

        copied = cache.copy_texture_to_cache(source, self.workspace, self.model, original_path=source.name)

        self.assertEqual(copied.suffix, ".png")

    def test_model_hash_same_content_and_unreadable_fallback(self):
        same = self.root / "same.pmd"
        same.write_bytes(self.model.read_bytes())
        missing = self.root / "missing" / "model.pmx"

        self.assertEqual(cache.compute_model_hash(self.model), cache.compute_model_hash(same))
        self.assertEqual(cache.compute_model_hash(missing), cache.compute_model_hash(missing))

    def test_resolve_texture_to_cache(self):
        result = cache.resolve_texture_to_cache(
            original_path=self.texture.name,
            file_texture_path=str(self.root / "????.png"),
            model_path=self.model,
            workspace_root=self.workspace,
        )

        self.assertEqual(result.status, "resolved")
        self.assertTrue(result.cached)
        self.assertTrue(Path(result.file_texture_path).exists())
        expected_name = hashlib.sha256(self.texture.name.encode("utf-8")).hexdigest()[:16] + ".png"
        self.assertEqual(Path(result.file_texture_path).name, expected_name)

    def test_resolve_texture_to_cache_accepts_absolute_original_under_model_parent(self):
        result = cache.resolve_texture_to_cache(
            original_path=str(self.texture),
            file_texture_path=str(self.texture),
            model_path=self.model,
            workspace_root=self.workspace,
        )

        self.assertEqual(result.status, "resolved")
        self.assertTrue(result.cached)
        self.assertTrue(Path(result.cache_path).exists())

    def test_resolve_texture_to_cache_reports_copy_failure(self):
        with patch.object(cache.shutil, "copy2", side_effect=PermissionError("denied")):
            result = cache.resolve_texture_to_cache(
                original_path=self.texture.name,
                file_texture_path=str(self.root / "????.png"),
                model_path=self.model,
                workspace_root=self.workspace,
            )

        self.assertEqual(result.status, "unrecoverable")
        self.assertEqual(result.reason, "cache_copy_failed")
        self.assertFalse(result.cached)
        self.assertEqual(Path(result.source_path), self.texture)


if __name__ == "__main__":
    unittest.main()
