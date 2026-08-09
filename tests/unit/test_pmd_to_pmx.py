import os
from unittest.mock import patch

from mmd_tools.core import mmd_parser
from mmd_tools.core.pmd_data import PmdData
from mmd_tools.core.pmd_to_pmx import convert_pmd_to_pmx_data
from mmd_tools.core.pmx_data import PmxData
from mmd_tools.core.pmx_data.material import PmxDrawFlag
from tests.common.pmd_mock import PmdMock
from tests.common.test_base import TestBase


class TestPmdToPmx(TestBase):
    def _write_full_pmd(self):
        path = os.path.join(self.temp_dir, "full_model.pmd")
        with open(path, "wb") as f:
            f.write(PmdMock.create_full_pmd())
        return path

    def test_convert_pmd_to_pmx_preserves_import_sections(self):
        """PMD import に必要な主要セクションを PMX data へ変換する。"""
        pmd = PmdData().parse_file(self._write_full_pmd())

        pmx = convert_pmd_to_pmx_data(pmd)

        self.assertIsInstance(pmx, PmxData)
        self.assertEqual(pmx.header.magic, b"PMX ")
        self.assertEqual(len(pmx.vertices), len(pmd.vertices))
        self.assertEqual(len(pmx.faces), len(pmd.faces))
        self.assertEqual(len(pmx.materials), len(pmd.materials))
        self.assertEqual(len(pmx.bones), len(pmd.bones))
        self.assertEqual(len(pmx.morphs), 2)
        self.assertEqual(len(pmx.rigid_bodies), len(pmd.rigid_bodies))
        self.assertEqual(len(pmx.joints), len(pmd.joints))
        self.assertEqual(
            [int(bool(material.draw_flag & PmxDrawFlag.EDGE_DRAWING)) for material in pmx.materials],
            [int(material.edge_flag) for material in pmd.materials],
        )
        self.assertTrue(any(bone.ik_links for bone in pmx.bones))
        self.assertGreater(len(pmx.display_frames), 0)

    def test_convert_pmd_to_pmx_preserves_toon_disabled_special_value(self):
        """PMD toon 0xFF は shared toon10 へ丸めず toon 無効として保持する。"""
        pmd = PmdData().parse_file(self._write_full_pmd())
        pmd.materials[0].toon_texture_index = 0xFF

        pmx = convert_pmd_to_pmx_data(pmd)

        material = pmx.materials[0]
        self.assertEqual(int(material.shared_toon_flag), 0)
        self.assertEqual(material.toon_texture_index, -1)

    def test_parse_pmd_file_as_pmx_routes_converted_temp_file_to_pmx_parser(self):
        """PMD 専用入口は変換済み一時 PMX を PMX parser policy に渡す。"""
        pmd_path = self._write_full_pmd()

        def _fake_parse_pmx_file(file_path, use_native_pmx_parse=None, require_native_pmx_parse=False):
            self.assertTrue(os.path.exists(file_path))
            self.assertTrue(file_path.endswith(".pmx"))
            self.assertTrue(require_native_pmx_parse)
            with open(file_path, "rb") as f:
                self.assertEqual(f.read(4), b"PMX ")
            return PmxData()

        with patch("mmd_tools.core.mmd_parser.parse_pmx_file", side_effect=_fake_parse_pmx_file) as parser:
            parsed = mmd_parser.parse_pmd_file_as_pmx(pmd_path)

        self.assertIsInstance(parsed, PmxData)
        parser.assert_called_once()

    def test_parse_mmd_file_returns_pmx_for_pmd(self):
        """汎用 parser の PMD 分岐は PmdData ではなく PMX data を返す。"""
        parsed = mmd_parser.parse_mmd_file(
            self._write_full_pmd(),
            require_native_pmx_parse=False,
        )

        self.assertIsInstance(parsed, PmxData)
        self.assertEqual(parsed.header.magic, b"PMX ")
