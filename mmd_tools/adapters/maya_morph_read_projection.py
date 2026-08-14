"""Collect one model-owned Maya scan into immutable morph projections."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Tuple

from mmd_tools.adapters.maya_morph_binding_query import (
    MayaMorphBindingQueryError,
    resolve_maya_morph_binding,
)
from mmd_tools.core.constants import ATTR_MMD_MODEL_ROOT
from mmd_tools.core.morph_binding_resolver import (
    MorphBindingRequest,
    MorphBindingResolutionError,
)
from mmd_tools.core.morph_read_projection import (
    MorphBindingProjection,
    MorphBlendShapeReadProjection,
    MorphProjectionRequest,
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
    ) -> MorphBlendShapeReadProjection:
        """Return canonical bindings after one model mesh/history observation pass."""

        root = self._canonical_identity(model_root, "model root")
        normalized_requests = self._normalize_requests(requests)
        controller = self._controller_identity(root)
        meshes, blend_shapes = self._owned_blend_shapes(root)
        registry_bindings = self._registry_morph_bindings(root)
        cached = _CachedBindingQueryAdapter(self._adapter)

        projected: List[MorphBindingProjection] = []
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
                registry_bindings,
            )
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
            projected.append(
                MorphBindingProjection(
                    raw_pmx_name=item.raw_pmx_name,
                    global_morph_index=item.global_morph_index,
                    binding_identity=semantic_binding,
                    bindings=resolution.bindings,
                    warnings=resolution.warnings,
                )
            )

        return MorphBlendShapeReadProjection(
            root_identity=root,
            controller_identity=controller,
            owned_mesh_identities=meshes,
            owned_blend_shape_identities=blend_shapes,
            morphs=tuple(projected),
        )

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
        if morph_type != "vertex":
            raise MayaMorphReadProjectionError(
                "morph semantic binding {!r} must have vertex type".format(binding)
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

    def _owned_blend_shapes(self, root: str) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
        shapes = self._call(
            "list_relatives",
            root,
            allDescendents=True,
            type="mesh",
        ) or ()
        shapes = self._require_sequence(shapes, "owned mesh descendants")
        canonical_meshes: List[str] = []
        blend_shapes: List[str] = []
        seen_meshes = set()
        seen_blend_shapes = set()
        for shape_value in shapes:
            shape = self._canonical_identity(shape_value, "owned mesh")
            if shape in seen_meshes:
                continue
            seen_meshes.add(shape)
            canonical_meshes.append(shape)
            history = self._call("list_history", shape) or ()
            history = self._require_sequence(history, "mesh history")
            candidates = self._call("ls", history, type="blendShape") or ()
            candidates = self._require_sequence(candidates, "blendShape history")
            for candidate in candidates:
                blend_shape = self._canonical_identity(candidate, "blendShape")
                if blend_shape not in seen_blend_shapes:
                    seen_blend_shapes.add(blend_shape)
                    blend_shapes.append(blend_shape)
        return tuple(canonical_meshes), tuple(sorted(blend_shapes))

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


class _CachedBindingQueryAdapter:
    """Memoize graph observations while preserving the existing query policy."""

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter
        self._cache: Dict[Tuple[object, ...], Any] = {}

    def list_connections(self, plug: str, **kwargs: Any) -> Any:
        return self._adapter.list_connections(plug, **kwargs)

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


__all__ = ["MayaMorphReadProjectionAdapter", "MayaMorphReadProjectionError"]
