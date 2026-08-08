import unittest

from mmd_tools.converters import texture_alpha as ta


def _make_alpha(width, height, fn):
    """Row-major alpha plane built from fn(x, y) -> 0..255."""
    return bytes(fn(x, y) for y in range(height) for x in range(width))


# Two triangles covering the full [0,1]x[0,1] UV quad.
_FULL_QUAD = [(0.0, 0.0, 1.0, 0.0, 0.0, 1.0), (1.0, 0.0, 1.0, 1.0, 0.0, 1.0)]
# Two triangles covering only the left UV strip (u in [0, 0.4]).
_LEFT_STRIP = [(0.0, 0.0, 0.4, 0.0, 0.0, 1.0), (0.4, 0.0, 0.4, 1.0, 0.0, 1.0)]
_RIGHT_STRIP = [(0.63, 0.0, 0.87, 0.0, 0.87, 1.0)]


class TestTextureAlphaClassification(unittest.TestCase):
    def test_fully_opaque(self):
        alpha = _make_alpha(64, 64, lambda x, y: 255)
        mode = ta.classify_uv_triangles(alpha, 64, 64, _FULL_QUAD, resolution=64)
        self.assertEqual(mode, ta.MODE_OPAQUE)

    def test_translucent_gradient_is_blend(self):
        # Uniform mid alpha -> lots of partial-alpha samples -> blend.
        alpha = _make_alpha(64, 64, lambda x, y: 128)
        mode = ta.classify_uv_triangles(alpha, 64, 64, _FULL_QUAD, resolution=64)
        self.assertEqual(mode, ta.MODE_BLEND)

    def test_hard_edges_are_cutout(self):
        # Sharp on/off stripes with no partial alpha -> cutout (alphaTest).
        alpha = _make_alpha(64, 64, lambda x, y: 255 if (x // 4) % 2 == 0 else 0)
        mode = ta.classify_uv_triangles(alpha, 64, 64, _FULL_QUAD, resolution=64)
        self.assertEqual(mode, ta.MODE_CUTOUT)

    def test_atlas_opaque_subregion(self):
        # Atlas: left half opaque, right half fully transparent. A material that
        # only uses the LEFT strip must classify opaque despite the texture having
        # a large transparent area elsewhere (the core atlas-safety property).
        alpha = _make_alpha(64, 64, lambda x, y: 255 if x < 32 else 0)
        mode = ta.classify_uv_triangles(alpha, 64, 64, _LEFT_STRIP, resolution=64)
        self.assertEqual(mode, ta.MODE_OPAQUE)

    def test_named_conformance_vectors(self):
        """Keep Python vectors aligned with the native standalone smoke.

        The same seven named cases are repeated in ``MmdRenderQueueSmoke.cpp``;
        this test intentionally asserts only the shared mode contract, not an
        ABI or a Python-to-C++ bridge.
        """
        opaque = bytes([255] * 16)
        cutout = bytes([
            0, 0, 0, 0,
            255, 255, 255, 255,
            0, 0, 0, 0,
            255, 255, 255, 255,
        ])
        blend = bytes([128] * 16)
        atlas = bytes(
            255 if x < 3 else 64
            for _y in range(4)
            for x in range(4)
        )
        degenerate = bytes([64] + [255] * 15)
        wrapped = bytes([255, 64] + [255] * 14)
        extreme_fallback = bytes([255, 64] + [255] * 14)

        cases = {
            "opaque": (opaque, _FULL_QUAD, ta.MODE_OPAQUE),
            "cutout": (cutout, _FULL_QUAD, ta.MODE_CUTOUT),
            "blend": (blend, _FULL_QUAD, ta.MODE_BLEND),
            "atlas": (atlas, _RIGHT_STRIP, ta.MODE_BLEND),
            "degenerate": (
                degenerate,
                [(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)],
                ta.MODE_BLEND,
            ),
            "wrap": (
                wrapped,
                [(1.0, 0.0, 1.25, 0.0, 1.0, 1.0)],
                ta.MODE_BLEND,
            ),
            # Non-finite UVs are ignored; the every-fourth-texel fallback sees
            # only opaque texels at indices 0, 4, 8, and 12.  The second
            # triangle exercises the over-magnitude guard; the third exceeds
            # the 64-tile span and must use vertex fallback.
            "extreme": (
                extreme_fallback,
                [
                    (float("inf"), 0.0, 0.0, 0.0, 0.0, 0.0),
                    (1000001.0, 0.0, 1000001.0, 0.0, 1000001.0, 1.0),
                    (0.0, 0.0, 65.0, 0.0, 0.0, 1.0),
                ],
                ta.MODE_OPAQUE,
            ),
        }
        for name, (alpha, triangles, expected) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(
                    ta.classify_uv_triangles(alpha, 4, 4, triangles, resolution=4),
                    expected,
                )

    def test_invalid_alpha_buffer_fails_closed(self):
        self.assertEqual(
            ta.classify_uv_triangles(bytes([255]), 2, 2, _FULL_QUAD, resolution=4),
            ta.MODE_OPAQUE,
        )
        self.assertEqual(
            ta.classify_uv_triangles(None, 2, 2, _FULL_QUAD, resolution=4),
            ta.MODE_OPAQUE,
        )

    def test_classify_material_rejects_non_alpha_extension(self):
        self.assertEqual(ta.classify_material("C:/x/foo.bmp", _FULL_QUAD), ta.MODE_OPAQUE)

    def test_classify_material_missing_file_is_opaque(self):
        self.assertEqual(
            ta.classify_material("C:/does/not/exist/foo.png", _FULL_QUAD), ta.MODE_OPAQUE
        )


if __name__ == "__main__":
    unittest.main()
