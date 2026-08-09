"""Coordinate pure model mutations with Maya binding transactions.

This module is the structural transaction boundary for product authoring.  It
computes the complete immutable target before opening Maya's undo chunk, runs
binding operations without opening nested chunks, rebases the backend to the
strictly observed binding/index set, and finally applies the full semantic
specification with fingerprint verification.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
import math
from typing import Any, Callable

from mmd_tools.adapters import maya_bone_authoring
from mmd_tools.core.bone_authoring import (
    BoneResetPlan,
    capture_rest,
    register_bone,
    reindex_bones,
    replace_bone as replace_bone_spec,
    unregister_bone,
)
from mmd_tools.core.material_authoring import (
    create_material,
    delete_material,
    duplicate_material,
    replace_material,
)
from mmd_tools.core.model_authoring_spec import (
    MmdBoneSpec,
    MmdMaterialSpec,
    MmdModelAuthoringSpec,
    MmdMorphSpec,
)
from mmd_tools.core.morph_authoring import (
    create_morph as create_morph_spec,
    delete_morph as delete_morph_spec,
    move_morph as move_morph_spec,
    reindex_morphs as reindex_morphs_spec,
    replace_morph as replace_morph_spec,
    replace_morph_offsets as replace_morph_offsets_spec,
)
from mmd_tools.core.pmx_data.bone import PmxBoneFlag


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
            ("create_material", "resolve_material", "assign_material", "delete_material"),
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

    # Material operations -------------------------------------------------
    def create_material(
        self,
        model_root: str,
        targets: Sequence[str],
    ) -> MmdModelAuthoringSpec:
        """Create and assign one default material in the same transaction."""
        current = self._read_current(model_root, "create_material")
        self._require_optional_targets(targets)
        target = self._pure("create_material", lambda: create_material(current))
        created = max(target.materials, key=lambda item: item.index)
        unbound = replace(created, binding_identity=None)
        target = replace_material(target, unbound)

        def bind() -> MmdModelAuthoringSpec:
            bound, _shader, _shading_group = self._materials.create_material(model_root, unbound)
            if targets:
                self._materials.assign_material(model_root, bound, tuple(targets))
            return replace_material(target, bound)

        return self._execute(model_root, "create_material", target, bind)

    def duplicate_material(
        self,
        model_root: str,
        source_index: int,
        targets: Sequence[str],
    ) -> MmdModelAuthoringSpec:
        """Duplicate and assign a material in the same transaction."""
        current = self._read_current(model_root, "duplicate_material")
        self._require_optional_targets(targets)
        target = self._pure(
            "duplicate_material",
            lambda: duplicate_material(current, source_index),
        )
        duplicated = max(target.materials, key=lambda item: item.index)
        # Pure duplication deliberately copies all semantic fields.  A Maya
        # binding identity is not semantic material data and must be replaced.
        unbound = replace(duplicated, binding_identity=None)
        target = replace_material(target, unbound)

        def bind() -> MmdModelAuthoringSpec:
            bound, _shader, _shading_group = self._materials.create_material(model_root, unbound)
            if targets:
                self._materials.assign_material(model_root, bound, tuple(targets))
            return replace_material(target, bound)

        return self._execute(model_root, "duplicate_material", target, bind)

    def replace_material(
        self,
        model_root: str,
        material: MmdMaterialSpec,
    ) -> MmdModelAuthoringSpec:
        """Replace one material through a single binding/metadata transaction."""
        current = self._read_current(model_root, "replace_material")
        if type(material) is not MmdMaterialSpec:
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
            if type(result) is not MmdModelAuthoringSpec:
                raise TypeError("material binding replacement returned an invalid spec")
            return result

        return self._execute(model_root, "replace_material", target, bind)

    def assign_material(
        self,
        model_root: str,
        material_index: int,
        targets: Sequence[str],
    ) -> MmdModelAuthoringSpec:
        """Assign one existing binding to explicit mesh/face targets."""
        current = self._read_current(model_root, "assign_material")
        material = self._material(current, material_index)
        self._require_targets(targets)
        if material.binding_identity is None:
            raise MayaModelAuthoringCoordinatorError(
                f"material {material_index} has no Maya binding identity"
            )

        def bind() -> MmdModelAuthoringSpec:
            self._materials.assign_material(model_root, material, tuple(targets))
            return current

        return self._execute(model_root, "assign_material", current, bind)

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
            if type(result) is not MmdModelAuthoringSpec:
                raise TypeError("apply_material_spec_change returned an invalid spec")
            return result

        return self._execute(model_root, "delete_material", target, bind)

    # Bone operations -----------------------------------------------------
    def register_selected_joint(self, model_root: str, joint: str) -> MmdModelAuthoringSpec:
        """Register one selected joint with conservative default PMX metadata."""
        current = self._read_current(model_root, "register_selected_joint")
        canonical = self._canonical_existing_node(joint)
        parent_index = -1
        parents = self._cmds.list_relatives(canonical, parent=True, fullPath=True, type="joint") or []
        if len(parents) > 1:
            raise MayaModelAuthoringCoordinatorError("selected joint has multiple joint parents")
        if parents:
            parent = self._canonical_existing_node(parents[0])
            registered_parent = next(
                (bone for bone in current.bones if bone.binding_identity == parent),
                None,
            )
            if registered_parent is not None:
                parent_index = registered_parent.index
        name = canonical.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
        bone = MmdBoneSpec(
            name=name,
            name_english=name,
            index=len(current.bones),
            parent_index=parent_index,
            tail_offset=(0.0, 0.0, 0.0),
            rest_position=(0.0, 0.0, 0.0),
            binding_identity=canonical,
        )
        return self._register_bone(model_root, current, bone, "register_selected_joint")

    def register_bone(self, model_root: str, bone: MmdBoneSpec) -> MmdModelAuthoringSpec:
        """Register an existing descendant joint and persist its long identity."""
        current = self._read_current(model_root, "register_bone")
        if type(bone) is not MmdBoneSpec or bone.binding_identity is None:
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
        return self._register_bone(model_root, current, bound_bone, "register_bone")

    def _register_bone(
        self,
        model_root: str,
        current: MmdModelAuthoringSpec,
        bone: MmdBoneSpec,
        operation: str,
    ) -> MmdModelAuthoringSpec:
        target = self._pure(operation, lambda: register_bone(current, bone))
        registered = self._bone(target, max(item.index for item in target.bones))

        def bind() -> MmdModelAuthoringSpec:
            self._bones.register_existing_joint(model_root, registered, self._cmds)
            return target

        return self._execute(model_root, operation, target, bind)

    def capture_rest(
        self,
        model_root: str,
        index: int,
        joint: str,
    ) -> MmdModelAuthoringSpec:
        """Capture one joint's PMX-space rest position before starting writes."""
        current = self._read_current(model_root, "capture_rest")
        bone = self._bone(current, index)
        if bone.binding_identity is None:
            raise MayaModelAuthoringCoordinatorError(f"bone {index} has no Maya binding identity")
        canonical = self._canonical_existing_node(joint)
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
        target = self._pure("capture_rest", lambda: capture_rest(current, index, position))
        return self._execute(model_root, "capture_rest", target, lambda: target)

    def replace_bone(
        self,
        model_root: str,
        bone: MmdBoneSpec,
        world_position: Sequence[float],
    ) -> MmdModelAuthoringSpec:
        """Replace one bone and its Maya position in one undo transaction."""
        current = self._read_current(model_root, "replace_bone")
        if type(bone) is not MmdBoneSpec:
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
    def create_morph(self, model_root: str, morph: MmdMorphSpec) -> MmdModelAuthoringSpec:
        """Create one morph and its canonical Maya binding."""
        current = self._read_current(model_root, "create_morph")
        target = self._pure("create_morph", lambda: create_morph_spec(current, morph))
        return self._execute_morph_change(model_root, "create_morph", current, target)

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

    def move_morph(self, model_root: str, index: int, new_position: int) -> MmdModelAuthoringSpec:
        """Move one morph and remap dependent morph indices."""
        current = self._read_current(model_root, "move_morph")
        target = self._pure(
            "move_morph",
            lambda: move_morph_spec(current, index, new_position),
        )
        return self._execute_morph_change(model_root, "move_morph", current, target)

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
            if type(result) is not MmdModelAuthoringSpec:
                raise TypeError("bone reset structural operation returned an invalid spec")
            return result

        return self._execute(model_root, "reset_bones", plan.target_spec, bind)


    # Transaction core ----------------------------------------------------
    def _execute(
        self,
        model_root: str,
        operation: str,
        target: MmdModelAuthoringSpec,
        structural_write: Callable[[], MmdModelAuthoringSpec],
    ) -> MmdModelAuthoringSpec:
        started = False
        try:
            self._backend.begin_write(model_root)
            started = True
            bound_target = structural_write()
            if type(bound_target) is not MmdModelAuthoringSpec:
                raise TypeError("structural binding operation returned an invalid spec")
            self._backend.rebase_write_bindings(model_root, bound_target)
            payload = bound_target.to_mapping()
            self._backend.apply_model_metadata(model_root, payload["model"])
            self._backend.apply_bone_metadata(model_root, payload["bones"])
            self._backend.apply_material_metadata(model_root, payload["materials"])
            self._backend.apply_morph_metadata(model_root, payload["morphs"])
            self._backend.commit_write(model_root)
            started = False
            return bound_target
        except Exception as exc:
            if started:
                try:
                    self._backend.rollback_write(model_root)
                except Exception as rollback_exc:
                    raise MayaModelAuthoringCoordinatorError(
                        f"{operation} failed for root {model_root!r}: {exc}; "
                        f"rollback failed: {rollback_exc}"
                    ) from rollback_exc
            raise MayaModelAuthoringCoordinatorError(
                f"{operation} failed for root {model_root!r}: {exc}"
            ) from exc

    def _read_current(self, model_root: str, operation: str) -> MmdModelAuthoringSpec:
        if not isinstance(model_root, str) or not model_root.strip():
            raise MayaModelAuthoringCoordinatorError("model_root must be a non-empty string")
        try:
            current = self._metadata.read_spec(model_root)
        except Exception as exc:
            raise MayaModelAuthoringCoordinatorError(
                f"{operation} read failed for root {model_root!r}: {exc}"
            ) from exc
        if type(current) is not MmdModelAuthoringSpec:
            raise MayaModelAuthoringCoordinatorError("metadata adapter returned an invalid spec")
        return current

    @staticmethod
    def _pure(operation: str, mutation: Callable[[], MmdModelAuthoringSpec]) -> MmdModelAuthoringSpec:
        try:
            target = mutation()
        except Exception as exc:
            raise MayaModelAuthoringCoordinatorError(f"{operation} preflight failed: {exc}") from exc
        if type(target) is not MmdModelAuthoringSpec:
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

    @staticmethod
    def _require_targets(targets: Sequence[str]) -> None:
        if isinstance(targets, (str, bytes, bytearray)) or not isinstance(targets, Sequence) or not targets:
            raise MayaModelAuthoringCoordinatorError("targets must be a non-empty sequence")
        if any(not isinstance(target, str) or not target.strip() for target in targets):
            raise MayaModelAuthoringCoordinatorError("targets must contain non-empty strings")

    @staticmethod
    def _require_optional_targets(targets: Sequence[str]) -> None:
        if isinstance(targets, (str, bytes, bytearray)) or not isinstance(targets, Sequence):
            raise MayaModelAuthoringCoordinatorError("targets must be a sequence")
        if any(not isinstance(target, str) or not target.strip() for target in targets):
            raise MayaModelAuthoringCoordinatorError("targets must contain non-empty strings")

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
