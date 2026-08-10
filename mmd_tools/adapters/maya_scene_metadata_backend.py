"""Read strict normalized model, material, bone, and morph metadata through Maya.

This is deliberately a narrow, read-only Maya integration boundary.  Semantic
values are taken only from persisted ``mmd_*`` attributes; ordinary Maya
display plugs and evaluated morph results are never treated as PMX authoring
data.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from mmd_tools.adapters.scene_metadata_adapter import SceneMetadataAdapter, SceneMetadataError
from mmd_tools.core.constants import (
    ATTR_MMD_AXIS_DIRECTION,
    ATTR_MMD_BONE_FLAGS,
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_BONE_NAME_EN,
    ATTR_MMD_BONE_OFFSET,
    ATTR_MMD_BONE_PARENT_INDEX,
    ATTR_MMD_CONNECT_BONE_INDEX,
    ATTR_MMD_CONNECTION_BONE,
    ATTR_MMD_CONNECT_INDEX,
    ATTR_MMD_DEFORM_LAYER,
    ATTR_MMD_EXTERNAL_PARENT_KEY,
    ATTR_MMD_FIXED_AXIS,
    ATTR_MMD_GRANT_PARENT,
    ATTR_MMD_GRANT_PARENT_INDEX,
    ATTR_MMD_GRANT_RATE,
    ATTR_MMD_IK_LIMIT_ANGLE,
    ATTR_MMD_IK_LINKS,
    ATTR_MMD_IK_LOOP,
    ATTR_MMD_IK_TARGET,
    ATTR_MMD_IK_TARGET_INDEX,
    ATTR_MMD_LOCAL_X_AXIS,
    ATTR_MMD_LOCAL_Z_AXIS,
    ATTR_MMD_MODEL_NAME,
    ATTR_MMD_MODEL_NAME_EN,
    ATTR_MMD_PMX_REST_POSITION,
    ATTR_MMD_X_AXIS_DIRECTION,
    ATTR_MMD_Z_AXIS_DIRECTION,
    ATTR_MMD_COMMENT,
    ATTR_MMD_COMMENT_EN,
    ATTR_MMD_MODEL_ROOT,
    ATTR_MMD_MODEL_REGISTRY,
    ATTR_MMD_REGISTRY_ROOT,
    ATTR_MMD_REGISTRY_SCHEMA,
    ATTR_MMD_DIFFUSE_COLOR,
    ATTR_MMD_AMBIENT_COLOR,
    ATTR_MMD_EDGE_COLOR,
    ATTR_MMD_EDGE_SIZE,
    ATTR_MMD_DRAW_FLAGS,
    ATTR_MMD_MEMO,
    ATTR_MMD_MATERIAL,
    ATTR_MMD_MATERIAL_INDEX,
    ATTR_MMD_MATERIAL_NAME,
    ATTR_MMD_MATERIAL_NAME_EN,
    ATTR_MMD_REGISTRY_MATERIAL_MEMBERS,
    ATTR_MMD_REGISTRY_MORPH_MEMBERS,
    ATTR_MMD_SHININESS,
    ATTR_MMD_SPECULAR_COLOR,
    ATTR_MMD_SPHERE_MODE,
    ATTR_MMD_SHARED_TOON_FLAG,
    ATTR_MMD_TOON_TEXTURE_INDEX,
    ATTR_MMD_BONE_MORPH_OFFSETS_RAW_JSON,
    ATTR_MMD_FLIP_MORPH_OFFSETS_JSON,
    ATTR_MMD_IMPULSE_MORPH_OFFSETS_JSON,
    ATTR_MMD_UV_MORPH_OFFSETS_JSON,
    ATTR_MMD_VERTEX_MORPH_OFFSETS_RAW_JSON,
)
from mmd_tools.core.pmx_data.bone import PmxBoneFlag
from mmd_tools.core.model_authoring_spec import MmdModelAuthoringSpec


class MayaSceneMetadataError(SceneMetadataError):
    """Raised when Maya metadata cannot be normalized without loss."""


class MayaSceneMetadataBackend:
    """Read model, material, PMX bone, and morph metadata from an adapter."""

    _DIFFUSE_ALPHA = "mmd_diffuse_alpha"
    _EDGE_ALPHA = "mmd_edge_alpha"
    _TEXTURE_PATH = "mmd_texture_path"
    _EXPLICIT_RESOLVED_TEXTURE_PATH = "mmd_resolved_texture_path"
    _SPHERE_PATH = "mmd_sphere_path"
    _EXPLICIT_RESOLVED_SPHERE_PATH = "mmd_resolved_sphere_texture_path"
    _TOON_PATH = "mmd_toon_path"
    _EXPLICIT_RESOLVED_TOON_PATH = "mmd_resolved_toon_texture_path"
    _ORIGINAL_TEXTURE_PATH = "mmd_original_texture_path"
    _FILE_TEXTURE_NAME = "fileTextureName"

    def __init__(self, cmds_adapter: Any) -> None:
        self._cmds = cmds_adapter
        self._write_transaction: dict[str, Any] | None = None

    def read_model_metadata(self, root: str) -> Mapping[str, Any]:
        """Return the canonical model-header mapping for an existing root."""
        self._require_root(root)
        return {
            "name": self._required_string(root, ATTR_MMD_MODEL_NAME),
            "name_english": self._required_string(root, ATTR_MMD_MODEL_NAME_EN),
            "comment": self._required_string(root, ATTR_MMD_COMMENT),
            "comment_english": self._required_string(root, ATTR_MMD_COMMENT_EN),
        }

    def iter_bone_metadata(self, root: str) -> Iterable[Mapping[str, Any]]:
        """Yield canonical PMX bone mappings for tagged descendant joints."""
        self._require_root(root)
        joints = self._cmds.list_relatives(root, allDescendents=True, fullPath=True, type="joint") or []
        seen_bindings: set[str] = set()
        tagged: list[dict[str, Any]] = []
        for joint in joints:
            if not isinstance(joint, str) or not joint.startswith("|"):
                raise MayaSceneMetadataError(f"{root!r}: joint binding identity must be a canonical long path")
            if joint in seen_bindings:
                raise MayaSceneMetadataError(f"{root!r}: duplicate joint binding identity {joint!r}")
            seen_bindings.add(joint)
            if not self._has_attr(joint, ATTR_MMD_BONE_INDEX):
                continue
            metadata = self._read_bone(joint)
            index = metadata["index"]
            if any(item["index"] == index for item in tagged):
                raise MayaSceneMetadataError(f"{root!r}: duplicate mmd_bone_index {index}")
            tagged.append(metadata)
        references = self._build_references(tagged)
        for metadata in tagged:
            joint = metadata["binding_identity"]
            self._read_connect(joint, metadata["flags"], metadata, references)
            self._read_grant(joint, metadata["flags"], metadata, references)
            self._read_axes(joint, metadata["flags"], metadata)
            self._read_external_parent(joint, metadata["flags"], metadata)
            self._read_ik(joint, metadata["flags"], metadata, references)
            yield metadata

    def iter_material_metadata(self, root: str) -> Iterable[Mapping[str, Any]]:
        """Yield strict PMX material mappings owned by one explicit root.

        New scenes use the root's model-registry ``materialMembers`` message
        array.  Older scenes are discovered only through meshes below that
        root and their shading-engine assignments; no scene-wide scan is used.
        """
        self._require_root(root)
        members = self._registry_material_members(root)
        if members is None:
            members = self._legacy_material_members(root)

        seen_bindings: set[str] = set()
        seen_indices: dict[int, str] = {}
        for member in members:
            identity = self._material_identity(member)
            if identity in seen_bindings:
                continue
            seen_bindings.add(identity)
            metadata = self._read_material(identity)
            index = metadata["index"]
            previous = seen_indices.get(index)
            if previous is not None and previous != identity:
                raise MayaSceneMetadataError(
                    f"{root!r}: duplicate mmd_material_index {index} on {previous!r} and {identity!r}"
                )
            seen_indices[index] = identity
            yield metadata

    def begin_write(self, model_root: str) -> None:
        """Capture the current spec and open one Maya undo chunk."""
        if self._write_transaction is not None:
            raise MayaSceneMetadataError("a metadata write transaction is already active")
        self._require_root(model_root)
        original = SceneMetadataAdapter(self).read_spec(model_root)
        canonical_root = self._material_identity(model_root)
        if not bool(self._call_adapter("undo_info", query=True, state=True)):
            raise MayaSceneMetadataError("Maya undo must be enabled for metadata writes")
        transaction = {
            "root": canonical_root,
            "original_fingerprint": original.fingerprint(),
            "target": original.to_mapping(),
            "bone_bindings": {bone.index: bone.binding_identity for bone in original.bones},
            "material_bindings": {material.index: material.binding_identity for material in original.materials},
            "morph_bindings": {morph.index: morph.binding_identity for morph in original.morphs},
            "bindings_rebased": False,
            "chunk_open": False,
        }
        self._call_adapter("undo_info", openChunk=True, chunkName="MMD Authoring Metadata")
        transaction["chunk_open"] = True
        self._write_transaction = transaction

    def rebase_write_bindings(
        self,
        model_root: str,
        target_spec: MmdModelAuthoringSpec,
    ) -> None:
        """Adopt one structurally updated binding set inside the transaction.

        Structural authoring creates, removes, or reindexes Maya bindings
        before the regular full-spec metadata hooks run.  This method is the
        only supported bridge between those two phases.  It performs a fresh
        strict scene read and accepts the rebase only when every target
        collection has the exact same ``index -> binding_identity`` mapping.

        The rebase is deliberately single-use.  A coordinator therefore
        cannot hide multiple structural phases inside one metadata write or
        mutate backend-private transaction state directly.
        """
        transaction = self._active_transaction(model_root)
        if transaction["bindings_rebased"]:
            raise MayaSceneMetadataError("write bindings have already been rebased")
        if not isinstance(target_spec, MmdModelAuthoringSpec):
            raise MayaSceneMetadataError("target_spec must be an MmdModelAuthoringSpec")

        try:
            scene_spec = SceneMetadataAdapter(self).read_spec(model_root)
        except Exception as exc:
            raise MayaSceneMetadataError(f"failed to read structurally updated bindings: {exc}") from exc

        sections = (
            ("bone", scene_spec.bones, target_spec.bones),
            ("material", scene_spec.materials, target_spec.materials),
            ("morph", scene_spec.morphs, target_spec.morphs),
        )
        rebased: dict[str, dict[int, str | None]] = {}
        for label, scene_items, target_items in sections:
            scene_bindings = {item.index: item.binding_identity for item in scene_items}
            target_bindings = {item.index: item.binding_identity for item in target_items}
            if scene_bindings != target_bindings:
                raise MayaSceneMetadataError(
                    f"{label} binding/index set does not match structural target: "
                    f"scene={scene_bindings!r}, target={target_bindings!r}"
                )
            rebased[label] = target_bindings

        # Preserve the strict post-structure scene as the baseline used by
        # material-path and morph-payload safety checks in the apply phase.
        transaction["target"] = scene_spec.to_mapping()
        transaction["bone_bindings"] = rebased["bone"]
        transaction["material_bindings"] = rebased["material"]
        transaction["morph_bindings"] = rebased["morph"]
        transaction["bindings_rebased"] = True

    def apply_model_metadata(self, model_root: str, metadata: Mapping[str, Any]) -> None:
        transaction = self._active_transaction(model_root)
        expected = {"name", "name_english", "comment", "comment_english"}
        self._require_exact_mapping(metadata, expected, "model metadata")
        attrs = {
            "name": ATTR_MMD_MODEL_NAME,
            "name_english": ATTR_MMD_MODEL_NAME_EN,
            "comment": ATTR_MMD_COMMENT,
            "comment_english": ATTR_MMD_COMMENT_EN,
        }
        for field, attr in attrs.items():
            self._set_string(model_root, attr, metadata[field])
        transaction["target"]["model"] = dict(metadata)

    def apply_bone_metadata(self, model_root: str, metadata: Iterable[Mapping[str, Any]]) -> None:
        transaction = self._active_transaction(model_root)
        items = self._write_items(metadata, "bone")
        self._require_same_bindings(items, transaction["bone_bindings"], "bone")
        target_by_index = {item["index"]: item for item in items}
        for item in items:
            node = item["binding_identity"]
            self._set_string(node, ATTR_MMD_BONE_NAME, item["name"])
            self._set_string(node, ATTR_MMD_BONE_NAME_EN, item["name_english"])
            self._set_scalar(node, ATTR_MMD_BONE_PARENT_INDEX, item["parent_index"])
            self._set_vector(node, ATTR_MMD_PMX_REST_POSITION, item["rest_position"])
            self._set_scalar(node, ATTR_MMD_DEFORM_LAYER, item["transform_layer"])
            self._set_scalar(node, ATTR_MMD_BONE_FLAGS, item["flags"])
            self._write_optional_bone_reference(
                node,
                item["connect_bone_index"],
                (ATTR_MMD_CONNECT_INDEX, ATTR_MMD_CONNECT_BONE_INDEX),
                ATTR_MMD_CONNECTION_BONE,
                target_by_index,
            )
            if item["connect_bone_index"] is not None:
                self._set_optional_vector(node, ATTR_MMD_BONE_OFFSET, (0.0, -1.0, 0.0))
            elif item["tail_offset"] is not None:
                self._set_optional_vector(node, ATTR_MMD_BONE_OFFSET, item["tail_offset"])
            self._write_optional_bone_reference(
                node,
                item["grant_parent_index"],
                (ATTR_MMD_GRANT_PARENT_INDEX,),
                ATTR_MMD_GRANT_PARENT,
                target_by_index,
            )
            if item["grant_parent_index"] is not None:
                self._set_existing_scalar(node, ATTR_MMD_GRANT_RATE, item["grant_ratio"])
            else:
                self._delete_existing_attr(node, ATTR_MMD_GRANT_PARENT_INDEX)
                self._set_existing_string(node, ATTR_MMD_GRANT_PARENT, "")
                self._set_existing_scalar(node, ATTR_MMD_GRANT_RATE, 1.0)
            for attr, value in (
                (ATTR_MMD_FIXED_AXIS, item["fixed_axis"]),
                (ATTR_MMD_AXIS_DIRECTION, item["fixed_axis"]),
                (ATTR_MMD_LOCAL_X_AXIS, item["local_axis_x"]),
                (ATTR_MMD_X_AXIS_DIRECTION, item["local_axis_x"]),
                (ATTR_MMD_LOCAL_Z_AXIS, item["local_axis_z"]),
                (ATTR_MMD_Z_AXIS_DIRECTION, item["local_axis_z"]),
            ):
                if value is not None:
                    self._set_optional_vector(node, attr, value)
            if item["fixed_axis"] is None:
                self._set_optional_vector(node, ATTR_MMD_FIXED_AXIS, (0.0, 0.0, 1.0))
                self._delete_existing_attr(node, ATTR_MMD_AXIS_DIRECTION)
            if item["local_axis_x"] is None and item["local_axis_z"] is None:
                self._set_optional_vector(node, ATTR_MMD_LOCAL_X_AXIS, (1.0, 0.0, 0.0))
                self._set_optional_vector(node, ATTR_MMD_LOCAL_Z_AXIS, (0.0, 0.0, 1.0))
                self._delete_existing_attr(node, ATTR_MMD_X_AXIS_DIRECTION)
                self._delete_existing_attr(node, ATTR_MMD_Z_AXIS_DIRECTION)
            if item["external_parent_key"] is not None:
                self._set_existing_scalar(node, ATTR_MMD_EXTERNAL_PARENT_KEY, item["external_parent_key"])
            else:
                self._set_existing_scalar(node, ATTR_MMD_EXTERNAL_PARENT_KEY, -1)
            self._write_optional_bone_reference(
                node,
                item["ik_target_index"],
                (ATTR_MMD_IK_TARGET_INDEX,),
                ATTR_MMD_IK_TARGET,
                target_by_index,
            )
            if item["ik_target_index"] is not None:
                self._set_existing_scalar(node, ATTR_MMD_IK_LOOP, item["ik_loop_count"])
            if item["ik_target_index"] is not None and item["ik_limit_radian"] is not None:
                self._set_existing_scalar(node, ATTR_MMD_IK_LIMIT_ANGLE, item["ik_limit_radian"])
            if item["ik_target_index"] is not None:
                self._set_existing_string(
                    node,
                    ATTR_MMD_IK_LINKS,
                    json.dumps(item["ik_links"], ensure_ascii=False, separators=(",", ":")),
                )
            else:
                self._delete_existing_attr(node, ATTR_MMD_IK_TARGET_INDEX)
                self._set_existing_string(node, ATTR_MMD_IK_TARGET, "")
                self._set_existing_scalar(node, ATTR_MMD_IK_LOOP, 10)
                self._set_existing_scalar(node, ATTR_MMD_IK_LIMIT_ANGLE, 2.0)
                self._set_existing_string(node, ATTR_MMD_IK_LINKS, "[]")
        transaction["target"]["bones"] = items

    def apply_material_metadata(self, model_root: str, metadata: Iterable[Mapping[str, Any]]) -> None:
        transaction = self._active_transaction(model_root)
        items = self._write_items(metadata, "material")
        self._require_same_bindings(items, transaction["material_bindings"], "material")
        original_by_index = {item["index"]: item for item in transaction["target"]["materials"]}
        path_fields = {
            "texture_path",
            "resolved_texture_path",
            "sphere_texture_path",
            "resolved_sphere_texture_path",
            "toon_texture_path",
            "resolved_toon_texture_path",
        }
        for item in items:
            original = original_by_index[item["index"]]
            changed_paths = sorted(field for field in path_fields if item[field] != original[field])
            if changed_paths:
                raise MayaSceneMetadataError(
                    f"material {item['index']} texture path changes require a binding transaction: {changed_paths!r}"
                )
        for item in items:
            node = item["binding_identity"]
            self._set_string(node, ATTR_MMD_MATERIAL_NAME, item["name"])
            self._set_string(node, ATTR_MMD_MATERIAL_NAME_EN, item["name_english"])
            self._set_vector(node, ATTR_MMD_DIFFUSE_COLOR, item["diffuse"][:3])
            self._set_scalar(node, self._DIFFUSE_ALPHA, item["diffuse"][3])
            self._set_vector(node, ATTR_MMD_SPECULAR_COLOR, item["specular"])
            self._set_scalar(node, ATTR_MMD_SHININESS, item["specular_coefficient"])
            self._set_vector(node, ATTR_MMD_AMBIENT_COLOR, item["ambient"])
            self._set_scalar(node, ATTR_MMD_DRAW_FLAGS, item["draw_flags"])
            self._set_vector(node, ATTR_MMD_EDGE_COLOR, item["edge_color"][:3])
            self._set_scalar(node, self._EDGE_ALPHA, item["edge_color"][3])
            self._set_scalar(node, ATTR_MMD_EDGE_SIZE, item["edge_size"])
            self._set_scalar(node, ATTR_MMD_SPHERE_MODE, item["sphere_mode"])
            self._set_scalar(node, ATTR_MMD_SHARED_TOON_FLAG, int(item["shared_toon"]))
            toon_index = -1 if item["toon_texture_index"] is None else item["toon_texture_index"]
            self._set_scalar(node, ATTR_MMD_TOON_TEXTURE_INDEX, toon_index)
            self._set_string(node, ATTR_MMD_MEMO, item["memo"])
        transaction["target"]["materials"] = items

    def apply_morph_metadata(self, model_root: str, metadata: Iterable[Mapping[str, Any]]) -> None:
        transaction = self._active_transaction(model_root)
        items = self._write_items(metadata, "morph")
        self._require_same_bindings(items, transaction["morph_bindings"], "morph")
        original_by_index = {item["index"]: item for item in transaction["target"]["morphs"]}
        for item in items:
            original = original_by_index[item["index"]]
            for field in ("morph_type", "offsets", "runtime_capability", "loss_policy"):
                if item[field] != original[field]:
                    raise MayaSceneMetadataError(
                        f"morph {item['index']} {field} changes require a binding transaction"
                    )
        for item in items:
            node = item["binding_identity"]
            self._set_string(node, "mmd_morph_name", item["name"])
            self._set_string(node, "mmd_morph_name_en", item["name_english"])
            self._set_scalar(node, "mmd_morph_panel", item["panel"])
        transaction["target"]["morphs"] = items

    def commit_write(self, model_root: str) -> None:
        transaction = self._active_transaction(model_root)
        try:
            expected = MmdModelAuthoringSpec.from_mapping(transaction["target"]).fingerprint()
            actual = SceneMetadataAdapter(self).read_spec(model_root).fingerprint()
        except Exception as exc:
            raise MayaSceneMetadataError(f"failed to verify metadata transaction: {exc}") from exc
        if actual != expected:
            raise MayaSceneMetadataError(
                f"metadata transaction fingerprint mismatch: expected {expected}, got {actual}"
            )
        self._call_adapter("undo_info", closeChunk=True)
        self._write_transaction = None

    def rollback_write(self, model_root: str) -> None:
        transaction = self._active_transaction(model_root)
        try:
            if transaction["chunk_open"]:
                self._call_adapter("undo_info", closeChunk=True)
                transaction["chunk_open"] = False
            self._call_adapter("undo")
        finally:
            self._write_transaction = None
        actual = SceneMetadataAdapter(self).read_spec(model_root).fingerprint()
        if actual != transaction["original_fingerprint"]:
            raise MayaSceneMetadataError("metadata rollback fingerprint mismatch")

    def iter_morph_metadata(self, root: str) -> Iterable[Mapping[str, Any]]:
        """Yield strict raw PMX morph mappings owned by one explicit root."""
        self._require_root(root)
        members = self._registry_morph_members(root)
        if members is None:
            members = self._legacy_morph_members(root)

        seen_bindings: set[str] = set()
        seen_indices: dict[int, str] = {}
        for member in members:
            identity = self._material_identity(member)
            if identity in seen_bindings:
                raise MayaSceneMetadataError(f"{root!r}: duplicate morph binding identity {identity!r}")
            seen_bindings.add(identity)
            if self._node_type(identity) != "network":
                raise MayaSceneMetadataError(f"{identity!r}: morph binding must be a network node")
            metadata = self._read_morph(identity)
            index = metadata["index"]
            previous = seen_indices.get(index)
            if previous is not None:
                raise MayaSceneMetadataError(
                    f"{root!r}: duplicate mmd_morph_index {index} on {previous!r} and {identity!r}"
                )
            seen_indices[index] = identity
            yield metadata

    def _registry_morph_members(self, root: str) -> list[str] | None:
        """Return validated registry morph members, or ``None`` for legacy scenes."""
        requested_root = self._material_identity(root)
        if not self._has_attr(root, ATTR_MMD_MODEL_REGISTRY):
            return None
        registries = self._list_connections(
            f"{root}.{ATTR_MMD_MODEL_REGISTRY}", source=True, destination=False
        )
        if len(registries) != 1:
            raise MayaSceneMetadataError(f"{root!r}: model registry must have exactly one connection")
        registry = self._material_identity(registries[0])
        if not self._has_attr(registry, ATTR_MMD_REGISTRY_SCHEMA):
            raise MayaSceneMetadataError(f"{registry!r}: registry schema is missing")
        schema = self._required(registry, ATTR_MMD_REGISTRY_SCHEMA)
        if not isinstance(schema, str) or schema != "1":
            raise MayaSceneMetadataError(f"{registry!r}: unsupported registry schema {schema!r}")
        if not self._has_attr(registry, ATTR_MMD_REGISTRY_ROOT):
            raise MayaSceneMetadataError(f"{registry!r}: registry root link is missing")
        linked_roots = self._list_connections(
            f"{registry}.{ATTR_MMD_REGISTRY_ROOT}", source=True, destination=False
        )
        if len(linked_roots) != 1 or self._material_identity(linked_roots[0]) != requested_root:
            raise MayaSceneMetadataError(f"{registry!r}: registry root link is not exactly {root!r}")
        if not self._has_attr(registry, ATTR_MMD_REGISTRY_MORPH_MEMBERS):
            return []
        return [
            self._material_identity(item)
            for item in self._list_connections(
                f"{registry}.{ATTR_MMD_REGISTRY_MORPH_MEMBERS}", source=True, destination=False
            )
        ]

    def _legacy_morph_members(self, root: str) -> list[str]:
        """Discover legacy morph nodes only through their explicit root link."""
        requested_root = self._material_identity(root)
        candidates = self._call_adapter("ls", type="network") or []
        if isinstance(candidates, (str, bytes, bytearray)):
            raise MayaSceneMetadataError("ls(type='network') returned a scalar")
        members: list[str] = []
        for candidate in candidates:
            identity = self._material_identity(candidate)
            if not self._has_attr(identity, "mmd_morph_type"):
                continue
            if not self._has_attr(identity, ATTR_MMD_MODEL_ROOT):
                continue
            roots = self._list_connections(
                f"{identity}.{ATTR_MMD_MODEL_ROOT}", source=True, destination=False
            )
            if len(roots) != 1:
                raise MayaSceneMetadataError(
                    f"{identity!r}: legacy morph root ownership must have exactly one connection"
                )
            if self._material_identity(roots[0]) == requested_root:
                members.append(identity)
        return members

    def _read_morph(self, node: str) -> dict[str, Any]:
        morph_type = self._required_string(node, "mmd_morph_type")
        attr_by_type = {
            "vertex": ATTR_MMD_VERTEX_MORPH_OFFSETS_RAW_JSON,
            "bone": ATTR_MMD_BONE_MORPH_OFFSETS_RAW_JSON,
            "group": "mmd_group_morph_offsets_json",
            "material": "mmd_material_morph_offsets_json",
            "uv": ATTR_MMD_UV_MORPH_OFFSETS_JSON,
            "additional_uv1": ATTR_MMD_UV_MORPH_OFFSETS_JSON,
            "additional_uv2": ATTR_MMD_UV_MORPH_OFFSETS_JSON,
            "additional_uv3": ATTR_MMD_UV_MORPH_OFFSETS_JSON,
            "additional_uv4": ATTR_MMD_UV_MORPH_OFFSETS_JSON,
            "flip": ATTR_MMD_FLIP_MORPH_OFFSETS_JSON,
            "impulse": ATTR_MMD_IMPULSE_MORPH_OFFSETS_JSON,
        }
        try:
            offsets_attr = attr_by_type[morph_type]
        except KeyError as exc:
            raise MayaSceneMetadataError(f"{node}.mmd_morph_type is unknown: {morph_type!r}") from exc
        offsets = self._required_morph_offsets(node, offsets_attr, morph_type)
        unsupported = morph_type in {"flip", "impulse"}
        return {
            "name": self._required_string(node, "mmd_morph_name"),
            "name_english": self._required_string(node, "mmd_morph_name_en"),
            "index": self._required_int(node, "mmd_morph_index", minimum=0),
            "panel": self._required_int(node, "mmd_morph_panel", minimum=0, maximum=4),
            "morph_type": morph_type,
            "offsets": offsets,
            "binding_identity": node,
            "runtime_capability": "unsupported" if unsupported else "supported",
            "loss_policy": "reject" if unsupported else "none",
        }

    def _required_morph_offsets(self, node: str, attr: str, morph_type: str) -> list[dict[str, Any]]:
        raw = self._required_string(node, attr)
        try:
            value = json.loads(raw, object_pairs_hook=self._unique_json_object)
        except (TypeError, ValueError) as exc:
            raise MayaSceneMetadataError(f"{node}.{attr} must contain strict JSON: {exc}") from exc
        if not isinstance(value, list):
            raise MayaSceneMetadataError(f"{node}.{attr} must contain a JSON list")
        return [
            self._normalize_morph_offset(node, attr, morph_type, offset, index)
            for index, offset in enumerate(value)
        ]

    @staticmethod
    def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON field {key!r}")
            value[key] = item
        return value

    def _normalize_morph_offset(
        self, node: str, attr: str, morph_type: str, offset: Any, index: int
    ) -> dict[str, Any]:
        path = f"{node}.{attr}[{index}]"
        if not isinstance(offset, Mapping):
            raise MayaSceneMetadataError(f"{path} must be a mapping")
        schemas: dict[str, dict[str, tuple[str, int | None]]] = {
            "vertex": {"vertex_index": ("index", None), "position_offset": ("vector", 3)},
            "bone": {
                "bone_index": ("index", None),
                "translation": ("vector", 3),
                "rotation": ("vector", 4),
            },
            "group": {"morph_index": ("index", None), "morph_rate": ("number", None)},
            "material": {
                "material_index": ("signed_index", None),
                "operation_type": ("operation", None),
                "diffuse": ("vector", 4),
                "specular": ("vector", 3),
                "specular_coefficient": ("number", None),
                "ambient": ("vector", 3),
                "edge_color": ("vector", 4),
                "edge_size": ("number", None),
                "texture_factor": ("vector", 4),
                "sphere_texture_factor": ("vector", 4),
                "toon_texture_factor": ("vector", 4),
            },
            "uv": {"vertex_index": ("index", None), "uv_offset": ("vector", 4)},
            "flip": {"morph_index": ("index", None), "flip_rate": ("number", None)},
            "impulse": {
                "rigid_body_index": ("index", None),
                "impulse": ("vector", 3),
                "torque": ("vector", 3),
            },
        }
        schema = schemas["uv"] if morph_type.startswith("additional_uv") else schemas[morph_type]
        actual = set(offset)
        expected = set(schema)
        if actual != expected:
            unknown = sorted(actual - expected)
            missing = sorted(expected - actual)
            raise MayaSceneMetadataError(f"{path} fields mismatch; unknown={unknown!r}, missing={missing!r}")
        result: dict[str, Any] = {}
        for key, (kind, size) in schema.items():
            field = f"{path}.{key}"
            item = offset[key]
            if kind in {"index", "signed_index", "operation"}:
                minimum = -1 if kind == "signed_index" else 0
                maximum = 1 if kind == "operation" else None
                result[key] = self._strict_json_int(item, field, minimum=minimum, maximum=maximum)
            elif kind == "number":
                result[key] = self._strict_json_number(item, field)
            else:
                result[key] = self._strict_json_vector(item, field, size or 0)
        return result

    @staticmethod
    def _strict_json_int(value: Any, field: str, *, minimum: int, maximum: int | None = None) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise MayaSceneMetadataError(f"{field} must be an integer")
        if value < minimum or (maximum is not None and value > maximum):
            bounds = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
            raise MayaSceneMetadataError(f"{field} must be {bounds}")
        return value

    @staticmethod
    def _strict_json_number(value: Any, field: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise MayaSceneMetadataError(f"{field} must be a finite number")
        return float(value)

    @classmethod
    def _strict_json_vector(cls, value: Any, field: str, size: int) -> list[float]:
        if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence) or len(value) != size:
            raise MayaSceneMetadataError(f"{field} must contain exactly {size} numbers")
        return [cls._strict_json_number(item, f"{field}[{index}]") for index, item in enumerate(value)]

    def _registry_material_members(self, root: str) -> list[str] | None:
        """Return validated registry members, or ``None`` for legacy scenes."""
        requested_root = self._material_identity(root)
        if not self._has_attr(root, ATTR_MMD_MODEL_REGISTRY):
            return None
        registries = self._list_connections(
            f"{root}.{ATTR_MMD_MODEL_REGISTRY}", source=True, destination=False
        )
        if len(registries) != 1:
            raise MayaSceneMetadataError(f"{root!r}: model registry must have exactly one connection")
        registry = self._material_identity(registries[0])
        if not self._has_attr(registry, ATTR_MMD_REGISTRY_SCHEMA):
            raise MayaSceneMetadataError(f"{registry!r}: registry schema is missing")
        schema = self._required(registry, ATTR_MMD_REGISTRY_SCHEMA)
        if not isinstance(schema, str) or schema != "1":
            raise MayaSceneMetadataError(f"{registry!r}: unsupported registry schema {schema!r}")
        if not self._has_attr(registry, ATTR_MMD_REGISTRY_ROOT):
            raise MayaSceneMetadataError(f"{registry!r}: registry root link is missing")
        linked_roots = self._list_connections(
            f"{registry}.{ATTR_MMD_REGISTRY_ROOT}", source=True, destination=False
        )
        if len(linked_roots) != 1 or self._material_identity(linked_roots[0]) != requested_root:
            raise MayaSceneMetadataError(f"{registry!r}: registry root link is not exactly {root!r}")
        if not self._has_attr(registry, ATTR_MMD_REGISTRY_MATERIAL_MEMBERS):
            # Registries created before the material ownership category are
            # valid legacy scenes. Their mesh/SG graph remains the bounded
            # fallback; malformed schema/root links above never fall back.
            return None
        return [self._material_identity(item) for item in self._list_connections(
            f"{registry}.{ATTR_MMD_REGISTRY_MATERIAL_MEMBERS}", source=True, destination=False
        )]

    def _legacy_material_members(self, root: str) -> list[str]:
        """Discover tagged materials assigned below ``root`` only."""
        shapes = self._call_adapter("list_relatives", root, allDescendents=True, type="mesh") or []
        members: list[str] = []
        for shape in shapes:
            shading_groups = self._list_connections(shape, type="shadingEngine")
            for shading_group in shading_groups:
                candidates = self._list_connections(
                    shading_group, source=True, destination=False
                )
                for candidate in candidates:
                    identity = self._material_identity(candidate)
                    if self._node_type(identity) in {"shadingEngine", "file", "place2dTexture"}:
                        continue
                    if self._has_attr(identity, ATTR_MMD_MATERIAL):
                        members.append(identity)
        return members

    def _read_material(self, shader: str) -> dict[str, Any]:
        """Read every field required by :class:`MmdMaterialSpec`."""
        tag = self._required_int(shader, ATTR_MMD_MATERIAL)
        if tag != 1:
            raise MayaSceneMetadataError(f"{shader}.{ATTR_MMD_MATERIAL} must equal integer 1")
        shared_flag = self._required_int(shader, ATTR_MMD_SHARED_TOON_FLAG)
        if shared_flag not in (0, 1):
            raise MayaSceneMetadataError(f"{shader}.{ATTR_MMD_SHARED_TOON_FLAG} must be 0 or 1")
        sphere_mode = self._required_int(shader, ATTR_MMD_SPHERE_MODE)
        if sphere_mode not in (0, 1, 2, 3):
            raise MayaSceneMetadataError(f"{shader}.{ATTR_MMD_SPHERE_MODE} must be between 0 and 3")
        toon_index = self._required_int(shader, ATTR_MMD_TOON_TEXTURE_INDEX, minimum=-1)
        shared_toon = bool(shared_flag)
        toon_source = self._source_path(shader, self._TOON_PATH)
        toon_explicit = self._optional_path(shader, self._EXPLICIT_RESOLVED_TOON_PATH)
        if shared_toon and (toon_source or toon_explicit):
            raise MayaSceneMetadataError(
                f"{shader}: shared toon must use table index, not a toon texture path"
            )
        return {
            "name": self._required_string(shader, ATTR_MMD_MATERIAL_NAME),
            "name_english": self._required_string(shader, ATTR_MMD_MATERIAL_NAME_EN),
            "index": self._required_int(shader, ATTR_MMD_MATERIAL_INDEX, minimum=0),
            "diffuse": self._required_vector_with_alpha(shader, ATTR_MMD_DIFFUSE_COLOR, self._DIFFUSE_ALPHA),
            "specular": self._required_vector(shader, ATTR_MMD_SPECULAR_COLOR),
            "specular_coefficient": self._required_number(shader, ATTR_MMD_SHININESS),
            "ambient": self._required_vector(shader, ATTR_MMD_AMBIENT_COLOR),
            "draw_flags": self._required_int(shader, ATTR_MMD_DRAW_FLAGS, minimum=0),
            "edge_color": self._required_vector_with_alpha(shader, ATTR_MMD_EDGE_COLOR, self._EDGE_ALPHA),
            "edge_size": self._required_number(shader, ATTR_MMD_EDGE_SIZE),
            "texture_path": self._source_path(shader, self._TEXTURE_PATH),
            "resolved_texture_path": self._resolved_path(
                shader, self._TEXTURE_PATH, self._EXPLICIT_RESOLVED_TEXTURE_PATH
            ),
            "sphere_texture_path": self._source_path(shader, self._SPHERE_PATH),
            "resolved_sphere_texture_path": self._resolved_path(
                shader, self._SPHERE_PATH, self._EXPLICIT_RESOLVED_SPHERE_PATH
            ),
            "sphere_mode": sphere_mode,
            "shared_toon": shared_toon,
            "toon_texture_index": None if toon_index == -1 else toon_index,
            "toon_texture_path": None if shared_toon else toon_source,
            "resolved_toon_texture_path": (
                None
                if shared_toon
                else self._resolved_path(
                    shader, self._TOON_PATH, self._EXPLICIT_RESOLVED_TOON_PATH
                )
            ),
            "memo": self._required_string(shader, ATTR_MMD_MEMO),
            "binding_identity": shader,
        }

    def _active_transaction(self, model_root: str) -> dict[str, Any]:
        transaction = self._write_transaction
        if transaction is None:
            raise MayaSceneMetadataError("no metadata write transaction is active")
        if self._material_identity(model_root) != transaction["root"]:
            raise MayaSceneMetadataError("metadata write transaction belongs to another model root")
        return transaction

    @staticmethod
    def _require_exact_mapping(metadata: Any, expected: set[str], context: str) -> None:
        if not isinstance(metadata, Mapping):
            raise MayaSceneMetadataError(f"{context} must be a mapping")
        actual = set(metadata)
        if actual != expected:
            raise MayaSceneMetadataError(
                f"{context} fields mismatch; unknown={sorted(actual - expected)!r}, missing={sorted(expected - actual)!r}"
            )

    @staticmethod
    def _write_items(metadata: Iterable[Mapping[str, Any]], context: str) -> list[dict[str, Any]]:
        if isinstance(metadata, (str, bytes, bytearray)):
            raise MayaSceneMetadataError(f"{context} metadata must be an iterable of mappings")
        try:
            items = [dict(item) for item in metadata]
        except (TypeError, ValueError) as exc:
            raise MayaSceneMetadataError(f"{context} metadata must contain mappings") from exc
        return items

    @staticmethod
    def _require_same_bindings(
        items: Sequence[Mapping[str, Any]], original: Mapping[int, Any], context: str
    ) -> None:
        target: dict[int, Any] = {}
        for item in items:
            index = item.get("index")
            binding = item.get("binding_identity")
            if isinstance(index, bool) or not isinstance(index, int) or index in target:
                raise MayaSceneMetadataError(f"{context} indices must remain unique integers")
            target[index] = binding
        if target != dict(original):
            raise MayaSceneMetadataError(
                f"{context} create/delete/reindex or binding changes require a structural transaction"
            )

    def _set_scalar(self, node: str, attr: str, value: Any) -> None:
        if not self._has_attr(node, attr):
            raise MayaSceneMetadataError(f"{node}.{attr} is required for metadata write")
        self._call_adapter("set_attr", f"{node}.{attr}", value)

    def _set_string(self, node: str, attr: str, value: Any) -> None:
        if not isinstance(value, str):
            raise MayaSceneMetadataError(f"{node}.{attr} must be a string")
        if not self._has_attr(node, attr):
            raise MayaSceneMetadataError(f"{node}.{attr} is required for metadata write")
        self._call_adapter("set_attr", f"{node}.{attr}", value, type="string")

    def _set_vector(self, node: str, attr: str, value: Any) -> None:
        if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence) or len(value) != 3:
            raise MayaSceneMetadataError(f"{node}.{attr} must be a vector3")
        if not self._has_attr(node, attr):
            raise MayaSceneMetadataError(f"{node}.{attr} is required for metadata write")
        self._call_adapter("set_attr", f"{node}.{attr}", *value, type="double3")

    def _set_existing_scalar(self, node: str, attr: str, value: Any) -> None:
        if self._has_attr(node, attr):
            self._call_adapter("set_attr", f"{node}.{attr}", value)

    def _set_existing_string(self, node: str, attr: str, value: str) -> None:
        if self._has_attr(node, attr):
            self._call_adapter("set_attr", f"{node}.{attr}", value, type="string")

    def _set_existing_vector(self, node: str, attr: str, value: Sequence[Any]) -> None:
        if self._has_attr(node, attr):
            self._call_adapter("set_attr", f"{node}.{attr}", *value, type="double3")

    def _set_optional_vector(self, node: str, attr: str, value: Sequence[Any]) -> None:
        if not self._has_attr(node, attr):
            self._call_adapter("add_attr", node, longName=attr, attributeType="double3")
            for suffix in ("X", "Y", "Z"):
                self._call_adapter(
                    "add_attr",
                    node,
                    longName=f"{attr}{suffix}",
                    attributeType="double",
                    parent=attr,
                )
        self._call_adapter("set_attr", f"{node}.{attr}", *value, type="double3")

    def _delete_existing_attr(self, node: str, attr: str) -> None:
        if self._has_attr(node, attr):
            self._call_adapter("delete_attr", f"{node}.{attr}")

    def _write_optional_bone_reference(
        self,
        node: str,
        index: int | None,
        numeric_attrs: tuple[str, ...],
        name_attr: str,
        target_by_index: Mapping[int, Mapping[str, Any]],
    ) -> None:
        if index is None:
            for attr in numeric_attrs:
                self._delete_existing_attr(node, attr)
            self._delete_existing_attr(node, name_attr)
            return
        if index == -1:
            for attr in numeric_attrs:
                if not self._has_attr(node, attr):
                    self._call_adapter("add_attr", node, longName=attr, attributeType="long")
                self._set_existing_scalar(node, attr, index)
            self._delete_existing_attr(node, name_attr)
            return
        target = target_by_index.get(index)
        if target is None:
            raise MayaSceneMetadataError(f"{node}: bone reference points to unknown index {index}")
        for attr in numeric_attrs:
            if not self._has_attr(node, attr):
                self._call_adapter("add_attr", node, longName=attr, attributeType="long")
            self._set_existing_scalar(node, attr, index)
        if not self._has_attr(node, name_attr):
            self._call_adapter("add_attr", node, longName=name_attr, dataType="string")
        self._set_existing_string(node, name_attr, target["name"])

    def _required_vector_with_alpha(self, node: str, color_attr: str, alpha_attr: str) -> tuple[float, ...]:
        return self._required_vector(node, color_attr) + (self._required_number(node, alpha_attr),)

    def _source_path(self, node: str, attr: str) -> str | None:
        if not self._has_attr(node, attr):
            return None
        value = self._required_string(node, attr)
        return value or None

    def _resolved_path(
        self,
        shader: str,
        source_attr: str,
        explicit_attr: str | None = None,
    ) -> str | None:
        """Resolve a texture path, preferring validated file provenance.

        A persisted resolved path is only a fallback for scenes that have no
        matching file-node provenance.  If both routes exist they must agree;
        otherwise the reader rejects the material instead of silently choosing
        one stale path.
        """
        source_path = self._source_path(shader, source_attr)
        explicit_path = self._optional_path(shader, explicit_attr)
        if not source_path:
            return explicit_path
        candidates: list[str] = []
        # Attribute-level connections identify the exact texture route.  Maya
        # ``listHistory`` is node-oriented (not plug-oriented), so query it
        # once on the shader rather than passing a ``node.attr`` path that can
        # be rejected by some Maya versions.
        candidates.extend(
            self._list_connections(
                f"{shader}.{source_attr}", source=True, destination=False, type="file"
            )
        )
        candidates.extend(self._call_adapter("list_history", shader) or [])
        file_nodes: list[str] = []
        for candidate in candidates:
            identity = self._material_identity(candidate)
            if identity in file_nodes:
                continue
            if self._node_type(identity) == "file":
                file_nodes.append(identity)
        matches: list[str] = []
        for file_node in file_nodes:
            if not self._has_attr(file_node, self._ORIGINAL_TEXTURE_PATH):
                continue
            original = self._required_string(file_node, self._ORIGINAL_TEXTURE_PATH)
            if original != source_path:
                continue
            if not self._has_attr(file_node, self._FILE_TEXTURE_NAME):
                raise MayaSceneMetadataError(f"{file_node}.{self._FILE_TEXTURE_NAME} is required for provenance")
            resolved = self._required_string(file_node, self._FILE_TEXTURE_NAME)
            matches.append(resolved)
        if len(matches) > 1:
            raise MayaSceneMetadataError(f"{shader}.{source_attr} has ambiguous file provenance")
        provenance = matches[0] if matches else None
        if provenance is not None and explicit_path is not None and provenance != explicit_path:
            raise MayaSceneMetadataError(
                f"{shader}.{source_attr} file provenance conflicts with {explicit_attr!r}"
            )
        return provenance if provenance is not None else explicit_path

    def _optional_path(self, node: str, attr: str | None) -> str | None:
        """Read an optional persisted resolved path with strict typing."""
        if attr is None or not self._has_attr(node, attr):
            return None
        value = self._required_string(node, attr)
        return value or None

    def _material_identity(self, node: Any) -> str:
        if not isinstance(node, str) or not node:
            raise MayaSceneMetadataError(f"material binding identity must be a non-empty string: {node!r}")
        if node.startswith("|"):
            return node
        try:
            long_names = self._cmds.ls(node, long=True) or []
        except Exception as exc:
            raise MayaSceneMetadataError(f"failed to canonicalize material node {node!r}: {exc}") from exc
        if len(long_names) == 1 and isinstance(long_names[0], str):
            return long_names[0]
        return node

    def _list_connections(self, query: Any, **kwargs: Any) -> list[str]:
        result = self._call_adapter("list_connections", query, **kwargs) or []
        if isinstance(result, (str, bytes, bytearray)):
            raise MayaSceneMetadataError(f"list_connections({query!r}) returned a scalar")
        return list(result)

    def _node_type(self, node: str) -> str:
        try:
            value = self._call_adapter("node_type", node)
        except MayaSceneMetadataError:
            return ""
        return value if isinstance(value, str) else ""

    def _call_adapter(self, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return getattr(self._cmds, method)(*args, **kwargs)
        except AttributeError as exc:
            raise MayaSceneMetadataError(f"injected adapter is missing {method}()") from exc
        except Exception as exc:
            raise MayaSceneMetadataError(f"adapter {method}() failed: {exc}") from exc

    def _read_bone(self, joint: str) -> dict[str, Any]:
        flags = self._required_int(joint, ATTR_MMD_BONE_FLAGS, minimum=0)
        data: dict[str, Any] = {
            "name": self._required_string(joint, ATTR_MMD_BONE_NAME),
            "name_english": self._required_string(joint, ATTR_MMD_BONE_NAME_EN),
            "index": self._required_int(joint, ATTR_MMD_BONE_INDEX, minimum=0),
            "parent_index": self._required_int(joint, ATTR_MMD_BONE_PARENT_INDEX, minimum=-1),
            "rest_position": self._required_vector(joint, ATTR_MMD_PMX_REST_POSITION),
            "transform_layer": self._required_int(joint, ATTR_MMD_DEFORM_LAYER, minimum=0),
            "flags": flags,
            "connect_bone_index": None,
            "tail_offset": None,
            "grant_parent_index": None,
            "grant_ratio": 0.0,
            "grant_local": bool(flags & PmxBoneFlag.LOCAL),
            "fixed_axis": None,
            "local_axis_x": None,
            "local_axis_z": None,
            "external_parent_key": None,
            "ik_target_index": None,
            "ik_loop_count": 0,
            "ik_limit_radian": None,
            "ik_links": [],
            "binding_identity": joint,
        }
        return data

    def _read_connect(self, joint: str, flags: int, data: dict[str, Any], references: Mapping[str, set[int]]) -> None:
        attrs = (ATTR_MMD_CONNECT_INDEX, ATTR_MMD_CONNECT_BONE_INDEX)
        if flags & PmxBoneFlag.CONNECT_BONE:
            data["connect_bone_index"] = self._resolve_reference(
                joint,
                attrs,
                ATTR_MMD_CONNECTION_BONE,
                references,
                minimum=-1,
            )
            # BonePresenter creates this editable field on every joint.  Its
            # exact UI default is inactive for index-connected bones, while a
            # different value is stale authored payload and must fail closed.
            self._reject_non_default(joint, ATTR_MMD_BONE_OFFSET, (0.0, -1.0, 0.0), "tail_offset")
        else:
            self._reject_present(joint, attrs + (ATTR_MMD_CONNECTION_BONE,), "connect_bone_index")
            data["tail_offset"] = self._required_vector(joint, ATTR_MMD_BONE_OFFSET)

    def _read_grant(self, joint: str, flags: int, data: dict[str, Any], references: Mapping[str, set[int]]) -> None:
        grant_flags = PmxBoneFlag.GRANT_PARENT_ROTATE | PmxBoneFlag.GRANT_PARENT_MOVE
        if flags & grant_flags:
            data["grant_parent_index"] = self._resolve_reference(
                joint, (ATTR_MMD_GRANT_PARENT_INDEX,), ATTR_MMD_GRANT_PARENT, references
            )
            data["grant_ratio"] = self._required_number(joint, ATTR_MMD_GRANT_RATE)
        else:
            self._reject_non_default(joint, ATTR_MMD_GRANT_PARENT_INDEX, None, "grant payload")
            self._reject_non_default(joint, ATTR_MMD_GRANT_PARENT, "", "grant payload")
            self._reject_non_default(joint, ATTR_MMD_GRANT_RATE, 1.0, "grant payload")

    def _read_axes(self, joint: str, flags: int, data: dict[str, Any]) -> None:
        fixed = (ATTR_MMD_FIXED_AXIS, ATTR_MMD_AXIS_DIRECTION)
        local_x = (ATTR_MMD_LOCAL_X_AXIS, ATTR_MMD_X_AXIS_DIRECTION)
        local_z = (ATTR_MMD_LOCAL_Z_AXIS, ATTR_MMD_Z_AXIS_DIRECTION)
        if flags & PmxBoneFlag.AXIS_FIXED:
            data["fixed_axis"] = self._agreed_vector_alias(joint, fixed)
        else:
            self._reject_non_default(joint, ATTR_MMD_FIXED_AXIS, (0.0, 0.0, 1.0), "fixed_axis")
            self._reject_present(joint, (ATTR_MMD_AXIS_DIRECTION,), "fixed_axis")
        if flags & PmxBoneFlag.LOCAL_AXIS:
            data["local_axis_x"] = self._agreed_vector_alias(joint, local_x)
            data["local_axis_z"] = self._agreed_vector_alias(joint, local_z)
        else:
            self._reject_non_default(joint, ATTR_MMD_LOCAL_X_AXIS, (1.0, 0.0, 0.0), "local_axis")
            self._reject_non_default(joint, ATTR_MMD_LOCAL_Z_AXIS, (0.0, 0.0, 1.0), "local_axis")
            self._reject_present(joint, (ATTR_MMD_X_AXIS_DIRECTION, ATTR_MMD_Z_AXIS_DIRECTION), "local_axis")

    def _read_external_parent(self, joint: str, flags: int, data: dict[str, Any]) -> None:
        if flags & PmxBoneFlag.EXTERNAL_PARENT_DEFORM:
            data["external_parent_key"] = self._required_int(joint, ATTR_MMD_EXTERNAL_PARENT_KEY)
        else:
            self._reject_non_default(joint, ATTR_MMD_EXTERNAL_PARENT_KEY, -1, "external_parent_key")

    def _read_ik(self, joint: str, flags: int, data: dict[str, Any], references: Mapping[str, set[int]]) -> None:
        if not flags & PmxBoneFlag.IK:
            self._reject_non_default(joint, ATTR_MMD_IK_TARGET_INDEX, None, "IK payload")
            self._reject_non_default(joint, ATTR_MMD_IK_TARGET, "", "IK payload")
            self._reject_non_default(joint, ATTR_MMD_IK_LOOP, 10, "IK payload")
            self._reject_non_default(joint, ATTR_MMD_IK_LIMIT_ANGLE, 2.0, "IK payload")
            self._reject_non_default(joint, ATTR_MMD_IK_LINKS, "[]", "IK payload")
            return
        data["ik_target_index"] = self._resolve_reference(
            joint, (ATTR_MMD_IK_TARGET_INDEX,), ATTR_MMD_IK_TARGET, references
        )
        data["ik_loop_count"] = self._required_int(joint, ATTR_MMD_IK_LOOP, minimum=0)
        data["ik_limit_radian"] = self._required_number(joint, ATTR_MMD_IK_LIMIT_ANGLE)
        raw_links = self._required(joint, ATTR_MMD_IK_LINKS)
        if isinstance(raw_links, str):
            try:
                raw_links = json.loads(raw_links)
            except (TypeError, ValueError) as exc:
                raise MayaSceneMetadataError(f"{joint}.{ATTR_MMD_IK_LINKS} must contain JSON list: {exc}") from exc
        if isinstance(raw_links, (str, bytes, bytearray)) or not isinstance(raw_links, Sequence):
            raise MayaSceneMetadataError(f"{joint}.{ATTR_MMD_IK_LINKS} must be a JSON/list payload")
        if not all(isinstance(link, Mapping) for link in raw_links):
            raise MayaSceneMetadataError(f"{joint}.{ATTR_MMD_IK_LINKS} entries must be mappings")
        data["ik_links"] = list(raw_links)

    @staticmethod
    def _build_references(metadata: Sequence[Mapping[str, Any]]) -> dict[str, set[int]]:
        references: dict[str, set[int]] = {}
        for item in metadata:
            index = item["index"]
            binding = item["binding_identity"]
            for alias in (binding, binding.rsplit("|", 1)[-1], item["name"], item["name_english"]):
                if alias:
                    references.setdefault(alias, set()).add(index)
        return references

    def _resolve_reference(
        self,
        joint: str,
        numeric_attrs: tuple[str, ...],
        name_attr: str,
        references: Mapping[str, set[int]],
        *,
        minimum: int = 0,
    ) -> int:
        numeric = self._agreed_int_alias(joint, numeric_attrs, minimum=minimum, required=False)
        name_value = None
        if self._has_attr(joint, name_attr):
            name_value = self._required_string(joint, name_attr)
            if not name_value:
                name_value = None
        named = None
        if name_value is not None:
            matches = references.get(name_value, set())
            if len(matches) != 1:
                problem = "unknown" if not matches else "ambiguous"
                raise MayaSceneMetadataError(f"{joint}.{name_attr} has {problem} bone alias {name_value!r}")
            named = next(iter(matches))
        if numeric is None and named is None:
            raise MayaSceneMetadataError(f"{joint}: missing required bone reference")
        if numeric is not None and named is not None and numeric != named:
            raise MayaSceneMetadataError(f"{joint}: conflicting numeric and name bone references")
        return numeric if numeric is not None else named  # type: ignore[return-value]

    def _agreed_int_alias(self, joint: str, attrs: tuple[str, ...], *, minimum: int, required: bool = True) -> int | None:
        values = [(attr, self._required_int(joint, attr, minimum=minimum)) for attr in attrs if self._has_attr(joint, attr)]
        if not values:
            if required:
                raise MayaSceneMetadataError(f"{joint}: missing required alias fields {attrs!r}")
            return None
        if len({value for _, value in values}) != 1:
            raise MayaSceneMetadataError(f"{joint}: conflicting alias fields {attrs!r}")
        return values[0][1]

    def _agreed_vector_alias(self, joint: str, attrs: tuple[str, ...]) -> tuple[float, float, float]:
        values = [(attr, self._required_vector(joint, attr)) for attr in attrs if self._has_attr(joint, attr)]
        if not values:
            raise MayaSceneMetadataError(f"{joint}: missing required alias fields {attrs!r}")
        if len({value for _, value in values}) != 1:
            raise MayaSceneMetadataError(f"{joint}: conflicting alias fields {attrs!r}")
        return values[0][1]

    def _required(self, node: str, attr: str) -> Any:
        if not self._has_attr(node, attr):
            raise MayaSceneMetadataError(f"{node}.{attr} is required")
        try:
            return self._cmds.get_attr(f"{node}.{attr}")
        except Exception as exc:
            raise MayaSceneMetadataError(f"failed to read {node}.{attr}: {exc}") from exc

    def _required_string(self, node: str, attr: str) -> str:
        value = self._required(node, attr)
        if not isinstance(value, str):
            raise MayaSceneMetadataError(f"{node}.{attr} must be an exact string")
        return value

    def _required_int(
        self,
        node: str,
        attr: str,
        *,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int:
        value = self._required(node, attr)
        if isinstance(value, bool) or not isinstance(value, int):
            raise MayaSceneMetadataError(f"{node}.{attr} must be an integer")
        if minimum is not None and value < minimum:
            raise MayaSceneMetadataError(f"{node}.{attr} must be >= {minimum}")
        if maximum is not None and value > maximum:
            raise MayaSceneMetadataError(f"{node}.{attr} must be <= {maximum}")
        return value

    def _required_number(self, node: str, attr: str) -> float:
        value = self._required(node, attr)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise MayaSceneMetadataError(f"{node}.{attr} must be a finite number")
        return float(value)

    def _required_vector(self, node: str, attr: str) -> tuple[float, float, float]:
        value = self._required(node, attr)
        if isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], (list, tuple)):
            value = value[0]
        if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence) or len(value) != 3:
            raise MayaSceneMetadataError(f"{node}.{attr} must be a vector3")
        numbers = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
                raise MayaSceneMetadataError(f"{node}.{attr} must contain finite numeric vector3 values")
            numbers.append(float(item))
        return tuple(numbers)  # type: ignore[return-value]

    def _has_attr(self, node: str, attr: str) -> bool:
        try:
            return bool(self._cmds.attribute_exists(attr, node))
        except Exception as exc:
            raise MayaSceneMetadataError(f"failed to inspect {node}.{attr}: {exc}") from exc

    def _reject_present(self, node: str, attrs: tuple[str, ...], field: str) -> None:
        present = [attr for attr in attrs if self._has_attr(node, attr)]
        if present:
            raise MayaSceneMetadataError(f"{node}: stale {field} fields present: {present!r}")

    def _reject_non_default(self, node: str, attr: str, default: Any, field: str) -> None:
        if not self._has_attr(node, attr):
            return
        value = self._required(node, attr)
        if default is None:
            matches = False
        elif isinstance(default, tuple):
            try:
                matches = self._required_vector(node, attr) == default
            except MayaSceneMetadataError:
                matches = False
        else:
            matches = value == default and type(value) is type(default)
        if not matches:
            raise MayaSceneMetadataError(f"{node}: stale {field} field {attr!r} has non-default payload")

    def _require_root(self, root: Any) -> None:
        if not isinstance(root, str) or not root.strip():
            raise MayaSceneMetadataError("root must be a non-empty string")
        try:
            exists = self._cmds.object_exists(root)
        except Exception as exc:
            raise MayaSceneMetadataError(f"failed to inspect root {root!r}: {exc}") from exc
        if not exists:
            raise MayaSceneMetadataError(f"model root does not exist: {root!r}")


__all__ = ["MayaSceneMetadataError", "MayaSceneMetadataBackend"]
