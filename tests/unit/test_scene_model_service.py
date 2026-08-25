import unittest

from mmd_tools.adapters import MayaCmdsAdapter
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
        self.long_paths = {}

    def objExists(self, node):
        return node in self.existing

    def ls(self, *args, **kwargs):
        if kwargs.get("selection"):
            selected = list(self.selection)
            if kwargs.get("long"):
                resolved = []
                for node in selected:
                    matches = self.long_paths.get(node)
                    if matches is None:
                        resolved.append(node)
                    elif isinstance(matches, (list, tuple)):
                        resolved.extend(matches)
                    else:
                        resolved.append(matches)
                return resolved
            return selected
        if kwargs.get("type") == "transform":
            if kwargs.get("long") and args:
                query = args[0]
                if query in self.long_paths:
                    matches = self.long_paths[query]
                    return list(matches) if isinstance(matches, (list, tuple)) else [matches]
            return list(self.transforms)
        if kwargs.get("materials"):
            materials = []
            for item in args[0] or []:
                materials.extend(self.materials_for_connections.get(item, []))
            return materials
        if kwargs.get("type") == "blendShape":
            return list(args[0] or [])
        if kwargs.get("long") and args:
            query = args[0]
            if query in self.long_paths:
                matches = self.long_paths[query]
                return list(matches) if isinstance(matches, (list, tuple)) else [matches]
            if isinstance(query, str):
                return [query]
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
    def test_cmds_module_legacy_injection_still_works(self):
        cmds = _FakeCmds()
        cmds.existing = {"model_root"}
        cmds.attrs = {"model_root": {ATTR_MMD_MODEL_NAME: "Model"}}
        service = SceneModelService(cmds_module=cmds)

        self.assertTrue(service.object_exists("model_root"))
        self.assertFalse(service.object_exists("missing_root"))
        self.assertTrue(service.attribute_exists("model_root", ATTR_MMD_MODEL_NAME))
        self.assertFalse(service.attribute_exists("model_root", ATTR_MMD_MODEL_NAME_EN))
        self.assertFalse(service.attribute_exists("", ATTR_MMD_MODEL_NAME))

    def test_cmds_adapter_injection_supports_service_methods(self):
        cmds = _FakeCmds()
        cmds.existing = {"ns:model_root"}
        cmds.transforms = ["ns:model_root"]
        cmds.attrs = {
            "ns:model_root": {ATTR_MMD_MODEL_NAME: "表示名", ATTR_MMD_MODEL_NAME_EN: "Name"}
        }
        cmds.meshes = {"ns:model_root": ["meshShape"]}
        cmds.vertices = {"meshShape": 3}
        cmds.connections = {"meshShape": ["sg"], "sg": ["mat"]}
        cmds.materials_for_connections = {"mat": ["mat"]}
        cmds.joints = {"ns:model_root": ["joint"]}
        cmds.history = {"meshShape": ["blendShape"]}
        cmds.blend_targets = {"blendShape": ["smile"]}
        cmds.selection = ["ns:model_root"]
        adapter = MayaCmdsAdapter(cmds_module=cmds)
        service = SceneModelService(cmds_adapter=adapter)

        self.assertTrue(service.object_exists("ns:model_root"))
        self.assertEqual(service.list_mmd_models(), ["ns:model_root"])
        info = service.get_model_info("ns:model_root")
        self.assertEqual(info["display_name"], "表示名")
        self.assertEqual(info["vertex_count"], 3)
        self.assertEqual(info["material_count"], 1)
        self.assertEqual(info["bone_count"], 1)
        self.assertEqual(info["morph_count"], 1)

        service.select_nodes(["ns:model_root"], replace=False)
        self.assertEqual(cmds.selected, (["ns:model_root"], False))

    def test_cmds_adapter_takes_priority_over_cmds_module(self):
        cmds_module = _FakeCmds()
        cmds_adapter_source = _FakeCmds()
        cmds_module.existing = {"module_root"}
        cmds_adapter_source.existing = {"adapter_root"}
        service = SceneModelService(
            cmds_module=cmds_module,
            cmds_adapter=MayaCmdsAdapter(cmds_module=cmds_adapter_source),
        )

        self.assertFalse(service.object_exists("module_root"))
        self.assertTrue(service.object_exists("adapter_root"))

    def test_list_mmd_models_returns_sorted_mmd_roots_only(self):
        cmds = _FakeCmds()
        cmds.transforms = ["b_root", "not_mmd_root", "ns:a_root", "b_root"]
        cmds.attrs = {
            "b_root": {ATTR_MMD_MODEL_NAME: "B"},
            "ns:a_root": {ATTR_MMD_MODEL_NAME_EN: "A"},
        }
        service = SceneModelService(cmds_module=cmds)

        self.assertEqual(service.list_mmd_models(), ["b_root", "ns:a_root"])

    def test_list_mmd_models_tolerates_none_ls_results(self):
        class _NoneLsCmds(_FakeCmds):
            def ls(self, *args, **kwargs):
                if kwargs.get("type") == "transform":
                    return None
                return super().ls(*args, **kwargs)

        service = SceneModelService(cmds_module=_NoneLsCmds())

        self.assertEqual(service.list_mmd_models(), [])

    def test_list_mmd_models_uses_long_paths_for_current_model_identity(self):
        class _LongPathCmds(_FakeCmds):
            def ls(self, *args, **kwargs):
                if kwargs.get("type") == "transform":
                    return ["|group|model_root"] if kwargs.get("long") else ["model_root"]
                return super().ls(*args, **kwargs)

        cmds = _LongPathCmds()
        cmds.attrs = {"|group|model_root": {ATTR_MMD_MODEL_NAME: "Model"}}
        service = SceneModelService(cmds_module=cmds)

        self.assertEqual(service.list_mmd_models(), ["|group|model_root"])

    def test_list_mmd_models_keeps_same_leaf_roots_by_canonical_path(self):
        cmds = _FakeCmds()
        cmds.transforms = ["|groupA|model_root", "|groupB|model_root"]
        cmds.attrs = {
            "|groupA|model_root": {ATTR_MMD_MODEL_NAME: "A"},
            "|groupB|model_root": {ATTR_MMD_MODEL_NAME: "B"},
        }
        service = SceneModelService(cmds_module=cmds)

        self.assertEqual(
            service.list_mmd_models(),
            ["|groupA|model_root", "|groupB|model_root"],
        )

    def test_resolve_model_from_selection_uses_canonical_identity_only(self):
        cmds = _FakeCmds()
        cmds.selection = ["|grp|child"]
        cmds.parents = {"|grp|child": "|grp|model_root"}
        cmds.attrs = {"|grp|model_root": {ATTR_MMD_MODEL_NAME: "Model"}}
        service = SceneModelService(cmds_module=cmds)

        self.assertEqual(service.resolve_model_from_selection(["|grp|model_root"]), "|grp|model_root")
        # A short available root is not a second identity or a leaf-name fallback.
        self.assertIsNone(service.resolve_model_from_selection(["model_root"]))

    def test_resolve_model_from_selection_rejects_same_leaf_ambiguous_available_root(self):
        cmds = _FakeCmds()
        cmds.selection = ["joint"]
        cmds.long_paths["joint"] = ["|modelA|joint", "|modelB|joint"]
        cmds.parents = {
            "|modelA|joint": "|modelA|model_root",
            "|modelB|joint": "|modelB|model_root",
        }
        cmds.attrs = {
            "|modelA|model_root": {ATTR_MMD_MODEL_NAME: "A"},
            "|modelB|model_root": {ATTR_MMD_MODEL_NAME: "B"},
        }
        service = SceneModelService(cmds_module=cmds)

        self.assertIsNone(
            service.resolve_model_from_selection(
                ["|modelA|model_root", "|modelB|model_root"]
            )
        )

    def test_resolve_model_from_selection_routes_same_leaf_models_by_long_path(self):
        cmds = _FakeCmds()
        cmds.selection = ["|modelA|model_root|joint"]
        cmds.parents = {
            "|modelA|model_root|joint": "|modelA|model_root",
        }
        cmds.attrs = {
            "|modelA|model_root": {ATTR_MMD_MODEL_NAME: "A"},
            "|modelB|model_root": {ATTR_MMD_MODEL_NAME: "B"},
        }
        service = SceneModelService(cmds_module=cmds)

        self.assertEqual(
            service.resolve_model_from_selection(
                ["|modelA|model_root", "|modelB|model_root"]
            ),
            "|modelA|model_root",
        )

    def test_resolve_model_from_selection_rejects_multi_root_selection(self):
        cmds = _FakeCmds()
        cmds.selection = ["|modelA|joint", "|modelB|joint"]
        cmds.parents = {
            "|modelA|joint": "|modelA|model_root",
            "|modelB|joint": "|modelB|model_root",
        }
        cmds.attrs = {
            "|modelA|model_root": {ATTR_MMD_MODEL_NAME: "A"},
            "|modelB|model_root": {ATTR_MMD_MODEL_NAME: "B"},
        }
        service = SceneModelService(cmds_module=cmds)

        self.assertIsNone(
            service.resolve_model_from_selection(
                ["|modelA|model_root", "|modelB|model_root"]
            )
        )

    def test_get_parent_mmd_root_returns_none_when_parent_lookup_raises(self):
        class _FailingRelativesCmds(_FakeCmds):
            def listRelatives(self, node, **kwargs):
                raise RuntimeError("listRelatives failed")

        service = SceneModelService(cmds_module=_FailingRelativesCmds())

        self.assertIsNone(service.get_parent_mmd_root("|grp|child"))

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

    def test_get_attr_safe_returns_default_when_get_attr_raises(self):
        class _FailingGetAttrCmds(_FakeCmds):
            def getAttr(self, attr_path):
                raise RuntimeError("getAttr failed")

        cmds = _FailingGetAttrCmds()
        cmds.attrs = {"node": {"present": "value"}}
        service = SceneModelService(cmds_module=cmds)

        self.assertEqual(service.get_attr_safe("node", "present", "fallback"), "fallback")


if __name__ == "__main__":
    unittest.main()
