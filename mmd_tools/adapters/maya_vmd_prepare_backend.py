"""Maya-owned preparation boundary for user-path Mode C VMD exports.

The public prepare action is intentionally Maya independent.  This adapter
supplies the small host-side seam it needs: resolve the Current Model, build a
conservative dependency closure, arm a :class:`SceneRevisionService` watch,
and collect one Mode C payload.  Maya modules are imported lazily so importing
the package remains safe in unit-test and tooling processes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
from typing import Any, Optional

from ..actions.prepare_vmd_export_action import (
    PrepareVmdExportError,
    VMD_MODE_C,
    VmdExportDiscovery,
)
from ..io.vmd_exporter import VmdExporter
from ..services.scene_revision_service import SceneRevisionService
from ..validation.snapshot import fingerprint_payload


_CAMERA_MARKER = "mmd_camera"
_LIGHT_MARKER = "mmd_light"


@dataclass(frozen=True)
class MayaVmdExportRoute:
    """The immutable host route used by one preparation operation."""

    target_model: str
    collector_options: Mapping[str, Any]
    dependency_nodes: tuple[str, ...]
    dependency_uuids: tuple[str, ...]
    dependency_fingerprint: str


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
        "mode",
        "vmd_mode",
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


class MayaVmdPrepareBackend:
    """Discover, watch, and collect one Current Model-scoped Mode C route.

    ``revision_service`` is injected in tests and in application wiring.  If
    omitted it is created lazily as a normal ``SceneRevisionService``; that
    service imports Maya only when ``arm`` actually needs host callbacks.
    """

    def __init__(
        self,
        cmds_module: Any = None,
        *,
        collector: Any = None,
        converter: Any = None,
        revision_service: Any = None,
        mobject_resolver: Any = None,
    ) -> None:
        self._cmds = cmds_module
        self._collector = collector
        self._converter = converter
        self._revision_service = revision_service
        self._mobject_resolver = mobject_resolver
        self._active_watch: Any = None
        self._active_route: Optional[MayaVmdExportRoute] = None
        self._watch_generation = 0

    @property
    def revision_provider(self) -> "MayaVmdPrepareBackend":
        """Return the provider paired with this backend for PrepareAction."""

        return self

    @property
    def active_watch(self) -> Any:
        """Expose the current watch for host teardown and diagnostics."""

        return self._active_watch

    def discover(self, request: Any) -> VmdExportDiscovery:
        """Resolve the Current Model and fingerprint its dependency closure."""

        options = self._validated_options(request)
        target_model = self._resolve_target_model(options)
        target_uuid = self._stable_uuid(target_model)
        records, topology = self._dependency_closure(target_model, options)
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
        )
        self._active_route = route
        return VmdExportDiscovery(
            scene_session_id=session_id,
            target_uuid=target_uuid,
            target_identity=target_model,
            dependency_closure_fingerprint=dependency_fingerprint,
            cache_id=cache_id,
            route=route,
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
            dependencies = [self._resolve_mobject(node) for node in discovery.route.dependency_nodes]
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

    def collect(self, request: Any) -> Any:
        """Collect one Mode C payload through the production collector."""

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

            collector = VmdSceneCollector()
        collect = getattr(collector, "collect", None)
        if not callable(collect):
            if not callable(collector):
                raise PrepareVmdExportError("VMD collector is not callable")
            collect = collector
        # Each invocation gets a new dict.  A collector must not mutate the
        # route cached by discovery or affect a later export.
        collector_options = dict(self._active_route.collector_options)
        collector_options.update(
            {
                "target_model": target_model,
                "vmd_mode": VMD_MODE_C,
                "mode": VMD_MODE_C,
                "preserve_raw_bone_transforms": False,
            }
        )
        payload = collect(collector_options)
        converter = self._converter
        if converter is None:
            converter = VmdExporter(native_exporter=None).to_vmd_data
        elif hasattr(converter, "to_vmd_data") and callable(converter.to_vmd_data):
            converter = converter.to_vmd_data
        if not callable(converter):
            raise PrepareVmdExportError("VMD converter is not callable")
        return converter(payload)

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
        mode = str(_field(options, "mode", "vmd_mode") or "").upper()
        if mode != VMD_MODE_C:
            raise PrepareVmdExportError("Maya VMD preparation supports Mode C only")
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

    def _collector_options(self, options: Mapping[str, Any], target_model: str) -> dict[str, Any]:
        result = dict(options)
        result.update(
            {
                "target_model": target_model,
                "vmd_mode": VMD_MODE_C,
                "mode": VMD_MODE_C,
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
        upstream_plugs = {str(value) for value in upstream_values}
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
        node, separator, attribute = value.rpartition(".")
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


MayaVmdPrepareRevisionProvider = MayaVmdPrepareBackend


__all__ = [
    "MayaVmdExportRoute",
    "MayaVmdPrepareBackend",
    "MayaVmdPrepareRevisionProvider",
]
