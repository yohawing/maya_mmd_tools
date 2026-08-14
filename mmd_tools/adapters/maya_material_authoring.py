"""Maya material binding operations for immutable MMD material specs.

This adapter owns only the Maya node/binding side of material authoring.  It
never infers PMX semantics from a shader and never uses active selection;
callers provide an explicit model root and mesh/face targets.  Scene-level
undo transactions remain the responsibility of the caller.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
from typing import Any

from mmd_tools.core import model_registry
from mmd_tools.adapters.maya_material_shader_route import (
    MayaMaterialTextureSlotRoute,
    material_diffuse_route,
    material_shader_route,
)
from mmd_tools.core.material_authoring import classify_material_change
from mmd_tools.core.constants import (
    ATTR_MMD_AMBIENT_COLOR,
    ATTR_MMD_DIFFUSE_COLOR,
    ATTR_MMD_DRAW_FLAGS,
    ATTR_MMD_EDGE_COLOR,
    ATTR_MMD_EDGE_FLAG,
    ATTR_MMD_EDGE_SIZE,
    ATTR_MMD_MATERIAL,
    ATTR_MMD_MATERIAL_INDEX,
    ATTR_MMD_MATERIAL_NAME,
    ATTR_MMD_MATERIAL_NAME_EN,
    ATTR_MMD_MEMO,
    ATTR_MMD_ORIGINAL_TEXTURE_PATH,
    ATTR_MMD_SHARED_TOON_FLAG,
    ATTR_MMD_SHININESS,
    ATTR_MMD_SPHERE_MODE,
    ATTR_MMD_SPECULAR_COLOR,
    ATTR_MMD_SPHERE_PATH,
    ATTR_MMD_SPHERE_TEXTURE_INDEX,
    ATTR_MMD_TEXTURE_INDEX,
    ATTR_MMD_TOON_PATH,
    ATTR_MMD_TOON_TEXTURE_INDEX,
)
from mmd_tools.core.model_authoring_spec import MmdMaterialSpec, MmdModelAuthoringSpec
from mmd_tools.adapters.native_authoring_command import (
    NativeAuthoringCommandGateway,
    NativeCommandUnavailable,
)


REGISTRY_CATEGORY_MATERIAL = "material"


ATTR_MMD_TEXTURE_PATH = "mmd_texture_path"
ATTR_MMD_RESOLVED_TEXTURE_PATH = "mmd_resolved_texture_path"
ATTR_MMD_RESOLVED_SPHERE_TEXTURE_PATH = "mmd_resolved_sphere_texture_path"
ATTR_MMD_RESOLVED_TOON_TEXTURE_PATH = "mmd_resolved_toon_texture_path"
ATTR_MMD_TOON_TEXTURE_PATH = ATTR_MMD_TOON_PATH
ATTR_MMD_DIFFUSE_ALPHA = "mmd_diffuse_alpha"
ATTR_MMD_EDGE_ALPHA = "mmd_edge_alpha"
ATTR_MMD_MATERIAL_MORPH_OFFSETS = "mmd_material_morph_offsets_json"
_MATERIAL_OUTLINE_ATTRS = (
    "technique",
    "EdgeSize",
    "mmd_shader_outline_enabled",
    "mmdDoubleSided",
    "mmdTransparencyMode",
)


class MayaMaterialAuthoringError(RuntimeError):
    """Raised when a material binding operation cannot fail closed."""


@dataclass(frozen=True)
class MaterialReindexResult:
    """Result of a narrow adjacent material swap."""

    first_index: int
    second_index: int


class MayaMaterialAuthoring:
    """Create, resolve, assign, and delete MMD material shader bindings."""

    def __init__(
        self,
        cmds_adapter: Any,
        registry_api: Any = model_registry,
        *,
        runtime_rebuilders: Mapping[str, Any] | None = None,
        native_queue_reindexer: Any | None = None,
        native_authoring_gateway: Any | None = None,
    ) -> None:
        self._cmds = cmds_adapter
        self._registry = registry_api
        if runtime_rebuilders is None:
            self._runtime_rebuilders = {"material": self._default_material_rebuilder}
        else:
            self._runtime_rebuilders = dict(runtime_rebuilders)
        self._native_queue_reindexer = native_queue_reindexer
        self._native_authoring_gateway = (
            native_authoring_gateway
            if native_authoring_gateway is not None
            else NativeAuthoringCommandGateway(cmds_adapter)
        )

    def create_material(
        self,
        model_root: str,
        material: MmdMaterialSpec,
        *,
        narrow: bool = False,
    ) -> tuple[MmdMaterialSpec, str, str]:
        """Create or resolve a standardSurface shader and its shading group.

        The returned material is a fresh spec carrying the canonical shader
        identity, so callers can persist that binding in their semantic spec.
        """
        root = self._require_root(model_root)
        self._require_material(material)
        existing = None if narrow else self._resolve_material(root, material)
        if existing is not None:
            shader, shading_group = existing
            self._bind_texture_graphs(shader, material)
            self._rebuild_material_morph_graph(root)
            return replace(material, binding_identity=shader), shader, shading_group

        shader: str | None = None
        shading_group: str | None = None
        try:
            # Keep Maya node names ASCII and deterministic; semantic Unicode
            # names remain lossless in the canonical attributes below.
            name = f"mmdMaterial_{material.index}"
            shader = self._canonical_node(
                str(self._call("shading_node", "standardSurface", asShader=True, name=name))
            )
            shading_group = str(
                self._call(
                    "sets",
                    renderable=True,
                    noSurfaceShader=True,
                    empty=True,
                    name=f"{name}_SG",
                )
            )
            self._call("connect_attr", f"{shader}.outColor", f"{shading_group}.surfaceShader", force=True)
            bound_material = replace(material, binding_identity=shader)
            # A duplicate may carry a resolved main texture; binding that one
            # local file node is part of cloning the selected shader.  The
            # narrow route still skips the model-wide Material Morph rebuild.
            self._write_material_attrs(shader, bound_material, bind_texture_graph=True)
            registry = self._registry.ensure_model_registry(root)
            self._registry.register_model_members(
                registry,
                REGISTRY_CATEGORY_MATERIAL,
                [shader],
            )
            if not narrow:
                self._rebuild_material_morph_graph(root)
            return bound_material, shader, shading_group
        except Exception as exc:
            if shader:
                try:
                    registry = self._registry.ensure_model_registry(root)
                    self._registry.unregister_model_members(
                        registry,
                        REGISTRY_CATEGORY_MATERIAL,
                        [shader],
                    )
                except Exception:
                    pass
            for node in (shading_group, shader):
                if node:
                    try:
                        self._call("delete", node)
                    except Exception:
                        pass
            raise MayaMaterialAuthoringError(
                f"failed to create material {material.index} under root {root!r}: {exc}"
            ) from exc

    def resolve_material(self, model_root: str, material: MmdMaterialSpec) -> tuple[str, str] | None:
        """Resolve an existing shader only when binding identity and index agree."""
        root = self._require_root(model_root)
        self._require_material(material)
        return self._resolve_material(root, material)

    def replace_material(
        self,
        model_root: str,
        old_spec: MmdModelAuthoringSpec,
        new_spec: MmdModelAuthoringSpec,
    ) -> MmdModelAuthoringSpec:
        """Replace material fields while preserving the canonical Maya binding.

        The caller computes ``new_spec`` before opening its transaction.  This
        method writes the complete existing shader attributes and refreshes all
        registry-owned material morph evaluators in that same transaction.
        """
        return self.apply_material_spec_change(
            model_root,
            old_spec,
            new_spec,
            allow_material_edits=True,
        )

    def apply_material_value_patch(
        self,
        model_root: str,
        old_material: MmdMaterialSpec,
        new_material: MmdMaterialSpec,
    ) -> MmdMaterialSpec:
        """Write only patch-safe values on the one changed shader binding.

        This path intentionally does not bind textures, rebuild Material
        Morph runtime nodes, enumerate other bindings, or write scene
        metadata.  The coordinator owns the surrounding undo transaction and
        the narrow metadata backend verifies the selected attributes after
        these writes.
        """
        root = self._require_root(model_root)
        self._require_material(old_material)
        self._require_material(new_material)
        if old_material.binding_identity != new_material.binding_identity:
            raise MayaMaterialAuthoringError("material value patch cannot change binding identity")
        route = classify_material_change(old_material, new_material)
        if route == "noop":
            return new_material
        if route != "value":
            raise MayaMaterialAuthoringError(
                "material value patch contains binding-sensitive fields"
            )
        binding = old_material.binding_identity
        if not isinstance(binding, str) or not binding:
            raise MayaMaterialAuthoringError("material value patch requires a binding identity")
        resolved = self._resolve_material_value_binding(root, old_material)
        if resolved is None or resolved[0] != binding:
            raise MayaMaterialAuthoringError(
                f"material {old_material.index} binding is not resolvable under root {root!r}"
            )
        self._write_material_value_attrs(binding, old_material, new_material)
        return new_material

    def try_apply_native_material_value_patch(
        self,
        model_root: str,
        old_material: MmdMaterialSpec,
        new_material: MmdMaterialSpec,
    ) -> MmdMaterialSpec | None:
        """Use the dedicated native command, or return ``None`` when unavailable.

        Python retains semantic classification, binding resolution, shader
        route policy, and fixed write-set expansion.  A registered command
        failure is deliberately propagated and never falls back after a
        possibly attempted mutation.
        """
        mode = os.environ.get("MMD_AUTHORING_MATERIAL_VALUE_MODE", "auto").strip().lower()
        if mode not in {"auto", "native", "python"}:
            raise MayaMaterialAuthoringError(
                "MMD_AUTHORING_MATERIAL_VALUE_MODE must be auto, native, or python"
            )
        if mode == "python":
            return None
        root = self._require_root(model_root)
        self._require_material(old_material)
        self._require_material(new_material)
        if classify_material_change(old_material, new_material) != "value":
            raise MayaMaterialAuthoringError("native material value command requires a value patch")
        binding = old_material.binding_identity
        if not isinstance(binding, str) or not binding:
            raise MayaMaterialAuthoringError("native material value patch requires a binding identity")
        if new_material.binding_identity != binding or new_material.index != old_material.index:
            raise MayaMaterialAuthoringError("native material value patch cannot change identity")
        resolved = self._resolve_material_value_binding(root, old_material)
        if resolved is None or resolved[0] != binding:
            raise MayaMaterialAuthoringError(
                f"material {old_material.index} binding is not resolvable under root {root!r}"
            )
        updates = self._material_value_updates(binding, old_material, new_material)
        if not bool(self._call("undo_info", query=True, state=True)):
            raise MayaMaterialAuthoringError(
                "Maya undo must be enabled for native material value patches"
            )
        try:
            self._native_authoring_gateway.set_material_values(
                root,
                binding,
                old_material.index,
                updates,
            )
        except NativeCommandUnavailable:
            if mode == "native":
                raise
            return None
        return new_material

    def try_apply_native_material_outline_patch(
        self,
        model_root: str,
        old_material: MmdMaterialSpec,
        new_material: MmdMaterialSpec,
        outline_enabled: bool,
    ) -> MmdMaterialSpec | None:
        """Use the dedicated native DX11 outline command when registered."""
        mode = os.environ.get("MMD_AUTHORING_MATERIAL_OUTLINE_MODE", "auto").strip().lower()
        if mode not in {"auto", "native", "python"}:
            raise MayaMaterialAuthoringError(
                "MMD_AUTHORING_MATERIAL_OUTLINE_MODE must be auto, native, or python"
            )
        if mode == "python":
            return None
        if type(outline_enabled) is not bool:
            raise MayaMaterialAuthoringError("material outline intent must be a bool")
        root = self._require_root(model_root)
        self._require_material(old_material)
        self._require_material(new_material)
        route = classify_material_change(old_material, new_material)
        if route not in {"value", "noop"}:
            raise MayaMaterialAuthoringError(
                "native material outline command requires a value or noop patch"
            )
        binding = old_material.binding_identity
        if not isinstance(binding, str) or not binding:
            raise MayaMaterialAuthoringError("native material outline patch requires a binding identity")
        if new_material.binding_identity != binding or new_material.index != old_material.index:
            raise MayaMaterialAuthoringError("native material outline patch cannot change identity")
        resolved = self._resolve_material_value_binding(root, old_material)
        if resolved is None or resolved[0] != binding:
            raise MayaMaterialAuthoringError(
                f"material {old_material.index} binding is not resolvable under root {root!r}"
            )
        if self._call("node_type", binding) != "dx11Shader":
            raise MayaMaterialAuthoringError("material outline intent requires a dx11Shader")
        outline_preimage = self._capture_material_outline(binding)
        from mmd_tools.converters.mesh_converter import expected_shader_outline_preview

        transparency = outline_preimage["mmdTransparencyMode"]
        outline_target = expected_shader_outline_preview(
            str(outline_preimage["technique"]["value"] or ""),
            transparency["value"] if transparency["exists"] else None,
            new_material.draw_flags,
            outline_enabled,
            new_material.edge_size,
            edge_size_exists=bool(outline_preimage["EdgeSize"]["exists"]),
        )
        updates = (
            self._material_value_updates(binding, old_material, new_material)
            if route == "value"
            else []
        )
        if not bool(self._call("undo_info", query=True, state=True)):
            raise MayaMaterialAuthoringError(
                "Maya undo must be enabled for native material outline patches"
            )
        try:
            self._native_authoring_gateway.set_material_outline(
                root,
                binding,
                old_material.index,
                updates,
                outline_preimage,
                outline_target,
            )
        except NativeCommandUnavailable:
            if mode == "native":
                raise
            return None
        return new_material

    def _capture_material_outline(self, shader: str) -> dict[str, dict[str, Any]]:
        """Capture the fixed DX11 policy fingerprint for a TOCTOU precondition."""
        result = {}
        for attr in _MATERIAL_OUTLINE_ATTRS:
            exists = self._has_attr(shader, attr)
            result[attr] = {
                "exists": exists,
                "value": self._get_attr(shader, attr) if exists else None,
            }
        return result

    def _material_value_updates(
        self,
        shader: str,
        old: MmdMaterialSpec,
        new: MmdMaterialSpec,
    ) -> list[dict[str, Any]]:
        """Expand semantic intent to the dedicated command's fixed fields."""
        old_mapping = old.to_mapping()
        new_mapping = new.to_mapping()
        changed = {field for field in old_mapping if old_mapping[field] != new_mapping[field]}
        updates: list[dict[str, Any]] = []

        def add(field: str, value: Any) -> None:
            updates.append({"field": field, "value": value})

        if "name" in changed:
            add("name", new.name)
        if "name_english" in changed:
            add("name_english", new.name_english)
        if "diffuse" in changed:
            add("diffuse_color", list(new.diffuse[:3]))
            add("diffuse_alpha", new.diffuse[3])
            route = material_diffuse_route(
                str(self._call("node_type", shader)),
                has_main_texture=bool(old.resolved_texture_path or old.texture_path),
            )
            if route is not None:
                add("viewport_diffuse", list(new.diffuse[:3]))
        if "specular" in changed:
            add("specular", list(new.specular))
        if "specular_coefficient" in changed:
            add("specular_coefficient", new.specular_coefficient)
        if "ambient" in changed:
            add("ambient", list(new.ambient))
        if "draw_flags" in changed:
            add("draw_flags", new.draw_flags)
            add("edge_flag", bool(new.draw_flags & 0x10))
        if "edge_color" in changed:
            add("edge_color", list(new.edge_color[:3]))
            add("edge_alpha", new.edge_color[3])
        if "edge_size" in changed:
            add("edge_size", new.edge_size)
        if "memo" in changed:
            add("memo", new.memo)
        return updates

    def apply_material_binding_patch(
        self,
        model_root: str,
        old_material: MmdMaterialSpec,
        new_material: MmdMaterialSpec,
    ) -> MmdMaterialSpec:
        """Replace one selected shader, including its texture binding fields."""
        root = self._require_root(model_root)
        self._require_material(old_material)
        self._require_material(new_material)
        if old_material.binding_identity != new_material.binding_identity:
            raise MayaMaterialAuthoringError("material binding patch cannot change binding identity")
        if old_material.index != new_material.index:
            raise MayaMaterialAuthoringError("material binding patch cannot change material index")
        if classify_material_change(old_material, new_material) != "binding":
            raise MayaMaterialAuthoringError("material binding patch requires binding-sensitive fields")
        binding = old_material.binding_identity
        if not isinstance(binding, str) or not binding:
            raise MayaMaterialAuthoringError("material binding patch requires a binding identity")
        resolved = self._resolve_material_value_binding(root, old_material)
        if resolved is None or resolved[0] != binding:
            raise MayaMaterialAuthoringError(
                f"material {old_material.index} binding is not resolvable under root {root!r}"
            )
        self._write_material_attrs(binding, new_material)
        # Texture writes can replace the destination of an existing material
        # morph evaluator. Restore the runtime route inside the same undo chunk.
        self._rebuild_material_morph_graph(root)
        return new_material

    def apply_material_outline(
        self,
        shader: str,
        enabled: bool,
        edge_size: float,
    ) -> Mapping[str, Any]:
        """Apply the shared DX11 outline writer and return its exact attr state."""
        if type(enabled) is not bool:
            raise MayaMaterialAuthoringError("material outline intent must be a bool")
        if self._call("node_type", shader) != "dx11Shader":
            raise MayaMaterialAuthoringError("material outline intent requires a dx11Shader")
        from mmd_tools.converters.mesh_converter import apply_shader_outline

        apply_shader_outline(shader, enabled, edge_size, cmds_module=self._cmds)
        result = {}
        for attr in _MATERIAL_OUTLINE_ATTRS:
            exists = self._has_attr(shader, attr)
            result[attr] = {
                "exists": exists,
                "value": self._get_attr(shader, attr) if exists else None,
            }
        return result

    def _resolve_material_value_binding(
        self,
        root: str,
        material: MmdMaterialSpec,
    ) -> tuple[str, str] | None:
        """Resolve only the selected shader without creating registry metadata."""
        members = self._registry.list_model_registry_members(root, REGISTRY_CATEGORY_MATERIAL) or []
        shader = self._canonical_node(str(material.binding_identity))
        matches: list[str] = []
        if shader in {str(member) for member in members}:
            index = self._get_attr(shader, ATTR_MMD_MATERIAL_INDEX)
            if type(index) is int and index == material.index:
                matches.append(shader)
        if len(matches) > 1:
            raise MayaMaterialAuthoringError(
                f"material {material.index} has ambiguous bindings under root {root!r}"
            )
        if not matches:
            return None
        shader = matches[0]
        shading_groups = list(self._call("list_connections", shader, type="shadingEngine") or [])
        if len(shading_groups) != 1:
            raise MayaMaterialAuthoringError(f"shader {shader!r} must have exactly one shading group")
        return shader, str(shading_groups[0])

    def _write_material_value_attrs(
        self,
        shader: str,
        old: MmdMaterialSpec,
        new: MmdMaterialSpec,
    ) -> None:
        """Write only changed semantic/final value attributes on ``shader``."""
        old_mapping = old.to_mapping()
        new_mapping = new.to_mapping()
        changed = {
            field
            for field in old_mapping
            if old_mapping[field] != new_mapping[field]
        }
        if "name" in changed:
            self._set_attr(shader, ATTR_MMD_MATERIAL_NAME, new.name, "string")
        if "name_english" in changed:
            self._set_attr(shader, ATTR_MMD_MATERIAL_NAME_EN, new.name_english, "string")
        if "diffuse" in changed:
            self._set_attr(shader, ATTR_MMD_DIFFUSE_COLOR, new.diffuse[:3], "double3")
            self._set_attr(shader, ATTR_MMD_DIFFUSE_ALPHA, new.diffuse[3], "double")
            route = material_diffuse_route(
                str(self._call("node_type", shader)),
                has_main_texture=bool(old.resolved_texture_path or old.texture_path),
            )
            if route is not None:
                self._set_attr(
                    shader,
                    route.diffuse_attribute,
                    new.diffuse[:3],
                    route.diffuse_attribute_type,
                )
        if "specular" in changed:
            self._set_attr(shader, ATTR_MMD_SPECULAR_COLOR, new.specular, "double3")
        if "specular_coefficient" in changed:
            self._set_attr(shader, ATTR_MMD_SHININESS, new.specular_coefficient, "double")
        if "ambient" in changed:
            self._set_attr(shader, ATTR_MMD_AMBIENT_COLOR, new.ambient, "double3")
        if "draw_flags" in changed:
            self._set_attr(shader, ATTR_MMD_DRAW_FLAGS, new.draw_flags, "long")
            self._set_attr(shader, ATTR_MMD_EDGE_FLAG, bool(new.draw_flags & 0x10), "bool")
        if "edge_color" in changed:
            self._set_attr(shader, ATTR_MMD_EDGE_COLOR, new.edge_color[:3], "double3")
            self._set_attr(shader, ATTR_MMD_EDGE_ALPHA, new.edge_color[3], "double")
        if "edge_size" in changed:
            self._set_attr(shader, ATTR_MMD_EDGE_SIZE, new.edge_size, "double")
        if "memo" in changed:
            self._set_attr(shader, ATTR_MMD_MEMO, new.memo, "string")

    def assign_material(
        self,
        model_root: str,
        material: MmdMaterialSpec,
        targets: Sequence[str],
    ) -> tuple[str, str]:
        """Assign a resolved material to explicit mesh/face targets below ``model_root``."""
        root = self._require_root(model_root)
        self._require_material(material)
        if isinstance(targets, (str, bytes, bytearray)) or not isinstance(targets, Sequence) or not targets:
            raise MayaMaterialAuthoringError("targets must be a non-empty sequence")
        binding = self._resolve_material(root, material)
        if binding is None:
            raise MayaMaterialAuthoringError(
                f"material {material.index} is not registered under root {root!r}"
            )
        shader, shading_group = binding
        validated_targets = [self._validate_target(root, target) for target in targets]
        try:
            for target in validated_targets:
                self._call("sets", target, e=True, forceElement=shading_group)
        except Exception as exc:
            raise MayaMaterialAuthoringError(
                f"failed to assign material {material.index} under root {root!r}: {exc}"
            ) from exc
        return shader, shading_group

    def delete_material(
        self,
        model_root: str,
        material: MmdMaterialSpec | str,
        replacement_shader: str,
    ) -> None:
        """Reassign old shading-group members, then delete old shader/SG.

        ``replacement_shader`` is an explicit existing shading group (or a
        shader node with exactly one connected shading group).  Deletion is
        rejected unless replacement is supplied and valid.
        """
        root = self._require_root(model_root)
        if not isinstance(replacement_shader, str) or not replacement_shader.strip():
            raise MayaMaterialAuthoringError("replacement_shader must be a non-empty string")
        old_shader, old_sg = self._resolve_delete_target(root, material)
        replacement_sg = self._resolve_replacement_sg(replacement_shader)
        if replacement_sg == old_sg:
            raise MayaMaterialAuthoringError("replacement_shader must differ from deleted material")
        members = list(self._call("sets", old_sg, query=True) or [])
        validated_members = [self._validate_target(root, member) for member in members]
        try:
            for member in validated_members:
                self._call("sets", member, e=True, forceElement=replacement_sg)
            registry = self._registry.ensure_model_registry(root)
            self._registry.unregister_model_members(
                registry,
                REGISTRY_CATEGORY_MATERIAL,
                [old_shader],
            )
            self._call("disconnect_attr", f"{old_shader}.outColor", f"{old_sg}.surfaceShader")
            self._call("delete", old_sg)
            self._call("delete", old_shader)
            self._rebuild_material_morph_graph(root)
        except Exception as exc:
            raise MayaMaterialAuthoringError(
                f"failed to delete material under root {root!r}: {exc}"
            ) from exc

    def apply_material_spec_change(
        self,
        model_root: str,
        old_spec: MmdModelAuthoringSpec,
        new_spec: MmdModelAuthoringSpec,
        replacement_shader: str | None = None,
        *,
        allow_material_edits: bool = False,
    ) -> MmdModelAuthoringSpec:
        """Apply a validated material edit/reindex/delete plan to Maya bindings.

        The pure material authoring layer must produce ``new_spec`` first.
        This method then updates survivor indices, remaps material-morph raw
        JSON, and optionally replaces/deletes one removed material binding.
        It never opens an undo chunk; callers own the surrounding transaction.
        """
        root = self._require_root(model_root)
        self._require_model_spec(old_spec, "old_spec")
        self._require_model_spec(new_spec, "new_spec")
        self._validate_material_spec_shape(
            old_spec,
            new_spec,
            allow_material_edits=allow_material_edits,
        )

        registry = self._registry.ensure_model_registry(root)
        registry_members = self._registry.list_model_registry_members(
            root, REGISTRY_CATEGORY_MATERIAL
        ) or []
        owned_materials = {
            self._canonical_node(str(member)) for member in registry_members
        }
        old_by_binding = self._material_bindings(old_spec)
        if owned_materials != set(old_by_binding):
            raise MayaMaterialAuthoringError(
                f"material registry membership does not exactly match old_spec under root {root!r}"
            )
        new_by_binding = self._material_bindings(new_spec)
        unknown = set(new_by_binding) - set(old_by_binding)
        if unknown:
            raise MayaMaterialAuthoringError(
                f"new_spec contains unknown material bindings: {sorted(unknown)!r}"
            )
        deleted = sorted(set(old_by_binding) - set(new_by_binding))
        if len(deleted) > 1:
            raise MayaMaterialAuthoringError(
                "one material binding may be deleted per structural transaction"
            )

        morph_updates = self._material_morph_updates(root, old_spec, new_spec)
        old_shader: str | None = None
        old_shading_group: str | None = None
        replacement_sg: str | None = None
        validated_members: list[str] = []
        if deleted:
            if not isinstance(replacement_shader, str) or not replacement_shader.strip():
                raise MayaMaterialAuthoringError(
                    "replacement_shader is required when deleting a material binding"
                )
            deleted_binding = deleted[0]
            old_material = old_by_binding[deleted_binding]
            binding = self._resolve_material(root, old_material)
            if binding is None:
                raise MayaMaterialAuthoringError(
                    f"deleted material {old_material.index} is not resolvable"
                )
            old_shader, old_shading_group = binding
            replacement_sg = self._resolve_replacement_sg(replacement_shader)
            if replacement_sg == old_shading_group:
                raise MayaMaterialAuthoringError(
                    "replacement_shader must differ from deleted material"
                )
            members = list(self._call("sets", old_shading_group, query=True) or [])
            validated_members = [
                self._validate_target(root, member) for member in members
            ]

        try:
            for binding, material in new_by_binding.items():
                prior = old_by_binding[binding]
                if allow_material_edits and self._material_fields_changed(prior, material):
                    self._write_material_attrs(binding, material)
                elif material.index != prior.index:
                    self._set_attr(
                        binding,
                        ATTR_MMD_MATERIAL_INDEX,
                        material.index,
                        "long",
                    )
            for node, payload in morph_updates:
                self._set_attr(
                    node,
                    ATTR_MMD_MATERIAL_MORPH_OFFSETS,
                    payload,
                    "string",
                )
            if deleted and old_shader is not None and old_shading_group is not None:
                for member in validated_members:
                    self._call("sets", member, e=True, forceElement=replacement_sg)
                self._registry.unregister_model_members(
                    registry,
                    REGISTRY_CATEGORY_MATERIAL,
                    [old_shader],
                )
                self._call(
                    "disconnect_attr",
                    f"{old_shader}.outColor",
                    f"{old_shading_group}.surfaceShader",
                )
                self._call("delete", old_shading_group)
                self._call("delete", old_shader)
            self._rebuild_material_morph_graph(root)
        except Exception as exc:
            raise MayaMaterialAuthoringError(
                f"failed to apply material structural change under root {root!r}: {exc}"
            ) from exc
        return new_spec

    def apply_material_reindex(
        self,
        model_root: str,
        old_spec: MmdModelAuthoringSpec,
        new_spec: MmdModelAuthoringSpec,
    ) -> MmdModelAuthoringSpec:
        """Apply one adjacent material swap without rebuilding material graphs.

        Only the two survivor shader ``mmd_material_index`` attributes, the
        Material Morph JSON attributes whose offsets changed, and native
        ``mmdRenderShape`` queue ordering are touched.  The coordinator owns
        the surrounding Maya undo chunk and rolls these writes back if any
        operation fails.
        """
        root = self._require_root(model_root)
        self._require_model_spec(old_spec, "old_spec")
        self._require_model_spec(new_spec, "new_spec")
        first_index, second_index = self._validate_adjacent_reindex(old_spec, new_spec)

        registry_members = self._registry.list_model_registry_members(
            root, REGISTRY_CATEGORY_MATERIAL
        ) or []
        owned_materials = {self._canonical_node(str(member)) for member in registry_members}
        old_by_binding = self._material_bindings(old_spec)
        if owned_materials != set(old_by_binding):
            raise MayaMaterialAuthoringError(
                f"material registry membership does not exactly match old_spec under root {root!r}"
            )
        morph_updates = self._material_morph_updates(root, old_spec, new_spec)

        try:
            for binding, material in self._material_bindings(new_spec).items():
                prior = old_by_binding[binding]
                if material.index != prior.index:
                    self._set_attr(binding, ATTR_MMD_MATERIAL_INDEX, material.index, "long")
            for node, payload in morph_updates:
                self._set_attr(node, ATTR_MMD_MATERIAL_MORPH_OFFSETS, payload, "string")
            self._update_native_render_queue(root, first_index, second_index)
        except Exception as exc:
            raise MayaMaterialAuthoringError(
                f"failed to apply adjacent material reindex under root {root!r}: {exc}"
            ) from exc
        return new_spec

    def apply_material_reindex_fast(
        self,
        model_root: str,
        index: int,
        new_position: int,
    ) -> MaterialReindexResult:
        """Apply an adjacent swap without reading a model authoring spec."""
        root = self._require_root(model_root)
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise MayaMaterialAuthoringError("material index must be a non-negative integer")
        if isinstance(new_position, bool) or not isinstance(new_position, int) or new_position < 0:
            raise MayaMaterialAuthoringError("new_position must be a non-negative integer")
        if abs(index - new_position) != 1:
            raise MayaMaterialAuthoringError("material reindex requires an adjacent swap")
        first_index, second_index = sorted((index, new_position))

        registry_members = self._registry.list_model_registry_members(
            root, REGISTRY_CATEGORY_MATERIAL
        )
        if registry_members is None:
            raise MayaMaterialAuthoringError("material reindex requires a model registry")
        by_index: dict[int, str] = {}
        for member in registry_members:
            binding = self._canonical_node(str(member))
            observed = self._get_attr(binding, ATTR_MMD_MATERIAL_INDEX)
            if isinstance(observed, bool) or not isinstance(observed, int) or observed < 0:
                raise MayaMaterialAuthoringError(
                    f"material {binding!r} has an invalid material index"
                )
            if observed in by_index and by_index[observed] != binding:
                raise MayaMaterialAuthoringError(
                    f"duplicate material index {observed} in the model registry"
                )
            by_index[observed] = binding
        try:
            first_binding = by_index[first_index]
            second_binding = by_index[second_index]
        except KeyError as exc:
            raise MayaMaterialAuthoringError(
                "material reindex indices are not registry-owned"
            ) from exc

        morph_updates = self._material_morph_reindex_updates(
            root, first_index, second_index, set(by_index)
        )
        try:
            self._set_attr(first_binding, ATTR_MMD_MATERIAL_INDEX, second_index, "long")
            self._set_attr(second_binding, ATTR_MMD_MATERIAL_INDEX, first_index, "long")
            for node, payload in morph_updates:
                self._set_attr(node, ATTR_MMD_MATERIAL_MORPH_OFFSETS, payload, "string")
            self._update_native_render_queue(root, first_index, second_index)
        except Exception as exc:
            raise MayaMaterialAuthoringError(
                f"failed to apply adjacent material reindex under root {root!r}: {exc}"
            ) from exc
        return MaterialReindexResult(
            first_index=first_index,
            second_index=second_index,
        )

    def _material_morph_reindex_updates(
        self,
        root: str,
        first_index: int,
        second_index: int,
        valid_indices: set[int],
    ) -> list[tuple[str, str]]:
        """Build writes for only Material Morph JSON affected by a swap."""
        morph_members = self._registry.list_model_registry_members(root, "morph") or []
        updates: list[tuple[str, str]] = []
        swap = {first_index: second_index, second_index: first_index}
        for member in morph_members:
            node = self._canonical_node(str(member))
            morph_type = self._get_attr(node, "mmd_morph_type")
            if morph_type != "material":
                continue
            if not self._has_attr(node, ATTR_MMD_MATERIAL_MORPH_OFFSETS):
                raise MayaMaterialAuthoringError(
                    f"material morph {node!r} is missing its offsets JSON"
                )
            raw = self._get_attr(node, ATTR_MMD_MATERIAL_MORPH_OFFSETS)
            try:
                offsets = json.loads(raw, object_pairs_hook=self._unique_json_object)
            except (TypeError, ValueError) as exc:
                raise MayaMaterialAuthoringError(
                    f"material morph {node!r} contains invalid offsets JSON"
                ) from exc
            if not isinstance(offsets, list):
                raise MayaMaterialAuthoringError(
                    f"material morph {node!r} offsets must be a JSON list"
                )
            changed = False
            for offset_number, offset in enumerate(offsets):
                self._validate_material_morph_offset(
                    offset,
                    set(),
                    f"material morph {node} offset {offset_number}",
                    valid_indices=valid_indices,
                )
                material_index = offset["material_index"]
                replacement = swap.get(material_index)
                if replacement is not None:
                    offset["material_index"] = replacement
                    changed = True
            if changed:
                updates.append(
                    (
                        node,
                        json.dumps(offsets, ensure_ascii=False, separators=(",", ":")),
                    )
                )
        return updates

    @staticmethod
    def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON field {key!r}")
            value[key] = item
        return value

    def _validate_adjacent_reindex(
        self,
        old_spec: MmdModelAuthoringSpec,
        new_spec: MmdModelAuthoringSpec,
    ) -> tuple[int, int]:
        """Validate that exactly two adjacent material indices were swapped."""
        self._validate_material_spec_shape(old_spec, new_spec, allow_material_edits=False)
        old_by_binding = self._material_bindings(old_spec)
        new_by_binding = self._material_bindings(new_spec)
        if set(old_by_binding) != set(new_by_binding):
            raise MayaMaterialAuthoringError("material reindex cannot create or delete bindings")
        changed = [
            (old_by_binding[binding].index, new_by_binding[binding].index)
            for binding in old_by_binding
            if old_by_binding[binding].index != new_by_binding[binding].index
        ]
        if len(changed) != 2:
            raise MayaMaterialAuthoringError("material reindex must change exactly two indices")
        old_indices = {old for old, _new in changed}
        new_indices = {new for _old, new in changed}
        if old_indices != new_indices:
            raise MayaMaterialAuthoringError("material reindex must preserve the swapped index set")
        first_index, second_index = sorted(old_indices)
        if second_index - first_index != 1:
            raise MayaMaterialAuthoringError("material reindex indices must be adjacent")
        if {(old, new) for old, new in changed} != {
            (first_index, second_index),
            (second_index, first_index),
        }:
            raise MayaMaterialAuthoringError("material reindex must swap adjacent indices")
        return first_index, second_index

    def _update_native_render_queue(self, root: str, first_index: int, second_index: int) -> None:
        """Reindex native shape queues when the optional command surface exists."""
        updater = self._native_queue_reindexer
        if callable(updater):
            result = updater(root, first_index, second_index)
            if result is False:
                raise MayaMaterialAuthoringError("native render queue reindex was rejected")
            return

        updater = getattr(self._cmds, "mmd_render_queue_reindex", None)
        if not callable(updater):
            return
        try:
            node_types = self._call("all_node_types")
        except Exception as exc:
            raise MayaMaterialAuthoringError(
                f"failed to query Maya node types before native render queue reindex: {exc}"
            ) from exc
        if isinstance(node_types, (str, bytes, bytearray)) or not isinstance(node_types, Sequence):
            raise MayaMaterialAuthoringError("Maya node type listing must be a sequence")
        if "mmdRenderShape" not in node_types:
            return
        try:
            shapes = self._call(
                "list_relatives",
                root,
                allDescendents=True,
                fullPath=True,
                type="mmdRenderShape",
            ) or []
        except Exception as exc:
            # A callable native updater means queue state is part of this
            # transaction.  If model-root descendant discovery fails, do not
            # silently leave an existing native queue stale.
            raise MayaMaterialAuthoringError(
                f"failed to discover native render shapes under root {root!r}: {exc}"
            ) from exc
        if isinstance(shapes, (str, bytes, bytearray)):
            raise MayaMaterialAuthoringError("native render shape listing must be a sequence")
        if not isinstance(shapes, Sequence):
            raise MayaMaterialAuthoringError("native render shape listing must be a sequence")
        for shape in shapes:
            if not isinstance(shape, str) or not shape.strip():
                raise MayaMaterialAuthoringError("native render shape listing contains an invalid node")
            result = updater(shape, first_index, second_index)
            if result is False:
                raise MayaMaterialAuthoringError(
                    f"native render queue reindex was rejected for {shape!r}"
                )

    def _validate_material_spec_shape(
        self,
        old_spec: MmdModelAuthoringSpec,
        new_spec: MmdModelAuthoringSpec,
        *,
        allow_material_edits: bool = False,
    ) -> None:
        if old_spec.schema_version != new_spec.schema_version:
            raise MayaMaterialAuthoringError("material structural change cannot change schema version")
        if old_spec.model.to_mapping() != new_spec.model.to_mapping():
            raise MayaMaterialAuthoringError("material structural change cannot change model metadata")
        if old_spec.bones != new_spec.bones:
            raise MayaMaterialAuthoringError("material structural change cannot change bone metadata")
        old_materials = {material.binding_identity: material for material in old_spec.materials}
        new_materials = {material.binding_identity: material for material in new_spec.materials}
        if None in old_materials or None in new_materials:
            raise MayaMaterialAuthoringError(
                "material structural change requires binding identities on every material"
            )
        for binding in set(old_materials) & set(new_materials):
            old_mapping = old_materials[binding].to_mapping()
            new_mapping = new_materials[binding].to_mapping()
            for field in ("index", "binding_identity"):
                old_mapping.pop(field, None)
                new_mapping.pop(field, None)
            if not allow_material_edits and old_mapping != new_mapping:
                raise MayaMaterialAuthoringError(
                    f"material binding {binding!r} changed fields beyond index"
                )
        old_morphs = {morph.binding_identity: morph for morph in old_spec.morphs}
        new_morphs = {morph.binding_identity: morph for morph in new_spec.morphs}
        if None in old_morphs or None in new_morphs:
            raise MayaMaterialAuthoringError(
                "material structural change requires binding identities on every morph"
            )
        if set(old_morphs) != set(new_morphs):
            raise MayaMaterialAuthoringError(
                "material structural change cannot create or delete morph bindings"
            )
        for binding in old_morphs:
            old_mapping = old_morphs[binding].to_mapping()
            new_mapping = new_morphs[binding].to_mapping()
            allowed = {"offsets"} if old_morphs[binding].morph_type == "material" else set()
            for field in allowed:
                old_mapping.pop(field, None)
                new_mapping.pop(field, None)
            if old_mapping != new_mapping:
                raise MayaMaterialAuthoringError(
                    f"morph binding {binding!r} changed outside material offsets"
                )

    def _material_bindings(
        self,
        spec: MmdModelAuthoringSpec,
    ) -> dict[str, MmdMaterialSpec]:
        result: dict[str, MmdMaterialSpec] = {}
        for material in spec.materials:
            if not material.binding_identity:
                raise MayaMaterialAuthoringError(
                    f"material {material.index} has no binding identity"
                )
            binding = self._canonical_node(material.binding_identity)
            if binding in result:
                raise MayaMaterialAuthoringError(
                    f"duplicate material binding identity: {binding!r}"
                )
            result[binding] = material
        return result

    def _material_morph_updates(
        self,
        root: str,
        old_spec: MmdModelAuthoringSpec,
        new_spec: MmdModelAuthoringSpec,
    ) -> list[tuple[str, str]]:
        old_by_binding = {
            morph.binding_identity: morph
            for morph in old_spec.morphs
            if morph.morph_type == "material"
        }
        new_by_binding = {
            morph.binding_identity: morph
            for morph in new_spec.morphs
            if morph.morph_type == "material"
        }
        if not old_by_binding:
            return []
        registry_members = self._registry.list_model_registry_members(
            root, "morph"
        ) or []
        owned = {self._canonical_node(str(member)) for member in registry_members}
        required = {binding for binding in old_by_binding if binding}
        if not required.issubset(owned):
            raise MayaMaterialAuthoringError(
                f"material morph bindings are not registry-owned under root {root!r}"
            )
        material_indices = {
            material.index for material in new_spec.materials
        }
        old_material_indices = {
            material.index for material in old_spec.materials
        }
        # The deleted-index set is derived directly from old/new bindings so a
        # pure delete cannot be bypassed by manually rewriting an offset.
        old_material_bindings = {
            material.binding_identity: material for material in old_spec.materials
        }
        new_material_bindings = {
            material.binding_identity: material for material in new_spec.materials
        }
        deleted_indices = {
            old_material_bindings[binding].index
            for binding in set(old_material_bindings) - set(new_material_bindings)
            if binding is not None
        }
        updates: list[tuple[str, str]] = []
        for binding, old_morph in old_by_binding.items():
            if binding is None:
                continue
            new_morph = new_by_binding[binding]
            for offset_number, offset in enumerate(old_morph.offsets):
                self._validate_material_morph_offset(
                    offset,
                    deleted_indices,
                    f"morph {old_morph.index} offset {offset_number}",
                    valid_indices=old_material_indices,
                )
            for offset_number, offset in enumerate(new_morph.offsets):
                self._validate_material_morph_offset(
                    offset,
                    set(),
                    f"new morph {new_morph.index} offset {offset_number}",
                    valid_indices=material_indices,
                )
            if old_morph.offsets == new_morph.offsets:
                continue
            payload = json.dumps(
                new_morph.to_mapping()["offsets"],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            updates.append((self._canonical_node(str(binding)), payload))
        return updates

    @staticmethod
    def _material_fields_changed(old: MmdMaterialSpec, new: MmdMaterialSpec) -> bool:
        """Return whether a material changed outside its explicit index."""
        old_mapping = old.to_mapping()
        new_mapping = new.to_mapping()
        for field in ("index", "binding_identity"):
            old_mapping.pop(field, None)
            new_mapping.pop(field, None)
        return old_mapping != new_mapping

    @staticmethod
    def _default_material_rebuilder(root: str) -> Any:
        """Build all material-morph evaluators through the production runtime."""
        from mmd_tools.converters.material_morph_runtime import build_material_morph_graph

        return build_material_morph_graph(root)

    def _rebuild_material_morph_graph(self, root: str) -> None:
        """Refresh the complete material-morph graph for this model root."""
        rebuilder = self._runtime_rebuilders.get("material")
        if not callable(rebuilder):
            raise MayaMaterialAuthoringError(
                "material morph runtime rebuild requires an explicit rebuilder"
            )
        try:
            result = rebuilder(root)
        except Exception as exc:
            raise MayaMaterialAuthoringError(
                f"material morph runtime graph rebuild failed under root {root!r}: {exc}"
            ) from exc
        if isinstance(result, Mapping) and result.get("success") is False:
            raise MayaMaterialAuthoringError(
                f"material morph runtime graph rebuild reported failure: {result!r}"
            )

    @staticmethod
    def _validate_material_morph_offset(
        offset: Any,
        deleted_indices: set[int],
        context: str,
        *,
        valid_indices: set[int] | None = None,
    ) -> None:
        if not isinstance(offset, Mapping) or set(offset) != {
            "material_index",
            "operation_type",
            "diffuse",
            "specular",
            "specular_coefficient",
            "ambient",
            "edge_color",
            "edge_size",
            "texture_factor",
            "sphere_texture_factor",
            "toon_texture_factor",
        }:
            raise MayaMaterialAuthoringError(f"{context} has malformed material offset")
        material_index = offset["material_index"]
        if isinstance(material_index, bool) or not isinstance(material_index, int):
            raise MayaMaterialAuthoringError(
                f"{context}.material_index must be an integer"
            )
        if material_index in deleted_indices:
            raise MayaMaterialAuthoringError(
                f"{context} references a deleted material {material_index}"
            )
        if valid_indices is not None and material_index != -1 and material_index not in valid_indices:
            raise MayaMaterialAuthoringError(
                f"{context} references unknown material {material_index}"
            )

    def _resolve_material(self, root: str, material: MmdMaterialSpec) -> tuple[str, str] | None:
        self._registry.ensure_model_registry(root)
        members = self._registry.list_model_registry_members(root, REGISTRY_CATEGORY_MATERIAL) or []
        matches: list[str] = []
        for member in members:
            shader = self._canonical_node(str(member))
            if material.binding_identity is None or shader != material.binding_identity:
                continue
            index = self._get_attr(shader, ATTR_MMD_MATERIAL_INDEX)
            if type(index) is int and index == material.index:
                matches.append(shader)
        if len(matches) > 1:
            raise MayaMaterialAuthoringError(
                f"material {material.index} has ambiguous bindings under root {root!r}"
            )
        if not matches:
            return None
        shader = matches[0]
        shading_groups = list(self._call("list_connections", shader, type="shadingEngine") or [])
        if len(shading_groups) != 1:
            raise MayaMaterialAuthoringError(f"shader {shader!r} must have exactly one shading group")
        return shader, str(shading_groups[0])

    def _resolve_delete_target(self, root: str, material: MmdMaterialSpec | str) -> tuple[str, str]:
        if isinstance(material, MmdMaterialSpec):
            binding = self._resolve_material(root, material)
            if binding is None:
                raise MayaMaterialAuthoringError(f"material {material.index} is not registered")
            return binding
        if not isinstance(material, str) or not material.strip():
            raise MayaMaterialAuthoringError("material must be MmdMaterialSpec or shader identity")
        shader = material
        members = self._registry.list_model_registry_members(root, REGISTRY_CATEGORY_MATERIAL) or []
        if shader not in members:
            raise MayaMaterialAuthoringError(f"shader {shader!r} is not owned by root {root!r}")
        groups = list(self._call("list_connections", shader, type="shadingEngine") or [])
        if len(groups) != 1:
            raise MayaMaterialAuthoringError(f"shader {shader!r} must have exactly one shading group")
        return shader, str(groups[0])

    def _resolve_replacement_sg(self, replacement: str) -> str:
        if not self._object_exists(replacement):
            raise MayaMaterialAuthoringError(f"replacement shader does not exist: {replacement!r}")
        node_type = self._call("node_type", replacement)
        if node_type == "shadingEngine":
            return replacement
        if node_type in {"standardSurface", "lambert", "blinn", "phong"}:
            groups = list(self._call("list_connections", replacement, type="shadingEngine") or [])
            if len(groups) == 1:
                return str(groups[0])
        raise MayaMaterialAuthoringError("replacement_shader must resolve to exactly one shading group")

    def _write_material_attrs(
        self,
        shader: str,
        material: MmdMaterialSpec,
        *,
        bind_texture_graph: bool = True,
    ) -> None:
        vectors = {
            ATTR_MMD_DIFFUSE_COLOR: material.diffuse[:3],
            ATTR_MMD_SPECULAR_COLOR: material.specular,
            ATTR_MMD_AMBIENT_COLOR: material.ambient,
            ATTR_MMD_EDGE_COLOR: material.edge_color[:3],
        }
        scalars = {
            ATTR_MMD_MATERIAL: 1,
            ATTR_MMD_MATERIAL_INDEX: material.index,
            # MmdMaterialSpec stores source-relative paths rather than a
            # complete PMX texture table.  Preserve an existing table index
            # only when its source path agrees; otherwise use the explicit
            # unresolved sentinel while retaining path provenance in strings.
            ATTR_MMD_TEXTURE_INDEX: self._texture_index_for_write(
                shader,
                ATTR_MMD_TEXTURE_INDEX,
                ATTR_MMD_TEXTURE_PATH,
                material.texture_path,
            ),
            ATTR_MMD_SPHERE_TEXTURE_INDEX: self._texture_index_for_write(
                shader,
                ATTR_MMD_SPHERE_TEXTURE_INDEX,
                ATTR_MMD_SPHERE_PATH,
                material.sphere_texture_path,
            ),
            ATTR_MMD_SHININESS: material.specular_coefficient,
            ATTR_MMD_DRAW_FLAGS: material.draw_flags,
            ATTR_MMD_EDGE_FLAG: bool(material.draw_flags & 0x10),
            ATTR_MMD_EDGE_SIZE: material.edge_size,
            ATTR_MMD_SPHERE_MODE: material.sphere_mode,
            ATTR_MMD_SHARED_TOON_FLAG: int(material.shared_toon),
            ATTR_MMD_TOON_TEXTURE_INDEX: -1 if material.toon_texture_index is None else material.toon_texture_index,
            ATTR_MMD_DIFFUSE_ALPHA: material.diffuse[3],
            ATTR_MMD_EDGE_ALPHA: material.edge_color[3],
        }
        strings = {
            ATTR_MMD_MATERIAL_NAME: material.name,
            ATTR_MMD_MATERIAL_NAME_EN: material.name_english,
            ATTR_MMD_MEMO: material.memo,
            ATTR_MMD_TEXTURE_PATH: material.texture_path or "",
            ATTR_MMD_ORIGINAL_TEXTURE_PATH: material.texture_path or "",
            ATTR_MMD_RESOLVED_TEXTURE_PATH: material.resolved_texture_path or "",
            ATTR_MMD_SPHERE_PATH: material.sphere_texture_path or "",
            ATTR_MMD_RESOLVED_SPHERE_TEXTURE_PATH: material.resolved_sphere_texture_path or "",
            ATTR_MMD_TOON_TEXTURE_PATH: "" if material.shared_toon else material.toon_texture_path or "",
            ATTR_MMD_RESOLVED_TOON_TEXTURE_PATH: (
                "" if material.shared_toon else material.resolved_toon_texture_path or ""
            ),
        }
        for attr, value in vectors.items():
            self._set_attr(shader, attr, value, "double3")
        integral_attrs = {
            ATTR_MMD_MATERIAL,
            ATTR_MMD_MATERIAL_INDEX,
            ATTR_MMD_DRAW_FLAGS,
            ATTR_MMD_TEXTURE_INDEX,
            ATTR_MMD_SPHERE_TEXTURE_INDEX,
            ATTR_MMD_SPHERE_MODE,
            ATTR_MMD_SHARED_TOON_FLAG,
            ATTR_MMD_TOON_TEXTURE_INDEX,
        }
        for attr, value in scalars.items():
            if isinstance(value, bool):
                attr_type = "bool"
            elif attr in integral_attrs:
                attr_type = "long"
            else:
                attr_type = "double"
            self._set_attr(shader, attr, value, attr_type)
        for attr, value in strings.items():
            self._set_attr(shader, attr, value, "string")

        # Reconcile the graph before touching a stock shader's final color.
        # A connected file node owns that destination and Maya rejects a
        # direct setAttr while the connection is live.
        if bind_texture_graph:
            self._bind_texture_graphs(shader, material)
        route = material_diffuse_route(
            str(self._call("node_type", shader)),
            has_main_texture=bool(material.resolved_texture_path),
        )
        if route is not None:
            self._set_attr(
                shader,
                route.diffuse_attribute,
                material.diffuse[:3],
                route.diffuse_attribute_type,
            )

    def _set_attr(self, node: str, attr: str, value: Any, attr_type: str) -> None:
        if not self._has_attr(node, attr):
            kwargs = {"long_name": attr, "attribute_type": attr_type}
            if attr_type == "string":
                kwargs = {"longName": attr, "dataType": "string"}
            elif attr_type in {"double3", "float3"}:
                kwargs = {"longName": attr, "attributeType": attr_type}
            else:
                kwargs = {"longName": attr, "attributeType": attr_type}
            self._call("add_attr", node, **kwargs)
            if attr_type in {"double3", "float3"}:
                child_type = "double" if attr_type == "double3" else "float"
                for suffix in ("X", "Y", "Z"):
                    self._call(
                        "add_attr",
                        node,
                        longName=f"{attr}{suffix}",
                        attributeType=child_type,
                        parent=attr,
                    )
        path = f"{node}.{attr}"
        if attr_type in {"double3", "float3"}:
            self._call("set_attr", path, *value, type=attr_type)
        elif attr_type == "bool":
            self._call("set_attr", path, bool(value))
        elif attr_type == "string":
            self._call("set_attr", path, value, type="string")
        else:
            self._call("set_attr", path, value)

    def _texture_index_for_write(
        self,
        shader: str,
        index_attr: str,
        path_attr: str,
        source_path: str | None,
    ) -> int:
        """Preserve a table index only when its source-path provenance agrees."""
        if not source_path:
            return -1
        if not self._has_attr(shader, index_attr) or not self._has_attr(shader, path_attr):
            return -1
        prior_path = self._get_attr(shader, path_attr)
        prior_index = self._get_attr(shader, index_attr)
        if (
            prior_path == source_path
            and isinstance(prior_index, int)
            and not isinstance(prior_index, bool)
            and prior_index >= 0
        ):
            return prior_index
        # Importers may persist the resolved fileTextureName in the shader
        # path attr while the exact slot file node retains the PMX source
        # path.  Preserve the table index only when both provenance values
        # agree; a stale or cross-slot file node must invalidate the index.
        semantic = {
            ATTR_MMD_TEXTURE_PATH: "main",
            ATTR_MMD_SPHERE_PATH: "sphere",
        }.get(path_attr)
        route = material_shader_route(str(self._call("node_type", shader)))
        queries = [f"{shader}.{path_attr}"]
        if route is not None and semantic is not None:
            slot = route.texture_slot(semantic)
            if slot is not None:
                queries.append(f"{shader}.{slot.texture_attribute}")
        file_nodes: list[str] = []
        for query in queries:
            connections = self._call(
                "list_connections",
                query,
                source=True,
                destination=False,
                type="file",
            ) or []
            for candidate in connections:
                identity = self._canonical_node(str(candidate).rsplit(".", 1)[0])
                if identity not in file_nodes:
                    file_nodes.append(identity)
        if len(file_nodes) != 1:
            return -1
        file_node = file_nodes[0]
        if not self._has_attr(file_node, ATTR_MMD_ORIGINAL_TEXTURE_PATH):
            return -1
        original = self._get_attr(file_node, ATTR_MMD_ORIGINAL_TEXTURE_PATH)
        if original != source_path or not self._has_attr(file_node, "fileTextureName"):
            return -1
        resolved = self._get_attr(file_node, "fileTextureName")
        if not isinstance(prior_path, str) or not isinstance(resolved, str):
            return -1
        if os.path.normcase(os.path.normpath(prior_path)) != os.path.normcase(
            os.path.normpath(resolved)
        ):
            return -1
        if (
            isinstance(prior_index, int)
            and not isinstance(prior_index, bool)
            and prior_index >= 0
        ):
            return prior_index
        return -1

    def _bind_texture_graphs(self, shader: str, material: MmdMaterialSpec) -> None:
        """Reconcile every texture graph supported by the shader backend."""
        route = material_shader_route(str(self._call("node_type", shader)))
        if route is None:
            raise MayaMaterialAuthoringError(
                f"shader {shader!r} has no supported texture route"
            )
        paths = {
            "main": (material.texture_path, material.resolved_texture_path),
            "sphere": (
                material.sphere_texture_path,
                material.resolved_sphere_texture_path,
            ),
            "toon": self._toon_texture_paths(material),
        }
        for slot in route.texture_slots:
            source_path, resolved_path = paths[slot.semantic]
            self._bind_texture_slot(
                shader,
                material.index,
                slot,
                source_path,
                resolved_path,
            )
        if route.texture_slot("sphere") is not None:
            self._set_attr(shader, "SphereMode", material.sphere_mode, "long")

    def _bind_texture_slot(
        self,
        shader: str,
        material_index: int,
        slot: MayaMaterialTextureSlotRoute,
        source_path: str | None,
        resolved_path: str | None,
    ) -> None:
        """Create, update, or clear one exact shader texture slot."""
        destination = f"{shader}.{slot.texture_attribute}"
        source_plugs = self._call(
            "list_connections",
            destination,
            source=True,
            destination=False,
            plugs=True,
            type="file",
        ) or []
        file_nodes = [
            self._canonical_node(str(source).rsplit(".", 1)[0])
            for source in source_plugs
        ]
        if len(file_nodes) > 1:
            raise MayaMaterialAuthoringError(
                f"shader {shader!r} has ambiguous {slot.semantic} texture file nodes"
            )
        if not resolved_path:
            if file_nodes:
                file_node = file_nodes[0]
                self._call("disconnect_attr", f"{file_node}.outColor", destination)
                remaining = self._call(
                    "list_connections",
                    f"{file_node}.outColor",
                    source=False,
                    destination=True,
                    plugs=True,
                ) or []
                if not remaining:
                    self._call("delete", file_node)
            if slot.presence_attribute is not None:
                self._set_attr(
                    shader,
                    slot.presence_attribute,
                    0,
                    "long",
                )
            return
        if file_nodes:
            file_node = file_nodes[0]
        else:
            file_node = self._canonical_node(
                str(
                    self._call(
                        "shading_node",
                        "file",
                        asTexture=True,
                        isColorManaged=True,
                        name=f"mmdMaterial_{material_index}_{slot.file_node_suffix}",
                    )
                )
            )
        self._set_attr(file_node, "fileTextureName", resolved_path, "string")
        self._set_attr(file_node, ATTR_MMD_ORIGINAL_TEXTURE_PATH, source_path or "", "string")
        expected_source = f"{file_node}.outColor"
        if expected_source not in {str(source) for source in source_plugs}:
            self._call("connect_attr", expected_source, destination, force=True)
        if slot.presence_attribute is not None:
            self._set_attr(
                shader,
                slot.presence_attribute,
                1,
                "long",
            )

    @staticmethod
    def _toon_texture_paths(material: MmdMaterialSpec) -> tuple[str | None, str | None]:
        """Return source/resolved paths for custom or bundled shared toon."""
        if not material.shared_toon:
            return material.toon_texture_path, material.resolved_toon_texture_path
        index = material.toon_texture_index
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index <= 9:
            return None, None
        resolved = (
            Path(__file__).resolve().parents[1]
            / "shaders"
            / "toon_textures"
            / f"toon{index + 1:02d}.bmp"
        )
        return None, str(resolved)

    def _require_root(self, model_root: str) -> str:
        if not isinstance(model_root, str) or not model_root.strip() or not self._object_exists(model_root):
            raise MayaMaterialAuthoringError(f"invalid model root: {model_root!r}")
        roots = list(self._call("ls", model_root, long=True) or [])
        if len(roots) != 1 or not isinstance(roots[0], str) or not roots[0].startswith("|"):
            raise MayaMaterialAuthoringError(f"model root is not a unique canonical path: {model_root!r}")
        return roots[0]

    def _validate_target(self, root: str, target: str) -> str:
        if not isinstance(target, str) or not target.strip():
            raise MayaMaterialAuthoringError("targets must contain non-empty strings")
        node = target.split(".", 1)[0]
        paths = list(self._call("ls", node, long=True) or [])
        if len(paths) != 1 or not paths[0].startswith(root + "|"):
            raise MayaMaterialAuthoringError(f"target is outside model root {root!r}: {target!r}")
        return target

    def _object_exists(self, node: str) -> bool:
        return bool(self._call("object_exists", node))

    def _canonical_node(self, node: str) -> str:
        paths = list(self._call("ls", node, long=True) or [])
        if len(paths) != 1 or not isinstance(paths[0], str) or not paths[0]:
            raise MayaMaterialAuthoringError(f"node is not a unique canonical path: {node!r}")
        return paths[0]

    def _has_attr(self, node: str, attr: str) -> bool:
        return bool(self._call("attribute_exists", attr, node))

    def _get_attr(self, node: str, attr: str) -> Any:
        return self._call("get_attr", f"{node}.{attr}")

    def _require_material(self, material: Any) -> None:
        if not isinstance(material, MmdMaterialSpec):
            raise MayaMaterialAuthoringError("material must be an MmdMaterialSpec")

    def _require_model_spec(self, spec: Any, field: str) -> None:
        if not isinstance(spec, MmdModelAuthoringSpec):
            raise MayaMaterialAuthoringError(f"{field} must be an MmdModelAuthoringSpec")

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return getattr(self._cmds, method)(*args, **kwargs)
        except Exception as exc:
            raise MayaMaterialAuthoringError(f"Maya adapter call {method} failed: {exc}") from exc


__all__ = ["MaterialReindexResult", "MayaMaterialAuthoringError", "MayaMaterialAuthoring"]
