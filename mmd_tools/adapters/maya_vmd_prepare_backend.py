"""Maya-owned preparation boundary for user-path Bake Timeline VMD exports.

The public prepare action is intentionally Maya independent.  This adapter
supplies the small host-side seam it needs: resolve the Current Model, build a
conservative dependency closure, arm a :class:`SceneRevisionService` watch,
and stream one Bake Timeline payload.  Maya modules are imported lazily so importing
the package remains safe in unit-test and tooling processes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import time
from typing import Any, Optional

from ..actions.prepare_vmd_export_action import (
    PrepareVmdExportError,
    VmdExportDiscovery,
)
from ..core.constants import ATTR_MMD_CONTROL_RIG_JSON, ATTR_MMD_MODEL_NAME
from ..validation.snapshot import fingerprint_payload


_CAMERA_MARKER = "mmd_camera"
_LIGHT_MARKER = "mmd_light"
_BAKE_TIMELINE_EXPORT_STRATEGY = "bake_timeline"


@dataclass(frozen=True)
class MayaVmdExportRoute:
    """The immutable host route used by one preparation operation."""

    target_model: str
    collector_options: Mapping[str, Any]
    dependency_nodes: tuple[str, ...]
    dependency_uuids: tuple[str, ...]
    dependency_fingerprint: str
    model_name: str = ""


@dataclass(frozen=True)
class MayaVmdTemporaryControlRigBake:
    """Host lifecycle receipt for one non-destructive VMD preparation bake."""

    target_model: str
    original_state: str
    original_owner: str


def _field(value: Any, name: str, *aliases: str) -> Any:
    """Read a request field, including fields nested in ``options``."""

    names = (name,) + aliases
    if isinstance(value, Mapping):
        for candidate in names:
            if candidate in value:
                return value[candidate]
        normalized = {str(key).strip().lower().replace("-", "_"): item for key, item in value.items()}
        for candidate in names:
            item = normalized.get(str(candidate).strip().lower().replace("-", "_"))
            if item is not None:
                return item
        options = value.get("options")
    else:
        for candidate in names:
            if hasattr(value, candidate):
                return getattr(value, candidate)
        options = getattr(value, "options", None)
    if isinstance(options, Mapping):
        return _field(options, name, *aliases)
    return None


def _request_options(request: Any) -> dict[str, Any]:
    """Copy workflow options without allowing the adapter to mutate them."""

    options = getattr(request, "options", None)
    if isinstance(options, Mapping):
        result = dict(options)
    elif isinstance(request, Mapping):
        nested = request.get("options")
        result = dict(nested) if isinstance(nested, Mapping) else {}
        result.update({key: value for key, value in request.items() if key != "options"})
    else:
        result = {}
    for name in (
        "current_model_root",
        "target_model",
        "export_strategy",
        "scene_session_id",
        "frame_range",
        "frame_start",
        "frame_end",
        "frame_step",
        "motion_scale",
        "scale",
        "apply_scale",
        "model_name",
        "cameras",
        "lights",
        "joints",
        "blend_shapes",
    ):
        if name not in result and hasattr(request, name):
            result[name] = getattr(request, name)
    return result


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [value]
    try:
        return list(value)
    except TypeError:
        return [value]


def _copy_diagnostics(value: Any) -> Any:
    """Detach nested collector diagnostics for report serialization."""

    if isinstance(value, Mapping):
        return {str(key): _copy_diagnostics(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_copy_diagnostics(item) for item in value]
    return value


class MayaVmdPrepareBackend:
    """Discover, watch, and collect one Current Model-scoped Bake Timeline route.

    ``revision_service`` is injected in tests and in application wiring.  If
    omitted it is created lazily as a normal ``SceneRevisionService``; that
    service imports Maya only when ``arm`` actually needs host callbacks.
    """

    def __init__(
        self,
        cmds_module: Any = None,
        *,
        collector: Any = None,
        revision_service: Any = None,
        mobject_resolver: Any = None,
        bone_channel_sampler: Any = None,
        diagnostics_sink: Any = None,
    ) -> None:
        self._cmds = cmds_module
        self._collector = collector
        self._revision_service = revision_service
        self._mobject_resolver = mobject_resolver
        self._bone_channel_sampler = bone_channel_sampler
        self._diagnostics_sink = diagnostics_sink
        self._active_watch: Any = None
        self._active_route: Optional[MayaVmdExportRoute] = None
        self._watch_generation = 0
        self._diagnostics: dict[str, Any] = {}

    @property
    def diagnostics(self) -> dict[str, Any]:
        """Return detached host-side discovery/collection evidence."""

        return _copy_diagnostics(self._diagnostics)

    @property
    def diagnostics_copy(self) -> dict[str, Any]:
        """Alias used by preparation reports."""

        return self.diagnostics

    def _emit_diagnostics(self) -> None:
        """Publish a detached bounded snapshot without affecting export."""

        sink = self._diagnostics_sink
        if not callable(sink):
            return
        try:
            sink(self.diagnostics)
        except Exception as exc:  # diagnostics must never alter export semantics
            self._diagnostics["sink_error"] = f"{type(exc).__name__}: {exc}"

    def _control_rig_metadata_for_request(
        self,
        request: Any,
    ) -> tuple[str, Optional[Mapping[str, Any]]]:
        """Resolve optional Control Rig metadata without mutating Maya."""

        options = self._validated_options(request)
        target_model = self._resolve_target_model(options)
        cmds = self._cmds_api()
        attribute_query = getattr(cmds, "attributeQuery", None)
        if not callable(attribute_query):
            return target_model, None
        try:
            has_metadata = bool(
                attribute_query(
                    ATTR_MMD_CONTROL_RIG_JSON,
                    node=target_model,
                    exists=True,
                )
            )
        except Exception as exc:
            raise PrepareVmdExportError(
                f"could not inspect Control Rig metadata for {target_model!r}"
            ) from exc
        if not has_metadata:
            return target_model, None
        from ..core.mmd_control_rig_builder import read_mmd_control_rig_metadata

        metadata = read_mmd_control_rig_metadata(target_model, cmds_module=cmds)
        return target_model, metadata

    def can_prepare_for_collection(self, request: Any) -> bool:
        """Report whether this request can use the temporary Control Rig bake."""

        _target_model, metadata = self._control_rig_metadata_for_request(request)
        return bool(
            isinstance(metadata, Mapping)
            and str(metadata.get("state") or "") == "EDIT"
            and str(metadata.get("owner") or "") == "CONTROL_OWNED"
        )

    def prepare_for_collection(self, request: Any) -> Any:
        """Temporarily bake an EDIT-owned Control Rig for VMD collection.

        The collector must sample MMD-authored inputs, so an active Control
        Rig is moved through the existing ownership transaction before route
        discovery.  The returned receipt is consumed by
        :meth:`restore_after_collection`; all other rig states are untouched.
        """

        target_model, metadata = self._control_rig_metadata_for_request(request)
        cmds = self._cmds_api()
        from ..core.mmd_control_rig_motion import bake_mmd_control_rig

        if not isinstance(metadata, Mapping):
            return None
        state = str(metadata.get("state") or "")
        owner = str(metadata.get("owner") or "")
        if state != "EDIT" or owner != "CONTROL_OWNED":
            return None
        context = MayaVmdTemporaryControlRigBake(
            target_model=target_model,
            original_state=state,
            original_owner=owner,
        )
        try:
            baked = bake_mmd_control_rig(target_model, cmds_module=cmds)
        except Exception as exc:
            error = PrepareVmdExportError(
                "automatic Control Rig bake failed before VMD preparation"
            )
            try:
                self.restore_after_collection(context)
            except Exception as restore_exc:
                raise PrepareVmdExportError(
                    f"{error}; automatic Control Rig restoration failed: {restore_exc}"
                ) from exc
            raise error from exc
        try:
            if not isinstance(baked, Mapping) or str(baked.get("state") or "") != "BAKED":
                raise PrepareVmdExportError(
                    "automatic Control Rig bake did not produce a BAKED MMD-owned state"
                )
            if str(baked.get("owner") or "") != "MMD_OWNED":
                raise PrepareVmdExportError(
                    "automatic Control Rig bake did not transfer ownership to the MMD rig"
                )
        except Exception as exc:
            try:
                self.restore_after_collection(context)
            except Exception as restore_exc:
                raise PrepareVmdExportError(
                    f"{exc}; automatic Control Rig restoration failed: {restore_exc}"
                ) from exc
            raise
        return context

    def restore_after_collection(self, context: Any) -> None:
        """Restore EDIT/CONTROL_OWNED after a temporary collection bake."""

        if not isinstance(context, MayaVmdTemporaryControlRigBake):
            raise PrepareVmdExportError("temporary Control Rig bake receipt is invalid")
        cmds = self._cmds_api()
        from ..core.mmd_control_rig_builder import read_mmd_control_rig_metadata
        from ..core.mmd_control_rig_motion import enter_mmd_control_rig_edit

        metadata = read_mmd_control_rig_metadata(
            context.target_model,
            cmds_module=cmds,
        )
        if not isinstance(metadata, Mapping):
            raise PrepareVmdExportError("Control Rig metadata disappeared during VMD preparation")
        if (
            str(metadata.get("state") or "") == context.original_state
            and str(metadata.get("owner") or "") == context.original_owner
        ):
            return
        if (
            str(metadata.get("state") or "") != "BAKED"
            or str(metadata.get("owner") or "") != "MMD_OWNED"
        ):
            raise PrepareVmdExportError(
                "temporary Control Rig bake no longer owns the expected BAKED MMD state"
            )
        try:
            restored = enter_mmd_control_rig_edit(
                context.target_model,
                cmds_module=cmds,
            )
        except Exception as exc:
            raise PrepareVmdExportError(
                "automatic Control Rig restoration to EDIT failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(restored, Mapping):
            raise PrepareVmdExportError("Control Rig restoration returned invalid metadata")
        if (
            str(restored.get("state") or "") != context.original_state
            or str(restored.get("owner") or "") != context.original_owner
        ):
            raise PrepareVmdExportError(
                "automatic Control Rig restoration did not return EDIT/CONTROL_OWNED"
            )

    def discover(self, request: Any) -> VmdExportDiscovery:
        """Resolve the Current Model and fingerprint its dependency closure."""

        started = time.perf_counter()
        options = self._validated_options(request)
        target_model = self._resolve_target_model(options)
        model_name = self._resolve_model_name(options, target_model)
        target_uuid = self._stable_uuid(target_model)
        closure_started = time.perf_counter()
        records, topology = self._dependency_closure(target_model, options)
        closure_sec = round(time.perf_counter() - closure_started, 6)
        dependency_payload = {
            "target_uuid": target_uuid,
            "nodes": sorted(records),
            "connections": sorted(topology),
        }
        dependency_fingerprint = fingerprint_payload(dependency_payload)
        session_id = self._session_id(options)
        cache_id = self._cache_id(session_id, target_uuid, dependency_fingerprint)
        route = MayaVmdExportRoute(
            target_model=target_model,
            collector_options=self._collector_options(options, target_model),
            dependency_nodes=tuple(sorted(record[1] for record in records)),
            dependency_uuids=tuple(sorted(record[0] for record in records)),
            dependency_fingerprint=dependency_fingerprint,
            model_name=model_name,
        )
        self._active_route = route
        self._diagnostics["dependency_discovery"] = {
            "wall_sec": round(time.perf_counter() - started, 6),
            "closure_wall_sec": closure_sec,
            "node_count": len(records),
            "connection_count": len(topology),
            "target_uuid": target_uuid,
            "target_identity": target_model,
        }
        self._emit_diagnostics()
        return VmdExportDiscovery(
            scene_session_id=session_id,
            target_uuid=target_uuid,
            target_identity=target_model,
            dependency_closure_fingerprint=dependency_fingerprint,
            cache_id=cache_id,
            route=route,
            model_name=model_name,
        )

    def arm(self, request: Any, discovery: VmdExportDiscovery) -> Any:
        """Arm a dependency watch before the collector is called."""

        del request
        if not isinstance(discovery.route, MayaVmdExportRoute):
            raise PrepareVmdExportError("Maya VMD route is missing from discovery")
        if self._active_watch is not None:
            close = getattr(self._active_watch, "close", None)
            if callable(close):
                close()
            self._active_watch = None
            self._watch_generation += 1
        service = self._service()
        arm = getattr(service, "arm", None)
        if not callable(arm):
            raise PrepareVmdExportError("revision service does not expose arm(dependencies)")
        try:
            # Keep Maya's global time driver in the discovery fingerprint, but
            # do not watch it as mutable authored scene data. Bake Timeline sampling
            # deliberately advances and restores currentTime; watching the
            # time node would invalidate the preparation because of its own
            # controlled Timeline evaluation. Connection/topology changes to
            # the time driver remain covered by validate-time rediscovery.
            watched_nodes = [
                node
                for node in discovery.route.dependency_nodes
                if self._node_type(node) != "time"
            ]
            dependencies = [self._resolve_mobject(node) for node in watched_nodes]
        except Exception as exc:
            raise PrepareVmdExportError("could not resolve Maya dependency MObjects") from exc
        try:
            watch = arm(dependencies)
        except TypeError:
            # A test/application wrapper may accept the discovery object while
            # the production SceneRevisionService accepts MObject iterables.
            watch = arm(dependencies, discovery)
        if not self._watch_usable(watch):
            close = getattr(watch, "close", None)
            if callable(close):
                close()
            raise PrepareVmdExportError("scene revision watch is disabled or closed")
        self._active_watch = watch
        return watch

    def current_revision(self, request: Any, discovery: VmdExportDiscovery) -> str:
        """Flush host edits and return a generation-bound revision token."""

        del request
        watch = self._active_watch
        if watch is None or not self._watch_usable(watch):
            raise PrepareVmdExportError("scene revision watch is disabled, closed, or stale")
        service = self._service()
        current = getattr(service, "current_revision", None)
        if not callable(current):
            raise PrepareVmdExportError("revision service does not expose current_revision")
        try:
            revision = current()
        except TypeError:
            revision = current(None, discovery)
        # current_revision() flushes queued Maya anim-curve callbacks.  Check
        # the watch afterwards, because that flush can mark it stale.
        if not self._watch_usable(watch) or not self._watch_current(watch):
            raise PrepareVmdExportError("scene revision watch is disabled, closed, or stale")
        if discovery.target_uuid != self._stable_uuid(discovery.target_identity):
            raise PrepareVmdExportError("Current Model identity changed during preparation")
        if not isinstance(revision, (str, int)) or str(revision).strip() == "":
            raise PrepareVmdExportError("scene revision is unavailable")
        return f"{revision}:{self._watch_generation}"

    def supports_streaming(self) -> bool:
        """Report whether the injected collector explicitly supports sinks.

        The adapter itself always has a ``collect_to_sink`` method, but an
        injected legacy collector must not be selected merely because that
        method exists on the backend.  A missing collector means production
        construction is available and is therefore stream-capable.
        """

        collector = self._collector
        return collector is None or callable(getattr(collector, "collect_to_sink", None))

    def collect_to_sink(self, request: Any, sink: Any) -> Mapping[str, Any]:
        """Collect Bake Timeline directly into a VMD stream sink.

        This path deliberately does not invoke the dictionary collector's
        converter or construct ``VmdData``.  The returned bounded metadata is
        owned by the caller and contains only collector evidence needed for
        output verification and diagnostics.
        """

        if not self.supports_streaming():
            raise PrepareVmdExportError("injected VMD collector does not support streaming")
        collector, collector_options = self._collection_context(request)
        collect_to_sink = getattr(collector, "collect_to_sink", None)
        if not callable(collect_to_sink):
            raise PrepareVmdExportError("VMD collector does not expose collect_to_sink(options, sink)")
        collect_started = time.perf_counter()
        try:
            result = collect_to_sink(collector_options, sink)
        except BaseException as exc:
            self._diagnostics["raw_collector"] = {
                "wall_sec": round(time.perf_counter() - collect_started, 6),
                "status": "failed",
                "streaming": True,
                "error": f"{type(exc).__name__}: {exc}",
            }
            self._record_collector_diagnostics(collector)
            self._emit_diagnostics()
            raise
        self._diagnostics["raw_collector"] = {
            "wall_sec": round(time.perf_counter() - collect_started, 6),
            "status": "completed",
            "streaming": True,
        }
        self._record_collector_diagnostics(collector)
        self._diagnostics["collect_total"] = round(
            time.perf_counter() - collect_started, 6
        )
        self._emit_diagnostics()
        if result is None:
            return {}
        if not isinstance(result, Mapping):
            raise PrepareVmdExportError("streaming VMD collector must return bounded metadata")
        return _copy_diagnostics(result)

    def _collection_context(self, request: Any) -> tuple[Any, dict[str, Any]]:
        """Validate the active route and build one isolated collector call."""

        options = self._validated_options(request)
        if self._active_route is None:
            raise PrepareVmdExportError("VMD route was not discovered before collection")
        target_model = self._resolve_target_model(options)
        if target_model != self._active_route.target_model:
            raise PrepareVmdExportError("Current Model changed before collection")
        watch = self._active_watch
        if watch is None or not self._watch_usable(watch):
            raise PrepareVmdExportError("scene revision watch is disabled or stale")
        collector = self._collector
        if collector is None:
            from ..converters.vmd_scene_collector import VmdSceneCollector
            from .native_vmd_batch_sampler import NativeVmdBatchSampler

            sampler = self._bone_channel_sampler
            if sampler is None:
                sampler = NativeVmdBatchSampler(self._cmds_api())
                self._bone_channel_sampler = sampler
            collector = VmdSceneCollector(
                diagnostics_sink=self._diagnostics_sink,
                bone_channel_sampler=sampler,
            )
        # Each invocation gets a new dict.  A collector must not mutate the
        # route cached by discovery or affect a later export.
        collector_options = dict(self._active_route.collector_options)
        collector_options.update(
            {
                "target_model": target_model,
                "model_name": self._active_route.model_name,
                "export_strategy": _BAKE_TIMELINE_EXPORT_STRATEGY,
                "preserve_raw_bone_transforms": False,
            }
        )
        return collector, collector_options

    def _record_collector_diagnostics(self, collector: Any) -> None:
        collector_diagnostics = getattr(collector, "diagnostics_copy", None)
        if callable(collector_diagnostics):
            collector_diagnostics = collector_diagnostics()
        elif collector_diagnostics is None:
            collector_diagnostics = getattr(collector, "diagnostics", None)
        if not isinstance(collector_diagnostics, Mapping):
            return
        if collector_diagnostics:
            self._diagnostics["collector"] = _copy_diagnostics(collector_diagnostics)
            native_diagnostics = collector_diagnostics.get("native_sampler")
            if native_diagnostics is not None:
                self._diagnostics["native_sampler"] = _copy_diagnostics(
                    native_diagnostics
                )
            native_morph_diagnostics = collector_diagnostics.get(
                "native_morph_sampler"
            )
            if native_morph_diagnostics is not None:
                self._diagnostics["native_morph_sampler"] = _copy_diagnostics(
                    native_morph_diagnostics
                )

    def close(self) -> None:
        """Close the active watch at scene/application teardown."""

        if self._active_watch is not None:
            close = getattr(self._active_watch, "close", None)
            if callable(close):
                close()
        self._active_watch = None
        self._active_route = None

    def _validated_options(self, request: Any) -> dict[str, Any]:
        options = _request_options(request)
        export_strategy = str(_field(options, "export_strategy") or "").lower()
        if export_strategy != _BAKE_TIMELINE_EXPORT_STRATEGY:
            raise PrepareVmdExportError(
                "Maya VMD preparation supports Bake Timeline only"
            )
        if not options.get("current_model_root"):
            raise PrepareVmdExportError("current_model_root is required for VMD preparation")
        if not options.get("target_model"):
            raise PrepareVmdExportError("target_model is required for VMD preparation")
        return options

    def _resolve_target_model(self, options: Mapping[str, Any]) -> str:
        current = self._canonical_node(options.get("current_model_root"))
        target = self._canonical_node(options.get("target_model"))
        if current is None or target is None:
            raise PrepareVmdExportError("Current Model target is not a unique Maya node")
        if current != target:
            raise PrepareVmdExportError("target_model does not match Current Model")
        return current

    def _resolve_model_name(self, options: Mapping[str, Any], target_model: str) -> str:
        """Resolve VMD header name: request, imported model metadata, identity."""

        requested = _field(options, "model_name")
        if requested is not None and str(requested).strip():
            return str(requested)
        getter = getattr(self._cmds_api(), "getAttr", None)
        if callable(getter):
            try:
                value = getter(f"{target_model}.{ATTR_MMD_MODEL_NAME}")
            except Exception:
                value = None
            if value is not None and str(value).strip():
                return str(value)
        return target_model

    def _collector_options(self, options: Mapping[str, Any], target_model: str) -> dict[str, Any]:
        result = dict(options)
        result.update(
            {
                "target_model": target_model,
                "export_strategy": _BAKE_TIMELINE_EXPORT_STRATEGY,
                "preserve_raw_bone_transforms": False,
            }
        )
        return result

    def _session_id(self, options: Mapping[str, Any]) -> str:
        service = self._service()
        value = getattr(service, "session_id", None)
        if callable(value):
            value = value()
        value = value or options.get("scene_session_id")
        if value is None or str(value).strip() == "":
            raise PrepareVmdExportError("scene session id is unavailable")
        return str(value)

    def _service(self) -> Any:
        if self._revision_service is None:
            from ..services.scene_revision_service import SceneRevisionService

            self._revision_service = SceneRevisionService()
        return self._revision_service

    def _cmds_api(self) -> Any:
        if self._cmds is None:
            from maya import cmds

            self._cmds = cmds
        return self._cmds

    def _resolve_mobject(self, node: str) -> Any:
        """Resolve a canonical Maya name to an MObject, never as a UUID string."""

        if self._mobject_resolver is not None:
            return self._mobject_resolver(node)
        import maya.api.OpenMaya as om

        selection = om.MSelectionList()
        selection.add(node)
        return selection.getDependNode(0)

    def _canonical_node(self, node: Any) -> Optional[str]:
        if node is None or not str(node).strip():
            return None
        cmds = self._cmds_api()
        try:
            matches = cmds.ls(str(node), long=True) or []
        except Exception as exc:
            raise PrepareVmdExportError(f"could not resolve Maya node {node!r}") from exc
        if isinstance(matches, (str, bytes)) or len(matches) != 1:
            return None
        value = matches[0]
        return str(value) if value else None

    def _stable_uuid(self, node: str) -> str:
        cmds = self._cmds_api()
        try:
            values = cmds.ls(node, uuid=True) or []
        except Exception as exc:
            raise PrepareVmdExportError(f"could not resolve stable UUID for {node!r}") from exc
        if isinstance(values, (str, bytes)) or len(values) != 1 or not values[0]:
            raise PrepareVmdExportError(f"Maya node {node!r} has no unique UUID")
        return str(values[0])

    def _node_type(self, node: str) -> str:
        node_type = getattr(self._cmds_api(), "nodeType", None)
        if not callable(node_type):
            return ""
        try:
            return str(node_type(node) or "")
        except Exception as exc:
            raise PrepareVmdExportError(f"could not resolve Maya node type for {node!r}") from exc

    def _dependency_closure(
        self,
        target_model: str,
        options: Mapping[str, Any],
    ) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str, str]]]:
        cmds = self._cmds_api()
        nodes: set[str] = {target_model}
        descendants = self._list_relatives(target_model, allDescendents=True, fullPath=True)
        nodes.update(self._canonical_required(value) for value in descendants)
        for name in _as_list(options.get("joints")) + _as_list(options.get("blend_shapes")):
            nodes.add(self._canonical_required(name))
        mesh_nodes = self._list_relatives(target_model, allDescendents=True, type="mesh", fullPath=True)
        for mesh in mesh_nodes:
            mesh_path = self._canonical_required(mesh)
            nodes.add(mesh_path)
            try:
                history = cmds.listHistory(mesh_path, pruneDagObjects=True) or []
            except Exception as exc:
                raise PrepareVmdExportError(f"could not inspect history for {mesh_path!r}") from exc
            for history_node in history:
                nodes.add(self._canonical_required(history_node))

        for marker, option_name in ((_CAMERA_MARKER, "cameras"), (_LIGHT_MARKER, "lights")):
            if option_name in options:
                tagged = _as_list(options.get(option_name))
            else:
                try:
                    tagged = cmds.ls(f"*.{marker}", objectsOnly=True, long=True) or []
                except Exception as exc:
                    raise PrepareVmdExportError(f"could not discover tagged {marker} tracks") from exc
            for track in tagged:
                track_path = self._canonical_required(track)
                nodes.add(track_path)
                # The camera collector samples shape properties (focalLength,
                # orthographic, and orthographicWidth), while the light
                # collector may sample a child shape color source.
                nodes.update(
                    self._canonical_required(child)
                    for child in self._list_relatives(track_path, shapes=True, fullPath=True)
                )

        # Walk upstream inputs. This captures animCurves, authored append/IK
        # routes, morph controllers, and any custom DG node used by a track.
        topology: set[tuple[str, str, str, str]] = set()
        queue = list(sorted(nodes))
        inspected: set[str] = set()
        while queue:
            node = queue.pop(0)
            if node in inspected:
                continue
            inspected.add(node)
            pairs, upstream_plugs = self._connection_pairs(node)
            for left, right in pairs:
                left_node, left_attr = self._split_plug(left)
                right_node, right_attr = self._split_plug(right)
                left_path = self._canonical_required(left_node)
                right_path = self._canonical_required(right_node)
                left_plug = f"{left_path}.{left_attr}"
                right_plug = f"{right_path}.{right_attr}"
                if left_plug in upstream_plugs and right_plug not in upstream_plugs:
                    source_path, source_attr = left_path, left_attr
                    destination_path, destination_attr = right_path, right_attr
                elif right_plug in upstream_plugs and left_plug not in upstream_plugs:
                    source_path, source_attr = right_path, right_attr
                    destination_path, destination_attr = left_path, left_attr
                elif left_path == node and right_path != node:
                    source_path, source_attr = right_path, right_attr
                    destination_path, destination_attr = left_path, left_attr
                elif right_path == node and left_path != node:
                    source_path, source_attr = left_path, left_attr
                    destination_path, destination_attr = right_path, right_attr
                else:
                    # A topology response that cannot identify the queried
                    # endpoint is not safe to include in the closure.
                    raise PrepareVmdExportError(f"Maya returned ambiguous connection topology for {node!r}")
                source_uuid = self._stable_uuid(source_path)
                destination_uuid = self._stable_uuid(destination_path)
                topology.add((source_uuid, source_attr, destination_uuid, destination_attr))
                if source_path not in nodes:
                    nodes.add(source_path)
                    queue.append(source_path)

        records = []
        for node in nodes:
            records.append((self._stable_uuid(node), node, self._node_type(node)))
        return records, sorted(topology)

    def _list_relatives(self, node: str, **kwargs: Any) -> list[Any]:
        method = getattr(self._cmds_api(), "listRelatives", None)
        if not callable(method):
            raise PrepareVmdExportError("Maya listRelatives API is unavailable")
        try:
            return list(method(node, **kwargs) or [])
        except Exception as exc:
            raise PrepareVmdExportError(f"could not inspect descendants for {node!r}") from exc

    def _connection_pairs(self, node: str) -> tuple[list[tuple[str, str]], set[str]]:
        method = getattr(self._cmds_api(), "listConnections", None)
        if not callable(method):
            raise PrepareVmdExportError("Maya listConnections API is unavailable")
        try:
            upstream_values = method(
                node,
                plugs=True,
                source=True,
                destination=False,
            ) or []
            values = method(
                node,
                plugs=True,
                connections=True,
                source=True,
                destination=False,
            ) or []
        except Exception as exc:
            raise PrepareVmdExportError(f"could not inspect connections for {node!r}") from exc
        if isinstance(upstream_values, Mapping):
            upstream_values = list(upstream_values.values())
        # Maya may return short node names from listConnections even when the
        # queried node was resolved to a full DAG path.  Normalize the source
        # plugs before comparing them with the canonicalized connection pair;
        # this is essential for valid intra-node connections, where the node
        # identity cannot disambiguate the source and destination endpoints.
        upstream_plugs = set()
        for value in upstream_values:
            source_node, source_attr = self._split_plug(value)
            source_path = self._canonical_required(source_node)
            upstream_plugs.add(f"{source_path}.{source_attr}")
        if isinstance(values, Mapping):
            values = [item for pair in values.items() for item in pair]
        values = list(values)
        if len(values) % 2:
            raise PrepareVmdExportError(f"Maya returned incomplete connection topology for {node!r}")
        return (
            [(str(values[index]), str(values[index + 1])) for index in range(0, len(values), 2)],
            upstream_plugs,
        )

    @staticmethod
    def _split_plug(plug: Any) -> tuple[str, str]:
        value = str(plug)
        node, separator, attribute = value.partition(".")
        if not separator or not node or not attribute:
            raise PrepareVmdExportError(f"Maya returned an unresolved plug {value!r}")
        return node, attribute

    def _canonical_required(self, node: Any) -> str:
        value = self._canonical_node(node)
        if value is None:
            raise PrepareVmdExportError(f"Maya dependency {node!r} is unresolved")
        return value

    @staticmethod
    def _cache_id(session_id: str, target_uuid: str, dependency_fingerprint: str) -> str:
        value = "|".join((session_id, target_uuid, dependency_fingerprint)).encode("utf-8")
        return f"vmd-c-maya:{hashlib.sha256(value).hexdigest()}"

    @staticmethod
    def _watch_usable(watch: Any) -> bool:
        if watch is None:
            return False
        usable = getattr(watch, "usable", None)
        if callable(usable):
            usable = usable()
        if usable is None:
            return False
        return bool(usable)

    @staticmethod
    def _watch_current(watch: Any) -> bool:
        current = getattr(watch, "current", None)
        if callable(current):
            current = current()
        return bool(current)


def create_maya_vmd_prepare_action(
    *,
    diagnostics_sink: Any = None,
    bone_channel_sampler: Any = None,
) -> Any:
    """Create the production backend/action pair without importing Maya.

    The backend resolves ``maya.cmds`` only when a prepare operation reaches
    discovery, and the revision service imports OpenMaya only while arming a
    watch.  Keeping construction here lets the UI presenter use the real
    production action while remaining import-safe in mayapy-free tooling and
    unit-test processes.
    """

    from ..actions.prepare_vmd_export_action import PrepareVmdExportAction

    backend = MayaVmdPrepareBackend(
        diagnostics_sink=diagnostics_sink,
        bone_channel_sampler=bone_channel_sampler,
    )
    return PrepareVmdExportAction(backend)


__all__ = [
    "MayaVmdExportRoute",
    "MayaVmdPrepareBackend",
    "create_maya_vmd_prepare_action",
]
