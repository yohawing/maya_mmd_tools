"""Immutable, Maya-free projections for Material authoring reads."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from mmd_tools.core.model_authoring_spec import MmdMaterialSpec


def _identity(value: str, *, field: str) -> str:
    """Validate an already-canonical node identity without interpreting it."""

    if not isinstance(value, str) or not value:
        raise ValueError("{} must be a non-empty canonical identity".format(field))
    return value


def _shader_identity(shader_plug: str) -> str:
    """Return the shader part of one exact ``node.attribute`` binding."""

    shader, separator, attribute = shader_plug.rpartition(".")
    if not separator or not shader or not attribute:
        raise ValueError("shader_plug must be an exact node.attribute binding")
    return shader


class MaterialTextureSlot(str, Enum):
    """Semantic texture slots whose Maya plugs must be resolved exactly."""

    MAIN = "main"
    SPHERE = "sphere"
    TOON = "toon"


class MaterialAssignmentKind(str, Enum):
    """How standard-set membership assigns faces to a material."""

    EMPTY = "empty"
    WHOLE_OBJECT = "whole_object"
    EXPLICIT_FACES = "explicit_faces"
    MIXED = "mixed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MaterialAssignmentSummary:
    """Live shading assignment counts for one model-owned material."""

    kind: MaterialAssignmentKind
    mesh_count: int
    face_count: Optional[int]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, MaterialAssignmentKind):
            raise TypeError("kind must be a MaterialAssignmentKind")
        if type(self.mesh_count) is not int or self.mesh_count < 0:
            raise ValueError("mesh_count must be a non-negative integer")
        if self.face_count is not None and (
            type(self.face_count) is not int or self.face_count < 0
        ):
            raise ValueError("face_count must be a non-negative integer or None")
        if self.kind is MaterialAssignmentKind.EMPTY and (
            self.mesh_count != 0 or self.face_count != 0
        ):
            raise ValueError("empty assignment must have zero meshes and faces")
        if self.kind is MaterialAssignmentKind.WHOLE_OBJECT and (
            self.mesh_count == 0 or self.face_count is not None
        ):
            raise ValueError(
                "whole-object assignment requires meshes and no explicit face count"
            )
        if self.kind is MaterialAssignmentKind.EXPLICIT_FACES and (
            self.mesh_count == 0 or self.face_count is None or self.face_count == 0
        ):
            raise ValueError(
                "explicit-face assignment requires meshes and a positive face count"
            )
        if self.kind is MaterialAssignmentKind.MIXED and (
            self.mesh_count == 0 or self.face_count is None or self.face_count == 0
        ):
            raise ValueError(
                "mixed assignment requires whole-object meshes and explicit faces"
            )
        if self.kind is MaterialAssignmentKind.UNKNOWN and self.face_count is not None:
            raise ValueError("unknown assignment cannot report an explicit face count")

    @property
    def label(self) -> str:
        """Return the compact text currently consumed by the Material list."""

        if self.kind is MaterialAssignmentKind.WHOLE_OBJECT:
            faces = "all"
        elif self.kind is MaterialAssignmentKind.MIXED:
            faces = "all+{}".format(self.face_count)
        elif self.kind is MaterialAssignmentKind.UNKNOWN:
            faces = "?"
        else:
            faces = str(self.face_count)
        return "meshes={}, faces={}".format(self.mesh_count, faces)


@dataclass(frozen=True)
class MaterialTextureBinding:
    """One exact shader plug and its optional canonical file-node source."""

    slot: MaterialTextureSlot
    shader_plug: str
    file_node_identity: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.slot, MaterialTextureSlot):
            raise TypeError("slot must be a MaterialTextureSlot")
        _identity(self.shader_plug, field="shader_plug")
        _shader_identity(self.shader_plug)
        if self.file_node_identity is not None:
            _identity(self.file_node_identity, field="file_node_identity")


@dataclass(frozen=True)
class MaterialTextureProvenance:
    """Authored source path and resolved file path for one semantic slot."""

    slot: MaterialTextureSlot
    source_path: Optional[str]
    resolved_path: Optional[str]
    binding: Optional[MaterialTextureBinding] = None

    def __post_init__(self) -> None:
        if not isinstance(self.slot, MaterialTextureSlot):
            raise TypeError("slot must be a MaterialTextureSlot")
        for field in ("source_path", "resolved_path"):
            value = getattr(self, field)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError("{} must be None or a non-empty path".format(field))
        if self.binding is not None and self.binding.slot is not self.slot:
            raise ValueError("texture binding slot must match provenance slot")


@dataclass(frozen=True)
class MaterialPreviewState:
    """Viewport-only state kept separate from authored PMX semantics."""

    shader_type: str
    outline_enabled: bool

    def __post_init__(self) -> None:
        if not isinstance(self.shader_type, str) or not self.shader_type:
            raise ValueError("shader_type must be a non-empty string")
        if type(self.outline_enabled) is not bool:
            raise TypeError("outline_enabled must be a boolean")


@dataclass(frozen=True)
class MaterialListItemProjection:
    """One semantic list row with live assignment information."""

    material: MmdMaterialSpec
    assignment: MaterialAssignmentSummary

    def __post_init__(self) -> None:
        if not isinstance(self.material, MmdMaterialSpec):
            raise TypeError("material must be an MmdMaterialSpec")
        if not isinstance(self.assignment, MaterialAssignmentSummary):
            raise TypeError("assignment must be a MaterialAssignmentSummary")
        _identity(self.material.binding_identity, field="material.binding_identity")

    @property
    def index(self) -> int:
        return self.material.index

    @property
    def binding_identity(self) -> str:
        # __post_init__ establishes this Optional field as non-null.
        return self.material.binding_identity  # type: ignore[return-value]


@dataclass(frozen=True)
class MaterialListProjection:
    """One model-root generation of material rows in strict PMX index order."""

    root_identity: str
    items: Tuple[MaterialListItemProjection, ...]

    def __post_init__(self) -> None:
        _identity(self.root_identity, field="root_identity")
        if not isinstance(self.items, tuple) or not all(
            isinstance(item, MaterialListItemProjection) for item in self.items
        ):
            raise TypeError("items must be a tuple of MaterialListItemProjection")
        indices = tuple(item.index for item in self.items)
        bindings = tuple(item.binding_identity for item in self.items)
        if indices != tuple(sorted(indices)) or len(indices) != len(set(indices)):
            raise ValueError("material items must have unique ascending PMX indices")
        if len(bindings) != len(set(bindings)):
            raise ValueError("material items must have unique canonical bindings")

    def item_for_index(self, index: int) -> MaterialListItemProjection:
        """Return the one list item addressed by semantic PMX index."""

        matches = tuple(item for item in self.items if item.index == index)
        if len(matches) != 1:
            raise KeyError("material index {!r} is not present".format(index))
        return matches[0]

    def item_for_binding(self, binding_identity: str) -> MaterialListItemProjection:
        """Return the one list item addressed by canonical shader identity."""

        _identity(binding_identity, field="binding_identity")
        matches = tuple(
            item for item in self.items if item.binding_identity == binding_identity
        )
        if len(matches) != 1:
            raise KeyError("material binding {!r} is not present".format(binding_identity))
        return matches[0]


@dataclass(frozen=True)
class MaterialDetailProjection:
    """Read-only detail surface for one projected material row.

    ``textures`` may be sparse when a shader backend does not expose every
    semantic slot, but present slots must follow MAIN, SPHERE, TOON order.
    """

    root_identity: str
    material: MmdMaterialSpec
    assignment: MaterialAssignmentSummary
    textures: Tuple[MaterialTextureProvenance, ...]
    preview: MaterialPreviewState

    def __post_init__(self) -> None:
        _identity(self.root_identity, field="root_identity")
        if not isinstance(self.material, MmdMaterialSpec):
            raise TypeError("material must be an MmdMaterialSpec")
        if not isinstance(self.assignment, MaterialAssignmentSummary):
            raise TypeError("assignment must be a MaterialAssignmentSummary")
        if not isinstance(self.preview, MaterialPreviewState):
            raise TypeError("preview must be a MaterialPreviewState")
        if not isinstance(self.textures, tuple) or not all(
            isinstance(texture, MaterialTextureProvenance) for texture in self.textures
        ):
            raise TypeError("textures must be a tuple of MaterialTextureProvenance")
        binding_identity = _identity(
            self.material.binding_identity, field="material.binding_identity"
        )
        slots = tuple(texture.slot for texture in self.textures)
        if len(slots) != len(set(slots)):
            raise ValueError("material detail texture slots must be unique")
        canonical_slots = (
            MaterialTextureSlot.MAIN,
            MaterialTextureSlot.SPHERE,
            MaterialTextureSlot.TOON,
        )
        if slots != tuple(slot for slot in canonical_slots if slot in slots):
            raise ValueError(
                "material detail texture slots must follow canonical slot order"
            )
        semantic_paths = {
            MaterialTextureSlot.MAIN: (
                self.material.texture_path,
                self.material.resolved_texture_path,
            ),
            MaterialTextureSlot.SPHERE: (
                self.material.sphere_texture_path,
                self.material.resolved_sphere_texture_path,
            ),
            MaterialTextureSlot.TOON: (
                self.material.toon_texture_path,
                self.material.resolved_toon_texture_path,
            ),
        }
        for texture in self.textures:
            if (texture.source_path, texture.resolved_path) != semantic_paths[texture.slot]:
                raise ValueError(
                    "texture provenance must match the semantic material paths"
                )
            if (
                texture.binding is not None
                and _shader_identity(texture.binding.shader_plug) != binding_identity
            ):
                raise ValueError(
                    "texture binding must target the material canonical binding"
                )

    def texture(self, slot: MaterialTextureSlot) -> MaterialTextureProvenance:
        """Return one exact semantic texture slot without fallback discovery."""

        if not isinstance(slot, MaterialTextureSlot):
            raise TypeError("slot must be a MaterialTextureSlot")
        matches = tuple(texture for texture in self.textures if texture.slot is slot)
        if len(matches) != 1:
            raise KeyError("texture slot {!r} is not present".format(slot.value))
        return matches[0]


__all__ = [
    "MaterialAssignmentKind",
    "MaterialAssignmentSummary",
    "MaterialDetailProjection",
    "MaterialListItemProjection",
    "MaterialListProjection",
    "MaterialPreviewState",
    "MaterialTextureBinding",
    "MaterialTextureProvenance",
    "MaterialTextureSlot",
]
