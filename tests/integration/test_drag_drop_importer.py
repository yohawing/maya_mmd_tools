"""Drag-and-drop VMD target resolution through the real Maya import pipeline."""

from pathlib import Path
from unittest import mock

from maya import cmds

from mmd_tools.core import settings
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.services.scene_model_service import SceneModelService
from mmd_tools.ui.drag_drop_importer import import_dropped_files
from tests.common.maya_test_base import MayaTestBase


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "data"
PMX_PATH = FIXTURE_DIR / "mmt_test_model.pmx"
VMD_PATH = FIXTURE_DIR / "mmt_test_model_test_motion.vmd"


def _animated_curves(root: str) -> set[str]:
    nodes = [root]
    nodes.extend(cmds.listRelatives(root, allDescendents=True, fullPath=True) or [])
    curves = set()
    for node in nodes:
        curves.update(cmds.listConnections(node, source=True, destination=False, type="animCurve") or [])
    return curves


class TestDragDropImporter(MayaTestBase):
    """VMD drops fail closed unless a unique target model can be resolved."""

    def setUp(self):
        super().setUp()
        settings.set("import.model.create_mmd_shaders", False)

    def _import_model(self, namespace: str) -> str:
        root = import_mmd_file(
            str(PMX_PATH),
            options={
                "custom_namespace": namespace,
                "use_namespace": True,
                "create_mmd_shaders": False,
                "setup_physics": False,
            },
        )
        self.assertTrue(root)
        return str(root)

    def test_vmd_only_drop_targets_the_sole_loaded_model(self):
        root = self._import_model("sole")
        cmds.select(clear=True)

        self.assertTrue(import_dropped_files([str(VMD_PATH)]))
        self.assertTrue(_animated_curves(root))

    def test_multiple_models_require_explicit_selection_and_preserve_other_model(self):
        root_a = self._import_model("model_a")
        root_b = self._import_model("model_b")
        cmds.select(clear=True)
        scene_models = SceneModelService().list_mmd_models()
        self.assertEqual(set(scene_models), {root_a, root_b})

        with mock.patch("mmd_tools.ui.drag_drop_importer._display_warning") as warning:
            self.assertFalse(
                import_dropped_files(
                    [str(VMD_PATH)],
                    scene_model_service=SceneModelService(),
                )
            )
        self.assertIn("select one MMD model", warning.call_args.args[0])
        self.assertFalse(_animated_curves(root_a))
        self.assertFalse(_animated_curves(root_b))

        target_joint = (cmds.listRelatives(root_b, allDescendents=True, type="joint", fullPath=True) or [])[0]
        cmds.select(target_joint, replace=True)
        self.assertTrue(import_dropped_files([str(VMD_PATH)]))
        self.assertFalse(_animated_curves(root_a))
        self.assertTrue(_animated_curves(root_b))
