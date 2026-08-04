"""Real MaterialTab edit and PMX fresh-import oracle test."""

import json
import tempfile
import unittest
from pathlib import Path

from tests.common.gui_test_base import GuiTestBase, requires_gui
from tools.export_release_maya_probe import (
    _capture_scene_oracle,
    _compare_scene_oracles,
    _fresh_import,
)

from mmd_tools.core import maya_attribute_utils
from mmd_tools.core.constants import (
    ATTR_MMD_SHARED_TOON_FLAG,
    ATTR_MMD_TEXTURE_TABLE_JSON,
    ATTR_MMD_TOON_PATH,
    ATTR_MMD_TOON_TEXTURE_INDEX,
)
from mmd_tools.core.pmx_data import PmxData
from mmd_tools.services.export_workflow_service import (
    ExportWorkflowRequest,
    ExportWorkflowService,
)


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "for_unit_test" / "test_1bone_cube.pmx"
CUSTOM_TOON_PATH = "textures/custom_toon.png"


def _first_material_node(root):
    """Return the first imported shader carrying canonical MMD material data."""
    from maya import cmds

    shapes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    for shape in shapes:
        for shading_group in cmds.listConnections(shape, type="shadingEngine") or []:
            for shader in cmds.listConnections(f"{shading_group}.surfaceShader") or []:
                if cmds.attributeQuery(ATTR_MMD_SHARED_TOON_FLAG, node=shader, exists=True):
                    return shader
    raise RuntimeError("Fresh-import scene contains no canonical MMD material shader")


def _edit_custom_toon_with_material_tab(root):
    """Author custom Toon path/index through the real MaterialTab controls."""
    from mmd_tools.ui.application_state import ApplicationState
    from mmd_tools.ui.presenters.material_presenter import MaterialPresenter
    from mmd_tools.ui.qt_compat import QApplication
    from mmd_tools.ui.tabs.material_tab import MaterialTab

    application = QApplication.instance() or QApplication([])
    view = MaterialTab()
    state = ApplicationState()
    presenter = MaterialPresenter(view, state)
    state.current_model_root = root
    presenter.load_materials()
    if view.material_list.count() < 1:
        raise RuntimeError("MaterialTab found no imported PMX materials")

    view.material_list.setCurrentRow(0)
    application.processEvents()
    item = view.material_list.currentItem()
    if not presenter.current_material and item is not None:
        presenter.on_material_selected(item, None)
    if not presenter.current_material:
        raise RuntimeError("MaterialTab did not select the first imported PMX material")

    from maya import cmds

    texture_table = json.loads(cmds.getAttr(f"{root}.{ATTR_MMD_TEXTURE_TABLE_JSON}"))
    custom_toon_index = texture_table.index(CUSTOM_TOON_PATH)
    view.toon_sharing_check.setChecked(False)
    view.toon_texture_path_edit.setText(CUSTOM_TOON_PATH)
    view.toon_texture_index_spin.setValue(custom_toon_index)
    application.processEvents()
    presenter.apply_changes()
    application.processEvents()

    material = presenter.current_material
    return {
        "material": material,
        "shared_toon_flag": maya_attribute_utils.get_attribute(material, ATTR_MMD_SHARED_TOON_FLAG),
        "toon_texture_index": maya_attribute_utils.get_attribute(material, ATTR_MMD_TOON_TEXTURE_INDEX),
        "toon_texture_path": maya_attribute_utils.get_attribute(material, ATTR_MMD_TOON_PATH),
        "texture_table": texture_table,
    }


@requires_gui
class TestMaterialExportRoundtripGUI(GuiTestBase):
    """Verify MaterialTab authoring survives PMX export and fresh import."""

    def test_material_tab_edit_survives_pmx_fresh_import_oracle(self):
        with tempfile.TemporaryDirectory(prefix="mmd_material_roundtrip_") as temp_dir:
            output_dir = Path(temp_dir)
            source_fixture = output_dir / "custom_toon_source.pmx"
            source_data = PmxData().parse_file(str(FIXTURE_PATH))
            source_data.textures = ["textures/initial_toon.png", CUSTOM_TOON_PATH]
            source_data.materials[0].shared_toon_flag = 0
            source_data.materials[0].toon_texture_index = 0
            source_data.materials[0].texture_index = 1
            source_data.write_file(str(source_fixture))
            source_root = _fresh_import(source_fixture)
            material_edit = _edit_custom_toon_with_material_tab(source_root)
            self.assertEqual(material_edit["shared_toon_flag"], 0)
            self.assertEqual(material_edit["toon_texture_index"], material_edit["texture_table"].index(CUSTOM_TOON_PATH))
            self.assertEqual(material_edit["toon_texture_path"], CUSTOM_TOON_PATH)
            source_oracle = _capture_scene_oracle(source_root, (0,))

            output = output_dir / "material_edit.pmx"
            report_dir = output_dir / "report"
            result = ExportWorkflowService().execute(
                ExportWorkflowRequest(
                    str(output),
                    {
                        "export_format": "pmx",
                        "require_target": True,
                        "target_model": source_root,
                        "target_identity": source_root,
                        "validation_report_dir": str(report_dir),
                        "validation_report_evidence": {
                            "gate": "V070-MATERIAL-EXPORT-COLLECTOR-1",
                            "fixture": FIXTURE_PATH.name,
                            "material_edit": material_edit,
                            "fresh_import": True,
                            "oracles": ["materials", "mesh", "pose", "metadata"],
                        },
                    },
                )
            )
            self.assertTrue(result.succeeded, result.error or str(result.report))
            self.assertTrue(output.is_file())
            parsed = PmxData()
            parsed.parse_file(str(output))
            parsed_material = parsed.materials[0]
            self.assertEqual(int(parsed_material.shared_toon_flag), 0)
            custom_toon_index = material_edit["toon_texture_index"]
            self.assertEqual(parsed_material.toon_texture_index, custom_toon_index)
            self.assertEqual(parsed.textures[custom_toon_index], CUSTOM_TOON_PATH)

            result_root = _fresh_import(output)
            result_oracle = _capture_scene_oracle(result_root, (0,))
            failures = _compare_scene_oracles(source_oracle, result_oracle, pose=True)
            self.assertEqual(failures, [], "; ".join(failures))
            result_material = _first_material_node(result_root)
            self.assertEqual(
                maya_attribute_utils.get_attribute(result_material, ATTR_MMD_SHARED_TOON_FLAG),
                0,
            )
            self.assertEqual(
                maya_attribute_utils.get_attribute(result_material, ATTR_MMD_TOON_TEXTURE_INDEX),
                custom_toon_index,
            )
            self.assertEqual(
                maya_attribute_utils.get_attribute(result_material, ATTR_MMD_TOON_PATH),
                CUSTOM_TOON_PATH,
            )


if __name__ == "__main__":
    unittest.main()
