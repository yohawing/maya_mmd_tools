"""PMX import model-root morph metadata regression tests."""

import json
import unittest
from unittest.mock import MagicMock, patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.core.pmx_data.morph import PmxMorphType  # noqa: E402
from mmd_tools.converters.morph_converter import MorphConverter  # noqa: E402
from mmd_tools.io.pmx_importer import _serialize_pmx_morph_data  # noqa: E402
from mmd_tools.core.morph_metadata_reader import morph_info_from_presenter_entry  # noqa: E402
from mmd_tools.converters.morph_scene_metadata import (  # noqa: E402
    read_blendshape_morph_entries,
    read_blendshape_morph_names,
)
from mmd_tools.converters import export_scene_collector  # noqa: E402
from mmd_tools.core.native.runtime_node_connector import (  # noqa: E402
    _connect_blendshape_morph_outputs,
)


class _Morph:
    def __init__(self, name, name_english, panel, morph_type):
        self.name = name
        self.name_english = name_english
        self.panel = panel
        self.morph_type = morph_type


class TestPmxMorphMetadataImport(unittest.TestCase):
    @patch("mmd_tools.converters.morph_converter.cmds.allNodeTypes", return_value=[])
    def test_morph_runtime_preflight_rejects_missing_controller_before_import(self, _all_node_types):
        converter = MorphConverter()
        converter.settings = {"import_morphs": True}
        pmx_data = type("PmxData", (), {"morphs": [object()]})()

        with self.assertRaisesRegex(RuntimeError, "Load or reload.*plugin"):
            converter.validate_runtime_requirements(pmx_data)

    @patch("mmd_tools.converters.morph_converter.cmds.allNodeTypes", return_value=[])
    def test_morph_runtime_preflight_skips_when_morph_import_is_disabled(self, _all_node_types):
        converter = MorphConverter()
        converter.settings = {"import_morphs": False}
        pmx_data = type("PmxData", (), {"morphs": [object()]})()

        converter.validate_runtime_requirements(pmx_data)

    def test_serializes_all_authoritative_fields_without_custom_group(self):
        encoded = _serialize_pmx_morph_data(
            [
                _Morph("眉上げ", "brow_up", 1, PmxMorphType.VertexMorph),
                _Morph("目UV", "eye_uv", 2, PmxMorphType.UVMorph),
                _Morph("表示", "visibility", 4, PmxMorphType.MaterialMorph),
            ]
        )

        metadata = json.loads(encoded)
        self.assertEqual(
            metadata[0],
            {"name_jp": "眉上げ", "name_en": "brow_up", "panel": 1, "type": 1, "index": 0},
        )
        self.assertEqual(metadata[1]["type"], 3)
        self.assertEqual(metadata[2]["index"], 2)
        self.assertTrue(all("group" not in entry for entry in metadata))

    def test_missing_morph_list_serializes_as_empty_metadata(self):
        self.assertEqual(json.loads(_serialize_pmx_morph_data(None)), [])

    def test_duplicate_and_empty_names_remain_lossless(self):
        metadata = json.loads(
            _serialize_pmx_morph_data(
                [
                    _Morph("笑顔", "smile_a", 2, PmxMorphType.VertexMorph),
                    _Morph("笑顔", "smile_b", 3, PmxMorphType.VertexMorph),
                    _Morph("", "unnamed", 4, PmxMorphType.VertexMorph),
                ]
            )
        )

        self.assertEqual(len(metadata), 3)
        self.assertEqual([entry["index"] for entry in metadata], [0, 1, 2])
        self.assertEqual([entry["name_jp"] for entry in metadata], ["笑顔", "笑顔", ""])

    @patch("mmd_tools.converters.morph_scene_metadata.maya_attribute_utils.read_json_attr")
    @patch("mmd_tools.converters.morph_scene_metadata.maya_attribute_utils.attribute_exists", return_value=True)
    def test_blendshape_reader_supports_new_and_legacy_entries(self, _exists, read_json):
        read_json.return_value = {
            "0": {"name": "笑顔", "index": 7},
            "3": "legacyBlink",
        }

        self.assertEqual(
            read_blendshape_morph_entries("faceBlendShape"),
            {0: {"name": "笑顔", "index": 7}, 3: {"name": "legacyBlink"}},
        )
        self.assertEqual(
            read_blendshape_morph_names("faceBlendShape"),
            {0: "笑顔", 3: "legacyBlink"},
        )

    def test_raw_pmx_type_is_not_interpreted_as_legacy_ui_index(self):
        info = morph_info_from_presenter_entry(
            "material",
            {"type": 8, "_pmx_type_raw": True, "panel": 4, "index": 2},
        )
        self.assertEqual(info.morph_type, "material")

    def test_export_collector_keeps_exact_raw_names_from_object_schema(self):
        payload = {"0": {"name": "笑顔/生名", "index": 7}, "2": {"name": "", "index": 9}}
        fake_cmds = MagicMock()
        fake_cmds.attributeQuery.return_value = True
        fake_cmds.getAttr.return_value = json.dumps(payload, ensure_ascii=False)

        with patch.object(export_scene_collector, "cmds", fake_cmds):
            names = export_scene_collector._blendshape_stored_names("faceBlendShape")

        self.assertEqual(names, {0: "笑顔/生名", 2: ""})

    def test_native_runtime_connector_uses_global_index_with_object_schema(self):
        class _Cmds:
            connections = []

            @staticmethod
            def blendShape(*_args, **_kwargs):
                return 2

            @staticmethod
            def attributeQuery(*_args, **_kwargs):
                return True

            @staticmethod
            def getAttr(*_args, **_kwargs):
                # Local order is reversed relative to PMX global indices.
                return json.dumps(
                    {
                        "0": {"name": "duplicate", "index": 7},
                        "1": {"name": "duplicate", "index": 4},
                    }
                )

            @staticmethod
            def aliasAttr(*_args, **_kwargs):
                return None

            @staticmethod
            def listConnections(*_args, **_kwargs):
                return []

            @classmethod
            def connectAttr(cls, source, destination, **_kwargs):
                cls.connections.append((source, destination))

        result = {"connected_morphs": [], "warnings": []}
        _connect_blendshape_morph_outputs(
            _Cmds,
            "runtime",
            "faceBlendShape",
            ["duplicate", "duplicate"],
            {0: 4, 1: 7},
            lambda name: name,
            result,
        )

        self.assertEqual(
            _Cmds.connections,
            [
                ("runtime.morphWeights[4]", "faceBlendShape.weight[1]"),
                ("runtime.morphWeights[7]", "faceBlendShape.weight[0]"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
