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
import os
import re
import struct
from copy import deepcopy
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from mmd_tools.adapters.maya_material_shader_route import (
    MayaMaterialShaderRoute,
    material_diffuse_route,
)
from mmd_tools.adapters.maya_material_metadata_repository import (
    MayaMaterialMetadataRepository,
)
from mmd_tools.adapters.maya_bone_metadata_repository import (
    MayaBoneMetadataRepository,
)
from mmd_tools.adapters.maya_authoring_metadata_writers import (
    BoneMetadataWriter,
    MaterialMetadataWriter,
    MetadataWriterContext,
    ModelMetadataWriter,
    MorphMetadataWriter,
)
from mmd_tools.adapters.maya_full_metadata_transaction import (
    MayaFullMetadataTransaction,
    MayaFullMetadataTransactionContext,
)
from mmd_tools.adapters.maya_metadata_read_support import MayaMetadataReadSupport
from mmd_tools.adapters.maya_model_metadata_repository import (
    MayaModelMetadataRepository,
)
from mmd_tools.adapters.maya_morph_metadata_repository import (
    MayaMorphMetadataRepository,
)
from mmd_tools.adapters.native_authoring_command import (
    NativeAuthoringCommandError,
    NativeAuthoringCommandGateway,
    NativeCommandProtocolError,
    NativeCommandUnavailable,
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
    ATTR_MMD_DIFFUSE_COLOR,
    ATTR_MMD_AMBIENT_COLOR,
    ATTR_MMD_EDGE_COLOR,
    ATTR_MMD_EDGE_FLAG,
    ATTR_MMD_EDGE_SIZE,
    ATTR_MMD_DRAW_FLAGS,
    ATTR_MMD_MEMO,
    ATTR_MMD_MATERIAL_INDEX,
    ATTR_MMD_MATERIAL_NAME,
    ATTR_MMD_MATERIAL_NAME_EN,
    ATTR_MMD_SHININESS,
    ATTR_MMD_SPECULAR_COLOR,
    ATTR_MMD_DISPLAY_FRAMES_JSON,
    ATTR_MMD_FLIP_MORPH_OFFSETS_JSON,
)
from mmd_tools.core.logger import get_logger
from mmd_tools.core.material_read_projection import (
    MaterialAssignmentSummary,
    MaterialDetailProjection,
    MaterialListProjection,
)
from mmd_tools.core.morph_read_projection import MorphAuthoringReadSnapshot
from mmd_tools.core.pmx_data.bone import PmxBoneFlag
from mmd_tools.core.model_authoring_spec import (
    MmdBoneSpec,
    MmdMaterialSpec,
    MmdModelAuthoringSpec,
    MmdMorphSpec,
)
from mmd_tools.core.morph_topology import (
    TOPOLOGY_VERSION,
    MorphTopologyInspection,
    serialize_group_topology,
)


logger = get_logger(__name__)

_MATERIAL_OUTLINE_ATTRS = (
    "technique",
    "EdgeSize",
    "mmd_shader_outline_enabled",
    "mmdDoubleSided",
    "mmdTransparencyMode",
)

# Keep the narrow transaction surface explicit.  Aggregate Model/Bone/Material/
# Morph hooks are valid only for ``full_metadata``; every other kind must fail
# closed before it can perform a write.  The unit matrix intentionally checks
# this inventory against the kind literals below.
NARROW_TRANSACTION_KINDS = (
    "info_metadata",
    "morph_topology_repair",
    "display_frames",
    "material_reindex",
    "material_create",
    "bone_register",
    "material_value",
    "material_binding",
    "bone_value",
    "morph_value",
    "morph_preview",
    "morph_reindex",
    "morph_create",
)


class MayaSceneMetadataError(SceneMetadataError):
    """Raised when Maya metadata cannot be normalized without loss."""


@dataclass(frozen=True)
class MorphPreviewSession:
    """Opaque event-spanning preview identity with a fixed write-set."""

    root: str
    targets: tuple[str, ...]
    token: object


@dataclass(frozen=True)
class InfoMetadataSession:
    """Opaque Info-field edit identity fixed at focus-in."""

    root: str
    attr: str
    token: object


class MayaSceneMetadataBackend:
    """Read model, material, PMX bone, and morph metadata from an adapter."""

    _MATERIAL_MORPH_OFFSETS_JSON = "mmd_material_morph_offsets_json"
    _INFO_STRING_ATTRS = frozenset(
        (
            ATTR_MMD_MODEL_NAME,
            ATTR_MMD_MODEL_NAME_EN,
            ATTR_MMD_COMMENT,
            ATTR_MMD_COMMENT_EN,
        )
    )

    def begin_info_metadata_edit(
        self, model_root: str, attr: str
    ) -> InfoMetadataSession:
        """Capture one Info string and open its focus-spanning undo chunk."""
        if self._write_transaction is not None:
            raise MayaSceneMetadataError("a metadata write transaction is already active")
        if attr not in self._INFO_STRING_ATTRS:
            raise MayaSceneMetadataError(f"unsupported Info metadata attribute: {attr!r}")
        names = self._call_adapter("ls", model_root, long=True) or ()
        if isinstance(names, (str, bytes, bytearray)) or len(names) != 1:
            raise MayaSceneMetadataError(
                f"Info model root has no unique canonical identity: {model_root!r}"
            )
        root = names[0]
        self._require_root(root)
        if not self._has_attr(root, attr):
            raise MayaSceneMetadataError(f"missing Info metadata attribute: {root}.{attr}")
        if bool(self._call_adapter("get_attr", f"{root}.{attr}", lock=True)):
            raise MayaSceneMetadataError(f"locked Info metadata attribute: {root}.{attr}")
        if not bool(self._call_adapter("undo_info", query=True, state=True)):
            raise MayaSceneMetadataError("Maya undo must be enabled for Info metadata edits")
        original = self._info_string_value(
            self._call_adapter("get_attr", f"{root}.{attr}"), root, attr
        )
        token = object()
        self._call_adapter("undo_info", openChunk=True, chunkName="MMD Info Edit")
        self._write_transaction = {
            "root": root,
            "kind": "info_metadata",
            "attr": attr,
            "token": token,
            "original_value": original,
            "target_value": original,
            "chunk_open": True,
            "mutated": False,
        }
        return InfoMetadataSession(root=root, attr=attr, token=token)

    def apply_info_metadata_edit(
        self, model_root: str, session: InfoMetadataSession, value: str
    ) -> bool:
        """Write and exactly read back one fixed Info string target."""
        transaction = self._active_info_metadata_edit(model_root, session)
        expected = self._info_string_value(value, session.root, session.attr)
        try:
            self._call_adapter(
                "set_attr", f"{session.root}.{session.attr}", expected, type="string"
            )
        except Exception:
            # A rejected first setAttr leaves an empty chunk.  Probe only the
            # fixed plug so rollback never undoes an unrelated prior action.
            actual = self._info_string_value(
                self._call_adapter("get_attr", f"{session.root}.{session.attr}"),
                session.root,
                session.attr,
            )
            transaction["mutated"] = bool(transaction["mutated"]) or (
                actual != transaction["original_value"]
            )
            raise
        transaction["mutated"] = True
        actual = self._info_string_value(
            self._call_adapter("get_attr", f"{session.root}.{session.attr}"),
            session.root,
            session.attr,
        )
        if actual != expected:
            raise MayaSceneMetadataError(
                f"Info metadata readback mismatch for {session.root}.{session.attr}"
            )
        transaction["target_value"] = expected
        return expected != transaction["original_value"]

    def commit_info_metadata_edit(
        self, model_root: str, session: InfoMetadataSession
    ) -> bool:
        """Verify the final string and close the session's undo chunk."""
        transaction = self._active_info_metadata_edit(model_root, session)
        actual = self._info_string_value(
            self._call_adapter("get_attr", f"{session.root}.{session.attr}"),
            session.root,
            session.attr,
        )
        if actual != transaction["target_value"]:
            raise MayaSceneMetadataError(
                f"Info metadata commit readback mismatch for {session.root}.{session.attr}"
            )
        self._call_adapter("undo_info", closeChunk=True)
        transaction["chunk_open"] = False
        self._write_transaction = None
        return transaction["target_value"] != transaction["original_value"]

    def rollback_info_metadata_edit(
        self, model_root: str, session: InfoMetadataSession
    ) -> None:
        """Close then undo one mutated edit and verify its exact preimage."""
        transaction = self._active_info_metadata_edit(model_root, session)
        if transaction["chunk_open"]:
            self._call_adapter("undo_info", closeChunk=True)
            transaction["chunk_open"] = False
        if transaction["mutated"]:
            self._call_adapter("undo")
            transaction["mutated"] = False
        self._write_transaction = None
        actual = self._info_string_value(
            self._call_adapter("get_attr", f"{session.root}.{session.attr}"),
            session.root,
            session.attr,
        )
        if actual != transaction["original_value"]:
            error = MayaSceneMetadataError(
                f"Info metadata rollback preimage mismatch for {session.root}.{session.attr}"
            )
            error.rollback_pending = False
            raise error

    def _active_info_metadata_edit(
        self, model_root: str, session: InfoMetadataSession
    ) -> dict[str, Any]:
        if not isinstance(session, InfoMetadataSession):
            raise MayaSceneMetadataError("invalid Info metadata session")
        transaction = self._active_transaction(model_root)
        if (
            transaction.get("kind") != "info_metadata"
            or transaction.get("token") is not session.token
            or transaction.get("root") != session.root
            or transaction.get("attr") != session.attr
        ):
            raise MayaSceneMetadataError("Info metadata session identity mismatch")
        return transaction

    @staticmethod
    def _info_string_value(value: Any, root: str, attr: str) -> str:
        if not isinstance(value, str):
            raise MayaSceneMetadataError(
                f"Info metadata must be a string for {root}.{attr}"
            )
        return value

    def inspect_morph_topology(self, model_root: str) -> MorphTopologyInspection:
        """Inspect derived controller topology without changing the scene."""
        return self._morph_repository.inspect_morph_topology(model_root)

    def begin_morph_topology_repair(self, model_root: str, expected_source: str) -> None:
        """Open an explicit derived-topology-only repair transaction."""
        if self._write_transaction is not None:
            raise MayaSceneMetadataError("a metadata write transaction is already active")
        inspection = self.inspect_morph_topology(model_root)
        if not inspection.repairable:
            raise MayaSceneMetadataError("morph topology is not repairable")
        canonical = serialize_group_topology(inspection.expected)
        if expected_source != canonical:
            raise MayaSceneMetadataError("morph topology repair target is stale")
        root = self._material_identity(model_root)
        controllers = self._list_connections(
            f"{root}.mmd_morph_controller", source=True, destination=False
        )
        controller = self._material_identity(controllers[0])
        if not bool(self._call_adapter("undo_info", query=True, state=True)):
            raise MayaSceneMetadataError("Maya undo must be enabled for morph topology repair")
        original = {
            "version": self._call_adapter("get_attr", f"{controller}.topologyVersion"),
            "source": self._call_adapter("get_attr", f"{controller}.groupTopology"),
            "version_locked": bool(
                self._call_adapter(
                    "get_attr", f"{controller}.topologyVersion", lock=True
                )
            ),
            "source_locked": bool(
                self._call_adapter(
                    "get_attr", f"{controller}.groupTopology", lock=True
                )
            ),
        }
        self._call_adapter("undo_info", openChunk=True, chunkName="MMD Morph Topology Repair")
        self._write_transaction = {
            "root": root,
            "kind": "morph_topology_repair",
            "controller": controller,
            "original_values": original,
            "expected_source": canonical,
            "chunk_open": True,
            "mutated": False,
        }

    def apply_morph_topology_repair(self, model_root: str, expected_source: str) -> str:
        """Write only the two derived controller cache attributes."""
        transaction = self._active_transaction(model_root)
        if transaction.get("kind") != "morph_topology_repair":
            raise MayaSceneMetadataError("active transaction is not a morph topology repair")
        if expected_source != transaction["expected_source"]:
            raise MayaSceneMetadataError("morph topology repair target changed")
        controller = transaction["controller"]
        self._call_adapter("set_attr", f"{controller}.topologyVersion", lock=False)
        transaction["mutated"] = True
        self._call_adapter("set_attr", f"{controller}.topologyVersion", TOPOLOGY_VERSION, lock=True)
        self._call_adapter("set_attr", f"{controller}.groupTopology", lock=False)
        self._call_adapter(
            "set_attr", f"{controller}.groupTopology", expected_source, type="string", lock=True
        )
        return expected_source

    def commit_morph_topology_repair(self, model_root: str, result: str) -> None:
        """Exact-readback and close an explicit topology repair."""
        transaction = self._active_transaction(model_root)
        if transaction.get("kind") != "morph_topology_repair":
            raise MayaSceneMetadataError("active transaction is not a morph topology repair")
        controller = transaction["controller"]
        actual = {
            "version": self._call_adapter("get_attr", f"{controller}.topologyVersion"),
            "source": self._call_adapter("get_attr", f"{controller}.groupTopology"),
            "version_locked": bool(
                self._call_adapter(
                    "get_attr", f"{controller}.topologyVersion", lock=True
                )
            ),
            "source_locked": bool(
                self._call_adapter(
                    "get_attr", f"{controller}.groupTopology", lock=True
                )
            ),
        }
        expected = {
            "version": TOPOLOGY_VERSION,
            "source": transaction["expected_source"],
            "version_locked": True,
            "source_locked": True,
        }
        if result != transaction["expected_source"] or actual != expected:
            raise MayaSceneMetadataError("morph topology repair readback mismatch")
        inspection = self.inspect_morph_topology(model_root)
        if not inspection.valid:
            raise MayaSceneMetadataError("morph topology remains invalid after repair")
        self._call_adapter("undo_info", closeChunk=True)
        self._write_transaction = None

    _DIFFUSE_ALPHA = "mmd_diffuse_alpha"
    _EDGE_ALPHA = "mmd_edge_alpha"
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
        self._read_support = MayaMetadataReadSupport(
            cmds_adapter,
            error_factory=MayaSceneMetadataError,
        )
        self._model_repository = MayaModelMetadataRepository(self._read_support)
        self._bone_repository = MayaBoneMetadataRepository(
            self._read_support,
            error_factory=MayaSceneMetadataError,
        )
        self._material_repository = MayaMaterialMetadataRepository(
            self._read_support,
            cmds_adapter=cmds_adapter,
            error_factory=MayaSceneMetadataError,
        )
        self._morph_repository = MayaMorphMetadataRepository(
            self._read_support,
            cmds_adapter=cmds_adapter,
            error_factory=MayaSceneMetadataError,
        )
        self._native_authoring = NativeAuthoringCommandGateway(cmds_adapter)
        # Native writes remain opt-in until both supported Maya versions show
        # lower p50 and p95 latency than the direct Python path.
        self._use_native_morph_weights = (
            os.environ.get("MMD_AUTHORING_MORPH_WEIGHT_MODE", "python").strip().lower()
            == "native"
        )
        self._write_transaction: dict[str, Any] | None = None
        self._full_metadata_transaction = MayaFullMetadataTransaction(
            MayaFullMetadataTransactionContext(
                error_factory=MayaSceneMetadataError,
                require_root=self._require_root,
                canonical_identity=self._material_identity,
                read_spec=lambda root: SceneMetadataAdapter(self).read_spec(root),
                call_adapter=self._call_adapter,
                get_active_transaction=self._get_write_transaction,
                set_active_transaction=self._set_write_transaction,
                active_transaction=self._active_transaction,
            )
        )
        writer_context = MetadataWriterContext(
            error_factory=MayaSceneMetadataError,
            require_exact_mapping=self._require_exact_mapping,
            write_items=self._write_items,
            require_same_bindings=self._require_same_bindings,
            set_scalar=self._set_scalar,
            set_string=self._set_string,
            set_vector=self._set_vector,
            set_existing_scalar=self._set_existing_scalar,
            set_existing_string=self._set_existing_string,
            set_optional_scalar=self._set_optional_scalar,
            set_optional_string=self._set_optional_string,
            set_optional_vector=self._set_optional_vector,
            delete_existing_attr=self._delete_existing_attr,
            write_optional_bone_reference=self._write_optional_bone_reference,
            diffuse_alpha_attribute=self._DIFFUSE_ALPHA,
            edge_alpha_attribute=self._EDGE_ALPHA,
        )
        self._model_metadata_writer = ModelMetadataWriter(writer_context)
        self._bone_metadata_writer = BoneMetadataWriter(writer_context)
        self._material_metadata_writer = MaterialMetadataWriter(writer_context)
        self._morph_metadata_writer = MorphMetadataWriter(writer_context)

    def read_model_metadata(self, root: str) -> Mapping[str, Any]:
        """Return the canonical model-header mapping for an existing root."""
        return self._model_repository.read_model_metadata(root)

    def read_material_list_projection(self, model_root: str) -> MaterialListProjection:
        return self._material_repository.read_material_list_projection(model_root)

    def read_material_detail_projection(
        self,
        model_root: str,
        index: int,
        binding: str,
        assignment: MaterialAssignmentSummary,
    ) -> MaterialDetailProjection:
        return self._material_repository.read_material_detail_projection(
            model_root,
            index,
            binding,
            assignment,
            material_reader=self.read_material_value,
        )

    def read_morph_authoring_snapshot(self, model_root: str) -> MorphAuthoringReadSnapshot:
        """Read semantic Morph data and its runtime projection in one generation."""
        return self._morph_repository.read_morph_authoring_snapshot(
            model_root,
            model_reader=self.read_model_metadata,
            morph_reader=self.iter_morph_metadata,
        )

    def read_bone_value(
        self,
        model_root: str,
        binding: str,
        index: int | None = None,
    ) -> MmdBoneSpec:
        return self._bone_repository.read_bone_value(model_root, binding, index)

    def read_morph_value(
        self,
        model_root: str,
        binding: str,
        index: int | None = None,
    ) -> MmdMorphSpec:
        """Read one selected morph binding without enumerating other metadata."""
        return self._morph_repository.read_morph_value(model_root, binding, index)

    def iter_bone_metadata(self, root: str) -> Iterable[Mapping[str, Any]]:
        return self._bone_repository.iter_bone_metadata(root)

    def iter_material_metadata(self, root: str) -> Iterable[Mapping[str, Any]]:
        return self._material_repository.iter_material_metadata(
            root,
            member_reader=self._registry_material_members,
            legacy_member_reader=self._legacy_material_members,
            material_reader=self._read_material,
        )

    def begin_write(self, model_root: str) -> None:
        """Capture the current spec and open one Maya undo chunk."""
        self._full_metadata_transaction.begin_write(model_root)

    def begin_display_frames_write(self, model_root: str) -> None:
        """Capture one display-frame JSON plug and open its undo chunk."""
        if self._write_transaction is not None:
            raise MayaSceneMetadataError("a metadata write transaction is already active")
        self._require_root(model_root)
        root = self._material_identity(model_root)
        existed = self._has_attr(root, ATTR_MMD_DISPLAY_FRAMES_JSON)
        original_value = (
            self._required_string(root, ATTR_MMD_DISPLAY_FRAMES_JSON) if existed else None
        )
        if not bool(self._call_adapter("undo_info", query=True, state=True)):
            raise MayaSceneMetadataError("Maya undo must be enabled for display frame writes")
        self._call_adapter(
            "undo_info",
            openChunk=True,
            chunkName="Edit Display Frames",
        )
        self._write_transaction = {
            "root": root,
            "kind": "display_frames",
            "attr_existed": existed,
            "original_value": original_value,
            "target_value": None,
            "chunk_open": True,
            "mutated": False,
        }

    def apply_display_frames_write(self, model_root: str, payload: str) -> None:
        """Write only ``mmd_display_frames_json`` inside an active chunk."""
        transaction = self._active_transaction(model_root)
        if transaction.get("kind") != "display_frames":
            raise MayaSceneMetadataError("active transaction is not a display frame write")
        if not isinstance(payload, str):
            raise MayaSceneMetadataError("display frame payload must be a string")
        existed = self._has_attr(transaction["root"], ATTR_MMD_DISPLAY_FRAMES_JSON)
        if existed != transaction["attr_existed"]:
            raise MayaSceneMetadataError("display frame metadata changed after transaction begin")
        if existed:
            current = self._required_string(transaction["root"], ATTR_MMD_DISPLAY_FRAMES_JSON)
            if current != transaction["original_value"]:
                raise MayaSceneMetadataError("display frame metadata changed after transaction begin")
        else:
            self._call_adapter(
                "add_attr",
                transaction["root"],
                longName=ATTR_MMD_DISPLAY_FRAMES_JSON,
                dataType="string",
            )
            transaction["mutated"] = True
        self._call_adapter(
            "set_attr",
            f"{transaction['root']}.{ATTR_MMD_DISPLAY_FRAMES_JSON}",
            payload,
            type="string",
        )
        transaction["mutated"] = True
        transaction["target_value"] = payload

    def commit_display_frames_write(self, model_root: str, payload: str) -> None:
        """Verify exact display-frame JSON readback and close the undo chunk."""
        transaction = self._active_transaction(model_root)
        if transaction.get("kind") != "display_frames":
            raise MayaSceneMetadataError("active transaction is not a display frame write")
        if transaction["target_value"] != payload:
            raise MayaSceneMetadataError("display frame transaction target mismatch")
        actual = self._required_string(transaction["root"], ATTR_MMD_DISPLAY_FRAMES_JSON)
        if actual != payload:
            raise MayaSceneMetadataError(
                f"display frame metadata readback mismatch: expected {payload!r}, got {actual!r}"
            )
        self._call_adapter("undo_info", closeChunk=True)
        self._write_transaction = None

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
        return self._material_repository.read_material_value(
            model_root,
            binding,
            index,
            member_reader=self._registry_material_members,
            material_reader=self._read_material,
        )

    def read_material_value_by_index(
        self,
        model_root: str,
        index: int,
    ) -> MmdMaterialSpec:
        return self._material_repository.read_material_value_by_index(
            model_root,
            index,
            member_reader=self._registry_material_members,
            material_reader=self._read_material,
        )

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
        self._full_metadata_transaction.rebase_write_bindings(model_root, target_spec)

    def apply_model_metadata(self, model_root: str, metadata: Mapping[str, Any]) -> None:
        transaction = self._full_metadata_transaction.require_active(model_root)
        self._model_metadata_writer.write(transaction, metadata)

    def apply_bone_metadata(self, model_root: str, metadata: Iterable[Mapping[str, Any]]) -> None:
        transaction = self._full_metadata_transaction.require_active(model_root)
        self._bone_metadata_writer.write(transaction, metadata)

    def apply_material_metadata(self, model_root: str, metadata: Iterable[Mapping[str, Any]]) -> None:
        transaction = self._full_metadata_transaction.require_active(model_root)
        self._material_metadata_writer.write(transaction, metadata)

    def apply_morph_metadata(self, model_root: str, metadata: Iterable[Mapping[str, Any]]) -> None:
        transaction = self._full_metadata_transaction.require_active(model_root)
        self._morph_metadata_writer.write(transaction, metadata)

    def commit_write(self, model_root: str) -> None:
        self._full_metadata_transaction.commit_write(model_root)

    def begin_material_value_patch(
        self,
        model_root: str,
        binding: str,
        old_material: MmdMaterialSpec,
        new_material: MmdMaterialSpec,
        outline_enabled: bool | None = None,
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
        outline_original = self._begin_material_outline_capture(shader, outline_enabled)
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
        if not self._material_value_attrs_equal(original, expected_old):
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
            "outline_original": outline_original,
            "outline_enabled": outline_enabled,
        }

    def begin_material_binding_patch(
        self,
        model_root: str,
        binding: str,
        old_material: MmdMaterialSpec,
        new_material: MmdMaterialSpec,
        outline_enabled: bool | None = None,
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
        outline_original = self._begin_material_outline_capture(shader, outline_enabled)
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
            "outline_original": outline_original,
            "outline_enabled": outline_enabled,
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

    def begin_morph_preview(
        self,
        model_root: str,
        target_plugs: Sequence[str],
        *,
        chunk_name: str = "MMD Morph Preview",
    ) -> MorphPreviewSession:
        """Capture a fixed preview write-set and open one Maya undo chunk."""
        if self._write_transaction is not None:
            raise MayaSceneMetadataError("a metadata write transaction is already active")
        root = self._material_identity(model_root)
        self._require_root(root)
        if not bool(self._call_adapter("undo_info", query=True, state=True)):
            raise MayaSceneMetadataError("Maya undo must be enabled for morph preview")
        canonical = tuple(self._canonical_preview_plug(plug) for plug in target_plugs)
        if not canonical:
            raise MayaSceneMetadataError("morph preview requires at least one target plug")
        if len(set(canonical)) != len(canonical):
            raise MayaSceneMetadataError("morph preview target plugs must be unique")
        original: dict[str, float] = {}
        for plug in canonical:
            if not self._call_adapter("object_exists", plug):
                raise MayaSceneMetadataError(f"morph preview target does not exist: {plug!r}")
            if bool(self._call_adapter("get_attr", plug, lock=True)):
                raise MayaSceneMetadataError(f"morph preview target is locked: {plug!r}")
            original[plug] = self._preview_weight(self._call_adapter("get_attr", plug), plug)
        token = object()
        self._call_adapter("undo_info", openChunk=True, chunkName=chunk_name)
        self._write_transaction = {
            "root": root,
            "kind": "morph_preview",
            "token": token,
            "targets": canonical,
            "original_values": original,
            "target_values": dict(original),
            "chunk_open": True,
            "mutated": False,
        }
        return MorphPreviewSession(root=root, targets=canonical, token=token)

    def apply_morph_preview(
        self,
        model_root: str,
        session: MorphPreviewSession,
        target_values: Sequence[float],
    ) -> int:
        """Write only the session's fixed targets and verify their exact values."""
        transaction = self._active_morph_preview(model_root, session)
        if len(target_values) != len(session.targets):
            raise MayaSceneMetadataError("morph preview update value count mismatch")
        expected = {
            plug: self._preview_weight(value, plug)
            for plug, value in zip(session.targets, target_values)
        }
        native_updates = [
            {"plug": plug, "value": value} for plug, value in expected.items()
        ]
        use_python = not self._use_native_morph_weights
        if self._use_native_morph_weights:
            try:
                if not hasattr(self._cmds, "command_exists"):
                    raise NativeCommandUnavailable("adapter has no native command surface")
                result = self._native_authoring.set_morph_weights(native_updates)
                canonical_values = result["values"]
                expected = {
                    plug: self._preview_weight(value, plug)
                    for plug, value in zip(session.targets, canonical_values)
                }
                transaction["mutated"] = True
            except NativeCommandUnavailable:
                use_python = True
            except NativeCommandProtocolError:
                # Maya returned from a registered command, but its envelope
                # cannot prove whether the no-op-looking command was queued.
                # Undo it even when all observed values equal the preimage.
                transaction["mutated"] = True
                raise
            except NativeAuthoringCommandError:
                # A transport/protocol failure can occur after Maya executed
                # the command. Preserve enough state for the coordinator's
                # rollback to undo the whole event-spanning preview safely.
                for plug, original in transaction["original_values"].items():
                    try:
                        actual = self._preview_weight(
                            self._call_adapter("get_attr", plug), plug
                        )
                    except Exception:
                        transaction["mutated"] = True
                        break
                    if not self._preview_weights_equal(actual, original):
                        transaction["mutated"] = True
                        break
                raise
        if use_python:
            for plug, value in expected.items():
                self._call_adapter("set_attr", plug, value)
                transaction["mutated"] = True
                actual = self._preview_weight(self._call_adapter("get_attr", plug), plug)
                if not self._preview_weights_equal(actual, value):
                    raise MayaSceneMetadataError(
                        f"morph preview readback mismatch for {plug!r}: expected {value!r}, got {actual!r}"
                    )
        transaction["target_values"] = expected
        return len(expected)

    def commit_morph_preview(self, model_root: str, session: MorphPreviewSession) -> int:
        """Close a preview chunk only after exact final-target readback."""
        transaction = self._active_morph_preview(model_root, session)
        for plug, expected in transaction["target_values"].items():
            actual = self._preview_weight(self._call_adapter("get_attr", plug), plug)
            if not self._preview_weights_equal(actual, expected):
                raise MayaSceneMetadataError(
                    f"morph preview commit readback mismatch for {plug!r}"
                )
        self._call_adapter("undo_info", closeChunk=True)
        transaction["chunk_open"] = False
        self._write_transaction = None
        return len(transaction["targets"])

    def rollback_morph_preview(self, model_root: str, session: MorphPreviewSession) -> None:
        """Close and undo one mutated preview chunk, then verify its preimage."""
        transaction = self._active_morph_preview(model_root, session)
        try:
            if transaction["chunk_open"]:
                self._call_adapter("undo_info", closeChunk=True)
                transaction["chunk_open"] = False
            if transaction["mutated"]:
                self._call_adapter("undo")
        finally:
            self._write_transaction = None
        for plug, expected in transaction["original_values"].items():
            actual = self._preview_weight(self._call_adapter("get_attr", plug), plug)
            if not self._preview_weights_equal(actual, expected):
                raise MayaSceneMetadataError(
                    f"morph preview rollback preimage mismatch for {plug!r}"
                )

    def _active_morph_preview(
        self, model_root: str, session: MorphPreviewSession
    ) -> dict[str, Any]:
        if not isinstance(session, MorphPreviewSession):
            raise MayaSceneMetadataError("invalid morph preview session")
        transaction = self._active_transaction(model_root)
        if (
            transaction.get("kind") != "morph_preview"
            or transaction.get("token") is not session.token
            or transaction.get("root") != session.root
            or transaction.get("targets") != session.targets
        ):
            raise MayaSceneMetadataError("morph preview session identity mismatch")
        return transaction

    def _canonical_preview_plug(self, plug: Any) -> str:
        if not isinstance(plug, str) or "." not in plug:
            raise MayaSceneMetadataError(f"invalid morph preview target plug: {plug!r}")
        node, attr = plug.rsplit(".", 1)
        names = self._call_adapter("ls", node, long=True) or ()
        if isinstance(names, (str, bytes, bytearray)) or len(names) != 1:
            raise MayaSceneMetadataError(
                f"morph preview node has no unique canonical identity: {node!r}"
            )
        canonical = names[0]
        if not isinstance(canonical, str) or not canonical or not attr:
            raise MayaSceneMetadataError(f"invalid morph preview target plug: {plug!r}")
        return f"{canonical}.{attr}"

    @staticmethod
    def _preview_weight(value: Any, plug: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MayaSceneMetadataError(f"morph preview weight must be numeric for {plug!r}")
        result = float(value)
        if not math.isfinite(result):
            raise MayaSceneMetadataError(f"morph preview weight must be finite for {plug!r}")
        return result

    @staticmethod
    def _preview_weights_equal(actual: float, expected: float) -> bool:
        """Accept only the bounded round-trip error of Maya float attributes."""
        return math.isclose(actual, expected, rel_tol=1e-7, abs_tol=1e-7)

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
        outline_target: Mapping[str, Any] | None = None,
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
        if not self._material_value_attrs_equal(actual, expected):
            raise MayaSceneMetadataError(
                f"material value patch fingerprint mismatch: expected {expected!r}, got {actual!r}"
            )
        self._verify_material_outline_target(transaction, outline_target, material)
        self._call_adapter("undo_info", closeChunk=True)
        self._write_transaction = None

    def commit_material_binding_patch(
        self,
        model_root: str,
        binding: str,
        material: MmdMaterialSpec,
        outline_target: Mapping[str, Any] | None = None,
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
        self._verify_material_outline_target(transaction, outline_target, material)
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
    def _material_value_attrs_equal(
        actual: Mapping[str, Any],
        expected: Mapping[str, Any],
    ) -> bool:
        """Compare a material patch fingerprint across Maya numeric storage types.

        Imported attributes may be stored as Maya ``float`` while newly authored
        ones use ``double``.  Maya therefore reads a value such as ``0.6`` back as
        ``0.6000000238418579`` on older/imported shaders.  Keep the fingerprint
        structure exact, but accept only the normal numeric round-trip error.
        """
        if actual.keys() != expected.keys():
            return False
        numeric_fields = {
            "diffuse",
            "specular",
            "specular_coefficient",
            "ambient",
            "edge_color",
            "edge_size",
            "viewport_diffuse",
        }
        for field in actual:
            actual_value = actual[field]
            expected_value = expected[field]
            if field not in numeric_fields:
                if actual_value != expected_value:
                    return False
                continue
            actual_values = (
                tuple(actual_value)
                if isinstance(actual_value, (list, tuple))
                else (actual_value,)
            )
            expected_values = (
                tuple(expected_value)
                if isinstance(expected_value, (list, tuple))
                else (expected_value,)
            )
            if len(actual_values) != len(expected_values):
                return False
            if any(
                not math.isclose(
                    float(actual_component),
                    float(expected_component),
                    rel_tol=1.0e-6,
                    abs_tol=1.0e-7,
                )
                for actual_component, expected_component in zip(
                    actual_values, expected_values
                )
            ):
                return False
        return True

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
        if not actual["aliases"].get(morph.index):
            raise MayaSceneMetadataError("created morph controller alias is missing")
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
        if transaction.get("kind") == "full_metadata":
            self._full_metadata_transaction.rollback_write(model_root)
            return
        if transaction.get("kind") == "morph_topology_repair":
            try:
                if transaction["chunk_open"]:
                    self._call_adapter("undo_info", closeChunk=True)
                    transaction["chunk_open"] = False
                if transaction["mutated"]:
                    self._call_adapter("undo")
            finally:
                self._write_transaction = None
            controller = transaction["controller"]
            actual = {
                "version": self._call_adapter("get_attr", f"{controller}.topologyVersion"),
                "source": self._call_adapter("get_attr", f"{controller}.groupTopology"),
                "version_locked": bool(
                    self._call_adapter(
                        "get_attr", f"{controller}.topologyVersion", lock=True
                    )
                ),
                "source_locked": bool(
                    self._call_adapter(
                        "get_attr", f"{controller}.groupTopology", lock=True
                    )
                ),
            }
            if actual != transaction["original_values"]:
                raise MayaSceneMetadataError("morph topology rollback preimage mismatch")
            return
        if transaction.get("kind") == "display_frames":
            try:
                if transaction["chunk_open"]:
                    self._call_adapter("undo_info", closeChunk=True)
                    transaction["chunk_open"] = False
                if transaction["mutated"]:
                    self._call_adapter("undo")
            finally:
                self._write_transaction = None
            existed = self._has_attr(transaction["root"], ATTR_MMD_DISPLAY_FRAMES_JSON)
            if existed != transaction["attr_existed"]:
                raise MayaSceneMetadataError("display frame rollback attribute existence mismatch")
            if existed:
                actual = self._required_string(
                    transaction["root"], ATTR_MMD_DISPLAY_FRAMES_JSON
                )
                if actual != transaction["original_value"]:
                    raise MayaSceneMetadataError("display frame rollback preimage mismatch")
            return
        if transaction.get("kind") == "material_value":
            # Native Material commands can fail before Maya records any undo
            # item, or after changing only part of their fixed write set.
            # Decide from the narrow preimage/read-back pair instead of
            # unconditionally consuming the user's previous global Undo item.
            try:
                transaction["mutated"] = self._material_value_mutated(transaction)
            except Exception:
                # An unavailable read-back cannot prove that the command was
                # harmless.  Preserve the fail-closed rollback behavior; the
                # subsequent exact preimage check reports any remaining issue.
                transaction["mutated"] = True
        try:
            if transaction["chunk_open"]:
                self._call_adapter("undo_info", closeChunk=True)
                transaction["chunk_open"] = False
            if transaction.get("mutated", True):
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
            if not self._material_value_attrs_equal(
                actual, transaction["original_values"]
            ):
                raise MayaSceneMetadataError("material value patch rollback fingerprint mismatch")
            self._verify_material_outline_rollback(transaction)
            return
        if transaction.get("kind") == "material_binding":
            actual = self.read_material_value(
                transaction["root"], transaction["binding"], transaction["index"]
            )
            if actual != transaction["original_material"]:
                raise MayaSceneMetadataError("material binding patch rollback fingerprint mismatch")
            self._verify_material_outline_rollback(transaction)
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

    def _material_value_mutated(self, transaction: Mapping[str, Any]) -> bool:
        """Return whether a narrow Material command changed owned state."""
        actual = self._read_material_value_attrs(transaction["binding"])
        diffuse_route = transaction.get("diffuse_route")
        expected = transaction["original_values"]
        if isinstance(diffuse_route, MayaMaterialShaderRoute):
            actual["viewport_diffuse"] = self._required_vector(
                transaction["binding"], diffuse_route.diffuse_attribute
            )
        if not self._material_value_attrs_equal(actual, expected):
            return True
        outline_original = transaction.get("outline_original")
        if outline_original is not None:
            return (
                self._capture_material_outline_attrs(transaction["binding"])
                != outline_original
            )
        return False

    def iter_morph_metadata(self, root: str) -> Iterable[Mapping[str, Any]]:
        """Yield strict raw PMX morph mappings owned by one explicit root."""
        yield from self._morph_repository.iter_morph_metadata(root)

    def _registry_morph_members(self, root: str) -> list[str] | None:
        """Delegate writer verification ownership reads to the Morph authority."""
        return self._morph_repository.registry_morph_members(root)

    def _legacy_morph_members(self, root: str) -> list[str]:
        """Delegate legacy ownership discovery to the Morph authority."""
        return self._morph_repository.legacy_morph_members(root)

    def _read_morph(self, node: str, *, root: str | None = None) -> dict[str, Any]:
        """Delegate strict Morph mapping reads used by writer readback checks."""
        return self._morph_repository.read_morph_mapping(node, root=root)

    @staticmethod
    def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON field {key!r}")
            value[key] = item
        return value

    def _registry_material_members(self, root: str) -> list[str] | None:
        return self._material_repository.registry_material_members(root)

    def _legacy_material_members(self, root: str) -> list[str]:
        return self._material_repository.legacy_material_members(root)

    def _read_material(self, shader: str) -> dict[str, Any]:
        return self._material_repository.read_material_mapping(shader)

    def _get_write_transaction(self) -> dict[str, Any] | None:
        """Return the shared active transaction for injected authorities."""
        return self._write_transaction

    def _set_write_transaction(self, transaction: dict[str, Any] | None) -> None:
        """Update the shared active transaction registry."""
        self._write_transaction = transaction

    def _active_transaction(self, model_root: str) -> dict[str, Any]:
        transaction = self._write_transaction
        if transaction is None:
            raise MayaSceneMetadataError("no metadata write transaction is active")
        if self._material_identity(model_root) != transaction["root"]:
            raise MayaSceneMetadataError("metadata write transaction belongs to another model root")
        return transaction

    def _begin_material_outline_capture(
        self,
        shader: str,
        outline_enabled: bool | None,
    ) -> dict[str, Any] | None:
        """Capture preview attrs only for an explicit DX11 outline edit."""
        if outline_enabled is None:
            return None
        if type(outline_enabled) is not bool:
            raise MayaSceneMetadataError("material outline intent must be bool or None")
        if self._node_type(shader) != "dx11Shader":
            raise MayaSceneMetadataError("material outline intent requires a dx11Shader")
        return self._capture_material_outline_attrs(shader)

    def _capture_material_outline_attrs(self, shader: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for attr in _MATERIAL_OUTLINE_ATTRS:
            exists = self._has_attr(shader, attr)
            result[attr] = {
                "exists": exists,
                "value": (
                    deepcopy(self._call_adapter("get_attr", f"{shader}.{attr}"))
                    if exists
                    else None
                ),
            }
        return result

    def _verify_material_outline_target(
        self,
        transaction: Mapping[str, Any],
        outline_target: Mapping[str, Any] | None,
        material: MmdMaterialSpec,
    ) -> None:
        original = transaction.get("outline_original")
        if original is None:
            if outline_target is not None:
                raise MayaSceneMetadataError("unexpected material outline target")
            return
        if not isinstance(outline_target, Mapping):
            raise MayaSceneMetadataError("material outline target was not recorded")
        expected = dict(outline_target)
        if set(expected) != set(_MATERIAL_OUTLINE_ATTRS):
            raise MayaSceneMetadataError("material outline target fields mismatch")
        actual = self._capture_material_outline_attrs(transaction["binding"])
        if actual != expected:
            raise MayaSceneMetadataError(
                f"material outline fingerprint mismatch: expected {expected!r}, got {actual!r}"
            )
        outline_attr = actual["mmd_shader_outline_enabled"]
        if not outline_attr["exists"] or bool(outline_attr["value"]) is not transaction["outline_enabled"]:
            raise MayaSceneMetadataError("material outline intent readback mismatch")
        from mmd_tools.converters.mesh_converter import expected_shader_outline_preview

        original = transaction["outline_original"]
        transparency = original["mmdTransparencyMode"]
        expected_policy = expected_shader_outline_preview(
            str(original["technique"]["value"] or ""),
            transparency["value"] if transparency["exists"] else None,
            material.draw_flags,
            transaction["outline_enabled"],
            material.edge_size,
            edge_size_exists=bool(original["EdgeSize"]["exists"]),
        )
        for attr, expected_value in expected_policy.items():
            observed = actual[attr]
            matches = observed["value"] == expected_value
            if attr == "EdgeSize" and observed["exists"]:
                value = observed["value"]
                matches = (
                    not isinstance(value, bool)
                    and isinstance(value, (int, float))
                    and math.isclose(
                        float(value),
                        float(expected_value),
                        rel_tol=1e-6,
                        abs_tol=1e-7,
                    )
                )
            if not observed["exists"] or not matches:
                raise MayaSceneMetadataError(
                    f"material outline policy mismatch for {attr}: "
                    f"expected {expected_value!r}, got {observed!r}"
                )

    def _verify_material_outline_rollback(self, transaction: Mapping[str, Any]) -> None:
        original = transaction.get("outline_original")
        if original is None:
            return
        actual = self._capture_material_outline_attrs(transaction["binding"])
        if actual != original:
            raise MayaSceneMetadataError("material outline rollback preimage mismatch")

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

    def _set_optional_scalar(self, node: str, attr: str, value: Any, attribute_type: str) -> None:
        """Create an optional scalar metadata attribute when it becomes active."""
        if not self._has_attr(node, attr):
            self._call_adapter("add_attr", node, longName=attr, attributeType=attribute_type)
        self._call_adapter("set_attr", f"{node}.{attr}", value)

    def _set_optional_string(self, node: str, attr: str, value: str) -> None:
        """Create an optional string metadata attribute when it becomes active."""
        if not self._has_attr(node, attr):
            self._call_adapter("add_attr", node, longName=attr, dataType="string")
        self._call_adapter("set_attr", f"{node}.{attr}", value, type="string")

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

    def _material_identity(self, node: Any) -> str:
        return self._read_support.canonical_identity(node)

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
        result = self._read_support.call_adapter(method, *args, **kwargs)
        if method in {"set_attr", "add_attr", "delete_attr"}:
            self._full_metadata_transaction.mark_mutation()
        return result

    def _agreed_vector_alias(self, joint: str, attrs: tuple[str, ...]) -> tuple[float, float, float]:
        values = [(attr, self._required_vector(joint, attr)) for attr in attrs if self._has_attr(joint, attr)]
        if not values:
            raise MayaSceneMetadataError(f"{joint}: missing required alias fields {attrs!r}")
        if len({value for _, value in values}) != 1:
            raise MayaSceneMetadataError(f"{joint}: conflicting alias fields {attrs!r}")
        return values[0][1]

    def _required(self, node: str, attr: str) -> Any:
        return self._read_support.required(node, attr)

    def _required_string(self, node: str, attr: str) -> str:
        return self._read_support.required_string(node, attr)

    def _required_int(
        self,
        node: str,
        attr: str,
        *,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int:
        return self._read_support.required_int(
            node,
            attr,
            minimum=minimum,
            maximum=maximum,
        )

    def _required_number(self, node: str, attr: str) -> float:
        return self._read_support.required_number(node, attr)

    def _required_vector(self, node: str, attr: str) -> tuple[float, float, float]:
        return self._read_support.required_vector(node, attr)

    def _has_attr(self, node: str, attr: str) -> bool:
        return self._read_support.has_attr(node, attr)

    def _require_root(self, root: Any) -> None:
        self._read_support.require_root(root)

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
        """Delegate writer verification ownership reads to the Morph authority."""
        return self._morph_repository.require_selected_morph(root, node, index)


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


__all__ = [
    "MayaSceneMetadataError",
    "MayaSceneMetadataBackend",
    "NARROW_TRANSACTION_KINDS",
]
