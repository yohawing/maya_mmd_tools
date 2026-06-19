import unittest

from mmd_tools.core.constants import ATTR_MMD_MODEL_NAME, ATTR_MMD_MODEL_NAME_EN
from mmd_tools.services.scene_model_service import SceneModelService


class _FakeCmds:
    def __init__(self):
        self.existing = set()
        self.transforms = []
        self.attrs = {}
        self.parents = {}
        self.selection = []
        self.meshes = {}
        self.joints = {}
        self.vertices = {}
        self.connections = {}
        self.materials_for_connections = {}
        self.history = {}
        self.blend_targets = {}
        self.selected = None

    def objExists(self, node):
        return node in self.existing

    def ls(self, *args, **kwargs):
        if kwargs.get("selection"):
            return list(self.selection)
        if kwargs.get("type") == "transform":
            return list(self.transforms)
        if kwargs.get("materials"):
            materials = []
            for item in args[0] or []:
                materials.extend(self.materials_for_connections.get(item, []))
            return materials
        if kwargs.get("type") == "blendShape":
            return list(args[0] or [])
        return list(args[0] or [])

    def attributeQuery(self, attr, node, exists):
        return exists and attr in self.attrs.get(node, {})

    def getAttr(self, attr_path):
        node, attr = attr_path.rsplit(".", 1)
        return self.attrs.get(node, {}).get(attr)

    def listRelatives(self, node, **kwargs):
        if kwargs.get("parent"):
            parent = self.parents.get(node)
            return [parent] if parent else []
        if kwargs.get("type") == "mesh":
            return list(self.meshes.get(node, []))
        if kwargs.get("type") == "joint":
            return list(self.joints.get(node, []))
        return []

    def polyEvaluate(self, shape, vertex):
        return self.vertices.get(shape, 0) if vertex else 0

    def listConnections(self, node, **kwargs):
        if isinstance(node, list):
            result = []
            for item in node:
                result.extend(self.connections.get(item, []))
            return result
        return list(self.connections.get(node, []))

    def listHistory(self, shapes):
        result = []
        for shape in shapes:
            result.extend(self.history.get(shape, []))
        return result

    def blendShape(self, node, **kwargs):
        if kwargs.get("query") and kwargs.get("target"):
            return list(self.blend_targets.get(node, []))
        return []

    def select(self, nodes, replace=True):
        self.selected = (nodes, replace)


class TestSceneModelService(unittest.TestCase):
    def test_list_mmd_models_returns_sorted_mmd_roots_only(self):
        cmds = _FakeCmds()
        cmds.transforms = ["b_root", "not_mmd_root", "ns:a_root", "b_root"]
        cmds.attrs = {
            "b_root": {ATTR_MMD_MODEL_NAME: "B"},
            "ns:a_root": {ATTR_MMD_MODEL_NAME_EN: "A"},
        }
        service = SceneModelService(cmds_module=cmds)

        self.assertEqual(service.list_mmd_models(), ["b_root", "ns:a_root"])

    def test_resolve_model_from_selection_prefers_available_full_path_then_short_name(self):
        cmds = _FakeCmds()
        cmds.selection = ["|grp|child"]
        cmds.parents = {"|grp|child": "|grp|model_root"}
        cmds.attrs = {"|grp|model_root": {ATTR_MMD_MODEL_NAME: "Model"}}
        service = SceneModelService(cmds_module=cmds)

        self.assertEqual(service.resolve_model_from_selection(["|grp|model_root"]), "|grp|model_root")
        self.assertEqual(service.resolve_model_from_selection(["model_root"]), "model_root")

    def test_get_model_display_name_uses_japanese_then_english_then_node_name(self):
        cmds = _FakeCmds()
        cmds.attrs = {
            "jp_root": {ATTR_MMD_MODEL_NAME: "日本語名", ATTR_MMD_MODEL_NAME_EN: "English"},
            "en_root": {ATTR_MMD_MODEL_NAME_EN: "English"},
        }
        service = SceneModelService(cmds_module=cmds)

        self.assertEqual(service.get_model_display_name("jp_root"), "日本語名")
        self.assertEqual(service.get_model_display_name("en_root"), "English")
        self.assertEqual(service.get_model_display_name("plain_root"), "plain")

    def test_get_model_info_collects_summary_counts(self):
        cmds = _FakeCmds()
        cmds.existing = {"ns:model_root"}
        cmds.attrs = {"ns:model_root": {ATTR_MMD_MODEL_NAME: "表示名", ATTR_MMD_MODEL_NAME_EN: "Name"}}
        cmds.meshes = {"ns:model_root": ["meshShape1", "meshShape2"]}
        cmds.vertices = {"meshShape1": 8, "meshShape2": 4}
        cmds.connections = {
            "meshShape1": ["sg1"],
            "meshShape2": ["sg1", "sg2"],
            "sg1": ["mat1"],
            "sg2": ["mat2"],
        }
        cmds.materials_for_connections = {"mat1": ["mat1"], "mat2": ["mat2"]}
        cmds.joints = {"ns:model_root": ["j1", "j2"]}
        cmds.history = {"meshShape1": ["bs1"], "meshShape2": ["bs2"]}
        cmds.blend_targets = {"bs1": ["smile"], "bs2": ["blink", "angry"]}
        service = SceneModelService(cmds_module=cmds)

        info = service.get_model_info("ns:model_root")

        self.assertEqual(info["namespace"], "ns")
        self.assertEqual(info["display_name"], "表示名")
        self.assertEqual(info["vertex_count"], 12)
        self.assertEqual(info["material_count"], 2)
        self.assertEqual(info["bone_count"], 2)
        self.assertEqual(info["morph_count"], 3)

    def test_get_attr_safe_returns_default_for_missing_or_none(self):
        cmds = _FakeCmds()
        cmds.attrs = {"node": {"present": None}}
        service = SceneModelService(cmds_module=cmds)

        self.assertEqual(service.get_attr_safe("node", "present", "fallback"), "fallback")
        self.assertEqual(service.get_attr_safe("node", "missing", "fallback"), "fallback")


if __name__ == "__main__":
    unittest.main()
