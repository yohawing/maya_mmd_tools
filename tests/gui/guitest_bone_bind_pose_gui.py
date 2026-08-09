"""Bone tab Bind Pose session E2E with an imported PMX and VMD."""

from pathlib import Path
import unittest

from maya import cmds

from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.ui.application_state import ApplicationState
from mmd_tools.ui.presenters.bone_presenter import BonePresenter
from mmd_tools.ui.qt_compat import QApplication
from mmd_tools.ui.tabs.bone_tab import BoneTab
from tests.common.gui_test_base import GuiTestBase, requires_gui


ROOT = Path(__file__).resolve().parents[2]
PMX = ROOT / "tests" / "data" / "for_unit_test" / "test_1bone_cube.pmx"
VMD = ROOT / "tests" / "data" / "for_unit_test" / "test_1bone_cube_motion.vmd"


def _incoming_transform_edges(joints):
    """Return canonical incoming transform edges for the supplied joints."""
    attributes = {
        *(f"{name}{axis}" for name in ("translate", "rotate", "scale") for axis in "XYZ"),
        "translate",
        "rotate",
        "scale",
        "shear",
        "shearXY",
        "shearXZ",
        "shearYZ",
        "offsetParentMatrix",
    }
    edges = set()
    for joint in joints:
        pairs = cmds.listConnections(
            joint,
            source=True,
            destination=False,
            plugs=True,
            connections=True,
        ) or []
        if len(pairs) % 2:
            raise AssertionError(f"Invalid Maya connection pair data: {joint}")
        for index in range(0, len(pairs), 2):
            destination, source = str(pairs[index]), str(pairs[index + 1])
            if destination.rsplit(".", 1)[-1] in attributes:
                edges.add((source, destination))
    return edges


@requires_gui
class TestBoneBindPoseGUI(GuiTestBase):
    """Exercise the actual Bone-tab button with imported motion writers."""

    def test_imported_pmx_vmd_returns_to_exact_motion_graph(self):
        cmds.file(new=True, force=True)
        tab = None
        presenter = None

        def cleanup():
            errors = []
            operations = []
            if presenter is not None:
                operations.append(
                    lambda: self.assertTrue(
                        presenter.disconnect_signals(),
                        "Return to Motion failed during GUI cleanup",
                    )
                )
            if tab is not None:
                operations.extend((tab.hide, tab.close, tab.deleteLater, QApplication.processEvents))
            operations.append(lambda: cmds.file(new=True, force=True))
            for operation in operations:
                try:
                    operation()
                except Exception as exc:
                    errors.append(str(exc))
            if errors:
                self.fail("; ".join(errors))

        self.addCleanup(cleanup)
        root = import_mmd_file(
            str(PMX),
            options={"setup_rig": False, "setup_bone_orientation": False},
        )
        self.assertTrue(root)
        cmds.select(root, replace=True)
        import_mmd_file(
            str(VMD),
            options={"target_model": root, "pmx_path": str(PMX), "bake_mode": True},
        )

        joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
        self.assertTrue(joints)
        cmds.currentTime(10, edit=True)
        animated_matrix = tuple(cmds.xform(joints[0], query=True, matrix=True, worldSpace=True))
        original_edges = _incoming_transform_edges(joints)
        self.assertTrue(original_edges)

        tab = BoneTab()
        app_state = ApplicationState()
        presenter = BonePresenter(tab, app_state)
        app_state._current_model_root = root
        presenter.load_bones()
        tab.show()
        QApplication.processEvents()

        tab.bind_pose_btn.click()
        QApplication.processEvents()
        self.assertTrue(presenter.bind_pose_action.active)
        self.assertEqual(tab.bind_pose_btn.property("mmdBindPoseActive"), True)
        self.assertFalse(_incoming_transform_edges(joints))
        bind_matrix = tuple(cmds.xform(joints[0], query=True, matrix=True, worldSpace=True))

        cmds.currentTime(20, edit=True)
        QApplication.processEvents()
        self.assertEqual(
            tuple(cmds.xform(joints[0], query=True, matrix=True, worldSpace=True)),
            bind_matrix,
        )

        tab.bind_pose_btn.click()
        QApplication.processEvents()
        self.assertFalse(presenter.bind_pose_action.active)
        self.assertEqual(tab.bind_pose_btn.property("mmdBindPoseActive"), False)
        self.assertEqual(cmds.currentTime(query=True), 10.0)
        self.assertEqual(_incoming_transform_edges(joints), original_edges)
        self.assertEqual(
            tuple(cmds.xform(joints[0], query=True, matrix=True, worldSpace=True)),
            animated_matrix,
        )


if __name__ == "__main__":
    unittest.main()
