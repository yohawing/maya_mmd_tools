"""Collect one model-owned Maya scan into immutable morph projections."""

from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from mmd_tools.adapters.maya_morph_binding_query import (
    MayaMorphBindingQueryError,
    resolve_maya_morph_binding,
)
from mmd_tools.core.constants import ATTR_MMD_MODEL_ROOT
from mmd_tools.core.morph_binding_resolver import (
    MorphBinding,
    MorphBindingRequest,
    MorphBindingResolution,
    MorphBindingResolutionError,
)
from mmd_tools.core.morph_read_projection import (
    MorphBindingProjection,
    MorphBlendShapeReadProjection,
    MorphProjectionRequest,
    project_runtime_capabilities,
)
from mmd_tools.core.model_registry import (
    REGISTRY_CATEGORY_MORPH,
    list_model_registry_members_from_adapter,
)


class MayaMorphReadProjectionError(RuntimeError):
    """Raised when a model-owned morph projection cannot be proven safely."""


class MayaMorphReadProjectionAdapter:
    """Read blendShape ownership and bindings once for one selected model."""

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter

    def read_blend_shape_projection(
        self,
        model_root: str,
        requests: Iterable[MorphProjectionRequest],
        controller_topology: Optional[Mapping[object, Iterable[Tuple[int, float]]]] = None,
    ) -> MorphBlendShapeReadProjection:
        """Return canonical bindings/capabilities after one model graph scan."""

        root = self._canonical_identity(model_root, "model root")
        normalized_requests = self._normalize_requests(requests)
        topology = self._normalize_topology(
            {} if controller_topology is None else controller_topology
        )
        controller = self._controller_identity(root)
        meshes, non_intermediate_meshes, blend_shapes = self._owned_blend_shapes(root)
        registry_bindings = self._registry_morph_bindings(root)
        cached = CachedMorphBindingQueryAdapter(self._adapter)

        observations = []
        connected_output_indices = []
        semantic_bindings = set()
        for item in normalized_requests:
            semantic_binding = self._canonical_identity(
                item.binding_identity,
                "morph semantic binding",
            )
            if semantic_binding in semantic_bindings:
                raise MayaMorphReadProjectionError(
                    "duplicate canonical morph binding identity {!r}".format(
                        semantic_binding
                    )
                )
            semantic_bindings.add(semantic_binding)
            self._require_owned_semantic_binding(
                root,
                semantic_binding,
                item.global_morph_index,
                item.raw_pmx_name,
                item.morph_type,
                registry_bindings,
            )
            output_plug = "{}.outputWeight[{}]".format(
                controller,
                item.global_morph_index,
            )
            destinations = cached.list_connections(
                output_plug,
                source=False,
                destination=True,
                plugs=True,
            ) or ()
            destinations = self._require_sequence(destinations, "controller output destinations")
            if destinations:
                connected_output_indices.append(item.global_morph_index)

            resolution = None
            if item.morph_type == "vertex":
                request = MorphBindingRequest(
                    raw_pmx_name=item.raw_pmx_name,
                    global_morph_index=item.global_morph_index,
                    controller_identity=controller,
                    controller_slot=item.global_morph_index,
                )
                try:
                    resolution = resolve_maya_morph_binding(cached, request)
                except (MayaMorphBindingQueryError, MorphBindingResolutionError) as exc:
                    raise MayaMorphReadProjectionError(
                        "morph index {} binding projection failed: {}".format(
                            item.global_morph_index,
                            exc,
                        )
                    ) from exc

                foreign = tuple(
                    binding.blend_shape_identity
                    for binding in resolution.bindings
                    if binding.blend_shape_identity not in blend_shapes
                )
                if foreign:
                    raise MayaMorphReadProjectionError(
                        "morph index {} resolves outside the model-owned mesh history: {!r}".format(
                            item.global_morph_index,
                            foreign,
                        )
                    )
            observations.append((item, semantic_binding, resolution))

        capabilities = project_runtime_capabilities(
            normalized_requests,
            topology,
            tuple(connected_output_indices),
        )
        projected: List[MorphBindingProjection] = []
        for (item, semantic_binding, resolution), supported in zip(observations, capabilities):
            bindings = resolution.bindings if resolution is not None else ()
            warnings = resolution.warnings if resolution is not None else ()
            projected.append(
                MorphBindingProjection(
                    raw_pmx_name=item.raw_pmx_name,
                    global_morph_index=item.global_morph_index,
                    binding_identity=semantic_binding,
                    bindings=bindings,
                    warnings=warnings,
                    runtime_preview_plugs=(
                        "{}.inputWeight[{}]".format(controller, item.global_morph_index),
                    ),
                    runtime_supported=supported,
                    unsupported_reason="" if supported else "runtime_output_unsupported",
                )
            )

        return MorphBlendShapeReadProjection(
            root_identity=root,
            controller_identity=controller,
            owned_mesh_identities=meshes,
            owned_blend_shape_identities=blend_shapes,
            morphs=tuple(projected),
            owned_non_intermediate_mesh_identities=non_intermediate_meshes,
        )

    def read_runtime_only_projection(
        self,
        model_root: str,
    ) -> MorphBlendShapeReadProjection:
        """Project bare model-owned blendShape aliases for read-only preview."""

        root = self._canonical_identity(model_root, "model root")
        meshes, non_intermediate_meshes, blend_shapes = self._owned_blend_shapes(root)
        cached = CachedMorphBindingQueryAdapter(self._adapter)
        bindings_by_name: Dict[str, List[MorphBinding]] = {}
        alias_by_plug: Dict[str, str] = {}
        for blend_shape in blend_shapes:
            aliases = cached.alias_attr(blend_shape, query=True) or ()
            aliases = self._require_sequence(aliases, "blendShape aliases")
            if len(aliases) % 2:
                raise MayaMorphReadProjectionError(
                    "blendShape aliases must contain alias/plug pairs"
                )
            seen_aliases = set()
            for alias_value, plug_value in zip(aliases[0::2], aliases[1::2]):
                if not isinstance(alias_value, str) or not alias_value:
                    raise MayaMorphReadProjectionError("blendShape alias is empty")
                if alias_value in seen_aliases:
                    raise MayaMorphReadProjectionError(
                        "duplicate blendShape alias {!r} on {!r}".format(
                            alias_value,
                            blend_shape,
                        )
                    )
                seen_aliases.add(alias_value)
                target_index = self._runtime_weight_index(plug_value, blend_shape)
                weight_plug = "{}.weight[{}]".format(blend_shape, target_index)
                previous_alias = alias_by_plug.get(weight_plug)
                if previous_alias is not None and previous_alias != alias_value:
                    raise MayaMorphReadProjectionError(
                        "runtime preview plug {!r} has ambiguous aliases".format(weight_plug)
                    )
                alias_by_plug[weight_plug] = alias_value
                bindings_by_name.setdefault(alias_value, []).append(
                    MorphBinding(
                        raw_pmx_name=alias_value,
                        global_morph_index=-1,
                        blend_shape_identity=blend_shape,
                        alias=alias_value,
                        logical_target_index=target_index,
                        weight_plug=weight_plug,
                        controller_identity="",
                        controller_slot=-1,
                    )
                )

        projected = []
        for runtime_index, (name, bindings) in enumerate(
            sorted(
                bindings_by_name.items(),
                key=lambda item: tuple(binding.weight_plug for binding in item[1]),
            )
        ):
            normalized_bindings = tuple(
                MorphBinding(
                    raw_pmx_name=binding.raw_pmx_name,
                    global_morph_index=runtime_index,
                    blend_shape_identity=binding.blend_shape_identity,
                    alias=binding.alias,
                    logical_target_index=binding.logical_target_index,
                    weight_plug=binding.weight_plug,
                    controller_identity="",
                    controller_slot=runtime_index,
                )
                for binding in bindings
            )
            projected.append(
                MorphBindingProjection(
                    raw_pmx_name=name,
                    global_morph_index=runtime_index,
                    binding_identity=normalized_bindings[0].weight_plug,
                    bindings=normalized_bindings,
                    warnings=(),
                    runtime_preview_plugs=tuple(
                        binding.weight_plug for binding in normalized_bindings
                    ),
                    runtime_supported=True,
                    semantic_registered=False,
                )
            )
        if not projected:
            legacy = self._read_legacy_controller_projection(root)
            if legacy is not None:
                return legacy
        return MorphBlendShapeReadProjection(
            root_identity=root,
            controller_identity="",
            owned_mesh_identities=meshes,
            owned_blend_shape_identities=blend_shapes,
            morphs=tuple(projected),
            owned_non_intermediate_mesh_identities=non_intermediate_meshes,
        )

    def _read_legacy_controller_projection(
        self, root: str
    ) -> Optional[MorphBlendShapeReadProjection]:
        """Keep controller-backed legacy metadata usable without graph lookup.

        Older imported scenes can retain only ``mmdMorphData`` plus the root's
        controller link.  Material morphs have no blendShape destination, so
        an otherwise valid controller input must remain the preview target
        when the optional network-node lookup has no result.
        """

        if not self._call("attribute_exists", "mmdMorphData", root):
            return None
        if not self._call("attribute_exists", "mmd_morph_controller", root):
            return None
        controllers = self._call(
            "list_connections",
            f"{root}.mmd_morph_controller",
            source=True,
            destination=False,
        ) or ()
        if len(controllers) != 1:
            return None
        controller = self._canonical_identity(controllers[0], "morph controller")
        if not (
            self._call("attribute_exists", "inputWeight", controller)
            and self._call("attribute_exists", "outputWeight", controller)
        ):
            return None
        if not self._legacy_controller_is_owned(root, controller):
            return None
        raw = self._call("get_attr", f"{root}.mmdMorphData")
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if isinstance(parsed, dict):
            entries = []
            for name, value in parsed.items():
                if not isinstance(value, dict):
                    return None
                entry = dict(value)
                entry.setdefault("name_jp", name)
                entries.append(entry)
        elif isinstance(parsed, list):
            entries = parsed
        else:
            return None

        projections = []
        seen_indices = set()
        for entry in entries:
            if not isinstance(entry, dict):
                return None
            index = entry.get("index")
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                return None
            name = entry.get("name_jp") or entry.get("name") or entry.get("name_en")
            if not isinstance(name, str) or not name or index in seen_indices:
                return None
            seen_indices.add(index)
            try:
                raw_type = int(entry.get("type", 1))
            except (TypeError, ValueError):
                return None
            target = f"{controller}.inputWeight[{index}]"
            runtime_supported = raw_type in {1, 2, 8}
            if runtime_supported:
                destinations = self._call(
                    "list_connections",
                    f"{controller}.outputWeight[{index}]",
                    source=False,
                    destination=True,
                    plugs=True,
                ) or ()
                destinations = self._require_sequence(
                    destinations,
                    "legacy controller output destinations",
                )
                runtime_supported = bool(
                    destinations
                    and all(isinstance(destination, str) and destination for destination in destinations)
                )
            projections.append(
                MorphBindingProjection(
                    raw_pmx_name=name,
                    global_morph_index=index,
                    binding_identity=target,
                    bindings=(),
                    warnings=(),
                    runtime_preview_plugs=(target,),
                    runtime_supported=runtime_supported,
                    unsupported_reason=(
                        "" if runtime_supported else "runtime_output_unsupported"
                    ),
                    semantic_registered=False,
                )
            )
        if not projections:
            return None
        return MorphBlendShapeReadProjection(
            root_identity=root,
            controller_identity=controller,
            owned_mesh_identities=(),
            owned_blend_shape_identities=(),
            morphs=tuple(sorted(projections, key=lambda item: item.global_morph_index)),
            owned_non_intermediate_mesh_identities=(),
        )

    def _legacy_controller_is_owned(self, root: str, controller: str) -> bool:
        """Require the legacy controller to belong only to this model root."""

        destinations = self._call(
            "list_connections",
            f"{controller}.message",
            source=False,
            destination=True,
            plugs=True,
        ) or ()
        destinations = self._require_sequence(
            destinations,
            "legacy controller message destinations",
        )
        if len(destinations) != 1:
            return False
        destination = destinations[0]
        if not isinstance(destination, str) or "." not in destination:
            return False
        destination_node, destination_attr = destination.rsplit(".", 1)
        if destination_attr != "mmd_morph_controller":
            return False
        try:
            return self._canonical_identity(
                destination_node,
                "legacy controller destination",
            ) == root
        except MayaMorphReadProjectionError:
            return False

    @staticmethod
    def _runtime_weight_index(value: object, blend_shape: str) -> int:
        if not isinstance(value, str):
            raise MayaMorphReadProjectionError("blendShape alias plug is invalid")
        match = re.fullmatch(r"(?:(?P<node>.+)\.)?(?:weight|w)\[(?P<index>\d+)\]", value)
        if match is None or (
            match.group("node") is not None and match.group("node") != blend_shape
        ):
            raise MayaMorphReadProjectionError(
                "blendShape alias plug {!r} is not owned by {!r}".format(
                    value,
                    blend_shape,
                )
            )
        return int(match.group("index"))

    def read_validated_spec_projection(
        self,
        model_root: str,
        requests: Iterable[MorphProjectionRequest],
        controller_identity: str,
        resolutions: Mapping[int, MorphBindingResolution],
        controller_topology: Optional[Mapping[object, Iterable[Tuple[int, float]]]] = None,
        query_adapter: Optional[Any] = None,
    ) -> MorphBlendShapeReadProjection:
        """Project one backend-validated Spec without rereading semantic bindings.

        The caller must produce ``requests`` and ``resolutions`` during the
        same strict backend read for ``model_root``.  Registry ownership and
        semantic node type/name/index are therefore trusted here; Maya mesh
        history ownership and controller output capability remain adapter
        observations.
        """

        root = self._required_identity(model_root, "model root")
        normalized_requests = self._normalize_requests(requests)
        if controller_identity:
            controller = self._required_identity(controller_identity, "morph controller")
        else:
            controller = ""
        topology = self._normalize_topology(
            {} if controller_topology is None else controller_topology
        )
        cached = query_adapter or CachedMorphBindingQueryAdapter(self._adapter)
        meshes, non_intermediate_meshes, blend_shapes = self._owned_blend_shapes(root)

        vertex_indices = {
            request.global_morph_index
            for request in normalized_requests
            if request.morph_type == "vertex"
        }
        if any(
            isinstance(index, bool) or not isinstance(index, int) or index < 0
            for index in resolutions
        ) or set(resolutions) != vertex_indices:
            raise MayaMorphReadProjectionError(
                "captured vertex binding resolutions do not match the validated Spec"
            )
        requests_by_index = {
            request.global_morph_index: request for request in normalized_requests
        }
        for index, resolution in resolutions.items():
            if not isinstance(resolution, MorphBindingResolution):
                raise MayaMorphReadProjectionError(
                    "captured morph index {} has an invalid binding resolution".format(index)
                )
            request = requests_by_index[index]
            if not resolution.bindings or any(
                binding.global_morph_index != index
                or binding.raw_pmx_name != request.raw_pmx_name
                or binding.controller_identity != controller
                or binding.controller_slot != index
                for binding in resolution.bindings
            ):
                raise MayaMorphReadProjectionError(
                    "captured morph index {} binding identity is inconsistent".format(index)
                )
            foreign = tuple(
                binding.blend_shape_identity
                for binding in resolution.bindings
                if binding.blend_shape_identity not in blend_shapes
            )
            if foreign:
                raise MayaMorphReadProjectionError(
                    "morph index {} resolves outside the model-owned mesh history: {!r}".format(
                        index,
                        foreign,
                    )
                )

        if vertex_indices and not controller:
            raise MayaMorphReadProjectionError(
                "vertex morph projection requires a canonical controller"
            )
        if topology and not controller:
            raise MayaMorphReadProjectionError(
                "controller topology requires a canonical controller"
            )
        connected_output_indices = set(vertex_indices)
        for target in topology:
            output_plug = "{}.outputWeight[{}]".format(controller, target)
            destinations = cached.list_connections(
                output_plug,
                source=False,
                destination=True,
                plugs=True,
            ) or ()
            if self._require_sequence(destinations, "controller output destinations"):
                connected_output_indices.add(target)

        capabilities = project_runtime_capabilities(
            normalized_requests,
            topology,
            tuple(sorted(connected_output_indices)),
        )
        projected = []
        for request, supported in zip(normalized_requests, capabilities):
            resolution = resolutions.get(request.global_morph_index)
            if controller:
                runtime_preview_plugs = (
                    "{}.inputWeight[{}]".format(
                        controller,
                        request.global_morph_index,
                    ),
                )
            elif request.morph_type in {"bone", "material"} and cached.attribute_exists(
                "weight", request.binding_identity
            ):
                runtime_preview_plugs = (
                    "{}.weight".format(request.binding_identity),
                )
            else:
                runtime_preview_plugs = ()
            supported = bool(supported and runtime_preview_plugs)
            projected.append(
                MorphBindingProjection(
                    raw_pmx_name=request.raw_pmx_name,
                    global_morph_index=request.global_morph_index,
                    binding_identity=request.binding_identity,
                    bindings=resolution.bindings if resolution is not None else (),
                    warnings=resolution.warnings if resolution is not None else (),
                    runtime_preview_plugs=runtime_preview_plugs,
                    runtime_supported=supported,
                    unsupported_reason="" if supported else "runtime_output_unsupported",
                )
            )
        return MorphBlendShapeReadProjection(
            root_identity=root,
            controller_identity=controller,
            owned_mesh_identities=meshes,
            owned_blend_shape_identities=blend_shapes,
            morphs=tuple(projected),
            owned_non_intermediate_mesh_identities=non_intermediate_meshes,
        )

    @staticmethod
    def _normalize_topology(
        topology: Mapping[object, Iterable[Tuple[int, float]]],
    ) -> Dict[int, Tuple[Tuple[int, float], ...]]:
        if not isinstance(topology, Mapping):
            raise MayaMorphReadProjectionError("controller topology must be a mapping")
        normalized = {}
        for target_value, sources in topology.items():
            if isinstance(target_value, bool):
                raise MayaMorphReadProjectionError("controller topology target is invalid")
            if isinstance(target_value, int):
                target = target_value
            elif (
                isinstance(target_value, str)
                and target_value.isdecimal()
                and str(int(target_value)) == target_value
            ):
                target = int(target_value)
            else:
                raise MayaMorphReadProjectionError("controller topology target is invalid")
            if target < 0:
                raise MayaMorphReadProjectionError("controller topology target is invalid")
            if target in normalized:
                raise MayaMorphReadProjectionError(
                    "controller topology target is duplicated after normalization"
                )
            try:
                pairs = tuple(sources)
            except TypeError as exc:
                raise MayaMorphReadProjectionError("controller topology sources are invalid") from exc
            normalized_sources = []
            for pair in pairs:
                if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                    raise MayaMorphReadProjectionError("controller topology source is invalid")
                source, rate = pair
                if isinstance(source, bool) or not isinstance(source, int) or source < 0:
                    raise MayaMorphReadProjectionError("controller topology source index is invalid")
                if isinstance(rate, bool) or not isinstance(rate, (int, float)):
                    raise MayaMorphReadProjectionError("controller topology rate is invalid")
                normalized_rate = float(rate)
                if not math.isfinite(normalized_rate):
                    raise MayaMorphReadProjectionError("controller topology rate is invalid")
                normalized_sources.append((source, normalized_rate))
            normalized[target] = tuple(normalized_sources)
        return normalized

    def _registry_morph_bindings(self, root: str) -> Any:
        members = list_model_registry_members_from_adapter(
            self._adapter,
            root,
            REGISTRY_CATEGORY_MORPH,
        )
        if members is None:
            return None
        return frozenset(
            self._canonical_identity(member, "registry morph member")
            for member in members
        )

    def _require_owned_semantic_binding(
        self,
        root: str,
        binding: str,
        expected_index: int,
        expected_name: str,
        expected_type: str,
        registry_bindings: Any,
    ) -> None:
        if self._call("node_type", binding) != "network":
            raise MayaMorphReadProjectionError(
                "morph semantic binding {!r} must be a network node".format(binding)
            )
        for attr in ("mmd_morph_type", "mmd_morph_name"):
            if not self._call("attribute_exists", attr, binding):
                raise MayaMorphReadProjectionError(
                    "morph semantic binding {!r} has no {}".format(binding, attr)
                )
        morph_type = self._call("get_attr", "{}.mmd_morph_type".format(binding))
        if morph_type != expected_type:
            raise MayaMorphReadProjectionError(
                "morph semantic binding {!r} type does not match {!r}".format(
                    binding,
                    expected_type,
                )
            )
        raw_name = self._call("get_attr", "{}.mmd_morph_name".format(binding))
        if raw_name != expected_name:
            raise MayaMorphReadProjectionError(
                "morph semantic binding {!r} raw name does not match {!r}".format(
                    binding,
                    expected_name,
                )
            )
        if not self._call("attribute_exists", "mmd_morph_index", binding):
            raise MayaMorphReadProjectionError(
                "morph semantic binding {!r} has no mmd_morph_index".format(binding)
            )
        observed_index = self._call("get_attr", "{}.mmd_morph_index".format(binding))
        if (
            isinstance(observed_index, bool)
            or not isinstance(observed_index, int)
            or observed_index != expected_index
        ):
            raise MayaMorphReadProjectionError(
                "morph semantic binding {!r} index does not match {}".format(
                    binding,
                    expected_index,
                )
            )
        if registry_bindings is not None:
            if binding not in registry_bindings:
                raise MayaMorphReadProjectionError(
                    "morph semantic binding {!r} is not owned by the model registry".format(
                        binding
                    )
                )
            return
        if not self._call("attribute_exists", ATTR_MMD_MODEL_ROOT, binding):
            raise MayaMorphReadProjectionError(
                "legacy morph semantic binding {!r} has no model root link".format(binding)
            )
        roots = self._call(
            "list_connections",
            "{}.{}".format(binding, ATTR_MMD_MODEL_ROOT),
            source=True,
            destination=False,
        ) or ()
        roots = self._require_sequence(roots, "legacy morph root connections")
        if len(roots) != 1 or self._canonical_identity(roots[0], "legacy morph root") != root:
            raise MayaMorphReadProjectionError(
                "legacy morph semantic binding {!r} is not owned by {!r}".format(
                    binding,
                    root,
                )
            )

    def _controller_identity(self, root: str) -> str:
        if not self._call("attribute_exists", "mmd_morph_controller", root):
            raise MayaMorphReadProjectionError(
                "{} has no mmd_morph_controller attribute".format(root)
            )
        controllers = self._call(
            "list_connections",
            "{}.mmd_morph_controller".format(root),
            source=True,
            destination=False,
        ) or ()
        controllers = self._require_sequence(controllers, "morph controller connections")
        if len(controllers) != 1:
            raise MayaMorphReadProjectionError(
                "{} must have exactly one morph controller".format(root)
            )
        return self._canonical_identity(controllers[0], "morph controller")

    def _owned_blend_shapes(
        self,
        root: str,
    ) -> Tuple[Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]]:
        shapes = self._call(
            "list_relatives",
            root,
            allDescendents=True,
            type="mesh",
        ) or ()
        shapes = self._require_sequence(shapes, "owned mesh descendants")
        canonical_meshes: List[str] = []
        non_intermediate_meshes: List[str] = []
        blend_shapes: List[str] = []
        seen_meshes = set()
        seen_blend_shapes = set()
        for shape_value in shapes:
            shape = self._canonical_identity(shape_value, "owned mesh")
            if shape in seen_meshes:
                continue
            seen_meshes.add(shape)
            canonical_meshes.append(shape)
            if bool(self._call("get_attr", "{}.intermediateObject".format(shape))):
                continue
            non_intermediate_meshes.append(shape)
            history = self._call("list_history", shape) or ()
            history = self._require_sequence(history, "mesh history")
            candidates = self._call("ls", history, type="blendShape") or ()
            candidates = self._require_sequence(candidates, "blendShape history")
            for candidate in candidates:
                blend_shape = self._canonical_identity(candidate, "blendShape")
                if blend_shape not in seen_blend_shapes:
                    seen_blend_shapes.add(blend_shape)
                    blend_shapes.append(blend_shape)
        return (
            tuple(canonical_meshes),
            tuple(non_intermediate_meshes),
            tuple(sorted(blend_shapes)),
        )

    def _canonical_identity(self, value: object, label: str) -> str:
        if not isinstance(value, str) or not value:
            raise MayaMorphReadProjectionError("{} identity is empty".format(label))
        names = self._call("ls", value, long=True) or ()
        names = self._require_sequence(names, "{} canonical lookup".format(label))
        if len(names) != 1 or not isinstance(names[0], str) or not names[0]:
            raise MayaMorphReadProjectionError(
                "{} {!r} has no unique canonical identity".format(label, value)
            )
        return names[0]

    @staticmethod
    def _required_identity(value: object, label: str) -> str:
        if not isinstance(value, str) or not value:
            raise MayaMorphReadProjectionError("{} identity is empty".format(label))
        return value

    @staticmethod
    def _normalize_requests(
        requests: Iterable[MorphProjectionRequest],
    ) -> Tuple[MorphProjectionRequest, ...]:
        if isinstance(requests, (str, bytes, bytearray)):
            raise MayaMorphReadProjectionError("morph projection requests must be iterable")
        try:
            values = tuple(requests)
        except TypeError as exc:
            raise MayaMorphReadProjectionError(
                "morph projection requests must be iterable"
            ) from exc
        seen_indices = set()
        seen_bindings = set()
        for value in values:
            if not isinstance(value, MorphProjectionRequest):
                raise MayaMorphReadProjectionError(
                    "each morph projection request must be a MorphProjectionRequest"
                )
            if not isinstance(value.raw_pmx_name, str) or not value.raw_pmx_name:
                raise MayaMorphReadProjectionError("raw PMX morph name must be non-empty")
            if (
                isinstance(value.global_morph_index, bool)
                or not isinstance(value.global_morph_index, int)
                or value.global_morph_index < 0
            ):
                raise MayaMorphReadProjectionError("global morph index must be non-negative")
            if not isinstance(value.binding_identity, str) or not value.binding_identity:
                raise MayaMorphReadProjectionError("morph binding identity must be non-empty")
            if not isinstance(value.morph_type, str) or not value.morph_type:
                raise MayaMorphReadProjectionError("morph type must be non-empty")
            if value.global_morph_index in seen_indices:
                raise MayaMorphReadProjectionError(
                    "duplicate global morph index {}".format(value.global_morph_index)
                )
            if value.binding_identity in seen_bindings:
                raise MayaMorphReadProjectionError(
                    "duplicate morph binding identity {!r}".format(value.binding_identity)
                )
            seen_indices.add(value.global_morph_index)
            seen_bindings.add(value.binding_identity)
        return tuple(sorted(values, key=lambda item: item.global_morph_index))

    @staticmethod
    def _require_sequence(value: object, label: str) -> Tuple[object, ...]:
        if isinstance(value, (str, bytes, bytearray)):
            raise MayaMorphReadProjectionError("{} returned a scalar".format(label))
        try:
            return tuple(value)  # type: ignore[arg-type]
        except TypeError as exc:
            raise MayaMorphReadProjectionError("{} is not iterable".format(label)) from exc

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return getattr(self._adapter, method)(*args, **kwargs)
        except MayaMorphReadProjectionError:
            raise
        except AttributeError as exc:
            raise MayaMorphReadProjectionError(
                "injected adapter is missing {}()".format(method)
            ) from exc
        except Exception as exc:
            raise MayaMorphReadProjectionError(
                "adapter {}() failed: {}".format(method, exc)
            ) from exc


class CachedMorphBindingQueryAdapter:
    """Memoize graph observations while preserving the existing query policy."""

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter
        self._cache: Dict[Tuple[object, ...], Any] = {}

    def list_connections(self, plug: str, **kwargs: Any) -> Any:
        return self._memoized("list_connections", (plug,), kwargs)

    def ls(self, node: str, **kwargs: Any) -> Any:
        return self._memoized("ls", (node,), kwargs)

    def node_type(self, node: str) -> Any:
        return self._memoized("node_type", (node,), {})

    def alias_attr(self, node: str, **kwargs: Any) -> Any:
        return self._memoized("alias_attr", (node,), kwargs)

    def attribute_exists(self, attr: str, node: str) -> Any:
        return self._memoized("attribute_exists", (attr, node), {})

    def get_attr(self, plug: str, **kwargs: Any) -> Any:
        return self._memoized("get_attr", (plug,), kwargs)

    def _memoized(
        self,
        method: str,
        args: Tuple[object, ...],
        kwargs: Mapping[str, Any],
    ) -> Any:
        key = (method, args, tuple(sorted(kwargs.items())))
        if key not in self._cache:
            self._cache[key] = getattr(self._adapter, method)(*args, **kwargs)
        return self._cache[key]


__all__ = [
    "CachedMorphBindingQueryAdapter",
    "MayaMorphReadProjectionAdapter",
    "MayaMorphReadProjectionError",
]
