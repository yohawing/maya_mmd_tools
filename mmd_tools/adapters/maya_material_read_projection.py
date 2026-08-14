"""Project one root-bounded Maya material list from validated semantics."""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from mmd_tools.core.constants import (
    ATTR_MMD_MATERIAL,
    ATTR_MMD_SHADER_OUTLINE_ENABLED,
    ATTR_MMD_MODEL_REGISTRY,
    ATTR_MMD_REGISTRY_MATERIAL_MEMBERS,
    ATTR_MMD_REGISTRY_ROOT,
    ATTR_MMD_REGISTRY_SCHEMA,
)
from mmd_tools.core.material_read_projection import (
    MaterialAssignmentKind,
    MaterialAssignmentSummary,
    MaterialDetailProjection,
    MaterialListItemProjection,
    MaterialListProjection,
    MaterialListSemantic,
    MaterialPreviewState,
    MaterialTextureBinding,
    MaterialTextureProvenance,
    MaterialTextureSlot,
)
from mmd_tools.adapters.maya_material_shader_route import material_shader_route
from mmd_tools.core.maya_identity import canonical_node_identity
from mmd_tools.core.model_authoring_spec import MmdMaterialSpec
from mmd_tools.core.model_registry import REGISTRY_SCHEMA_VERSION


_FACE_MEMBER = re.compile(r"^(?P<node>.+)\.f\[(?P<start>\d+)(?::(?P<end>\d+))?\]$")
SemanticMaterialBatchReader = Callable[
    [str, Tuple[str, ...]],
    Tuple[MaterialListSemantic, ...],
]


class MayaMaterialReadProjectionError(RuntimeError):
    """Raised when model ownership or assignment cannot be proven safely."""


class MayaMaterialReadProjectionAdapter:
    """Observe ownership/assignment while a caller owns semantic reads."""

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter

    def read_detail_projection(
        self,
        model_root: str,
        material: MmdMaterialSpec,
        assignment: MaterialAssignmentSummary,
    ) -> MaterialDetailProjection:
        """Project one selected material without broad shader-graph discovery."""

        if not isinstance(material, MmdMaterialSpec):
            raise TypeError("material must be an MmdMaterialSpec")
        if not isinstance(assignment, MaterialAssignmentSummary):
            raise TypeError("assignment must be a MaterialAssignmentSummary")
        root = self._canonical_dag_identity(model_root, "model root")
        shader = self._canonical_identity(
            material.binding_identity,
            "material binding",
        )
        if material.binding_identity != shader:
            raise MayaMaterialReadProjectionError(
                "material binding_identity must already be canonical"
            )
        shader_type = self._adapter.node_type(shader)
        if not isinstance(shader_type, str) or not shader_type:
            raise MayaMaterialReadProjectionError(
                "material shader type must be a non-empty string"
            )
        route = material_shader_route(shader_type)
        semantic_slots = {
            "main": MaterialTextureSlot.MAIN,
            "sphere": MaterialTextureSlot.SPHERE,
            "toon": MaterialTextureSlot.TOON,
        }
        paths = {
            MaterialTextureSlot.MAIN: (
                material.texture_path,
                material.resolved_texture_path,
            ),
            MaterialTextureSlot.SPHERE: (
                material.sphere_texture_path,
                material.resolved_sphere_texture_path,
            ),
            MaterialTextureSlot.TOON: (
                material.toon_texture_path,
                material.resolved_toon_texture_path,
            ),
        }
        if route is None and any(
            source_path or resolved_path
            for source_path, resolved_path in paths.values()
        ):
            raise MayaMaterialReadProjectionError(
                "unsupported shader type cannot project authored texture paths"
            )
        textures = []
        for slot_route in (() if route is None else route.texture_slots):
            slot = semantic_slots[slot_route.semantic]
            shader_plug = "{}.{}".format(shader, slot_route.texture_attribute)
            raw = self._adapter.list_connections(
                shader_plug,
                source=True,
                destination=False,
                type="file",
            ) or ()
            candidates = self._sequence(raw, "{} texture file nodes".format(slot.value))
            file_nodes = []
            for candidate in candidates:
                identity = self._canonical_identity(candidate, "texture file node")
                if self._adapter.node_type(identity) != "file":
                    raise MayaMaterialReadProjectionError(
                        "{} texture source is not a file node".format(slot.value)
                    )
                if identity not in file_nodes:
                    file_nodes.append(identity)
            if len(file_nodes) > 1:
                raise MayaMaterialReadProjectionError(
                    "{} texture slot has ambiguous file nodes".format(slot.value)
                )
            binding = MaterialTextureBinding(
                slot,
                shader_plug,
                file_nodes[0] if file_nodes else None,
            )
            source_path, resolved_path = paths[slot]
            textures.append(
                MaterialTextureProvenance(
                    slot,
                    source_path,
                    resolved_path,
                    binding,
                )
            )

        outline_enabled = False
        if self._adapter.attribute_exists(ATTR_MMD_SHADER_OUTLINE_ENABLED, shader):
            raw_outline = self._adapter.get_attr(
                "{}.{}".format(shader, ATTR_MMD_SHADER_OUTLINE_ENABLED)
            )
            if not (
                type(raw_outline) is bool
                or (type(raw_outline) is int and raw_outline in (0, 1))
            ):
                raise MayaMaterialReadProjectionError(
                    "material outline preview must be bool or integer 0/1"
                )
            outline_enabled = bool(raw_outline)
        elif shader_type == "dx11Shader" and self._adapter.attribute_exists(
            "technique", shader
        ):
            technique = self._adapter.get_attr("{}.technique".format(shader))
            if not isinstance(technique, str):
                raise MayaMaterialReadProjectionError(
                    "legacy DX11 technique must be a string"
                )
            outline_enabled = bool(technique) and "NoEdge" not in technique
        return MaterialDetailProjection(
            root,
            material,
            assignment,
            tuple(textures),
            MaterialPreviewState(shader_type, outline_enabled),
        )

    def read_list_projection(
        self,
        model_root: str,
        semantic_materials: Tuple[MaterialListSemantic, ...],
    ) -> MaterialListProjection:
        """Return PMX-indexed rows after one root mesh scan.

        ``semantic_materials`` must be one already-validated narrow backend read for
        this root. This adapter never rebuilds semantic fields from Maya
        attributes or invokes a per-material semantic reader.
        """

        if not isinstance(semantic_materials, tuple) or not all(
            isinstance(material, MaterialListSemantic) for material in semantic_materials
        ):
            raise TypeError("semantic_materials must be a tuple of MaterialListSemantic")
        root, meshes, mesh_shading_groups, owned_bindings = self._observe_ownership(
            model_root
        )
        return self._project_semantics(
            root,
            meshes,
            mesh_shading_groups,
            owned_bindings,
            semantic_materials,
        )

    def read_list_projection_from_batch(
        self,
        model_root: str,
        semantic_batch_reader: SemanticMaterialBatchReader,
    ) -> MaterialListProjection:
        """Observe ownership once and invoke one batch semantic read.

        The callback receives already-canonical owned bindings. It must read
        only those bindings and return one immutable tuple; root/registry
        discovery remains this adapter's responsibility.
        """

        if not callable(semantic_batch_reader):
            raise TypeError("semantic_batch_reader must be callable")
        root, meshes, mesh_shading_groups, owned_bindings = self._observe_ownership(
            model_root
        )
        semantic_materials = semantic_batch_reader(root, owned_bindings)
        return self._project_semantics(
            root,
            meshes,
            mesh_shading_groups,
            owned_bindings,
            semantic_materials,
        )

    def _observe_ownership(
        self,
        model_root: str,
    ) -> Tuple[
        str,
        Tuple[str, ...],
        Dict[str, Tuple[str, ...]],
        Tuple[str, ...],
    ]:
        root = self._canonical_dag_identity(model_root, "model root")
        meshes = self._owned_meshes(root)
        mesh_shading_groups = self._mesh_shading_groups(meshes)
        raw_members = self._owned_materials(root, mesh_shading_groups)

        owned_bindings: List[str] = []
        for member in raw_members:
            binding = self._canonical_identity(member, "material binding")
            if binding in owned_bindings:
                raise MayaMaterialReadProjectionError(
                    "duplicate canonical material binding {!r}".format(binding)
                )
            owned_bindings.append(binding)
        return root, meshes, mesh_shading_groups, tuple(owned_bindings)

    def _project_semantics(
        self,
        root: str,
        meshes: Tuple[str, ...],
        mesh_shading_groups: Dict[str, Tuple[str, ...]],
        owned_bindings: Tuple[str, ...],
        semantic_materials: Tuple[MaterialListSemantic, ...],
    ) -> MaterialListProjection:
        if not isinstance(semantic_materials, tuple) or not all(
            isinstance(material, MaterialListSemantic) for material in semantic_materials
        ):
            raise TypeError("semantic_materials must be a tuple of MaterialListSemantic")

        material_by_binding: Dict[str, MaterialListSemantic] = {}
        seen_indices: Dict[int, str] = {}
        for material in semantic_materials:
            binding = self._canonical_identity(
                material.binding_identity,
                "semantic material binding",
            )
            if material.binding_identity != binding:
                raise MayaMaterialReadProjectionError(
                    "semantic material binding_identity must already be canonical"
                )
            if binding in material_by_binding:
                raise MayaMaterialReadProjectionError(
                    "duplicate semantic material binding {!r}".format(binding)
                )
            previous = seen_indices.get(material.index)
            if previous is not None:
                raise MayaMaterialReadProjectionError(
                    "duplicate material index {} on {!r} and {!r}".format(
                        material.index,
                        previous,
                        binding,
                    )
                )
            material_by_binding[binding] = material
            seen_indices[material.index] = binding
        if set(owned_bindings) != set(material_by_binding):
            raise MayaMaterialReadProjectionError(
                "semantic materials do not exactly match discovered material ownership"
            )

        rows: List[MaterialListItemProjection] = []
        membership_cache: Dict[str, Optional[Tuple[str, ...]]] = {}
        shading_group_owner: Dict[str, str] = {}
        for binding, material in sorted(
            material_by_binding.items(),
            key=lambda item: item[1].index,
        ):
            assignment = self._assignment_summary(
                root,
                binding,
                meshes,
                membership_cache,
                shading_group_owner,
            )
            rows.append(MaterialListItemProjection(material, assignment))

        rows.sort(key=lambda row: row.index)
        return MaterialListProjection(root, tuple(rows))

    def _owned_meshes(self, root: str) -> Tuple[str, ...]:
        raw = self._adapter.list_relatives(
            root,
            allDescendents=True,
            fullPath=True,
            type="mesh",
        ) or ()
        values = self._sequence(raw, "model mesh descendants")
        meshes: List[str] = []
        for value in values:
            mesh = self._canonical_dag_identity(value, "model mesh")
            if not mesh.startswith(root + "|"):
                raise MayaMaterialReadProjectionError(
                    "mesh resolves outside model root: {!r}".format(mesh)
                )
            if mesh in meshes:
                raise MayaMaterialReadProjectionError(
                    "duplicate or instanced model mesh path {!r}".format(mesh)
                )
            try:
                intermediate = self._adapter.get_attr(
                    "{}.intermediateObject".format(mesh)
                )
            except Exception as exc:
                raise MayaMaterialReadProjectionError(
                    "model mesh intermediate state cannot be read: {!r}".format(mesh)
                ) from exc
            if type(intermediate) not in (bool, int) or intermediate not in (False, True, 0, 1):
                raise MayaMaterialReadProjectionError(
                    "model mesh intermediate state is invalid: {!r}".format(mesh)
                )
            if bool(intermediate):
                continue
            meshes.append(mesh)
        return tuple(meshes)

    def _mesh_shading_groups(
        self,
        meshes: Tuple[str, ...],
    ) -> Dict[str, Tuple[str, ...]]:
        result: Dict[str, Tuple[str, ...]] = {}
        for mesh in meshes:
            raw = self._adapter.list_connections(mesh, type="shadingEngine") or ()
            groups = []
            for value in self._sequence(raw, "mesh shading groups"):
                identity = self._canonical_identity(value, "shading group")
                if identity not in groups:
                    groups.append(identity)
            result[mesh] = tuple(groups)
        return result

    def _owned_materials(
        self,
        root: str,
        mesh_shading_groups: Dict[str, Tuple[str, ...]],
    ) -> Tuple[str, ...]:
        registry_members = self._registry_members(root)
        if registry_members is not None:
            return registry_members

        members: List[str] = []
        for groups in mesh_shading_groups.values():
            for shading_group in groups:
                raw = self._adapter.list_connections(
                    shading_group,
                    source=True,
                    destination=False,
                ) or ()
                for candidate in self._sequence(raw, "legacy shading inputs"):
                    identity = self._canonical_identity(candidate, "legacy material")
                    if not self._adapter.attribute_exists(ATTR_MMD_MATERIAL, identity):
                        continue
                    if identity not in members:
                        members.append(identity)
        return tuple(members)

    def _registry_members(self, root: str) -> Optional[Tuple[str, ...]]:
        if not self._adapter.attribute_exists(ATTR_MMD_MODEL_REGISTRY, root):
            return None
        registries = self._connections(
            "{}.{}".format(root, ATTR_MMD_MODEL_REGISTRY),
            "model registry",
        )
        if len(registries) != 1:
            raise MayaMaterialReadProjectionError(
                "model root must have exactly one registry connection"
            )
        registry = self._canonical_identity(registries[0], "model registry")
        if not self._adapter.attribute_exists(ATTR_MMD_REGISTRY_SCHEMA, registry):
            raise MayaMaterialReadProjectionError("model registry schema is missing")
        schema = self._adapter.get_attr(
            "{}.{}".format(registry, ATTR_MMD_REGISTRY_SCHEMA)
        )
        if str(schema or "") != REGISTRY_SCHEMA_VERSION:
            raise MayaMaterialReadProjectionError(
                "unsupported model registry schema {!r}".format(schema)
            )
        if not self._adapter.attribute_exists(ATTR_MMD_REGISTRY_ROOT, registry):
            raise MayaMaterialReadProjectionError("model registry root link is missing")
        roots = self._connections(
            "{}.{}".format(registry, ATTR_MMD_REGISTRY_ROOT),
            "model registry root",
        )
        if len(roots) != 1 or self._canonical_identity(roots[0], "registry root") != root:
            raise MayaMaterialReadProjectionError(
                "model registry belongs to another root"
            )
        if not self._adapter.attribute_exists(
            ATTR_MMD_REGISTRY_MATERIAL_MEMBERS,
            registry,
        ):
            return None
        raw = self._connections(
            "{}.{}".format(registry, ATTR_MMD_REGISTRY_MATERIAL_MEMBERS),
            "registry material members",
        )
        return tuple(
            self._canonical_identity(member, "registry material member")
            for member in raw
        )

    def _assignment_summary(
        self,
        root: str,
        binding: str,
        meshes: Tuple[str, ...],
        membership_cache: Dict[str, Optional[Tuple[str, ...]]],
        shading_group_owner: Dict[str, str],
    ) -> MaterialAssignmentSummary:
        groups = set(
            self._canonical_identity(value, "material shading group")
            for value in self._sequence(
                self._adapter.list_connections(binding, type="shadingEngine") or (),
                "material shading groups",
            )
        )
        if not groups:
            return MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, 0, 0)

        whole_meshes: Set[str] = set()
        explicit_meshes: Set[str] = set()
        explicit_faces = 0
        for group in sorted(groups):
            owner = shading_group_owner.setdefault(group, binding)
            if owner != binding:
                raise MayaMaterialReadProjectionError(
                    "shading group is connected to multiple owned materials"
                )
            if group not in membership_cache:
                try:
                    raw_members = self._adapter.sets(group, query=True) or ()
                    membership_cache[group] = tuple(
                        str(value)
                        for value in self._sequence(raw_members, "shading group members")
                    )
                except Exception:
                    membership_cache[group] = None
            members = membership_cache[group]
            if members is None:
                return MaterialAssignmentSummary(
                    MaterialAssignmentKind.UNKNOWN,
                    0,
                    None,
                )
            for member in members:
                face = _FACE_MEMBER.match(member)
                raw_node = face.group("node") if face is not None else member
                node = self._canonical_dag_identity(raw_node, "shading member")
                if not node.startswith(root + "|"):
                    raise MayaMaterialReadProjectionError(
                        "material assignment resolves outside model root: {!r}".format(
                            node
                        )
                    )
                matching_meshes = self._matching_meshes(node, meshes)
                if len(matching_meshes) != 1:
                    raise MayaMaterialReadProjectionError(
                        "material assignment must resolve to exactly one owned mesh: {!r}".format(
                            node
                        )
                    )
                mesh = matching_meshes[0]
                if face is None:
                    whole_meshes.add(mesh)
                    continue
                start = int(face.group("start"))
                end = int(face.group("end") or start)
                if end < start:
                    raise MayaMaterialReadProjectionError(
                        "material face range is descending: {!r}".format(member)
                    )
                explicit_meshes.add(mesh)
                explicit_faces += end - start + 1

        mesh_count = len(whole_meshes | explicit_meshes)
        if whole_meshes and explicit_faces:
            kind = MaterialAssignmentKind.MIXED
            face_count = explicit_faces
        elif whole_meshes:
            kind = MaterialAssignmentKind.WHOLE_OBJECT
            face_count = None
        elif explicit_faces:
            kind = MaterialAssignmentKind.EXPLICIT_FACES
            face_count = explicit_faces
        else:
            return MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, 0, 0)
        return MaterialAssignmentSummary(kind, mesh_count, face_count)

    @staticmethod
    def _matching_meshes(node: str, meshes: Tuple[str, ...]) -> Tuple[str, ...]:
        """Return all owned shapes addressed by a shape or transform member."""

        return tuple(
            mesh
            for mesh in meshes
            if mesh == node or mesh.rsplit("|", 1)[0] == node
        )

    def _connections(self, plug: str, label: str) -> Tuple[Any, ...]:
        raw = self._adapter.list_connections(
            plug,
            source=True,
            destination=False,
        ) or ()
        return self._sequence(raw, label)

    def _canonical_identity(self, value: Any, label: str) -> str:
        identity = canonical_node_identity(self._adapter, value)
        if identity is None:
            raise MayaMaterialReadProjectionError(
                "{} has no unique canonical identity: {!r}".format(label, value)
            )
        return identity

    def _canonical_dag_identity(self, value: Any, label: str) -> str:
        if not isinstance(value, str) or not value:
            raise MayaMaterialReadProjectionError("{} is invalid".format(label))
        paths = self._adapter.ls(value, long=True, allPaths=True) or ()
        paths = self._sequence(paths, "{} paths".format(label))
        if len(paths) != 1 or not isinstance(paths[0], str) or not paths[0].startswith("|"):
            raise MayaMaterialReadProjectionError(
                "{} is missing or instanced: {!r}".format(label, value)
            )
        return paths[0]

    @staticmethod
    def _sequence(value: Any, label: str) -> Tuple[Any, ...]:
        if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Iterable):
            raise MayaMaterialReadProjectionError("{} must be a sequence".format(label))
        return tuple(value)


__all__ = [
    "MayaMaterialReadProjectionAdapter",
    "MayaMaterialReadProjectionError",
    "SemanticMaterialBatchReader",
]
