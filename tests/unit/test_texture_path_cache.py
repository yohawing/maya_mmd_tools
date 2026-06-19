import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mmd_tools.core import texture_path_cache as cache


class TestTexturePathCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=r"F:\tmp")
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

    def test_ansi_incompatible_path_ignores_unknown_codec(self):
        self.assertFalse(cache.is_ansi_incompatible_path("textures/颜.png", encoding="missing-codec"))

    def test_unreadable_detection_includes_ansi_incompatible_existing_path(self):
        with patch.object(cache, "is_ansi_incompatible_path", return_value=True):
            self.assertTrue(cache.is_unreadable_file_texture_path(str(self.texture)))

    def test_classify_unreadable_reason_can_inject_windows_codepage(self):
        self.assertEqual(
            cache.classify_unreadable_file_texture_path(str(self.texture), encoding="cp932"),
            "ansi_incompatible_path",
        )

    def test_describe_texture_issue_is_plain_language(self):
        self.assertEqual(
            cache.describe_texture_issue("ansi_incompatible_path"),
            "Unsupported characters in path",
        )
        self.assertEqual(cache.describe_texture_issue("missing_file"), "File not found")
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

    def test_safety_rejects_absolute_parent_traversal_outside_and_extension(self):
        bad_ext = self.root / "texture.exe"
        bad_ext.write_bytes(b"exe")

        cases = [
            (str(self.texture), "absolute_original_path_rejected"),
            ("../outside.png", "parent_traversal_rejected"),
            ("texture.exe", "extension_rejected"),
        ]
        for original, reason in cases:
            with self.subTest(original=original):
                source, actual_reason = cache.find_resolvable_source(original, self.model)
                self.assertIsNone(source)
                self.assertEqual(actual_reason, reason)

    def test_safety_rejects_symlink_when_supported(self):
        link = self.root / "linked.png"
        try:
            os.symlink(self.texture, link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")

        source, reason = cache.find_resolvable_source(link.name, self.model)

        self.assertIsNone(source)
        self.assertEqual(reason, "symlink_rejected")

    def test_cache_name_ascii_reuse_and_collision_suffix(self):
        first = cache.copy_texture_to_cache(self.texture, self.workspace, self.model)
        reused = cache.copy_texture_to_cache(self.texture, self.workspace, self.model)
        self.assertEqual(first, reused)
        self.assertTrue(first.name.isascii())
        self.assertEqual(first.suffix, ".png")

        colliding_source = self.root / "collision.PNG"
        colliding_source.write_bytes(b"different")
        target_hash = cache.hashlib.sha256(colliding_source.read_bytes()).hexdigest()[:16]
        cache_dir = self.workspace / "sourceimages" / "mmd_tools_texture_cache" / cache.compute_model_hash(self.model)
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"{target_hash}.png").write_bytes(b"preexisting mismatch")

        collided = cache.copy_texture_to_cache(colliding_source, self.workspace, self.model)

        self.assertEqual(collided.name, f"{target_hash}_001.png")
        self.assertEqual((cache_dir / f"{target_hash}.png").read_bytes(), b"preexisting mismatch")

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


if __name__ == "__main__":
    unittest.main()
