"""Read strict normalized PMX material metadata through Maya."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from mmd_tools.adapters.maya_material_read_projection import (
    MayaMaterialReadProjectionAdapter,
)
from mmd_tools.adapters.maya_material_shader_route import material_shader_route
from mmd_tools.adapters.maya_metadata_read_support import MayaMetadataReadSupport
from mmd_tools.core.constants import (
    ATTR_MMD_AMBIENT_COLOR,
    ATTR_MMD_DIFFUSE_COLOR,
    ATTR_MMD_DRAW_FLAGS,
    ATTR_MMD_EDGE_COLOR,
    ATTR_MMD_EDGE_SIZE,
    ATTR_MMD_MATERIAL,
    ATTR_MMD_MATERIAL_INDEX,
    ATTR_MMD_MATERIAL_NAME,
    ATTR_MMD_MATERIAL_NAME_EN,
    ATTR_MMD_MEMO,
    ATTR_MMD_MODEL_REGISTRY,
    ATTR_MMD_REGISTRY_MATERIAL_MEMBERS,
    ATTR_MMD_REGISTRY_ROOT,
    ATTR_MMD_REGISTRY_SCHEMA,
    ATTR_MMD_SHARED_TOON_FLAG,
    ATTR_MMD_SHININESS,
    ATTR_MMD_SPHERE_MODE,
    ATTR_MMD_SPECULAR_COLOR,
    ATTR_MMD_TOON_TEXTURE_INDEX,
)
from mmd_tools.core.material_read_projection import (
    MaterialAssignmentSummary,
    MaterialDetailProjection,
    MaterialListProjection,
    MaterialListSemantic,
)
from mmd_tools.core.model_authoring_spec import MmdMaterialSpec


MaterialMemberReader = Callable[[str], list[str] | None]
MaterialMappingReader = Callable[[str], dict[str, Any]]


class MayaMaterialMetadataRepository:
    """Read the Material aggregate without owning transactions or writes."""

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
    _TEXTURE_SOURCE_SEMANTICS = {
        _TEXTURE_PATH: "main",
        _SPHERE_PATH: "sphere",
        _TOON_PATH: "toon",
    }

    def __init__(
        self,
        read_support: MayaMetadataReadSupport,
        *,
        cmds_adapter: Any,
        error_factory: Callable[[str], Exception],
    ) -> None:
        self._cmds = cmds_adapter
        self._read = read_support
        self._error = error_factory

    def read_material_list_projection(self, model_root: str) -> MaterialListProjection:
        """Read only list semantics and root-bounded live assignments."""

        self._read.require_root(model_root)

        def read_semantics(
            canonical_root: str,
            bindings: tuple[str, ...],
        ) -> tuple[MaterialListSemantic, ...]:
            # Ownership was validated by the projection adapter. Read only
            # list fields here; full material/texture reads belong to detail.
            if canonical_root != self._material_identity(model_root):
                raise self._error("material list projection root identity changed during read")
            result = []
            for binding in bindings:
                if self._required_int(binding, ATTR_MMD_MATERIAL) != 1:
                    raise self._error(
                        f"{binding}.{ATTR_MMD_MATERIAL} must equal integer 1"
                    )
                result.append(
                    MaterialListSemantic(
                        index=self._required_int(
                            binding,
                            ATTR_MMD_MATERIAL_INDEX,
                            minimum=0,
                        ),
                        binding_identity=binding,
                        name=self._required_string(binding, ATTR_MMD_MATERIAL_NAME),
                        name_english=self._required_string(
                            binding,
                            ATTR_MMD_MATERIAL_NAME_EN,
                        ),
                    )
                )
            return tuple(result)

        try:
            return MayaMaterialReadProjectionAdapter(
                self._cmds
            ).read_list_projection_from_batch(model_root, read_semantics)
        except Exception as exc:
            raise self._error(
                f"failed to read material list projection for {model_root!r}: {exc}"
            ) from exc

    def read_material_detail_projection(
        self,
        model_root: str,
        index: int,
        binding: str,
        assignment: MaterialAssignmentSummary,
        *,
        material_reader: MaterialMappingReader | None = None,
    ) -> MaterialDetailProjection:
        """Read one selected material's semantics, exact slots, and preview."""

        reader = self.read_material_value if material_reader is None else material_reader
        try:
            material = reader(model_root, binding, index)
            return MayaMaterialReadProjectionAdapter(
                self._cmds
            ).read_detail_projection(model_root, material, assignment)
        except Exception as exc:
            raise self._error(
                f"failed to read material detail projection for {binding!r}: {exc}"
            ) from exc

    def read_material_value(
        self,
        model_root: str,
        binding: str,
        index: int | None = None,
        *,
        member_reader: MaterialMemberReader | None = None,
        material_reader: MaterialMappingReader | None = None,
    ) -> MmdMaterialSpec:
        """Read one selected material without enumerating other metadata."""

        root = self._material_identity(model_root)
        shader = self._material_identity(binding)
        members = (
            self.registry_material_members(root)
            if member_reader is None
            else member_reader(root)
        )
        if members is None:
            raise self._error(
                f"selected material ownership cannot be proven for root {model_root!r}"
            )
        if shader not in members:
            raise self._error(
                f"material binding {binding!r} is not owned by root {model_root!r}"
            )
        if index is not None:
            observed_index = self._required_int(shader, ATTR_MMD_MATERIAL_INDEX, minimum=0)
            if observed_index != index:
                raise self._error(
                    f"material binding index mismatch: expected {index}, got {observed_index}"
                )
        reader = self._read_material if material_reader is None else material_reader
        try:
            return MmdMaterialSpec.from_mapping(reader(shader))
        except Exception as exc:
            raise self._error(
                f"failed to read selected material value for {shader!r}: {exc}"
            ) from exc

    def read_material_value_by_index(
        self,
        model_root: str,
        index: int,
        *,
        member_reader: MaterialMemberReader | None = None,
        material_reader: MaterialMappingReader | None = None,
    ) -> MmdMaterialSpec:
        """Read exactly one registry-owned material selected by PMX index."""

        root = self._material_identity(model_root)
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise self._error("material index must be a non-negative integer")
        members = (
            self.registry_material_members(root)
            if member_reader is None
            else member_reader(root)
        )
        if members is None:
            raise self._error(
                f"selected material ownership cannot be proven for root {model_root!r}"
            )
        matches = []
        for member in members:
            shader = self._material_identity(member)
            if self._required_int(shader, ATTR_MMD_MATERIAL_INDEX, minimum=0) == index:
                matches.append(shader)
        if len(matches) != 1:
            raise self._error(
                f"material index {index} must resolve to exactly one registry binding"
            )
        reader = self._read_material if material_reader is None else material_reader
        try:
            return MmdMaterialSpec.from_mapping(reader(matches[0]))
        except Exception as exc:
            raise self._error(
                f"failed to read selected material value for index {index}: {exc}"
            ) from exc

    def iter_material_metadata(
        self,
        root: str,
        *,
        member_reader: MaterialMemberReader | None = None,
        legacy_member_reader: Callable[[str], list[str]] | None = None,
        material_reader: MaterialMappingReader | None = None,
    ) -> Iterable[Mapping[str, Any]]:
        """Yield strict PMX material mappings owned by one explicit root."""

        self._read.require_root(root)
        registry_reader = (
            self.registry_material_members if member_reader is None else member_reader
        )
        legacy_reader = (
            self.legacy_material_members
            if legacy_member_reader is None
            else legacy_member_reader
        )
        members = registry_reader(root)
        if members is None:
            members = legacy_reader(root)

        seen_bindings: set[str] = set()
        seen_indices: dict[int, str] = {}
        reader = self._read_material if material_reader is None else material_reader
        for member in members:
            identity = self._material_identity(member)
            if identity in seen_bindings:
                continue
            seen_bindings.add(identity)
            metadata = reader(identity)
            index = metadata["index"]
            previous = seen_indices.get(index)
            if previous is not None and previous != identity:
                raise self._error(
                    f"{root!r}: duplicate mmd_material_index {index} on {previous!r} and {identity!r}"
                )
            seen_indices[index] = identity
            yield metadata

    def registry_material_members(self, root: str) -> list[str] | None:
        """Return validated registry members, or ``None`` for legacy scenes."""

        requested_root = self._material_identity(root)
        if not self._has_attr(root, ATTR_MMD_MODEL_REGISTRY):
            return None
        registries = self._list_connections(
            f"{root}.{ATTR_MMD_MODEL_REGISTRY}",
            source=True,
            destination=False,
        )
        if len(registries) != 1:
            raise self._error(
                f"{root!r}: model registry must have exactly one connection"
            )
        registry = self._material_identity(registries[0])
        if not self._has_attr(registry, ATTR_MMD_REGISTRY_SCHEMA):
            raise self._error(f"{registry!r}: registry schema is missing")
        schema = self._required(registry, ATTR_MMD_REGISTRY_SCHEMA)
        if not isinstance(schema, str) or schema != "1":
            raise self._error(f"{registry!r}: unsupported registry schema {schema!r}")
        if not self._has_attr(registry, ATTR_MMD_REGISTRY_ROOT):
            raise self._error(f"{registry!r}: registry root link is missing")
        linked_roots = self._list_connections(
            f"{registry}.{ATTR_MMD_REGISTRY_ROOT}",
            source=True,
            destination=False,
        )
        if (
            len(linked_roots) != 1
            or self._material_identity(linked_roots[0]) != requested_root
        ):
            raise self._error(
                f"{registry!r}: registry root link is not exactly {root!r}"
            )
        if not self._has_attr(registry, ATTR_MMD_REGISTRY_MATERIAL_MEMBERS):
            # Registries created before the material ownership category are
            # valid legacy scenes. Their mesh/SG graph remains the bounded
            # fallback; malformed schema/root links above never fall back.
            return None
        return [
            self._material_identity(item)
            for item in self._list_connections(
                f"{registry}.{ATTR_MMD_REGISTRY_MATERIAL_MEMBERS}",
                source=True,
                destination=False,
            )
        ]

    def legacy_material_members(self, root: str) -> list[str]:
        """Discover tagged materials assigned below ``root`` only."""

        shapes = self._call_adapter(
            "list_relatives",
            root,
            allDescendents=True,
            type="mesh",
        ) or []
        members: list[str] = []
        for shape in shapes:
            shading_groups = self._list_connections(shape, type="shadingEngine")
            for shading_group in shading_groups:
                candidates = self._list_connections(
                    shading_group,
                    source=True,
                    destination=False,
                )
                for candidate in candidates:
                    identity = self._material_identity(candidate)
                    if self._node_type(identity) in {
                        "shadingEngine",
                        "file",
                        "place2dTexture",
                    }:
                        continue
                    if self._has_attr(identity, ATTR_MMD_MATERIAL):
                        members.append(identity)
        return members

    def read_material_mapping(self, shader: str) -> dict[str, Any]:
        """Read every field required by :class:`MmdMaterialSpec`."""

        return self._read_material(shader)

    def _read_material(self, shader: str) -> dict[str, Any]:
        tag = self._required_int(shader, ATTR_MMD_MATERIAL)
        if tag != 1:
            raise self._error(f"{shader}.{ATTR_MMD_MATERIAL} must equal integer 1")
        shared_flag = self._required_int(shader, ATTR_MMD_SHARED_TOON_FLAG)
        if shared_flag not in (0, 1):
            raise self._error(f"{shader}.{ATTR_MMD_SHARED_TOON_FLAG} must be 0 or 1")
        sphere_mode = self._required_int(shader, ATTR_MMD_SPHERE_MODE)
        if sphere_mode not in (0, 1, 2, 3):
            raise self._error(f"{shader}.{ATTR_MMD_SPHERE_MODE} must be between 0 and 3")
        toon_index = self._required_int(shader, ATTR_MMD_TOON_TEXTURE_INDEX, minimum=-1)
        shared_toon = bool(shared_flag)
        toon_source = self._source_path(shader, self._TOON_PATH)
        toon_explicit = self._optional_path(shader, self._EXPLICIT_RESOLVED_TOON_PATH)
        if shared_toon and (toon_source or toon_explicit):
            raise self._error(
                f"{shader}: shared toon must use table index, not a toon texture path"
            )
        return {
            "name": self._required_string(shader, ATTR_MMD_MATERIAL_NAME),
            "name_english": self._required_string(shader, ATTR_MMD_MATERIAL_NAME_EN),
            "index": self._required_int(shader, ATTR_MMD_MATERIAL_INDEX, minimum=0),
            "diffuse": self._required_vector_with_alpha(
                shader, ATTR_MMD_DIFFUSE_COLOR, self._DIFFUSE_ALPHA
            ),
            "specular": self._required_vector(shader, ATTR_MMD_SPECULAR_COLOR),
            "specular_coefficient": self._required_number(shader, ATTR_MMD_SHININESS),
            "ambient": self._required_vector(shader, ATTR_MMD_AMBIENT_COLOR),
            "draw_flags": self._required_int(shader, ATTR_MMD_DRAW_FLAGS, minimum=0),
            "edge_color": self._required_vector_with_alpha(
                shader, ATTR_MMD_EDGE_COLOR, self._EDGE_ALPHA
            ),
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

    def _required_vector_with_alpha(
        self,
        node: str,
        color_attr: str,
        alpha_attr: str,
    ) -> tuple[float, ...]:
        return self._required_vector(node, color_attr) + (
            self._required_number(node, alpha_attr),
        )

    def _source_path(self, node: str, attr: str) -> str | None:
        if not self._has_attr(node, attr):
            return None
        value = self._required_string(node, attr)
        if not value:
            return None
        # Importers may persist the resolved fileTextureName in the shader's
        # metadata attr while the exact slot file node retains the PMX source
        # path in mmd_original_texture_path. Restore that source provenance
        # only for the requested slot.
        file_nodes = self._texture_file_nodes(node, attr)
        if len(file_nodes) != 1:
            return value
        file_node = file_nodes[0]
        if not self._has_attr(file_node, self._ORIGINAL_TEXTURE_PATH):
            return value
        original = self._required_string(file_node, self._ORIGINAL_TEXTURE_PATH)
        if not original or not self._has_attr(file_node, self._FILE_TEXTURE_NAME):
            return value
        resolved = self._required_string(file_node, self._FILE_TEXTURE_NAME)
        if resolved and os.path.normcase(os.path.normpath(value)) == os.path.normcase(
            os.path.normpath(resolved)
        ):
            return original
        return value

    def _texture_file_nodes(self, shader: str, source_attr: str) -> list[str]:
        """Return file nodes connected to one exact material texture slot."""

        queries = [f"{shader}.{source_attr}"]
        semantic = self._TEXTURE_SOURCE_SEMANTICS.get(source_attr)
        route = material_shader_route(self._node_type(shader))
        if route is not None and semantic is not None:
            slot = route.texture_slot(semantic)
            if slot is not None:
                queries.append(f"{shader}.{slot.texture_attribute}")
        file_nodes: list[str] = []
        for query in queries:
            for candidate in self._list_connections(
                query,
                source=True,
                destination=False,
                type="file",
            ):
                identity = self._material_identity(candidate)
                if identity in file_nodes:
                    continue
                if self._node_type(identity) == "file":
                    file_nodes.append(identity)
        return file_nodes

    def _resolved_path(
        self,
        shader: str,
        source_attr: str,
        explicit_attr: str | None = None,
    ) -> str | None:
        """Resolve a texture path from exact-slot provenance and metadata."""

        source_path = self._source_path(shader, source_attr)
        explicit_path = self._optional_path(shader, explicit_attr)
        if not source_path:
            return explicit_path
        file_nodes = self._texture_file_nodes(shader, source_attr)
        matches: list[str] = []
        for file_node in file_nodes:
            if not self._has_attr(file_node, self._ORIGINAL_TEXTURE_PATH):
                continue
            original = self._required_string(file_node, self._ORIGINAL_TEXTURE_PATH)
            if original != source_path:
                continue
            if not self._has_attr(file_node, self._FILE_TEXTURE_NAME):
                raise self._error(
                    f"{file_node}.{self._FILE_TEXTURE_NAME} is required for provenance"
                )
            resolved = self._required_string(file_node, self._FILE_TEXTURE_NAME)
            matches.append(resolved)
        if len(matches) > 1:
            raise self._error(f"{shader}.{source_attr} has ambiguous file provenance")
        provenance = matches[0] if matches else None
        if provenance is not None and explicit_path is not None:
            if os.path.normcase(os.path.normpath(provenance)) != os.path.normcase(
                os.path.normpath(explicit_path)
            ):
                raise self._error(
                    f"{shader}.{source_attr} file provenance conflicts with {explicit_attr!r}"
                )
            return explicit_path
        return provenance if provenance is not None else explicit_path

    def _optional_path(self, node: str, attr: str | None) -> str | None:
        if attr is None or not self._has_attr(node, attr):
            return None
        value = self._required_string(node, attr)
        return value or None

    def _material_identity(self, node: Any) -> str:
        return self._read.canonical_identity(node)

    def _list_connections(self, query: Any, **kwargs: Any) -> list[str]:
        result = self._call_adapter("list_connections", query, **kwargs) or []
        if isinstance(result, (str, bytes, bytearray)):
            raise self._error(f"list_connections({query!r}) returned a scalar")
        return list(result)

    def _node_type(self, node: str) -> str:
        try:
            value = self._call_adapter("node_type", node)
        except Exception:
            return ""
        return value if isinstance(value, str) else ""

    def _call_adapter(self, method: str, *args: Any, **kwargs: Any) -> Any:
        return self._read.call_adapter(method, *args, **kwargs)

    def _required(self, node: str, attr: str) -> Any:
        return self._read.required(node, attr)

    def _required_string(self, node: str, attr: str) -> str:
        return self._read.required_string(node, attr)

    def _required_int(
        self,
        node: str,
        attr: str,
        *,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int:
        return self._read.required_int(
            node,
            attr,
            minimum=minimum,
            maximum=maximum,
        )

    def _required_number(self, node: str, attr: str) -> float:
        return self._read.required_number(node, attr)

    def _required_vector(self, node: str, attr: str) -> tuple[float, float, float]:
        return self._read.required_vector(node, attr)

    def _has_attr(self, node: str, attr: str) -> bool:
        return self._read.has_attr(node, attr)


__all__ = ["MayaMaterialMetadataRepository"]
