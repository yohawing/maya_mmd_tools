"""Binary layout regressions for PMX morph records."""

from __future__ import annotations

from io import BytesIO
import struct
import unittest

from mmd_tools.core.pmx_data.header import PmxEncoding
from mmd_tools.core.pmx_data.morph import PmxMorph, PmxMorphType


def _impulse_morph(offset=None):
    morph = PmxMorph(
        vertex_index_size=1,
        material_index_size=1,
        bone_index_size=1,
        morph_index_size=1,
        rigid_body_index_size=1,
        encoding=PmxEncoding.UTF8,
    )
    morph.name = "impulse"
    morph.name_english = "impulse"
    morph.panel = 4
    morph.morph_type = PmxMorphType.ImpulseMorph
    morph.offsets = [] if offset is None else [offset]
    return morph


class TestPmxMorphBinaryLayout(unittest.TestCase):
    def test_impulse_round_trip_preserves_local_flag_and_vectors(self):
        source = _impulse_morph(
            {
                "rigid_body_index": 0,
                "is_local": 1,
                "impulse": (0.1, -0.2, 0.3),
                "torque": (-0.4, 0.5, -0.6),
            }
        )

        encoded = BytesIO()
        source.write(encoded)
        self.assertEqual(
            encoded.getvalue()[-26:],
            struct.pack(
                "<bB6f",
                0,
                1,
                0.1,
                -0.2,
                0.3,
                -0.4,
                0.5,
                -0.6,
            ),
        )

        decoded = _impulse_morph()
        decoded.parse(BytesIO(encoded.getvalue()))
        offset = decoded.offsets[0]
        self.assertEqual(offset["rigid_body_index"], 0)
        self.assertEqual(offset["is_local"], 1)
        for actual, expected in zip(offset["impulse"], (0.1, -0.2, 0.3)):
            self.assertAlmostEqual(actual, expected, places=6)
        for actual, expected in zip(offset["torque"], (-0.4, 0.5, -0.6)):
            self.assertAlmostEqual(actual, expected, places=6)

    def test_impulse_writer_defaults_legacy_offsets_to_global_space(self):
        source = _impulse_morph(
            {
                "rigid_body_index": 0,
                "impulse": (0.1, 0.2, 0.3),
                "torque": (0.4, 0.5, 0.6),
            }
        )

        encoded = BytesIO()
        source.write(encoded)

        decoded = _impulse_morph()
        decoded.parse(BytesIO(encoded.getvalue()))
        self.assertEqual(decoded.offsets[0]["is_local"], 0)


if __name__ == "__main__":
    unittest.main()
