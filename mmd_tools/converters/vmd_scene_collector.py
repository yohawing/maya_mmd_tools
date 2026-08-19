"""Minimum Maya scene collector for VMD export.

This collector gathers keyed joint transforms, blendShape weights, and
model-scoped PMX network morph controller weights into the dict contract
consumed by ``VmdExporter``. Bone translation can be converted back to VMD
offsets when a bind-pose map is supplied, and XYZ joint rotations are
converted back to VMD quaternions with jointOrient compensation. Explicit
Mode C requests sample the selected Maya frame range at one-frame intervals:
bones use the native sampler while morph/IK/camera/light tracks advance Maya's
normal Timeline and read current-frame values. Sampling failures block export.
An imported raw key/interpolation/transform payload is reused only when the
caller explicitly opts into ``preserve_raw_bone_transforms``; Mode A and
low-level collector callers retain sparse collection semantics.
"""

import json
import math
import time
from contextlib import nullcontext
from typing import Any, Iterable, Mapping, Optional, Sequence

import maya.api.OpenMaya as om
from maya import cmds

from mmd_tools.core.constants import (
    ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_CAMERA,
    ATTR_MMD_LIGHT,
    ATTR_MMD_MODEL_NAME,
    ATTR_MMD_VMD_IMPORT_PROVENANCE_JSON,
)
from mmd_tools.core.mmd_control_rig_builder import (
    CONTROL_RIG_EDIT,
    read_mmd_control_rig_metadata,
    resolve_mmd_control_rig_binding_authored_plugs,
    resolve_mmd_control_rig_binding_joint,
)
from mmd_tools.core.mmd_control_rig_analyzer import (
    INPUT_BONE_MORPH_BASE,
    INPUT_IK_CONTROLLER,
)
from mmd_tools.core.morph_metadata_reader import parse_blendshape_morph_names
from mmd_tools.converters.morph_scene_metadata import iter_morph_network_metadata
from mmd_tools.converters.bone_morph_runtime import resolve_owned_bone_morph_base_routes
from mmd_tools.converters.vmd_camera_animation import (
    ATTR_MMD_CAMERA_ROOT_NODE,
    ATTR_MMD_CAMERA_TARGET_NODE,
    MMD_CAMERA_EXPR_SCALE_ATTR,
    mmd_camera_rotation_from_maya_forward_up,
)
from mmd_tools.converters.vmd_append_decomposition import collect_append_info
from mmd_tools.converters.vmd_ik_enabled_animation import collect_ik_nodes_by_bone_name
from mmd_tools.converters.vmd_ik_passthrough import collect_mmd_ik_passthrough_info
from mmd_tools.converters.vmd_import_state import get_stored_bind_translate
from mmd_tools.converters.vmd_runtime_sampling import (
    maya_time_to_vmd_frame as _maya_time_to_vmd_frame_at_fps,
)
from mmd_tools.converters.vmd_redirected_authoring_proxy import (
    redirected_authority_matches,
    resolve_redirected_authoring_proxy_authority,
)
from mmd_tools.validation.snapshot import fingerprint_payload


_BONE_EXPORT_ATTRS = (
    "translateX",
    "translateY",
    "translateZ",
    "rotateX",
    "rotateY",
    "rotateZ",
)


_CAMERA_EXPORT_ATTRS = (
    "translateX",
    "translateY",
    "translateZ",
    "mmd_camera_target_x",
    "mmd_camera_target_y",
    "mmd_camera_target_z",
    "rotateX",
    "rotateY",
    "rotateZ",
    "mmd_camera_rotation_x",
    "mmd_camera_rotation_y",
    "mmd_camera_rotation_z",
    "mmd_camera_distance",
    "mmd_camera_viewing_angle",
    "mmd_camera_perspective",
)
_LIGHT_ROTATE_ATTRS = ("rotateX", "rotateY", "rotateZ")
_LIGHT_COLOR_ATTRS = ("mmd_light_colorR", "mmd_light_colorG", "mmd_light_colorB")
_LIGHT_SHAPE_COLOR_ATTRS = ("colorR", "colorG", "colorB")
_ATTR_MMD_CAMERA_RIG_TYPE = "mmd_camera_rig_type"
_MMD_CAMERA_AIM_ROLL_RIG_TYPE = "mmd_aim_roll"
_TRANSFORM_EXPORT_ATTRS = ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
_CAMERA_SHAPE_EXPORT_ATTRS = ("focalLength", "orthographic", "orthographicWidth")
_TRACK_SELECTION_DECISIONS = (
    "omitted_default",
    "constant_one_key",
    "authored_sampled",
    "dependency_baked",
    "physics_output_excluded",
)
_MAX_TRACK_SELECTION_EVIDENCE = 128
_MAYA_TIME_UNIT_FPS = {
    "game": 15.0,
    "film": 24.0,
    "pal": 25.0,
    "ntsc": 30.0,
    "show": 48.0,
    "palf": 50.0,
    "ntscf": 60.0,
}


def _copy_diagnostics(value: Any) -> Any:
    """Detach nested timing/count diagnostics from the collector."""

    if isinstance(value, Mapping):
        return {str(key): _copy_diagnostics(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_copy_diagnostics(item) for item in value]
    return value


def _is_direct_authored_track(node: str, attrs: Sequence[str]) -> bool:
    """Reject unknown incoming graph nodes before selecting one source key."""
    for attr in attrs:
        try:
            sources = cmds.listConnections(
                f"{node}.{attr}", source=True, destination=False, plugs=True
            ) or []
            if isinstance(sources, (str, bytes)):
                sources = [sources]
            if len(sources) > 1:
                return False
            if sources and not str(cmds.nodeType(str(sources[0]).split(".", 1)[0])).startswith("animCurve"):
                return False
        except Exception:
            return False
    return True


_MODE_C_LAYER_STATE_ATTRS = ("weight", "mute", "solo", "override", "passthrough")
_MODE_C_LAYER_OPTIONAL_STATE_ATTRS = ("rotationAccumulationMode",)


def _mode_c_writable_plug(node: str, attribute: str) -> bool:
    """Check the resolved physical plug using Maya API 2.0."""
    try:
        selection = om.MSelectionList()
        selection.add(f"{node}.{attribute}")
        return bool(om.MFnAttribute(selection.getPlug(0).attribute()).writable)
    except Exception:
        return False


def _mode_c_layer_chain(layer: str) -> Optional[set[str]]:
    """Validate one layer and its parents through BaseAnimation."""
    chain = set()
    while layer:
        if layer in chain:
            return None
        chain.add(layer)
        try:
            for attribute in _MODE_C_LAYER_STATE_ATTRS:
                plug = f"{layer}.{attribute}"
                if _incoming_connection_state(layer, (attribute,), strict=True) != "none":
                    return None
                if cmds.keyframe(plug, query=True, timeChange=True) or []:
                    return None
            for attribute in _MODE_C_LAYER_OPTIONAL_STATE_ATTRS:
                if not cmds.attributeQuery(attribute, node=layer, exists=True):
                    continue
                plug = f"{layer}.{attribute}"
                if _incoming_connection_state(layer, (attribute,), strict=True) != "none":
                    return None
                if cmds.keyframe(plug, query=True, timeChange=True) or []:
                    return None
            parent = cmds.animLayer(layer, q=True, parent=True)
        except Exception:
            return None
        if isinstance(parent, (list, tuple)):
            if len(parent) > 1:
                return None
            parent = parent[0] if parent else None
        layer = str(parent or "")
    return chain


def _mode_c_direct_curve_source(curve: str, expected_type: str) -> bool:
    """Accept a channel-specific time curve with implicit or explicit time."""
    try:
        if str(cmds.nodeType(curve) or "") != expected_type:
            return False
        sources = cmds.listConnections(f"{curve}.input", source=True, destination=False, plugs=True, skipConversionNodes=False) or []
    except Exception:
        return False
    if isinstance(sources, (str, bytes)) or len(sources) > 1:
        return False
    if not sources:
        return True
    try:
        return str(cmds.nodeType(str(sources[0]).split(".", 1)[0]) or "") == "time"
    except Exception:
        return False


def _mode_c_validate_anim_blend(
    node: str,
    *,
    expected_type: str,
    curve_type: str,
    physical_node: str,
) -> Optional[str]:
    """Validate one supported additive Animation Layer blend node."""
    try:
        if str(cmds.nodeType(node) or "") != expected_type:
            return None
    except Exception:
        return None
    try:
        pairs = cmds.listConnections(
            node, s=True, d=False, p=True, c=True, skipConversionNodes=False
        ) or []
    except Exception:
        return None
    if isinstance(pairs, (str, bytes)) or len(pairs) % 2:
        return None
    incoming: dict[str, str] = {}
    for index in range(0, len(pairs), 2):
        left, right = str(pairs[index]), str(pairs[index + 1])
        if left.startswith(f"{node}."):
            destination, source = left.split(".", 1)[1], right
        elif right.startswith(f"{node}."):
            destination, source = right.split(".", 1)[1], left
        else:
            return None
        if destination in incoming:
            return None
        incoming[destination] = source
    allowed = (
        {"weightA", "weightB", "inputA", "inputB"}
        if expected_type == "animBlendNodeAdditiveDL"
        else {
            "rotateOrder", "accumulationMode", "weightA", "weightB",
            "inputAX", "inputAY", "inputAZ", "inputBX", "inputBY", "inputBZ",
        }
    )
    if any(attribute not in allowed for attribute in incoming):
        return None
    input_attrs = (
        ("inputA", "inputB")
        if expected_type == "animBlendNodeAdditiveDL"
        else ("inputAX", "inputAY", "inputAZ", "inputBX", "inputBY", "inputBZ")
    )
    for attribute in input_attrs:
        if attribute in incoming and not _mode_c_direct_curve_source(
            incoming[attribute].split(".", 1)[0], curve_type
        ):
            return None
        if attribute in incoming and incoming[attribute].split(".", 1)[1] != "output":
            return None
    layers = set()
    for attribute in ("weightA", "weightB", "accumulationMode"):
        if attribute not in incoming:
            continue
        source_node = incoming[attribute].split(".", 1)[0]
        try:
            valid_source = str(cmds.nodeType(source_node) or "") == "animLayer"
        except Exception:
            valid_source = False
        if not valid_source:
            return None
        source_attribute = incoming[attribute].split(".", 1)[1]
        expected_source_attribute = {
            "weightA": "backgroundWeight",
            "weightB": "foregroundWeight",
            "accumulationMode": "outRotationAccumulationMode",
        }[attribute]
        if source_attribute != expected_source_attribute:
            return None
        layers.add(source_node)
    if expected_type == "animBlendNodeAdditiveRotation" and "rotateOrder" in incoming:
        rotate_order_node, rotate_order_attr = incoming["rotateOrder"].split(".", 1)
        if (
            rotate_order_attr != "rotateOrder"
            or _canonical_dag_path(rotate_order_node)
            != _canonical_dag_path(physical_node)
        ):
            return None
    if not layers:
        return None
    valid_layers = set()
    for layer in sorted(layers):
        chain = _mode_c_layer_chain(layer)
        if chain is None:
            return None
        valid_layers.update(chain)
    try:
        scene_layers = cmds.ls(type="animLayer") or []
    except Exception:
        return None
    for scene_layer in scene_layers:
        if _mode_c_layer_chain(str(scene_layer)) is None:
            return None
    for attribute in ("weightA", "weightB", "accumulationMode"):
        if attribute in incoming and incoming[attribute].split(".", 1)[0] not in valid_layers:
            return None
    return sorted(layers)[0]


def _mode_c_authored_input_plug(
    node: str,
    physical_attr: str,
    logical_attr: str,
) -> bool:
    """Limit one-key route folding to known authored input surfaces."""
    try:
        node_type = str(cmds.nodeType(node) or "")
    except Exception:
        return False
    if node_type in {"transform", "joint"}:
        return physical_attr == logical_attr
    channel = "Translate" if logical_attr.startswith("translate") else "Rotate"
    axis = logical_attr[-1]
    if node_type in {"mmdAppend", "mmdBoneMorphAccum"}:
        return physical_attr == f"base{channel}{axis}"
    if node_type == "mmdPhysicsBoneDriver":
        return physical_attr == f"inPre{channel}{axis}"
    if node_type != "mmdCcdIk" or channel != "Rotate":
        return False
    prefix = "inputRotate["
    suffix = f"].inputRotateElement{axis}"
    if not physical_attr.startswith(prefix) or not physical_attr.endswith(suffix):
        return False
    return physical_attr[len(prefix) : -len(suffix)].isdigit()


def _mode_c_single_key_bone_route(
    joint: str,
    route: Mapping[str, tuple[str, str]],
) -> Optional[str]:
    """Return ``direct``/``layered`` for a safe one-key source graph."""
    blend_kinds: set[str] = set()
    for attribute in _BONE_EXPORT_ATTRS:
        node, physical_attr = route.get(attribute, (joint, attribute))
        if not _mode_c_authored_input_plug(
            str(node), str(physical_attr), attribute
        ) or not _mode_c_writable_plug(str(node), str(physical_attr)):
            return None
        try:
            sources = cmds.listConnections(f"{node}.{physical_attr}", source=True, destination=False, plugs=True, skipConversionNodes=False) or []
        except Exception:
            return None
        if isinstance(sources, (str, bytes)) or len(sources) > 1:
            return None
        if not sources:
            continue
        source_node = str(sources[0]).split(".", 1)[0]
        source_attr = str(sources[0]).split(".", 1)[1]
        try:
            source_type = str(cmds.nodeType(source_node) or "")
        except Exception:
            return None
        expected_curve = "animCurveTL" if attribute.startswith("translate") else "animCurveTA"
        if source_type == expected_curve:
            if source_attr != "output" or not _mode_c_direct_curve_source(
                source_node, expected_curve
            ):
                return None
            continue
        expected_blend = "animBlendNodeAdditiveDL" if attribute.startswith("translate") else "animBlendNodeAdditiveRotation"
        if source_type != expected_blend:
            return None
        expected_output = (
            "output"
            if attribute.startswith("translate")
            else f"output{attribute[-1]}"
        )
        if source_attr != expected_output:
            return None
        layer = _mode_c_validate_anim_blend(
            source_node,
            expected_type=expected_blend,
            curve_type=expected_curve,
            physical_node=str(node),
        )
        if layer is None:
            return None
        blend_kinds.add(layer)
    if len(blend_kinds) > 1:
        return None
    return "layered" if blend_kinds else "direct"


def _has_no_incoming_connections(node: str, attrs: Sequence[str]) -> bool:
    """Return whether every logical plug is completely unconnected."""

    try:
        return _incoming_connection_state(node, attrs) == "none"
    except Exception:
        return False


def _incoming_connection_state(
    node: str,
    attrs: Sequence[str],
    *,
    strict: bool = False,
) -> str:
    """Classify logical incoming connections without hiding query failures.

    ``strict`` is used by standard Mode C keyless-track planning.  A failed
    connection query is not equivalent to an unconnected plug there: sampling
    the visible joint would otherwise silently bake an unknown dependency.
    """

    has_incoming = False
    for attr in attrs:
        try:
            sources = cmds.listConnections(
                f"{node}.{attr}",
                source=True,
                destination=False,
                plugs=True,
            ) or []
        except Exception:
            if strict:
                raise
            return "error"
        if isinstance(sources, (str, bytes)) or sources:
            has_incoming = True
    return "some" if has_incoming else "none"


def _new_track_selection() -> dict[str, Any]:
    return {
        "counts": {key: 0 for key in _TRACK_SELECTION_DECISIONS},
        "counts_by_section": {
            section: {key: 0 for key in _TRACK_SELECTION_DECISIONS}
            for section in ("bone", "morph")
        },
        "key_counts": {key: 0 for key in ("source", "planned", "reduced", "added")},
        "evidence": [],
        "evidence_omitted_count": 0,
        "source_omission_identity": {
            "count": 0,
            "fingerprint": fingerprint_payload([]),
        },
    }


def _normalize_track_selection_identity(section: Any, name: Any) -> tuple[str, str]:
    """Return the stable section/name pair used by selection diagnostics."""

    normalized_name = " ".join(str(name or "").strip().casefold().split())
    return str(section).strip().lower(), normalized_name


def _canonical_dag_path(node: str) -> Optional[str]:
    """Resolve one Maya node to an unambiguous long DAG path."""
    try:
        matches = cmds.ls(node, long=True) or []
    except Exception:
        return None
    if len(matches) != 1:
        return None
    return str(matches[0])


def _dag_path_is_under_root(node: str, root_path: str) -> bool:
    """Return whether a DAG node is the root or a descendant of it."""
    node_path = _canonical_dag_path(node)
    if not node_path:
        return False
    return node_path == root_path or node_path.startswith(f"{root_path}|")


def _raw_vmd_rotation_interpolation(
    provenance: Optional[Mapping[str, Any]],
) -> dict[str, dict[int, bytes]]:
    """Decode complete raw bone interpolation records into collector keys."""
    if not isinstance(provenance, Mapping):
        return {}
    result: dict[str, dict[int, bytes]] = {}
    for record in provenance.get("raw_bone_interpolation", ()) or ():
        if not isinstance(record, Mapping):
            continue
        name = str(record.get("bone_name") or "")
        try:
            frame_number = int(record.get("frame_number"))
            interpolation = bytes(record.get("interpolation", ()))
        except (TypeError, ValueError, OverflowError):
            continue
        if not name or frame_number < 0 or len(interpolation) != 64:
            continue
        result.setdefault(name, {})[frame_number] = interpolation
    return result


def _raw_vmd_bone_transforms(
    provenance: Optional[Mapping[str, Any]],
) -> dict[tuple[str, int], tuple[tuple[float, ...], tuple[float, ...]]]:
    """Decode complete raw bone position/rotation records for Mode A reuse."""
    if not isinstance(provenance, Mapping) or not provenance.get(
        "raw_bone_transform_complete"
    ):
        return {}
    records = provenance.get("raw_bone_interpolation")
    if not isinstance(records, list):
        return {}
    try:
        expected_count = int(provenance.get("raw_bone_key_count", len(records)))
    except (TypeError, ValueError, OverflowError):
        return {}
    if expected_count != len(records):
        return {}
    result = {}
    for record in records:
        if not isinstance(record, Mapping):
            return {}
        name = str(record.get("bone_name") or "")
        try:
            frame_number = int(record.get("frame_number"))
            position = tuple(float(value) for value in record.get("position", ()))
            rotation = tuple(float(value) for value in record.get("rotation", ()))
        except (TypeError, ValueError, OverflowError):
            return {}
        key = (name, frame_number)
        if (
            not name
            or frame_number < 0
            or len(position) != 3
            or len(rotation) != 4
            or not all(math.isfinite(value) for value in position + rotation)
            or key in result
        ):
            return {}
        result[key] = (position, rotation)
    return result


def _raw_bone_transform_matches(
    position: Sequence[float],
    rotation: Sequence[float],
    expected: tuple[tuple[float, ...], tuple[float, ...]],
) -> bool:
    """Return whether Maya reconstruction still represents the raw payload."""
    expected_position, expected_rotation = expected
    try:
        if len(position) != 3 or len(rotation) != 4:
            return False
        if any(
            not math.isclose(float(actual), float(source), rel_tol=0.0, abs_tol=1.0e-5)
            for actual, source in zip(position, expected_position)
        ):
            return False
        actual_rotation = tuple(float(value) for value in rotation)
        source_rotation = tuple(float(value) for value in expected_rotation)
        actual_norm = math.sqrt(sum(value * value for value in actual_rotation))
        source_norm = math.sqrt(sum(value * value for value in source_rotation))
        if actual_norm <= 1.0e-12 or source_norm <= 1.0e-12:
            return False
        dot = abs(
            sum(actual * source for actual, source in zip(actual_rotation, source_rotation))
            / (actual_norm * source_norm)
        )
        return math.isclose(dot, 1.0, rel_tol=0.0, abs_tol=1.0e-5)
    except (TypeError, ValueError, OverflowError):
        return False


def _index_raw_bone_transform_frames(
    raw_bone_transforms: Mapping[
        tuple[str, int], tuple[tuple[float, ...], tuple[float, ...]]
    ],
) -> dict[str, set[int]]:
    """Index raw transform frame numbers by bone name in one pass."""
    result: dict[str, set[int]] = {}
    for bone_name, frame_number in raw_bone_transforms:
        result.setdefault(bone_name, set()).add(frame_number)
    return result


def _read_vmd_import_provenance(target_model: Optional[str]) -> Optional[dict[str, Any]]:
    """Read complete raw VMD bone provenance from one model root."""
    if not target_model:
        return None
    try:
        if not cmds.attributeQuery(
            ATTR_MMD_VMD_IMPORT_PROVENANCE_JSON,
            node=target_model,
            exists=True,
        ):
            return None
        raw = cmds.getAttr(f"{target_model}.{ATTR_MMD_VMD_IMPORT_PROVENANCE_JSON}")
        provenance = json.loads(raw or "")
    except (TypeError, ValueError, RuntimeError):
        return None
    if not isinstance(provenance, dict) or not provenance.get("raw_bone_interpolation_complete"):
        return None
    records = provenance.get("raw_bone_interpolation")
    if not isinstance(records, list):
        return None
    try:
        expected_count = int(provenance.get("raw_bone_key_count", len(records)))
    except (TypeError, ValueError, OverflowError):
        return None
    if expected_count != len(records):
        return None
    decoded = _raw_vmd_rotation_interpolation(provenance)
    if sum(len(frames) for frames in decoded.values()) != len(records):
        return None
    return provenance


def _close_native_samples(native_samples: Any) -> None:
    """Close native sample storage without requiring legacy test fakes to do so."""

    close = getattr(native_samples, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            # Storage cleanup must not mask an export or collection failure.
            pass


class VmdSceneCollector:
    """Collect minimum VMD-compatible animation data from a Maya scene."""

    def __init__(self, diagnostics_sink=None, bone_channel_sampler=None):
        """Create a collector with optional end-of-collection diagnostics sink.

        The sink receives one small JSON-shaped dictionary after collection;
        it never receives per-frame values.  Keeping it optional preserves the
        existing low-level collector API and keeps the hot loop untouched.
        ``bone_channel_sampler`` is the required Mode C bone sampling seam.
        Native command, protocol, and value failures are fatal for Mode C;
        sparse non-Mode-C collection continues to use ``cmds.getAttr``.
        """

        self._diagnostics_sink = diagnostics_sink
        # Optional native batch sampling is intentionally injected at this
        # seam.  Route discovery, quaternion conversion, VMD dict assembly,
        # and every non-bone track remain Python-owned.
        self._bone_channel_sampler = bone_channel_sampler
        self._diagnostics: dict[str, Any] = {}
        # Standard Mode C physics ownership is scoped to one collection.  A
        # target that cannot be routed through authored/pre-physics channels
        # must not later be mistaken for an ordinary keyless dependency.
        self._mode_c_physics_output_excluded_targets: set[str] = set()
        self._source_omission_identities: set[tuple[str, str]] = set()

    @property
    def diagnostics(self) -> dict[str, Any]:
        """Return detached timing and count evidence for the last collect."""

        report = self._diagnostics.get("track_selection")
        if isinstance(report, dict):
            identities = [
                list(identity) for identity in sorted(self._source_omission_identities)
            ]
            report["source_omission_identity"] = {
                "count": len(identities),
                "fingerprint": fingerprint_payload(identities),
            }
        return _copy_diagnostics(self._diagnostics)

    @property
    def diagnostics_copy(self) -> dict[str, Any]:
        """Alias used by Maya preparation evidence."""

        return self.diagnostics

    def _emit_diagnostics(self) -> None:
        """Flush the latest bounded diagnostics snapshot to the optional sink."""

        sink = self._diagnostics_sink
        if not callable(sink):
            return
        try:
            sink(self.diagnostics)
        except Exception as exc:  # diagnostics must never alter export semantics
            self._diagnostics["sink_error"] = f"{type(exc).__name__}: {exc}"

    def _accept_native_diagnostics(self, value: Any) -> None:
        """Merge native preflight/chunk evidence and flush it immediately."""

        if isinstance(value, Mapping):
            merged = dict(self._diagnostics.get("native_sampler", {}))
            merged.update(_copy_diagnostics(value))
            self._diagnostics["native_sampler"] = merged
            self._emit_diagnostics()

    def _record_track_selection(
        self,
        section: str,
        name: str,
        decision: str,
        reason: str,
        source_key_count: int,
        planned_key_count: int,
    ) -> None:
        """Record bounded track-selection evidence without frame values."""
        if decision not in _TRACK_SELECTION_DECISIONS:
            decision = "authored_sampled"
        normalized_section, normalized_name = _normalize_track_selection_identity(
            section, name
        )
        source_count = max(0, int(source_key_count))
        planned_count = max(0, int(planned_key_count))
        report = self._diagnostics.setdefault("track_selection", _new_track_selection())
        report["counts"][decision] += 1
        section_counts = report["counts_by_section"].get(normalized_section)
        if section_counts is not None:
            section_counts[decision] += 1
        report["key_counts"]["source"] += source_count
        report["key_counts"]["planned"] += planned_count
        report["key_counts"]["reduced"] += max(source_count - planned_count, 0)
        report["key_counts"]["added"] += max(planned_count - source_count, 0)
        if decision == "omitted_default" and source_count > 0:
            self._source_omission_identities.add((normalized_section, normalized_name))
        if len(report["evidence"]) < _MAX_TRACK_SELECTION_EVIDENCE:
            report["evidence"].append(
                {
                    "section": normalized_section,
                    "name": normalized_name,
                    "decision": decision,
                    "reason": str(reason),
                    "source_key_count": source_count,
                    "planned_key_count": planned_count,
                }
            )
        else:
            report["evidence_omitted_count"] += 1

    def collect(self, options: Optional[Mapping[str, Any]] = None) -> dict:
        """Collect and publish low-overhead timing diagnostics."""

        started = time.perf_counter()
        self._diagnostics = {}
        self._source_omission_identities = set()
        try:
            result = self._collect_impl(options)
            self._diagnostics["status"] = "completed"
            return result
        except Exception as exc:
            self._diagnostics["status"] = "failed"
            self._diagnostics["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._diagnostics["total"] = {
                "wall_sec": round(time.perf_counter() - started, 6),
            }
            self._emit_diagnostics()

    def _collect_impl(self, options: Optional[Mapping[str, Any]] = None) -> dict:
        """Collect VMD exporter input from the current Maya scene.

        Args:
            options: Optional mapping. Supported keys are ``target_model`` /
                ``model_root``, ``joints``, ``blend_shapes``, ``cameras``,
                ``lights``, ``start_frame`` / ``end_frame`` or ``frame_range``,
                ``vmd_mode``, ``model_name``, ``motion_scale``, and
                ``bone_bind_poses``. Automatic joint and blendShape discovery
                is scoped to the selected model root; camera/light discovery
                remains scene-level. Explicit node lists remain authoritative.
        """
        options = options or {}
        self._mode_c_physics_output_excluded_targets = set()
        self._diagnostics["track_selection"] = _new_track_selection()
        planning_started = time.perf_counter()
        target_model = options.get("target_model") or options.get("model_root")
        joints = list(options.get("joints") or self._find_joints(target_model))
        blend_shapes = list(
            options.get("blend_shapes") or self._find_blend_shapes(target_model)
        )
        # Current Model scopes model tracks only. Cameras and lights are
        # scene-level tracks unless the caller provides an explicit list.
        cameras = self._resolve_tagged_track(options, "cameras", ATTR_MMD_CAMERA, None)
        lights = self._resolve_tagged_track(options, "lights", ATTR_MMD_LIGHT, None)
        start_frame, end_frame = _resolve_collection_frame_range(options)
        motion_scale = float(options.get("motion_scale", 1.0) or 1.0)
        bone_bind_poses = options.get("bone_bind_poses") or {}
        maya_time_to_vmd = _scene_maya_time_to_vmd_frame()
        mode = str(options.get("vmd_mode", options.get("mode", "")) or "").upper()
        preserve_raw_bone_transforms = bool(
            options.get("preserve_raw_bone_transforms", False)
        )
        dense_control_rig_export = self._control_rig_dense_export(target_model)
        dense_mode_c_export = mode == "C"
        rotation_interpolation = self._rotation_time_curve_interpolation(target_model)
        raw_provenance = _read_vmd_import_provenance(target_model)
        if mode != "C" or preserve_raw_bone_transforms:
            for bone_name, values in _raw_vmd_rotation_interpolation(raw_provenance).items():
                rotation_interpolation.setdefault(bone_name, {}).update(values)
        raw_bone_transforms = _raw_vmd_bone_transforms(raw_provenance)
        preserve_sparse_mode_c = bool(
            dense_mode_c_export
            and preserve_raw_bone_transforms
            and raw_provenance
            and raw_bone_transforms
            and raw_provenance.get("raw_bone_interpolation_complete")
            and raw_provenance.get("raw_bone_transform_complete")
        )
        dense_mode_c_export = dense_mode_c_export and not preserve_sparse_mode_c
        authored_routes = self._scene_authored_input_routes(
            joints,
            target_model,
            standard_mode_c=dense_mode_c_export,
        )
        mode_c_dense_frames = (
            self._mode_c_dense_frame_samples(
                joints,
                blend_shapes,
                cameras,
                lights,
                target_model,
                authored_routes,
                start_frame,
                end_frame,
            )
            if dense_mode_c_export
            else None
        )

        self._diagnostics["route_provenance_dense_planning"] = {
            "wall_sec": round(time.perf_counter() - planning_started, 6),
            "joint_count": len(joints),
            "blend_shape_count": len(blend_shapes),
            "camera_count": len(cameras),
            "light_count": len(lights),
            "authored_route_count": len(authored_routes),
            "raw_provenance": bool(raw_provenance),
            "dense_frame_count": len(mode_c_dense_frames or ()),
        }

        bone_started = time.perf_counter()
        bone_frames = self.collect_bone_frames(
            joints,
            start_frame,
            end_frame,
            motion_scale=motion_scale,
            bone_bind_poses=bone_bind_poses,
            input_routes=authored_routes,
            dense_sample=dense_control_rig_export or dense_mode_c_export,
            force_dense_sample=dense_mode_c_export,
            time_converter=maya_time_to_vmd,
            rotation_interpolation=rotation_interpolation,
            dense_frame_samples=mode_c_dense_frames,
            preserve_raw_bone_transforms=preserve_raw_bone_transforms,
            raw_bone_transforms=raw_bone_transforms,
            bone_channel_sampler=self._bone_channel_sampler,
        )
        self._diagnostics["bone_collection"] = {
            "wall_sec": round(time.perf_counter() - bone_started, 6),
            "joint_count": len(joints),
            "frame_count": len(bone_frames),
            "estimated_scalar_bone_reads": len(bone_frames) * 6,
        }

        morph_started = time.perf_counter()
        morph_frames = self.collect_morph_frames(
            blend_shapes,
            start_frame,
            end_frame,
            time_converter=maya_time_to_vmd,
            target_model=target_model,
            dense_sample=dense_mode_c_export,
            dense_frame_samples=mode_c_dense_frames,
            timeline_evaluation=mode == "C",
        )
        self._diagnostics["morph_collection"] = {
            "wall_sec": round(time.perf_counter() - morph_started, 6),
            "frame_count": len(morph_frames),
        }

        camera_started = time.perf_counter()
        camera_frames = self.collect_camera_frames(
            cameras,
            start_frame,
            end_frame,
            time_converter=maya_time_to_vmd,
            dense_sample=dense_mode_c_export,
            dense_frame_samples=mode_c_dense_frames,
            timeline_evaluation=mode == "C",
        )
        self._diagnostics["camera_collection"] = {
            "wall_sec": round(time.perf_counter() - camera_started, 6),
            "frame_count": len(camera_frames),
        }

        light_started = time.perf_counter()
        light_frames = self.collect_light_frames(
            lights,
            start_frame,
            end_frame,
            time_converter=maya_time_to_vmd,
            dense_sample=dense_mode_c_export,
            dense_frame_samples=mode_c_dense_frames,
            timeline_evaluation=mode == "C",
        )
        self._diagnostics["light_collection"] = {
            "wall_sec": round(time.perf_counter() - light_started, 6),
            "frame_count": len(light_frames),
        }

        ik_started = time.perf_counter()
        ik_frames = self.collect_ik_show_hide_frames(
            target_model,
            start_frame,
            end_frame,
            time_converter=maya_time_to_vmd,
            # IK show/hide is a step track, not a numeric pose track.
            # Keep keyed/baseline semantics even when Mode C bakes the
            # other tracks at every frame.
            dense_sample=False,
            dense_frame_samples=None,
            timeline_evaluation=mode == "C",
        )
        self._diagnostics["ik_collection"] = {
            "wall_sec": round(time.perf_counter() - ik_started, 6),
            "frame_count": len(ik_frames),
        }
        self._diagnostics["section_counts"] = {
            "bone_frames": len(bone_frames),
            "morph_frames": len(morph_frames),
            "camera_frames": len(camera_frames),
            "light_frames": len(light_frames),
            "ik_show_hide_frames": len(ik_frames),
        }

        return {
            "model_name": str(options.get("model_name") or self._model_name(target_model)),
            "raw_provenance": raw_provenance,
            "bone_frames": bone_frames,
            "morph_frames": morph_frames,
            "camera_frames": camera_frames,
            "light_frames": light_frames,
            "ik_show_hide_frames": ik_frames,
        }

    def _mode_c_dense_frame_samples(
        self,
        joints: Sequence[str],
        blend_shapes: Sequence[str],
        cameras: Sequence[str],
        lights: Sequence[str],
        target_model: Optional[str],
        input_routes: Mapping[str, Mapping[str, tuple[str, str]]],
        start_frame: Optional[float],
        end_frame: Optional[float],
    ) -> Optional[list[int]]:
        """Build one Maya-time sample range shared by Mode C tracks."""
        keyed_times = []
        for joint in joints:
            long_name = (cmds.ls(joint, long=True) or [joint])[0]
            keyed_times.extend(
                _routed_key_times(joint, input_routes.get(str(long_name), {}))
            )
        for blend_shape in blend_shapes:
            morph_names = self._blendshape_morph_names(blend_shape)
            keyed_times.extend(
                _key_times(
                    blend_shape,
                    [f"weight[{index}]" for index in morph_names],
                )
            )
        if target_model:
            controller = _morph_controller_for_model(target_model)
            if controller:
                entries = iter_morph_network_metadata(root_group=target_model)
                attrs = {
                    f"inputWeight[{int(entry.index)}]"
                    for entry in entries
                    if entry.index is not None
                }
                keyed_times.extend(_key_times(controller, attrs))
        for camera in cameras:
            camera_root = _camera_root_node(camera)
            camera_target = _camera_target_node(camera)
            camera_shape = _camera_shape(camera)
            keyed_times.extend(_key_times(camera, _CAMERA_EXPORT_ATTRS))
            if camera_root:
                keyed_times.extend(_key_times(camera_root, _BONE_EXPORT_ATTRS))
            if camera_target:
                keyed_times.extend(_key_times(camera_target, _TRANSFORM_EXPORT_ATTRS))
            if camera_shape:
                keyed_times.extend(_key_times(camera_shape, _CAMERA_SHAPE_EXPORT_ATTRS))
        for light in lights:
            keyed_times.extend(_key_times(light, _LIGHT_COLOR_ATTRS + _LIGHT_ROTATE_ATTRS))
        return _dense_frame_samples(keyed_times, start_frame, end_frame)

    def collect_bone_frames(
        self,
        joints: Sequence[str],
        start_frame: Optional[float] = None,
        end_frame: Optional[float] = None,
        motion_scale: float = 1.0,
        bone_bind_poses: Optional[Mapping[str, Sequence[float]]] = None,
        input_routes: Optional[Mapping[str, Mapping[str, tuple[str, str]]]] = None,
        dense_sample: bool = False,
        time_converter=None,
        rotation_interpolation: Optional[Mapping[str, Mapping[int, bytes]]] = None,
        force_dense_sample: bool = False,
        dense_frame_samples: Optional[Sequence[float]] = None,
        preserve_raw_bone_transforms: bool = False,
        raw_bone_transforms: Optional[
            Mapping[tuple[str, int], tuple[tuple[float, ...], tuple[float, ...]]]
        ] = None,
        bone_channel_sampler=None,
    ) -> list[dict]:
        """Collect keyed or one-frame-sampled local joint transforms.

        ``dense_sample`` is retained for the baked control-rig route, where a
        rotation-time curve may intentionally keep sparse VMD keys.  Mode C
        uses ``force_dense_sample`` so its numeric pose export is not
        accidentally changed back to sparse collection by raw interpolation
        metadata.  ``preserve_raw_bone_transforms`` is an explicit import
        roundtrip route for callers that have established that raw VMD
        provenance is authoritative; it does not change the default edited
        scene behavior.
        """
        bone_bind_poses = bone_bind_poses or {}
        input_routes = input_routes or {}
        time_converter = time_converter or _scene_maya_time_to_vmd_frame()
        rotation_context = _build_rotation_export_context(joints)
        rotation_interpolation = rotation_interpolation or {}
        raw_bone_transforms = raw_bone_transforms or {}
        raw_bone_frames_by_name = _index_raw_bone_transform_frames(raw_bone_transforms)
        native_samples = None
        frames = []
        dense_frames = (
            list(dense_frame_samples)
            if dense_frame_samples is not None
            else None
        )
        keyed_times_by_joint = {}
        single_key_joints = set()
        single_key_kinds: dict[str, str] = {}
        static_keyless_joints = set()
        keyless_dependency_joints: dict[str, str] = {}
        direct_multi_key_candidates: dict[str, list[tuple[str, int]]] = {}
        bone_output_providers: dict[str, set[str]] = {}
        bone_dense_diagnostic_rows: dict[str, tuple[str, str, int, int]] = {}
        if dense_sample:
            all_keyed = []
            for joint in joints:
                long_names = cmds.ls(joint, long=True) or [joint]
                route = input_routes.get(str(long_names[0]), {})
                joint_keyed = _routed_key_times(joint, route)
                keyed_times_by_joint[joint] = joint_keyed
                all_keyed.extend(joint_keyed)
            if dense_frames is None:
                if force_dense_sample and start_frame is not None and end_frame is not None:
                    dense_frames = list(
                        range(int(math.ceil(start_frame)), int(math.floor(end_frame)) + 1)
                    )
                else:
                    ranged = _filter_frame_range(all_keyed, start_frame, end_frame)
                    if ranged:
                        dense_frames = list(
                            range(int(math.floor(min(ranged))), int(math.ceil(max(ranged))) + 1)
                        )
            if force_dense_sample:
                for joint in joints:
                    long_name = str((cmds.ls(joint, long=True) or [joint])[0])
                    source_frames = _filter_frame_range(
                        keyed_times_by_joint.get(joint, ()), start_frame, end_frame
                    )
                    route = input_routes.get(long_name, {})
                    all_source_frames = keyed_times_by_joint.get(joint, ())
                    physics_excluded = (
                        long_name in self._mode_c_physics_output_excluded_targets
                    )
                    if physics_excluded:
                        # Never fall through to the visible, post-physics
                        # joint when its authored/pre-physics route is
                        # incomplete.
                        continue
                    bone_output_providers.setdefault(
                        self._mmd_bone_name(joint), set()
                    ).add(long_name)
                    if not route and not all_source_frames:
                        incoming_state = _incoming_connection_state(
                            long_name,
                            _BONE_EXPORT_ATTRS,
                            strict=True,
                        )
                        if incoming_state == "some":
                            if dense_frames:
                                keyless_dependency_joints[joint] = (
                                    "keyless_incoming_dependency"
                                )
                            continue
                        # A direct keyless joint has no dependency and keeps
                        # the existing omit/one-key behavior.
                        if (
                            len(all_source_frames) == 0
                            and len(source_frames) == 0
                            and dense_frames
                            and _is_direct_authored_track(
                                long_name, _BONE_EXPORT_ATTRS
                            )
                        ):
                            static_keyless_joints.add(joint)
                            single_key_joints.add(joint)
                        continue
                    if (
                        route
                        and len(all_source_frames) == 0
                        and dense_frames
                    ):
                        keyless_dependency_joints[joint] = (
                            "keyless_routed_dependency"
                        )
                    if (
                        not route
                        and len(source_frames) > 1
                        and _is_direct_authored_track(long_name, _BONE_EXPORT_ATTRS)
                    ):
                        direct_multi_key_candidates.setdefault(
                            self._mmd_bone_name(joint), []
                        ).append((long_name, len(source_frames)))
                    if (
                        len(all_source_frames) == 1
                        and len(source_frames) == 1
                    ):
                        single_kind = _mode_c_single_key_bone_route(joint, route)
                        if single_kind:
                            single_key_joints.add(joint)
                            single_key_kinds[joint] = single_kind
            if (
                force_dense_sample
                and dense_frames
                and bone_channel_sampler is None
                and any(
                    (
                        keyed_times_by_joint.get(joint)
                        or joint in keyless_dependency_joints
                    )
                    and joint not in single_key_joints
                    and str((cmds.ls(joint, long=True) or [joint])[0])
                    not in self._mode_c_physics_output_excluded_targets
                    for joint in joints
                )
            ):
                self._diagnostics["native_sampler"] = {
                    "available": False,
                    "used": False,
                    "fatal": True,
                    "fallback_reason": "Mode C native sampler was not provided",
                }
                self._emit_diagnostics()
                raise RuntimeError("Mode C native bone sampling is unavailable")
            if (
                force_dense_sample
                and dense_frames
                and bone_channel_sampler is not None
            ):
                # A joint without source keys must not acquire a native track
                # merely because another joint defines the dense range.
                native_joints = [
                    joint
                    for joint in joints
                    if (
                        keyed_times_by_joint.get(joint)
                        or joint in keyless_dependency_joints
                    )
                    and joint not in single_key_joints
                    and str((cmds.ls(joint, long=True) or [joint])[0])
                    not in self._mode_c_physics_output_excluded_targets
                ]
                if not native_joints:
                    self._diagnostics["native_sampler"] = {
                        "available": bool(
                            getattr(bone_channel_sampler, "available", False)
                        ),
                        "used": False,
                        "fallback_reason": "no eligible dense bone channels",
                    }
                else:
                    route_inventory = _native_route_inventory(
                        native_joints,
                        input_routes,
                    )
                    sampler_available = getattr(
                        bone_channel_sampler,
                        "available",
                        None,
                    )
                    if callable(sampler_available):
                        try:
                            sampler_available = sampler_available()
                        except Exception:
                            sampler_available = False
                    self._diagnostics["native_sampler"] = {
                        "status": "preflight",
                        "available": bool(sampler_available),
                        "used": False,
                        "frame_count": len(dense_frames),
                        "logical_channel_count": len(native_joints) * len(_BONE_EXPORT_ATTRS),
                        **route_inventory,
                    }
                    self._emit_diagnostics()
                    set_native_sink = getattr(
                        bone_channel_sampler,
                        "set_diagnostics_sink",
                        None,
                    )
                    if callable(set_native_sink):
                        set_native_sink(self._accept_native_diagnostics)
                    native_started = time.perf_counter()
                    try:
                        if sampler_available is False:
                            raise RuntimeError("native sampler is unavailable")
                        sampler_method = getattr(
                            bone_channel_sampler,
                            "sample_dense_bone_channels",
                            None,
                        )
                        if not callable(sampler_method):
                            sampler_method = getattr(
                                bone_channel_sampler,
                                "sample_dense_bones",
                                None,
                            )
                        if not callable(sampler_method):
                            raise RuntimeError("native sampler has no dense bone method")
                        native_samples = sampler_method(
                            dense_frames,
                            native_joints,
                            input_routes,
                        )
                        if not callable(getattr(native_samples, "value", None)):
                            raise RuntimeError("native sampler returned no value accessor")
                        native_diagnostics = getattr(
                            native_samples,
                            "diagnostics",
                            None,
                        )
                        if callable(native_diagnostics):
                            native_diagnostics = native_diagnostics()
                        sampler_diagnostics = getattr(
                            bone_channel_sampler,
                            "last_diagnostics",
                            None,
                        )
                        native_report = dict(
                            self._diagnostics.get("native_sampler", {})
                        )
                        native_report.update(sampler_diagnostics or {})
                        native_report.update(native_diagnostics or {})
                        self._diagnostics["native_sampler"] = native_report
                        self._diagnostics["native_sampler"].setdefault(
                            "available", True
                        )
                        self._diagnostics["native_sampler"].setdefault("used", True)
                    except BaseException as exc:
                        if native_samples is not None:
                            try:
                                _close_native_samples(native_samples)
                            except Exception:
                                pass
                        native_samples = None
                        sampler_diagnostics = getattr(
                            bone_channel_sampler,
                            "last_diagnostics",
                            None,
                        )
                        native_report = dict(
                            self._diagnostics.get("native_sampler", {})
                        )
                        native_report.update(sampler_diagnostics or {})
                        self._diagnostics["native_sampler"] = native_report
                        self._diagnostics["native_sampler"].update(
                            {
                                "available": bool(
                                    self._diagnostics["native_sampler"].get(
                                        "available", True
                                    )
                                ),
                                "used": False,
                                "fallback_reason": f"{type(exc).__name__}: {exc}",
                                "fatal": True,
                                "fallback_wall_sec": round(
                                    time.perf_counter() - native_started,
                                    6,
                                ),
                            }
                        )
                        self._emit_diagnostics()
                        if not isinstance(exc, Exception):
                            raise
                        raise RuntimeError(
                            f"Mode C native bone sampling failed: {exc}"
                        ) from exc
            elif bone_channel_sampler is not None:
                available = getattr(bone_channel_sampler, "available", False)
                if callable(available):
                    available = available()
                self._diagnostics["native_sampler"] = {
                    "available": bool(available),
                    "used": False,
                    "fallback_reason": "no eligible dense bone channels",
                }

        try:
            static_sample = (
                _mode_c_earliest_integer_sample(
                    dense_frames,
                    start_frame,
                    end_frame,
                )
                if force_dense_sample
                else None
            )
            if static_sample is not None:
                for joint in joints:
                    long_name = str((cmds.ls(joint, long=True) or [joint])[0])
                    route = input_routes.get(long_name, {})
                    all_joint_keyed = keyed_times_by_joint.get(joint)
                    if all_joint_keyed is None:
                        all_joint_keyed = _routed_key_times(joint, route)
                        keyed_times_by_joint[joint] = all_joint_keyed
                    if (
                        not route
                        and not all_joint_keyed
                        and long_name
                        not in self._mode_c_physics_output_excluded_targets
                        and _incoming_connection_state(long_name, _BONE_EXPORT_ATTRS)
                        == "none"
                    ):
                        static_keyless_joints.add(joint)
                        single_key_joints.add(joint)
                        keyed_times_by_joint[joint] = [static_sample]
        except BaseException:
            if native_samples is not None:
                _close_native_samples(native_samples)
                native_samples = None
            raise

        def read_value(joint, attr, frame_number, route, use_native=True):
            nonlocal native_samples
            if use_native and native_samples is not None:
                try:
                    return float(native_samples.value(joint, attr, frame_number))
                except Exception as exc:
                    try:
                        _close_native_samples(native_samples)
                    except Exception:
                        pass
                    native_samples = None
                    self._diagnostics.setdefault("native_sampler", {}).update(
                        {
                            "used": False,
                            "fallback_reason": f"{type(exc).__name__}: {exc}",
                            "fatal": True,
                        }
                    )
                    self._emit_diagnostics()
                    raise RuntimeError(
                        f"Mode C native bone value failed for {joint}.{attr}"
                    ) from exc
            return _routed_plug_float(joint, attr, frame_number, route)

        try:
            for joint in joints:
                bone_name = self._mmd_bone_name(joint)
                bind_pose = _resolve_bind_pose(bone_bind_poses, bone_name, joint)
                long_names = cmds.ls(joint, long=True) or [joint]
                long_name = str(long_names[0])
                if long_name in self._mode_c_physics_output_excluded_targets:
                    # The physics solver's final output is intentionally outside
                    # standard Mode C.  An incomplete pre-physics route cannot
                    # safely represent any unclaimed channels.
                    continue
                route = input_routes.get(long_name, {})
                all_joint_keyed = keyed_times_by_joint.get(joint)
                if all_joint_keyed is None:
                    all_joint_keyed = _routed_key_times(joint, route)
                sparse_frames = _filter_frame_range(
                    all_joint_keyed,
                    start_frame,
                    end_frame,
                )
                single_key = joint in single_key_joints
                static_keyless = joint in static_keyless_joints
                direct_multi_key = (
                    len(direct_multi_key_candidates.get(bone_name, ())) == 1
                    and len(bone_output_providers.get(bone_name, ())) == 1
                    and direct_multi_key_candidates[bone_name][0][0] == long_name
                )
                raw_provenance_frames = raw_bone_frames_by_name.get(bone_name, set())
                has_new_authored_key = bool(
                    raw_provenance_frames
                    and set(sparse_frames).difference(raw_provenance_frames)
                )
                preserve_sparse_rotation = (
                    not force_dense_sample
                    and bone_name in rotation_interpolation
                    and not has_new_authored_key
                )
                keyed_frames = (
                    sparse_frames
                    if single_key
                    else dense_frames
                    if dense_frames is not None
                    and (all_joint_keyed or joint in keyless_dependency_joints)
                    and not preserve_sparse_rotation
                    else sparse_frames
                )
                for frame_number in keyed_frames:
                    rotation = _maya_joint_rotate_to_vmd_quaternion(
                        joint,
                        read_value(joint, "rotateX", frame_number, route, not single_key),
                        read_value(joint, "rotateY", frame_number, route, not single_key),
                        read_value(joint, "rotateZ", frame_number, route, not single_key),
                        rotation_context.get(str(long_names[0])),
                    )
                    vmd_frame = _vmd_frame_number(frame_number, time_converter)
                    payload = {
                            "bone_name": bone_name,
                            "frame_number": vmd_frame,
                            "position": _maya_translate_to_vmd_position(
                                (
                                    read_value(joint, "translateX", frame_number, route, not single_key),
                                    read_value(joint, "translateY", frame_number, route, not single_key),
                                    read_value(joint, "translateZ", frame_number, route, not single_key),
                                ),
                                bind_pose,
                                motion_scale,
                            ),
                            "rotation": rotation,
                        }
                    interpolation = rotation_interpolation.get(bone_name, {}).get(vmd_frame)
                    if interpolation is not None:
                        payload["interpolation"] = interpolation
                    raw_transform = (
                        None
                        if force_dense_sample
                        else raw_bone_transforms.get((bone_name, vmd_frame))
                    )
                    if raw_transform is not None and (
                        preserve_raw_bone_transforms
                        or _raw_bone_transform_matches(
                            payload["position"],
                            payload["rotation"],
                            raw_transform,
                        )
                    ):
                        payload["position"], payload["rotation"] = raw_transform
                    if single_key:
                        is_default = payload["position"] == (0.0, 0.0, 0.0) and payload[
                            "rotation"
                        ] == (0.0, 0.0, 0.0, 1.0)
                        self._record_track_selection(
                            "bone",
                            bone_name,
                            "omitted_default" if is_default else "constant_one_key",
                            (
                                "keyless_static_default"
                                if static_keyless
                                else (
                                    (
                                        "layered_direct_single_key_default"
                                        if single_key_kinds.get(joint) == "layered"
                                        else "routed_direct_single_key_default"
                                    )
                                    if route
                                    else "direct_single_key_default"
                                )
                            )
                            if is_default
                            else (
                                "keyless_static_non_default"
                                if static_keyless
                                else (
                                    (
                                        "layered_direct_single_key_non_default"
                                        if single_key_kinds.get(joint) == "layered"
                                        else "routed_direct_single_key_non_default"
                                    )
                                    if route
                                    else "direct_single_key_non_default"
                                )
                            ),
                            0 if static_keyless else len(sparse_frames),
                            0 if is_default else 1,
                        )
                        if is_default:
                            continue
                    frames.append(payload)
                if force_dense_sample and not single_key:
                    if direct_multi_key:
                        continue
                    keyless_reason = keyless_dependency_joints.get(joint)
                    if keyless_reason:
                        decision = "dependency_baked"
                        reason = keyless_reason
                        source_key_count = 0
                    elif sparse_frames:
                        decision = "dependency_baked" if route else "authored_sampled"
                        reason = "routed_dependency" if route else (
                            "multiple_source_keys"
                            if len(sparse_frames) > 1
                            else "conservative_dense_path"
                        )
                        source_key_count = len(sparse_frames)
                    else:
                        decision = None
                        reason = ""
                        source_key_count = 0
                    if decision is None:
                        continue
                    planned_key_count = (
                        len(dense_frames or ()) if dense_sample else len(sparse_frames)
                    )
                    current = bone_dense_diagnostic_rows.get(bone_name)
                    if current is None or (
                        decision == "dependency_baked" and current[0] != "dependency_baked"
                    ):
                        bone_dense_diagnostic_rows[bone_name] = (
                            decision,
                            reason,
                            source_key_count,
                            planned_key_count,
                        )
        finally:
            if native_samples is not None:
                try:
                    _close_native_samples(native_samples)
                finally:
                    native_samples = None
        for name, (decision, reason, source_key_count, planned_key_count) in (
            bone_dense_diagnostic_rows.items()
        ):
            self._record_track_selection(
                "bone",
                name,
                decision,
                reason,
                source_key_count,
                planned_key_count,
            )
        bone_collapse_candidates = {
            name: [
                (
                    provider,
                    next(
                        (
                            count
                            for candidate_provider, count in provider_rows
                            if candidate_provider == provider
                        ),
                        0,
                    ),
                )
                for provider in sorted(bone_output_providers.get(name, ()))
            ]
            for name, provider_rows in direct_multi_key_candidates.items()
        }
        bone_frames, selection_evidence = _collapse_exact_constant_direct_tracks(
            frames,
            "bone_name",
            ("position", "rotation"),
            bone_collapse_candidates,
            ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)),
            len(dense_frames or ()) if dense_sample else 0,
        )
        for name, decision, reason, source_key_count, planned_key_count in selection_evidence:
            self._record_track_selection(
                "bone",
                name,
                decision,
                reason,
                source_key_count,
                planned_key_count,
            )
        return bone_frames

    def collect_ik_show_hide_frames(
        self,
        target_model: Optional[str],
        start_frame: Optional[float] = None,
        end_frame: Optional[float] = None,
        time_converter=None,
        dense_sample: bool = False,
        dense_frame_samples: Optional[Sequence[float]] = None,
        timeline_evaluation: bool = False,
    ) -> list[dict]:
        """Collect keyed owned ``mmdCcdIk.enabled`` values as VMD properties."""
        if not target_model:
            return []
        time_converter = time_converter or _scene_maya_time_to_vmd_frame()
        nodes_by_name = collect_ik_nodes_by_bone_name(target_model=target_model)
        all_keyed_frames = sorted(
            {
                frame
                for node in nodes_by_name.values()
                for frame in _key_times(node, ("enabled",))
            }
        )
        keyed_frames = (
            sorted(set(dense_frame_samples))
            if dense_sample
            and dense_frame_samples is not None
            and nodes_by_name
            else _filter_frame_range(
                all_keyed_frames,
                start_frame,
                end_frame,
            )
        )
        if dense_sample and dense_frame_samples and nodes_by_name and not all_keyed_frames:
            first_sample = float(dense_frame_samples[0])
            if timeline_evaluation:
                with _MayaTimelineReader() as initial_reader:
                    initial_reader.set_frame(first_sample)
                    all_enabled = all(
                        bool(_current_plug_float(node, "enabled"))
                        for node in nodes_by_name.values()
                    )
            else:
                all_enabled = all(
                    bool(_plug_float(node, "enabled", first_sample))
                    for node in nodes_by_name.values()
                )
            if all_enabled:
                # A keyless production rig defaults to enabled=True.  Dense
                # sampling must not manufacture a redundant all-ON property
                # section that was absent from the source motion.
                return []
        frames = []
        timeline_reader = _MayaTimelineReader() if timeline_evaluation else None

        def read_enabled(node: str, frame: float) -> bool:
            if timeline_reader is not None:
                timeline_reader.set_frame(frame)
                return bool(_current_plug_float(node, "enabled"))
            return bool(_plug_float(node, "enabled", frame))

        baseline_time = _ik_baseline_time(start_frame, end_frame)
        context = timeline_reader or nullcontext()
        with context:
            if (
                not dense_sample
                and nodes_by_name
                and baseline_time is not None
                and baseline_time not in all_keyed_frames
            ):
                baseline_frame = _vmd_frame_number(baseline_time, time_converter)
                if baseline_frame >= 0:
                    baseline_states = [
                        (name, read_enabled(node, baseline_time))
                        for name, node in sorted(nodes_by_name.items())
                    ]
                    # A keyless production rig has enabled=True as its default.
                    # Omitting that redundant ON section keeps the exported VMD
                    # faithful to a source with no IK show/hide property frames.
                    # Keep the baseline when a solver is OFF or any later key
                    # exists; those states need an explicit VMD representation.
                    if all_keyed_frames or any(not state for _, state in baseline_states):
                        frames.append(
                            {
                                "frame_number": baseline_frame,
                                "visible": True,
                                "ik_states": baseline_states,
                            }
                        )
            for frame in keyed_frames:
                vmd_frame = _vmd_frame_number(frame, time_converter)
                if vmd_frame < 0:
                    continue
                frames.append(
                    {
                        "frame_number": vmd_frame,
                        "visible": True,
                        "ik_states": [
                            (name, read_enabled(node, frame))
                            for name, node in sorted(nodes_by_name.items())
                        ],
                    }
                )
        return _deduplicate_frames(frames, ("frame_number",))

    def _control_rig_dense_export(
        self,
        target_model: Optional[str],
    ) -> bool:
        """Reject EDIT and request dense sampling for an attached control rig."""
        if not target_model:
            return False
        metadata = read_mmd_control_rig_metadata(target_model)
        if not metadata:
            return False
        if metadata["state"] == CONTROL_RIG_EDIT:
            raise ValueError("Bake the MMD control rig before VMD export")
        return True

    @staticmethod
    def _rotation_time_curve_interpolation(
        target_model: Optional[str],
    ) -> dict[str, dict[int, bytes]]:
        """Resolve Experimental rotation interpolation for sparse export."""
        if not target_model:
            return {}
        metadata = read_mmd_control_rig_metadata(target_model)
        if not metadata:
            return {}
        from mmd_tools.converters.vmd_rotation_time_curve import (
            rotation_time_curve_interpolation_by_bone,
        )

        return rotation_time_curve_interpolation_by_bone(metadata)

    def _scene_authored_input_routes(
        self,
        joints: Sequence[str],
        target_model: Optional[str] = None,
        *,
        standard_mode_c: bool = False,
    ) -> dict[str, dict[str, tuple[str, str]]]:
        """Resolve authored channels that bypass the visible joint transform.

        Control-rig EDIT rewires ``bone_morph_base`` and ``ik_controller``
        bindings through an ``mmdBoneMorphAccum`` node.  Once the rig is
        baked, the joint itself has no keys, so the VMD collector must sample
        the accumulator's base channels instead.  UUID-backed metadata is
        authoritative; malformed or stale rows are skipped so unrelated
        joints remain exportable.
        """
        routes = {}
        append_info = collect_append_info()
        ik_info = collect_mmd_ik_passthrough_info()
        for joint_name in joints:
            long_names = cmds.ls(joint_name, long=True) or [joint_name]
            joint = str(long_names[0])
            append = append_info.get(joint)
            if append:
                node = str(append["node"])
                for source_attr, target_attr in append.get("attr_map", {}).items():
                    routes.setdefault(joint, {})[source_attr] = (node, target_attr)
            ik = ik_info.get(joint)
            if ik:
                node = str(ik["node"])
                slot = int(ik["input_slot"])
                for axis in "XYZ":
                    routes.setdefault(joint, {})[f"rotate{axis}"] = (
                        node,
                        f"inputRotate[{slot}].inputRotateElement{axis}",
                    )

        # The owned accumulator base is the authored layer before append/IK;
        # it therefore replaces their passthrough routes. Control-rig EDIT
        # metadata below remains the highest-priority authoring contract.
        accumulator_resolution = resolve_owned_bone_morph_base_routes(joints)
        if accumulator_resolution.blocked:
            details = "; ".join(
                f"{joint}: channels={channels!r}, reason={reason}"
                for joint, (channels, reason) in sorted(
                    accumulator_resolution.blocked.items()
                )
            )
            raise ValueError(
                "VMD collection blocked by unresolved bone-morph accumulator ownership: "
                + details
            )
        for joint, route in accumulator_resolution.routes.items():
            routes.setdefault(joint, {}).update(route)
        if not target_model:
            self._merge_redirected_authoring_proxy_routes(joints, routes)
            return routes
        metadata = read_mmd_control_rig_metadata(target_model)
        if not metadata:
            self._merge_physics_authored_input_routes(
                joints=joints,
                target_model=target_model,
                routes=routes,
                standard_mode_c=standard_mode_c,
            )
            self._merge_redirected_authoring_proxy_routes(joints, routes)
            return routes
        joints_by_path = {
            str((cmds.ls(joint, long=True) or [joint])[0]): str(joint)
            for joint in joints
        }
        for binding in (metadata.get("bindings", {}) or {}).values():
            if not isinstance(binding, Mapping) or binding.get("inputKind") not in {
                INPUT_BONE_MORPH_BASE,
                INPUT_IK_CONTROLLER,
            }:
                continue
            try:
                joint = str(resolve_mmd_control_rig_binding_joint(cmds, binding))
            except Exception:
                joint = str(binding.get("joint") or "")
            joint = str((cmds.ls(joint, long=True) or [joint])[0]) if joint else ""
            if joint not in joints_by_path:
                continue
            try:
                authored_plugs = resolve_mmd_control_rig_binding_authored_plugs(cmds, binding)
            except Exception:
                authored_plugs = tuple(str(plug) for plug in (binding.get("authoredPlugs") or ()))
            route = routes.setdefault(joint, {})
            for authored in authored_plugs:
                node, separator, attribute = str(authored).rpartition(".")
                if not separator or attribute not in {"baseTranslate", "baseRotate"}:
                    continue
                channel = "translate" if attribute == "baseTranslate" else "rotate"
                for axis in "XYZ":
                    route[f"{channel}{axis}"] = (node, f"{attribute}{axis}")
        self._merge_physics_authored_input_routes(
            joints=joints,
            target_model=target_model,
            routes=routes,
            standard_mode_c=standard_mode_c,
        )
        self._merge_redirected_authoring_proxy_routes(joints, routes)
        return routes

    def _merge_redirected_authoring_proxy_routes(
        self,
        joints: Sequence[str],
        routes: dict[str, dict[str, tuple[str, str]]],
    ) -> None:
        """Validate current logical destinations before selecting proxy tracks."""
        for joint_name in joints:
            joint = str((cmds.ls(joint_name, long=True) or [joint_name])[0])
            route_key = str(joint_name) if str(joint_name) in routes else joint
            proxy_route, authority, claimed = (
                resolve_redirected_authoring_proxy_authority(joint)
            )
            if not claimed:
                continue
            current = {
                channel: routes.get(route_key, {}).get(channel, (joint, channel))
                for channel in authority
            }
            if not proxy_route or not redirected_authority_matches(current, authority):
                raise ValueError(
                    "VMD collection blocked by stale redirected authoring proxy "
                    f"authority: {joint}; current={current!r}; authority={authority!r}"
                )
            routes.setdefault(route_key, {}).update(proxy_route)

    def _merge_physics_authored_input_routes(
        self,
        *,
        joints: Sequence[str],
        target_model: str,
        routes: dict[str, dict[str, tuple[str, str]]],
        standard_mode_c: bool = False,
    ) -> None:
        """Add owned physics-driver pre-inputs without replacing authored routes.

        The final ``outTranslate``/``outRotate`` values are physics results and
        are not motion sources.  VMD recovery connects authored animation to
        the driver's ``inPre*`` plugs, so only a unique, model-owned driver
        with a validated target and an incoming non-physics source is eligible.
        Missing or ambiguous graph pieces are skipped fail-closed for legacy
        callers.  Standard Mode C uses the same graph boundary but raises on
        ownership ambiguity so a physics final output cannot be exported by
        guessing.
        """

        if not target_model:
            return
        root_path = _canonical_dag_path(target_model)
        if not root_path:
            return
        joints_by_path = {
            str((cmds.ls(joint, long=True) or [joint])[0]): str(joint)
            for joint in joints
        }
        if not joints_by_path:
            return
        candidates: dict[str, list[tuple[str, int, bool]]] = {}
        used_indices: dict[int, list[str]] = {}
        scene_solvers, drivers_by_solver, driver_owners = (
            _physics_solver_driver_inventory()
        )
        owned_solvers = _physics_solvers_owned_by_model(
            root_path,
            strict=standard_mode_c,
            solvers=scene_solvers,
        )
        owned_drivers = []
        selected_driver_owners: dict[str, set[str]] = {}
        seen_drivers = set()
        for solver in owned_solvers:
            for driver in drivers_by_solver.get(solver, ()):
                selected_driver_owners.setdefault(driver, set()).add(solver)
                if driver not in seen_drivers:
                    seen_drivers.add(driver)
                    owned_drivers.append(driver)

        for driver in sorted(owned_drivers):
            if standard_mode_c:
                scene_owners = driver_owners.get(driver, ())
                selected_owners = sorted(selected_driver_owners.get(driver, ()))
                if scene_owners != selected_owners or len(scene_owners) != 1:
                    raise ValueError(
                        "Mode C physics driver must belong to exactly one "
                        f"selected solver; driver={driver}, "
                        f"solvers={scene_owners}"
                    )
            target_connections = _physics_driver_target_connections(driver)
            if standard_mode_c and len(target_connections) != 1:
                raise ValueError(
                    "Mode C physics ownership requires exactly one target "
                    f"connection for {driver}; found {len(target_connections)}"
                )
            if len(target_connections) != 1:
                continue
            target_joint = target_connections[0]
            target_path = _canonical_dag_path(target_joint)
            if standard_mode_c and (
                not target_path
                or not _dag_path_is_under_root(target_path, root_path)
            ):
                raise ValueError(
                    "Mode C physics ownership target is outside the selected "
                    f"model: {driver} -> {target_joint}"
                )
            if not target_path or target_path not in joints_by_path:
                continue
            if not _dag_path_is_under_root(target_path, root_path):
                continue
            if standard_mode_c:
                bone_index = _physics_driver_bone_index(
                    driver,
                    strict=True,
                )
                if bone_index is None:
                    raise ValueError(
                        "Mode C physics ownership requires a valid non-negative "
                        f"bone index for {driver}"
                    )
            else:
                bone_index = _physics_driver_bone_index(driver)
                if bone_index is None:
                    continue
            pre_inputs_exist = _physics_driver_pre_inputs_exist(driver)
            if not standard_mode_c and not pre_inputs_exist:
                continue
            candidates.setdefault(target_path, []).append(
                (driver, bone_index, pre_inputs_exist)
            )
            used_indices.setdefault(bone_index, []).append(target_path)

        # A duplicate target or bone index cannot establish ownership safely.
        ambiguous_targets = {
            target
            for target, values in candidates.items()
            if len({driver for driver, _index, _pre_inputs in values}) != 1
        }
        ambiguous_indices = {
            index
            for index, targets in used_indices.items()
            if len(set(targets)) != 1
        }
        if standard_mode_c and ambiguous_targets:
            target = sorted(ambiguous_targets)[0]
            drivers = sorted(
                driver
                for driver, _index, _pre_inputs in candidates[target]
            )
            raise ValueError(
                "Mode C physics ownership has duplicate drivers for target "
                f"{target}: {drivers}"
            )
        if standard_mode_c and ambiguous_indices:
            index = sorted(ambiguous_indices)[0]
            targets = sorted(set(used_indices[index]))
            raise ValueError(
                "Mode C physics ownership has duplicate bone index "
                f"{index} across targets: {targets}"
            )
        for target_path, values in candidates.items():
            if target_path in ambiguous_targets:
                continue
            driver, bone_index, pre_inputs_exist = values[0]
            if bone_index in ambiguous_indices:
                continue
            if standard_mode_c:
                existing_route = dict(routes.get(target_path, {}))
                completed_route = dict(existing_route)
                missing_channels = []
                # Resolve each logical channel independently.  Existing
                # authoring routes keep priority; a unique authored source on
                # the visible joint is the next safest source, followed by a
                # static or animated driver pre-input when that exact plug
                # exists.  No incoming animation is required for pre-inputs.
                for logical_attr, pre_attr in _PHYSICS_PRE_INPUT_ATTRS.items():
                    if logical_attr in completed_route:
                        continue
                    authored_source = _unique_nonphysics_source(
                        f"{target_path}.{logical_attr}"
                    )
                    if authored_source:
                        completed_route[logical_attr] = authored_source
                        continue
                    if _physics_driver_pre_input_exists(driver, pre_attr):
                        completed_route[logical_attr] = (driver, pre_attr)
                        continue
                    missing_channels.append(logical_attr)
                if missing_channels:
                    # Do not leave a partial route that could be mistaken for
                    # a safe source by a later collector pass.
                    routes.pop(target_path, None)
                    self._mode_c_physics_output_excluded_targets.add(target_path)
                    self._record_track_selection(
                        "bone",
                        self._mmd_bone_name(joints_by_path[target_path]),
                        "physics_output_excluded",
                        "incomplete_pre_physics_route",
                        0,
                        0,
                    )
                    continue
                routes[target_path] = completed_route
                self._record_track_selection(
                    "bone",
                    self._mmd_bone_name(joints_by_path[target_path]),
                    "physics_output_excluded",
                    "standard_mode_c_owned_physics_final_output",
                    0,
                    0,
                )
                continue
            route = routes.setdefault(target_path, {})
            for logical_attr, pre_attr in _PHYSICS_PRE_INPUT_ATTRS.items():
                if logical_attr in route:
                    # append/IK/control-rig authored routes remain the
                    # established priority and must never be overwritten.
                    continue
                if pre_inputs_exist and _unique_nonphysics_source(
                    f"{driver}.{pre_attr}"
                ):
                    route[logical_attr] = (driver, pre_attr)
                    continue
                authored_source = _unique_nonphysics_source(
                    f"{target_path}.{logical_attr}"
                )
                if authored_source:
                    route[logical_attr] = authored_source

    def collect_morph_frames(
        self,
        blend_shapes: Sequence[str],
        start_frame: Optional[float] = None,
        end_frame: Optional[float] = None,
        time_converter=None,
        target_model: Optional[str] = None,
        dense_sample: bool = False,
        dense_frame_samples: Optional[Sequence[float]] = None,
        timeline_evaluation: bool = False,
    ) -> list[dict]:
        """Collect keyed blendShape and model-owned network morph frames.

        Vertex morphs are represented by the existing blendShape targets.  The
        PMX controller also owns non-vertex morphs (bone, group, and material),
        whose weights are keyed on ``inputWeight[index]``.  Network metadata is
        used as the authoritative name/index mapping and is only consulted when
        a model root is supplied, so unrelated model controllers cannot leak
        into an export.
        """
        time_converter = time_converter or _scene_maya_time_to_vmd_frame()
        standard_dense_mode = bool(dense_sample and timeline_evaluation)
        if standard_dense_mode and dense_frame_samples is None:
            dense_frame_samples = _dense_frame_samples((), start_frame, end_frame)
        frames = []
        channels = []
        controller_nodes = set()
        controller_channel_morph_types = {}
        keyless_dependency_channels = set()
        static_keyless_channels = set()
        static_sample = (
            _mode_c_earliest_integer_sample(
                dense_frame_samples,
                start_frame,
                end_frame,
            )
            if dense_sample and timeline_evaluation
            else None
        )
        for blend_shape in blend_shapes:
            for weight_index, morph_name in self._blendshape_morph_names(blend_shape).items():
                attr = f"weight[{weight_index}]"
                source_frames = _key_times(blend_shape, (attr,))
                incoming_state = None
                if not source_frames:
                    if standard_dense_mode:
                        incoming_state = _incoming_connection_state(
                            blend_shape,
                            (attr,),
                            strict=True,
                        )
                    elif static_sample is not None:
                        incoming_state = (
                            "none"
                            if _has_no_incoming_connections(blend_shape, (attr,))
                            else "some"
                        )
                keyless_dependency = bool(
                    standard_dense_mode
                    and not source_frames
                    and incoming_state == "some"
                    and dense_frame_samples
                )
                static_keyless = bool(
                    static_sample is not None
                    and not source_frames
                    and incoming_state == "none"
                )
                if static_keyless:
                    static_keyless_channels.add((blend_shape, attr))
                    source_frames = [static_sample]
                if keyless_dependency:
                    keyless_dependency_channels.add((blend_shape, attr))
                if source_frames or keyless_dependency:
                    channels.append((blend_shape, attr, morph_name, source_frames))

        # Model-scoped mmdMorphController keys cover vertex and non-vertex
        # morphs. Vertex rows are normally also represented by blendShape
        # targets, so the final deduplication keeps those explicit rows first.
        # Keep the existing blendShape rows first so a malformed scene that
        # reuses a morph name remains deterministic and does not duplicate a
        # VMD name/frame pair.
        if target_model:
            controller = _morph_controller_for_model(target_model)
            if controller:
                metadata = list(
                    iter_morph_network_metadata(root_group=target_model)
                )
                metadata_by_index = {}
                metadata_by_name = {}
                metadata_by_node = {}
                for entry in metadata:
                    if not entry.name or entry.index is None:
                        continue
                    index = int(entry.index)
                    name = str(entry.name)
                    # Duplicate index/name providers are ambiguous.  Skip all
                    # contenders instead of guessing which network is active.
                    metadata_by_index.setdefault(index, []).append(entry)
                    metadata_by_name.setdefault(name, []).append(entry)
                    provider = str(getattr(entry, "node", "") or "")
                    if provider:
                        metadata_by_node.setdefault(provider, []).append(entry)

                if standard_dense_mode:
                    duplicate_nodes = sorted(
                        node
                        for node, entries in metadata_by_node.items()
                        if len(entries) != 1
                    )
                    if duplicate_nodes:
                        node = duplicate_nodes[0]
                        raise ValueError(
                            "Mode C morph metadata has conflicting provider ownership "
                            f"for {node!r}"
                        )
                    duplicate_indices = sorted(
                        index
                        for index, entries in metadata_by_index.items()
                        if len(entries) != 1
                    )
                    if duplicate_indices:
                        index = duplicate_indices[0]
                        providers = sorted(
                            str(getattr(entry, "node", ""))
                            for entry in metadata_by_index[index]
                        )
                        raise ValueError(
                            "Mode C morph metadata has duplicate controller index "
                            f"{index}: {providers}"
                        )
                    duplicate_names = sorted(
                        name
                        for name, entries in metadata_by_name.items()
                        if len(entries) != 1
                    )
                    if duplicate_names:
                        name = duplicate_names[0]
                        providers = sorted(
                            str(getattr(entry, "node", ""))
                            for entry in metadata_by_name[name]
                        )
                        raise ValueError(
                            "Mode C morph metadata has duplicate controller name "
                            f"{name!r}: {providers}"
                        )

                for index, entries in sorted(metadata_by_index.items()):
                    if len(entries) != 1:
                        continue
                    entry = entries[0]
                    if len(metadata_by_name.get(str(entry.name), ())) != 1:
                        continue
                    attr = f"inputWeight[{index}]"
                    source_frames = _key_times(controller, (attr,))
                    if source_frames or standard_dense_mode:
                        channels.append((controller, attr, str(entry.name), source_frames))
                        controller_nodes.add(controller)
                        controller_channel_morph_types[(controller, attr)] = str(
                            getattr(entry, "morph_type", "") or ""
                        ).strip().casefold()

        channels = [
            (
                node,
                attr,
                morph_name,
                ranged_source_frames,
                bool(
                    dense_sample
                    and len(source_frames) == 1
                    and len(ranged_source_frames) == 1
                    and _is_direct_authored_track(node, (attr,))
                ),
            )
            for node, attr, morph_name, source_frames in channels
            for ranged_source_frames in [
                _filter_frame_range(source_frames, start_frame, end_frame)
            ]
        ]

        if standard_dense_mode:
            output_providers = {}
            dropped_providers = set()
            for node, attr, morph_name, _source_frames, _direct_single in channels:
                output_providers.setdefault(str(morph_name), []).append(
                    (str(node), str(attr))
                )
            for morph_name, providers in sorted(output_providers.items()):
                unique_providers = sorted(set(providers))
                if len(unique_providers) <= 1:
                    continue
                controller_providers = [
                    provider
                    for provider in unique_providers
                    if provider[0] in controller_nodes
                ]
                non_controller_providers = [
                    provider
                    for provider in unique_providers
                    if provider[0] not in controller_nodes
                ]
                if (
                    len(unique_providers) != 2
                    or len(controller_providers) != 1
                    or len(non_controller_providers) != 1
                ):
                    raise ValueError(
                        "Mode C morph output has duplicate providers for "
                        f"{morph_name!r}: {unique_providers}"
                    )
                controller_provider = controller_providers[0]
                controller_type = controller_channel_morph_types.get(
                    controller_provider,
                    "",
                )
                if controller_type != "vertex":
                    raise ValueError(
                        "Mode C morph output has ambiguous non-vertex controller "
                        f"provider for {morph_name!r}: {unique_providers}"
                    )
                dropped_providers.add(
                    (str(morph_name), non_controller_providers[0])
                )
            if dropped_providers:
                channels = [
                    channel
                    for channel in channels
                    if (
                        str(channel[2]),
                        (str(channel[0]), str(channel[1])),
                    )
                    not in dropped_providers
                ]

        direct_multi_key_candidates: dict[str, list[tuple[str, int]]] = {}
        if standard_dense_mode:
            for node, attr, morph_name, ranged_source_frames, direct_single in channels:
                if direct_single:
                    continue
                if (
                    len(ranged_source_frames) > 1
                    and _is_direct_authored_track(node, (attr,))
                ):
                    direct_multi_key_candidates.setdefault(
                        str(morph_name), []
                    ).append((f"{node}.{attr}", len(ranged_source_frames)))

        def append_frame(
            node,
            attr,
            morph_name,
            frame_number,
            weight,
            source_frames,
            direct_single,
        ):
            static_keyless = (node, attr) in static_keyless_channels
            if direct_single:
                is_default = weight == 0.0
                controller_direct = node in controller_nodes
                self._record_track_selection(
                    "morph",
                    morph_name,
                    "omitted_default" if is_default else "constant_one_key",
                    (
                        "keyless_static_default"
                        if static_keyless
                        else "controller_direct_single_default"
                        if controller_direct
                        else "direct_single_key_default"
                    )
                    if is_default
                    else (
                        "keyless_static_non_default"
                        if static_keyless
                        else "controller_direct_single_non_default"
                        if controller_direct
                        else "direct_single_key_non_default"
                    ),
                    0 if static_keyless else len(source_frames),
                    0 if is_default else 1,
                )
                if is_default:
                    return
            frames.append(
                {
                    "morph_name": morph_name,
                    "frame_number": _vmd_frame_number(frame_number, time_converter),
                    "weight": weight,
                }
            )

        if timeline_evaluation and channels:
            channel_samples = [
                (
                    node,
                    attr,
                    morph_name,
                    set(dense_frame_samples)
                    if dense_sample
                    and dense_frame_samples is not None
                    and not direct_single
                    else set(ranged_source_frames),
                    ranged_source_frames,
                    direct_single,
                )
                for node, attr, morph_name, ranged_source_frames, direct_single in channels
            ]
            sample_times = sorted(
                {
                    frame
                    for _node, _attr, _morph_name, frames_for_channel, _ranged_source_frames, _direct in channel_samples
                    for frame in frames_for_channel
                }
            )
            with _MayaTimelineReader() as timeline_reader:
                for frame_number in sample_times:
                    timeline_reader.set_frame(frame_number)
                    for node, attr, morph_name, frames_for_channel, ranged_source_frames, direct_single in channel_samples:
                        if frame_number not in frames_for_channel:
                            continue
                        append_frame(
                            node,
                            attr,
                            morph_name,
                            frame_number,
                            _current_plug_float(node, attr),
                            ranged_source_frames,
                            direct_single,
                        )
        else:
            for node, attr, morph_name, ranged_source_frames, direct_single in channels:
                planned_frames = (
                    ranged_source_frames
                    if direct_single
                    else sorted(set(dense_frame_samples))
                    if dense_sample and dense_frame_samples is not None
                    else ranged_source_frames
                )
                for frame_number in planned_frames:
                    append_frame(
                        node,
                        attr,
                        morph_name,
                        frame_number,
                        _plug_float(node, attr, frame_number),
                        ranged_source_frames,
                        direct_single,
                    )
        if dense_sample:
            diagnostic_rows = {}
            for node, _attr, morph_name, ranged_source_frames, direct_single in channels:
                if direct_single:
                    continue
                direct_multi_key = (
                    len(direct_multi_key_candidates.get(str(morph_name), ())) == 1
                    and direct_multi_key_candidates[str(morph_name)][0][0]
                    == f"{node}.{_attr}"
                )
                if direct_multi_key:
                    continue
                keyless_dependency = (node, _attr) in keyless_dependency_channels
                dependency = node in controller_nodes or keyless_dependency
                reason = (
                    "keyless_incoming_dependency"
                    if keyless_dependency
                    else "keyless_controller_dependency"
                    if node in controller_nodes and not ranged_source_frames
                    else "morph_controller_route"
                    if dependency
                    else "multiple_source_keys"
                )
                candidate = (
                    dependency,
                    reason,
                    len(ranged_source_frames),
                )
                current = diagnostic_rows.get(str(morph_name))
                if current is None or (dependency and not current[0]):
                    diagnostic_rows[str(morph_name)] = candidate
            for morph_name, (dependency, reason, source_key_count) in diagnostic_rows.items():
                planned_key_count = (
                    len(dense_frame_samples)
                    if dense_frame_samples is not None
                    else source_key_count
                )
                self._record_track_selection(
                    "morph",
                    morph_name,
                    "dependency_baked" if dependency else "authored_sampled",
                    reason,
                    source_key_count,
                    planned_key_count,
                )
        morph_frames, selection_evidence = _collapse_exact_constant_direct_tracks(
            frames,
            "morph_name",
            ("weight",),
            direct_multi_key_candidates,
            (0.0,),
            len(dense_frame_samples or ()) if dense_sample else 0,
        )
        for name, decision, reason, source_key_count, planned_key_count in selection_evidence:
            self._record_track_selection(
                "morph",
                name,
                decision,
                reason,
                source_key_count,
                planned_key_count,
            )
        return morph_frames

    def collect_camera_frames(
        self,
        cameras: Sequence[str],
        start_frame: Optional[float] = None,
        end_frame: Optional[float] = None,
        time_converter=None,
        dense_sample: bool = False,
        dense_frame_samples: Optional[Sequence[float]] = None,
        timeline_evaluation: bool = False,
    ) -> list[dict]:
        """Collect keyed MMD camera controller frames."""
        time_converter = time_converter or _scene_maya_time_to_vmd_frame()
        frames = []
        restore_time = None
        timeline_reader = _MayaTimelineReader() if timeline_evaluation else None
        with timeline_reader or nullcontext():
            try:
                for camera in cameras:
                    camera_target = _camera_target_node(camera)
                    camera_root = _camera_root_node(camera)
                    camera_shape = _camera_shape(camera)
                    source_frames = sorted(
                        set(_key_times(camera, _CAMERA_EXPORT_ATTRS))
                        | (
                            set(_key_times(camera_root, _BONE_EXPORT_ATTRS))
                            if camera_root
                            else set()
                        )
                        | (
                            set(_key_times(camera_target, _TRANSFORM_EXPORT_ATTRS))
                            if camera_target
                            else set()
                        )
                        | (
                            set(_key_times(camera_shape, _CAMERA_SHAPE_EXPORT_ATTRS))
                            if camera_shape
                            else set()
                        )
                    )
                    keyed_frames = (
                        sorted(set(dense_frame_samples))
                        if dense_sample
                        and dense_frame_samples is not None
                        and source_frames
                        else _filter_frame_range(
                            source_frames,
                            start_frame,
                            end_frame,
                        )
                    )
                    for frame_number in keyed_frames:
                        uses_raw_mmd_attrs = _uses_raw_mmd_camera_attrs(camera)
                        uses_aim_roll_rig = bool(
                            _uses_aim_roll_camera(camera) and camera_target
                        )
                        if timeline_reader is not None:
                            timeline_reader.set_frame(frame_number)
                            read_value = _current_plug_float_at_frame
                        else:
                            read_value = _plug_float
                        if uses_aim_roll_rig:
                            if timeline_reader is None:
                                if restore_time is None:
                                    restore_time = _query_current_time()
                                cmds.currentTime(frame_number, edit=True)
                            motion_scale = _camera_motion_scale(camera)
                            eye = om.MVector(
                                *cmds.xform(
                                    camera,
                                    query=True,
                                    worldSpace=True,
                                    translation=True,
                                )
                            )
                            target = om.MVector(
                                *cmds.xform(
                                    camera_target,
                                    query=True,
                                    worldSpace=True,
                                    translation=True,
                                )
                            )
                            position = (
                                float(target.x) / motion_scale,
                                float(target.y) / motion_scale,
                                -float(target.z) / motion_scale,
                            )
                            matrix = om.MMatrix(
                                cmds.getAttr(f"{camera}.worldMatrix[0]")
                            )
                            forward = om.MVector(0.0, 0.0, -1.0) * matrix
                            up = om.MVector(0.0, 1.0, 0.0) * matrix
                            if forward.length() > 1e-12:
                                forward.normalize()
                            if up.length() > 1e-12:
                                up.normalize()
                            distance = (
                                _signed_camera_distance(eye, target, forward)
                                / motion_scale
                            )
                            rotation = mmd_camera_rotation_from_maya_forward_up(
                                (forward.x, forward.y, forward.z),
                                (up.x, up.y, up.z),
                            )
                            viewing_angle = _camera_viewing_angle(
                                camera, camera_shape, frame_number, read_value
                            )
                            perspective = _camera_perspective_value(
                                camera, camera_shape, frame_number, read_value
                            )
                        elif uses_raw_mmd_attrs and all(
                            _has_attr(camera, attr)
                            for attr in (
                                "mmd_camera_target_x",
                                "mmd_camera_target_y",
                                "mmd_camera_target_z",
                            )
                        ):
                            position = (
                                read_value(
                                    camera, "mmd_camera_target_x", frame_number
                                ),
                                read_value(
                                    camera, "mmd_camera_target_y", frame_number
                                ),
                                read_value(
                                    camera, "mmd_camera_target_z", frame_number
                                ),
                            )
                        else:
                            position = (
                                read_value(camera, "translateX", frame_number),
                                read_value(camera, "translateY", frame_number),
                                -read_value(camera, "translateZ", frame_number),
                            )
                        if not uses_aim_roll_rig:
                            if uses_raw_mmd_attrs and all(
                                _has_attr(camera, attr)
                                for attr in (
                                    "mmd_camera_rotation_x",
                                    "mmd_camera_rotation_y",
                                    "mmd_camera_rotation_z",
                                )
                            ):
                                rotation = (
                                    read_value(
                                        camera,
                                        "mmd_camera_rotation_x",
                                        frame_number,
                                    ),
                                    read_value(
                                        camera,
                                        "mmd_camera_rotation_y",
                                        frame_number,
                                    ),
                                    read_value(
                                        camera,
                                        "mmd_camera_rotation_z",
                                        frame_number,
                                    ),
                                )
                            else:
                                rotation = (
                                    math.radians(
                                        read_value(camera, "rotateX", frame_number)
                                    ),
                                    math.radians(
                                        read_value(camera, "rotateY", frame_number)
                                    ),
                                    -math.radians(
                                        read_value(camera, "rotateZ", frame_number)
                                    ),
                                )
                            distance = read_value(
                                camera, "mmd_camera_distance", frame_number
                            )
                            viewing_angle = int(
                                round(
                                    read_value(
                                        camera,
                                        "mmd_camera_viewing_angle",
                                        frame_number,
                                    )
                                )
                            )
                            perspective = int(
                                round(
                                    read_value(
                                        camera,
                                        "mmd_camera_perspective",
                                        frame_number,
                                    )
                                )
                            )
                        frames.append(
                            {
                                "frame_number": _vmd_frame_number(
                                    frame_number, time_converter
                                ),
                                "distance": distance,
                                "position": position,
                                "rotation": rotation,
                                "viewing_angle": viewing_angle,
                                "perspective": perspective,
                            }
                        )
            finally:
                if restore_time is not None:
                    cmds.currentTime(restore_time, edit=True)
        frames.sort(key=lambda item: item["frame_number"])
        return frames

    def collect_light_frames(
        self,
        lights: Sequence[str],
        start_frame: Optional[float] = None,
        end_frame: Optional[float] = None,
        time_converter=None,
        dense_sample: bool = False,
        dense_frame_samples: Optional[Sequence[float]] = None,
        timeline_evaluation: bool = False,
    ) -> list[dict]:
        """Collect keyed MMD light controller frames."""
        time_converter = time_converter or _scene_maya_time_to_vmd_frame()
        frames = []
        timeline_reader = _MayaTimelineReader() if timeline_evaluation else None
        with timeline_reader or nullcontext():
            for light in lights:
                color_node, color_attrs = _light_color_source(light)
                source_frames = set(_key_times(light, _LIGHT_ROTATE_ATTRS)) | set(
                    _key_times(color_node, color_attrs)
                )
                keyed_frames = (
                    sorted(set(dense_frame_samples))
                    if dense_sample
                    and dense_frame_samples is not None
                    and source_frames
                    else _filter_frame_range(
                        source_frames,
                        start_frame,
                        end_frame,
                    )
                )
                for frame_number in keyed_frames:
                    if timeline_reader is not None:
                        timeline_reader.set_frame(frame_number)
                        read_value = _current_plug_float_at_frame
                    else:
                        read_value = _plug_float
                    frames.append(
                        {
                            "frame_number": _vmd_frame_number(
                                frame_number, time_converter
                            ),
                            "color": tuple(
                                read_value(color_node, attr, frame_number)
                                for attr in color_attrs
                            ),
                            "position": _maya_light_rotation_to_vmd_direction(
                                read_value(light, "rotateX", frame_number),
                                read_value(light, "rotateY", frame_number),
                            ),
                        }
                    )
        frames.sort(key=lambda item: item["frame_number"])
        return frames

    def _find_joints(self, target_model: Optional[str]) -> list[str]:
        if not target_model:
            return cmds.ls(type="joint") or []
        descendants = cmds.listRelatives(target_model, allDescendents=True, type="joint", fullPath=True) or []
        nodes = []
        if cmds.nodeType(target_model) == "joint":
            nodes.append(target_model)
        nodes.extend(descendants)
        return nodes

    def _find_blend_shapes(self, target_model: Optional[str] = None) -> list[str]:
        """Find blendShapes on mesh history below the selected model root.

        A global ``cmds.ls(type="blendShape")`` query is safe only when no
        model target exists.  With a target, history is resolved from its mesh
        shapes so another namespaced model cannot contribute vertex morph keys.
        """
        if not target_model:
            return cmds.ls(type="blendShape") or []

        try:
            target_type = cmds.nodeType(target_model)
        except Exception:
            target_type = None
        if target_type == "mesh":
            shapes = [target_model]
        else:
            shapes = list(
                cmds.listRelatives(
                    target_model,
                    shapes=True,
                    type="mesh",
                    fullPath=True,
                )
                or []
            )
            shapes.extend(
                cmds.listRelatives(
                    target_model,
                    allDescendents=True,
                    type="mesh",
                    fullPath=True,
                )
                or []
            )

        result = []
        seen = set()
        for shape in shapes:
            try:
                history = cmds.listHistory(shape, pruneDagObjects=True) or []
            except Exception:
                continue
            for node in history:
                if node in seen:
                    continue
                try:
                    is_blend_shape = cmds.nodeType(node) == "blendShape"
                except Exception:
                    is_blend_shape = False
                if is_blend_shape:
                    seen.add(node)
                    result.append(node)
        return result

    def _find_tagged_nodes(
        self,
        attr: str,
        target_model: Optional[str] = None,
    ) -> list[str]:
        """Find tagged DAG nodes, restricting automatic discovery to a root."""
        nodes = cmds.ls(f"*.{attr}", objectsOnly=True, long=True) or []
        if not target_model:
            return self._require_single_tagged_track(nodes, attr, "targetless automatic")

        root_path = _canonical_dag_path(target_model)
        if not root_path:
            return []
        scoped = [
            node
            for node in nodes
            if _dag_path_is_under_root(node, root_path)
        ]
        return self._require_single_tagged_track(scoped, attr, "target-scoped automatic")

    def _resolve_tagged_track(
        self,
        options: Mapping[str, Any],
        key: str,
        attr: str,
        target_model: Optional[str],
    ) -> list[str]:
        """Resolve one VMD camera/light track while preserving explicit empties."""
        if key in options:
            return self._require_single_tagged_track(
                list(options.get(key) or []),
                attr,
                "explicit",
            )
        return self._find_tagged_nodes(attr, target_model)

    @staticmethod
    def _require_single_tagged_track(nodes: Sequence[str], attr: str, source: str) -> list[str]:
        """Enforce VMD's single camera/light track contract."""
        resolved = list(nodes)
        if len(resolved) > 1:
            raise RuntimeError(
                f"{source} {attr} discovery found multiple tagged nodes; "
                "VMD export requires one camera/light track"
            )
        return resolved

    def _model_name(self, target_model: Optional[str]) -> str:
        if target_model and _has_attr(target_model, ATTR_MMD_MODEL_NAME):
            value = cmds.getAttr(f"{target_model}.{ATTR_MMD_MODEL_NAME}")
            if value:
                return str(value)
        return str(target_model or "")

    def _mmd_bone_name(self, joint: str) -> str:
        if _has_attr(joint, ATTR_MMD_BONE_NAME):
            value = cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_NAME}")
            if value:
                return str(value)
        return _leaf_name(joint)

    def _blendshape_morph_names(self, blend_shape: str) -> dict[int, str]:
        stored = _read_blendshape_morph_names(blend_shape)
        weight_count = int(cmds.blendShape(blend_shape, query=True, weightCount=True) or 0)
        result = {}
        for weight_index in range(weight_count):
            alias = cmds.aliasAttr(f"{blend_shape}.weight[{weight_index}]", query=True)
            result[weight_index] = stored.get(weight_index) or alias or f"weight[{weight_index}]"
        return result


def _read_blendshape_morph_names(blend_shape: str) -> dict[int, str]:
    if not _has_attr(blend_shape, ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON):
        return {}
    try:
        raw = cmds.getAttr(f"{blend_shape}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}") or "{}"
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parse_blendshape_morph_names(parsed)


def _morph_controller_for_model(target_model: str) -> Optional[str]:
    """Resolve the single morph controller connected to a model root.

    The root message connection is the ownership boundary for morph export.
    Missing, malformed, or ambiguous connections fail closed so a VMD export
    cannot accidentally include another model's controller weights.
    """
    if not target_model or not _has_attr(target_model, "mmd_morph_controller"):
        return None
    try:
        controllers = cmds.listConnections(
            f"{target_model}.mmd_morph_controller",
            source=True,
            destination=False,
        ) or []
    except Exception:
        return None
    if len(controllers) != 1:
        return None
    controller = str(controllers[0])
    try:
        if cmds.nodeType(controller) != "mmdMorphController":
            return None
    except Exception:
        return None
    return controller


def _key_times(node: str, attrs: Iterable[str]) -> list[float]:
    if not node:
        return []
    times = []
    for attr in attrs:
        plug = f"{node}.{attr}"
        try:
            values = cmds.keyframe(plug, query=True, timeChange=True) or []
        except Exception:
            values = []
        times.extend(float(value) for value in values)
        for source_node in _upstream_anim_curves(plug):
            try:
                values = cmds.keyframe(source_node, query=True, timeChange=True) or []
                times.extend(float(value) for value in values)
            except Exception:
                continue
    return sorted(set(times))


def _upstream_anim_curves(plug: str) -> set[str]:
    curves = set()
    try:
        queue = list(
            cmds.listConnections(
                plug, source=True, destination=False, plugs=True
            )
            or []
        )
    except (RuntimeError, ValueError):
        return curves
    visited = set()
    while queue:
        source = str(queue.pop())
        node = source.split(".", 1)[0]
        if node in visited:
            continue
        visited.add(node)
        node_type = str(cmds.nodeType(node))
        if node_type.startswith("animCurve"):
            curves.add(node)
            continue
        if not (
            node_type in {"pairBlend", "blendWeighted", "unitConversion"}
            or node_type.startswith("animBlendNode")
        ):
            continue
        queue.extend(
            cmds.listConnections(
                node, source=True, destination=False, plugs=True
            )
            or []
        )
    return curves


def _routed_key_times(
    joint: str,
    route: Mapping[str, tuple[str, str]],
) -> list[float]:
    times = []
    for attr in _BONE_EXPORT_ATTRS:
        node, target_attr = route.get(attr, (joint, attr))
        times.extend(_key_times(node, (target_attr,)))
    return sorted(set(times))


def _routed_plug_float(
    joint: str,
    attr: str,
    frame_number: float,
    route: Mapping[str, tuple[str, str]],
) -> float:
    node, target_attr = route.get(attr, (joint, attr))
    return _plug_float(node, target_attr, frame_number)


def _native_route_inventory(
    joints: Sequence[str],
    input_routes: Mapping[str, Mapping[str, tuple[str, str]]],
) -> dict[str, Any]:
    """Build bounded route/node-type evidence before native sampling starts."""

    target_types: dict[str, set[str]] = {}
    target_nodes: set[str] = set()
    physics_drivers: set[str] = set()
    for joint in joints:
        long_name = str((cmds.ls(joint, long=True) or [joint])[0])
        route = input_routes.get(long_name, {})
        for attr in _BONE_EXPORT_ATTRS:
            node, _target_attr = route.get(attr, (long_name, attr))
            node = str(node)
            target_nodes.add(node)
            try:
                node_type = str(cmds.nodeType(node) or "unknown")
            except Exception:
                node_type = "unknown"
            target_types.setdefault(node_type, set()).add(node)
            if node_type == "mmdPhysicsBoneDriver":
                physics_drivers.add(node)
    return {
        "route_target_node_count": len(target_nodes),
        "route_target_node_types": {
            node_type: len(nodes)
            for node_type, nodes in sorted(target_types.items())
        },
        "physics_driver_reached_count": len(physics_drivers),
    }


def _query_current_time() -> Optional[float]:
    try:
        return float(cmds.currentTime(query=True))
    except Exception:
        return None


class _MayaTimelineReader:
    """Read current-frame values while advancing Maya's Timeline safely."""

    def __init__(self) -> None:
        self._entry_time: Optional[float] = None
        self._sample_time: Optional[float] = None
        self._has_sampled = False

    def __enter__(self):
        try:
            playing = bool(cmds.play(query=True, state=True))
        except Exception as exc:
            raise RuntimeError("Mode C Timeline playback state query failed") from exc
        if playing:
            raise RuntimeError("Mode C Timeline sampling cannot run during playback")
        try:
            self._entry_time = float(cmds.currentTime(query=True))
        except Exception as exc:
            raise RuntimeError("Mode C Timeline entry time query failed") from exc
        self._sample_time = self._entry_time
        return self

    def set_frame(self, frame: float) -> None:
        sample_time = float(frame)
        if (
            self._has_sampled
            and self._sample_time is not None
            and sample_time < self._sample_time
        ):
            raise RuntimeError(
                "Mode C Timeline samples must be evaluated in ascending order"
            )
        if sample_time == self._sample_time:
            self._has_sampled = True
            return
        try:
            cmds.currentTime(sample_time, edit=True)
        except Exception as exc:
            raise RuntimeError(
                f"Mode C Timeline evaluation failed at frame {sample_time:g}"
            ) from exc
        self._sample_time = sample_time
        self._has_sampled = True

    def __exit__(self, _exc_type, _exc, _traceback) -> bool:
        if self._entry_time is None:
            return False
        try:
            cmds.currentTime(self._entry_time, edit=True)
        except Exception as exc:
            raise RuntimeError("Mode C Timeline time restoration failed") from exc
        return False


def _scene_maya_fps() -> float:
    """Return the current Maya UI FPS used to evaluate scene key times."""
    try:
        unit = cmds.currentUnit(query=True, time=True)
    except Exception:
        return 30.0
    if isinstance(unit, (int, float)):
        return float(unit) if float(unit) > 0.0 else 30.0
    unit_name = str(unit).strip().lower()
    fps = _MAYA_TIME_UNIT_FPS.get(unit_name)
    if fps is not None:
        return fps
    if unit_name.endswith("fps"):
        try:
            parsed = float(unit_name[:-3])
        except ValueError:
            parsed = 0.0
        if parsed > 0.0:
            return parsed
    return 30.0


def _scene_maya_time_to_vmd_frame():
    """Build a converter from current Maya time values to fixed-30fps VMD frames."""
    fps = _scene_maya_fps()
    return lambda maya_time: _maya_time_to_vmd_frame_at_fps(maya_time, fps)


def _vmd_frame_number(maya_time: float, time_converter) -> int:
    """Convert a Maya time to one integer VMD frame number."""
    return int(round(float(time_converter(maya_time))))


def _deduplicate_frames(frames: Iterable[dict], key_fields: Sequence[str]) -> list[dict]:
    """Keep the first Maya sample for each VMD output key."""
    unique = {}
    for frame in frames:
        key = tuple(frame[field] for field in key_fields)
        unique.setdefault(key, frame)
    return sorted(unique.values(), key=lambda item: tuple(item[field] for field in key_fields))


def _collapse_exact_constant_direct_tracks(
    frames: Iterable[dict],
    key_field: str,
    signature_fields: Sequence[str],
    candidates: Mapping[str, Sequence[tuple[str, int]]],
    default_signature: tuple,
    planned_key_count: int,
) -> tuple[list[dict], list[tuple[str, str, str, int, int]]]:
    """Collapse exact-constant direct tracks after VMD output deduplication.

    ``candidates`` is populated only for direct-authored multi-key channels.
    A name with multiple providers remains dense and is intentionally excluded
    from this optimization.  Values are compared exactly as emitted; no source
    curve or floating-point tolerance is involved.
    """

    unique = _deduplicate_frames(frames, (key_field, "frame_number"))
    grouped_frames: dict[Any, list[dict]] = {}
    for frame in unique:
        grouped_frames.setdefault(frame[key_field], []).append(frame)
    evidence = []
    for name, provider_rows in candidates.items():
        providers = {provider for provider, _count in provider_rows}
        if len(providers) != 1:
            continue
        track = grouped_frames.get(name, [])
        if not track:
            continue
        signatures = {
            tuple(frame[field] for field in signature_fields) for frame in track
        }
        source_key_count = max(count for _provider, count in provider_rows)
        if len(signatures) != 1:
            evidence.append(
                (
                    name,
                    "authored_sampled",
                    "multiple_source_keys",
                    source_key_count,
                    planned_key_count,
                )
            )
            continue

        first = track[0]
        is_default = tuple(first[field] for field in signature_fields) == default_signature
        if not is_default:
            grouped_frames[name] = [first]
        else:
            grouped_frames.pop(name, None)
        evidence.append(
            (
                name,
                "omitted_default" if is_default else "constant_one_key",
                "dense_exact_constant",
                source_key_count,
                0 if is_default else 1,
            )
        )
    flattened = [
        frame
        for track in grouped_frames.values()
        for frame in track
    ]
    return _deduplicate_frames(flattened, (key_field, "frame_number")), evidence


def _ik_baseline_time(
    start_frame: Optional[float],
    end_frame: Optional[float],
) -> Optional[float]:
    """Return an in-range Maya time for an unkeyed IK baseline property."""
    if start_frame is not None:
        candidate = float(start_frame)
    else:
        candidate = 0.0
    if candidate < 0.0:
        return None
    if end_frame is not None and candidate > float(end_frame):
        return None
    return candidate


def _filter_frame_range(
    frames: Iterable[float],
    start_frame: Optional[float],
    end_frame: Optional[float],
) -> list[float]:
    result = []
    for frame in sorted(set(float(value) for value in frames)):
        if start_frame is not None and frame < start_frame:
            continue
        if end_frame is not None and frame > end_frame:
            continue
        result.append(frame)
    return result


def _dense_frame_samples(
    frames: Iterable[float],
    start_frame: Optional[float],
    end_frame: Optional[float],
) -> Optional[list[int]]:
    """Return one-frame integer samples for a Mode C animation range."""
    if start_frame is not None and end_frame is not None:
        try:
            first = int(math.ceil(float(start_frame)))
            last = int(math.floor(float(end_frame)))
        except (TypeError, ValueError, OverflowError):
            return []
        if last < first:
            return []
        return list(range(first, last + 1))
    observed = [float(value) for value in frames]
    if not observed:
        return None
    if start_frame is not None or end_frame is not None:
        ranged = _filter_frame_range(observed, start_frame, end_frame)
        if not ranged:
            return None
    else:
        ranged = observed
    first = int(math.floor(min(ranged)))
    last = int(math.ceil(max(ranged)))
    if last < first:
        return []
    return list(range(first, last + 1))


def _mode_c_earliest_integer_sample(
    dense_frame_samples: Optional[Sequence[float]],
    start_frame: Optional[float],
    end_frame: Optional[float],
) -> Optional[float]:
    """Resolve one requested-range integer sample for keyless Mode C tracks."""

    if start_frame is None or end_frame is None:
        return None
    try:
        first = int(math.ceil(float(start_frame)))
        last = int(math.floor(float(end_frame)))
    except (TypeError, ValueError, OverflowError):
        return None
    if last < first:
        return None
    if dense_frame_samples:
        candidates = []
        for frame in dense_frame_samples:
            try:
                candidate = float(frame)
            except (TypeError, ValueError, OverflowError):
                continue
            if (
                math.isfinite(candidate)
                and candidate.is_integer()
                and first <= candidate <= last
            ):
                candidates.append(candidate)
        if candidates:
            return min(candidates)
    return float(first)


def _plug_float(node: str, attr: str, frame: float) -> float:
    value = cmds.getAttr(f"{node}.{attr}", time=frame)
    if isinstance(value, (list, tuple)):
        if len(value) == 1 and isinstance(value[0], (list, tuple)):
            value = value[0][0]
        else:
            value = value[0]
    return float(value or 0.0)


def _current_plug_float(node: str, attr: str) -> float:
    """Read one plug at Maya's current Timeline time."""
    value = cmds.getAttr(f"{node}.{attr}")
    if isinstance(value, (list, tuple)):
        if len(value) == 1 and isinstance(value[0], (list, tuple)):
            value = value[0][0]
        else:
            value = value[0]
    return float(value or 0.0)


def _current_plug_float_at_frame(node: str, attr: str, _frame: float) -> float:
    return _current_plug_float(node, attr)


def _camera_shape(camera: str) -> Optional[str]:
    shapes = cmds.listRelatives(camera, shapes=True, type="camera") or []
    return shapes[0] if shapes else None


def _camera_viewing_angle(
    camera: str,
    camera_shape: Optional[str],
    frame: float,
    read_value=_plug_float,
) -> int:
    if camera_shape:
        focal_length = read_value(camera_shape, "focalLength", frame)
        if abs(focal_length) > 1e-9:
            aperture_inch = read_value(
                camera_shape, "verticalFilmAperture", frame
            )
            aperture_mm = aperture_inch * 25.4
            return int(round(math.degrees(2.0 * math.atan(aperture_mm / (2.0 * focal_length)))))
    if _has_attr(camera, "mmd_camera_viewing_angle"):
        return int(round(read_value(camera, "mmd_camera_viewing_angle", frame)))
    return 45


def _camera_perspective_value(
    camera: str,
    camera_shape: Optional[str],
    frame: float,
    read_value=_plug_float,
) -> int:
    if camera_shape and _has_attr(camera_shape, "orthographic"):
        return int(round(read_value(camera_shape, "orthographic", frame)))
    if _has_attr(camera, "mmd_camera_perspective"):
        return int(round(read_value(camera, "mmd_camera_perspective", frame)))
    return 0


def _resolve_bind_pose(
    bind_poses: Mapping[str, Sequence[float]],
    bone_name: str,
    joint: str,
) -> tuple[float, float, float]:
    value = bind_poses.get(bone_name, bind_poses.get(joint))
    if value is None:
        value = get_stored_bind_translate(joint) or (0.0, 0.0, 0.0)
    if len(value) != 3:
        raise ValueError("bone bind pose must contain 3 numbers")
    return (float(value[0]), float(value[1]), float(value[2]))


def _maya_translate_to_vmd_position(
    translate: Sequence[float],
    bind_pose: Sequence[float],
    motion_scale: float,
) -> tuple[float, float, float]:
    if abs(float(motion_scale)) < 1e-12:
        raise ValueError("motion_scale must not be zero")
    tx, ty, tz = (float(translate[0]), float(translate[1]), float(translate[2]))
    bx, by, bz = (float(bind_pose[0]), float(bind_pose[1]), float(bind_pose[2]))
    scale = float(motion_scale)
    return ((tx - bx) / scale, (ty - by) / scale, -(tz - bz) / scale)


def _build_rotation_export_context(
    joints: Sequence[str],
) -> dict[str, dict[str, Any]]:
    canonical = {
        int(cmds.getAttr(f"{joint}.mmd_bone_index")): str(
            (cmds.ls(joint, long=True) or [joint])[0]
        )
        for joint in joints
        if _has_attr(joint, "mmd_bone_index")
    }
    index_by_joint = {joint: index for index, joint in canonical.items()}
    parent_by_index = {}
    for index, joint in canonical.items():
        parent = (cmds.listRelatives(joint, parent=True, fullPath=True) or [None])[0]
        parent_by_index[index] = index_by_joint.get(parent)

    bind_worlds = {}

    def bind_world(index: int) -> om.MMatrix:
        if index in bind_worlds:
            return bind_worlds[index]
        joint = canonical[index]
        translate = get_stored_bind_translate(joint)
        if translate is None:
            translate = cmds.getAttr(f"{joint}.translate")[0]
        transform = om.MTransformationMatrix()
        transform.setTranslation(om.MVector(*translate), om.MSpace.kTransform)
        orient = _joint_orient_quaternion(joint)
        transform.setRotation(orient)
        local = transform.asMatrix()
        parent_index = parent_by_index[index]
        result = local * bind_world(parent_index) if parent_index is not None else local
        bind_worlds[index] = result
        return result

    for index in canonical:
        bind_world(index)

    no_orient = {}
    for index, matrix in bind_worlds.items():
        value = om.MMatrix()
        value[12], value[13], value[14] = matrix[12], matrix[13], matrix[14]
        no_orient[index] = value

    result = {}
    for index, joint in canonical.items():
        parent_index = parent_by_index[index]
        parent_bind = bind_worlds[parent_index] if parent_index is not None else om.MMatrix()
        parent_no_orient = no_orient[parent_index] if parent_index is not None else om.MMatrix()
        result[joint] = {
            "jointOrient": _joint_orient_quaternion(joint),
            "rotateOrder": int(cmds.getAttr(f"{joint}.rotateOrder")),
            "bindCorrection": om.MTransformationMatrix(
                bind_worlds[index] * no_orient[index].inverse()
            ).rotation(asQuaternion=True),
            "parentCorrection": om.MTransformationMatrix(
                parent_no_orient * parent_bind.inverse()
            ).rotation(asQuaternion=True),
        }
    return result


def _joint_orient_quaternion(joint: str) -> om.MQuaternion:
    values = cmds.getAttr(f"{joint}.jointOrient")[0]
    return om.MEulerRotation(
        math.radians(float(values[0])),
        math.radians(float(values[1])),
        math.radians(float(values[2])),
    ).asQuaternion()


def _maya_joint_rotate_to_vmd_quaternion(
    joint: str,
    rx: float,
    ry: float,
    rz: float,
    export_context: Optional[Mapping[str, Any]] = None,
) -> tuple[float, float, float, float]:
    """Convert Maya XYZ joint.rotate degrees to a JO-aware VMD quaternion."""
    if export_context:
        order = int(export_context["rotateOrder"])
        order_map = (
            om.MEulerRotation.kXYZ,
            om.MEulerRotation.kYZX,
            om.MEulerRotation.kZXY,
            om.MEulerRotation.kXZY,
            om.MEulerRotation.kYXZ,
            om.MEulerRotation.kZYX,
        )
        euler = om.MEulerRotation(
            math.radians(rx),
            math.radians(ry),
            math.radians(rz),
            order_map[order] if 0 <= order < len(order_map) else order_map[0],
        )
        q_rotate = euler.asQuaternion()
        q_total = q_rotate * export_context["jointOrient"]
        q_maya = (
            export_context["bindCorrection"].inverse()
            * q_total
            * export_context["parentCorrection"].inverse()
        )
        q_maya.normalizeIt()
        return (-q_maya.x, -q_maya.y, q_maya.z, q_maya.w)
    joint_orient = _joint_orient_values(joint)
    if joint_orient is not None:
        openmaya_result = _openmaya_joint_rotate_to_vmd_quaternion(rx, ry, rz, joint_orient)
        if openmaya_result is not None:
            return openmaya_result

    q_rotate = _euler_xyz_degrees_to_quaternion(rx, ry, rz)
    q_jo = _euler_xyz_degrees_to_quaternion(*joint_orient) if joint_orient is not None else None
    if q_jo is not None:
        q_maya = _quat_multiply(_quat_multiply(_quat_inverse(q_jo), q_rotate), q_jo)
    else:
        q_maya = q_rotate
    q_maya = _quat_normalize(q_maya)
    return (-q_maya[0], -q_maya[1], q_maya[2], q_maya[3])


def _joint_orient_values(joint: str) -> Optional[tuple[float, float, float]]:
    if not _has_attr(joint, "jointOrient"):
        return None
    value = _attr_tuple(joint, "jointOrient")
    if len(value) != 3 or not any(abs(item) > 1e-8 for item in value):
        return None
    return (float(value[0]), float(value[1]), float(value[2]))


def _openmaya_joint_rotate_to_vmd_quaternion(
    rx: float,
    ry: float,
    rz: float,
    joint_orient: Sequence[float],
) -> Optional[tuple[float, float, float, float]]:
    try:
        import maya.api.OpenMaya as om

        q_rotate = om.MEulerRotation(
            math.radians(rx),
            math.radians(ry),
            math.radians(rz),
        ).asQuaternion()
        q_jo = om.MEulerRotation(
            math.radians(joint_orient[0]),
            math.radians(joint_orient[1]),
            math.radians(joint_orient[2]),
        ).asQuaternion()
        q_maya = q_jo.inverse() * q_rotate * q_jo
        q_maya.normalizeIt()
        return (-q_maya.x, -q_maya.y, q_maya.z, q_maya.w)
    except Exception:
        return None


def _attr_tuple(node: str, attr: str) -> tuple[float, ...]:
    value = cmds.getAttr(f"{node}.{attr}")
    if isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], (list, tuple)):
        value = value[0]
    if not isinstance(value, (list, tuple)):
        return (float(value),)
    return tuple(float(item) for item in value)


def _euler_xyz_degrees_to_quaternion(rx: float, ry: float, rz: float) -> tuple[float, float, float, float]:
    """Convert XYZ Euler degrees to a Maya quaternion tuple."""
    hx = math.radians(rx) * 0.5
    hy = math.radians(ry) * 0.5
    hz = math.radians(rz) * 0.5
    sx, cx = math.sin(hx), math.cos(hx)
    sy, cy = math.sin(hy), math.cos(hy)
    sz, cz = math.sin(hz), math.cos(hz)
    return (
        sx * cy * cz + cx * sy * sz,
        cx * sy * cz - sx * cy * sz,
        cx * cy * sz + sx * sy * cz,
        cx * cy * cz - sx * sy * sz,
    )


def _quat_multiply(
    left: Sequence[float],
    right: Sequence[float],
) -> tuple[float, float, float, float]:
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return (
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
        lw * rw - lx * rx - ly * ry - lz * rz,
    )


def _quat_inverse(quat: Sequence[float]) -> tuple[float, float, float, float]:
    x, y, z, w = quat
    norm_sq = x * x + y * y + z * z + w * w
    if norm_sq <= 1e-16:
        return (0.0, 0.0, 0.0, 1.0)
    return (-x / norm_sq, -y / norm_sq, -z / norm_sq, w / norm_sq)


def _quat_normalize(quat: Sequence[float]) -> tuple[float, float, float, float]:
    x, y, z, w = quat
    length = math.sqrt(x * x + y * y + z * z + w * w)
    if length <= 1e-16:
        return (0.0, 0.0, 0.0, 1.0)
    return (x / length, y / length, z / length, w / length)


def _maya_light_rotation_to_vmd_direction(rx: float, ry: float) -> tuple[float, float, float]:
    """Invert VmdConverter._convert_light_animation's direction-to-Euler mapping."""
    rx_rad = math.radians(rx)
    ry_rad = math.radians(ry)
    cos_rx = math.cos(rx_rad)
    maya_x = -math.sin(ry_rad) * cos_rx
    maya_y = math.sin(rx_rad)
    maya_z = -math.cos(ry_rad) * cos_rx
    return (maya_x, maya_y, -maya_z)


def _has_attr(node: str, attr: str) -> bool:
    try:
        return bool(cmds.attributeQuery(attr, node=node, exists=True))
    except Exception:
        return False


def _uses_raw_mmd_camera_attrs(camera: str) -> bool:
    if not _has_attr(camera, _ATTR_MMD_CAMERA_RIG_TYPE):
        return False
    try:
        return cmds.getAttr(f"{camera}.{_ATTR_MMD_CAMERA_RIG_TYPE}") == "mmd"
    except Exception:
        return False


def _light_color_source(light: str) -> tuple[str, tuple[str, str, str]]:
    """Resolve the canonical MMD light color source for one tagged transform.

    Authoring controllers expose ``mmd_light_color*`` on the transform.  VMD
    imports that predate the controller use the directional-light shape's
    native ``color*`` channels instead, while rotation remains on the tagged
    transform.  Keep the transform as the ownership boundary and read color
    from the child shape only when the controller channels are absent.
    """
    if all(_has_attr(light, attr) for attr in _LIGHT_COLOR_ATTRS) or any(
        _key_times(light, (attr,)) for attr in _LIGHT_COLOR_ATTRS
    ):
        return light, _LIGHT_COLOR_ATTRS
    shapes = cmds.listRelatives(
        light,
        shapes=True,
        type="directionalLight",
        fullPath=True,
    ) or []
    for shape in shapes:
        if all(_has_attr(shape, attr) for attr in _LIGHT_SHAPE_COLOR_ATTRS):
            return str(shape), _LIGHT_SHAPE_COLOR_ATTRS
    raise RuntimeError(f"MMD light {light} has no supported color channels")


def _uses_aim_roll_camera(camera: str) -> bool:
    if not _has_attr(camera, _ATTR_MMD_CAMERA_RIG_TYPE):
        return False
    try:
        return cmds.getAttr(f"{camera}.{_ATTR_MMD_CAMERA_RIG_TYPE}") == _MMD_CAMERA_AIM_ROLL_RIG_TYPE
    except Exception:
        return False


def _camera_target_node(camera: str) -> Optional[str]:
    if not _has_attr(camera, ATTR_MMD_CAMERA_TARGET_NODE):
        return None
    targets = cmds.listConnections(
        f"{camera}.{ATTR_MMD_CAMERA_TARGET_NODE}",
        source=True,
        destination=False,
    ) or []
    return targets[0] if targets else None


def _camera_root_node(camera: str) -> Optional[str]:
    if not _has_attr(camera, ATTR_MMD_CAMERA_ROOT_NODE):
        return None
    roots = cmds.listConnections(
        f"{camera}.{ATTR_MMD_CAMERA_ROOT_NODE}",
        source=True,
        destination=False,
    ) or []
    return roots[0] if roots else None


def _camera_motion_scale(camera: str) -> float:
    if _has_attr(camera, MMD_CAMERA_EXPR_SCALE_ATTR):
        scale = _plug_float(camera, MMD_CAMERA_EXPR_SCALE_ATTR, _query_current_time())
        if abs(scale) > 1e-12:
            return scale
    return 1.0


def _signed_camera_distance(eye: om.MVector, target: om.MVector, forward: om.MVector) -> float:
    target_from_eye = target - eye
    distance = target_from_eye.length()
    if distance <= 1e-12:
        return 0.0
    forward_normal = om.MVector(forward.x, forward.y, forward.z)
    if forward_normal.length() <= 1e-12:
        return -distance
    forward_normal.normalize()
    return -distance if target_from_eye * forward_normal >= 0.0 else distance


def _leaf_name(node: str) -> str:
    leaf = node.rsplit("|", 1)[-1]
    return leaf.rsplit(":", 1)[-1]


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _resolve_collection_frame_range(
    options: Mapping[str, Any],
) -> tuple[Optional[float], Optional[float]]:
    """Resolve the public frame-range option shapes used by export callers."""
    value = options.get("frame_range")
    if value is None and "frame_start" in options and "frame_end" in options:
        value = (options.get("frame_start"), options.get("frame_end"))
    if value is not None:
        try:
            return _optional_float(value[0]), _optional_float(value[1])
        except (IndexError, KeyError, TypeError, ValueError, OverflowError):
            return None, None
    return (
        _optional_float(options.get("start_frame")),
        _optional_float(options.get("end_frame")),
    )


_PHYSICS_PRE_INPUT_ATTRS = {
    "translateX": "inPreTranslateX",
    "translateY": "inPreTranslateY",
    "translateZ": "inPreTranslateZ",
    "rotateX": "inPreRotateX",
    "rotateY": "inPreRotateY",
    "rotateZ": "inPreRotateZ",
}


def _physics_solvers_owned_by_model(
    root_path: str,
    *,
    strict: bool = False,
    solvers: Optional[Sequence[str]] = None,
) -> list[str]:
    """Resolve only solvers whose root or registry owns the Current Model."""

    if solvers is None:
        try:
            solvers = cmds.ls(type="mmdPhysicsSolver") or []
        except Exception:
            return []
    target_registry = None
    try:
        from mmd_tools.core.model_registry import get_model_registry

        target_registry = get_model_registry(root_path)
    except Exception:
        pass
    owned = []
    for solver in sorted({str(value) for value in solvers}):
        try:
            roots = cmds.listConnections(
                f"{solver}.modelRoot",
                source=True,
                destination=False,
            ) or []
            registries = cmds.listConnections(
                f"{solver}.modelRegistry",
                source=True,
                destination=False,
            ) or []
        except Exception:
            continue
        root_matches = [
            value
            for value in roots
            if _canonical_dag_path(value) == root_path
        ]
        selected_registry_matches = [
            value
            for value in registries
            if target_registry and str(value) == str(target_registry)
        ]
        registry_matches = bool(
            target_registry
            and len(registries) == 1
            and str(registries[0]) == str(target_registry)
        )
        selected_claim = bool(root_matches or selected_registry_matches)
        if strict and selected_claim:
            root_registry_agrees = bool(
                len(roots) == 1
                and len(root_matches) == 1
                and len(registries) == 1
                and len(selected_registry_matches) == 1
            )
            ambiguous = len(roots) > 1 or len(registries) > 1
            conflicting = bool(roots and registries and not root_registry_agrees)
            if ambiguous or conflicting:
                raise ValueError(
                    "Mode C physics solver ownership is ambiguous for "
                    f"{solver}: roots={list(roots)!r}, "
                    f"registries={list(registries)!r}"
                )
        # More than one root/registry source is ambiguous even if one happens
        # to match the Current Model.
        if (len(roots) == 1 and len(root_matches) == 1) or (
            not roots and registry_matches
        ):
            owned.append(solver)
    return owned


def _physics_solver_driver_inventory() -> tuple[
    list[str],
    dict[str, list[str]],
    dict[str, list[str]],
]:
    """Build one bounded solver/driver ownership inventory for this scene."""

    try:
        solvers = cmds.ls(type="mmdPhysicsSolver") or []
    except Exception:
        return [], {}, {}
    if isinstance(solvers, (str, bytes)):
        solvers = [solvers]
    scene_solvers = sorted({str(value) for value in solvers})
    drivers_by_solver = {
        solver: _physics_drivers_for_solver(solver) for solver in scene_solvers
    }
    owners_by_driver: dict[str, list[str]] = {}
    for solver, drivers in drivers_by_solver.items():
        for driver in drivers:
            owners_by_driver.setdefault(driver, []).append(solver)
    for owners in owners_by_driver.values():
        owners.sort()
    return scene_solvers, drivers_by_solver, owners_by_driver


def _physics_drivers_for_solver(solver: str) -> list[str]:
    """Return unique drivers connected to one solver's output surface."""

    drivers = []
    seen = set()
    for output_attr in ("outBoneMatrices", "outBoneCount", "outSolved"):
        try:
            connected = cmds.listConnections(
                f"{solver}.{output_attr}",
                source=False,
                destination=True,
                type="mmdPhysicsBoneDriver",
            ) or []
        except Exception:
            continue
        for driver in connected:
            value = str(driver)
            if value not in seen:
                seen.add(value)
                drivers.append(value)
    return sorted(drivers)


def _physics_driver_target_connections(driver: str) -> list[str]:
    """Return rename-safe target-joint message connections for one driver."""
    try:
        if not cmds.attributeQuery(
            "mmd_target_joint_message", node=driver, exists=True
        ):
            return []
        targets = cmds.listConnections(
            f"{driver}.mmd_target_joint_message",
            source=True,
            destination=False,
            type="joint",
        ) or []
    except Exception:
        return []
    if isinstance(targets, (str, bytes)):
        targets = [targets]
    return [str(target) for target in targets]


def _physics_driver_target_joint(driver: str) -> Optional[str]:
    """Resolve exactly one rename-safe target-joint message connection."""

    targets = _physics_driver_target_connections(driver)
    return targets[0] if len(targets) == 1 else None


def _physics_driver_bone_index(
    driver: str,
    *,
    strict: bool = False,
) -> Optional[int]:
    try:
        if not cmds.attributeQuery("inBoneIndex", node=driver, exists=True):
            return None
        value = cmds.getAttr(f"{driver}.inBoneIndex")
        index = int(value)
        if strict:
            if isinstance(value, bool):
                return None
            numeric_value = float(value)
            if not math.isfinite(numeric_value) or numeric_value != index:
                return None
    except (TypeError, ValueError, RuntimeError, OverflowError):
        return None
    return index if index >= 0 else None


def _physics_driver_pre_inputs_exist(driver: str) -> bool:
    try:
        return all(
            bool(cmds.attributeQuery(attribute, node=driver, exists=True))
            for attribute in _PHYSICS_PRE_INPUT_ATTRS.values()
        )
    except Exception:
        return False


def _physics_driver_pre_input_exists(driver: str, attribute: str) -> bool:
    """Return whether one validated physics pre-input plug exists."""

    try:
        return bool(cmds.attributeQuery(attribute, node=driver, exists=True))
    except Exception:
        return False


def _unique_nonphysics_source(plug: str) -> Optional[tuple[str, str]]:
    """Return one direct authored source plug, rejecting known physics nodes."""

    try:
        sources = cmds.listConnections(
            plug,
            source=True,
            destination=False,
            plugs=True,
        ) or []
    except Exception:
        return None
    if isinstance(sources, (str, bytes)) or len(sources) != 1:
        return None
    source_node, separator, source_attr = str(sources[0]).partition(".")
    if not separator or not source_node or not source_attr:
        return None
    try:
        source_type = str(cmds.nodeType(source_node) or "")
    except Exception:
        return None
    if source_type in {
        "mmdPhysicsBoneDriver",
        "mmdPhysicsSolver",
    }:
        return None
    return source_node, source_attr
