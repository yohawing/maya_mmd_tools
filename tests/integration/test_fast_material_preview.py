"""Mesh-only material creation uses the normal texture and metadata boundary."""

from pathlib import Path

from maya import cmds

from mmd_tools.converters.export_scene_collector import _collect_mmd_material_dict
from mmd_tools.core.native.native_pmx_parser import parse_pmx_native
from mmd_tools.io.cpp_fast_importer import _apply_basic_materials
from tests.common.maya_test_base import MayaTestBase


class TestFastMaterialPreview(MayaTestBase):
    def test_main_texture_and_canonical_edits_survive_reload(self):
        import tempfile

        root = Path(__file__).resolve().parents[2]
        model = root / "tests/data/test_morph_model.pmx"
        pmx = parse_pmx_native(str(model))
        self.assertIsNotNone(pmx)
        texture = root / "tests/data/tex/diffuse.png"
        pmx.textures = [str(texture)]
        for material in pmx.materials:
            material.texture_index = 0
        mesh = cmds.polyCube()[0]
        metadata = _apply_basic_materials(str(model), mesh, cmds, native_pmx=pmx)
        self.assertIsNotNone(metadata)
        shaders = cmds.ls("*.mmd_material_index", objectsOnly=True)
        self.assertEqual(len(shaders), len(pmx.materials))
        shader = shaders[0]
        files = cmds.listConnections(shader + ".baseColor", s=True, d=False, type="file")
        self.assertEqual(len(files), 1)
        file = files[0]
        self.assertEqual(Path(cmds.getAttr(file + ".fileTextureName")), texture)
        cmds.setAttr(shader + ".diffuse_color", .2, .4, .6, type="double3")
        for actual, expected in zip(cmds.getAttr(file + ".colorGain")[0], (.2, .4, .6)):
            self.assertAlmostEqual(actual, expected, places=6)
        original = _collect_mmd_material_dict(shader)
        with tempfile.TemporaryDirectory() as directory:
            scene = str(Path(directory) / "fast.ma")
            cmds.file(rename=scene)
            cmds.file(save=True, type="mayaAscii", force=True)
            cmds.file(scene, open=True, force=True)
            self.assertTrue(cmds.isConnected(file + ".outColor", shader + ".baseColor"))
            self.assertEqual(_collect_mmd_material_dict(shader), original)
