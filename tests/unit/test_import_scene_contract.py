"""Counterfeit scene snapshots must exercise the semantic parity contract."""

from __future__ import annotations

import copy
import json
import math
import unittest

from tools.smoke.import_scene_contract import (
    _MISSING,
    _call,
    _normalize_vmd_authoring_attrs,
    _read_attr,
    _semantic_id,
    capture_scene,
    compare_scenes,
)


def _snapshot() -> dict:
    mesh = {
        "key": "|body#mesh[0]",
        "transformPath": "|body",
        "shapeIndex": 0,
        "shapeName": "bodyShape",
        "vertices": 3,
        "points": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        "faceCorners": [{"vertices": [0, 1, 2], "corners": [{"vertex": 0, "local": 0}, {"vertex": 1, "local": 1}, {"vertex": 2, "local": 2}]}],
        "faceVertexNormals": [[0.0, 0.0, 1.0], [0.0, 0.0, 1.0], [0.0, 0.0, 1.0]],
        "uvSets": [{"name": "map1", "values": [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]], "faceCounts": [3], "faceUVIds": [0, 1, 2]}],
        "additionalUvMetadata": {"mmd_pmx_additional_uv_count": 0},
        "faceMaterialIndices": [0],
        "materials": [{"slot": 0, "key": "material:0", "attrs": {"mmd_material_index": 0, "mmd_draw_flags": 3, "mmd_edge_size": 1.0}, "connections": []}],
        "skin": [{"key": "skinCluster1", "influences": [{"index": 0, "key": "bone:0", "bindPreMatrix": [1.0] * 16}], "weights": [{"vertex": 0, "weights": [{"bone": "bone:0", "value": 1.0}]}, {"vertex": 1, "weights": [{"bone": "bone:0", "value": 1.0}]}, {"vertex": 2, "weights": [{"bone": "bone:0", "value": 1.0}]}], "weightsFormat": "sparse-nonzero-v1", "weightVertexCount": 3, "weightInfluenceCount": 1, "absentInfluenceMeansZero": True, "attrs": {"normalizeWeights": 1}, "connections": []}],
    }
    return {
        "schemaVersion": 1,
        "root": {"path": "<root>", "attrs": {"mmd_model_name": "Counterfeit"}},
        "meshes": [mesh],
        "semanticDag": [{"key": "|body", "path": "|body", "type": "transform"}],
        "implementationNodes": [],
        "morphs": [],
        "physics": [],
        "connections": [{"src": "|body.visibility", "dst": "|bodyShape.visibility"}],
        "proxies": {"count": 1, "nodes": [{"key": "proxy:0", "name": "proxyA", "type": "proxy", "attrs": {"target": "|body"}}], "links": [{"src": "|body.out", "dst": "proxy:0.in"}]},
    }


class TestImportSceneContract(unittest.TestCase):
    def test_unchanged_snapshot_passes(self):
        result = compare_scenes(_snapshot(), _snapshot())
        self.assertEqual(result["status"], "pass")

    def test_uv_change_fails(self):
        changed = _snapshot()
        changed["meshes"][0]["uvSets"][0]["values"][1][0] = 0.25
        self.assertEqual(compare_scenes(_snapshot(), changed)["status"], "fail")

    def test_face_vertex_normal_change_fails(self):
        changed = _snapshot()
        changed["meshes"][0]["faceVertexNormals"][1][2] = 0.75
        self.assertEqual(compare_scenes(_snapshot(), changed)["status"], "fail")

    def test_weight_change_fails(self):
        changed = _snapshot()
        changed["meshes"][0]["skin"][0]["weights"][1]["weights"][0]["value"] = 0.5
        self.assertEqual(compare_scenes(_snapshot(), changed)["status"], "fail")

    def test_material_attribute_change_fails(self):
        changed = _snapshot()
        changed["meshes"][0]["materials"][0]["attrs"]["mmd_draw_flags"] = 7
        self.assertEqual(compare_scenes(_snapshot(), changed)["status"], "fail")

    def test_connection_change_fails(self):
        changed = _snapshot()
        changed["connections"][0]["dst"] = "|body.visibilityProxy"
        self.assertEqual(compare_scenes(_snapshot(), changed)["status"], "fail")

    def test_typed_semantic_connection_change_fails(self):
        left = _snapshot()
        right = copy.deepcopy(left)
        left["connections"] = [{"src": "bone:1.output", "dst": "material:1.input"}]
        right["connections"] = [{"src": "material:1.output", "dst": "material:1.input"}]
        self.assertEqual(compare_scenes(left, right)["status"], "fail")

    def test_physics_semantic_ids_include_node_role(self):
        class _Cmds:
            def attributeQuery(self, attr, **kwargs):
                return attr == "pmxIndex"

            def getAttr(self, plug, **kwargs):
                return "long" if kwargs.get("type") else 0

        cmds = _Cmds()
        self.assertEqual(_semantic_id(cmds, "|rigid", "mmdRigidBodyShape"), "rigidbody:0")
        self.assertEqual(_semantic_id(cmds, "|joint", "mmdPhysicsJointShape"), "physicsjoint:0")

    def test_material_semantic_ids_include_registry_role(self):
        class _Cmds:
            def attributeQuery(self, attr, **kwargs):
                return attr == "mmd_material_index" if kwargs.get("exists") else False

            def getAttr(self, plug, **kwargs):
                if kwargs.get("type"):
                    return "long"
                return 0

            def listConnections(self, node, **kwargs):
                return ["TestMaterial.message", "root_registry.materialMembers[0]"]

            def ls(self, node, **kwargs):
                return ["|root_registry"]

        cmds = _Cmds()
        self.assertEqual(
            _semantic_id(cmds, "TestMaterial", "dx11Shader", "|root"),
            "material:0@root_registry@0",
        )

    def test_vmd_authoring_uuids_normalize_to_semantic_endpoints(self):
        class _Cmds:
            uuid_paths = {
                "target-uuid-a": "|root|bone",
                "target-uuid-b": "|root|bone",
                "owner-uuid-a": "ownerNode",
                "owner-uuid-b": "ownerNode",
            }

            def ls(self, node, **kwargs):
                if kwargs.get("long"):
                    return [self.uuid_paths[node]] if node in self.uuid_paths else [node]
                return [node]

            def nodeType(self, node):
                return "joint" if str(node).endswith("bone") else "mmdAppend"

            def attributeQuery(self, attr, node, **kwargs):
                return kwargs.get("exists") and attr == "mmd_bone_index" and str(node).endswith("bone")

            def getAttr(self, plug, **kwargs):
                if kwargs.get("type"):
                    return "long"
                return 7

        attrs = {
            "mmd_vmd_authoring_proxy": True,
            "mmd_vmd_authoring_target_path": "|root|bone",
            "mmd_vmd_authoring_target_uuid": "target-uuid-a",
            "mmd_vmd_authoring_destinations_json": json.dumps(
                {"rotateX": {"owner_uuid": "owner-uuid-a", "plug": "ownerNode.baseRotateX"}}
            ),
        }
        normalized, ledger = _normalize_vmd_authoring_attrs(_Cmds(), "|root", "|proxy", attrs)
        self.assertEqual(normalized["mmd_vmd_authoring_target_uuid"], "bone:7.message")
        destinations = json.loads(normalized["mmd_vmd_authoring_destinations_json"])
        self.assertEqual(destinations["rotateX"]["owner_uuid"], "ownerNode.message")
        self.assertEqual({entry["raw"] for entry in ledger}, {"target-uuid-a", "owner-uuid-a"})

        attrs["mmd_vmd_authoring_target_uuid"] = "target-uuid-b"
        attrs["mmd_vmd_authoring_destinations_json"] = attrs["mmd_vmd_authoring_destinations_json"].replace(
            "owner-uuid-a", "owner-uuid-b"
        )
        normalized_again, _ = _normalize_vmd_authoring_attrs(_Cmds(), "|root", "|proxy", attrs)
        self.assertEqual(normalized, normalized_again)

    def test_vmd_authoring_uuid_owner_mismatch_fails_closed(self):
        class _Cmds:
            def ls(self, node, **kwargs):
                if kwargs.get("long"):
                    if node == "foreign-uuid":
                        return ["|foreign:root|ownerNode"]
                    if node == "ownerNode":
                        return ["|root|ownerNode"]
                    return [node]
                return [node]

            def nodeType(self, node):
                return "mmdAppend"

            def attributeQuery(self, attr, node, **kwargs):
                return False

        attrs = {
            "mmd_vmd_authoring_proxy": True,
            "mmd_vmd_authoring_target_path": "|root|target",
            "mmd_vmd_authoring_target_uuid": "|root|target",
            "mmd_vmd_authoring_destinations_json": json.dumps(
                {"rotateX": {"owner_uuid": "foreign-uuid", "plug": "ownerNode.baseRotateX"}}
            ),
        }
        with self.assertRaisesRegex(RuntimeError, "different node"):
            _normalize_vmd_authoring_attrs(_Cmds(), "|root", "|proxy", attrs)

    def test_vmd_authoring_uuid_unresolved_fails_closed(self):
        class _Cmds:
            def ls(self, node, **kwargs):
                return [] if kwargs.get("long") else [node]

        attrs = {
            "mmd_vmd_authoring_proxy": True,
            "mmd_vmd_authoring_target_path": "|root|target",
            "mmd_vmd_authoring_target_uuid": "missing-uuid",
            "mmd_vmd_authoring_destinations_json": "{}",
        }
        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            _normalize_vmd_authoring_attrs(_Cmds(), "|root", "|proxy", attrs)

    def test_shape_and_proxy_names_are_allowed_but_logged(self):
        changed = _snapshot()
        changed["meshes"][0]["shapeName"] = "generatedShape42"
        changed["proxies"]["nodes"][0]["name"] = "generatedProxy42"
        result = compare_scenes(_snapshot(), changed)
        self.assertEqual(result["status"], "pass")
        checks = {check["name"]: check for check in result["checks"]}
        self.assertEqual(checks["auto-shape-name-allowed"]["differences"], ["|body#mesh[0]"])
        self.assertEqual(checks["proxy-node-name-allowed"]["differences"], ["proxy:0"])

    def test_empty_scene_fails(self):
        self.assertEqual(compare_scenes({}, {})["status"], "fail")

    def test_nonfinite_value_fails(self):
        changed = _snapshot()
        changed["meshes"][0]["points"][0][0] = math.nan
        result = compare_scenes(_snapshot(), changed)
        self.assertEqual(result["status"], "fail")
        self.assertFalse(next(check for check in result["checks"] if check["name"] == "finite-values")["pass"])

    def test_equal_malformed_snapshots_fail_closed(self):
        malformed = _snapshot()
        del malformed["meshes"][0]["faceVertexNormals"]
        result = compare_scenes(malformed, copy.deepcopy(malformed))
        self.assertEqual(result["status"], "fail")
        structure = next(check for check in result["checks"] if check["name"] == "snapshot-structure")
        self.assertFalse(structure["pass"])

    def test_duplicate_semantic_rows_fail_closed(self):
        malformed = _snapshot()
        malformed["semanticDag"].append(copy.deepcopy(malformed["semanticDag"][0]))
        result = compare_scenes(malformed, copy.deepcopy(malformed))
        self.assertEqual(result["status"], "fail")
        structure = next(check for check in result["checks"] if check["name"] == "snapshot-structure")
        self.assertFalse(structure["pass"])

    def test_mmd_render_shape_difference_is_explicitly_allowed(self):
        changed = copy.deepcopy(_snapshot())
        changed["implementationNodes"] = [{"key": "|mmdRenderShape", "type": "mmdRenderShape"}]
        result = compare_scenes(_snapshot(), changed)
        self.assertEqual(result["status"], "pass")
        check = next(item for item in result["checks"] if item["name"] == "mmdRenderShape-implementation-allowed")
        self.assertTrue(check["allowed"])
        self.assertTrue(check["differences"])

    def test_non_render_implementation_node_is_not_allowed(self):
        changed = copy.deepcopy(_snapshot())
        changed["implementationNodes"] = [{"key": "|other", "type": "other"}]
        self.assertEqual(compare_scenes(_snapshot(), changed)["status"], "fail")

    def test_nested_native_links_use_allowance_without_hiding_shader_links(self):
        changed = _snapshot()
        link = {"src": "morph.outputDiffuse", "dst": "|render.materialValues[0]"}
        changed["implementationNodes"] = [{"path": "|render", "type": "mmdRenderShape"}]
        changed["implementationLinks"] = [link]
        links = changed["meshes"][0]["materials"][0]["connections"]
        links.append(link)
        result = compare_scenes(_snapshot(), changed)
        self.assertEqual(result["status"], "pass")
        ledger = next(c for c in result["checks"] if c["name"] == "mmdRenderShape-implementation-allowed")
        self.assertEqual(ledger["rightLinks"], [link])
        links.append({"src": "morph.outputDiffuse", "dst": "shader.diffuse"})
        self.assertEqual(compare_scenes(_snapshot(), changed)["status"], "fail")

    def test_morph_metadata_without_current_evaluation_fails(self):
        left = _snapshot()
        right = _snapshot()
        morph = {"key": "morph:0", "type": "blendShape", "attrs": {"mmd_morph_type": "vertex"}, "drivers": []}
        left["morphs"] = [morph]
        right["morphs"] = [copy.deepcopy(morph)]
        result = compare_scenes(left, right)
        self.assertEqual(result["status"], "fail")
        required = next(check for check in result["checks"] if check["name"] == "morph-evaluated-state-required")
        self.assertFalse(required["pass"])

    def test_capture_fails_closed_when_required_mesh_api_is_missing(self):
        class _Cmds:
            def ls(self, node, long=False):
                return [node]

            def listRelatives(self, node, **kwargs):
                if kwargs.get("allDescendents"):
                    return ["|root|body|bodyShape"]
                if kwargs.get("parent"):
                    return ["|root|body"]
                return []

            def nodeType(self, node):
                return "mesh" if node.endswith("Shape") else "transform"

            def attributeQuery(self, attr, **kwargs):
                return False

            def listConnections(self, node, **kwargs):
                return []

            def listAttr(self, node, **kwargs):
                return []

        with self.assertRaisesRegex(RuntimeError, "MSelectionList"):
            capture_scene(_Cmds(), object(), "|root")

    def test_message_attribute_value_is_omitted_for_connection_ledger(self):
        class _Cmds:
            def attributeQuery(self, attr, **kwargs):
                return attr == "mmd_model_registry"

            def getAttr(self, plug, **kwargs):
                if kwargs.get("type"):
                    return "message"
                raise AssertionError("message values must be represented by connections")

        self.assertIs(_read_attr(_Cmds(), "|root", "mmd_model_registry"), _MISSING)

    def test_compound_attribute_reads_typed_children(self):
        class _Cmds:
            children = {"MainTextureAdd": ["MainTextureAddR", "MainTextureAddG"]}
            values = {"MainTextureAddR": 0.25, "MainTextureAddG": 0.5}

            def attributeQuery(self, attr, **kwargs):
                if kwargs.get("exists"):
                    return attr in self.children or attr in self.values
                if kwargs.get("listChildren"):
                    return self.children.get(attr, [])
                return False

            def getAttr(self, plug, **kwargs):
                attr = plug.rsplit(".", 1)[-1]
                if kwargs.get("type"):
                    return "float4" if attr == "MainTextureAdd" else "float"
                if attr == "MainTextureAdd":
                    raise RuntimeError("attribute composed of complex data")
                return self.values[attr]

        self.assertEqual(
            _read_attr(_Cmds(), "TestMaterial", "MainTextureAdd"),
            {"MainTextureAddR": 0.25, "MainTextureAddG": 0.5},
        )

    def test_unsupported_leaf_attribute_still_fails(self):
        class _Cmds:
            def attributeQuery(self, attr, **kwargs):
                if kwargs.get("exists"):
                    return attr == "unsupported"
                if kwargs.get("listChildren"):
                    return []
                return False

            def getAttr(self, plug, **kwargs):
                if kwargs.get("type"):
                    return "float"
                raise RuntimeError("unsupported leaf")

        with self.assertRaisesRegex(RuntimeError, "unsupported leaf"):
            _read_attr(_Cmds(), "TestMaterial", "unsupported")

    def test_capture_api_failure_is_not_converted_to_empty_default(self):
        class _Cmds:
            def listConnections(self, node, **kwargs):
                raise ValueError("connection query failed")

        with self.assertRaisesRegex(ValueError, "connection query failed"):
            _call(_Cmds(), "listConnections", "|root", default=[])


if __name__ == "__main__":
    unittest.main()
