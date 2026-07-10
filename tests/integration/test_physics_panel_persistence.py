"""Physics panel editable values survive Maya scene save / new / open.

Gate for Physics panel closeout: applied rigid/joint scalars and names are
written through MayaPhysicsSceneWriter, then re-read via MayaPhysicsSceneReader
from the reopened scene (the real source of truth). Graph-dependent fields stay
read-only and must not change.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from maya import cmds

from mmd_tools.adapters.maya_cmds_adapter import MayaCmdsAdapter
from mmd_tools.converters import PhysicsConverter
from mmd_tools.core.constants import ATTR_MMD_MODEL_NAME, ATTR_MMD_SHOW_PHYSICS_COLLIDERS
from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.core.physics_form_validation import JointFormValues, RigidBodyFormValues
from mmd_tools.core.physics_scene_query import MayaPhysicsSceneReader
from mmd_tools.core.physics_scene_writer import MayaPhysicsSceneWriter
from mmd_tools.core import settings
from mmd_tools.io.pmx_importer import import_pmx_file
from tests.common.maya_test_base import MayaTestBase


HAIR_PHYSICS_FIXTURE = Path(__file__).resolve().parents[1] / "data" / "physics" / "test_hair_physics.pmx"


def _almost_equal(a, b, places=5):
    return abs(float(a) - float(b)) < 10 ** (-places)


class TestPhysicsPanelPersistence(MayaTestBase):
    """Maya scene save/reopen gate for PhysicsTab writer / reader contract."""

    def setUp(self):
        super().setUp()
        cmds.file(new=True, force=True)
        # Save persistent optionVar / Undo state so this suite never leaks to peers.
        self._previous_create_mmd_shaders = settings.get(
            "import.model.create_mmd_shaders", True
        )
        try:
            self._previous_undo_enabled = bool(cmds.undoInfo(query=True, state=True))
        except Exception:
            self._previous_undo_enabled = True
        # LIFO: restore state after temp cleanup / scene reset from tearDown.
        self.addCleanup(self._restore_persistent_state)
        settings.set("import.model.create_mmd_shaders", False)
        self._temp_paths = []

    def _restore_persistent_state(self):
        """Restore optionVar setting and Maya Undo; assert both took effect."""
        settings.set(
            "import.model.create_mmd_shaders",
            self._previous_create_mmd_shaders,
        )
        actual_shaders = settings.get("import.model.create_mmd_shaders", True)
        self.assertEqual(
            actual_shaders,
            self._previous_create_mmd_shaders,
            "import.model.create_mmd_shaders was not restored after test",
        )
        cmds.undoInfo(stateWithoutFlush=bool(self._previous_undo_enabled))
        actual_undo = bool(cmds.undoInfo(query=True, state=True))
        self.assertEqual(
            actual_undo,
            bool(self._previous_undo_enabled),
            "Maya Undo enabled state was not restored after test",
        )

    def tearDown(self):
        for path in self._temp_paths:
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass
        try:
            cmds.file(new=True, force=True)
        except Exception:
            pass
        super().tearDown()

    def _temp_ma_path(self):
        handle, path = tempfile.mkstemp(prefix="mmd_physics_panel_", suffix=".ma")
        os.close(handle)
        self._temp_paths.append(path)
        return path

    def _require_bullet(self):
        if not PhysicsConverter.is_bullet_available():
            self.skipTest("Bullet plugin is unavailable")

    def _import_hair_fixture(self):
        parser = parse_pmx_file(str(HAIR_PHYSICS_FIXTURE))
        root = import_pmx_file(
            parser,
            str(HAIR_PHYSICS_FIXTURE),
            options={
                "import_physics": True,
                "create_physics_joints": True,
                "create_mmd_shaders": False,
            },
        )
        self.assertTrue(root, "PMX import did not return a model root")
        self.assertTrue(cmds.objExists(root), "imported root missing")
        return root

    def _find_mmd_roots(self):
        roots = []
        for node in cmds.ls(type="transform", long=True) or []:
            if cmds.attributeQuery(ATTR_MMD_MODEL_NAME, node=node, exists=True):
                roots.append(node)
        return roots

    def _dynamic_rigid_body(self, refs):
        for body in refs.rigid_bodies:
            if body.physics_mode == 2 and body.mass >= 0.0:
                return body
        self.fail("No dynamic rigid body found after import")

    def _connected_joint(self, refs):
        for joint in refs.joints:
            if joint.rigid_body_a_index >= 0 and joint.rigid_body_b_index >= 0:
                return joint
        self.fail("No connected joint found after import")

    def _rigid_form_from_ref(self, ref, **overrides):
        values = {
            "name": ref.name,
            "name_english": ref.name_english,
            "shape_type": ref.shape_type,
            "physics_mode": ref.physics_mode,
            "related_bone_index": ref.related_bone_index,
            "collision_group": ref.collision_group,
            "collision_mask": ref.collision_mask,
            "mass": ref.mass,
            "linear_damping": ref.linear_damping,
            "angular_damping": ref.angular_damping,
            "restitution": ref.restitution,
            "friction": ref.friction,
        }
        values.update(overrides)
        return RigidBodyFormValues(**values)

    def _joint_form_from_ref(self, ref, **overrides):
        values = {
            "name": ref.name,
            "name_english": ref.name_english,
            "joint_type": ref.joint_type,
            "rigid_body_a_index": ref.rigid_body_a_index,
            "rigid_body_b_index": ref.rigid_body_b_index,
            "linear_constraint_states": ref.linear_constraint_states,
            "angular_constraint_states": ref.angular_constraint_states,
            "translation_limit_min": ref.translation_limit_min,
            "translation_limit_max": ref.translation_limit_max,
            "rotation_limit_min_degrees": ref.rotation_limit_min_degrees,
            "rotation_limit_max_degrees": ref.rotation_limit_max_degrees,
            "spring_translation": ref.spring_translation,
            "spring_rotation": ref.spring_rotation,
            "spring_translation_enabled": ref.spring_translation_enabled,
            "spring_rotation_enabled": ref.spring_rotation_enabled,
        }
        values.update(overrides)
        return JointFormValues(**values)

    def test_applied_editable_physics_values_survive_scene_save_reopen(self):
        """Apply editable fields, save/new/open, then read back from scene attrs."""
        self._require_bullet()
        root = self._import_hair_fixture()

        adapter = MayaCmdsAdapter()
        reader = MayaPhysicsSceneReader(adapter)
        writer = MayaPhysicsSceneWriter(adapter)

        # Writer requires undo so Apply can roll back atomically.
        cmds.undoInfo(stateWithoutFlush=True)
        self.assertTrue(cmds.undoInfo(query=True, state=True))

        before = reader.collect(root)
        self.assertGreaterEqual(len(before.rigid_bodies), 1)
        self.assertGreaterEqual(len(before.joints), 1)

        rigid = self._dynamic_rigid_body(before)
        joint = self._connected_joint(before)

        # Distinctive editable values (graph-dependent fields stay at scene values).
        rigid_target = self._rigid_form_from_ref(
            rigid,
            name="persist_rb_jp",
            name_english="Persist RB EN",
            mass=3.125,
            linear_damping=0.17,
            angular_damping=0.27,
            restitution=0.37,
            friction=0.47,
        )
        joint_target = self._joint_form_from_ref(
            joint,
            name="persist_joint_jp",
            name_english="Persist Joint EN",
            linear_constraint_states=(0, 1, 2),
            angular_constraint_states=(2, 1, 0),
            translation_limit_min=(-1.25, -2.25, -3.25),
            translation_limit_max=(1.25, 2.25, 3.25),
            rotation_limit_min_degrees=(-11.0, -22.0, -33.0),
            rotation_limit_max_degrees=(11.0, 22.0, 33.0),
            spring_translation=(0.11, 0.22, 0.33),
            spring_rotation=(0.44, 0.55, 0.66),
            spring_translation_enabled=(True, False, True),
            spring_rotation_enabled=(False, True, False),
        )

        # Root collider visibility is the panel's scene source of truth (not in-memory).
        # Import with physics must always install this attr; do not soft-skip.
        self.assertTrue(
            cmds.attributeQuery(ATTR_MMD_SHOW_PHYSICS_COLLIDERS, node=root, exists=True),
            "imported root must expose {0} after physics import".format(
                ATTR_MMD_SHOW_PHYSICS_COLLIDERS
            ),
        )
        cmds.setAttr("{0}.{1}".format(root, ATTR_MMD_SHOW_PHYSICS_COLLIDERS), True)

        writer.apply_rigid_body(rigid, rigid_target)
        writer.apply_joint(joint, joint_target)

        applied = reader.collect(root)
        rigid_applied = next(body for body in applied.rigid_bodies if body.transform == rigid.transform)
        joint_applied = next(item for item in applied.joints if item.transform == joint.transform)

        self.assertEqual(rigid_applied.name, "persist_rb_jp")
        self.assertEqual(rigid_applied.name_english, "Persist RB EN")
        self.assertTrue(_almost_equal(rigid_applied.mass, 3.125))
        self.assertTrue(_almost_equal(rigid_applied.linear_damping, 0.17))
        self.assertTrue(_almost_equal(rigid_applied.angular_damping, 0.27))
        self.assertTrue(_almost_equal(rigid_applied.restitution, 0.37))
        self.assertTrue(_almost_equal(rigid_applied.friction, 0.47))
        # Read-only / graph-dependent fields must remain the original scene values.
        self.assertEqual(rigid_applied.shape_type, rigid.shape_type)
        self.assertEqual(rigid_applied.physics_mode, rigid.physics_mode)
        self.assertEqual(rigid_applied.related_bone_index, rigid.related_bone_index)
        self.assertEqual(rigid_applied.collision_group, rigid.collision_group)
        self.assertEqual(rigid_applied.collision_mask, rigid.collision_mask)

        self.assertEqual(joint_applied.name, "persist_joint_jp")
        self.assertEqual(joint_applied.name_english, "Persist Joint EN")
        self.assertEqual(joint_applied.linear_constraint_states, (0, 1, 2))
        self.assertEqual(joint_applied.angular_constraint_states, (2, 1, 0))
        self.assertEqual(joint_applied.spring_translation_enabled, (True, False, True))
        self.assertEqual(joint_applied.spring_rotation_enabled, (False, True, False))
        self.assertEqual(joint_applied.joint_type, joint.joint_type)
        self.assertEqual(joint_applied.rigid_body_a_index, joint.rigid_body_a_index)
        self.assertEqual(joint_applied.rigid_body_b_index, joint.rigid_body_b_index)

        scene_path = self._temp_ma_path()
        cmds.file(rename=scene_path)
        cmds.file(save=True, type="mayaAscii")
        self.assertTrue(os.path.isfile(scene_path))

        cmds.file(new=True, force=True)
        self.assertFalse(self._find_mmd_roots())

        # Re-open must load Bullet so shape attrs resolve as the scene source of truth.
        if not cmds.pluginInfo("bullet", query=True, loaded=True):
            cmds.loadPlugin("bullet", quiet=True)
        cmds.file(scene_path, open=True, force=True)

        roots = self._find_mmd_roots()
        self.assertEqual(len(roots), 1, "expected exactly one MMD root after reopen")
        reopened_root = roots[0]

        reopened = reader.collect(reopened_root)
        self.assertGreaterEqual(len(reopened.rigid_bodies), 1)
        self.assertGreaterEqual(len(reopened.joints), 1)

        rigid_after = next(
            body for body in reopened.rigid_bodies if body.name == "persist_rb_jp" or body.name_english == "Persist RB EN"
        )
        joint_after = next(
            item
            for item in reopened.joints
            if item.name == "persist_joint_jp" or item.name_english == "Persist Joint EN"
        )

        self.assertEqual(rigid_after.name, "persist_rb_jp")
        self.assertEqual(rigid_after.name_english, "Persist RB EN")
        self.assertTrue(_almost_equal(rigid_after.mass, 3.125), rigid_after.mass)
        self.assertTrue(_almost_equal(rigid_after.linear_damping, 0.17), rigid_after.linear_damping)
        self.assertTrue(_almost_equal(rigid_after.angular_damping, 0.27), rigid_after.angular_damping)
        self.assertTrue(_almost_equal(rigid_after.restitution, 0.37), rigid_after.restitution)
        self.assertTrue(_almost_equal(rigid_after.friction, 0.47), rigid_after.friction)
        self.assertEqual(rigid_after.shape_type, rigid.shape_type)
        self.assertEqual(rigid_after.physics_mode, rigid.physics_mode)
        self.assertEqual(rigid_after.related_bone_index, rigid.related_bone_index)
        self.assertEqual(rigid_after.collision_group, rigid.collision_group)
        self.assertEqual(rigid_after.collision_mask, rigid.collision_mask)

        self.assertEqual(joint_after.name, "persist_joint_jp")
        self.assertEqual(joint_after.name_english, "Persist Joint EN")
        self.assertEqual(joint_after.linear_constraint_states, (0, 1, 2))
        self.assertEqual(joint_after.angular_constraint_states, (2, 1, 0))
        for actual, expected in zip(joint_after.translation_limit_min, (-1.25, -2.25, -3.25)):
            self.assertTrue(_almost_equal(actual, expected), (actual, expected))
        for actual, expected in zip(joint_after.translation_limit_max, (1.25, 2.25, 3.25)):
            self.assertTrue(_almost_equal(actual, expected), (actual, expected))
        for actual, expected in zip(joint_after.rotation_limit_min_degrees, (-11.0, -22.0, -33.0)):
            self.assertTrue(_almost_equal(actual, expected), (actual, expected))
        for actual, expected in zip(joint_after.rotation_limit_max_degrees, (11.0, 22.0, 33.0)):
            self.assertTrue(_almost_equal(actual, expected), (actual, expected))
        for actual, expected in zip(joint_after.spring_translation, (0.11, 0.22, 0.33)):
            self.assertTrue(_almost_equal(actual, expected), (actual, expected))
        for actual, expected in zip(joint_after.spring_rotation, (0.44, 0.55, 0.66)):
            self.assertTrue(_almost_equal(actual, expected), (actual, expected))
        self.assertEqual(joint_after.spring_translation_enabled, (True, False, True))
        self.assertEqual(joint_after.spring_rotation_enabled, (False, True, False))
        self.assertEqual(joint_after.joint_type, joint.joint_type)
        self.assertEqual(joint_after.rigid_body_a_index, joint.rigid_body_a_index)
        self.assertEqual(joint_after.rigid_body_b_index, joint.rigid_body_b_index)

        self.assertTrue(
            cmds.attributeQuery(
                ATTR_MMD_SHOW_PHYSICS_COLLIDERS, node=reopened_root, exists=True
            ),
            "reopened root must still expose {0}".format(ATTR_MMD_SHOW_PHYSICS_COLLIDERS),
        )
        self.assertTrue(
            cmds.getAttr("{0}.{1}".format(reopened_root, ATTR_MMD_SHOW_PHYSICS_COLLIDERS)),
            "root collider visibility did not survive save/reopen",
        )
