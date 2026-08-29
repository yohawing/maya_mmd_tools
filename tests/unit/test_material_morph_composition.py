"""Pure-Python coverage for PMX material morph numeric composition."""

import unittest

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

# The evaluator's compose method is pure, but its node module inherits from
# Maya's MPxNode at import time.  Supply only the import-time API needed here;
# no Maya command or DG behavior is used by these tests.
import maya.api.OpenMaya as om  # noqa: E402

_original_mpx_node = om.MPxNode
_original_type_id = om.MTypeId
try:
    om.MPxNode = type("_StubMpxNode", (), {})
    om.MTypeId = lambda value: value
    from mmd_tools.nodes.mmd_material_morph_eval_node import MmdMaterialMorphEvalNode  # noqa: E402
finally:
    # Do not make the import-only node stubs visible to unrelated unit modules.
    om.MPxNode = _original_mpx_node
    om.MTypeId = _original_type_id


class TestMaterialMorphNumericComposition(unittest.TestCase):
    """Shader routing and Maya DG are intentionally outside this unit gate."""

    @staticmethod
    def _base():
        return {
            "diffuse": (0.5, 0.4, 0.3, 0.8),
            "specular": (0.5, 0.5, 0.5),
            "specularCoefficient": (0.5,),
            "ambient": (0.5, 0.5, 0.5),
            "edgeColor": (0.5, 0.5, 0.5, 0.5),
            "edgeSize": (0.5,),
        }

    @staticmethod
    def _contribution(op, value, order=0, slot=0):
        return {
            "logical_index": slot,
            "morph_order": order,
            "weight": 1.0,
            "op": op,
            "diffuse": tuple(value for _ in range(4)),
            "specular": tuple(value for _ in range(3)),
            "specularCoefficient": (value,),
            "ambient": tuple(value for _ in range(3)),
            "edgeColor": tuple(value for _ in range(4)),
            "edgeSize": (value,),
            "texture": tuple(value for _ in range(4)),
            "sphereTexture": tuple(value for _ in range(4)),
            "toonTexture": tuple(value for _ in range(4)),
        }

    def test_add_and_multiply_are_composed_separately(self):
        contributions = [
            self._contribution(1, 0.2, order=0),
            self._contribution(0, 0.5, order=1),
        ]
        material, _, _ = MmdMaterialMorphEvalNode.compose(self._base(), contributions)
        self.assertAlmostEqual(material["diffuse"][0], 0.45)
        self.assertNotAlmostEqual(material["diffuse"][0], 0.35)

    def test_alpha_only_add_preserves_rgb(self):
        contribution = self._contribution(1, 0.0)
        contribution["diffuse"] = (0.0, 0.0, 0.0, -0.3)
        material, _, _ = MmdMaterialMorphEvalNode.compose(self._base(), [contribution])
        self.assertEqual(material["diffuse"][:3], self._base()["diffuse"][:3])
        self.assertAlmostEqual(material["diffuse"][3], 0.5)

    def test_semitransparent_alpha_multiply_is_not_squared(self):
        contribution = self._contribution(0, 1.0)
        contribution["diffuse"] = (1.0, 1.0, 1.0, 0.5)
        material, _, _ = MmdMaterialMorphEvalNode.compose(self._base(), [contribution])
        self.assertEqual(material["diffuse"][:3], self._base()["diffuse"][:3])
        self.assertAlmostEqual(material["diffuse"][3], 0.4)

    def test_all_texture_tracks_keep_multiply_and_add_separate(self):
        contributions = [self._contribution(0, 0.5), self._contribution(1, 0.2)]
        material, multiply, add = MmdMaterialMorphEvalNode.compose(self._base(), contributions)
        for name, values in material.items():
            for base_value, result in zip(self._base()[name], values):
                self.assertAlmostEqual(result, base_value * 0.5 + 0.2)
        for name in ("texture", "sphereTexture", "toonTexture"):
            self.assertEqual(tuple(multiply[name]), (0.5, 0.5, 0.5, 0.5))
            self.assertEqual(tuple(add[name]), (0.2, 0.2, 0.2, 0.2))

    def test_main_texture_alpha_factor_is_composed_as_rgba(self):
        multiply = self._contribution(0, 1.0)
        multiply["texture"] = (1.0, 1.0, 1.0, 0.5)
        additive = self._contribution(1, 0.0, order=1, slot=1)
        additive["texture"] = (0.0, 0.0, 0.0, 0.2)
        _, texture_multiply, texture_add = MmdMaterialMorphEvalNode.compose(
            self._base(), [multiply, additive]
        )
        self.assertEqual(tuple(texture_multiply["texture"]), (1.0, 1.0, 1.0, 0.5))
        self.assertEqual(tuple(texture_add["texture"]), (0.0, 0.0, 0.0, 0.2))
        sample_alpha = 0.8
        self.assertAlmostEqual(
            sample_alpha * texture_multiply["texture"][3] + texture_add["texture"][3],
            0.6,
        )
