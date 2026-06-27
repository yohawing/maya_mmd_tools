"""maya_bake_oracle_dumperのMaya非依存ロジックを検証するテスト。"""

import unittest
from pathlib import Path

from mmd_tools.tools.maya_bake_oracle_dumper import (
    _build_maya_morph_weight_map,
    _collect_bones,
    _collect_morphs,
    _collect_morphs_in_pmx_order,
    _convert_maya_world_matrix_to_mmd,
    _default_output_path,
    _make_record,
)


class FakeCmds:
    """Maya cmdsの最小スタブ。"""

    joint_attrs = {
        "root": {"mmd_bone_index": 1, "mmd_bone_name": "センター"},
        "knee": {"mmd_bone_index": 0, "mmd_bone_name": "左ひざ"},
    }
    matrices = {
        "root": list(range(16)),
        "knee": [1.0] * 16,
    }
    blend_shape_weights = {
        "faceBS": [0.25, 1.0],
    }
    aliases = {
        "faceBS.weight[0]": "まばたき",
        "faceBS.weight[1]": "笑い",
    }

    @classmethod
    def ls(cls, type=None):
        if type == "joint":
            return ["root", "knee"]
        if type == "blendShape":
            return ["faceBS"]
        return []

    @classmethod
    def attributeQuery(cls, attr, node=None, exists=False):
        return attr in cls.joint_attrs.get(node, {})

    @classmethod
    def getAttr(cls, attr_path):
        node, attr = attr_path.split(".", 1)
        if attr.startswith("weight["):
            index = int(attr[len("weight[") : -1])
            return cls.blend_shape_weights[node][index]
        return cls.joint_attrs[node][attr]

    @classmethod
    def xform(cls, node, query=False, worldSpace=False, matrix=False):
        return cls.matrices[node]

    @classmethod
    def blendShape(cls, node, query=False, weightCount=False):
        return len(cls.blend_shape_weights[node])

    @classmethod
    def aliasAttr(cls, attr_path, query=False):
        return cls.aliases.get(attr_path)


class TestMayaBakeOracleDumper(unittest.TestCase):
    """Maya bake結果をoracle JSONLとして保存する補助ロジックを検証する。"""

    def test_default_output_path_includes_maya_bake_and_offset(self):
        manifest_path = Path(r"F:\GoldenOracle\manifests\motion-numeric.json")
        case = {"oracle": {"path": "../runs/motion-numeric/sample/oracle.actual.jsonl"}}

        path = _default_output_path(manifest_path, case, sample_frame_offset=1.0)

        self.assertEqual(path.name, "maya-bake.offset1.oracle.jsonl")

    def test_collect_bones_sorts_by_mmd_bone_index(self):
        bones = _collect_bones(FakeCmds)

        self.assertEqual([bone["name"] for bone in bones], ["左ひざ", "センター"])
        # worldMatrix from Fake is "Maya" form; collector converts to MMD convention before output
        expected_knee = _convert_maya_world_matrix_to_mmd([1.0] * 16)
        self.assertEqual(bones[0]["worldMatrix"], expected_knee)
        # spot-check Z sign flip behavior on translation (Maya tz -> MMD -tz)
        self.assertEqual(expected_knee[14], -1.0)

    def test_collect_morphs_uses_blend_shape_aliases(self):
        morphs = _collect_morphs(FakeCmds)

        self.assertEqual(morphs[0], {"index": 0, "name": "まばたき", "weight": 0.25})
        self.assertEqual(morphs[1], {"index": 1, "name": "笑い", "weight": 1.0})

    def test_make_record_shape_matches_golden_oracle_model_record(self):
        record = _make_record(
            frame=10,
            evaluated_frame=11.0,
            pmx_path=Path("model.pmx"),
            vmd_path=Path("motion.vmd"),
            bones=[{"index": 0, "name": "bone", "worldMatrix": [1.0] * 16}],
            morphs=[{"index": 0, "name": "morph", "weight": 0.5}],
        )

        self.assertEqual(record["schemaVersion"], 1)
        self.assertEqual(record["source"]["backend"], "maya_mmd_tools.maya-bake")
        self.assertEqual(record["source"]["evaluatedFrame"], 11.0)
        self.assertEqual(record["frame"], 10)
        self.assertEqual(record["models"][0]["bones"][0]["name"], "bone")
        self.assertEqual(record["models"][0]["morphs"][0]["weight"], 0.5)

    def test_convert_maya_world_matrix_to_mmd_column_major_z_flip(self):
        """Verify row-major→column-major transpose + Z sign flip.

        Identity is a fixed point. Translation Z is sign-flipped.
        Off-diagonal rotation elements are transposed and Z-flipped.
        """
        ident = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1.0]
        self.assertEqual(_convert_maya_world_matrix_to_mmd(ident), ident)

        maya_tz5 = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 5.0, 1.0]
        mmd = _convert_maya_world_matrix_to_mmd(maya_tz5)
        self.assertEqual(mmd[14], -5.0)

        # Row-major M[0][2]=7 (index 2) → column-major [col2,row0] = index 8, Z-flipped
        maya_m02 = [0, 0, 7.0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]
        mmd_m02 = _convert_maya_world_matrix_to_mmd(maya_m02)
        self.assertEqual(mmd_m02[8], -7.0)
        self.assertEqual(mmd_m02[2], 0.0)

    def test_collect_morphs_in_pmx_order_uses_pmx_indices_and_names(self):
        """Verify PMX-order output + name-based weight lookup from Maya aliases.

        Uses FakeCmds (BS local order: 0=まばたき,1=笑い). PMX order is reversed here
        to prove indices/names come from pmx list, not BS local order.
        """
        pmx_order_names = ["笑い", "まばたき"]  # different order from Fake BS creation
        morphs = _collect_morphs_in_pmx_order(FakeCmds, pmx_order_names)

        self.assertEqual(len(morphs), 2)
        # index 0 in output is pmx[0]="笑い" (even though BS local 1), weight from alias
        self.assertEqual(morphs[0], {"index": 0, "name": "笑い", "weight": 1.0})
        # index 1 in output is pmx[1]="まばたき", weight from alias
        self.assertEqual(morphs[1], {"index": 1, "name": "まばたき", "weight": 0.25})

    def test_build_maya_morph_weight_map_and_pmx_order_fallback_to_zero(self):
        """Non-mapped names (no BS alias) get 0.0; map building tolerates the fake."""
        weight_map = _build_maya_morph_weight_map(FakeCmds)
        self.assertIn("まばたき", weight_map)
        self.assertIn("笑い", weight_map)

        # A name with no alias in Maya -> 0.0 , and still emits with correct pmx index
        pmx_names = ["まばたき", "存在しないモーフ", "笑い"]
        morphs = _collect_morphs_in_pmx_order(FakeCmds, pmx_names)
        self.assertEqual(morphs[0]["name"], "まばたき")
        self.assertEqual(morphs[0]["weight"], 0.25)
        self.assertEqual(morphs[1]["index"], 1)
        self.assertEqual(morphs[1]["name"], "存在しないモーフ")
        self.assertEqual(morphs[1]["weight"], 0.0)
        self.assertEqual(morphs[2]["weight"], 1.0)


if __name__ == "__main__":
    unittest.main()
