"""Read strict normalized PMX Morph metadata through Maya."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from mmd_tools.adapters.maya_metadata_read_support import MayaMetadataReadSupport
from mmd_tools.adapters.maya_morph_binding_query import (
    MayaMorphBindingQueryError,
    resolve_maya_morph_binding,
)
from mmd_tools.adapters.maya_morph_read_projection import (
    CachedMorphBindingQueryAdapter,
    MayaMorphReadProjectionAdapter,
)
from mmd_tools.core.constants import (
    ATTR_MMD_BONE_MORPH_OFFSETS_RAW_JSON,
    ATTR_MMD_FLIP_MORPH_OFFSETS_JSON,
    ATTR_MMD_IMPULSE_MORPH_OFFSETS_JSON,
    ATTR_MMD_IMPORT_SCALE,
    ATTR_MMD_MODEL_REGISTRY,
    ATTR_MMD_MODEL_ROOT,
    ATTR_MMD_REGISTRY_MORPH_MEMBERS,
    ATTR_MMD_REGISTRY_ROOT,
    ATTR_MMD_REGISTRY_SCHEMA,
    ATTR_MMD_SOURCE_VERTEX_INDICES,
    ATTR_MMD_UV_MORPH_OFFSETS_JSON,
)
from mmd_tools.core.logger import get_logger
from mmd_tools.core.morph_binding_resolver import (
    MorphBindingRequest,
    MorphBindingResolution,
    MorphBindingResolutionError,
)
from mmd_tools.core.morph_read_projection import (
    MorphAuthoringReadSnapshot,
    MorphProjectionRequest,
)
from mmd_tools.core.model_authoring_spec import MmdModelAuthoringSpec, MmdMorphSpec
from mmd_tools.core.morph_topology import (
    MorphTopologyInspection,
    inspect_group_topology,
)


logger = get_logger(__name__)

MorphSpecReader = Callable[[str], MmdModelAuthoringSpec]


@dataclass
class _MorphSnapshotReadContext:
    """Refresh-local observations shared by semantic and projection reads."""

    root: str
    query: CachedMorphBindingQueryAdapter
    controller: str | None
    resolutions: dict[int, MorphBindingResolution]


class MayaMorphMetadataRepository:
    """Read the Morph aggregate without owning transactions or writes."""

    def __init__(
        self,
        read_support: MayaMetadataReadSupport,
        *,
        cmds_adapter: Any,
        error_factory: Callable[[str], Exception],
    ) -> None:
        self._read = read_support
        self._cmds = cmds_adapter
        self._error = error_factory
        self._snapshot_read_context: _MorphSnapshotReadContext | None = None

    def inspect_morph_topology(self, model_root: str) -> MorphTopologyInspection:
        """Inspect derived controller topology without changing the scene."""

        self._read.require_root(model_root)
        root = self._material_identity(model_root)
        members = self.registry_morph_members(root)
        if members is None:
            members = self.legacy_morph_members(root)
        morphs = []
        for member in members:
            node = self._material_identity(member)
            morph_type = self._required_string(node, "mmd_morph_type")
            offsets = []
            if morph_type in {"group", "flip"}:
                attr = {
                    "group": "mmd_group_morph_offsets_json",
                    "flip": ATTR_MMD_FLIP_MORPH_OFFSETS_JSON,
                }[morph_type]
                offsets = self._required_morph_offsets(node, attr, morph_type)
            morphs.append(
                {
                    "index": self._required_int(node, "mmd_morph_index", minimum=0),
                    "morph_type": morph_type,
                    "offsets": offsets,
                }
            )
        controllers = self._list_connections(
            f"{root}.mmd_morph_controller", source=True, destination=False
        )
        if len(controllers) != 1:
            raise self._error(
                "morph topology inspection requires exactly one morph controller"
            )
        controller = self._material_identity(controllers[0])
        version = self._call_adapter("get_attr", f"{controller}.topologyVersion")
        source = self._call_adapter("get_attr", f"{controller}.groupTopology")
        return inspect_group_topology(morphs, version, source)

    def read_morph_authoring_snapshot(
        self,
        model_root: str,
        *,
        spec_reader: MorphSpecReader,
    ) -> MorphAuthoringReadSnapshot:
        """Read semantic Morph data and its runtime projection in one generation."""

        if self._snapshot_read_context is not None:
            raise self._error("a morph authoring snapshot read is already active")
        root = self._material_identity(model_root)
        self._read.require_root(root)
        context = _MorphSnapshotReadContext(
            root=root,
            query=CachedMorphBindingQueryAdapter(self._cmds),
            controller=None,
            resolutions={},
        )
        self._snapshot_read_context = context
        try:
            spec = spec_reader(root)
            controller = self._snapshot_morph_controller(context, required=False)
            if controller:
                version = context.query.get_attr(f"{controller}.topologyVersion")
                source = context.query.get_attr(f"{controller}.groupTopology")
                inspection = inspect_group_topology(spec.morphs, version, source)
            elif any(morph.morph_type in {"group", "flip"} for morph in spec.morphs):
                inspection = inspect_group_topology(spec.morphs, None, None)
            else:
                inspection = MorphTopologyInspection({}, {}, ())
            requests = tuple(
                MorphProjectionRequest(
                    raw_pmx_name=morph.name,
                    global_morph_index=morph.index,
                    binding_identity=self._required_snapshot_binding(
                        morph.binding_identity
                    ),
                    morph_type=morph.morph_type,
                )
                for morph in spec.morphs
            )
            projection = MayaMorphReadProjectionAdapter(
                self._cmds
            ).read_validated_spec_projection(
                root,
                requests,
                controller,
                context.resolutions,
                inspection.stored if inspection.valid else {},
                query_adapter=context.query,
            )
            return MorphAuthoringReadSnapshot(
                spec=spec,
                projection=projection,
                topology_inspection=inspection,
            )
        except Exception as exc:
            raise self._error(
                f"failed to read morph authoring snapshot for {root!r}: {exc}"
            ) from exc
        finally:
            self._snapshot_read_context = None

    @staticmethod
    def _required_snapshot_binding(value: str | None) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError(
                "morph authoring snapshot requires every semantic binding identity"
            )
        return value

    def _snapshot_morph_controller(
        self,
        context: _MorphSnapshotReadContext,
        *,
        required: bool = True,
    ) -> str:
        if context is not self._snapshot_read_context:
            raise self._error("morph snapshot read context identity mismatch")
        if context.controller is not None:
            return context.controller
        if not context.query.attribute_exists("mmd_morph_controller", context.root):
            if required:
                raise self._error(f"{context.root}.mmd_morph_controller is required")
            return ""
        controllers = context.query.list_connections(
            f"{context.root}.mmd_morph_controller",
            source=True,
            destination=False,
        ) or ()
        if isinstance(controllers, (str, bytes, bytearray)):
            raise self._error(
                f"{context.root}.mmd_morph_controller must be a connection sequence"
            )
        if not controllers and not required:
            return ""
        if len(controllers) != 1:
            raise self._error(
                f"{context.root}.mmd_morph_controller must have exactly one controller"
            )
        names = context.query.ls(controllers[0], long=True) or ()
        if isinstance(names, (str, bytes, bytearray)) or len(names) != 1:
            raise self._error("morph controller has no unique canonical identity")
        context.controller = names[0]
        return context.controller

    def read_morph_value(
        self,
        model_root: str,
        binding: str,
        index: int | None = None,
    ) -> MmdMorphSpec:
        """Read one selected morph binding without enumerating other metadata."""

        root = self._material_identity(model_root)
        node = self._material_identity(binding)
        self.require_selected_morph(root, node, index)
        return MmdMorphSpec.from_mapping(self.read_morph_mapping(node, root=root))

    def iter_morph_metadata(self, root: str) -> Iterable[Mapping[str, Any]]:
        """Yield strict raw PMX morph mappings owned by one explicit root."""

        self._read.require_root(root)
        members = self.registry_morph_members(root)
        if members is None:
            members = self.legacy_morph_members(root)

        seen_bindings: set[str] = set()
        seen_indices: dict[int, str] = {}
        for member in members:
            identity = self._material_identity(member)
            if identity in seen_bindings:
                raise self._error(
                    f"{root!r}: duplicate morph binding identity {identity!r}"
                )
            seen_bindings.add(identity)
            if self._node_type(identity) != "network":
                raise self._error(
                    f"{identity!r}: morph binding must be a network node"
                )
            metadata = self.read_morph_mapping(identity, root=root)
            index = metadata["index"]
            previous = seen_indices.get(index)
            if previous is not None:
                raise self._error(
                    f"{root!r}: duplicate mmd_morph_index {index} on {previous!r} and {identity!r}"
                )
            seen_indices[index] = identity
            yield metadata

    def registry_morph_members(self, root: str) -> list[str] | None:
        """Return validated registry morph members, or ``None`` for legacy scenes."""

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
        if not self._has_attr(registry, ATTR_MMD_REGISTRY_MORPH_MEMBERS):
            return []
        return [
            self._material_identity(item)
            for item in self._list_connections(
                f"{registry}.{ATTR_MMD_REGISTRY_MORPH_MEMBERS}",
                source=True,
                destination=False,
            )
        ]

    def legacy_morph_members(self, root: str) -> list[str]:
        """Discover legacy morph nodes only through their explicit root link."""

        requested_root = self._material_identity(root)
        candidates = self._call_adapter("ls", type="network") or []
        if isinstance(candidates, (str, bytes, bytearray)):
            raise self._error("ls(type='network') returned a scalar")
        members: list[str] = []
        for candidate in candidates:
            identity = self._material_identity(candidate)
            if not self._has_attr(identity, "mmd_morph_type"):
                continue
            if not self._has_attr(identity, ATTR_MMD_MODEL_ROOT):
                continue
            roots = self._list_connections(
                f"{identity}.{ATTR_MMD_MODEL_ROOT}",
                source=True,
                destination=False,
            )
            if len(roots) != 1:
                raise self._error(
                    f"{identity!r}: legacy morph root ownership must have exactly one connection"
                )
            if self._material_identity(roots[0]) == requested_root:
                members.append(identity)
        return members

    def read_morph_mapping(self, node: str, *, root: str | None = None) -> dict[str, Any]:
        """Read one strict Morph mapping for semantic or transaction readback."""

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
                raise self._error(
                    f"{node} vertex morph requires an explicit model root for blendShape binding"
                )
            raw_name = self._required_string(node, "mmd_morph_name")
            index = self._required_int(node, "mmd_morph_index", minimum=0)
            offsets = self._read_vertex_blendshape_offsets(root, node, raw_name, index)
        else:
            try:
                offsets_attr = attr_by_type[morph_type]
            except KeyError as exc:
                raise self._error(
                    f"{node}.mmd_morph_type is unknown: {morph_type!r}"
                ) from exc
            offsets = self._required_morph_offsets(node, offsets_attr, morph_type)
        unsupported = morph_type in {"flip", "impulse"}
        return {
            "name": raw_name
            if morph_type == "vertex"
            else self._required_string(node, "mmd_morph_name"),
            "name_english": self._required_string(node, "mmd_morph_name_en"),
            "index": index
            if morph_type == "vertex"
            else self._required_int(node, "mmd_morph_index", minimum=0),
            "panel": self._required_int(node, "mmd_morph_panel", minimum=0, maximum=4),
            "morph_type": morph_type,
            "offsets": offsets,
            "binding_identity": node,
            "runtime_capability": "unsupported" if unsupported else "supported",
            "loss_policy": "reject" if unsupported else "none",
        }

    def require_selected_morph(
        self,
        root: str,
        node: str,
        index: int | None,
    ) -> int:
        """Validate selected morph ownership using only registry/index attrs."""

        if not self._call_adapter("object_exists", node):
            raise self._error(f"selected morph does not exist: {node!r}")
        if self._node_type(node) != "network":
            raise self._error(f"selected morph binding must be a network node: {node!r}")
        canonical = self._material_identity(node)
        if self._has_attr(root, ATTR_MMD_MODEL_REGISTRY):
            members = self.registry_morph_members(root) or []
            owned = {self._material_identity(member) for member in members}
            if canonical not in owned:
                raise self._error(
                    f"selected morph {node!r} is not owned by root {root!r}"
                )
        else:
            if not self._has_attr(node, ATTR_MMD_MODEL_ROOT):
                raise self._error(
                    f"selected morph {node!r} has no explicit root ownership"
                )
            roots = self._list_connections(
                f"{node}.{ATTR_MMD_MODEL_ROOT}",
                source=True,
                destination=False,
            )
            if len(roots) != 1 or self._material_identity(roots[0]) != root:
                raise self._error(
                    f"selected morph {node!r} is not owned by root {root!r}"
                )
        observed = self._required_int(node, "mmd_morph_index", minimum=0)
        if index is not None and observed != index:
            raise self._error(
                f"selected morph index mismatch: expected {index}, got {observed}"
            )
        return observed

    def _read_vertex_blendshape_offsets(
        self,
        root: str,
        binding: str,
        raw_name: str,
        morph_index: int,
    ) -> list[dict[str, Any]]:
        """Read sparse PMX deltas from exact controller-owned blendShapes."""

        context = self._snapshot_read_context
        if context is not None:
            canonical_root = (
                root if root == context.root else self._material_identity(root)
            )
            if canonical_root != context.root:
                raise self._error(
                    "morph snapshot read attempted to cross model-root identity"
                )
            controller = self._snapshot_morph_controller(context)
            query_adapter = context.query
        else:
            controllers = tuple(
                self._list_connections(
                    f"{root}.mmd_morph_controller",
                    source=True,
                    destination=False,
                )
            )
            if len(controllers) != 1:
                raise self._error(
                    f"{root}.mmd_morph_controller must have exactly one controller for vertex morphs"
                )
            controller = self._material_identity(controllers[0])
            query_adapter = self._cmds
        request = MorphBindingRequest(
            raw_pmx_name=raw_name,
            global_morph_index=morph_index,
            controller_identity=controller,
            controller_slot=morph_index,
        )
        try:
            resolution = resolve_maya_morph_binding(query_adapter, request)
        except (MayaMorphBindingQueryError, MorphBindingResolutionError) as exc:
            raise self._error(
                f"vertex morph {binding!r} binding resolution failed: {exc}"
            ) from exc
        for warning in resolution.warnings:
            logger.warning("[%s] %s", warning.code, warning.message)
        if context is not None:
            previous = context.resolutions.get(morph_index)
            if previous is not None and previous != resolution:
                raise self._error(
                    f"morph snapshot captured conflicting resolution for index {morph_index}"
                )
            context.resolutions[morph_index] = resolution

        scale = self._required_number(root, ATTR_MMD_IMPORT_SCALE)
        if scale <= 0.0:
            raise self._error(f"{root}.{ATTR_MMD_IMPORT_SCALE} must be positive")
        offsets: dict[int, tuple[float, float, float]] = {}
        target_seen = False
        for resolved_binding in resolution.bindings:
            blend_shape = resolved_binding.blend_shape_identity
            target_index = resolved_binding.logical_target_index
            geometries = tuple(
                self._call_adapter("blend_shape", blend_shape, query=True, geometry=True)
                or ()
            )
            geometry_indices = tuple(
                self._call_adapter(
                    "blend_shape", blend_shape, query=True, geometryIndices=True
                )
                or ()
            )
            if len(geometries) != len(geometry_indices):
                raise self._error(
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
                components = self._call_adapter(
                    "get_attr", f"{item}.inputComponentsTarget"
                ) or ()
                qualified_components = [
                    str(component)
                    if ".vtx[" in str(component)
                    else f"{geometry}.{component}"
                    for component in components
                ]
                flattened_components = (
                    tuple(
                        self._call_adapter("ls", qualified_components, flatten=True)
                        or ()
                    )
                    if qualified_components
                    else ()
                )
                if len(points) != len(flattened_components):
                    raise self._error(f"{item} points/components lengths differ")
                for point, component in zip(points, flattened_components):
                    component_match = re.search(
                        r"(?:^|\.)vtx\[(\d+)\]$", str(component)
                    )
                    if component_match is None:
                        raise self._error(
                            f"{item} contains invalid component {component!r}"
                        )
                    local_index = int(component_match.group(1))
                    if not 0 <= local_index < len(source_indices):
                        raise self._error(
                            f"{item} component index {local_index} is out of range"
                        )
                    try:
                        delta = tuple(float(point[axis]) / scale for axis in range(3))
                    except (IndexError, TypeError, ValueError) as exc:
                        raise self._error(
                            f"{item} contains invalid point data {point!r}"
                        ) from exc
                    if not all(math.isfinite(value) for value in delta):
                        raise self._error(f"{item} contains non-finite point data")
                    pmx_delta = tuple(
                        0.0 if value == 0.0 else value
                        for value in (delta[0], delta[1], -delta[2])
                    )
                    source_index = source_indices[local_index]
                    if source_index in offsets:
                        raise self._error(
                            f"vertex morph {morph_index} maps source vertex {source_index} more than once"
                        )
                    offsets[source_index] = pmx_delta
        if not target_seen:
            raise self._error(
                f"vertex morph {morph_index} has no full-weight blendShape target"
            )
        return [
            {"vertex_index": index, "position_offset": list(offsets[index])}
            for index in sorted(offsets)
            if any(abs(value) > 1e-8 for value in offsets[index])
        ]

    def _read_vertex_source_indices(self, geometry: str) -> list[int]:
        """Resolve local vertex order to PMX source indices."""

        owner = geometry
        if not self._has_attr(owner, ATTR_MMD_SOURCE_VERTEX_INDICES):
            parents = tuple(
                self._call_adapter(
                    "list_relatives", geometry, parent=True, fullPath=True
                )
                or ()
            )
            if len(parents) > 1:
                raise self._error(f"geometry {geometry!r} has ambiguous parents")
            if parents:
                owner = self._material_identity(str(parents[0]))

        vertex_count = self._call_adapter("poly_evaluate", geometry, vertex=True)
        if (
            isinstance(vertex_count, bool)
            or not isinstance(vertex_count, int)
            or vertex_count < 0
        ):
            raise self._error(f"geometry {geometry!r} returned an invalid vertex count")
        if not self._has_attr(owner, ATTR_MMD_SOURCE_VERTEX_INDICES):
            return list(range(vertex_count))

        raw = self._call_adapter("get_attr", f"{owner}.{ATTR_MMD_SOURCE_VERTEX_INDICES}")
        if isinstance(raw, tuple) and len(raw) == 1 and isinstance(raw[0], (list, tuple)):
            raw = raw[0]
        if isinstance(raw, (str, bytes, bytearray)) or not isinstance(raw, (list, tuple)):
            raise self._error(
                f"geometry {geometry!r} has invalid source vertex mapping"
            )
        if len(raw) != vertex_count:
            raise self._error(
                f"geometry {geometry!r} has invalid source vertex mapping"
            )
        source_indices: list[int] = []
        seen: set[int] = set()
        for source_index in raw:
            if (
                isinstance(source_index, bool)
                or not isinstance(source_index, int)
                or source_index < 0
            ):
                raise self._error(
                    f"geometry {geometry!r} has invalid source vertex index"
                )
            if source_index in seen:
                raise self._error(
                    f"geometry {geometry!r} maps source vertex {source_index} more than once"
                )
            seen.add(source_index)
            source_indices.append(source_index)
        return source_indices

    def _required_morph_offsets(
        self,
        node: str,
        attr: str,
        morph_type: str,
    ) -> list[dict[str, Any]]:
        raw = self._required_string(node, attr)
        try:
            value = json.loads(raw, object_pairs_hook=self._unique_json_object)
        except (TypeError, ValueError) as exc:
            raise self._error(f"{node}.{attr} must contain strict JSON: {exc}") from exc
        if not isinstance(value, list):
            raise self._error(f"{node}.{attr} must contain a JSON list")
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
        self,
        node: str,
        attr: str,
        morph_type: str,
        offset: Any,
        index: int,
    ) -> dict[str, Any]:
        path = f"{node}.{attr}[{index}]"
        if not isinstance(offset, Mapping):
            raise self._error(f"{path} must be a mapping")
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
            raise self._error(
                f"{path} fields mismatch; unknown={unknown!r}, missing={missing!r}"
            )
        result: dict[str, Any] = {}
        for key, (kind, size) in schema.items():
            field = f"{path}.{key}"
            item = offset[key]
            if kind in {"index", "signed_index", "operation"}:
                minimum = -1 if kind == "signed_index" else 0
                maximum = 1 if kind == "operation" else None
                result[key] = self._strict_json_int(
                    item, field, minimum=minimum, maximum=maximum
                )
            elif kind == "number":
                result[key] = self._strict_json_number(item, field)
            else:
                result[key] = self._strict_json_vector(item, field, size or 0)
        return result

    @staticmethod
    def _strict_json_int(
        value: Any,
        field: str,
        *,
        minimum: int,
        maximum: int | None = None,
    ) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{field} must be an integer")
        if value < minimum or (maximum is not None and value > maximum):
            bounds = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
            raise ValueError(f"{field} must be {bounds}")
        return value

    @staticmethod
    def _strict_json_number(value: Any, field: str) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(f"{field} must be a finite number")
        return float(value)

    @classmethod
    def _strict_json_vector(cls, value: Any, field: str, size: int) -> list[float]:
        if (
            isinstance(value, (str, bytes, bytearray))
            or not isinstance(value, Sequence)
            or len(value) != size
        ):
            raise ValueError(f"{field} must contain exactly {size} numbers")
        return [
            cls._strict_json_number(item, f"{field}[{index}]")
            for index, item in enumerate(value)
        ]

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

    def _has_attr(self, node: str, attr: str) -> bool:
        return self._read.has_attr(node, attr)


__all__ = ["MayaMorphMetadataRepository"]
