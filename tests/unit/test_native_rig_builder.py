"""
native_rig_builder モジュールのユニットテスト。

PMX fixture から manifest 取得、ミニチェーン構築、append solver 構築を検証。
"""

import unittest
from pathlib import Path
from unittest.mock import patch

from mmd_tools.core.native import MmdIkChain, is_rig_primitive_available
from mmd_tools.converters.native_rig_builder import (
    NativeRigPrimitives,
    RigManifest,
    build_ik_mini_chain,
)

_TEST_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_PMX_PATH = _TEST_DATA_DIR / "mmt_test_model.pmx"
def _read_pmx(path: Path) -> bytes:
    if not path.exists():
        return b""
    return path.read_bytes()


class TestRigManifest(unittest.TestCase):
    @unittest.skipUnless(is_rig_primitive_available(), "DLL not available")
    def test_from_pmx_bytes(self):
        pmx_bytes = _read_pmx(_PMX_PATH)
        if not pmx_bytes:
            self.skipTest("fixture not found")
        manifest = RigManifest.from_pmx_bytes(pmx_bytes)
        self.assertIsNotNone(manifest)
        self.assertGreater(manifest.bone_count, 0)
        self.assertEqual(len(manifest.bones), manifest.bone_count)

    def test_from_invalid_bytes(self):
        manifest = RigManifest.from_pmx_bytes(b"garbage")
        self.assertIsNone(manifest)


@unittest.skipUnless(is_rig_primitive_available(), "DLL not available")
class TestNativeRigPrimitives(unittest.TestCase):
    def test_from_mmt_model(self):
        mmt_bytes = _read_pmx(_PMX_PATH)
        if not mmt_bytes:
            self.skipTest("mmt test model not found")
        prims = NativeRigPrimitives.from_pmx_bytes(mmt_bytes)
        self.assertIsNotNone(prims)
        prims.free()


class TestIkMiniChainOrdering(unittest.TestCase):
    """DLL 非依存で mini-chain の slot 契約と拒否診断を検証する。"""

    @staticmethod
    def _manifest(bones):
        return RigManifest({"boneCount": len(bones), "bones": bones})

    @staticmethod
    def _bone(parent, position):
        return {"parentIndex": parent, "restPosition": position}

    def test_parent_after_child_uses_parent_first_slots_and_preserves_mapping(self):
        """親 index が後ろでも native chain には親先行 slot を渡す。"""
        manifest = self._manifest([
            self._bone(-1, [0.0, 0.0, 0.0]),
            self._bone(3, [2.0, 0.0, 0.0]),
            self._bone(3, [1.0, 1.0, 0.0]),
            self._bone(0, [1.0, 0.0, 0.0]),
        ])
        ik_def = {
            "controllerBoneIndex": 2,
            "targetBoneIndex": 1,
            "links": [{"boneIndex": 1}],
        }
        fake_chain = object()
        with patch(
            "mmd_tools.converters.native_rig_builder.MmdIkChain.create",
            return_value=fake_chain,
        ) as create:
            result = build_ik_mini_chain(manifest, ik_def)

        self.assertIsNotNone(result)
        chain, mapping = result
        self.assertIs(chain, fake_chain)
        self.assertEqual(mapping["slot_to_pmx"], {0: 0, 1: 3, 2: 1, 3: 2})
        self.assertEqual(mapping["pmx_to_slot"], {0: 0, 3: 1, 1: 2, 2: 3})
        self.assertEqual(mapping["link_slots"], [2])
        self.assertEqual(create.call_args.kwargs["target_bone_slot"], 2)
        self.assertEqual(
            [bone["parent_slot"] for bone in create.call_args.kwargs["bones"]],
            [-1, 0, 1, 1],
        )
        self.assertEqual(
            create.call_args.kwargs["bones"][2]["rest_position"], [1.0, 0.0, 0.0]
        )

    def test_local_axis_stays_attached_to_its_remapped_bone_slot(self):
        local_axis = {"x": [0.0, 0.0, 1.0], "z": [0.0, 1.0, 0.0]}
        bones = [
            self._bone(-1, [0.0, 0.0, 0.0]),
            self._bone(2, [2.0, 0.0, 0.0]),
            self._bone(0, [1.0, 0.0, 0.0]),
        ]
        bones[1]["localAxis"] = local_axis
        with patch(
            "mmd_tools.converters.native_rig_builder.MmdIkChain.create",
            return_value=object(),
        ) as create:
            result = build_ik_mini_chain(
                self._manifest(bones),
                {"controllerBoneIndex": 2, "targetBoneIndex": 1, "links": [{"boneIndex": 1}]},
            )

        self.assertIsNotNone(result)
        self.assertEqual(result[1]["pmx_to_slot"][1], 2)
        self.assertIsNone(create.call_args.kwargs["bones"][0]["local_axis"])
        self.assertIsNone(create.call_args.kwargs["bones"][1]["local_axis"])
        self.assertEqual(create.call_args.kwargs["bones"][2]["local_axis"], local_axis)

    def test_multiple_roots_and_siblings_have_deterministic_parent_first_slots(self):
        manifest = self._manifest([
            self._bone(-1, [0.0, 0.0, 0.0]),
            self._bone(-1, [10.0, 0.0, 0.0]),
            self._bone(0, [1.0, 0.0, 0.0]),
            self._bone(0, [2.0, 0.0, 0.0]),
            self._bone(1, [11.0, 0.0, 0.0]),
        ])
        ik_def = {
            "controllerBoneIndex": 4,
            "targetBoneIndex": 2,
            "links": [{"boneIndex": 3}],
        }
        with patch(
            "mmd_tools.converters.native_rig_builder.MmdIkChain.create",
            return_value=object(),
        ):
            result = build_ik_mini_chain(manifest, ik_def)

        self.assertIsNotNone(result)
        _, mapping = result
        self.assertEqual(mapping["slot_to_pmx"], {0: 0, 1: 2, 2: 3, 3: 1, 4: 4})

    def test_deep_valid_hierarchy_does_not_depend_on_python_recursion_limit(self):
        bones = [self._bone(-1, [0.0, 0.0, 0.0])]
        for index in range(1, 1100):
            bones.append(self._bone(index - 1, [float(index), 0.0, 0.0]))
        with patch(
            "mmd_tools.converters.native_rig_builder.MmdIkChain.create",
            return_value=object(),
        ):
            result = build_ik_mini_chain(
                self._manifest(bones),
                {
                    "controllerBoneIndex": 1099,
                    "targetBoneIndex": 1098,
                    "links": [{"boneIndex": 1098}],
                },
            )

        self.assertIsNotNone(result)
        _, mapping = result
        self.assertEqual(mapping["slot_to_pmx"][0], 0)
        self.assertEqual(mapping["slot_to_pmx"][1099], 1099)

    def test_tentacle_style_multi_ik_fixture_rejects_old_slot_order_and_builds_all_chains(self):
        """画像の触手構成に寄せた複数 out-of-order IK chain を恒久回帰化する。"""
        if not is_rig_primitive_available():
            self.skipTest("rig primitive DLL not available")
        bones = [self._bone(-1, [0.0, 0.0, 0.0])]
        chains = []
        grants = []
        for chain_index in range(4):
            base = len(bones)
            append_bone = base
            target = base + 1
            link = base + 2
            controller = base + 3
            controller_parent = base + 4
            offset = float(chain_index + 1)
            bones.extend([
                self._bone(0, [offset, 1.0, 0.0]),
                self._bone(append_bone, [offset, 4.0, 0.0]),
                self._bone(append_bone, [offset, 3.0, 0.0]),
                self._bone(controller_parent, [offset, 5.0, 0.0]),
                self._bone(append_bone, [offset, 2.0, 0.0]),
            ])
            chains.append({
                "controllerBoneIndex": controller,
                "targetBoneIndex": target,
                "links": [{"boneIndex": link}],
            })
            grants.append({
                "targetBoneIndex": append_bone,
                "sourceBoneIndex": link,
                "affectRotation": True,
                "affectTranslation": False,
                "ratio": 0.5,
            })

        manifest = RigManifest({
            "boneCount": len(bones),
            "bones": bones,
            "ikChains": chains,
            "grants": grants,
        })
        results = [build_ik_mini_chain(manifest, chain) for chain in chains]
        self.assertTrue(all(result is not None for result in results))
        try:
            for chain, result in zip(chains, results):
                _, mapping = result
                old_indices = sorted(mapping["pmx_to_slot"])
                old_slots = {pmx_index: slot for slot, pmx_index in enumerate(old_indices)}
                old_bones = [
                    {
                        "parent_slot": old_slots.get(bones[pmx_index]["parentIndex"], -1),
                        "rest_position": [0.0, 0.0, 0.0],
                    }
                    for pmx_index in old_indices
                ]
                old_links = [
                    {"bone_slot": old_slots[link["boneIndex"]], "has_angle_limit": False}
                    for link in chain["links"]
                ]
                old_chain = MmdIkChain.create(
                    bones=old_bones,
                    target_bone_slot=old_slots[chain["targetBoneIndex"]],
                    links=old_links,
                    iteration_count=40,
                    limit_angle=2.0,
                )
                self.assertIsNone(old_chain)
                controller_slot = mapping["pmx_to_slot"][chain["controllerBoneIndex"]]
                parent_slot = mapping["pmx_to_slot"][chain["controllerBoneIndex"] + 1]
                self.assertLess(parent_slot, controller_slot)
        finally:
            for result in results:
                result[0].free()

    def test_invalid_controller_target_and_link_skip_before_factory(self):
        manifest = self._manifest([self._bone(-1, [0.0, 0.0, 0.0])])
        cases = [
            (
                {"controllerBoneIndex": 1, "targetBoneIndex": 0, "links": []},
                "invalid_controller_index",
            ),
            (
                {"controllerBoneIndex": 0, "targetBoneIndex": 1, "links": []},
                "invalid_target_index",
            ),
            (
                {
                    "controllerBoneIndex": 0,
                    "targetBoneIndex": 0,
                    "links": [{"boneIndex": 1}],
                },
                "invalid_link_index",
            ),
        ]
        for ik_def, expected_reason in cases:
            with self.subTest(reason=expected_reason), patch(
                "mmd_tools.converters.native_rig_builder.MmdIkChain.create"
            ) as create, patch(
                "mmd_tools.converters.native_rig_builder.logger.warning"
            ) as warning:
                result = build_ik_mini_chain(manifest, ik_def)

            self.assertIsNone(result)
            create.assert_not_called()
            self.assertEqual(warning.call_args.args[1], expected_reason)
            self.assertIn("event=ik_mini_chain_skipped", warning.call_args.args[0])

    def test_invalid_ancestor_parent_and_cycle_skip_before_factory(self):
        cases = [
            (
                [self._bone(5, [0.0, 0.0, 0.0])],
                "invalid_parent_index",
            ),
            (
                [self._bone(1, [0.0, 0.0, 0.0]), self._bone(0, [1.0, 0.0, 0.0])],
                "ancestor_cycle",
            ),
        ]
        for bones, expected_reason in cases:
            with self.subTest(reason=expected_reason), patch(
                "mmd_tools.converters.native_rig_builder.MmdIkChain.create"
            ) as create, patch(
                "mmd_tools.converters.native_rig_builder.logger.warning"
            ) as warning:
                result = build_ik_mini_chain(
                    self._manifest(bones),
                    {"controllerBoneIndex": 0, "targetBoneIndex": 0, "links": []},
                )

            self.assertIsNone(result)
            create.assert_not_called()
            self.assertEqual(warning.call_args.args[1], expected_reason)


if __name__ == "__main__":
    unittest.main()
