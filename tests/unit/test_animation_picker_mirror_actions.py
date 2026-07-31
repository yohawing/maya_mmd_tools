"""Focused tests for UUID-scoped Mirror Pose and Mirror Select helpers."""

from __future__ import annotations

import unittest

from mmd_tools.ui.mirror_actions import (
    MirrorActionError,
    MirrorEntry,
    MirrorMapping,
    MirrorPoseTransaction,
    build_mirror_pairs,
    mirrored_transform_values,
)


class _FakeCmds:
    def __init__(self):
        self.uuid = "model-uuid"
        self.paths = {
            "|model": "|model",
            "|model|L_arm": "|model|L_arm",
            "|model|R_arm": "|model|R_arm",
        }
        self.values = {}
        self.incoming = {}
        self.locks = {}
        self.keyframes = []
        self.time = 12.0
        for node, offset in (("|model|L_arm", 1.0), ("|model|R_arm", 10.0)):
            for channel, value in zip(
                ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"),
                (offset, offset + 1.0, offset + 2.0, offset + 3.0, offset + 4.0, offset + 5.0),
            ):
                self.values[f"{node}.{channel}"] = value
                self.incoming[f"{node}.{channel}"] = []
                self.locks[f"{node}.{channel}"] = False
        self.fail_plug = None

    def ls(self, node=None, long=False, uuid=False, **_kwargs):
        if uuid:
            return [self.uuid] if node == "|model" else [f"uuid:{node}"] if node in self.paths else []
        if node is None:
            return []
        nodes = node if isinstance(node, (list, tuple)) else [node]
        if long:
            return [self.paths[value] for value in nodes if value in self.paths]
        return list(nodes)

    def getAttr(self, plug, lock=False):
        return self.locks[plug] if lock else self.values[plug]

    def setAttr(self, plug, value=None, lock=None, **_kwargs):
        if lock is not None:
            self.locks[plug] = bool(lock)
            return
        if plug == self.fail_plug:
            self.fail_plug = None
            raise RuntimeError("setAttr failure")
        self.values[plug] = value

    def listConnections(self, plug, source=False, destination=False, plugs=False):
        if source and not destination and plugs:
            return list(self.incoming.get(plug, ()))
        return []

    @staticmethod
    def nodeType(node):
        return "animCurveTA" if node.startswith("animCurve") else "transform"

    def currentTime(self, query=False):
        return self.time if query else None

    def setKeyframe(self, node, time, value):
        self.keyframes.append((node, tuple(time), float(value)))
        for plug, incoming in self.incoming.items():
            if incoming == [f"{node}.output"]:
                self.values[plug] = float(value)

    def undo(self):
        pass


class _FakeAdapter:
    def __init__(self, cmds):
        self._cmds = cmds
        self.undo_chunks = []

    def undo_info(self, **kwargs):
        if kwargs.get("openChunk"):
            self.undo_chunks.append(kwargs.get("chunkName"))


class TestMirrorPairing(unittest.TestCase):
    def test_japanese_english_and_namespace_names_pair_by_metadata(self):
        entries = [
            MirrorEntry("left-uuid", "|charA|左腕", "|charA|左腕", ("左腕",)),
            MirrorEntry("right-uuid", "|charA|右腕", "|charA|右腕", ("右腕",)),
            MirrorEntry("left-finger", "|charA|hand_L", "|charA|hand_L", ("Hand_L",)),
            MirrorEntry("right-finger", "|charA|hand_R", "|charA|hand_R", ("Hand_R",)),
        ]
        pairs = build_mirror_pairs(entries)
        self.assertEqual(pairs["left-uuid"].identity, "right-uuid")
        self.assertEqual(pairs["left-finger"].identity, "right-finger")
        self.assertEqual(build_mirror_pairs(entries)["left-uuid"].node, "|charA|右腕")

    def test_center_unpaired_and_ambiguous_selection_fail_closed(self):
        entries = [
            MirrorEntry("center", "|char|センター", "|char|センター", ("センター",)),
            MirrorEntry("left", "|char|左腕", "|char|左腕", ("左腕",)),
        ]
        self.assertEqual(build_mirror_pairs(entries), {})
        ambiguous = entries + [
            MirrorEntry("right-a", "|char|右腕A", "|char|右腕A", ("右腕",)),
            MirrorEntry("right-b", "|char|右腕B", "|char|右腕B", ("右腕",)),
        ]
        self.assertEqual(build_mirror_pairs(ambiguous), {})

    def test_non_identity_control_bases_mirror_in_bone_space(self):
        quarter_turn_z = (0.0, 0.0, 2**-0.5, 2**-0.5)
        translation, rotation = mirrored_transform_values(
            (1.0, 2.0, 3.0),
            (30.0, 0.0, 0.0),
            source_basis=quarter_turn_z,
            target_basis=quarter_turn_z,
        )

        self.assertEqual(translation, (-1.0, 2.0, 3.0))
        self.assertAlmostEqual(rotation[0], -30.0, places=6)
        self.assertAlmostEqual(rotation[1], 0.0, places=6)
        self.assertAlmostEqual(rotation[2], 0.0, places=6)


class TestMirrorPoseTransaction(unittest.TestCase):
    def _make(self):
        cmds = _FakeCmds()
        adapter = _FakeAdapter(cmds)
        left = MirrorEntry("left", "|model|L_arm", "|model|L_arm", ("左腕",))
        right = MirrorEntry("right", "|model|R_arm", "|model|R_arm", ("右腕",))
        mapping = MirrorMapping(left, right)
        return cmds, adapter, mapping

    def _transaction(self, adapter, mapping):
        return MirrorPoseTransaction(
            adapter,
            model_root="|model",
            model_uuid="model-uuid",
            mappings=[mapping],
        )

    def test_mirror_sign_contract_preserves_source_and_connections(self):
        cmds, adapter, mapping = self._make()
        source_plug = "|model|L_arm.rotateY"
        cmds.incoming[source_plug] = ["animCurve.output"]
        before_source = dict(cmds.values)

        self.assertEqual(self._transaction(adapter, mapping).apply(), 1)
        self.assertEqual(cmds.values["|model|R_arm.translateX"], -1.0)
        self.assertEqual(cmds.values["|model|R_arm.translateY"], 2.0)
        self.assertEqual(cmds.values["|model|R_arm.rotateX"], 4.0)
        self.assertEqual(cmds.values["|model|R_arm.rotateY"], -5.0)
        self.assertEqual(cmds.values["|model|R_arm.rotateZ"], -6.0)
        self.assertEqual(cmds.values[source_plug], before_source[source_plug])
        self.assertEqual(cmds.incoming[source_plug], ["animCurve.output"])

    def test_failure_rolls_back_exact_target_values(self):
        cmds, adapter, mapping = self._make()
        original = dict(cmds.values)
        cmds.fail_plug = "|model|R_arm.rotateX"

        with self.assertRaises(MirrorActionError):
            self._transaction(adapter, mapping).apply()
        self.assertEqual(cmds.values, original)
        self.assertEqual(cmds.incoming["|model|R_arm.rotateX"], [])

    def test_uuid_mismatch_and_driven_target_fail_before_write(self):
        cmds, adapter, mapping = self._make()
        cmds.uuid = "other-model"
        with self.assertRaises(MirrorActionError):
            self._transaction(adapter, mapping).apply()

        cmds.uuid = "model-uuid"
        cmds.incoming["|model|R_arm.rotateX"] = ["other.output"]
        before = dict(cmds.values)
        with self.assertRaises(MirrorActionError):
            self._transaction(adapter, mapping).apply()
        self.assertEqual(cmds.values, before)

    def test_direct_anim_curve_target_gets_current_frame_keys(self):
        cmds, adapter, mapping = self._make()
        for channel in ("rotateX", "rotateY", "rotateZ"):
            cmds.incoming[f"|model|R_arm.{channel}"] = [
                f"animCurve_{channel}.output"
            ]

        self.assertEqual(self._transaction(adapter, mapping).apply(), 1)

        self.assertEqual(
            cmds.keyframes,
            [
                ("animCurve_rotateX", (12.0, 12.0), 4.0),
                ("animCurve_rotateY", (12.0, 12.0), -5.0),
                ("animCurve_rotateZ", (12.0, 12.0), -6.0),
            ],
        )


if __name__ == "__main__":
    unittest.main()
