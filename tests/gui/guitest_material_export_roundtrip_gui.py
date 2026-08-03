"""Real MaterialTab edit and PMX fresh-import oracle test."""

import tempfile
import unittest
from pathlib import Path

from tests.common.gui_test_base import GuiTestBase, requires_gui
from tools.export_release_maya_probe import (
    _capture_scene_oracle,
    _compare_scene_oracles,
    _edit_first_material_with_material_tab,
    _fresh_import,
)

from mmd_tools.core.pmx_data import PmxData
from mmd_tools.services.export_workflow_service import (
    ExportWorkflowRequest,
    ExportWorkflowService,
)


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "for_unit_test" / "test_1bone_cube.pmx"


@requires_gui
class TestMaterialExportRoundtripGUI(GuiTestBase):
    """Verify MaterialTab authoring survives PMX export and fresh import."""

    def test_material_tab_edit_survives_pmx_fresh_import_oracle(self):
        with tempfile.TemporaryDirectory(prefix="mmd_material_roundtrip_") as temp_dir:
            output_dir = Path(temp_dir)
            source_root = _fresh_import(FIXTURE_PATH)
            material_edit = _edit_first_material_with_material_tab(source_root)
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
            PmxData().parse_file(str(output))

            result_root = _fresh_import(output)
            result_oracle = _capture_scene_oracle(result_root, (0,))
            failures = _compare_scene_oracles(source_oracle, result_oracle, pose=True)
            self.assertEqual(failures, [], "; ".join(failures))
            self.assertGreater(abs(material_edit["after"] - material_edit["before"]), 1.0e-4)


if __name__ == "__main__":
    unittest.main()
