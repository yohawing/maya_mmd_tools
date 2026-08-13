"""Read strict normalized model, material, bone, and morph metadata through Maya.

This is deliberately a narrow, read-only Maya integration boundary.  Semantic
values come from persisted ``mmd_*`` attributes, except Vertex Morph offsets,
which are read from their exact controller-owned blendShape targets. Ordinary
Maya display plugs and evaluated morph results are never treated as PMX
authoring data.
"""

from __future__ import annotations

import json
import math
import re
import struct
from copy import deepcopy
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from mmd_tools.adapters.maya_material_shader_route import (
    MayaMaterialShaderRoute,
    material_diffuse_route,
)
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
    ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
    ATTR_MMD_IMPORT_SCALE,
    ATTR_MMD_SOURCE_VERTEX_INDICES,
    ATTR_MMD_MODEL_ROOT,
    ATTR_MMD_MODEL_REGISTRY,
    ATTR_MMD_REGISTRY_ROOT,
    ATTR_MMD_REGISTRY_SCHEMA,
    ATTR_MMD_DIFFUSE_COLOR,
    ATTR_MMD_AMBIENT_COLOR,
    ATTR_MMD_EDGE_COLOR,
    ATTR_MMD_EDGE_FLAG,
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
    ATTR_MMD_DISPLAY_FRAMES_JSON,
    ATTR_MMD_FLIP_MORPH_OFFSETS_JSON,
    ATTR_MMD_IMPULSE_MORPH_OFFSETS_JSON,
    ATTR_MMD_UV_MORPH_OFFSETS_JSON,
)
from mmd_tools.core.pmx_data.bone import PmxBoneFlag
from mmd_tools.core.model_authoring_spec import (
    MmdBoneSpec,
    MmdMaterialSpec,
    MmdModelAuthoringSpec,
    MmdMorphSpec,
)


class MayaSceneMetadataError(SceneMetadataError):
    """Raised when Maya metadata cannot be normalized without loss."""


class MayaSceneMetadataBackend:
    """Read model, material, PMX bone, and morph metadata from an adapter."""

    _MATERIAL_MORPH_OFFSETS_JSON = "mmd_material_morph_offsets_json"

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
    _BONE_REGISTER_ATTRS = (
        ATTR_MMD_BONE_NAME,
        ATTR_MMD_BONE_NAME_EN,
        ATTR_MMD_BONE_INDEX,
        ATTR_MMD_BONE_PARENT_INDEX,
        ATTR_MMD_PMX_REST_POSITION,
        ATTR_MMD_PMX_REST_POSITION + "X",
        ATTR_MMD_PMX_REST_POSITION + "Y",
        ATTR_MMD_PMX_REST_POSITION + "Z",
        ATTR_MMD_DEFORM_LAYER,
        ATTR_MMD_BONE_FLAGS,
        ATTR_MMD_BONE_OFFSET,
        ATTR_MMD_BONE_OFFSET + "X",
        ATTR_MMD_BONE_OFFSET + "Y",
        ATTR_MMD_BONE_OFFSET + "Z",
        ATTR_MMD_CONNECT_INDEX,
        ATTR_MMD_CONNECT_BONE_INDEX,
        ATTR_MMD_CONNECTION_BONE,
        ATTR_MMD_GRANT_PARENT_INDEX,
        ATTR_MMD_GRANT_PARENT,
        ATTR_MMD_GRANT_RATE,
        ATTR_MMD_FIXED_AXIS,
        ATTR_MMD_FIXED_AXIS + "X",
        ATTR_MMD_FIXED_AXIS + "Y",
        ATTR_MMD_FIXED_AXIS + "Z",
        ATTR_MMD_AXIS_DIRECTION,
        ATTR_MMD_LOCAL_X_AXIS,
        ATTR_MMD_LOCAL_X_AXIS + "X",
        ATTR_MMD_LOCAL_X_AXIS + "Y",
        ATTR_MMD_LOCAL_X_AXIS + "Z",
        ATTR_MMD_X_AXIS_DIRECTION,
        ATTR_MMD_LOCAL_Z_AXIS,
        ATTR_MMD_LOCAL_Z_AXIS + "X",
        ATTR_MMD_LOCAL_Z_AXIS + "Y",
        ATTR_MMD_LOCAL_Z_AXIS + "Z",
        ATTR_MMD_Z_AXIS_DIRECTION,
        ATTR_MMD_EXTERNAL_PARENT_KEY,
        ATTR_MMD_IK_TARGET_INDEX,
        ATTR_MMD_IK_TARGET,
        ATTR_MMD_IK_LOOP,
        ATTR_MMD_IK_LIMIT_ANGLE,
        ATTR_MMD_IK_LINKS,
    )

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

    def read_bone_value(
        self,
        model_root: str,
        binding: str,
        index: int | None = None,
    ) -> MmdBoneSpec:
        """Read one selected bone without enumerating model collections."""
        root = self._material_identity(model_root)
        joint = self._material_identity(binding)
        self._require_selected_bone(root, joint, index)
        data = self._read_bone(joint)
        flags = int(data["flags"])
        if flags & PmxBoneFlag.CONNECT_BONE:
            data["connect_bone_index"] = self._agreed_int_alias(
                joint,
                (ATTR_MMD_CONNECT_INDEX, ATTR_MMD_CONNECT_BONE_INDEX),
                minimum=-1,
                required=False,
            )
            data["tail_offset"] = (0.0, -1.0, 0.0)
        else:
            data["tail_offset"] = self._required_vector(joint, ATTR_MMD_BONE_OFFSET)
        grant_flags = PmxBoneFlag.GRANT_PARENT_ROTATE | PmxBoneFlag.GRANT_PARENT_MOVE
        if flags & grant_flags:
            data["grant_parent_index"] = self._agreed_int_alias(
                joint, (ATTR_MMD_GRANT_PARENT_INDEX,), minimum=0, required=False
            )
            data["grant_ratio"] = self._required_number(joint, ATTR_MMD_GRANT_RATE)
        if flags & PmxBoneFlag.AXIS_FIXED:
            data["fixed_axis"] = self._agreed_vector_alias(
                joint, (ATTR_MMD_FIXED_AXIS, ATTR_MMD_AXIS_DIRECTION)
            )
        if flags & PmxBoneFlag.LOCAL_AXIS:
            data["local_axis_x"] = self._agreed_vector_alias(
                joint, (ATTR_MMD_LOCAL_X_AXIS, ATTR_MMD_X_AXIS_DIRECTION)
            )
            data["local_axis_z"] = self._agreed_vector_alias(
                joint, (ATTR_MMD_LOCAL_Z_AXIS, ATTR_MMD_Z_AXIS_DIRECTION)
            )
        if flags & PmxBoneFlag.EXTERNAL_PARENT_DEFORM:
            data["external_parent_key"] = self._required_int(joint, ATTR_MMD_EXTERNAL_PARENT_KEY)
        if flags & PmxBoneFlag.IK:
            data["ik_target_index"] = self._agreed_int_alias(
                joint, (ATTR_MMD_IK_TARGET_INDEX,), minimum=0, required=False
            )
            data["ik_loop_count"] = self._required_int(joint, ATTR_MMD_IK_LOOP, minimum=0)
            data["ik_limit_radian"] = self._required_number(joint, ATTR_MMD_IK_LIMIT_ANGLE)
            raw_links = self._required(joint, ATTR_MMD_IK_LINKS)
            if isinstance(raw_links, str):
                try:
                    raw_links = json.loads(raw_links)
                except (TypeError, ValueError) as exc:
                    raise MayaSceneMetadataError(
                        f"{joint}.{ATTR_MMD_IK_LINKS} must contain JSON list: {exc}"
                    ) from exc
            if isinstance(raw_links, (str, bytes, bytearray)) or not isinstance(raw_links, Sequence):
                raise MayaSceneMetadataError(f"{joint}.{ATTR_MMD_IK_LINKS} must be a JSON/list payload")
            data["ik_links"] = list(raw_links)
        return MmdBoneSpec.from_mapping(data)

    def read_morph_value(
        self,
        model_root: str,
        binding: str,
        index: int | None = None,
    ) -> MmdMorphSpec:
        """Read one selected morph binding without enumerating other metadata."""
        root = self._material_identity(model_root)
        node = self._material_identity(binding)
        self._require_selected_morph(root, node, index)
        return MmdMorphSpec.from_mapping(self._read_morph(node, root=root))

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

    def begin_material_reindex(
        self,
        model_root: str,
        index: int,
        new_position: int,
    ) -> None:
        """Open a narrow adjacent-material transaction.

        This captures only registry ownership, the two material index
        attributes, and registered Material Morph JSON.  In particular it
        never constructs a full authoring spec.
        """
        if self._write_transaction is not None:
            raise MayaSceneMetadataError("a metadata write transaction is already active")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise MayaSceneMetadataError("material index must be a non-negative integer")
        if isinstance(new_position, bool) or not isinstance(new_position, int) or new_position < 0:
            raise MayaSceneMetadataError("material new position must be a non-negative integer")
        if abs(index - new_position) != 1:
            raise MayaSceneMetadataError("material reindex requires an adjacent swap")
        self._require_root(model_root)
        root = self._material_identity(model_root)
        first_index, second_index = sorted((index, new_position))
        original = self._capture_material_reindex_state(root, first_index, second_index)
        if not bool(self._call_adapter("undo_info", query=True, state=True)):
            raise MayaSceneMetadataError("Maya undo must be enabled for material reindex")
        self._call_adapter(
            "undo_info",
            openChunk=True,
            chunkName="MMD Material Reindex",
        )
        self._write_transaction = {
            "root": root,
            "kind": "material_reindex",
            "first_index": first_index,
            "second_index": second_index,
            "original_values": original,
            "chunk_open": True,
        }

    def read_material_value(
        self,
        model_root: str,
        binding: str,
        index: int | None = None,
    ) -> MmdMaterialSpec:
        """Read one selected material without enumerating other metadata."""
        root = self._material_identity(model_root)
        shader = self._material_identity(binding)
        members = self._registry_material_members(root)
        if members is None:
            raise MayaSceneMetadataError(
                f"selected material ownership cannot be proven for root {model_root!r}"
            )
        if shader not in members:
            raise MayaSceneMetadataError(
                f"material binding {binding!r} is not owned by root {model_root!r}"
            )
        if index is not None:
            observed_index = self._required_int(shader, ATTR_MMD_MATERIAL_INDEX, minimum=0)
            if observed_index != index:
                raise MayaSceneMetadataError(
                    f"material binding index mismatch: expected {index}, got {observed_index}"
                )
        try:
            return MmdMaterialSpec.from_mapping(self._read_material(shader))
        except Exception as exc:
            raise MayaSceneMetadataError(
                f"failed to read selected material value for {shader!r}: {exc}"
            ) from exc

    def read_material_value_by_index(
        self,
        model_root: str,
        index: int,
    ) -> MmdMaterialSpec:
        """Read exactly one registry-owned material selected by PMX index."""
        root = self._material_identity(model_root)
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise MayaSceneMetadataError("material index must be a non-negative integer")
        members = self._registry_material_members(root)
        if members is None:
            raise MayaSceneMetadataError(
                f"selected material ownership cannot be proven for root {model_root!r}"
            )
        matches = []
        for member in members:
            shader = self._material_identity(member)
            if self._required_int(shader, ATTR_MMD_MATERIAL_INDEX, minimum=0) == index:
                matches.append(shader)
        if len(matches) != 1:
            raise MayaSceneMetadataError(
                f"material index {index} must resolve to exactly one registry binding"
            )
        try:
            return MmdMaterialSpec.from_mapping(self._read_material(matches[0]))
        except Exception as exc:
            raise MayaSceneMetadataError(
                f"failed to read selected material value for index {index}: {exc}"
            ) from exc

    def next_material_index(self, model_root: str) -> int:
        """Return the next trailing material index from registry index attrs."""
        root = self._material_identity(model_root)
        members = self._registry_material_members(root)
        if members is None:
            raise MayaSceneMetadataError(
                f"material ownership cannot be proven for root {model_root!r}"
            )
        indices = [
            self._required_int(self._material_identity(member), ATTR_MMD_MATERIAL_INDEX, minimum=0)
            for member in members
        ]
        if len(indices) != len(set(indices)):
            raise MayaSceneMetadataError("material registry contains duplicate indices")
        return max(indices, default=-1) + 1

    def begin_material_create(self, model_root: str, index: int) -> None:
        """Open a selected-material-only create transaction."""
        if self._write_transaction is not None:
            raise MayaSceneMetadataError("a metadata write transaction is already active")
        root = self._material_identity(model_root)
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise MayaSceneMetadataError("material index must be a non-negative integer")
        members = self._registry_material_members(root)
        if members is None:
            raise MayaSceneMetadataError(
                f"material ownership cannot be proven for root {model_root!r}"
            )
        indices = [
            self._required_int(self._material_identity(member), ATTR_MMD_MATERIAL_INDEX, minimum=0)
            for member in members
        ]
        expected_index = max(indices, default=-1) + 1
        if index != expected_index:
            raise MayaSceneMetadataError(
                f"material create index is not trailing: expected {expected_index}, got {index}"
            )
        if not bool(self._call_adapter("undo_info", query=True, state=True)):
            raise MayaSceneMetadataError("Maya undo must be enabled for material creation")
        self._call_adapter(
            "undo_info", openChunk=True, chunkName="MMD Material Create"
        )
        self._write_transaction = {
            "root": root,
            "kind": "material_create",
            "index": index,
            "original_members": tuple(self._material_identity(member) for member in members),
            "chunk_open": True,
        }

    def begin_bone_register(self, model_root: str, bone: MmdBoneSpec) -> None:
        """Open a selected-joint-only registration transaction."""
        if self._write_transaction is not None:
            raise MayaSceneMetadataError("a metadata write transaction is already active")
        if not isinstance(bone, MmdBoneSpec):
            raise MayaSceneMetadataError("bone registration requires an MmdBoneSpec")
        root = self._material_identity(model_root)
        joint = self._material_identity(bone.binding_identity)
        registry_members = self._registry_morph_members(root)
        if registry_members is None:
            raise MayaSceneMetadataError(
                f"bone ownership cannot be proven for root {model_root!r}"
            )
        self._require_unregistered_selected_bone(root, joint)
        if bone.index < 0 or bone.parent_index < -1:
            raise MayaSceneMetadataError("bone registration indices are invalid")
        descendants = self._call_adapter(
            "list_relatives", root, allDescendents=True, fullPath=True, type="joint"
        ) or []
        indices = [
            self._required_int(self._material_identity(item), ATTR_MMD_BONE_INDEX, minimum=0)
            for item in descendants
            if self._has_attr(self._material_identity(item), ATTR_MMD_BONE_INDEX)
        ]
        if len(indices) != len(set(indices)):
            raise MayaSceneMetadataError("root contains duplicate bone indices")
        expected_index = max(indices, default=-1) + 1
        if bone.index != expected_index:
            raise MayaSceneMetadataError(
                f"bone registration index is not trailing: expected {expected_index}, got {bone.index}"
            )
        original_attrs = {
            attr: deepcopy(self._call_adapter("get_attr", f"{joint}.{attr}"))
            for attr in self._BONE_REGISTER_ATTRS
            if self._has_attr(joint, attr)
        }
        if original_attrs:
            raise MayaSceneMetadataError(
                f"selected bone has stale registration metadata: {joint!r}"
            )
        if not bool(self._call_adapter("undo_info", query=True, state=True)):
            raise MayaSceneMetadataError("Maya undo must be enabled for bone registration")
        self._call_adapter("undo_info", openChunk=True, chunkName="MMD Bone Register")
        self._write_transaction = {
            "root": root,
            "kind": "bone_register",
            "binding": joint,
            "index": bone.index,
            "registry_members": tuple(registry_members),
            "original_attrs": original_attrs,
            "chunk_open": True,
        }

    def commit_bone_register(self, model_root: str, bone: MmdBoneSpec) -> None:
        """Strictly verify selected-joint metadata and close its undo chunk."""
        transaction = self._active_transaction(model_root)
        if transaction.get("kind") != "bone_register":
            raise MayaSceneMetadataError("active transaction is not a bone registration")
        if not isinstance(bone, MmdBoneSpec):
            raise MayaSceneMetadataError("bone registration commit requires an MmdBoneSpec")
        joint = self._material_identity(bone.binding_identity)
        if joint != transaction["binding"] or bone.index != transaction["index"]:
            raise MayaSceneMetadataError("bone registration commit binding/index mismatch")
        current_registry = tuple(self._registry_morph_members(transaction["root"]) or ())
        if current_registry != tuple(transaction["registry_members"]):
            raise MayaSceneMetadataError("bone registration changed registry ownership")
        self._require_selected_bone(transaction["root"], joint, bone.index)
        try:
            actual = self.read_bone_value(transaction["root"], joint, bone.index)
        except Exception as exc:
            raise MayaSceneMetadataError(f"bone registration readback failed: {exc}") from exc
        if actual.to_mapping() != bone.to_mapping():
            raise MayaSceneMetadataError(
                f"bone registration readback mismatch: expected {bone.to_mapping()!r}, got {actual.to_mapping()!r}"
            )
        self._call_adapter("undo_info", closeChunk=True)
        self._write_transaction = None

    def commit_material_create(self, model_root: str, material: MmdMaterialSpec) -> None:
        """Strictly verify one new shader binding and close its undo chunk."""
        transaction = self._active_transaction(model_root)
        if transaction.get("kind") != "material_create":
            raise MayaSceneMetadataError("active transaction is not a material create")
        if not isinstance(material, MmdMaterialSpec):
            raise MayaSceneMetadataError("material create commit requires an MmdMaterialSpec")
        shader = self._material_identity(material.binding_identity)
        if material.index != transaction["index"]:
            raise MayaSceneMetadataError("material create commit index mismatch")
        members = self._registry_material_members(transaction["root"])
        if members is None:
            raise MayaSceneMetadataError("material create registry ownership disappeared")
        canonical_members = tuple(self._material_identity(member) for member in members)
        original = tuple(transaction["original_members"])
        if len(canonical_members) != len(set(canonical_members)) or set(canonical_members) != set(original) | {shader}:
            raise MayaSceneMetadataError("material create registry membership mismatch")
        if shader in original:
            raise MayaSceneMetadataError("material create reused an existing binding")
        shading_groups = self._list_connections(shader, type="shadingEngine")
        if len(shading_groups) != 1:
            raise MayaSceneMetadataError("material create shader must have exactly one shading group")
        actual = MmdMaterialSpec.from_mapping(self._read_material(shader))
        if actual.to_mapping() != material.to_mapping():
            raise MayaSceneMetadataError(
                f"material create readback mismatch: expected {material.to_mapping()!r}, got {actual.to_mapping()!r}"
            )
        self._call_adapter("undo_info", closeChunk=True)
        self._write_transaction = None

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

    def begin_material_value_patch(
        self,
        model_root: str,
        binding: str,
        old_material: MmdMaterialSpec,
        new_material: MmdMaterialSpec,
    ) -> None:
        """Open a selected-shader-only value patch transaction.

        Unlike ``begin_write``, this method never reads model, bone, morph, or
        other material metadata.  It captures only the patch-safe attribute
        preimage on the explicitly selected shader; commit and rollback use
        the same narrow readback for strict verification.
        """
        if self._write_transaction is not None:
            raise MayaSceneMetadataError("a metadata write transaction is already active")
        root = self._material_identity(model_root)
        if not isinstance(binding, str) or not binding.strip():
            raise MayaSceneMetadataError("material value patch binding must be a non-empty string")
        shader = self._material_identity(binding)
        if not isinstance(old_material, MmdMaterialSpec) or not isinstance(new_material, MmdMaterialSpec):
            raise MayaSceneMetadataError("material value patch requires material specs")
        if old_material.binding_identity != shader or new_material.binding_identity != shader:
            raise MayaSceneMetadataError("material value patch binding identity mismatch")
        if old_material.index != new_material.index:
            raise MayaSceneMetadataError("material value patch cannot change material index")
        if not bool(self._call_adapter("undo_info", query=True, state=True)):
            raise MayaSceneMetadataError("Maya undo must be enabled for material value patches")
        original = self._read_material_value_attrs(shader)
        expected_old = self._material_value_attrs(old_material)
        diffuse_route = material_diffuse_route(
            self._node_type(shader),
            has_main_texture=bool(old_material.resolved_texture_path or old_material.texture_path),
        )
        if diffuse_route is not None:
            original["viewport_diffuse"] = self._required_vector(
                shader, diffuse_route.diffuse_attribute
            )
            expected_old["viewport_diffuse"] = self._maya_float3(old_material.diffuse[:3])
        if original != expected_old:
            raise MayaSceneMetadataError(
                f"material value patch preimage mismatch for {shader!r}: "
                f"expected {expected_old!r}, got {original!r}"
            )
        self._call_adapter(
            "undo_info",
            openChunk=True,
            chunkName="MMD Material Value Patch",
        )
        self._write_transaction = {
            "root": root,
            "kind": "material_value",
            "binding": shader,
            "index": old_material.index,
            "original_values": original,
            "target_values": self._material_value_attrs(new_material),
            "diffuse_route": diffuse_route,
            "target": None,
            "chunk_open": True,
        }

    def begin_material_binding_patch(
        self,
        model_root: str,
        binding: str,
        old_material: MmdMaterialSpec,
        new_material: MmdMaterialSpec,
    ) -> None:
        """Open a full selected-shader patch without reading other materials."""
        if self._write_transaction is not None:
            raise MayaSceneMetadataError("a metadata write transaction is already active")
        root = self._material_identity(model_root)
        shader = self._material_identity(binding)
        if not isinstance(old_material, MmdMaterialSpec) or not isinstance(new_material, MmdMaterialSpec):
            raise MayaSceneMetadataError("material binding patch requires material specs")
        if old_material.binding_identity != shader or new_material.binding_identity != shader:
            raise MayaSceneMetadataError("material binding patch binding identity mismatch")
        if old_material.index != new_material.index:
            raise MayaSceneMetadataError("material binding patch cannot change material index")
        original = self.read_material_value(root, shader, old_material.index)
        if original != old_material:
            raise MayaSceneMetadataError(
                f"material binding patch preimage mismatch for {shader!r}"
            )
        if not bool(self._call_adapter("undo_info", query=True, state=True)):
            raise MayaSceneMetadataError("Maya undo must be enabled for material binding patches")
        self._call_adapter(
            "undo_info",
            openChunk=True,
            chunkName="MMD Material Binding Patch",
        )
        self._write_transaction = {
            "root": root,
            "kind": "material_binding",
            "binding": shader,
            "index": old_material.index,
            "original_material": old_material,
            "target_material": new_material,
            "chunk_open": True,
        }

    def begin_bone_value_patch(
        self,
        model_root: str,
        binding: str,
        old_bone: MmdBoneSpec,
        new_bone: MmdBoneSpec,
    ) -> None:
        """Open a selected-bone-only value patch transaction."""
        if self._write_transaction is not None:
            raise MayaSceneMetadataError("a metadata write transaction is already active")
        if not isinstance(old_bone, MmdBoneSpec) or not isinstance(new_bone, MmdBoneSpec):
            raise MayaSceneMetadataError("bone value patch requires bone specs")
        root = self._material_identity(model_root)
        joint = self._material_identity(binding)
        if old_bone.binding_identity != joint or new_bone.binding_identity != joint:
            raise MayaSceneMetadataError("bone value patch binding identity mismatch")
        if old_bone.index != new_bone.index:
            raise MayaSceneMetadataError("bone value patch cannot change bone index")
        self._require_selected_bone(root, joint, old_bone.index)
        if not bool(self._call_adapter("undo_info", query=True, state=True)):
            raise MayaSceneMetadataError("Maya undo must be enabled for bone value patches")
        original = self._read_bone_value_attrs(joint)
        expected_old = self._bone_value_attrs(old_bone)
        if original != expected_old:
            raise MayaSceneMetadataError(
                f"bone value patch preimage mismatch for {joint!r}: "
                f"expected {expected_old!r}, got {original!r}"
            )
        self._call_adapter(
            "undo_info",
            openChunk=True,
            chunkName="MMD Bone Value Patch",
        )
        self._write_transaction = {
            "root": root,
            "kind": "bone_value",
            "binding": joint,
            "index": old_bone.index,
            "original_values": original,
            "target_values": self._bone_value_attrs(new_bone),
            "chunk_open": True,
        }

    def begin_morph_value_patch(
        self,
        model_root: str,
        binding: str,
        old_morph: MmdMorphSpec,
        new_morph: MmdMorphSpec,
    ) -> None:
        """Open a selected-morph-only value patch transaction."""
        if self._write_transaction is not None:
            raise MayaSceneMetadataError("a metadata write transaction is already active")
        if not isinstance(old_morph, MmdMorphSpec) or not isinstance(new_morph, MmdMorphSpec):
            raise MayaSceneMetadataError("morph value patch requires morph specs")
        root = self._material_identity(model_root)
        node = self._material_identity(binding)
        if old_morph.binding_identity != node or new_morph.binding_identity != node:
            raise MayaSceneMetadataError("morph value patch binding identity mismatch")
        if old_morph.index != new_morph.index:
            raise MayaSceneMetadataError("morph value patch cannot change morph index")
        self._require_selected_morph(root, node, old_morph.index)
        if not bool(self._call_adapter("undo_info", query=True, state=True)):
            raise MayaSceneMetadataError("Maya undo must be enabled for morph value patches")
        original = self._morph_value_attrs(MmdMorphSpec.from_mapping(self._read_morph(node, root=root)))
        expected_old = self._morph_value_attrs(old_morph)
        if original != expected_old:
            raise MayaSceneMetadataError(
                f"morph value patch preimage mismatch for {node!r}: "
                f"expected {expected_old!r}, got {original!r}"
            )
        self._call_adapter(
            "undo_info",
            openChunk=True,
            chunkName="MMD Morph Value Patch",
        )
        self._write_transaction = {
            "root": root,
            "kind": "morph_value",
            "binding": node,
            "index": old_morph.index,
            "original_values": original,
            "target_values": self._morph_value_attrs(new_morph),
            "chunk_open": True,
        }

    def commit_morph_value_patch(
        self,
        model_root: str,
        binding: str,
        morph: MmdMorphSpec,
    ) -> None:
        """Strictly read back the selected morph and close its undo chunk."""
        transaction = self._active_transaction(model_root)
        if transaction.get("kind") != "morph_value":
            raise MayaSceneMetadataError("active transaction is not a morph value patch")
        node = self._material_identity(binding)
        if node != transaction["binding"] or not isinstance(morph, MmdMorphSpec):
            raise MayaSceneMetadataError("morph value patch commit binding mismatch")
        if morph.index != transaction["index"] or morph.binding_identity != node:
            raise MayaSceneMetadataError("morph value patch commit index/binding mismatch")
        self._require_selected_morph(transaction["root"], node, transaction["index"])
        actual = self._morph_value_attrs(MmdMorphSpec.from_mapping(self._read_morph(node, root=transaction["root"])))
        expected = dict(transaction["target_values"])
        if actual != expected:
            raise MayaSceneMetadataError(
                f"morph value patch fingerprint mismatch: expected {expected!r}, got {actual!r}"
            )
        self._call_adapter("undo_info", closeChunk=True)
        self._write_transaction = None

    @staticmethod
    def _morph_value_attrs(morph: MmdMorphSpec) -> dict[str, Any]:
        """Project a morph into the selected-binding transaction payload."""
        return {
            "name": morph.name,
            "name_english": morph.name_english,
            "index": morph.index,
            "panel": morph.panel,
            "morph_type": morph.morph_type,
            "offsets": morph.to_mapping()["offsets"],
            "runtime_capability": morph.runtime_capability,
            "loss_policy": morph.loss_policy,
        }

    def commit_bone_value_patch(
        self,
        model_root: str,
        binding: str,
        bone: MmdBoneSpec,
    ) -> None:
        """Strictly read back the selected bone and close its undo chunk."""
        transaction = self._active_transaction(model_root)
        if transaction.get("kind") != "bone_value":
            raise MayaSceneMetadataError("active transaction is not a bone value patch")
        joint = self._material_identity(binding)
        if joint != transaction["binding"] or not isinstance(bone, MmdBoneSpec):
            raise MayaSceneMetadataError("bone value patch commit binding mismatch")
        if bone.index != transaction["index"] or bone.binding_identity != joint:
            raise MayaSceneMetadataError("bone value patch commit index/binding mismatch")
        self._require_selected_bone(transaction["root"], joint, transaction["index"])
        actual = self._read_bone_value_attrs(joint)
        expected = dict(transaction["target_values"])
        if actual != expected:
            raise MayaSceneMetadataError(
                f"bone value patch fingerprint mismatch: expected {expected!r}, got {actual!r}"
            )
        self._call_adapter("undo_info", closeChunk=True)
        self._write_transaction = None

    @staticmethod
    def _bone_value_attrs(bone: MmdBoneSpec) -> dict[str, Any]:
        """Project a bone into the explicit narrow transaction fields."""
        return {
            "name": bone.name,
            "name_english": bone.name_english,
            "transform_layer": bone.transform_layer,
            "flags": bone.flags,
            "rest_position": tuple(bone.rest_position),
            "fixed_axis": None if bone.fixed_axis is None else tuple(bone.fixed_axis),
            "local_axis_x": None if bone.local_axis_x is None else tuple(bone.local_axis_x),
            "local_axis_z": None if bone.local_axis_z is None else tuple(bone.local_axis_z),
        }

    def _read_bone_value_attrs(self, joint: str) -> dict[str, Any]:
        """Read only patch-safe semantic attrs from one selected joint."""
        flags = self._required_int(joint, ATTR_MMD_BONE_FLAGS, minimum=0)

        def optional_axis(attrs: tuple[str, ...]) -> tuple[float, float, float] | None:
            present = [attr for attr in attrs if self._has_attr(joint, attr)]
            if not present:
                return None
            return self._agreed_vector_alias(joint, attrs)

        return {
            "name": self._required_string(joint, ATTR_MMD_BONE_NAME),
            "name_english": self._required_string(joint, ATTR_MMD_BONE_NAME_EN),
            "transform_layer": self._required_int(joint, ATTR_MMD_DEFORM_LAYER, minimum=0),
            "flags": flags,
            "rest_position": self._required_vector(joint, ATTR_MMD_PMX_REST_POSITION),
            "fixed_axis": (
                optional_axis((ATTR_MMD_FIXED_AXIS, ATTR_MMD_AXIS_DIRECTION))
                if flags & PmxBoneFlag.AXIS_FIXED
                else None
            ),
            "local_axis_x": (
                optional_axis((ATTR_MMD_LOCAL_X_AXIS, ATTR_MMD_X_AXIS_DIRECTION))
                if flags & PmxBoneFlag.LOCAL_AXIS
                else None
            ),
            "local_axis_z": (
                optional_axis((ATTR_MMD_LOCAL_Z_AXIS, ATTR_MMD_Z_AXIS_DIRECTION))
                if flags & PmxBoneFlag.LOCAL_AXIS
                else None
            ),
        }

    def commit_material_value_patch(
        self,
        model_root: str,
        binding: str,
        material: MmdMaterialSpec,
    ) -> None:
        """Strictly read back the selected shader and close its undo chunk."""
        transaction = self._active_transaction(model_root)
        if transaction.get("kind") != "material_value":
            raise MayaSceneMetadataError("active transaction is not a material value patch")
        shader = self._material_identity(binding)
        if shader != transaction["binding"] or material.binding_identity != shader:
            raise MayaSceneMetadataError("material value patch commit binding mismatch")
        actual = self._read_material_value_attrs(shader)
        expected = dict(transaction["target_values"])
        diffuse_route = transaction.get("diffuse_route")
        if isinstance(diffuse_route, MayaMaterialShaderRoute):
            actual["viewport_diffuse"] = self._required_vector(
                shader, diffuse_route.diffuse_attribute
            )
            expected["viewport_diffuse"] = self._maya_float3(material.diffuse[:3])
        if actual != expected:
            raise MayaSceneMetadataError(
                f"material value patch fingerprint mismatch: expected {expected!r}, got {actual!r}"
            )
        self._call_adapter("undo_info", closeChunk=True)
        self._write_transaction = None

    def commit_material_binding_patch(
        self,
        model_root: str,
        binding: str,
        material: MmdMaterialSpec,
    ) -> None:
        """Strictly read back one complete selected material and close its chunk."""
        transaction = self._active_transaction(model_root)
        if transaction.get("kind") != "material_binding":
            raise MayaSceneMetadataError("active transaction is not a material binding patch")
        shader = self._material_identity(binding)
        if shader != transaction["binding"] or material != transaction["target_material"]:
            raise MayaSceneMetadataError("material binding patch commit target mismatch")
        actual = self.read_material_value(model_root, shader, transaction["index"])
        if actual != material:
            raise MayaSceneMetadataError(
                f"material binding patch fingerprint mismatch: expected {material!r}, got {actual!r}"
            )
        self._call_adapter("undo_info", closeChunk=True)
        self._write_transaction = None

    @staticmethod
    def _material_value_attrs(material: MmdMaterialSpec) -> dict[str, Any]:
        """Project a material into the explicit narrow transaction fields."""
        return {
            "name": material.name,
            "name_english": material.name_english,
            "diffuse": tuple(material.diffuse),
            "specular": tuple(material.specular),
            "specular_coefficient": material.specular_coefficient,
            "ambient": tuple(material.ambient),
            "draw_flags": material.draw_flags,
            "edge_flag": bool(material.draw_flags & 0x10),
            "edge_color": tuple(material.edge_color),
            "edge_size": material.edge_size,
            "memo": material.memo,
        }

    @staticmethod
    def _maya_float3(values: Sequence[float]) -> tuple[float, float, float]:
        """Canonicalize Python doubles to Maya ``float3`` storage precision."""
        converted = tuple(
            struct.unpack("=f", struct.pack("=f", float(value)))[0]
            for value in values
        )
        if len(converted) != 3:
            raise MayaSceneMetadataError("Maya float3 values must contain exactly three numbers")
        return converted

    def _read_material_value_attrs(self, shader: str) -> dict[str, Any]:
        """Read only patch-safe semantic attrs from one shader binding."""
        draw_flags = self._required_int(shader, ATTR_MMD_DRAW_FLAGS, minimum=0)
        edge_flag = (
            bool(self._required(shader, ATTR_MMD_EDGE_FLAG))
            if self._has_attr(shader, ATTR_MMD_EDGE_FLAG)
            else bool(draw_flags & 0x10)
        )
        return {
            "name": self._required_string(shader, ATTR_MMD_MATERIAL_NAME),
            "name_english": self._required_string(shader, ATTR_MMD_MATERIAL_NAME_EN),
            "diffuse": self._required_vector_with_alpha(shader, ATTR_MMD_DIFFUSE_COLOR, self._DIFFUSE_ALPHA),
            "specular": self._required_vector(shader, ATTR_MMD_SPECULAR_COLOR),
            "specular_coefficient": self._required_number(shader, ATTR_MMD_SHININESS),
            "ambient": self._required_vector(shader, ATTR_MMD_AMBIENT_COLOR),
            "draw_flags": draw_flags,
            "edge_flag": edge_flag,
            "edge_color": self._required_vector_with_alpha(shader, ATTR_MMD_EDGE_COLOR, self._EDGE_ALPHA),
            "edge_size": self._required_number(shader, ATTR_MMD_EDGE_SIZE),
            "memo": self._required_string(shader, ATTR_MMD_MEMO),
        }

    def commit_material_reindex(
        self,
        model_root: str,
        result: Any,
    ) -> None:
        """Verify and close a narrow adjacent-material undo transaction.

        The narrow transaction verifies only the two index attributes and
        affected Material Morph JSON.  The material adapter has already
        written them in this chunk.
        """
        transaction = self._active_transaction(model_root)
        if transaction.get("kind") == "material_reindex":
            first_index, second_index = self._material_reindex_result_indices(result)
            if (first_index, second_index) != (
                transaction["first_index"],
                transaction["second_index"],
            ):
                raise MayaSceneMetadataError(
                    "material reindex commit indices do not match preimage"
                )
            try:
                actual = self._capture_material_reindex_state(
                    transaction["root"],
                    first_index,
                    second_index,
                    transaction["original_values"]["bindings"],
                )
                expected = self._expected_material_reindex_state(
                    transaction["original_values"], first_index, second_index
                )
            except Exception as exc:
                raise MayaSceneMetadataError(
                    f"failed to verify material reindex transaction: {exc}"
                ) from exc
            if actual != expected:
                raise MayaSceneMetadataError(
                    "material reindex transaction narrow-state mismatch"
                )
            self._call_adapter("undo_info", closeChunk=True)
            self._write_transaction = None
            return
        raise MayaSceneMetadataError("active transaction is not a material reindex")

    def _capture_material_reindex_state(
        self,
        root: str,
        first_index: int,
        second_index: int,
        target_bindings: Mapping[int, str] | None = None,
    ) -> dict[str, Any]:
        """Capture only state touched by an adjacent material swap."""
        members = self._registry_material_members(root)
        if members is None:
            raise MayaSceneMetadataError("material reindex requires a model registry")
        canonical_members = tuple(self._material_identity(member) for member in members)
        if len(set(canonical_members)) != len(canonical_members):
            raise MayaSceneMetadataError("material registry contains duplicate members")
        by_index: dict[int, str] = {}
        if target_bindings is None:
            for binding in canonical_members:
                observed = self._required_int(binding, ATTR_MMD_MATERIAL_INDEX, minimum=0)
                if observed in by_index and by_index[observed] != binding:
                    raise MayaSceneMetadataError(
                        f"duplicate material index {observed} in the model registry"
                    )
                by_index[observed] = binding
            if first_index not in by_index or second_index not in by_index:
                raise MayaSceneMetadataError("material reindex indices are not registry-owned")
            target_bindings = {
                first_index: by_index[first_index],
                second_index: by_index[second_index],
            }
        else:
            target_bindings = dict(target_bindings)
            if set(target_bindings) != {first_index, second_index}:
                raise MayaSceneMetadataError("material reindex target bindings are invalid")
            if any(binding not in canonical_members for binding in target_bindings.values()):
                raise MayaSceneMetadataError("material reindex target binding is not registry-owned")
        indices = {
            binding: self._required_int(binding, ATTR_MMD_MATERIAL_INDEX, minimum=0)
            for binding in target_bindings.values()
        }

        morphs: dict[str, Any] = {}
        morph_members = self._registry_morph_members(root) or []
        for member in morph_members:
            binding = self._material_identity(member)
            if self._required_string(binding, "mmd_morph_type") != "material":
                continue
            raw = self._required_string(binding, self._MATERIAL_MORPH_OFFSETS_JSON)
            morphs[binding] = self._parse_material_reindex_offsets(binding, raw)
        return {
            "members": canonical_members,
            "bindings": target_bindings,
            "indices": indices,
            "morphs": morphs,
        }

    def _parse_material_reindex_offsets(self, node: str, raw: str) -> list[dict[str, Any]]:
        try:
            value = json.loads(raw, object_pairs_hook=self._unique_json_object)
        except (TypeError, ValueError) as exc:
            raise MayaSceneMetadataError(
                f"{node}.{self._MATERIAL_MORPH_OFFSETS_JSON} must contain strict JSON"
            ) from exc
        if not isinstance(value, list):
            raise MayaSceneMetadataError(
                f"{node}.{self._MATERIAL_MORPH_OFFSETS_JSON} must contain a JSON list"
            )
        result: list[dict[str, Any]] = []
        for offset in value:
            if not isinstance(offset, Mapping):
                raise MayaSceneMetadataError(f"{node} material morph offset must be a mapping")
            item = dict(offset)
            material_index = item.get("material_index")
            if isinstance(material_index, bool) or not isinstance(material_index, int):
                raise MayaSceneMetadataError(
                    f"{node} material morph offset index must be an integer"
                )
            result.append(item)
        return result

    @staticmethod
    def _expected_material_reindex_state(
        original: Mapping[str, Any],
        first_index: int,
        second_index: int,
    ) -> dict[str, Any]:
        expected = deepcopy(original)
        swap = {first_index: second_index, second_index: first_index}
        expected["indices"] = {
            binding: swap.get(index, index)
            for binding, index in original["indices"].items()
        }
        for offsets in expected["morphs"].values():
            for offset in offsets:
                offset["material_index"] = swap.get(
                    offset["material_index"], offset["material_index"]
                )
        return expected

    @staticmethod
    def _material_reindex_result_indices(result: Any) -> tuple[int, int]:
        if result is None:
            raise MayaSceneMetadataError("material reindex commit result is missing")
        first = getattr(result, "first_index", None)
        second = getattr(result, "second_index", None)
        if first is None and second is None and isinstance(result, (tuple, list)) and len(result) == 2:
            first, second = result
        if (
            isinstance(first, bool)
            or not isinstance(first, int)
            or isinstance(second, bool)
            or not isinstance(second, int)
            or abs(second - first) != 1
        ):
            raise MayaSceneMetadataError("material reindex commit indices are invalid")
        return tuple(sorted((first, second)))

    def begin_morph_reindex(
        self,
        model_root: str,
        index: int,
        new_position: int,
    ) -> None:
        """Open a narrow adjacent-morph reindex transaction."""
        if self._write_transaction is not None:
            raise MayaSceneMetadataError("a metadata write transaction is already active")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise MayaSceneMetadataError("morph index must be a non-negative integer")
        if isinstance(new_position, bool) or not isinstance(new_position, int) or new_position < 0:
            raise MayaSceneMetadataError("new_position must be a non-negative integer")
        if abs(index - new_position) != 1:
            raise MayaSceneMetadataError("morph reindex requires an adjacent swap")
        self._require_root(model_root)
        root = self._material_identity(model_root)
        original = self._capture_morph_reindex_state(root)
        indices = {value["index"] for value in original["morphs"].values()}
        if len(indices) != len(original["morphs"]) or indices != set(range(len(indices))):
            raise MayaSceneMetadataError("morph indices must be a contiguous registry-owned range")
        if index not in indices or new_position not in indices:
            raise MayaSceneMetadataError("morph reindex selected indices are not registry-owned")
        if not bool(self._call_adapter("undo_info", query=True, state=True)):
            raise MayaSceneMetadataError("Maya undo must be enabled for morph reindex")
        self._call_adapter("undo_info", openChunk=True, chunkName="MMD Morph Reindex")
        self._write_transaction = {
            "root": root,
            "kind": "morph_reindex",
            "index": index,
            "new_position": new_position,
            "original_values": original,
            "chunk_open": True,
        }

    def begin_morph_create(self, model_root: str, morph: MmdMorphSpec) -> int:
        """Begin a narrow empty-offset morph creation transaction."""
        if self._write_transaction is not None:
            raise MayaSceneMetadataError("a metadata write transaction is already active")
        if not isinstance(morph, MmdMorphSpec):
            raise MayaSceneMetadataError("morph must be an MmdMorphSpec")
        if morph.binding_identity is not None or morph.offsets:
            raise MayaSceneMetadataError("morph creation requires an unbound empty-offset morph")
        self._require_root(model_root)
        root = self._material_identity(model_root)
        original = self._capture_morph_create_state(root)
        new_index = len(original["morphs"])
        if set(original["morphs"].values()) != set(range(new_index)):
            raise MayaSceneMetadataError("morph indices must be a contiguous registry-owned range")
        if not bool(self._call_adapter("undo_info", query=True, state=True)):
            raise MayaSceneMetadataError("Maya undo must be enabled for morph creation")
        self._call_adapter("undo_info", openChunk=True, chunkName="MMD Morph Create")
        self._write_transaction = {
            "root": root,
            "kind": "morph_create",
            "index": new_index,
            "original_values": original,
            "chunk_open": True,
        }
        return new_index

    def commit_morph_create(self, model_root: str, morph: MmdMorphSpec) -> None:
        """Verify and close a narrow morph creation transaction."""
        transaction = self._active_transaction(model_root)
        if transaction.get("kind") != "morph_create":
            raise MayaSceneMetadataError("active transaction is not a morph creation")
        if not isinstance(morph, MmdMorphSpec) or morph.binding_identity is None:
            raise MayaSceneMetadataError("morph creation result is invalid")
        if morph.index != transaction["index"] or morph.offsets:
            raise MayaSceneMetadataError("morph creation result does not match preimage")
        actual = self._capture_morph_create_state(transaction["root"])
        original = transaction["original_values"]
        binding = self._material_identity(morph.binding_identity)
        expected_members = tuple(original["members"]) + (binding,)
        if actual["members"] != expected_members:
            raise MayaSceneMetadataError("morph creation registry membership/order mismatch")
        if set(actual["morphs"]) != set(original["morphs"]) | {binding}:
            raise MayaSceneMetadataError("morph creation registry membership mismatch")
        for node, index in original["morphs"].items():
            if actual["morphs"].get(node) != index:
                raise MayaSceneMetadataError("existing morph binding changed during creation")
        if actual["morphs"].get(binding) != morph.index:
            raise MayaSceneMetadataError("created morph index readback mismatch")
        if self._required_string(binding, "mmd_morph_name") != morph.name:
            raise MayaSceneMetadataError("created morph name readback mismatch")
        if self._required_string(binding, "mmd_morph_name_en") != morph.name_english:
            raise MayaSceneMetadataError("created morph English name readback mismatch")
        if self._required_string(binding, "mmd_morph_type") != morph.morph_type:
            raise MayaSceneMetadataError("created morph type readback mismatch")
        if self._required_int(binding, "mmd_morph_panel") != morph.panel:
            raise MayaSceneMetadataError("created morph panel readback mismatch")
        if actual["controller"] != original["controller"]:
            if original["controller"] is not None:
                raise MayaSceneMetadataError("existing morph controller changed during creation")
        if original["controller"] is not None and original.get("topology") != actual.get("topology"):
            raise MayaSceneMetadataError("existing morph controller topology changed during creation")
        new_slot = actual["slots"].get(morph.index)
        if new_slot is None:
            raise MayaSceneMetadataError("created morph controller slot is missing")
        if morph.morph_type != "vertex" and f"{binding}.weight" not in new_slot["destinations"]:
            raise MayaSceneMetadataError("created morph controller output readback mismatch")
        if original["controller"] is not None:
            for index, slot in original["slots"].items():
                if actual["slots"].get(index) != slot:
                    raise MayaSceneMetadataError("existing morph controller slot changed during creation")
            for index, alias in original["aliases"].items():
                if actual["aliases"].get(index) != alias:
                    raise MayaSceneMetadataError("existing morph controller alias changed during creation")
        if actual["aliases"].get(morph.index) != f"morph_{morph.index}":
            raise MayaSceneMetadataError("created morph controller alias readback mismatch")
        self._call_adapter("undo_info", closeChunk=True)
        self._write_transaction = None

    def _capture_morph_create_state(self, root: str) -> dict[str, Any]:
        members = self._registry_morph_members(root)
        if members is None:
            raise MayaSceneMetadataError("morph creation requires a model registry")
        canonical_members = tuple(self._material_identity(member) for member in members)
        if len(set(canonical_members)) != len(canonical_members):
            raise MayaSceneMetadataError("morph registry contains duplicate binding identities")
        morphs: dict[str, int] = {}
        for binding in canonical_members:
            if self._node_type(binding) != "network":
                raise MayaSceneMetadataError(f"morph binding {binding!r} must be a network node")
            morphs[binding] = self._required_int(binding, "mmd_morph_index", minimum=0)
        controllers = self._list_connections(
            f"{root}.mmd_morph_controller", source=True, destination=False
        ) if self._has_attr(root, "mmd_morph_controller") else []
        if len(controllers) > 1:
            raise MayaSceneMetadataError("morph controller connection is ambiguous")
        controller = self._material_identity(controllers[0]) if controllers else None
        slots: dict[int, Any] = {}
        aliases: dict[int, str | None] = {}
        if controller is not None:
            for index in sorted(morphs.values()):
                input_plug = f"{controller}.inputWeight[{index}]"
                output_plug = f"{controller}.outputWeight[{index}]"
                incoming = tuple(self._list_connections(input_plug, source=True, destination=False, plugs=True))
                if len(incoming) > 1:
                    raise MayaSceneMetadataError(f"{input_plug} has ambiguous incoming connections")
                slots[index] = {
                    "source": incoming[0] if incoming else None,
                    "value": self._required_input_weight(controller, index),
                    "destinations": tuple(
                        self._list_connections(output_plug, source=False, destination=True, plugs=True)
                    ),
                }
            aliases = self._capture_morph_controller_aliases(controller, slots)
        topology = None
        if controller is not None and self._has_attr(controller, "groupTopology"):
            raw_topology = self._call_adapter("get_attr", f"{controller}.groupTopology")
            if raw_topology is not None and not isinstance(raw_topology, str):
                raise MayaSceneMetadataError(
                    f"{controller}.groupTopology must be an exact string or None"
                )
            topology = raw_topology
        return {
            "members": canonical_members,
            "morphs": morphs,
            "controller": controller,
            "slots": slots,
            "aliases": aliases,
            "topology": topology,
        }

    def commit_morph_reindex(
        self,
        model_root: str,
        result: Any,
    ) -> None:
        """Verify an adjacent morph swap and close its undo chunk."""
        transaction = self._active_transaction(model_root)
        if transaction.get("kind") != "morph_reindex":
            raise MayaSceneMetadataError("active transaction is not a morph reindex")
        if not hasattr(result, "swapped_indices"):
            raise MayaSceneMetadataError("morph reindex commit result is invalid")
        raw_swapped = result.swapped_indices
        if (
            not isinstance(raw_swapped, tuple)
            or len(raw_swapped) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) for value in raw_swapped)
        ):
            raise MayaSceneMetadataError("morph reindex commit indices are invalid")
        swapped = raw_swapped
        if swapped != (transaction["index"], transaction["new_position"]):
            raise MayaSceneMetadataError("morph reindex commit indices do not match preimage")
        actual = self._capture_morph_reindex_state(transaction["root"])
        expected = self._expected_morph_reindex_state(transaction["original_values"], swapped)
        if actual != expected:
            raise MayaSceneMetadataError("morph reindex fingerprint mismatch")
        self._call_adapter("undo_info", closeChunk=True)
        self._write_transaction = None

    def _capture_morph_reindex_state(self, root: str) -> dict[str, Any]:
        members = self._registry_morph_members(root)
        if members is None:
            raise MayaSceneMetadataError("morph reindex requires a model registry")
        canonical_members = tuple(sorted(self._material_identity(member) for member in members))
        morphs: dict[str, dict[str, Any]] = {}
        for binding in canonical_members:
            if self._node_type(binding) != "network":
                raise MayaSceneMetadataError(f"morph binding {binding!r} must be a network node")
            morph_type = self._required_string(binding, "mmd_morph_type")
            payload = None
            if morph_type in {"group", "flip"}:
                attr = {
                    "group": "mmd_group_morph_offsets_json",
                    "flip": ATTR_MMD_FLIP_MORPH_OFFSETS_JSON,
                }[morph_type]
                payload = self._required_string(binding, attr)
            morphs[binding] = {
                "index": self._required_int(binding, "mmd_morph_index", minimum=0),
                "morph_type": morph_type,
                "payload": payload,
            }
        controllers = self._list_connections(
            f"{root}.mmd_morph_controller", source=True, destination=False
        )
        if len(controllers) != 1:
            raise MayaSceneMetadataError("morph reindex requires one morph controller")
        controller = self._material_identity(controllers[0])
        slots: dict[int, Any] = {}
        for index in sorted(value["index"] for value in morphs.values()):
            input_plug = f"{controller}.inputWeight[{index}]"
            output_plug = f"{controller}.outputWeight[{index}]"
            sources = tuple(self._list_connections(input_plug, source=True, destination=False, plugs=True))
            if len(sources) > 1:
                raise MayaSceneMetadataError(f"{input_plug} has ambiguous sources")
            slots[index] = {
                "source": sources[0] if sources else None,
                "value": self._required_input_weight(controller, index),
                "destinations": tuple(self._list_connections(output_plug, source=False, destination=True, plugs=True)),
            }
        return {
            "members": canonical_members,
            "morphs": morphs,
            "controller": controller,
            "slots": slots,
            "topology": self._optional_string(controller, "groupTopology"),
            "display": self._optional_string(root, ATTR_MMD_DISPLAY_FRAMES_JSON),
            "aliases": self._capture_morph_controller_aliases(controller, slots),
            "runtime": self._capture_morph_runtime_state(morphs),
        }

    def _capture_morph_controller_aliases(
        self, controller: str, slots: Mapping[int, Mapping[str, Any]]
    ) -> dict[int, str | None]:
        """Capture aliases for the two controller inputs being reindexed.

        Alias state is part of the controller slot identity.  Missing aliases
        are represented as ``None``; duplicate aliases or malformed query
        payloads fail closed before any write occurs.
        """
        try:
            raw = self._call_adapter("alias_attr", controller, query=True) or ()
        except MayaSceneMetadataError:
            raise
        if isinstance(raw, (str, bytes, bytearray)) or len(raw) % 2:
            raise MayaSceneMetadataError("morph controller aliases must be alias/plug pairs")
        by_plug: dict[str, str] = {}
        by_alias: set[str] = set()
        for offset in range(0, len(raw), 2):
            alias, plug = raw[offset], raw[offset + 1]
            if not isinstance(alias, str) or not isinstance(plug, str):
                raise MayaSceneMetadataError("morph controller aliases must be strings")
            plug_text = plug.rsplit(".", 1)[-1]
            if not plug_text.startswith("inputWeight["):
                continue
            if plug_text in by_plug or alias in by_alias:
                raise MayaSceneMetadataError("morph controller input aliases are ambiguous")
            by_plug[plug_text] = alias
            by_alias.add(alias)
        return {
            index: by_plug.get(f"inputWeight[{index}]")
            for index in slots
        }

    def _capture_morph_runtime_state(
        self, morphs: Mapping[str, Mapping[str, Any]]
    ) -> tuple[dict[str, Any], ...]:
        """Read selected evaluator contributions through morph weight outputs."""
        captured: list[dict[str, Any]] = []
        evaluator_types = {"mmdBoneMorphAccum", "mmdMaterialMorphEval"}
        for binding, value in morphs.items():
            destinations = self._list_connections(
                f"{binding}.weight",
                source=False,
                destination=True,
                plugs=True,
            )
            for destination in destinations:
                if not isinstance(destination, str):
                    continue
                match = re.fullmatch(
                    r"(?P<node>.+)\.contribution\[(?P<slot>\d+)\]\.weight",
                    destination,
                )
                if match is None:
                    continue
                node = match.group("node")
                if self._node_type(node) not in evaluator_types:
                    continue
                slot = int(match.group("slot"))
                order = self._required_runtime_morph_order(node, slot)
                expected = value["index"]
                if order != expected:
                    raise MayaSceneMetadataError(
                        f"{destination!r} morphOrder mismatch: expected {expected}, got {order}"
                    )
                captured.append({"node": node, "slot": slot, "morph_order": order})
        return tuple(captured)

    def _optional_string(self, node: str, attr: str) -> str | None:
        if not self._has_attr(node, attr):
            return None
        return self._required_string(node, attr)

    def _required_input_weight(self, controller: str, index: int) -> float:
        """Read a multi attribute element without attributeQuery on the array."""
        try:
            value = self._call_adapter("get_attr", f"{controller}.inputWeight[{index}]")
        except MayaSceneMetadataError as exc:
            raise MayaSceneMetadataError(
                f"{controller}.inputWeight[{index}] is required"
            ) from exc
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise MayaSceneMetadataError(
                f"{controller}.inputWeight[{index}] must be a finite number"
            )
        return float(value)

    def _required_runtime_morph_order(self, node: str, slot: int) -> int:
        """Read a contribution array element directly from Maya."""
        try:
            value = self._call_adapter(
                "get_attr", f"{node}.contribution[{slot}].morphOrder"
            )
        except MayaSceneMetadataError as exc:
            raise MayaSceneMetadataError(
                f"{node}.contribution[{slot}].morphOrder is required"
            ) from exc
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise MayaSceneMetadataError(
                f"{node}.contribution[{slot}].morphOrder must be a non-negative integer"
            )
        return value

    @staticmethod
    def _expected_morph_reindex_state(original: Mapping[str, Any], swapped: tuple[int, int]) -> dict[str, Any]:
        first, second = swapped
        swap = {first: second, second: first}
        expected = deepcopy(original)
        for value in expected["morphs"].values():
            value["index"] = swap.get(value["index"], value["index"])
        slots = expected["slots"]
        expected["slots"] = {
            swap.get(index, index): value
            for index, value in slots.items()
        }
        aliases = expected.get("aliases")
        if isinstance(aliases, dict):
            expected["aliases"] = {
                swap.get(index, index): value
                for index, value in aliases.items()
            }
        if expected["topology"]:
            expected["topology"] = _swap_morph_json(
                expected["topology"], swap, "topology"
            )
        if expected["display"]:
            expected["display"] = _swap_morph_json(
                expected["display"], swap, "display"
            )
        for value in expected["morphs"].values():
            if value["payload"]:
                value["payload"] = _swap_morph_json(
                    value["payload"], swap, value["morph_type"]
                )
        for value in expected.get("runtime", ()):
            value["morph_order"] = swap.get(value["morph_order"], value["morph_order"])
        return expected

    def rollback_write(self, model_root: str) -> None:
        transaction = self._active_transaction(model_root)
        try:
            if transaction["chunk_open"]:
                self._call_adapter("undo_info", closeChunk=True)
                transaction["chunk_open"] = False
            self._call_adapter("undo")
        finally:
            self._write_transaction = None
        if transaction.get("kind") == "bone_value":
            self._require_selected_bone(
                transaction["root"], transaction["binding"], transaction["index"]
            )
            actual = self._read_bone_value_attrs(transaction["binding"])
            if actual != transaction["original_values"]:
                raise MayaSceneMetadataError("bone value patch rollback fingerprint mismatch")
            return
        if transaction.get("kind") == "bone_register":
            members = tuple(self._registry_morph_members(transaction["root"]) or ())
            if members != tuple(transaction["registry_members"]):
                raise MayaSceneMetadataError("bone registration rollback registry mismatch")
            self._require_unregistered_selected_bone(
                transaction["root"], transaction["binding"]
            )
            actual_attrs = {
                attr: deepcopy(self._call_adapter("get_attr", f"{transaction['binding']}.{attr}"))
                for attr in self._BONE_REGISTER_ATTRS
                if self._has_attr(transaction["binding"], attr)
            }
            if actual_attrs != transaction["original_attrs"]:
                raise MayaSceneMetadataError("bone registration rollback preimage mismatch")
            return
        if transaction.get("kind") == "material_value":
            actual = self._read_material_value_attrs(transaction["binding"])
            diffuse_route = transaction.get("diffuse_route")
            if isinstance(diffuse_route, MayaMaterialShaderRoute):
                actual["viewport_diffuse"] = self._required_vector(
                    transaction["binding"], diffuse_route.diffuse_attribute
                )
            if actual != transaction["original_values"]:
                raise MayaSceneMetadataError("material value patch rollback fingerprint mismatch")
            return
        if transaction.get("kind") == "material_binding":
            actual = self.read_material_value(
                transaction["root"], transaction["binding"], transaction["index"]
            )
            if actual != transaction["original_material"]:
                raise MayaSceneMetadataError("material binding patch rollback fingerprint mismatch")
            return
        if transaction.get("kind") == "material_create":
            members = self._registry_material_members(transaction["root"])
            if members is None:
                raise MayaSceneMetadataError("material create rollback registry ownership disappeared")
            actual = tuple(self._material_identity(member) for member in members)
            if actual != tuple(transaction["original_members"]):
                raise MayaSceneMetadataError("material create rollback registry mismatch")
            return
        if transaction.get("kind") == "material_reindex":
            actual = self._capture_material_reindex_state(
                transaction["root"],
                transaction["first_index"],
                transaction["second_index"],
                transaction["original_values"]["bindings"],
            )
            if actual != transaction["original_values"]:
                raise MayaSceneMetadataError("material reindex rollback narrow-state mismatch")
            return
        if transaction.get("kind") == "morph_value":
            self._require_selected_morph(
                transaction["root"], transaction["binding"], transaction["index"]
            )
            actual = self._morph_value_attrs(
                MmdMorphSpec.from_mapping(
                    self._read_morph(transaction["binding"], root=transaction["root"])
                )
            )
            if actual != transaction["original_values"]:
                raise MayaSceneMetadataError("morph value patch rollback fingerprint mismatch")
            return
        if transaction.get("kind") == "morph_reindex":
            actual = self._capture_morph_reindex_state(transaction["root"])
            if actual != transaction["original_values"]:
                raise MayaSceneMetadataError("morph reindex rollback fingerprint mismatch")
            return
        if transaction.get("kind") == "morph_create":
            actual = self._capture_morph_create_state(transaction["root"])
            if actual != transaction["original_values"]:
                raise MayaSceneMetadataError("morph creation rollback fingerprint mismatch")
            return
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
            metadata = self._read_morph(identity, root=root)
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

    def _read_morph(self, node: str, *, root: str | None = None) -> dict[str, Any]:
        morph_type = self._required_string(node, "mmd_morph_type")
        attr_by_type = {
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
        if morph_type == "vertex":
            if not isinstance(root, str) or not root:
                raise MayaSceneMetadataError(
                    f"{node} vertex morph requires an explicit model root for blendShape binding"
                )
            index = self._required_int(node, "mmd_morph_index", minimum=0)
            offsets = self._read_vertex_blendshape_offsets(root, node, index)
        else:
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

    def _read_vertex_blendshape_offsets(
        self,
        root: str,
        binding: str,
        morph_index: int,
    ) -> list[dict[str, Any]]:
        """Read sparse PMX deltas from the exact controller-owned blendShapes.

        Vertex network nodes intentionally do not carry a JSON offset copy.
        Their controller output destinations and the blendShape target data
        are the sole source of truth.  Any missing or ambiguous ownership or
        source-index mapping fails closed before a spec is published.
        """
        controllers = tuple(
            self._list_connections(
                f"{root}.mmd_morph_controller", source=True, destination=False
            )
        )
        if len(controllers) != 1:
            raise MayaSceneMetadataError(
                f"{root}.mmd_morph_controller must have exactly one controller for vertex morphs"
            )
        controller = self._material_identity(controllers[0])
        destinations = tuple(
            self._list_connections(
                f"{controller}.outputWeight[{morph_index}]",
                source=False,
                destination=True,
                plugs=True,
            )
        )
        if not destinations:
            raise MayaSceneMetadataError(
                f"vertex morph {binding!r} has no blendShape output binding"
            )

        scale = self._required_number(root, ATTR_MMD_IMPORT_SCALE)
        if scale <= 0.0:
            raise MayaSceneMetadataError(f"{root}.{ATTR_MMD_IMPORT_SCALE} must be positive")
        offsets: dict[int, tuple[float, float, float]] = {}
        target_seen = False
        for destination in destinations:
            destination = str(destination)
            if "." not in destination:
                raise MayaSceneMetadataError(
                    f"vertex morph {morph_index} output is not a blendShape plug: {destination!r}"
                )
            blend_shape, target_index = self._resolve_vertex_weight_destination(
                destination, morph_index
            )
            mapping = self._read_vertex_target_mapping(blend_shape)
            entry = mapping.get(str(target_index))
            if not isinstance(entry, Mapping) or entry.get("index") != morph_index:
                raise MayaSceneMetadataError(
                    f"blendShape {blend_shape!r} target {target_index} metadata does not match morph {morph_index}"
                )
            geometries = tuple(
                self._call_adapter("blend_shape", blend_shape, query=True, geometry=True) or ()
            )
            geometry_indices = tuple(
                self._call_adapter("blend_shape", blend_shape, query=True, geometryIndices=True) or ()
            )
            if len(geometries) != len(geometry_indices):
                raise MayaSceneMetadataError(
                    f"blendShape {blend_shape!r} geometry/index topology is ambiguous"
            )
            for geometry, geometry_index in zip(geometries, geometry_indices):
                geometry = self._material_identity(str(geometry))
                source_indices = self._read_vertex_source_indices(geometry)
                group = (
                    f"{blend_shape}.inputTarget[{int(geometry_index)}]."
                    f"inputTargetGroup[{target_index}]"
                )
                item_indices = self._call_adapter(
                    "get_attr", f"{group}.inputTargetItem", multiIndices=True
                ) or ()
                if 6000 not in {int(value) for value in item_indices}:
                    continue
                target_seen = True
                item = f"{group}.inputTargetItem[6000]"
                points = self._call_adapter("get_attr", f"{item}.inputPointsTarget") or ()
                components = self._call_adapter("get_attr", f"{item}.inputComponentsTarget") or ()
                qualified_components = [
                    str(component)
                    if ".vtx[" in str(component)
                    else f"{geometry}.{component}"
                    for component in components
                ]
                flattened_components = tuple(
                    self._call_adapter("ls", qualified_components, flatten=True) or ()
                ) if qualified_components else ()
                if len(points) != len(flattened_components):
                    raise MayaSceneMetadataError(
                        f"{item} points/components lengths differ"
                    )
                for point, component in zip(points, flattened_components):
                    component_match = re.search(r"(?:^|\.)vtx\[(\d+)\]$", str(component))
                    if component_match is None:
                        raise MayaSceneMetadataError(
                            f"{item} contains invalid component {component!r}"
                        )
                    local_index = int(component_match.group(1))
                    if not 0 <= local_index < len(source_indices):
                        raise MayaSceneMetadataError(
                            f"{item} component index {local_index} is out of range"
                        )
                    try:
                        delta = tuple(float(point[axis]) / scale for axis in range(3))
                    except (IndexError, TypeError, ValueError) as exc:
                        raise MayaSceneMetadataError(
                            f"{item} contains invalid point data {point!r}"
                        ) from exc
                    if not all(math.isfinite(value) for value in delta):
                        raise MayaSceneMetadataError(f"{item} contains non-finite point data")
                    pmx_delta = tuple(
                        0.0 if value == 0.0 else value
                        for value in (delta[0], delta[1], -delta[2])
                    )
                    source_index = source_indices[local_index]
                    if source_index in offsets:
                        raise MayaSceneMetadataError(
                            f"vertex morph {morph_index} maps source vertex {source_index} more than once"
                        )
                    offsets[source_index] = pmx_delta
        if not target_seen:
            raise MayaSceneMetadataError(
                f"vertex morph {morph_index} has no full-weight blendShape target"
            )
        return [
            {"vertex_index": index, "position_offset": list(offsets[index])}
            for index in sorted(offsets)
            if any(abs(value) > 1e-8 for value in offsets[index])
        ]

    def _resolve_vertex_weight_destination(
        self, destination: str, morph_index: int
    ) -> tuple[str, int]:
        """Resolve an explicit weight plug or one unique blendShape alias."""
        if "." not in destination:
            raise MayaSceneMetadataError(
                f"vertex morph {morph_index} has invalid output {destination!r}"
            )
        raw_node, plug_or_alias = destination.rsplit(".", 1)
        node = self._material_identity(raw_node)
        explicit = re.fullmatch(r"(?:weight|w)\[(\d+)\]", plug_or_alias)
        if explicit is not None:
            if self._node_type(node) != "blendShape":
                raise MayaSceneMetadataError(
                    f"vertex morph {morph_index} has non-blendShape output {destination!r}"
                )
            return node, int(explicit.group(1))
        if self._node_type(node) != "blendShape":
            raise MayaSceneMetadataError(
                f"vertex morph {morph_index} has non-blendShape output {destination!r}"
            )
        flat = list(self._call_adapter("alias_attr", node, query=True) or ())
        matches: list[int] = []
        for candidate_alias, plug in zip(flat[0::2], flat[1::2]):
            if str(candidate_alias) != plug_or_alias:
                continue
            plug_match = re.fullmatch(r"(?:weight|w)\[(\d+)\]", str(plug))
            if plug_match is not None:
                matches.append(int(plug_match.group(1)))
        if len(matches) != 1:
            raise MayaSceneMetadataError(
                f"vertex morph {morph_index} alias output is ambiguous: {destination!r}"
            )
        return node, matches[0]

    def _read_vertex_source_indices(self, geometry: str) -> list[int]:
        """Resolve local vertex order to PMX source indices.

        Imported meshes only persist ``mmd_source_vertex_indices`` when a
        split/compaction changed local order.  An untagged mesh therefore uses
        the identity mapping, matching ``maya_morph_authoring._source_vertex_map``.
        """
        owner = geometry
        if not self._has_attr(owner, ATTR_MMD_SOURCE_VERTEX_INDICES):
            parents = tuple(
                self._call_adapter("list_relatives", geometry, parent=True, fullPath=True) or ()
            )
            if len(parents) > 1:
                raise MayaSceneMetadataError(f"geometry {geometry!r} has ambiguous parents")
            if parents:
                owner = self._material_identity(str(parents[0]))

        vertex_count = self._call_adapter("poly_evaluate", geometry, vertex=True)
        if isinstance(vertex_count, bool) or not isinstance(vertex_count, int) or vertex_count < 0:
            raise MayaSceneMetadataError(f"geometry {geometry!r} returned an invalid vertex count")
        if not self._has_attr(owner, ATTR_MMD_SOURCE_VERTEX_INDICES):
            return list(range(vertex_count))

        raw = self._call_adapter("get_attr", f"{owner}.{ATTR_MMD_SOURCE_VERTEX_INDICES}")
        if isinstance(raw, tuple) and len(raw) == 1 and isinstance(raw[0], (list, tuple)):
            raw = raw[0]
        if isinstance(raw, (str, bytes, bytearray)) or not isinstance(raw, (list, tuple)):
            raise MayaSceneMetadataError(
                f"geometry {geometry!r} has invalid source vertex mapping"
            )
        if len(raw) != vertex_count:
            raise MayaSceneMetadataError(
                f"geometry {geometry!r} has invalid source vertex mapping"
            )
        source_indices: list[int] = []
        seen: set[int] = set()
        for source_index in raw:
            if isinstance(source_index, bool) or not isinstance(source_index, int) or source_index < 0:
                raise MayaSceneMetadataError(
                    f"geometry {geometry!r} has invalid source vertex index"
                )
            if source_index in seen:
                raise MayaSceneMetadataError(
                    f"geometry {geometry!r} maps source vertex {source_index} more than once"
                )
            seen.add(source_index)
            source_indices.append(source_index)
        return source_indices

    def _read_vertex_target_mapping(self, blend_shape: str) -> dict[str, Any]:
        if not self._has_attr(blend_shape, ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON):
            raise MayaSceneMetadataError(
                f"blendShape {blend_shape!r} is missing {ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}"
            )
        raw = self._required_string(blend_shape, ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON)
        try:
            value = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise MayaSceneMetadataError(
                f"{blend_shape}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON} must contain JSON"
            ) from exc
        if not isinstance(value, Mapping):
            raise MayaSceneMetadataError(
                f"{blend_shape}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON} must contain an object"
            )
        return dict(value)

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

    def _require_selected_bone(self, root: str, joint: str, index: int | None) -> int:
        """Validate selected-joint ownership using only root/path/index attrs."""
        if not self._call_adapter("object_exists", joint):
            raise MayaSceneMetadataError(f"selected bone does not exist: {joint!r}")
        if joint == root or not joint.startswith(root.rstrip("|") + "|"):
            raise MayaSceneMetadataError(f"selected bone {joint!r} is not owned by root {root!r}")
        observed = self._required_int(joint, ATTR_MMD_BONE_INDEX, minimum=0)
        if index is not None and observed != index:
            raise MayaSceneMetadataError(
                f"selected bone index mismatch: expected {index}, got {observed}"
            )
        return observed

    def _require_unregistered_selected_bone(self, root: str, joint: str) -> None:
        """Validate selected-joint ownership before adding bone metadata."""
        if not self._call_adapter("object_exists", joint):
            raise MayaSceneMetadataError(f"selected bone does not exist: {joint!r}")
        if joint == root or not joint.startswith(root.rstrip("|") + "|"):
            raise MayaSceneMetadataError(f"selected bone {joint!r} is not owned by root {root!r}")
        if self._has_attr(joint, ATTR_MMD_BONE_INDEX):
            raise MayaSceneMetadataError(f"selected bone is already registered: {joint!r}")

    def _require_selected_morph(self, root: str, node: str, index: int | None) -> int:
        """Validate selected morph ownership using only registry/index attrs."""
        if not self._call_adapter("object_exists", node):
            raise MayaSceneMetadataError(f"selected morph does not exist: {node!r}")
        if self._node_type(node) != "network":
            raise MayaSceneMetadataError(f"selected morph binding must be a network node: {node!r}")
        canonical = self._material_identity(node)
        if self._has_attr(root, ATTR_MMD_MODEL_REGISTRY):
            members = self._registry_morph_members(root) or []
            owned = {self._material_identity(member) for member in members}
            if canonical not in owned:
                raise MayaSceneMetadataError(f"selected morph {node!r} is not owned by root {root!r}")
        else:
            if not self._has_attr(node, ATTR_MMD_MODEL_ROOT):
                raise MayaSceneMetadataError(f"selected morph {node!r} has no explicit root ownership")
            roots = self._list_connections(
                f"{node}.{ATTR_MMD_MODEL_ROOT}", source=True, destination=False
            )
            if len(roots) != 1 or self._material_identity(roots[0]) != root:
                raise MayaSceneMetadataError(f"selected morph {node!r} is not owned by root {root!r}")
        observed = self._required_int(node, "mmd_morph_index", minimum=0)
        if index is not None and observed != index:
            raise MayaSceneMetadataError(
                f"selected morph index mismatch: expected {index}, got {observed}"
            )
        return observed


def _swap_morph_json(raw: str, swap: Mapping[int, int], kind: str) -> str:
    """Remap one known morph-reference JSON payload without generic guessing."""
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise MayaSceneMetadataError(f"{kind} metadata contains invalid JSON: {exc}") from exc
    if kind in {"group", "flip"}:
        if not isinstance(value, list):
            raise MayaSceneMetadataError(f"{kind} metadata must contain a JSON list")
        for offset in value:
            if not isinstance(offset, Mapping) or "morph_index" not in offset:
                raise MayaSceneMetadataError(f"{kind} offset must contain morph_index")
            index = offset["morph_index"]
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise MayaSceneMetadataError(f"{kind} morph_index must be a non-negative integer")
            offset["morph_index"] = swap.get(index, index)
    elif kind == "topology":
        if not isinstance(value, Mapping):
            raise MayaSceneMetadataError("groupTopology must contain a JSON object")
        remapped: dict[str, Any] = {}
        for target, sources in value.items():
            if isinstance(target, bool) or not isinstance(target, (str, int)):
                raise MayaSceneMetadataError("groupTopology target must be an integer key")
            try:
                target_index = int(target)
            except (TypeError, ValueError) as exc:
                raise MayaSceneMetadataError("groupTopology target must be an integer key") from exc
            if target_index < 0 or not isinstance(sources, list):
                raise MayaSceneMetadataError("groupTopology payload is malformed")
            output: list[list[Any]] = []
            for source in sources:
                if not isinstance(source, list) or len(source) != 2:
                    raise MayaSceneMetadataError("groupTopology source must be [index, rate]")
                source_index = source[0]
                if isinstance(source_index, bool) or not isinstance(source_index, int) or source_index < 0:
                    raise MayaSceneMetadataError("groupTopology source index must be a non-negative integer")
                output.append([swap.get(source_index, source_index), source[1]])
            remapped[str(swap.get(target_index, target_index))] = output
        value = remapped
    elif kind == "display":
        if not isinstance(value, list):
            raise MayaSceneMetadataError("display frame metadata must contain a JSON list")
        for frame in value:
            if not isinstance(frame, Mapping):
                raise MayaSceneMetadataError("display frame entry must be a mapping")
            elements = frame.get("elements", [])
            if not isinstance(elements, list):
                raise MayaSceneMetadataError("display frame elements must be a list")
            for element in elements:
                if not isinstance(element, Mapping):
                    raise MayaSceneMetadataError("display frame element must be a mapping")
                element_type = element.get("type")
                element_index = element.get("index")
                if (
                    isinstance(element_type, bool)
                    or not isinstance(element_type, int)
                    or element_type not in {0, 1}
                    or isinstance(element_index, bool)
                    or not isinstance(element_index, int)
                    or element_index < 0
                ):
                    raise MayaSceneMetadataError("display frame element type/index is malformed")
                if element_type == 1:
                    element["index"] = swap.get(element_index, element_index)
    else:
        raise MayaSceneMetadataError(f"unsupported morph JSON kind: {kind!r}")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


__all__ = ["MayaSceneMetadataError", "MayaSceneMetadataBackend"]
