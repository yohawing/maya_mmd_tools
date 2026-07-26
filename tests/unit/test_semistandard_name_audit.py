"""Focused corpus aggregation contracts for the semistandard-name audit."""

import unittest

from tests.local.semistandard_name_audit import (
    _build_corpus_statistics,
    _corpus_name_flags,
)


def _record(path: str, name: str, converted: str, *, category: str = "material"):
    return {
        "category": category,
        "source_kind": f"pmx.{category}s",
        "file": path,
        "index": 0,
        "name": name,
        "english_name": "",
        "converted": converted,
        "flags": _corpus_name_flags(name, converted),
        "morph_type": "VertexMorph" if category == "morph" else None,
    }


class TestSemistandardNameAuditCorpus(unittest.TestCase):
    """Contracts discovered by the repo's mayapy unittest runner."""

    def test_corpus_ranking_distinguishes_repeats_from_distinct_models(self):
        records = [
            _record("model-a.pmx", "髪", "HASHaaaa1111"),
            _record("model-a.pmx", "髪", "HASHaaaa1111"),
            _record("model-b.pmx", "髪", "HASHaaaa1111"),
        ]

        statistics, dangerous = _build_corpus_statistics(records, category="material")

        self.assertEqual(statistics[0]["occurrences"], 3)
        self.assertEqual(statistics[0]["distinct_models"], 2)
        self.assertEqual(statistics[0]["within_model_repeats"], 1)
        self.assertEqual(statistics[0]["flags"], ["hash_fallback"])
        self.assertEqual(dangerous[0]["original_name"], "髪")

    def test_conversion_collision_is_ranked_without_frequency_inference(self):
        records = [
            _record("model-a.pmx", "髪", "hair"),
            _record("model-b.pmx", "頭髪", "hair"),
        ]

        statistics, dangerous = _build_corpus_statistics(records, category="material")

        self.assertEqual({row["original_name"] for row in dangerous}, {"髪", "頭髪"})
        self.assertTrue(all("conversion_collision" in row["flags"] for row in dangerous))
        self.assertTrue(all(row["distinct_models"] == 1 for row in dangerous))

    def test_source_hazards_are_explicit_and_maya_safe_output_is_checked(self):
        flags = _corpus_name_flags("1:髪+", "1__")

        self.assertEqual(
            flags,
            [
                "leading_digit",
                "colon_namespace",
                "unsupported_punctuation",
                "unsafe_maya_identifier",
            ],
        )


if __name__ == "__main__":
    unittest.main()
