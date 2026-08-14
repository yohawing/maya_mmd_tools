"""Coordinate pure model mutations with Maya binding transactions.

This module is the structural transaction boundary for product authoring.  It
computes complete immutable targets for generic structural edits, while the
adjacent Material swap uses a dedicated narrow transaction.  Binding
operations run without opening nested chunks and every path fails closed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import is_dataclass, replace
import math
from typing import Any, Callable

from mmd_tools.adapters import maya_bone_authoring
from mmd_tools.adapters.maya_material_authoring import MaterialReindexResult
from mmd_tools.core.bone_authoring import (
    BoneResetPlan,
    classify_bone_change,
    reindex_bones,
    replace_bone as replace_bone_spec,
    replace_bone_semantic as replace_bone_semantic_spec,
    unregister_bone,
)
from mmd_tools.core.material_authoring import (
    classify_material_change,
    delete_material,
    move_material as move_material_spec,
    reindex_materials,
    replace_material,
)
from mmd_tools.core.model_authoring_spec import (
    MmdBoneSpec,
    MmdMaterialSpec,
    MmdModelAuthoringSpec,
    MmdMorphSpec,
)
from mmd_tools.core.morph_authoring import (
    classify_morph_change,
    delete_morph as delete_morph_spec,
    MorphReindexResult,
    reindex_morphs as reindex_morphs_spec,
    replace_morph as replace_morph_spec,
    replace_morph_offsets as replace_morph_offsets_spec,
)
from mmd_tools.core.morph_topology import (
    MorphTopologyInspection,
    serialize_group_topology,
)
from mmd_tools.core.pmx_data.bone import PmxBoneFlag
from mmd_tools.core.logger import get_logger
from mmd_tools.core.constants import ATTR_MMD_DISPLAY_FRAMES_JSON
from mmd_tools.adapters.transaction_runner import TransactionFailure, TransactionRunner


logger = get_logger(__name__)
_REHYDRATED_SPEC_TYPE_IDS: set[int] = set()


class MayaModelAuthoringCoordinatorError(RuntimeError):
    """Raised when a structural Maya authoring transaction fails closed."""


class MayaModelAuthoringCoordinator:
    """Run one pure mutation and all of its Maya writes in one undo chunk."""

    def __init__(
        self,
        metadata_adapter: Any,
        metadata_backend: Any,
        material_authoring: Any,
        cmds_adapter: Any,
        *,
        bone_api: Any = maya_bone_authoring,
        morph_authoring: Any | None = None,
        model_scale_resolver: Callable[[str], float] | None = None,
    ) -> None:
        self._require_methods(metadata_adapter, ("read_spec",), "metadata_adapter")
        self._require_methods(
            metadata_backend,
            (
                "begin_write",
                "rebase_write_bindings",
                "apply_model_metadata",
                "apply_bone_metadata",
                "apply_material_metadata",
                "apply_morph_metadata",
                "commit_write",
                "rollback_write",
            ),
            "metadata_backend",
        )
        self._require_methods(
            material_authoring,
            ("create_material", "resolve_material", "delete_material"),
            "material_authoring",
        )
        self._require_methods(
            cmds_adapter,
            ("object_exists", "ls", "list_relatives", "xform"),
            "cmds_adapter",
        )
        self._require_methods(
            bone_api,
            (
                "capture_rest_position",
                "register_existing_joint",
                "apply_bone_reindex",
                "unregister_existing_joint",
            ),
            "bone_api",
        )
        self._metadata = metadata_adapter
        self._backend = metadata_backend
        self._materials = material_authoring
        self._cmds = cmds_adapter
        self._bones = bone_api
        self._morphs = morph_authoring
        self._model_scale_resolver = model_scale_resolver

    def read_spec(self, model_root: str) -> MmdModelAuthoringSpec:
        """Read the current strict scene specification for UI refreshes."""
        return self._read_current(model_root, "read_spec")

    def inspect_morph_topology(self, model_root: str) -> MorphTopologyInspection:
        """Return read-only diagnostics for the derived controller cache."""
        inspect = getattr(self._backend, "inspect_morph_topology", None)
        if not callable(inspect):
            raise MayaModelAuthoringCoordinatorError(
                "morph topology inspection is unavailable"
            )
        result = inspect(model_root)
        if not isinstance(result, MorphTopologyInspection):
            raise MayaModelAuthoringCoordinatorError(
                "morph topology inspection returned an invalid result"
            )
        return result

    def repair_morph_topology(self, model_root: str) -> MorphTopologyInspection:
        """Explicitly repair only the derived controller cache."""
        inspection = self.inspect_morph_topology(model_root)
        if not inspection.repairable:
            raise MayaModelAuthoringCoordinatorError("morph topology is not repairable")
        source = serialize_group_topology(inspection.expected)

        def error_factory(failure: TransactionFailure) -> Exception:
            return MayaModelAuthoringCoordinatorError(str(failure))

        TransactionRunner[str](
            "repair_morph_topology",
            (model_root,),
            begin=lambda _targets: self._backend.begin_morph_topology_repair(
                model_root, source
            ),
            mutate=lambda _targets: self._backend.apply_morph_topology_repair(
                model_root, source
            ),
            verify_and_commit=lambda result, _targets: self._backend.commit_morph_topology_repair(
                model_root, result
            ),
            rollback=lambda _targets: self._backend.rollback_write(model_root),
            error_factory=error_factory,
        ).run()
        return self.inspect_morph_topology(model_root)

    def write_display_frames(self, model_root: str, payload: str) -> str:
        """Persist one display-frame JSON payload without reading the full spec."""
        if not isinstance(payload, str):
            raise MayaModelAuthoringCoordinatorError("display frame payload must be a string")
        begin = getattr(self._backend, "begin_display_frames_write", None)
        apply = getattr(self._backend, "apply_display_frames_write", None)
        commit = getattr(self._backend, "commit_display_frames_write", None)
        if not callable(begin) or not callable(apply) or not callable(commit):
            raise MayaModelAuthoringCoordinatorError(
                "display frame write requires narrow metadata transaction APIs"
            )

        def mutate(_targets: tuple[Any, ...]) -> str:
            apply(model_root, payload)
            return payload

        return self._run_transaction(
            model_root,
            "write_display_frames",
            (model_root, ATTR_MMD_DISPLAY_FRAMES_JSON),
            lambda _targets: begin(model_root),
            mutate,
            lambda result, _targets: commit(model_root, result),
        )

    def read_material_value(
        self,
        model_root: str,
        index: int,
        binding: str,
    ) -> MmdMaterialSpec:
        """Read one selected material without reading unrelated metadata."""
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise MayaModelAuthoringCoordinatorError("material index must be a non-negative integer")
        if not isinstance(binding, str) or not binding.strip():
            raise MayaModelAuthoringCoordinatorError("material binding must be a non-empty string")
        reader = getattr(self._metadata, "read_material_value", None)
        if not callable(reader):
            raise MayaModelAuthoringCoordinatorError(
                "read_material_value requires a selected-material metadata reader"
            )
        try:
            material = reader(model_root, binding, index)
        except Exception as exc:
            raise MayaModelAuthoringCoordinatorError(
                f"read_material_value failed for root {model_root!r}: {exc}"
            ) from exc
        if not isinstance(material, MmdMaterialSpec):
            raise MayaModelAuthoringCoordinatorError("selected-material reader returned an invalid material")
        if material.index != index or material.binding_identity != binding:
            raise MayaModelAuthoringCoordinatorError("selected-material reader returned the wrong binding")
        return material

    def read_bone_value(
        self,
        model_root: str,
        index: int,
        binding: str,
    ) -> MmdBoneSpec:
        """Read one selected bone without reading unrelated model metadata."""
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise MayaModelAuthoringCoordinatorError("bone index must be a non-negative integer")
        if not isinstance(binding, str) or not binding.strip():
            raise MayaModelAuthoringCoordinatorError("bone binding must be a non-empty string")
        reader = getattr(self._metadata, "read_bone_value", None)
        if not callable(reader):
            raise MayaModelAuthoringCoordinatorError(
                "read_bone_value requires a selected-bone metadata reader"
            )
        try:
            bone = reader(model_root, binding, index)
        except Exception as exc:
            raise MayaModelAuthoringCoordinatorError(
                f"read_bone_value failed for root {model_root!r}: {exc}"
            ) from exc
        if not isinstance(bone, MmdBoneSpec):
            raise MayaModelAuthoringCoordinatorError("selected-bone reader returned an invalid bone")
        if bone.index != index or bone.binding_identity != binding:
            raise MayaModelAuthoringCoordinatorError("selected-bone reader returned the wrong binding")
        return bone

    def apply_bone_value_patch(
        self,
        model_root: str,
        bone: MmdBoneSpec,
    ) -> MmdBoneSpec:
        """Apply one selected-bone value edit in a narrow undo chunk."""
        if not isinstance(bone, MmdBoneSpec):
            raise MayaModelAuthoringCoordinatorError(
                "apply_bone_value_patch requires an MmdBoneSpec"
            )
        binding = bone.binding_identity
        if not isinstance(binding, str) or not binding:
            raise MayaModelAuthoringCoordinatorError(
                "apply_bone_value_patch requires a bone binding identity"
            )
        previous = self.read_bone_value(model_root, bone.index, binding)
        route = classify_bone_change(previous, bone)
        if route == "noop":
            return previous
        if route != "value":
            raise MayaModelAuthoringCoordinatorError(
                "apply_bone_value_patch received structural fields"
            )
        structural_patch = getattr(self._bones, "apply_bone_value_patch", None)
        begin = getattr(self._backend, "begin_bone_value_patch", None)
        commit = getattr(self._metadata, "commit_bone_value_patch", None)
        if not callable(structural_patch) or not callable(begin) or not callable(commit):
            raise MayaModelAuthoringCoordinatorError(
                "apply_bone_value_patch requires narrow bone binding/metadata APIs; "
                "no Maya writes were performed"
            )

        def bind() -> MmdBoneSpec:
            result = structural_patch(model_root, previous, bone, self._cmds)
            if not isinstance(result, MmdBoneSpec):
                raise TypeError("bone value patch binding operation returned an invalid bone")
            return result

        return self._execute_bone_value_patch(
            model_root,
            "apply_bone_value_patch",
            previous,
            bone,
            binding,
            begin,
            bind,
            commit,
        )

    def read_morph_value(
        self,
        model_root: str,
        index: int,
        binding: str,
    ) -> MmdMorphSpec:
        """Read one selected morph without enumerating the model spec."""
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise MayaModelAuthoringCoordinatorError("morph index must be a non-negative integer")
        if not isinstance(binding, str) or not binding.strip():
            raise MayaModelAuthoringCoordinatorError("morph binding must be a non-empty string")
        reader = getattr(self._metadata, "read_morph_value", None)
        if not callable(reader):
            raise MayaModelAuthoringCoordinatorError(
                "read_morph_value requires a selected-morph metadata reader"
            )
        try:
            morph = reader(model_root, binding, index)
        except Exception as exc:
            raise MayaModelAuthoringCoordinatorError(
                f"read_morph_value failed for root {model_root!r}: {exc}"
            ) from exc
        if not isinstance(morph, MmdMorphSpec):
            raise MayaModelAuthoringCoordinatorError("selected-morph reader returned an invalid morph")
        if morph.index != index or morph.binding_identity != binding:
            raise MayaModelAuthoringCoordinatorError("selected-morph reader returned the wrong binding")
        return morph

    def apply_morph_value_patch(
        self,
        model_root: str,
        morph: MmdMorphSpec,
    ) -> MmdMorphSpec:
        """Apply one selected morph's patch-safe values in a narrow undo chunk."""
        if not isinstance(morph, MmdMorphSpec):
            raise MayaModelAuthoringCoordinatorError(
                "apply_morph_value_patch requires an MmdMorphSpec"
            )
        binding = morph.binding_identity
        if not isinstance(binding, str) or not binding:
            raise MayaModelAuthoringCoordinatorError(
                "apply_morph_value_patch requires a morph binding identity"
            )
        previous = self.read_morph_value(model_root, morph.index, binding)
        route = classify_morph_change(previous, morph)
        if route == "noop":
            return previous
        if route != "value":
            raise MayaModelAuthoringCoordinatorError(
                "apply_morph_value_patch received structural fields"
            )
        begin = getattr(self._backend, "begin_morph_value_patch", None)
        commit = getattr(self._metadata, "commit_morph_value_patch", None)
        if not callable(begin) or not callable(commit):
            raise MayaModelAuthoringCoordinatorError(
                "apply_morph_value_patch requires narrow morph metadata APIs; "
                "no Maya writes were performed"
            )

        def bind() -> MmdMorphSpec:
            patch = getattr(self._morphs, "apply_morph_value_patch", None)
            if callable(patch):
                result = patch(model_root, previous, morph, self._cmds)
            else:
                # Production composition injects a closure for structural
                # morph writes.  The narrow implementation is kept on the
                # adapter module so this path never falls back to that closure
                # (which would rebuild the full controller graph).
                from mmd_tools.adapters import maya_morph_authoring

                result = maya_morph_authoring.apply_morph_value_patch(
                    model_root, previous, morph, self._cmds
                )
            if not isinstance(result, MmdMorphSpec):
                raise TypeError("morph value patch binding operation returned an invalid morph")
            return result

        return self._execute_morph_value_patch(
            model_root,
            "apply_morph_value_patch",
            previous,
            morph,
            binding,
            begin,
            bind,
            commit,
        )

    # Material operations -------------------------------------------------
    def create_material(
        self,
        model_root: str,
    ) -> MmdMaterialSpec:
        """Create one default material through a material-only transaction."""
        next_index = self._narrow_next_material_index(model_root, "create_material")
        material = MmdMaterialSpec(
            name=f"Material {next_index}",
            name_english=f"Material {next_index}",
            index=next_index,
        )
        return self._execute_material_create(model_root, "create_material", material)

    def duplicate_material(
        self,
        model_root: str,
        source_index: int,
    ) -> MmdMaterialSpec:
        """Duplicate one selected material through a material-only transaction."""
        try:
            source = self._metadata.read_material_value_by_index(model_root, source_index)
            next_index = self._metadata.next_material_index(model_root)
        except Exception as exc:
            raise MayaModelAuthoringCoordinatorError(
                f"duplicate_material narrow read failed for root {model_root!r}: {exc}"
            ) from exc
        if not isinstance(source, MmdMaterialSpec):
            raise MayaModelAuthoringCoordinatorError("selected material reader returned an invalid material")
        duplicated = replace(
            source,
            index=next_index,
            name=f"{source.name} Copy",
            name_english=f"{source.name_english} Copy",
            binding_identity=None,
        )
        return self._execute_material_create(model_root, "duplicate_material", duplicated)

    def replace_material(
        self,
        model_root: str,
        material: MmdMaterialSpec,
    ) -> MmdModelAuthoringSpec:
        """Replace one material through a single binding/metadata transaction."""
        current = self._read_current(model_root, "replace_material")
        if not isinstance(material, MmdMaterialSpec):
            raise MayaModelAuthoringCoordinatorError(
                "replace_material requires an MmdMaterialSpec"
            )
        previous = self._material(current, material.index)
        if material.binding_identity is None:
            material = replace(material, binding_identity=previous.binding_identity)
        if material.binding_identity != previous.binding_identity:
            raise MayaModelAuthoringCoordinatorError(
                f"material {material.index} binding identity cannot change"
            )
        target = self._pure(
            "replace_material",
            lambda: replace_material(current, material),
        )
        structural_replace = getattr(self._materials, "replace_material", None)
        if not callable(structural_replace):
            raise MayaModelAuthoringCoordinatorError(
                "replace_material requires a material binding replacement API; no Maya writes were performed"
            )

        def bind() -> MmdModelAuthoringSpec:
            result = structural_replace(model_root, current, target)
            if not isinstance(result, MmdModelAuthoringSpec):
                raise TypeError("material binding replacement returned an invalid spec")
            return result

        return self._execute(model_root, "replace_material", target, bind)

    def apply_material_value_patch(
        self,
        model_root: str,
        material: MmdMaterialSpec,
        outline_enabled: bool | None = None,
    ) -> MmdMaterialSpec:
        """Apply one patch-safe material value edit in a narrow undo chunk."""
        if not isinstance(material, MmdMaterialSpec):
            raise MayaModelAuthoringCoordinatorError(
                "apply_material_value_patch requires an MmdMaterialSpec"
            )
        binding = material.binding_identity
        if not isinstance(binding, str) or not binding:
            raise MayaModelAuthoringCoordinatorError(
                "apply_material_value_patch requires a material binding identity"
            )
        previous = self.read_material_value(model_root, material.index, binding)
        if material.binding_identity is None:
            material = replace(material, binding_identity=previous.binding_identity)
        if material.binding_identity != previous.binding_identity:
            raise MayaModelAuthoringCoordinatorError(
                f"material {material.index} binding identity cannot change"
            )
        route = classify_material_change(previous, material)
        if outline_enabled is not None and type(outline_enabled) is not bool:
            raise MayaModelAuthoringCoordinatorError("material outline intent must be bool or None")
        if route == "noop" and outline_enabled is None:
            return previous
        if route not in {"value", "noop"}:
            raise MayaModelAuthoringCoordinatorError(
                "apply_material_value_patch received binding-sensitive fields"
            )
        structural_patch = getattr(self._materials, "apply_material_value_patch", None)
        outline_patch = getattr(self._materials, "apply_material_outline", None)
        if not callable(structural_patch):
            raise MayaModelAuthoringCoordinatorError(
                "apply_material_value_patch requires a narrow material binding API; no Maya writes were performed"
            )
        if outline_enabled is not None and not callable(outline_patch):
            raise MayaModelAuthoringCoordinatorError(
                "material outline edit requires a DX11 outline binding API"
            )
        begin = getattr(self._backend, "begin_material_value_patch", None)
        commit_owner = self._backend if outline_enabled is not None else self._metadata
        commit = getattr(commit_owner, "commit_material_value_patch", None)
        if not callable(begin) or not callable(commit):
            raise MayaModelAuthoringCoordinatorError(
                "apply_material_value_patch requires a narrow metadata transaction; no Maya writes were performed"
            )
        outline_target: Mapping[str, Any] | None = None

        def bind() -> MmdMaterialSpec:
            nonlocal outline_target
            result = (
                previous
                if route == "noop"
                else structural_patch(model_root, previous, material)
            )
            if not isinstance(result, MmdMaterialSpec):
                raise TypeError("material value patch binding operation returned an invalid material")
            if outline_enabled is not None:
                outline_target = outline_patch(binding, outline_enabled, material.edge_size)
            return result

        begin_patch = begin
        commit_patch = commit
        if outline_enabled is not None:
            def begin_with_outline(root, node, old, new):
                return begin(root, node, old, new, outline_enabled)

            def commit_with_outline(root, node, target):
                return commit(root, node, target, outline_target)

            begin_patch = begin_with_outline
            commit_patch = commit_with_outline

        return self._execute_material_patch(
            model_root,
            "apply_material_value_patch",
            previous,
            material,
            binding,
            begin_patch,
            bind,
            commit_patch,
        )

    def apply_material_binding_patch(
        self,
        model_root: str,
        material: MmdMaterialSpec,
        outline_enabled: bool | None = None,
    ) -> MmdMaterialSpec:
        """Apply one selected material, including texture binding fields."""
        if not isinstance(material, MmdMaterialSpec):
            raise MayaModelAuthoringCoordinatorError(
                "apply_material_binding_patch requires an MmdMaterialSpec"
            )
        binding = material.binding_identity
        if not isinstance(binding, str) or not binding:
            raise MayaModelAuthoringCoordinatorError(
                "apply_material_binding_patch requires a material binding identity"
            )
        previous = self.read_material_value(model_root, material.index, binding)
        if material.binding_identity != previous.binding_identity:
            raise MayaModelAuthoringCoordinatorError(
                f"material {material.index} binding identity cannot change"
            )
        if classify_material_change(previous, material) != "binding":
            raise MayaModelAuthoringCoordinatorError(
                "apply_material_binding_patch requires binding-sensitive fields"
            )
        if outline_enabled is not None and type(outline_enabled) is not bool:
            raise MayaModelAuthoringCoordinatorError("material outline intent must be bool or None")
        structural_patch = getattr(self._materials, "apply_material_binding_patch", None)
        outline_patch = getattr(self._materials, "apply_material_outline", None)
        begin = getattr(self._backend, "begin_material_binding_patch", None)
        commit_owner = self._backend if outline_enabled is not None else self._metadata
        commit = getattr(commit_owner, "commit_material_binding_patch", None)
        if not callable(structural_patch) or not callable(begin) or not callable(commit):
            raise MayaModelAuthoringCoordinatorError(
                "apply_material_binding_patch requires narrow binding/metadata APIs; "
                "no Maya writes were performed"
            )
        if outline_enabled is not None and not callable(outline_patch):
            raise MayaModelAuthoringCoordinatorError(
                "material outline edit requires a DX11 outline binding API"
            )

        outline_target: Mapping[str, Any] | None = None

        def bind() -> MmdMaterialSpec:
            nonlocal outline_target
            result = structural_patch(model_root, previous, material)
            if not isinstance(result, MmdMaterialSpec):
                raise TypeError("material binding patch returned an invalid material")
            if outline_enabled is not None:
                outline_target = outline_patch(binding, outline_enabled, material.edge_size)
            return result

        begin_patch = begin
        commit_patch = commit
        if outline_enabled is not None:
            def begin_with_outline(root, node, old, new):
                return begin(root, node, old, new, outline_enabled)

            def commit_with_outline(root, node, target):
                return commit(root, node, target, outline_target)

            begin_patch = begin_with_outline
            commit_patch = commit_with_outline

        return self._execute_material_patch(
            model_root,
            "apply_material_binding_patch",
            previous,
            material,
            binding,
            begin_patch,
            bind,
            commit_patch,
        )

    def delete_material(self, model_root: str, index: int) -> MmdModelAuthoringSpec:
        """Delete and compact one binding when the structural API is available."""
        current = self._read_current(model_root, "delete_material")
        target = self._pure("delete_material", lambda: delete_material(current, index))
        structural_change = getattr(self._materials, "apply_material_spec_change", None)
        if not callable(structural_change):
            raise MayaModelAuthoringCoordinatorError(
                "delete_material requires apply_material_spec_change; no Maya writes were performed"
            )
        if not target.materials:
            raise MayaModelAuthoringCoordinatorError(
                "delete_material cannot remove the final material because no replacement binding exists"
            )
        replacement_shader = target.materials[0].binding_identity
        if not isinstance(replacement_shader, str) or not replacement_shader:
            raise MayaModelAuthoringCoordinatorError(
                "delete_material replacement has no Maya binding identity"
            )

        def bind() -> MmdModelAuthoringSpec:
            result = structural_change(
                model_root,
                current,
                target,
                replacement_shader,
            )
            if not isinstance(result, MmdModelAuthoringSpec):
                raise TypeError("apply_material_spec_change returned an invalid spec")
            return result

        return self._execute(model_root, "delete_material", target, bind)

    def reindex_materials(
        self,
        model_root: str,
        ordered_indices: Sequence[int],
    ) -> MmdModelAuthoringSpec:
        """Reorder material bindings and remap material-morph references atomically."""
        current = self._read_current(model_root, "reindex_materials")
        target = self._pure(
            "reindex_materials",
            lambda: reindex_materials(current, ordered_indices),
        )
        structural_change = getattr(self._materials, "apply_material_spec_change", None)
        if not callable(structural_change):
            raise MayaModelAuthoringCoordinatorError(
                "reindex_materials requires apply_material_spec_change; no Maya writes were performed"
            )

        def bind() -> MmdModelAuthoringSpec:
            result = structural_change(model_root, current, target, None)
            if not isinstance(result, MmdModelAuthoringSpec):
                raise TypeError("apply_material_spec_change returned an invalid spec")
            return result

        return self._execute(model_root, "reindex_materials", target, bind)

    def move_material(
        self,
        model_root: str,
        index: int,
        new_position: int,
    ) -> MmdModelAuthoringSpec:
        """Move one material while preserving the full-spec return contract."""
        current = self._read_current(model_root, "move_material")
        target = self._pure(
            "move_material",
            lambda: move_material_spec(current, index, new_position),
        )
        structural_change = getattr(self._materials, "apply_material_reindex", None)
        if not callable(structural_change):
            raise MayaModelAuthoringCoordinatorError(
                "move_material requires apply_material_reindex; no Maya writes were performed"
            )

        def bind() -> MmdModelAuthoringSpec:
            result = structural_change(model_root, current, target)
            if not isinstance(result, MmdModelAuthoringSpec):
                raise TypeError("material reindex binding operation returned an invalid spec")
            return result

        return self._execute(model_root, "move_material", target, bind)

    def move_material_fast(
        self,
        model_root: str,
        index: int,
        new_position: int,
    ) -> MaterialReindexResult:
        """Move one adjacent material without constructing a full model spec."""
        begin = getattr(self._backend, "begin_material_reindex", None)
        structural_change = getattr(self._materials, "apply_material_reindex_fast", None)
        commit = getattr(self._metadata, "commit_material_reindex", None)
        if not all(callable(item) for item in (begin, structural_change, commit)):
            raise MayaModelAuthoringCoordinatorError(
                "move_material_fast requires the narrow material reindex APIs; no Maya writes were performed"
            )
        return self._execute_material_reindex_fast(
            model_root,
            "move_material_fast",
            index,
            new_position,
            begin,
            structural_change,
            commit,
        )

    # Bone operations -----------------------------------------------------
    def register_selected_joint(self, model_root: str, joint: str) -> MmdBoneSpec:
        """Register one selected joint through a bone-only transaction."""
        canonical = self._canonical_existing_node(joint)
        prepare = getattr(self._bones, "prepare_selected_joint_registration", None)
        begin = getattr(self._backend, "begin_bone_register", None)
        commit = getattr(self._metadata, "commit_bone_register", None)
        writer = getattr(self._bones, "register_selected_joint", None)
        if not all(callable(item) for item in (prepare, begin, commit, writer)):
            raise MayaModelAuthoringCoordinatorError(
                "register_selected_joint requires narrow bone registration APIs; "
                "no Maya writes were performed"
            )
        try:
            bone = prepare(model_root, canonical, self._cmds)
        except Exception as exc:
            raise MayaModelAuthoringCoordinatorError(
                f"register_selected_joint preflight failed for root {model_root!r}: {exc}"
            ) from exc
        if not isinstance(bone, MmdBoneSpec) or bone.binding_identity != canonical:
            raise MayaModelAuthoringCoordinatorError(
                "selected-joint registration preflight returned an invalid bone"
            )

        def bind() -> MmdBoneSpec:
            result = writer(model_root, bone, self._cmds)
            if not isinstance(result, MmdBoneSpec):
                raise TypeError("selected-joint registration returned an invalid bone")
            return result

        return self._execute_bone_register(model_root, "register_selected_joint", bone, begin, bind, commit)

    def register_bone(self, model_root: str, bone: MmdBoneSpec) -> MmdBoneSpec:
        """Register an existing descendant joint through a narrow transaction."""
        if not isinstance(bone, MmdBoneSpec) or bone.binding_identity is None:
            raise MayaModelAuthoringCoordinatorError(
                "register_bone requires an MmdBoneSpec with binding_identity"
            )
        canonical = self._canonical_existing_node(bone.binding_identity)
        bound_bone = replace(bone, binding_identity=canonical)
        if bound_bone.tail_offset is None:
            default_tail = (
                (0.0, -1.0, 0.0)
                if bound_bone.flags & PmxBoneFlag.CONNECT_BONE
                else (0.0, 0.0, 0.0)
            )
            bound_bone = replace(bound_bone, tail_offset=default_tail)
        begin = getattr(self._backend, "begin_bone_register", None)
        commit = getattr(self._metadata, "commit_bone_register", None)
        writer = getattr(self._bones, "register_existing_joint", None)
        if not callable(begin) or not callable(commit) or not callable(writer):
            raise MayaModelAuthoringCoordinatorError(
                "register_bone requires narrow bone registration APIs; no Maya writes were performed"
            )

        def bind() -> MmdBoneSpec:
            writer(model_root, bound_bone, self._cmds)
            return bound_bone

        return self._execute_bone_register(
            model_root, "register_bone", bound_bone, begin, bind, commit
        )

    def capture_rest(
        self,
        model_root: str,
        index: int,
        joint: str,
    ) -> MmdBoneSpec:
        """Capture one selected joint through the narrow value transaction."""
        canonical = self._canonical_existing_node(joint)
        bone = self.read_bone_value(model_root, index, canonical)
        if bone.binding_identity is None:
            raise MayaModelAuthoringCoordinatorError(f"bone {index} has no Maya binding identity")
        if canonical != bone.binding_identity:
            raise MayaModelAuthoringCoordinatorError(
                f"joint {canonical!r} is not the binding for bone {index}"
            )
        model_scale = self._resolve_model_scale(model_root)
        try:
            position = self._bones.capture_rest_position(
                model_root,
                bone.binding_identity,
                model_scale,
                self._cmds,
            )
        except Exception as exc:
            raise MayaModelAuthoringCoordinatorError(
                f"capture_rest preflight failed for root {model_root!r}: {exc}"
            ) from exc
        target = replace(bone, rest_position=position)
        if classify_bone_change(bone, target) == "noop":
            return bone
        structural_patch = getattr(self._bones, "apply_bone_value_patch", None)
        begin = getattr(self._backend, "begin_bone_value_patch", None)
        commit = getattr(self._metadata, "commit_bone_value_patch", None)
        if not callable(structural_patch) or not callable(begin) or not callable(commit):
            raise MayaModelAuthoringCoordinatorError(
                "capture_rest requires narrow bone binding/metadata APIs; "
                "no Maya writes were performed"
            )

        def bind() -> MmdBoneSpec:
            result = structural_patch(model_root, bone, target, self._cmds)
            if not isinstance(result, MmdBoneSpec):
                raise TypeError("bone value patch binding operation returned an invalid bone")
            return result

        return self._execute_bone_value_patch(
            model_root,
            "capture_rest",
            bone,
            target,
            bone.binding_identity,
            begin,
            bind,
            commit,
        )

    def replace_bone(
        self,
        model_root: str,
        bone: MmdBoneSpec,
        world_position: Sequence[float],
    ) -> MmdModelAuthoringSpec:
        """Replace one bone and its Maya position in one undo transaction."""
        current = self._read_current(model_root, "replace_bone")
        if not isinstance(bone, MmdBoneSpec):
            raise MayaModelAuthoringCoordinatorError("replace_bone requires an MmdBoneSpec")
        previous = self._bone(current, bone.index)
        if bone.binding_identity is None:
            bone = replace(bone, binding_identity=previous.binding_identity)
        if bone.binding_identity != previous.binding_identity:
            raise MayaModelAuthoringCoordinatorError(
                f"bone {bone.index} binding identity cannot change"
            )
        if (
            isinstance(world_position, (str, bytes, bytearray))
            or not isinstance(world_position, Sequence)
            or len(world_position) != 3
        ):
            raise MayaModelAuthoringCoordinatorError(
                "world_position must contain exactly three finite numbers"
            )
        values: list[float] = []
        for value in world_position:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise MayaModelAuthoringCoordinatorError(
                    "world_position must contain exactly three finite numbers"
                )
            values.append(float(value))
        scale = self._resolve_model_scale(model_root)
        bone = replace(
            bone,
            rest_position=(values[0] / scale, values[1] / scale, -values[2] / scale),
        )
        target = self._pure("replace_bone", lambda: replace_bone_spec(current, bone))

        def bind() -> MmdModelAuthoringSpec:
            self._cmds.xform(
                bone.binding_identity,
                translation=values,
                worldSpace=True,
            )
            return target

        return self._execute(model_root, "replace_bone", target, bind)

    def replace_bone_semantic(
        self,
        model_root: str,
        bone: MmdBoneSpec,
    ) -> MmdModelAuthoringSpec:
        """Apply editable bone metadata without touching Maya transforms.

        Rest position and PMX tail semantics are preserved by the pure
        semantic helper; only the metadata backend participates in this
        transaction.  Reset is the sole operation that captures transforms.
        """
        current = self._read_current(model_root, "replace_bone_semantic")
        if not isinstance(bone, MmdBoneSpec):
            raise MayaModelAuthoringCoordinatorError(
                "replace_bone_semantic requires an MmdBoneSpec"
            )
        previous = self._bone(current, bone.index)
        if bone.binding_identity is None:
            bone = replace(bone, binding_identity=previous.binding_identity)
        if bone.binding_identity != previous.binding_identity:
            raise MayaModelAuthoringCoordinatorError(
                f"bone {bone.index} binding identity cannot change"
            )
        target = self._pure(
            "replace_bone_semantic",
            lambda: replace_bone_semantic_spec(current, bone),
        )
        return self._execute(model_root, "replace_bone_semantic", target, lambda: target)

    def _resolve_model_scale(self, model_root: str) -> float:
        if not callable(self._model_scale_resolver):
            raise MayaModelAuthoringCoordinatorError(
                "bone rest capture requires persisted model import scale; no Maya writes were performed"
            )
        try:
            scale = self._model_scale_resolver(model_root)
        except Exception as exc:
            raise MayaModelAuthoringCoordinatorError(
                f"model import scale resolution failed for root {model_root!r}: {exc}"
            ) from exc
        if (
            isinstance(scale, bool)
            or not isinstance(scale, (int, float))
            or not math.isfinite(float(scale))
            or float(scale) <= 0.0
        ):
            raise MayaModelAuthoringCoordinatorError("persisted model import scale must be positive")
        return float(scale)

    # Morph operations ----------------------------------------------------
    def create_morph(self, model_root: str, morph: MmdMorphSpec) -> MmdMorphSpec:
        """Create one empty-offset morph through a narrow transaction."""
        if not isinstance(morph, MmdMorphSpec):
            raise MayaModelAuthoringCoordinatorError("create_morph requires an MmdMorphSpec")
        if morph.binding_identity is not None or morph.offsets:
            raise MayaModelAuthoringCoordinatorError(
                "create_morph accepts only an unbound empty-offset morph"
            )
        begin = getattr(self._backend, "begin_morph_create", None)
        commit = getattr(self._metadata, "commit_morph_create", None)
        if not callable(begin) or not callable(commit):
            raise MayaModelAuthoringCoordinatorError(
                "create_morph requires narrow morph transaction APIs; no Maya writes were performed"
            )
        structural_change = getattr(self._morphs, "apply_morph_create", None)
        direct_change = False
        if not callable(structural_change):
            from mmd_tools.adapters.maya_morph_authoring import apply_morph_create

            structural_change = apply_morph_create
            direct_change = True

        new_index_holder: dict[str, Any] = {}

        def begin_transaction(_targets: tuple[Any, ...]) -> None:
            # The index is assigned by the backend's begin hook.  Capture it
            # without validating here so an invalid result is treated as a
            # post-begin mutation failure and is rolled back exactly once.
            new_index_holder["value"] = begin(model_root, morph)

        def mutate(_targets: tuple[Any, ...]) -> MmdMorphSpec:
            new_index = new_index_holder.get("value")
            if isinstance(new_index, bool) or not isinstance(new_index, int) or new_index < 0:
                raise TypeError("morph creation transaction returned an invalid index")
            candidate = replace(morph, index=new_index)
            if direct_change:
                result = structural_change(
                    model_root,
                    candidate,
                    self._cmds,
                    model_scale_resolver=self._model_scale_resolver,
                )
            else:
                result = structural_change(model_root, candidate, self._cmds)
            if (
                not isinstance(result, MmdMorphSpec)
                or result.index != new_index
                or result.binding_identity is None
                or result.offsets
            ):
                raise TypeError("morph creation binding operation returned an invalid morph")
            return result

        return self._run_transaction(
            model_root,
            "create_morph",
            (model_root,),
            begin_transaction,
            mutate,
            lambda result, _targets: commit(model_root, result),
        )

    def replace_morph(self, model_root: str, morph: MmdMorphSpec) -> MmdModelAuthoringSpec:
        """Replace one morph's semantic metadata and runtime binding state."""
        current = self._read_current(model_root, "replace_morph")
        target = self._pure("replace_morph", lambda: replace_morph_spec(current, morph))
        return self._execute_morph_change(model_root, "replace_morph", current, target)

    def replace_morph_offsets(
        self,
        model_root: str,
        index: int,
        offsets: Sequence[Mapping[str, Any]],
    ) -> MmdModelAuthoringSpec:
        """Replace and rebuild one morph's validated offsets."""
        current = self._read_current(model_root, "replace_morph_offsets")
        target = self._pure(
            "replace_morph_offsets",
            lambda: replace_morph_offsets_spec(current, index, offsets),
        )
        return self._execute_morph_change(model_root, "replace_morph_offsets", current, target)

    def delete_morph(self, model_root: str, index: int) -> MmdModelAuthoringSpec:
        """Delete one morph and compact all dependent morph references."""
        current = self._read_current(model_root, "delete_morph")
        target = self._pure("delete_morph", lambda: delete_morph_spec(current, index))
        return self._execute_morph_change(model_root, "delete_morph", current, target)

    def move_morph(self, model_root: str, index: int, new_position: int) -> MorphReindexResult:
        """Swap adjacent morph bindings through the dedicated narrow route.

        This UI operation intentionally does not read or write the complete
        model specification.  Arbitrary permutations remain available via
        :meth:`reindex_morphs`, which is the explicit full transaction.
        """
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
            or isinstance(new_position, bool)
            or not isinstance(new_position, int)
            or new_position < 0
            or abs(index - new_position) != 1
        ):
            raise MayaModelAuthoringCoordinatorError(
                "move_morph requires two adjacent non-negative indices; no Maya writes were performed"
            )
        begin = getattr(self._backend, "begin_morph_reindex", None)
        commit = getattr(self._metadata, "commit_morph_reindex", None)
        if not callable(begin) or not callable(commit):
            raise MayaModelAuthoringCoordinatorError(
                "move_morph requires narrow morph reindex transaction APIs; no Maya writes were performed"
            )
        structural_change = getattr(self._morphs, "apply_morph_reindex", None)
        if not callable(structural_change):
            from mmd_tools.adapters.maya_morph_authoring import apply_morph_reindex

            structural_change = apply_morph_reindex

        def bind() -> MorphReindexResult:
            result = structural_change(model_root, index, new_position, self._cmds)
            if not isinstance(result, MorphReindexResult):
                raise TypeError("morph reindex binding operation returned an invalid result")
            return result

        return self._execute_morph_reindex(
            model_root,
            "move_morph",
            index,
            new_position,
            begin,
            bind,
            commit,
        )

    def reindex_morphs(
        self,
        model_root: str,
        ordered_indices: Sequence[int],
    ) -> MmdModelAuthoringSpec:
        """Apply one explicit morph order and rebuild dependent references."""
        current = self._read_current(model_root, "reindex_morphs")
        target = self._pure(
            "reindex_morphs",
            lambda: reindex_morphs_spec(current, ordered_indices),
        )
        return self._execute_morph_change(model_root, "reindex_morphs", current, target)

    def _execute_morph_change(
        self,
        model_root: str,
        operation: str,
        current: MmdModelAuthoringSpec,
        target: MmdModelAuthoringSpec,
    ) -> MmdModelAuthoringSpec:
        if not callable(self._morphs):
            raise MayaModelAuthoringCoordinatorError(
                f"{operation} requires a Maya morph structural writer; no Maya writes were performed"
            )
        return self._execute(
            model_root,
            operation,
            target,
            lambda: self._morphs(model_root, current, target),
        )

    def reindex_bones(
        self,
        model_root: str,
        ordered_indices: Sequence[int],
    ) -> MmdModelAuthoringSpec:
        """Reindex scene bindings, display frames, physics, and bone morph refs."""
        current = self._read_current(model_root, "reindex_bones")
        target = self._pure(
            "reindex_bones",
            lambda: reindex_bones(current, ordered_indices),
        )

        def bind() -> MmdModelAuthoringSpec:
            self._bones.apply_bone_reindex(model_root, current, target, self._cmds)
            return target

        return self._execute(model_root, "reindex_bones", target, bind)

    def unregister_bone(self, model_root: str, index: int) -> MmdModelAuthoringSpec:
        """Remove an unreferenced joint binding and compact survivor indices."""
        current = self._read_current(model_root, "unregister_bone")
        removed = self._bone(current, index)
        if removed.binding_identity is None:
            raise MayaModelAuthoringCoordinatorError(f"bone {index} has no Maya binding identity")
        target = self._pure("unregister_bone", lambda: unregister_bone(current, index))
        intermediate = replace(
            current,
            bones=tuple(item for item in current.bones if item.index != index),
        )

        def bind() -> MmdModelAuthoringSpec:
            self._bones.unregister_existing_joint(model_root, removed.binding_identity, self._cmds)
            if intermediate.bones:
                self._bones.apply_bone_reindex(model_root, intermediate, target, self._cmds)
            return target

        return self._execute(model_root, "unregister_bone", target, bind)

    def plan_bone_reset(
        self,
        model_root: str,
        requested_order: Sequence[str] | None = None,
    ) -> BoneResetPlan:
        """Build an immutable scene-as-authority reset plan without writes."""
        current = self._read_current(model_root, "plan_bone_reset")
        scale = self._resolve_model_scale(model_root)
        planner = getattr(self._bones, "plan_bone_reset", None)
        if callable(planner):
            plan = planner(
                model_root,
                current,
                scale,
                self._cmds,
                requested_order=requested_order,
            )
        else:
            raise MayaModelAuthoringCoordinatorError(
                "bone reset requires a Maya preflight planner; no Maya writes were performed"
            )
        if not isinstance(plan, BoneResetPlan):
            raise MayaModelAuthoringCoordinatorError("bone preflight returned an invalid plan")
        return plan

    def reset_bones(
        self,
        model_root: str,
        plan: BoneResetPlan | None = None,
        requested_order: Sequence[str] | None = None,
    ) -> MmdModelAuthoringSpec:
        """Apply one complete add/remove/rest/reindex transaction atomically."""
        if plan is None:
            plan = self.plan_bone_reset(model_root, requested_order=requested_order)
        if not isinstance(plan, BoneResetPlan) or not plan.is_valid or plan.target_spec is None:
            blockers = () if not isinstance(plan, BoneResetPlan) else plan.blockers
            raise MayaModelAuthoringCoordinatorError(
                f"bone reset preflight blocked: {'; '.join(blockers) or 'invalid plan'}"
            )
        current = self._read_current(model_root, "reset_bones")
        if current.fingerprint() != plan.expected_fingerprint:
            raise MayaModelAuthoringCoordinatorError(
                "bone reset plan is stale; scene/spec changed after preflight"
            )
        apply_structure = getattr(self._bones, "apply_bone_reset_structure", None)
        if not callable(apply_structure):
            raise MayaModelAuthoringCoordinatorError(
                "bone reset requires an atomic Maya structural writer; no Maya writes were performed"
            )

        def bind() -> MmdModelAuthoringSpec:
            result = apply_structure(model_root, plan, self._cmds)
            if not isinstance(result, MmdModelAuthoringSpec):
                raise TypeError("bone reset structural operation returned an invalid spec")
            return result

        return self._execute(model_root, "reset_bones", plan.target_spec, bind)


    # Transaction core ----------------------------------------------------
    def _run_transaction(
        self,
        model_root: str,
        operation: str,
        target_identities: Sequence[Any],
        begin: Callable[[tuple[Any, ...]], Any],
        mutate: Callable[[tuple[Any, ...]], Any],
        verify_and_commit: Callable[[Any, tuple[Any, ...]], Any],
        validate_result: Callable[[Any, tuple[Any, ...]], Any] | None = None,
    ) -> Any:
        """Adapt callback-owned backend transactions to the common runner."""

        return TransactionRunner(
            operation,
            target_identities,
            begin=begin,
            mutate=mutate,
            validate_result=validate_result,
            verify_and_commit=verify_and_commit,
            rollback=lambda _targets: self._backend.rollback_write(model_root),
            error_factory=lambda failure: self._transaction_error(model_root, failure),
        ).run()

    @staticmethod
    def _transaction_error(model_root: str, failure: TransactionFailure) -> MayaModelAuthoringCoordinatorError:
        message = f"{failure.operation} failed for root {model_root!r}: {failure.original_error}"
        if failure.rollback_error is not None:
            message += f"; rollback failed: {failure.rollback_error}"
        return MayaModelAuthoringCoordinatorError(message)

    def _narrow_next_material_index(self, model_root: str, operation: str) -> int:
        """Read only registry material indices for a trailing allocation."""
        reader = getattr(self._metadata, "next_material_index", None)
        if not callable(reader):
            raise MayaModelAuthoringCoordinatorError(
                f"{operation} requires a narrow material index reader; no Maya writes were performed"
            )
        try:
            index = reader(model_root)
        except Exception as exc:
            raise MayaModelAuthoringCoordinatorError(
                f"{operation} material index read failed for root {model_root!r}: {exc}"
            ) from exc
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise MayaModelAuthoringCoordinatorError("narrow material index reader returned an invalid index")
        return index

    def _execute_material_create(
        self,
        model_root: str,
        operation: str,
        material: MmdMaterialSpec,
    ) -> MmdMaterialSpec:
        """Run create/duplicate without full spec reads or metadata hooks."""
        if not isinstance(material, MmdMaterialSpec):
            raise MayaModelAuthoringCoordinatorError("material create requires an MmdMaterialSpec")
        begin = getattr(self._backend, "begin_material_create", None)
        commit = getattr(self._metadata, "commit_material_create", None)
        if not callable(begin) or not callable(commit):
            raise MayaModelAuthoringCoordinatorError(
                f"{operation} requires narrow material transaction APIs; no Maya writes were performed"
            )
        structural = getattr(self._materials, "create_material", None)
        if not callable(structural):
            raise MayaModelAuthoringCoordinatorError(
                f"{operation} requires a material binding creation API; no Maya writes were performed"
            )
        def begin_transaction(_targets: tuple[Any, ...]) -> None:
            begin(model_root, material.index)

        def mutate(_targets: tuple[Any, ...]) -> MmdMaterialSpec:
            result = structural(model_root, material, narrow=True)
            if isinstance(result, tuple):
                bound = result[0]
            else:
                bound = result
            if not isinstance(bound, MmdMaterialSpec):
                raise TypeError("material creation binding operation returned an invalid material")
            return bound

        return self._run_transaction(
            model_root,
            operation,
            (model_root, material.index),
            begin_transaction,
            mutate,
            lambda result, _targets: commit(model_root, result),
        )

    def _execute_bone_register(
        self,
        model_root: str,
        operation: str,
        bone: MmdBoneSpec,
        begin: Callable[[str, MmdBoneSpec], Any],
        structural_write: Callable[[], MmdBoneSpec],
        commit: Callable[[str, MmdBoneSpec], Any],
    ) -> MmdBoneSpec:
        """Run selected-joint registration without full metadata hooks."""
        def begin_transaction(_targets: tuple[Any, ...]) -> None:
            begin(model_root, bone)

        def mutate(_targets: tuple[Any, ...]) -> MmdBoneSpec:
            bound = structural_write()
            if not isinstance(bound, MmdBoneSpec):
                raise TypeError("selected-joint registration returned an invalid bone")
            return bound

        return self._run_transaction(
            model_root,
            operation,
            (model_root, bone.binding_identity),
            begin_transaction,
            mutate,
            lambda result, _targets: commit(model_root, result),
        )

    def _execute(
        self,
        model_root: str,
        operation: str,
        target: MmdModelAuthoringSpec,
        structural_write: Callable[[], MmdModelAuthoringSpec],
    ) -> MmdModelAuthoringSpec:
        def begin_transaction(_targets: tuple[Any, ...]) -> None:
            self._backend.begin_write(model_root)

        def mutate(_targets: tuple[Any, ...]) -> MmdModelAuthoringSpec:
            bound_target = structural_write()
            if not isinstance(bound_target, MmdModelAuthoringSpec):
                raise TypeError("structural binding operation returned an invalid spec")
            return bound_target

        def verify_and_commit(bound_target: MmdModelAuthoringSpec, _targets: tuple[Any, ...]) -> None:
            self._backend.rebase_write_bindings(model_root, bound_target)
            payload = bound_target.to_mapping()
            self._backend.apply_model_metadata(model_root, payload["model"])
            self._backend.apply_bone_metadata(model_root, payload["bones"])
            self._backend.apply_material_metadata(model_root, payload["materials"])
            self._backend.apply_morph_metadata(model_root, payload["morphs"])
            self._backend.commit_write(model_root)

        return self._run_transaction(
            model_root,
            operation,
            (model_root,),
            begin_transaction,
            mutate,
            verify_and_commit,
        )

    def _execute_material_reindex_fast(
        self,
        model_root: str,
        operation: str,
        index: int,
        new_position: int,
        begin: Callable[[str, int, int], Any],
        structural_write: Callable[[str, int, int], MaterialReindexResult],
        commit: Callable[[str, MaterialReindexResult], Any],
    ) -> MaterialReindexResult:
        """Run adjacent material swap without full-spec metadata hooks."""
        def begin_transaction(_targets: tuple[Any, ...]) -> None:
            begin(model_root, index, new_position)

        def mutate(_targets: tuple[Any, ...]) -> MaterialReindexResult:
            result = structural_write(model_root, index, new_position)
            if not isinstance(result, MaterialReindexResult):
                raise TypeError("material reindex binding operation returned an invalid result")
            return result

        return self._run_transaction(
            model_root,
            operation,
            (model_root, index, new_position),
            begin_transaction,
            mutate,
            lambda result, _targets: commit(model_root, result),
        )

    def _execute_morph_reindex(
        self,
        model_root: str,
        operation: str,
        index: int,
        new_position: int,
        begin: Callable[[str, int, int], Any],
        structural_write: Callable[[], MorphReindexResult],
        commit: Callable[[str, MorphReindexResult], Any],
    ) -> MorphReindexResult:
        """Run adjacent morph swap without generic metadata hooks."""
        def begin_transaction(_targets: tuple[Any, ...]) -> None:
            begin(model_root, index, new_position)

        def mutate(_targets: tuple[Any, ...]) -> MorphReindexResult:
            result = structural_write()
            if not isinstance(result, MorphReindexResult):
                raise TypeError("morph reindex binding operation returned an invalid result")
            return result

        return self._run_transaction(
            model_root,
            operation,
            (model_root, index, new_position),
            begin_transaction,
            mutate,
            lambda result, _targets: commit(model_root, result),
        )

    def _execute_material_patch(
        self,
        model_root: str,
        operation: str,
        old_material: MmdMaterialSpec,
        new_material: MmdMaterialSpec,
        binding: str,
        begin: Callable[[str, str, MmdMaterialSpec, MmdMaterialSpec], Any],
        structural_write: Callable[[], MmdMaterialSpec],
        commit: Callable[[str, str, MmdMaterialSpec], Any],
    ) -> MmdMaterialSpec:
        """Run a selected-shader patch with no full metadata hooks."""
        def begin_transaction(_targets: tuple[Any, ...]) -> None:
            begin(model_root, binding, old_material, new_material)

        def mutate(_targets: tuple[Any, ...]) -> MmdMaterialSpec:
            bound_target = structural_write()
            if not isinstance(bound_target, MmdMaterialSpec):
                raise TypeError("material value patch binding operation returned an invalid material")
            return bound_target

        # Commit the caller's intended semantic value, not the structural
        # mutation result, which may contain Maya-normalized values.
        return self._run_transaction(
            model_root,
            operation,
            (model_root, binding),
            begin_transaction,
            mutate,
            lambda _result, _targets: commit(model_root, binding, new_material),
        )

    def _execute_bone_value_patch(
        self,
        model_root: str,
        operation: str,
        old_bone: MmdBoneSpec,
        new_bone: MmdBoneSpec,
        binding: str,
        begin: Callable[[str, str, MmdBoneSpec, MmdBoneSpec], Any],
        structural_write: Callable[[], MmdBoneSpec],
        commit: Callable[[str, str, MmdBoneSpec], Any],
    ) -> MmdBoneSpec:
        """Run a selected-bone value patch without full metadata hooks."""
        def begin_transaction(_targets: tuple[Any, ...]) -> None:
            begin(model_root, binding, old_bone, new_bone)

        def mutate(_targets: tuple[Any, ...]) -> MmdBoneSpec:
            bound_target = structural_write()
            if not isinstance(bound_target, MmdBoneSpec):
                raise TypeError("bone value patch binding operation returned an invalid bone")
            return bound_target

        return self._run_transaction(
            model_root,
            operation,
            (model_root, binding),
            begin_transaction,
            mutate,
            lambda _result, _targets: commit(model_root, binding, new_bone),
        )

    def _execute_morph_value_patch(
        self,
        model_root: str,
        operation: str,
        old_morph: MmdMorphSpec,
        new_morph: MmdMorphSpec,
        binding: str,
        begin: Callable[[str, str, MmdMorphSpec, MmdMorphSpec], Any],
        structural_write: Callable[[], MmdMorphSpec],
        commit: Callable[[str, str, MmdMorphSpec], Any],
    ) -> MmdMorphSpec:
        """Run a selected-morph patch without full metadata hooks."""
        def begin_transaction(_targets: tuple[Any, ...]) -> None:
            begin(model_root, binding, old_morph, new_morph)

        def mutate(_targets: tuple[Any, ...]) -> MmdMorphSpec:
            bound_target = structural_write()
            if not isinstance(bound_target, MmdMorphSpec):
                raise TypeError("morph value patch binding operation returned an invalid morph")
            return bound_target

        return self._run_transaction(
            model_root,
            operation,
            (model_root, binding),
            begin_transaction,
            mutate,
            lambda _result, _targets: commit(model_root, binding, new_morph),
        )

    def _read_current(self, model_root: str, operation: str) -> MmdModelAuthoringSpec:
        if not isinstance(model_root, str) or not model_root.strip():
            raise MayaModelAuthoringCoordinatorError("model_root must be a non-empty string")
        try:
            current = self._metadata.read_spec(model_root)
        except Exception as exc:
            raise MayaModelAuthoringCoordinatorError(
                f"{operation} read failed for root {model_root!r}: {exc}"
            ) from exc
        if type(current) is MmdModelAuthoringSpec:
            return current
        observed_type = type(current)
        expected_fields = tuple(MmdModelAuthoringSpec.__dataclass_fields__)
        observed_fields = tuple(getattr(observed_type, "__dataclass_fields__", ()))
        is_reload_generation = (
            observed_type.__module__ == MmdModelAuthoringSpec.__module__
            and observed_type.__qualname__ == MmdModelAuthoringSpec.__qualname__
            and is_dataclass(current)
            and observed_fields == expected_fields
        )
        if not is_reload_generation:
            raise MayaModelAuthoringCoordinatorError(
                "metadata adapter returned an invalid spec "
                f"({observed_type.__module__}.{observed_type.__qualname__})"
            )
        try:
            canonical = MmdModelAuthoringSpec.from_mapping(current.to_mapping())
        except Exception as exc:
            raise MayaModelAuthoringCoordinatorError(
                f"{operation} reload-spec normalization failed for root {model_root!r}: {exc}"
            ) from exc
        observed_type_id = id(observed_type)
        if observed_type_id not in _REHYDRATED_SPEC_TYPE_IDS:
            _REHYDRATED_SPEC_TYPE_IDS.add(observed_type_id)
            logger.warning(
                "Rehydrated authoring spec after module reload: actual=%s.%s "
                "actual_class_id=%s current_class_id=%s schema=%s fingerprint=%s",
                observed_type.__module__,
                observed_type.__qualname__,
                observed_type_id,
                id(MmdModelAuthoringSpec),
                canonical.schema_version,
                canonical.fingerprint(),
            )
        return canonical

    @staticmethod
    def _pure(operation: str, mutation: Callable[[], MmdModelAuthoringSpec]) -> MmdModelAuthoringSpec:
        try:
            target = mutation()
        except Exception as exc:
            raise MayaModelAuthoringCoordinatorError(f"{operation} preflight failed: {exc}") from exc
        if not isinstance(target, MmdModelAuthoringSpec):
            raise MayaModelAuthoringCoordinatorError(f"{operation} returned an invalid target spec")
        return target

    @staticmethod
    def _material(spec: MmdModelAuthoringSpec, index: int) -> MmdMaterialSpec:
        if isinstance(index, bool) or not isinstance(index, int):
            raise MayaModelAuthoringCoordinatorError("material_index must be an integer")
        material = next((item for item in spec.materials if item.index == index), None)
        if material is None:
            raise MayaModelAuthoringCoordinatorError(f"unknown material index {index}")
        return material

    @staticmethod
    def _bone(spec: MmdModelAuthoringSpec, index: int) -> MmdBoneSpec:
        if isinstance(index, bool) or not isinstance(index, int):
            raise MayaModelAuthoringCoordinatorError("bone index must be an integer")
        bone = next((item for item in spec.bones if item.index == index), None)
        if bone is None:
            raise MayaModelAuthoringCoordinatorError(f"unknown bone index {index}")
        return bone

    def _canonical_existing_node(self, node: str) -> str:
        if not self._cmds.object_exists(node):
            raise MayaModelAuthoringCoordinatorError(f"binding node does not exist: {node!r}")
        paths = self._cmds.ls(node, long=True) or []
        if len(paths) != 1 or not isinstance(paths[0], str) or not paths[0]:
            raise MayaModelAuthoringCoordinatorError(
                f"binding node is not one unique canonical identity: {node!r}"
            )
        return paths[0]

    @staticmethod
    def _require_methods(value: Any, methods: Sequence[str], label: str) -> None:
        missing = [method for method in methods if not callable(getattr(value, method, None))]
        if missing:
            raise TypeError(f"{label} is missing required methods: {missing!r}")


__all__ = ["MayaModelAuthoringCoordinatorError", "MayaModelAuthoringCoordinator"]
