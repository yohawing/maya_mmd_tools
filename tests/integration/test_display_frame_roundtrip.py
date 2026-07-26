"""実PMXとMaya sceneを介した表示枠metadataの往復を検証する。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from maya import cmds

from mmd_tools.converters.export_scene_collector import ExportSceneCollector
from mmd_tools.core.constants import ATTR_MMD_DISPLAY_FRAMES_JSON
from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.io.pmx_exporter import PmxExporter
from tests.common.maya_test_base import MayaTestBase

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "physics" / "test_hair_physics.pmx"


@unittest.skipUnless(FIXTURE_PATH.exists(), "display-frame fixture not found")
class TestDisplayFrameRoundTrip(MayaTestBase):
    """表示枠の順序、名称、flag、bone/morph要素を実ファイルで保持する。"""

    @staticmethod
    def _import_fixture(path: Path) -> str:
        return import_mmd_file(
            str(path),
            options={
                "import_physics": False,
                "create_mmd_shaders": False,
                "setup_rig": False,
                "use_cpp_fast_load": False,
                "use_native_pmx_parse": False,
                "require_native_pmx_parse": False,
            },
        )

    def test_edits_survive_scene_save_export_and_fresh_reimport(self):
        root = self._import_fixture(FIXTURE_PATH)
        plug = f"{root}.{ATTR_MMD_DISPLAY_FRAMES_JSON}"
        frames = json.loads(cmds.getAttr(plug))
        edited_frame = {
            "name": "編集枠",
            "name_english": "Edited Frame",
            "special_flag": 0,
            "elements": [{"type": 0, "index": 0}, {"type": 1, "index": 1}],
        }
        frames.insert(2, edited_frame)
        expected_json = json.dumps(frames, ensure_ascii=False, separators=(",", ":"))
        cmds.setAttr(plug, expected_json, type="string")

        with tempfile.TemporaryDirectory() as temp_dir:
            scene_path = Path(temp_dir) / "display_frame_roundtrip.ma"
            export_path = Path(temp_dir) / "display_frame_roundtrip.pmx"
            cmds.file(rename=str(scene_path))
            cmds.file(save=True, type="mayaAscii", force=True)
            cmds.file(new=True, force=True)
            cmds.file(str(scene_path), open=True, force=True)

            self.assertEqual(json.loads(cmds.getAttr(plug)), frames)
            collected = ExportSceneCollector().collect_from_model_root(root)
            PmxExporter().export_pmx_model(str(export_path), collected)
            exported = parse_pmx_file(str(export_path), use_native_pmx_parse=False)
            self.assertEqual(exported.display_frames[2].name, "編集枠")
            self.assertEqual(exported.display_frames[2].name_english, "Edited Frame")
            self.assertEqual(exported.display_frames[2].special_flag, 0)
            self.assertEqual(exported.display_frames[2].elements, edited_frame["elements"])

            cmds.file(new=True, force=True)
            reimported_root = self._import_fixture(export_path)
            reimported_frames = json.loads(
                cmds.getAttr(f"{reimported_root}.{ATTR_MMD_DISPLAY_FRAMES_JSON}")
            )

        self.assertEqual(reimported_frames, frames)


if __name__ == "__main__":
    unittest.main()
