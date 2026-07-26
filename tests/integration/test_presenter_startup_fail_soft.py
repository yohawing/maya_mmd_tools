"""Maya startup scans tolerate absent optional presenter plugs."""

import unittest

from maya import cmds

from mmd_tools.adapters.maya_cmds_adapter import MayaCmdsAdapter
from mmd_tools.ui.presenters.morph_presenter import MorphPresenter
from mmd_tools.ui.presenters.physics_presenter import _resolve_message_name


class TestPresenterStartupFailSoftMaya(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)

    def tearDown(self):
        cmds.file(new=True, force=True)

    def test_legacy_physics_node_without_related_bone_is_unbound(self):
        shape = cmds.createNode("network", name="legacyRigidShape")

        self.assertEqual(_resolve_message_name(shape, "relatedBone"), "")

    def test_uninstantiated_sparse_output_weight_is_unsupported(self):
        controller = cmds.createNode("network", name="partialMorphController")
        cmds.addAttr(
            controller,
            longName="outputWeight",
            attributeType="double",
            multi=True,
        )
        presenter = object.__new__(MorphPresenter)
        presenter.maya_adapter = MayaCmdsAdapter(cmds)
        presenter._morph_controller = controller
        presenter._morph_capability_cache = {}

        self.assertFalse(
            presenter._morph_controls_supported(
                {"type": 8, "_pmx_type_raw": True, "index": 85}
            )
        )


if __name__ == "__main__":
    unittest.main()
