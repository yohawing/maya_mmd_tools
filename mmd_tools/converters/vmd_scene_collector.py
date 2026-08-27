"""Minimum Maya scene collector for VMD export.

This collector gathers keyed joint transforms, blendShape weights, and
model-scoped PMX network morph controller weights into the dict contract
consumed by ``VmdExporter``. Bone translation can be converted back to VMD
offsets when a bind-pose map is supplied, and XYZ joint rotations are
converted back to VMD quaternions with jointOrient compensation. Explicit
Bake Timeline requests sample the selected Maya frame range at one-frame intervals:
bones use the native sampler while morph/IK/camera/light tracks advance Maya's
normal Timeline and read current-frame values. Sampling failures block export.
The current character scene is the sole export authority. Import provenance
may remain attached to the scene for diagnostics, but is never materialized
as an export payload.
"""

import json
import math
import struct
import tempfile
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
    ATTR_MMD_MODEL_ROOT,
    ATTR_MMD_MODEL_NAME,
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
from mmd_tools.core.mmd_control_rig_motion import (
    resolve_control_rig_direct_vmd_export_routes,
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
_DEFAULT_CAMERA_INTERPOLATION = b"\x14" * 24
_ATTR_MMD_CAMERA_RIG_TYPE = "mmd_camera_rig_type"
_MMD_CAMERA_AIM_ROLL_RIG_TYPE = "mmd_aim_roll"
_BAKE_TIMELINE_TRACK_TARGETS = frozenset({"character", "camera", "light"})


class VmdIkSceneRepresentationMissingError(ValueError):
    """Source IK exists but Bake Timeline has no scene-owned IK authority."""

    validation_issue_code = "ROUTE_UNRESOLVED"
    validation_issue_path = "ik_show_hide_frames"


class ControlRigDirectVmdExportError(ValueError):
    """A Control Rig direct-export route could not be proven authoritative."""

    validation_issue_code = "ROUTE_UNRESOLVED"

    def __init__(
        self,
        message: str,
        *,
        path: str = "scene.control_rig.direct_vmd_export.route",
    ) -> None:
        self.validation_issue_path = str(path)
        super().__init__(str(message))
_TRANSFORM_EXPORT_ATTRS = ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
_CAMERA_SHAPE_EXPORT_ATTRS = (
    "focalLength",
    "verticalFilmAperture",
    "orthographic",
    "orthographicWidth",
    "centerOfInterest",
)
_RIGLESS_CAMERA_TARGET_PLANE_Y = 0.0
_RIGLESS_CAMERA_MAX_TARGET_DISTANCE = 1000.0
_RIGLESS_CAMERA_DEFAULT_TARGET_DISTANCE = 45.0
_RIGLESS_CAMERA_PLANE_EPSILON = 1e-6
_TRACK_SELECTION_DECISIONS = (
    "omitted_default",
    "omitted_unrepresentable",
    "constant_one_key",
    "authored_sampled",
    "dependency_baked",
    "physics_output_excluded",
)
_UNSUPPORTED_BONE_BAKE_REASON = (
    "This bone has no dedicated Control Rig mapping, so its evaluated motion was baked."
)
# A dependency bake may traverse an importer-owned Maya utility graph, but an
# arbitrary plug-in node or an unowned graph is never a safe authority.  Keep
# this list deliberately explicit; adding a node type is an ownership decision
# and must come with a focused route test.
_SUPPORTED_DEPENDENCY_NODE_TYPES = frozenset(
    {
        "animBlendNodeAdditiveDL",
        "animBlendNodeAdditiveRotation",
        "blendColors",
        "blendWeighted",
        "choice",
        "composeMatrix",
        "condition",
        "decomposeMatrix",
        "eulerToQuat",
        "multMatrix",
        "multiplyDivide",
        "pairBlend",
        "parentConstraint",
        "pointConstraint",
        "plusMinusAverage",
        "quatToEuler",
        "remapValue",
        "unitConversion",
        "aimConstraint",
        "orientConstraint",
        "scaleConstraint",
        "mmdAppend",
        "mmdBoneMorphAccum",
        "mmdCcdIk",
        "mmdControlRigInterop",
        "mmdIkController",
        "mmdMaterialMorphEval",
        "mmdMorphController",
        "mmdPhysicsBoneDriver",
        "mmdPhysicsSolver",
    }
)
_RUNTIME_DEPENDENCY_NODE_TYPES = frozenset(
    {
        "mmdAppend",
        "mmdBoneMorphAccum",
        "mmdCcdIk",
        "mmdControlRigInterop",
        "mmdIkController",
        "mmdMaterialMorphEval",
        "mmdMorphController",
        "mmdPhysicsBoneDriver",
        "mmdPhysicsSolver",
    }
)
_MAYA_DAG_NODE_TYPES = frozenset(
    {
        "camera",
        "joint",
        "light",
        "locator",
        "mesh",
        "nurbsCurve",
        "nurbsSurface",
        "transform",
    }
)
_MAX_TRACK_SELECTION_EVIDENCE = 128
_MAX_KEY_REDUCTION_WITNESSES = 64
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


def _new_key_reduction_report(enabled: bool) -> dict[str, Any]:
    """Create bounded aggregate evidence for streaming exact-run reduction."""

    return {
        "enabled": bool(enabled),
        "algorithm": "exact_maximal_same_signature_runs",
        "sections": {
            section: {
                "input": 0,
                "output": 0,
                "removed": 0,
                "witnesses": [],
                "witness_omitted_count": 0,
            }
            for section in ("bones", "morphs")
        },
    }


class _ExactRunReducer:
    """Retain exact plateau endpoints and protected interior frames online."""

    def __init__(
        self,
        emit,
        signature_keys: Sequence[str],
        protected_frames: Optional[set[int]],
        report: dict[str, Any],
        track: str,
    ):
        self._emit = emit
        self._signature_keys = tuple(signature_keys)
        self._protected_frames = protected_frames or set()
        self._report = report
        self._track = str(track)
        self._run_signature = None
        self._run_first = None
        self._run_last = None
        self._run_input_count = 0
        self._run_output_start = 0
        self._output_count = 0
        self._run_removed_witness = None

    def _signature(self, payload: Mapping[str, Any]) -> tuple[Any, ...]:
        return tuple(payload.get(key) for key in self._signature_keys)

    def _write(self, payload: Mapping[str, Any]) -> None:
        self._emit(payload)
        self._report["output"] += 1
        self._output_count += 1

    def add(self, payload: Mapping[str, Any]) -> None:
        """Consume one frame; sink and comparison failures intentionally escape."""

        self._report["input"] += 1
        signature = self._signature(payload)
        if self._run_first is None:
            self._run_signature = signature
            self._run_first = payload
            self._run_last = payload
            self._run_input_count = 1
            self._run_output_start = self._output_count
            self._write(payload)
            return
        if signature != self._run_signature:
            self._finish_run()
            self._run_signature = signature
            self._run_first = payload
            self._run_last = payload
            self._run_input_count = 1
            self._run_output_start = self._output_count
            self._write(payload)
            return
        previous_last = self._run_last
        previous_frame = int(previous_last["frame_number"])
        first_frame = int(self._run_first["frame_number"])
        if (
            previous_frame != first_frame
            and previous_frame not in self._protected_frames
        ):
            if self._run_removed_witness is None:
                self._run_removed_witness = previous_frame
        self._run_input_count += 1
        self._run_last = payload
        if int(payload["frame_number"]) in self._protected_frames:
            self._write(payload)

    def _finish_run(self) -> None:
        if self._run_first is None:
            return
        first_frame = int(self._run_first["frame_number"])
        last_frame = int(self._run_last["frame_number"])
        if last_frame != first_frame and last_frame not in self._protected_frames:
            self._write(self._run_last)
        removed = self._run_input_count - (
            self._output_count - self._run_output_start
        )
        # Aggregate counts are authoritative; witnesses are deliberately capped.
        witnesses = self._report["witnesses"]
        if (
            removed > 0
            and self._run_removed_witness is not None
            and len(witnesses) < _MAX_KEY_REDUCTION_WITNESSES
            and not any(row["track"] == self._track for row in witnesses)
        ):
            witnesses.append(
                {"track": self._track, "frame": self._run_removed_witness}
            )
        self._run_first = None
        self._run_last = None
        self._run_input_count = 0
        self._run_removed_witness = None

    def finish(self) -> None:
        self._finish_run()
        self._report["removed"] = self._report["input"] - self._report["output"]
        self._report["witness_omitted_count"] = (
            self._report["removed"] - len(self._report["witnesses"])
        )


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


_BAKE_TIMELINE_LAYER_STATE_ATTRS = ("weight", "mute", "solo", "override", "passthrough")
_BAKE_TIMELINE_LAYER_OPTIONAL_STATE_ATTRS = ("rotationAccumulationMode",)


def _bake_timeline_writable_plug(node: str, attribute: str) -> bool:
    """Check the resolved physical plug using Maya API 2.0."""
    try:
        selection = om.MSelectionList()
        selection.add(f"{node}.{attribute}")
        return bool(om.MFnAttribute(selection.getPlug(0).attribute()).writable)
    except Exception:
        return False


def _bake_timeline_layer_chain(layer: str) -> Optional[set[str]]:
    """Validate one layer and its parents through BaseAnimation."""
    chain = set()
    while layer:
        if layer in chain:
            return None
        chain.add(layer)
        try:
            for attribute in _BAKE_TIMELINE_LAYER_STATE_ATTRS:
                plug = f"{layer}.{attribute}"
                if _incoming_connection_state(layer, (attribute,), strict=True) != "none":
                    return None
                if cmds.keyframe(plug, query=True, timeChange=True) or []:
                    return None
            for attribute in _BAKE_TIMELINE_LAYER_OPTIONAL_STATE_ATTRS:
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


def _bake_timeline_direct_curve_source(curve: str, expected_type: str) -> bool:
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


def _bake_timeline_validate_anim_blend(
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
        if attribute in incoming and not _bake_timeline_direct_curve_source(
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
        chain = _bake_timeline_layer_chain(layer)
        if chain is None:
            return None
        valid_layers.update(chain)
    try:
        scene_layers = cmds.ls(type="animLayer") or []
    except Exception:
        return None
    for scene_layer in scene_layers:
        if _bake_timeline_layer_chain(str(scene_layer)) is None:
            return None
    for attribute in ("weightA", "weightB", "accumulationMode"):
        if attribute in incoming and incoming[attribute].split(".", 1)[0] not in valid_layers:
            return None
    return sorted(layers)[0]


def _bake_timeline_authored_input_plug(
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


def _bake_timeline_single_key_bone_route(
    joint: str,
    route: Mapping[str, tuple[str, str]],
) -> Optional[str]:
    """Return ``direct``/``layered`` for a safe one-key source graph."""
    blend_kinds: set[str] = set()
    for attribute in _BONE_EXPORT_ATTRS:
        node, physical_attr = route.get(attribute, (joint, attribute))
        if not _bake_timeline_authored_input_plug(
            str(node), str(physical_attr), attribute
        ) or not _bake_timeline_writable_plug(str(node), str(physical_attr)):
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
            if source_attr != "output" or not _bake_timeline_direct_curve_source(
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
        layer = _bake_timeline_validate_anim_blend(
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

    ``strict`` is used by standard Bake Timeline keyless-track planning.  A failed
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


def _dependency_node_model_local(node: str, target_model: str) -> bool:
    """Return whether a DAG node is owned by the selected model.

    Maya long paths are authoritative in production.  The parent walk is a
    small compatibility fallback for test hosts and for callers that resolved
    a short name before this boundary; it never crosses an ambiguous parent.
    """

    root_path = _canonical_dag_path(target_model) or str(target_model)
    if _dag_path_is_under_root(node, root_path):
        return True
    current = _canonical_dag_path(node) or str(node)
    seen = set()
    while current and current not in seen:
        seen.add(current)
        try:
            parents = cmds.listRelatives(
                current,
                parent=True,
                fullPath=True,
            ) or []
        except Exception:
            return False
        if isinstance(parents, (str, bytes)):
            parents = [parents]
        if len(parents) != 1:
            return False
        parent = _canonical_dag_path(str(parents[0])) or str(parents[0])
        if parent == root_path or parent == str(target_model):
            return True
        current = parent
    return False


def _dependency_node_has_model_marker(node: str, target_model: str) -> bool:
    """Check an explicit importer ownership marker on a DG node."""

    for marker in (ATTR_MMD_MODEL_ROOT, "modelRoot"):
        try:
            if not cmds.attributeQuery(marker, node=node, exists=True):
                continue
        except Exception:
            continue
        plug = f"{node}.{marker}"
        try:
            sources = cmds.listConnections(
                plug,
                source=True,
                destination=False,
                plugs=True,
            ) or []
        except Exception:
            continue
        if isinstance(sources, (str, bytes)):
            sources = [sources]
        if len(sources) == 1:
            source_node = str(sources[0]).split(".", 1)[0]
            if _dependency_node_model_local(source_node, target_model) or (
                _canonical_dag_path(source_node)
                == _canonical_dag_path(target_model)
            ):
                return True
            continue
        # A few Maya message attributes are exposed as the connected node by
        # getAttr in lightweight hosts.  Treat that value as an ownership claim
        # only when it resolves uniquely to the selected model.
        try:
            value = cmds.getAttr(plug)
        except Exception:
            value = None
        if value is not None and not isinstance(value, (list, tuple, dict)):
            if str(value) in {
                str(target_model),
                _canonical_dag_path(target_model) or str(target_model),
            }:
                return True
    return False


def _dependency_connection_plugs(node: str, *, source: bool, destination: bool):
    """Return a normalized Maya connection list for one node or plug."""

    try:
        values = cmds.listConnections(
            node,
            source=source,
            destination=destination,
            plugs=True,
        ) or []
    except Exception as exc:
        raise ValueError(f"dependency graph query failed for {node}: {exc}") from exc
    if isinstance(values, (str, bytes)):
        values = [values]
    return tuple(str(value) for value in values if str(value).strip())


def _classify_unsupported_bone_dependency(
    joint: str,
    target_model: str,
    channels: Sequence[str],
) -> dict[str, Any]:
    """Classify an un-routed bone graph before permitting dependency baking.

    The classifier is deliberately fail-closed.  A local DAG or an explicit
    importer-owned utility may be traversed, while external DAG nodes, shared
    writers, duplicate writers, cycles, and unknown utility types retain a
    dedicated fatal reason.  The returned reason is report-safe and does not
    expose evaluated values.
    """

    joint = str(joint)
    target_model = str(target_model)
    root_path = _canonical_dag_path(target_model) or target_model
    visited: set[str] = set()
    visiting: set[str] = {joint}
    visited_destinations: set[str] = set()
    nodes: list[str] = []
    node_types: dict[str, str] = {}
    plug_provenance: list[dict[str, str]] = []

    def evidence() -> dict[str, Any]:
        runtime_nodes = sorted(
            node for node, node_type in node_types.items()
            if node_type in _RUNTIME_DEPENDENCY_NODE_TYPES
        )
        runtime_node_types = sorted(
            {node_types[node] for node in runtime_nodes}
        )
        return {
            "nodes": tuple(nodes),
            "node_types": tuple(sorted(set(node_types.values()))),
            "node_type_by_node": dict(sorted(node_types.items())),
            "plug_provenance": tuple(plug_provenance),
            "runtime_nodes": tuple(runtime_nodes),
            "runtime_node_types": tuple(runtime_node_types),
        }

    def reject(reason: str, **extra: Any) -> dict[str, Any]:
        return {
            "status": "blocked",
            "reason": str(reason),
            "channels": tuple(str(channel) for channel in channels),
            **evidence(),
            **extra,
        }

    def canonical(node: str) -> Optional[str]:
        value = _canonical_dag_path(node)
        if value:
            return value
        try:
            matches = cmds.ls(node, long=True) or []
        except Exception:
            return None
        if len(matches) != 1:
            return None
        return str(matches[0])

    def inspect(source_plug: str, destination_plug: str) -> Optional[dict[str, Any]]:
        source_node, separator, _source_attr = str(source_plug).rpartition(".")
        destination_node = str(destination_plug).rpartition(".")[0]
        if not separator or not source_node:
            return reject(
                "unknown dependency closure: source plug is invalid",
                source=source_plug,
            )
        source_path = canonical(source_node)
        if not source_path:
            return reject(
                "unknown dependency closure: source node is not unique",
                source=source_plug,
            )
        if source_path in visiting:
            return reject(
                "dependency cycle detected in unsupported bone closure",
                source=source_plug,
            )
        if source_path not in nodes:
            nodes.append(source_path)
        try:
            node_type = str(cmds.nodeType(source_path) or "")
        except Exception as exc:
            return reject(
                f"unknown dependency closure: node type query failed ({exc})",
                source=source_plug,
            )
        if not node_type:
            return reject(
                "unknown dependency closure: node type is unavailable",
                source=source_plug,
            )
        node_types[source_path] = node_type
        plug_provenance.append(
            {
                "source": str(source_plug),
                "destination": str(destination_plug),
                "node": source_path,
                "node_type": node_type,
            }
        )

        try:
            destination_values = _dependency_connection_plugs(
                source_plug,
                source=False,
                destination=True,
            )
        except ValueError as exc:
            return reject(str(exc), source=source_plug)
        if len(destination_values) > 1:
            return reject(
                "shared dependency writer has multiple destinations",
                source=source_plug,
                destinations=destination_values,
            )
        if destination_values:
            for value in destination_values:
                destination_path = canonical(value.rpartition(".")[0])
                if not destination_path:
                    return reject(
                        "unknown dependency closure: destination node is not unique",
                        source=source_plug,
                    )
                visited_destinations.add(destination_path)
                try:
                    destination_type = str(cmds.nodeType(destination_path) or "")
                except Exception as exc:
                    return reject(
                        f"unknown dependency closure: destination type query failed ({exc})",
                        source=source_plug,
                    )
                if destination_type in _MAYA_DAG_NODE_TYPES and not _dependency_node_model_local(
                    destination_path, target_model
                ):
                    return reject(
                        "external/foreign dependency writer is outside the selected model",
                        source=source_plug,
                        destination=destination_path,
                    )
                if destination_type not in _MAYA_DAG_NODE_TYPES and not (
                    destination_type.startswith("animCurve")
                    or destination_type in _SUPPORTED_DEPENDENCY_NODE_TYPES
                ):
                    return reject(
                        "unknown dependency closure node type: " + destination_type,
                        source=source_plug,
                        destination=destination_path,
                    )
        # A source node may fan out through different output components.  An
        # output other than the one being followed is still part of the
        # writer's authority boundary: a foreign DAG or an unrelated DG
        # closure must block the bake even when the selected output itself is
        # model-local.
        try:
            node_destination_values = _dependency_connection_plugs(
                source_path,
                source=False,
                destination=True,
            )
        except ValueError as exc:
            return reject(str(exc), source=source_path)
        for value in node_destination_values:
            destination_path = canonical(value.rpartition(".")[0])
            if not destination_path:
                return reject(
                    "unknown dependency closure: destination node is not unique",
                    source=source_path,
                )
            try:
                destination_type = str(cmds.nodeType(destination_path) or "")
            except Exception as exc:
                return reject(
                    f"unknown dependency closure: destination type query failed ({exc})",
                    source=source_path,
                )
            if destination_type in _MAYA_DAG_NODE_TYPES:
                if not _dependency_node_model_local(destination_path, target_model):
                    return reject(
                        "external/foreign dependency writer is outside the selected model",
                        source=source_path,
                        destination=destination_path,
                    )
                continue
            if destination_type not in _SUPPORTED_DEPENDENCY_NODE_TYPES and not (
                destination_type.startswith("animCurve")
            ):
                return reject(
                    "unknown dependency closure node type: " + destination_type,
                    source=source_path,
                    destination=destination_path,
                )
            if destination_path not in (visiting | visited):
                return reject(
                    "shared/foreign dependency writer leaves the selected closure",
                    source=source_path,
                    destination=destination_path,
                )
        if node_type in _MAYA_DAG_NODE_TYPES and not _dependency_node_model_local(
            source_path, target_model
        ):
            return reject(
                "external/foreign dependency writer is outside the selected model",
                source=source_plug,
            )
        if node_type not in _MAYA_DAG_NODE_TYPES and not node_type.startswith("animCurve"):
            if node_type not in _SUPPORTED_DEPENDENCY_NODE_TYPES:
                return reject(
                    "unknown dependency closure node type: " + node_type,
                    source=source_plug,
                )
            if not _dependency_node_has_model_marker(source_path, target_model):
                # A known utility with a local destination is still accepted:
                # Maya utility nodes generally carry no DAG parent, and their
                # destination locality is the ownership proof available at
                # this boundary.  In a utility chain, the destination utility
                # must already be in the validated downstream closure; this
                # prevents an arbitrary DG node from becoming an owner merely
                # because its type appears in the allowlist.
                destination_path = canonical(destination_node)
                destination_is_local_dag = bool(
                    destination_path
                    and _dependency_node_model_local(destination_path, target_model)
                )
                destination_is_validated_utility = bool(
                    destination_path
                    and destination_path in (visiting | visited)
                )
                if not destination_is_local_dag and not destination_is_validated_utility:
                    return reject(
                        "foreign dependency writer has no selected-model ownership",
                        source=source_plug,
                    )

        if source_path in visited:
            return None
        visited.add(source_path)
        # The native timeline sampler owns animCurve evaluation.  Its normal
        # ``input`` connection is Maya's global time node, which is not part
        # of the selected model closure and must not be mistaken for a
        # foreign writer.
        if node_type.startswith("animCurve"):
            return None
        visiting.add(source_path)
        try:
            try:
                incoming = _dependency_connection_plugs(
                    source_path,
                    source=True,
                    destination=False,
                )
            except ValueError as exc:
                return reject(str(exc), source=source_path)
            for upstream in incoming:
                result = inspect(upstream, source_plug)
                if result is not None:
                    return result
        finally:
            visiting.discard(source_path)
        return None

    for channel in channels:
        destination_plug = f"{joint}.{channel}"
        try:
            sources = _dependency_connection_plugs(
                destination_plug,
                source=True,
                destination=False,
            )
        except ValueError as exc:
            return reject(str(exc))
        if len(sources) != 1:
            if len(sources) > 1:
                return reject(
                    "ambiguous dependency writer has multiple sources",
                    destination=destination_plug,
                    sources=sources,
                )
            return reject(
                "unknown dependency closure: incoming source disappeared",
                destination=destination_plug,
            )
        result = inspect(sources[0], destination_plug)
        if result is not None:
            return result
    return {
        "status": "accepted",
        "reason": "model_local_dependency_closure",
        "channels": tuple(str(channel) for channel in channels),
        **evidence(),
        "root": root_path,
        "visited_destinations": tuple(sorted(visited_destinations)),
    }


def _runtime_route_node_matches(node: str, runtime_nodes: Sequence[str]) -> bool:
    """Return whether a route source is one of the proven runtime nodes."""

    node = str(node)
    canonical = _canonical_dag_path(node) or node
    return canonical in {str(value) for value in runtime_nodes} or node in {
        str(value) for value in runtime_nodes
    }


def _runtime_route_group_complete(
    route: Mapping[str, tuple[str, str]],
    group: str,
    runtime_nodes: Sequence[str],
) -> bool:
    """Validate one complete translate/rotate authoring route.

    A runtime output is not an authoring surface.  Every component of a
    compound must therefore come from one of the importer-owned pre-runtime
    plugs before the native sampler is allowed to consume it.
    """

    if group not in {"translate", "rotate"}:
        return False
    attrs = tuple(f"{group}{axis}" for axis in "XYZ")
    if any(attribute not in route for attribute in attrs):
        return False
    values = [route[attribute] for attribute in attrs]
    if any(not isinstance(value, (tuple, list)) or len(value) != 2 for value in values):
        return False
    if any(not _runtime_route_node_matches(value[0], runtime_nodes) for value in values):
        return False
    route_nodes = {_canonical_dag_path(str(value[0])) or str(value[0]) for value in values}
    if len(route_nodes) != 1:
        return False
    node_types = []
    for node, _attribute in values:
        try:
            node_types.append(str(cmds.nodeType(str(node)) or ""))
        except Exception:
            return False
    if any(not node_type for node_type in node_types):
        return False
    if group == "translate":
        expected_by_type = {
            "mmdAppend": "baseTranslate",
            "mmdBoneMorphAccum": "baseTranslate",
            "mmdPhysicsBoneDriver": "inPreTranslate",
        }
        for (node, attribute), node_type, axis in zip(values, node_types, "XYZ"):
            prefix = expected_by_type.get(node_type)
            if prefix is None or str(attribute) != f"{prefix}{axis}":
                return False
        return True
    # Rotation can be supplied by a grant/accumulator, physics pre-input, or
    # the exact mmdCcdIk input slot selected by the semantic resolver.
    for (node, attribute), node_type, axis in zip(values, node_types, "XYZ"):
        attribute = str(attribute)
        if node_type in {"mmdAppend", "mmdBoneMorphAccum"}:
            if attribute != f"baseRotate{axis}":
                return False
        elif node_type == "mmdPhysicsBoneDriver":
            if attribute != f"inPreRotate{axis}":
                return False
        elif node_type == "mmdCcdIk":
            prefix = "inputRotate["
            suffix = f"].inputRotateElement{axis}"
            if not attribute.startswith(prefix) or not attribute.endswith(suffix):
                return False
        else:
            return False
    slots = {
        str(value[1]).split("].", 1)[0]
        for value in values
        if "inputRotate[" in str(value[1])
    }
    return len(slots) <= 1


def _close_native_samples(native_samples: Any) -> None:
    """Close native sample storage without requiring legacy test fakes to do so."""

    close = getattr(native_samples, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            # Storage cleanup must not mask an export or collection failure.
            pass


def _write_stream_frame(sink: Any, section: str, frame: Mapping[str, Any]) -> None:
    """Write one frame through the narrow VMD stream sink contract."""

    writer = getattr(sink, "write_frame", None)
    if callable(writer):
        writer(section, frame)
        return
    canonical_method = {
        "bones": "write_bone",
        "morphs": "write_morph",
        "cameras": "write_camera",
        "lights": "write_light",
        "shadows": "write_shadow",
        "ik": "write_ik",
    }[section]
    method = getattr(sink, canonical_method, None)
    if not callable(method):
        raise TypeError("VMD stream sink has no write_frame or section writer")
    method(frame)


def _should_emit_morph_frame(frame: Mapping[str, Any]) -> bool:
    """Return whether a Morph frame has a name representable by standard VMD."""

    name = str(frame.get("morph_name", frame.get("name", "")))
    try:
        name.encode("cp932")
    except UnicodeEncodeError:
        return False
    return True


class VmdSceneCollector:
    """Collect minimum VMD-compatible animation data from a Maya scene."""

    def __init__(self, diagnostics_sink=None, bone_channel_sampler=None):
        """Create a collector with optional end-of-collection diagnostics sink.

        The sink receives one small JSON-shaped dictionary after collection;
        it never receives per-frame values.  Keeping it optional preserves the
        existing low-level collector API and keeps the hot loop untouched.
        ``bone_channel_sampler`` is the required Bake Timeline bone sampling
        seam. Native command, protocol, and value failures are fatal for Bake
        Timeline; all output comes from the current scene.
        """

        self._diagnostics_sink = diagnostics_sink
        # Optional native batch sampling is intentionally injected at this
        # seam.  Route discovery, quaternion conversion, VMD dict assembly,
        # and every non-bone track remain Python-owned.
        self._bone_channel_sampler = bone_channel_sampler
        self._diagnostics: dict[str, Any] = {}
        # Standard Bake Timeline physics ownership is scoped to one collection.  A
        # target that cannot be routed through authored/pre-physics channels
        # must not later be mistaken for an ordinary keyless dependency.
        self._bake_timeline_physics_output_excluded_targets: set[str] = set()
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
        if decision in {"omitted_default", "omitted_unrepresentable"} and source_count > 0:
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

    def _record_unencodable_morph_omission(
        self,
        name: str,
        *,
        frame_count: int,
        nonzero_frame_count: int,
    ) -> None:
        """Record one bounded warning aggregate for CP932-incompatible Morph names."""

        report = self._diagnostics.setdefault(
            "omitted_unencodable_morphs",
            {
                "track_count": 0,
                "frame_count": 0,
                "nonzero_frame_count": 0,
                "names": [],
                "reason": "Standard VMD Morph names require CP932",
            },
        )
        normalized_name = str(name)
        if normalized_name not in report["names"]:
            report["names"].append(normalized_name)
            report["names"].sort()
            report["track_count"] = len(report["names"])
        report["frame_count"] += max(0, int(frame_count))
        report["nonzero_frame_count"] += max(0, int(nonzero_frame_count))

    def collect(self, options: Optional[Mapping[str, Any]] = None) -> dict:
        """Collect and publish low-overhead timing diagnostics."""

        started = time.perf_counter()
        self._diagnostics = {}
        self._source_omission_identities = set()
        try:
            options = options or {}
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

    def collect_to_sink(
        self,
        options: Optional[Mapping[str, Any]],
        sink: Any,
    ) -> dict[str, Any]:
        """Stream standard Bake Timeline sections into a VMD writer-compatible sink.

        This path owns Bake Timeline planning and shares the per-track collectors,
        but keeps only one bone track (and one aggregate morph candidate
        spool) alive at a time.  ``sink.finish`` is owned by the caller so the
        caller can validate and promote the private stage atomically.
        """

        if sink is None:
            raise TypeError("Bake Timeline stream collection requires a sink")
        options = options or {}
        export_strategy = str(options.get("export_strategy", "") or "").lower()
        if export_strategy != "bake_timeline":
            raise ValueError("collect_to_sink supports standard Bake Timeline only")
        started = time.perf_counter()
        self._diagnostics = {}
        self._source_omission_identities = set()
        self._bake_timeline_physics_output_excluded_targets = set()
        self._diagnostics["track_selection"] = _new_track_selection()
        exact_run_reduction = bool(options.get("bake_timeline_exact_run_reduction", True))
        key_reduction = _new_key_reduction_report(exact_run_reduction)
        self._diagnostics["key_reduction"] = key_reduction
        section_counts = {
            "bones": 0,
            "morphs": 0,
            "cameras": 0,
            "lights": 0,
            "shadows": 0,
            "ik": 0,
        }
        generated_bone_counts: dict[str, int] = {}
        def emit(section: str, frame: Mapping[str, Any]) -> None:
            if section == "morphs" and not _should_emit_morph_frame(frame):
                value = float(frame.get("value", frame.get("weight", 0.0)))
                self._record_unencodable_morph_omission(
                    str(frame.get("morph_name", frame.get("name", ""))),
                    frame_count=1,
                    nonzero_frame_count=int(value != 0.0),
                )
                return
            _write_stream_frame(sink, section, frame)
            section_counts[section] += 1
            if section == "bones":
                bone_name = str(frame.get("bone_name") or "")
                generated_bone_counts[bone_name] = (
                    generated_bone_counts.get(bone_name, 0) + 1
                )

        try:
            target_model = options.get("target_model") or options.get("model_root")
            track_targets = _resolve_bake_timeline_track_targets(options)
            include_character = "character" in track_targets
            include_camera = "camera" in track_targets
            include_light = "light" in track_targets
            joints = (
                list(options.get("joints") or self._find_joints(target_model))
                if include_character
                else []
            )
            rotation_context_joints = list(joints)
            blend_shapes = list(
                options.get("blend_shapes") or self._find_blend_shapes(target_model)
            ) if include_character else []
            discover_legacy_sections = (
                "track_targets" not in options and "export_target" not in options
            )
            camera_candidates = (
                self._resolve_tagged_track(options, "cameras", ATTR_MMD_CAMERA, None)
                if include_camera or discover_legacy_sections
                else []
            )
            light_candidates = (
                self._resolve_tagged_track(options, "lights", ATTR_MMD_LIGHT, None)
                if include_light or discover_legacy_sections
                else []
            )
            cameras = camera_candidates if include_camera else []
            lights = light_candidates if include_light else []
            unsupported_cameras = camera_candidates if discover_legacy_sections and not include_camera else []
            unsupported_lights = light_candidates if discover_legacy_sections and not include_light else []
            if include_camera and not cameras:
                raise RuntimeError(
                    "VMD Camera export target has no tagged mmd_camera node; "
                    "Reason: an explicit Camera target must resolve exactly one "
                    "scene-owned camera"
                )
            if include_light and not lights:
                raise RuntimeError(
                    "VMD Light export target has no tagged mmd_light node; "
                    "Reason: an explicit Light target must resolve exactly one "
                    "scene-owned light"
                )
            start_frame, end_frame = _resolve_collection_frame_range(options)
            motion_scale = float(options.get("motion_scale", 1.0) or 1.0)
            bone_bind_poses = options.get("bone_bind_poses") or {}
            maya_time_to_vmd = _scene_maya_time_to_vmd_frame()
            progress_callback = options.get("_progress_callback")
            cancel_requested = options.get("cancel_requested")
            direct_control_rig_plan = (
                self._control_rig_direct_export_plan(target_model, joints)
                if include_character
                else None
            )
            if direct_control_rig_plan is not None and target_model:
                rotation_context_joints = list(self._find_joints(target_model))
            direct_ik_routes = (
                direct_control_rig_plan.get("ik_state_routes")
                if direct_control_rig_plan is not None
                else None
            )
            protected_ik_frames = (
                self._bake_timeline_protected_ik_frames(
                    target_model,
                    start_frame,
                    end_frame,
                    maya_time_to_vmd,
                    ik_routes_by_name=direct_ik_routes,
                )
                if include_character
                else set()
            )
            if direct_control_rig_plan is None and include_character:
                self._control_rig_dense_export(target_model)
            rotation_interpolation = (
                self._rotation_time_curve_interpolation(target_model)
                if include_character
                else None
            )
            selector_key_times_by_joint = None
            if not include_character:
                authored_routes = {}
            elif direct_control_rig_plan is None:
                authored_routes = self._scene_authored_input_routes(
                    joints,
                    target_model,
                    strict_bake_timeline=True,
                )
            else:
                joints = direct_control_rig_plan["joints"]
                authored_routes = direct_control_rig_plan["value_routes"]
                selector_key_times_by_joint = direct_control_rig_plan[
                    "selector_key_times_by_joint"
                ]
            bake_timeline_dense_frames = self._bake_timeline_dense_frame_samples(
                joints,
                blend_shapes,
                cameras,
                lights,
                target_model,
                authored_routes,
                start_frame,
                end_frame,
                selector_key_times_by_joint=selector_key_times_by_joint,
            )
            if (
                not include_character
                and (include_camera or include_light)
                and bake_timeline_dense_frames is None
            ):
                current_time = _query_current_time()
                if current_time is None or not math.isfinite(current_time):
                    current_time = 0.0
                bake_timeline_dense_frames = [max(0, int(round(current_time)))]
            self._diagnostics["route_provenance_dense_planning"] = {
                "joint_count": len(joints),
                "blend_shape_count": len(blend_shapes),
                "camera_count": len(cameras),
                "light_count": len(lights),
                "authored_route_count": len(authored_routes),
                "dense_frame_count": len(bake_timeline_dense_frames or ()),
                "streaming": True,
            }
            if direct_control_rig_plan is not None:
                self._diagnostics["route_provenance_dense_planning"][
                    "control_rig_direct_export"
                ] = direct_control_rig_plan["diagnostics"]

            begin_section = getattr(sink, "begin_section", None)
            if not callable(begin_section):
                raise TypeError("VMD stream sink has no begin_section")
            begin_section("bones")
            if include_character:
                self.collect_bone_frames(
                    joints,
                    start_frame,
                    end_frame,
                    motion_scale=motion_scale,
                    bone_bind_poses=bone_bind_poses,
                    input_routes=authored_routes,
                    dense_sample=True,
                    force_dense_sample=True,
                    time_converter=maya_time_to_vmd,
                    rotation_interpolation=rotation_interpolation,
                    dense_frame_samples=bake_timeline_dense_frames,
                    bone_channel_sampler=self._bone_channel_sampler,
                    frame_sink=lambda frame: emit("bones", frame),
                    exact_run_reduction=exact_run_reduction,
                    protected_vmd_frames=protected_ik_frames,
                    key_reduction_report=key_reduction["sections"]["bones"],
                    selector_key_times_by_joint=selector_key_times_by_joint,
                    rotation_context_joints=rotation_context_joints,
                )
                self._finalize_direct_dependency_bake_diagnostics(
                    start_frame,
                    end_frame,
                    bake_timeline_dense_frames,
                    maya_time_to_vmd,
                    generated_bone_counts,
                )
            begin_section("morphs")
            if include_character:
                self.collect_morph_frames(
                    blend_shapes,
                    start_frame,
                    end_frame,
                    time_converter=maya_time_to_vmd,
                    target_model=target_model,
                    dense_sample=True,
                    dense_frame_samples=bake_timeline_dense_frames,
                    timeline_evaluation=True,
                    frame_sink=lambda frame: emit("morphs", frame),
                    exact_run_reduction=exact_run_reduction,
                    protected_vmd_frames=protected_ik_frames,
                    key_reduction_report=key_reduction["sections"]["morphs"],
                    morph_channel_sampler=self._bone_channel_sampler,
                )
            begin_section("cameras")
            if include_camera:
                self.collect_camera_frames(
                    cameras,
                    start_frame,
                    end_frame,
                    motion_scale=motion_scale,
                    time_converter=maya_time_to_vmd,
                    dense_sample=True,
                    dense_frame_samples=bake_timeline_dense_frames,
                    timeline_evaluation=True,
                    frame_sink=lambda frame: emit("cameras", frame),
                    progress_callback=progress_callback,
                    cancel_requested=cancel_requested,
                )
            begin_section("lights")
            if include_light:
                self.collect_light_frames(
                    lights,
                    start_frame,
                    end_frame,
                    time_converter=maya_time_to_vmd,
                    dense_sample=True,
                    dense_frame_samples=bake_timeline_dense_frames,
                    timeline_evaluation=True,
                    frame_sink=lambda frame: emit("lights", frame),
                    progress_callback=progress_callback,
                    cancel_requested=cancel_requested,
                )
            if unsupported_cameras or unsupported_lights:
                self._diagnostics["unsupported_bake_timeline_sections"] = {
                    "cameras": len(unsupported_cameras) if not include_camera else 0,
                    "lights": len(unsupported_lights) if not include_light else 0,
                }
            begin_section("shadows")
            begin_section("ik")
            if include_character:
                self.collect_ik_show_hide_frames(
                    target_model,
                    start_frame,
                    end_frame,
                    time_converter=maya_time_to_vmd,
                    dense_sample=False,
                    dense_frame_samples=None,
                    timeline_evaluation=False,
                    frame_sink=lambda frame: emit("ik", frame),
                    ik_routes_by_name=direct_ik_routes,
                )
            self._diagnostics["section_counts"] = dict(section_counts)
            self._diagnostics["streaming"] = {
                "enabled": True,
                "peak_buffered_track_frames": "one_track",
                "morph_candidate_spool": bool(
                    self._diagnostics.get("morph_collection", {}).get(
                        "candidate_spool", False
                    )
                ),
            }
            self._diagnostics["status"] = "completed"
            self._diagnostics["total"] = {
                "wall_sec": round(time.perf_counter() - started, 6),
            }
            validation_frame_range = None
            if bake_timeline_dense_frames:
                validation_frame_range = (
                    _vmd_frame_number(bake_timeline_dense_frames[0], maya_time_to_vmd),
                    _vmd_frame_number(bake_timeline_dense_frames[-1], maya_time_to_vmd),
                )
            elif start_frame is not None and end_frame is not None:
                validation_frame_range = (
                    _vmd_frame_number(start_frame, maya_time_to_vmd),
                    _vmd_frame_number(end_frame, maya_time_to_vmd),
                )
            return {
                "model_name": str(options.get("model_name") or self._model_name(target_model)),
                "validation_frame_range": validation_frame_range,
                "section_counts": dict(section_counts),
                "diagnostics": self.diagnostics,
            }
        except BaseException as exc:
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
                ``export_strategy`` (legacy input, normalized to Bake Timeline), ``model_name``,
                ``motion_scale``, and
                ``bone_bind_poses``. Automatic joint and blendShape discovery
                is scoped to the selected model root; camera/light discovery
                remains scene-level. Explicit node lists remain authoritative.
        """
        options = options or {}
        self._bake_timeline_physics_output_excluded_targets = set()
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
        dense_control_rig_export = self._control_rig_dense_export(target_model)
        rotation_interpolation = self._rotation_time_curve_interpolation(target_model)
        authored_routes = self._scene_authored_input_routes(
            joints,
            target_model,
        )

        self._diagnostics["route_provenance_dense_planning"] = {
            "wall_sec": round(time.perf_counter() - planning_started, 6),
            "joint_count": len(joints),
            "blend_shape_count": len(blend_shapes),
            "camera_count": len(cameras),
            "light_count": len(lights),
            "authored_route_count": len(authored_routes),
            "dense_frame_count": 0,
        }

        bone_started = time.perf_counter()
        bone_frames = self.collect_bone_frames(
            joints,
            start_frame,
            end_frame,
            motion_scale=motion_scale,
            bone_bind_poses=bone_bind_poses,
            input_routes=authored_routes,
            dense_sample=dense_control_rig_export,
            force_dense_sample=False,
            time_converter=maya_time_to_vmd,
            rotation_interpolation=rotation_interpolation,
            dense_frame_samples=None,
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
            dense_sample=False,
            dense_frame_samples=None,
            timeline_evaluation=False,
            morph_channel_sampler=None,
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
            motion_scale=motion_scale,
            time_converter=maya_time_to_vmd,
            dense_sample=False,
            dense_frame_samples=None,
            timeline_evaluation=False,
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
            dense_sample=False,
            dense_frame_samples=None,
            timeline_evaluation=False,
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
            dense_sample=False,
            dense_frame_samples=None,
            timeline_evaluation=False,
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
            "bone_frames": bone_frames,
            "morph_frames": morph_frames,
            "camera_frames": camera_frames,
            "light_frames": light_frames,
            "ik_show_hide_frames": ik_frames,
        }

    def _bake_timeline_dense_frame_samples(
        self,
        joints: Sequence[str],
        blend_shapes: Sequence[str],
        cameras: Sequence[str],
        lights: Sequence[str],
        target_model: Optional[str],
        input_routes: Mapping[str, Mapping[str, tuple[str, str]]],
        start_frame: Optional[float],
        end_frame: Optional[float],
        *,
        selector_key_times_by_joint: Optional[Mapping[str, Sequence[float]]] = None,
    ) -> Optional[list[int]]:
        """Build one Maya-time sample range shared by Bake Timeline tracks."""
        keyed_times = []
        for joint in joints:
            long_name = (cmds.ls(joint, long=True) or [joint])[0]
            selector_times = (
                selector_key_times_by_joint.get(str(long_name))
                if selector_key_times_by_joint is not None
                else None
            )
            if selector_times is None:
                keyed_times.extend(
                    _routed_key_times(joint, input_routes.get(str(long_name), {}))
                )
            else:
                keyed_times.extend(selector_times)
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
            for ancestor in _transform_ancestors(camera):
                keyed_times.extend(_key_times(ancestor, _TRANSFORM_EXPORT_ATTRS))
            if camera_root:
                keyed_times.extend(_key_times(camera_root, _BONE_EXPORT_ATTRS))
            if camera_target:
                keyed_times.extend(_key_times(camera_target, _TRANSFORM_EXPORT_ATTRS))
            if camera_shape:
                keyed_times.extend(_key_times(camera_shape, _CAMERA_SHAPE_EXPORT_ATTRS))
        for light in lights:
            keyed_times.extend(_key_times(light, _LIGHT_COLOR_ATTRS + _LIGHT_ROTATE_ATTRS))
            color_node, color_attrs = _light_color_source(light)
            if color_node != light:
                keyed_times.extend(_key_times(color_node, color_attrs))
        return _dense_frame_samples(keyed_times, start_frame, end_frame)

    @staticmethod
    def _bake_timeline_protected_ik_frames(
        target_model: Optional[str],
        start_frame: Optional[float],
        end_frame: Optional[float],
        time_converter,
        *,
        ik_routes_by_name: Optional[Mapping[str, tuple[str, str]]] = None,
    ) -> set[int]:
        """Return global IK key/transition frames that numeric tracks retain."""

        if not target_model:
            return set()
        routes = (
            dict(ik_routes_by_name)
            if ik_routes_by_name is not None
            else {
                name: (node, "enabled")
                for name, node in collect_ik_nodes_by_bone_name(
                    target_model=target_model
                ).items()
            }
        )
        maya_frames = _filter_frame_range(
            [
                frame
                for node, attribute in routes.values()
                for frame in _key_times(node, (attribute,))
            ],
            start_frame,
            end_frame,
        )
        return {_vmd_frame_number(frame, time_converter) for frame in maya_frames}

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
        bone_channel_sampler=None,
        frame_sink=None,
        exact_run_reduction: bool = False,
        protected_vmd_frames: Optional[set[int]] = None,
        key_reduction_report: Optional[dict[str, Any]] = None,
        selector_key_times_by_joint: Optional[Mapping[str, Sequence[float]]] = None,
        rotation_context_joints: Optional[Sequence[str]] = None,
    ) -> list[dict]:
        """Collect keyed or one-frame-sampled local joint transforms.

        ``dense_sample`` is retained for the baked control-rig route, where a
        rotation-time curve may intentionally keep sparse VMD keys. Bake Timeline
        uses ``force_dense_sample`` for numeric pose export; interpolation comes
        from the current character scene's registered curves.
        """
        bone_bind_poses = bone_bind_poses or {}
        input_routes = input_routes or {}
        time_converter = time_converter or _scene_maya_time_to_vmd_frame()
        # Direct Control Rig export intentionally limits emitted tracks to keyed
        # Controls.  Rotation conversion still needs the complete PMX parent
        # hierarchy; otherwise a selected child is converted as if it were a
        # root whenever its keyless parent was omitted.
        context_joints = (
            rotation_context_joints if rotation_context_joints is not None else joints
        )
        if selector_key_times_by_joint is not None:
            _validate_direct_rotation_export_indices(context_joints, joints)
        rotation_context = _build_rotation_export_context(context_joints)
        rotation_interpolation = rotation_interpolation or {}
        native_samples = None
        native_bulk_track_api = False
        native_bulk_track_count = 0
        native_bulk_track_frame_count = 0
        scalar_native_value_read_count = 0

        def update_native_track_diagnostics() -> None:
            report = self._diagnostics.get("native_sampler")
            if not isinstance(report, dict):
                return
            report.update(
                {
                    "bulk_track_api": native_bulk_track_api,
                    "bulk_track_count": native_bulk_track_count,
                    "bulk_track_frame_count": native_bulk_track_frame_count,
                    "scalar_native_value_read_count": scalar_native_value_read_count,
                }
            )
            if native_samples is not None:
                sample_diagnostics = getattr(native_samples, "diagnostics", None)
                if callable(sample_diagnostics):
                    sample_diagnostics = sample_diagnostics()
                if isinstance(sample_diagnostics, dict) and (
                    "python_scalar_unpack_count" in sample_diagnostics
                ):
                    report[
                        "python_scalar_unpack_count"
                    ] = sample_diagnostics["python_scalar_unpack_count"]

        frames = [] if frame_sink is None else None
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
        stream_seen_bone_frames = None
        if dense_sample:
            all_keyed = []
            for joint in joints:
                long_names = cmds.ls(joint, long=True) or [joint]
                route = input_routes.get(str(long_names[0]), {})
                selector_times = (
                    selector_key_times_by_joint.get(str(long_names[0]))
                    if selector_key_times_by_joint is not None
                    else None
                )
                joint_keyed = (
                    _routed_key_times(joint, route)
                    if selector_times is None
                    else list(selector_times)
                )
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
                        long_name in self._bake_timeline_physics_output_excluded_targets
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
                        single_kind = _bake_timeline_single_key_bone_route(joint, route)
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
                    not in self._bake_timeline_physics_output_excluded_targets
                    for joint in joints
                )
            ):
                self._diagnostics["native_sampler"] = {
                    "available": False,
                    "used": False,
                    "fatal": True,
                    "fallback_reason": "Bake Timeline native sampler was not provided",
                }
                self._emit_diagnostics()
                raise RuntimeError("Bake Timeline native bone sampling is unavailable")
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
                    not in self._bake_timeline_physics_output_excluded_targets
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
                            sampler_diagnostics = getattr(
                                bone_channel_sampler,
                                "last_diagnostics",
                                None,
                            )
                            detail_parts = []
                            if isinstance(sampler_diagnostics, Mapping):
                                plugin_status = sampler_diagnostics.get(
                                    "plugin_load_status"
                                )
                                plugin_path = sampler_diagnostics.get("plugin_path")
                                plugin_error = sampler_diagnostics.get(
                                    "plugin_load_error"
                                )
                                if plugin_status:
                                    detail_parts.append(
                                        f"plugin_load_status={plugin_status}"
                                    )
                                if plugin_path:
                                    detail_parts.append(f"plugin_path={plugin_path}")
                                if plugin_error:
                                    detail_parts.append(f"plugin_error={plugin_error}")
                            detail = (
                                f" ({', '.join(detail_parts)})"
                                if detail_parts
                                else ""
                            )
                            raise RuntimeError(
                                "native sampler is unavailable"
                                f"{detail}. Rebuild the C++ plug-in for this Maya "
                                "version and restart Maya."
                            )
                        sampler_method = getattr(
                            bone_channel_sampler,
                            "sample_dense_bone_channels",
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
                        native_bulk_track_api = callable(
                            getattr(native_samples, "bone_track", None)
                        )
                        update_native_track_diagnostics()
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
                            f"Bake Timeline native bone sampling failed: {exc}"
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

        if frame_sink is not None:
            stream_seen_bone_frames = {
                name: set()
                for name, providers in bone_output_providers.items()
                if len(providers) > 1
            }

        try:
            static_sample = (
                _bake_timeline_earliest_integer_sample(
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
                        not in self._bake_timeline_physics_output_excluded_targets
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

        def read_value(
            joint,
            attr,
            frame_number,
            route,
            use_native=True,
        ):
            nonlocal native_samples, scalar_native_value_read_count
            if use_native and native_samples is not None:
                try:
                    scalar_native_value_read_count += 1
                    value = float(native_samples.value(joint, attr, frame_number))
                    if not math.isfinite(value):
                        raise ValueError(
                            f"non-finite native value for {joint}.{attr}"
                        )
                    return value
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
                        f"Bake Timeline native bone value failed for {joint}.{attr}: {exc}"
                    ) from exc
            value = float(_routed_plug_float(joint, attr, frame_number, route))
            if not math.isfinite(value):
                raise RuntimeError(
                    f"Bake Timeline bone value is non-finite for {joint}.{attr}"
                )
            return value

        try:
            for joint in joints:
                bone_name = self._mmd_bone_name(joint)
                bind_pose = _resolve_bind_pose(bone_bind_poses, bone_name, joint)
                long_names = cmds.ls(joint, long=True) or [joint]
                long_name = str(long_names[0])
                if long_name in self._bake_timeline_physics_output_excluded_targets:
                    # The physics solver's final output is intentionally outside
                    # standard Bake Timeline.  An incomplete pre-physics route cannot
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
                dependency_multi_key = bool(
                    force_dense_sample
                    and not single_key
                    and joint in keyless_dependency_joints
                )
                preserve_sparse_rotation = (
                    not force_dense_sample
                    and bone_name in rotation_interpolation
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
                bulk_components = None
                if (
                    native_bulk_track_api
                    and native_samples is not None
                    and not single_key
                    and keyed_frames
                ):
                    try:
                        native_track = native_samples.bone_track(joint, keyed_frames)
                        expected_track_frames = tuple(
                            float(frame) for frame in keyed_frames
                        )
                        if tuple(native_track.frames) != expected_track_frames:
                            raise RuntimeError(
                                "native bone track returned unexpected frames"
                            )
                        component_accessor = getattr(
                            native_track,
                            "_components_for_collector",
                            None,
                        )
                        if callable(component_accessor):
                            bulk_components = tuple(component_accessor())
                        else:
                            bulk_components = tuple(
                                native_track.component(attr)
                                for attr in _BONE_EXPORT_ATTRS
                            )
                        if (
                            len(bulk_components) != len(_BONE_EXPORT_ATTRS)
                            or any(
                                len(component) != len(expected_track_frames)
                                for component in bulk_components
                            )
                        ):
                            raise RuntimeError(
                                "native bone track returned invalid component arrays"
                            )
                        if any(
                            not math.isfinite(float(value))
                            for component in bulk_components
                            for value in component
                        ):
                            raise RuntimeError(
                                "native bone track returned non-finite values"
                            )
                    except BaseException as exc:
                        if native_samples is not None:
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
                        update_native_track_diagnostics()
                        self._emit_diagnostics()
                        if not isinstance(exc, Exception):
                            raise
                        raise RuntimeError(
                            f"Bake Timeline native bone track failed for {joint}: {exc}"
                        ) from exc
                    native_bulk_track_count += 1
                    native_bulk_track_frame_count += len(keyed_frames)
                track_frames = [] if frame_sink is not None else None
                protected_track_frames = set(protected_vmd_frames or ())
                protected_track_frames.update(
                    _vmd_frame_number(frame, time_converter) for frame in sparse_frames
                )
                reducer = None
                if frame_sink is not None and exact_run_reduction:
                    reducer = _ExactRunReducer(
                        frame_sink,
                        ("position", "rotation", "interpolation"),
                        protected_track_frames,
                        key_reduction_report,
                        bone_name,
                    )

                def emit_stream_payload(stream_payload, *, reduce=True):
                    seen_frames = stream_seen_bone_frames.get(bone_name)
                    if seen_frames is not None:
                        stream_frame_number = stream_payload["frame_number"]
                        if stream_frame_number in seen_frames:
                            return
                        seen_frames.add(stream_frame_number)
                    if reducer is not None and reduce:
                        reducer.add(stream_payload)
                    else:
                        if key_reduction_report is not None:
                            key_reduction_report["input"] += 1
                            key_reduction_report["output"] += 1
                        frame_sink(stream_payload)

                constant_first = None
                constant_signature = None
                constant_varied = False

                def build_payload(track_index, frame_number):
                    if bulk_components is None:
                        rotation = _maya_joint_rotate_to_vmd_quaternion(
                            joint,
                            read_value(
                                joint, "rotateX", frame_number, route, not single_key
                            ),
                            read_value(
                                joint, "rotateY", frame_number, route, not single_key
                            ),
                            read_value(
                                joint, "rotateZ", frame_number, route, not single_key
                            ),
                            rotation_context.get(str(long_names[0])),
                        )
                        position_values = (
                            read_value(
                                joint,
                                "translateX",
                                frame_number,
                                route,
                                not single_key,
                            ),
                            read_value(
                                joint,
                                "translateY",
                                frame_number,
                                route,
                                not single_key,
                            ),
                            read_value(
                                joint,
                                "translateZ",
                                frame_number,
                                route,
                                not single_key,
                            ),
                        )
                    else:
                        rotation = _maya_joint_rotate_to_vmd_quaternion(
                            joint,
                            float(bulk_components[3][track_index]),
                            float(bulk_components[4][track_index]),
                            float(bulk_components[5][track_index]),
                            rotation_context.get(str(long_names[0])),
                        )
                        position_values = (
                            float(bulk_components[0][track_index]),
                            float(bulk_components[1][track_index]),
                            float(bulk_components[2][track_index]),
                        )
                    vmd_frame = _vmd_frame_number(frame_number, time_converter)
                    payload = {
                            "bone_name": bone_name,
                            "frame_number": vmd_frame,
                            "position": _maya_translate_to_vmd_position(
                                position_values,
                                bind_pose,
                                motion_scale,
                            ),
                            "rotation": rotation,
                        }
                    interpolation = rotation_interpolation.get(bone_name, {}).get(vmd_frame)
                    if interpolation is not None:
                        payload["interpolation"] = interpolation
                    return payload

                def iter_payloads():
                    last_vmd_frame = None
                    for track_index, frame_number in enumerate(keyed_frames):
                        vmd_frame = _vmd_frame_number(frame_number, time_converter)
                        if track_frames is not None and vmd_frame == last_vmd_frame:
                            continue
                        last_vmd_frame = vmd_frame
                        yield frame_number, build_payload(track_index, frame_number)

                for frame_number, payload in iter_payloads():
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
                    if track_frames is not None:
                        if direct_multi_key or dependency_multi_key:
                            signature = (payload["position"], payload["rotation"])
                            if constant_first is None:
                                constant_first = payload
                                constant_signature = signature
                            elif signature != constant_signature:
                                constant_varied = True
                        else:
                            emit_stream_payload(payload)
                    else:
                        frames.append(payload)
                if (direct_multi_key or dependency_multi_key) and constant_varied:
                    # Reuse the same detached track for the bounded second pass
                    # so protected interiors retain the original semantics.
                    for _frame_number, payload in iter_payloads():
                        emit_stream_payload(payload)
                if force_dense_sample and not single_key and not direct_multi_key:
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
                    if keyless_reason:
                        self._update_direct_dependency_bake_selection(
                            bone_name,
                            decision,
                            decision in {"omitted_default", "constant_one_key"},
                            planned_key_count,
                        )
                if track_frames is not None:
                    if direct_multi_key and not constant_varied and constant_first:
                        is_default = constant_signature == (
                            (0.0, 0.0, 0.0),
                            (0.0, 0.0, 0.0, 1.0),
                        )
                        self._record_track_selection(
                            "bone",
                            bone_name,
                            "omitted_default" if is_default else "constant_one_key",
                            "dense_exact_constant",
                            len(sparse_frames),
                            0 if is_default else 1,
                        )
                        if not is_default:
                            emit_stream_payload(constant_first, reduce=False)
                    elif dependency_multi_key and not constant_varied and constant_first:
                        is_default = constant_signature == (
                            (0.0, 0.0, 0.0),
                            (0.0, 0.0, 0.0, 1.0),
                        )
                        decision = "omitted_default" if is_default else "constant_one_key"
                        reason = (
                            "unsupported_dependency_static_default"
                            if is_default
                            else "unsupported_dependency_static_non_default"
                        )
                        bone_dense_diagnostic_rows[bone_name] = (
                            decision,
                            reason,
                            0,
                            0 if is_default else 1,
                        )
                        self._update_direct_dependency_bake_selection(
                            bone_name,
                            decision,
                            True,
                            0 if is_default else 1,
                        )
                        if not is_default:
                            emit_stream_payload(constant_first, reduce=False)
                    elif direct_multi_key and constant_varied:
                        self._record_track_selection(
                            "bone",
                            bone_name,
                            "authored_sampled",
                            "multiple_source_keys",
                            len(sparse_frames),
                            len(dense_frames or ()) if dense_sample else len(sparse_frames),
                        )
                        if reducer is not None:
                            reducer.finish()
                    elif dependency_multi_key and constant_varied:
                        bone_dense_diagnostic_rows[bone_name] = (
                            "dependency_baked",
                            keyless_dependency_joints[joint],
                            0,
                            len(dense_frames or ()) if dense_sample else len(sparse_frames),
                        )
                        self._update_direct_dependency_bake_selection(
                            bone_name,
                            "dependency_baked",
                            False,
                            len(dense_frames or ()) if dense_sample else len(sparse_frames),
                        )
                        if reducer is not None:
                            reducer.finish()
                    elif reducer is not None:
                        reducer.finish()
        finally:
            update_native_track_diagnostics()
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
        if frame_sink is not None:
            return []
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
        frame_sink=None,
        ik_routes_by_name: Optional[Mapping[str, tuple[str, str]]] = None,
    ) -> list[dict]:
        """Collect keyed owned ``mmdCcdIk.enabled`` values as VMD properties."""
        if not target_model:
            return []
        time_converter = time_converter or _scene_maya_time_to_vmd_frame()
        routes_by_name = (
            dict(ik_routes_by_name)
            if ik_routes_by_name is not None
            else {
                name: (node, "enabled")
                for name, node in collect_ik_nodes_by_bone_name(
                    target_model=target_model
                ).items()
            }
        )
        all_keyed_frames = sorted(
            {
                frame
                for node, attribute in routes_by_name.values()
                for frame in _key_times(node, (attribute,))
            }
        )
        keyed_frames = (
            sorted(set(dense_frame_samples))
            if dense_sample
            and dense_frame_samples is not None
            and routes_by_name
            else _filter_frame_range(
                all_keyed_frames,
                start_frame,
                end_frame,
            )
        )
        if dense_sample and dense_frame_samples and routes_by_name and not all_keyed_frames:
            first_sample = float(dense_frame_samples[0])
            if timeline_evaluation:
                with _MayaTimelineReader() as initial_reader:
                    initial_reader.set_frame(first_sample)
                    all_enabled = all(
                        bool(_current_plug_float(node, attribute))
                        for node, attribute in routes_by_name.values()
                    )
            else:
                all_enabled = all(
                    bool(_plug_float(node, attribute, first_sample))
                    for node, attribute in routes_by_name.values()
                )
            if all_enabled:
                # A keyless production rig defaults to enabled=True.  Dense
                # sampling must not manufacture a redundant all-ON property
                # section that was absent from the source motion.
                return []
        frames = [] if frame_sink is None else None
        emitted_frames = set() if frame_sink is not None else None

        def emit(payload: dict) -> None:
            frame_number = payload["frame_number"]
            if frame_sink is None:
                frames.append(payload)
                return
            if frame_number in emitted_frames:
                return
            emitted_frames.add(frame_number)
            frame_sink(payload)
        timeline_reader = _MayaTimelineReader() if timeline_evaluation else None

        def read_enabled(node: str, attribute: str, frame: float) -> bool:
            if timeline_reader is not None:
                timeline_reader.set_frame(frame)
                return bool(_current_plug_float(node, attribute))
            return bool(_plug_float(node, attribute, frame))

        baseline_time = _ik_baseline_time(start_frame, end_frame)
        context = timeline_reader or nullcontext()
        with context:
            if (
                not dense_sample
                and routes_by_name
                and baseline_time is not None
                and baseline_time not in all_keyed_frames
            ):
                baseline_frame = _vmd_frame_number(baseline_time, time_converter)
                if baseline_frame >= 0:
                    baseline_states = [
                        (name, read_enabled(node, attribute, baseline_time))
                        for name, (node, attribute) in sorted(routes_by_name.items())
                    ]
                    # A keyless production rig has enabled=True as its default.
                    # Omitting that redundant ON section keeps the exported VMD
                    # faithful to a source with no IK show/hide property frames.
                    # Keep the baseline when a solver is OFF or any later key
                    # exists; those states need an explicit VMD representation.
                    if all_keyed_frames or any(not state for _, state in baseline_states):
                        emit(
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
                emit(
                    {
                        "frame_number": vmd_frame,
                        "visible": True,
                        "ik_states": [
                            (name, read_enabled(node, attribute, frame))
                            for name, (node, attribute) in sorted(routes_by_name.items())
                        ],
                    }
                )
        if frame_sink is not None:
            return []
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

    def _update_direct_dependency_bake_selection(
        self,
        bone_name: str,
        decision: str,
        static: bool,
        planned_key_count: int,
    ) -> None:
        """Persist dependency selection without bounded evidence joins."""

        diagnostics = self._diagnostics.get("control_rig_direct_export")
        if not isinstance(diagnostics, Mapping):
            return
        rows = diagnostics.get("dependency_baked")
        if not isinstance(rows, list):
            return
        _section, normalized_name = _normalize_track_selection_identity(
            "bone", bone_name
        )
        for row in rows:
            if not isinstance(row, dict):
                continue
            _row_section, row_name = _normalize_track_selection_identity(
                "bone", row.get("bone")
            )
            if row_name != normalized_name:
                continue
            row["decision"] = str(decision)
            row["static"] = bool(static)
            row["planned_key_count"] = max(0, int(planned_key_count))
            return

    def _finalize_direct_dependency_bake_diagnostics(
        self,
        start_frame: Optional[float],
        end_frame: Optional[float],
        dense_frames: Optional[Sequence[float]],
        time_converter,
        generated_bone_counts: Mapping[str, int],
    ) -> None:
        """Attach frame and generated-key evidence to accepted dependencies."""

        diagnostics = self._diagnostics.get("control_rig_direct_export")
        if not isinstance(diagnostics, Mapping):
            return
        rows = diagnostics.get("dependency_baked")
        if not isinstance(rows, list):
            return
        resolved_range = None
        if dense_frames:
            resolved_range = (
                _vmd_frame_number(dense_frames[0], time_converter),
                _vmd_frame_number(dense_frames[-1], time_converter),
            )
        elif start_frame is not None and end_frame is not None:
            resolved_range = (
                _vmd_frame_number(start_frame, time_converter),
                _vmd_frame_number(end_frame, time_converter),
            )
        for row in rows:
            if not isinstance(row, dict):
                continue
            row["frame_range"] = resolved_range
            bone_name = str(row.get("bone") or "")
            planned = row.get("planned_key_count", 0)
            row["generated_key_count"] = int(
                generated_bone_counts.get(bone_name, planned or 0)
            )

    def _recover_runtime_authoring_routes(
        self,
        joint: str,
        target_model: str,
        route: Mapping[str, tuple[str, str]],
        classification: Mapping[str, Any],
    ) -> dict[str, tuple[str, str]]:
        """Recover importer-owned pre-runtime routes for one dependency.

        The visible joint is a runtime result when a supported MMD node appears
        in the dependency closure.  Re-run the existing semantic resolvers at
        this boundary to recover the complete compound route.  A partial route
        is intentionally left incomplete so the caller can fail closed instead
        of mixing authored inputs with a final evaluated component.
        """

        recovered = dict(route)
        runtime_types = set(str(value) for value in classification.get("runtime_node_types", ()))
        runtime_nodes = tuple(str(value) for value in classification.get("runtime_nodes", ()))
        long_joint = str((cmds.ls(joint, long=True) or [joint])[0])

        def matching(mapping: Mapping[str, Any]) -> Any:
            for key in (long_joint, str(joint), str(joint).rsplit("|", 1)[-1]):
                if key in mapping:
                    return mapping[key]
            for candidate in mapping.values():
                if not isinstance(candidate, Mapping):
                    continue
                target = candidate.get("target_joint") or candidate.get("joint")
                if target is not None and str(target) in {long_joint, str(joint)}:
                    return candidate
            return None

        # mmdAppend and mmdCcdIk are semantic passthrough surfaces.  Keep
        # accumulator bases ahead of append bases when both occur in a chain.
        if "mmdAppend" in runtime_types:
            try:
                append = matching(collect_append_info())
            except Exception:
                append = None
            if isinstance(append, Mapping):
                node = str(append.get("node") or "")
                if node and _runtime_route_node_matches(node, runtime_nodes):
                    for logical, physical in (append.get("attr_map") or {}).items():
                        recovered.setdefault(str(logical), (node, str(physical)))

        if "mmdCcdIk" in runtime_types:
            try:
                ik = matching(collect_mmd_ik_passthrough_info())
            except Exception:
                ik = None
            if isinstance(ik, Mapping):
                node = str(ik.get("node") or "")
                try:
                    slot = int(ik.get("input_slot", -1))
                except (TypeError, ValueError):
                    slot = -1
                if node and slot >= 0 and _runtime_route_node_matches(node, runtime_nodes):
                    for axis in "XYZ":
                        recovered.setdefault(
                            f"rotate{axis}",
                            (node, f"inputRotate[{slot}].inputRotateElement{axis}"),
                        )

        if "mmdBoneMorphAccum" in runtime_types:
            try:
                resolution = resolve_owned_bone_morph_base_routes((long_joint,))
            except Exception:
                resolution = None
            if resolution is not None:
                accum_route = resolution.routes.get(long_joint) or resolution.routes.get(str(joint))
                if accum_route:
                    accum_nodes = {
                        str(value[0])
                        for value in accum_route.values()
                        if isinstance(value, (tuple, list)) and len(value) == 2
                    }
                    if accum_nodes and all(
                        _runtime_route_node_matches(node, runtime_nodes)
                        for node in accum_nodes
                    ):
                        # An accumulator is the topmost authoring surface for
                        # both compounds, even when its output feeds mmdAppend.
                        recovered.update(
                            {
                                str(attribute): (str(value[0]), str(value[1]))
                                for attribute, value in accum_route.items()
                            }
                        )

        if "mmdPhysicsBoneDriver" in runtime_types:
            route_map = {long_joint: dict(recovered)}
            try:
                self._merge_physics_authored_input_routes(
                    joints=(long_joint,),
                    target_model=target_model,
                    routes=route_map,
                    strict_bake_timeline=True,
                )
                recovered = dict(route_map.get(long_joint, recovered))
            except Exception:
                # An incomplete pre-physics route is rejected by the caller;
                # do not silently fall through to the final solver output.
                pass
        return recovered

    def _control_rig_direct_export_plan(
        self,
        target_model: Optional[str],
        requested_joints: Sequence[str],
    ) -> Optional[dict[str, Any]]:
        """Select keyed Controls and current-scene authored non-Control tracks.

        Control selector keys decide whether a Control-owned joint is emitted;
        the selector is never a value source.  Joints without a Control
        binding use the same current-scene route resolver as ordinary Bake
        Timeline export.  This keeps a Control Rig EDIT export complete for
        character tracks which are intentionally not represented by a
        Control, without reintroducing imported VMD provenance.
        """

        if not target_model:
            return None
        metadata = read_mmd_control_rig_metadata(target_model)
        if not metadata or metadata.get("state") != CONTROL_RIG_EDIT:
            return None
        diagnostics = {
            "status": "planned",
            "selected": {
                "control": [],
                "scene_authored": [],
                "dependency_baked": [],
            },
            "omitted": {
                "keyless_control": [],
                "keyless_default": [],
                "duplicate_bone_name": [],
            },
            "blocked": {
                "dependency_output": [],
                "model_external": [],
                "ownership_unknown": [],
            },
            "dependency_baked": [],
        }
        self._diagnostics["control_rig_direct_export"] = diagnostics

        requested = []
        requested_set = set()
        for joint in requested_joints:
            canonical = str((cmds.ls(joint, long=True) or [joint])[0])
            if canonical not in requested_set:
                requested.append(canonical)
                requested_set.add(canonical)

        # Explicit joint lists must remain model-scoped. Automatic discovery is
        # already scoped by _find_joints, but callers may pass a mixed list.
        model_joints = {
            str((cmds.ls(joint, long=True) or [joint])[0])
            for joint in self._find_joints(target_model)
        }
        outside = [joint for joint in requested if joint not in model_joints]
        if outside:
            diagnostics["blocked"]["model_external"].extend(outside)
            diagnostics["status"] = "blocked"
            raise ControlRigDirectVmdExportError(
                "Control Rig direct VMD export requested joints outside the selected "
                f"model: {outside}",
                path="scene.control_rig.direct_vmd_export.model_scope",
            )

        try:
            resolved = resolve_control_rig_direct_vmd_export_routes(target_model)
        except Exception as exc:
            message = str(exc)
            category = (
                "model_external"
                if "outside the target model" in message
                or "outside the selected model" in message
                else "ownership_unknown"
            )
            diagnostics["blocked"][category].append(message)
            diagnostics["status"] = "blocked"
            raise ControlRigDirectVmdExportError(
                "Control Rig direct VMD export route resolution failed: " + message,
                path="scene.control_rig.direct_vmd_export.resolver",
            ) from exc

        try:
            scene_routes = self._scene_authored_input_routes(
                requested,
                target_model,
                strict_bake_timeline=True,
            )
        except ValueError as exc:
            message = str(exc)
            category = (
                "model_external"
                if "outside the selected model" in message
                else "ownership_unknown"
            )
            diagnostics["blocked"][category].append(message)
            diagnostics["status"] = "blocked"
            raise ControlRigDirectVmdExportError(
                "Control Rig direct VMD export authoring route resolution failed: "
                + message,
                path="scene.control_rig.direct_vmd_export.authoring_route",
            ) from exc

        selected_joints = []
        value_routes = {}
        selector_key_times_by_joint = {}
        selected_bone_names = {}
        control_candidates = {}

        for joint, candidate in resolved["candidates"].items():
            joint = str(joint)
            if joint not in requested_set:
                continue
            control_candidates[joint] = candidate
            key_times = set()
            for plug in candidate["selectorPlugs"]:
                node, separator, attribute = str(plug).rpartition(".")
                if not separator:
                    raise ControlRigDirectVmdExportError(
                        f"invalid Control Rig selector plug: {plug}",
                        path="scene.control_rig.direct_vmd_export.selector_plug",
                    )
                key_times.update(_key_times(node, (attribute,)))
            key_times = sorted(key_times)
            bone_name = str(candidate.get("boneName") or self._mmd_bone_name(joint))
            prior = selected_bone_names.get(bone_name)
            if prior is not None and prior != joint:
                diagnostics["blocked"]["ownership_unknown"].append(
                    f"duplicate VMD bone name {bone_name!r}: {prior}, {joint}"
                )
                diagnostics["status"] = "blocked"
                raise ControlRigDirectVmdExportError(
                    "Control Rig direct VMD export has duplicate VMD bone name: "
                    f"{bone_name!r}",
                    path="scene.control_rig.direct_vmd_export.duplicate_bone_name",
                )
            # Reserve the name even when the selector is keyless.  A later
            # unsupported dependency with the same VMD name must not bypass
            # Control ownership merely because this candidate emits no track.
            selected_bone_names[bone_name] = joint
            if not key_times:
                diagnostics["omitted"]["keyless_control"].append(joint)
                continue
            selected_joints.append(joint)
            value_routes[joint] = dict(candidate["valueRoutes"])
            selector_key_times_by_joint[joint] = key_times
            diagnostics["selected"]["control"].append(joint)

        # Resolver candidates are the Control ownership boundary.  Preserve
        # a fatal result when persisted metadata claims a joint but the
        # resolver dropped it (for example a missing control or malformed
        # fallback row); such a joint must never be hidden by dependency bake.
        claimed_control_joints = set()
        bindings = metadata.get("bindings", {})
        if isinstance(bindings, Mapping):
            for binding in bindings.values():
                if not isinstance(binding, Mapping) or binding.get("fallback") is not None:
                    continue
                try:
                    claimed = resolve_mmd_control_rig_binding_joint(cmds, binding)
                except Exception:
                    claimed = binding.get("joint")
                if not claimed:
                    continue
                claimed = str((cmds.ls(str(claimed), long=True) or [claimed])[0])
                if claimed in requested_set:
                    claimed_control_joints.add(claimed)

        # The resolver's candidate set is the ownership boundary for Control
        # joints.  A keyless Control must not fall back to its authored plug;
        # only joints without any Control candidate are eligible here.
        for joint in requested:
            if joint in control_candidates:
                continue
            if joint in claimed_control_joints:
                message = (
                    f"{joint}: Control Rig binding was dropped from the resolver "
                    "candidate set"
                )
                diagnostics["blocked"]["ownership_unknown"].append(message)
                diagnostics["status"] = "blocked"
                raise ControlRigDirectVmdExportError(
                    "Control Rig direct VMD export cannot hide Control-owned bone: "
                    + message,
                    path=(
                        "scene.control_rig.direct_vmd_export."
                        f"{joint}.candidate"
                    ),
                )
            route = dict(scene_routes.get(joint, {}))
            source_times = _routed_key_times(joint, route)
            # Never sample a visible final output when a dependency is not
            # represented by one of the validated authoring routes above.
            connected_unrouted_channels = [
                attr
                for attr in _BONE_EXPORT_ATTRS
                if attr not in route
                and _incoming_connection_state(joint, (attr,), strict=True) == "some"
            ]
            unresolved_channels = (
                connected_unrouted_channels
                if connected_unrouted_channels
                and _bake_timeline_single_key_bone_route(joint, route) is None
                else []
            )
            classifications = []
            if unresolved_channels:
                for group in ("translate", "rotate"):
                    group_channels = tuple(
                        attribute
                        for attribute in unresolved_channels
                        if attribute.startswith(group)
                    )
                    if not group_channels:
                        continue
                    classification = _classify_unsupported_bone_dependency(
                        joint,
                        target_model,
                        group_channels,
                    )
                    if classification.get("status") != "accepted":
                        reason = str(
                            classification.get(
                                "reason",
                                "unknown dependency closure",
                            )
                        )
                        message = (
                            f"{joint}: unresolved dependency output channels "
                            f"{group_channels!r}; reason: {reason}"
                        )
                        diagnostics["blocked"]["dependency_output"].append(message)
                        diagnostics["status"] = "blocked"
                        raise ControlRigDirectVmdExportError(
                            "Control Rig direct VMD export cannot sample dependency output: "
                            + message,
                            path=(
                                "scene.control_rig.direct_vmd_export."
                                f"{joint}.channels"
                            ),
                        )
                    classifications.append(classification)
                    runtime_nodes = classification.get("runtime_nodes", ())
                    if runtime_nodes:
                        route = self._recover_runtime_authoring_routes(
                            joint,
                            target_model,
                            route,
                            classification,
                        )
                        if not _runtime_route_group_complete(
                            route,
                            group,
                            runtime_nodes,
                        ):
                            message = (
                                f"{joint}: runtime dependency output has no complete "
                                f"{group} authoring route; final evaluated output "
                                "cannot be mixed with a partial route"
                            )
                            diagnostics["blocked"]["dependency_output"].append(message)
                            diagnostics["status"] = "blocked"
                            raise ControlRigDirectVmdExportError(
                                "Control Rig direct VMD export cannot sample dependency output: "
                                + message,
                                path=(
                                    "scene.control_rig.direct_vmd_export."
                                    f"{joint}.{group}.authoring_route"
                                ),
                            )
                bone_name = self._mmd_bone_name(joint)
                prior = selected_bone_names.get(bone_name)
                if prior is not None:
                    diagnostics["blocked"]["ownership_unknown"].append(
                        f"duplicate VMD bone name {bone_name!r}: {prior}, {joint}"
                    )
                    diagnostics["status"] = "blocked"
                    raise ControlRigDirectVmdExportError(
                        "Control Rig direct VMD export has duplicate VMD bone name: "
                        f"{bone_name!r}",
                        path="scene.control_rig.direct_vmd_export.duplicate_bone_name",
                    )
                selected_bone_names[bone_name] = joint
                selected_joints.append(joint)
                value_routes[joint] = route
                diagnostics["selected"]["dependency_baked"].append(joint)
                diagnostics["dependency_baked"].append(
                    {
                        "joint": joint,
                        "bone": bone_name,
                        "frame_range": None,
                        "generated_key_count": 0,
                        "reason": _UNSUPPORTED_BONE_BAKE_REASON,
                        "classification_reason": "; ".join(
                            str(item.get("reason")) for item in classifications
                        ),
                        "classification_node_types": sorted(
                            {
                                str(node_type)
                                for item in classifications
                                for node_type in item.get("node_types", ())
                            }
                        ),
                        "classification_runtime_node_types": sorted(
                            {
                                str(node_type)
                                for item in classifications
                                for node_type in item.get("runtime_node_types", ())
                            }
                        ),
                        "classification_plug_provenance": [
                            plug
                            for item in classifications
                            for plug in item.get("plug_provenance", ())
                        ],
                    }
                )
                continue

            if not source_times and not route:
                diagnostics["omitted"]["keyless_default"].append(joint)
                continue

            bone_name = self._mmd_bone_name(joint)
            prior = selected_bone_names.get(bone_name)
            if prior is not None:
                # A Control route is authoritative for the same VMD bone name.
                if prior in control_candidates:
                    diagnostics["omitted"]["duplicate_bone_name"].append(joint)
                    continue
                diagnostics["blocked"]["ownership_unknown"].append(
                    f"duplicate VMD bone name {bone_name!r}: {prior}, {joint}"
                )
                diagnostics["status"] = "blocked"
                raise ControlRigDirectVmdExportError(
                    "Control Rig direct VMD export has duplicate VMD bone name: "
                    f"{bone_name!r}",
                    path="scene.control_rig.direct_vmd_export.duplicate_bone_name",
                )
            selected_bone_names[bone_name] = joint
            selected_joints.append(joint)
            value_routes[joint] = route
            diagnostics["selected"]["scene_authored"].append(joint)

        return {
            "joints": selected_joints,
            "value_routes": value_routes,
            "selector_key_times_by_joint": selector_key_times_by_joint,
            "ik_state_routes": dict(resolved.get("ikStateRoutes", {})),
            "diagnostics": diagnostics,
        }

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
        strict_bake_timeline: bool = False,
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
                strict_bake_timeline=strict_bake_timeline,
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
            strict_bake_timeline=strict_bake_timeline,
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
        strict_bake_timeline: bool = False,
    ) -> None:
        """Add owned physics-driver pre-inputs without replacing authored routes.

        The final ``outTranslate``/``outRotate`` values are physics results and
        are not motion sources.  VMD recovery connects authored animation to
        the driver's ``inPre*`` plugs, so only a unique, model-owned driver
        with a validated target and an incoming non-physics source is eligible.
        Missing or ambiguous graph pieces are skipped fail-closed for legacy
        callers.  Standard Bake Timeline uses the same graph boundary but raises on
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
            strict=strict_bake_timeline,
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
            if strict_bake_timeline:
                scene_owners = driver_owners.get(driver, ())
                selected_owners = sorted(selected_driver_owners.get(driver, ()))
                if scene_owners != selected_owners or len(scene_owners) != 1:
                    raise ValueError(
                        "Bake Timeline physics driver must belong to exactly one "
                        f"selected solver; driver={driver}, "
                        f"solvers={scene_owners}"
                    )
            target_connections = _physics_driver_target_connections(driver)
            if strict_bake_timeline and len(target_connections) != 1:
                raise ValueError(
                    "Bake Timeline physics ownership requires exactly one target "
                    f"connection for {driver}; found {len(target_connections)}"
                )
            if len(target_connections) != 1:
                continue
            target_joint = target_connections[0]
            target_path = _canonical_dag_path(target_joint)
            if strict_bake_timeline and (
                not target_path
                or not _dag_path_is_under_root(target_path, root_path)
            ):
                raise ValueError(
                    "Bake Timeline physics ownership target is outside the selected "
                    f"model: {driver} -> {target_joint}"
                )
            if not target_path or target_path not in joints_by_path:
                continue
            if not _dag_path_is_under_root(target_path, root_path):
                continue
            if strict_bake_timeline:
                bone_index = _physics_driver_bone_index(
                    driver,
                    strict=True,
                )
                if bone_index is None:
                    raise ValueError(
                        "Bake Timeline physics ownership requires a valid non-negative "
                        f"bone index for {driver}"
                    )
            else:
                bone_index = _physics_driver_bone_index(driver)
                if bone_index is None:
                    continue
            pre_inputs_exist = _physics_driver_pre_inputs_exist(driver)
            if not strict_bake_timeline and not pre_inputs_exist:
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
        if strict_bake_timeline and ambiguous_targets:
            target = sorted(ambiguous_targets)[0]
            drivers = sorted(
                driver
                for driver, _index, _pre_inputs in candidates[target]
            )
            raise ValueError(
                "Bake Timeline physics ownership has duplicate drivers for target "
                f"{target}: {drivers}"
            )
        if strict_bake_timeline and ambiguous_indices:
            index = sorted(ambiguous_indices)[0]
            targets = sorted(set(used_indices[index]))
            raise ValueError(
                "Bake Timeline physics ownership has duplicate bone index "
                f"{index} across targets: {targets}"
            )
        for target_path, values in candidates.items():
            if target_path in ambiguous_targets:
                continue
            driver, bone_index, pre_inputs_exist = values[0]
            if bone_index in ambiguous_indices:
                continue
            if strict_bake_timeline:
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
                    self._bake_timeline_physics_output_excluded_targets.add(target_path)
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
                    "strict_bake_timeline_owned_physics_final_output",
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
        frame_sink=None,
        exact_run_reduction: bool = False,
        protected_vmd_frames: Optional[set[int]] = None,
        key_reduction_report: Optional[dict[str, Any]] = None,
        morph_channel_sampler=None,
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
        bake_timeline_dense_sampling = bool(dense_sample and timeline_evaluation)
        if bake_timeline_dense_sampling and dense_frame_samples is None:
            dense_frame_samples = _dense_frame_samples((), start_frame, end_frame)
        frames = [] if frame_sink is None else None
        channels = []
        controller_nodes = set()
        controller_channel_morph_types = {}
        keyless_dependency_channels = set()
        static_keyless_channels = set()
        static_sample = (
            _bake_timeline_earliest_integer_sample(
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
                    if bake_timeline_dense_sampling:
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
                    bake_timeline_dense_sampling
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

                if bake_timeline_dense_sampling:
                    duplicate_nodes = sorted(
                        node
                        for node, entries in metadata_by_node.items()
                        if len(entries) != 1
                    )
                    if duplicate_nodes:
                        node = duplicate_nodes[0]
                        raise ValueError(
                            "Bake Timeline morph metadata has conflicting provider ownership "
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
                            "Bake Timeline morph metadata has duplicate controller index "
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
                            "Bake Timeline morph metadata has duplicate controller name "
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
                    if source_frames or bake_timeline_dense_sampling:
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

        if bake_timeline_dense_sampling:
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
                        "Bake Timeline morph output has duplicate providers for "
                        f"{morph_name!r}: {unique_providers}"
                    )
                controller_provider = controller_providers[0]
                controller_type = controller_channel_morph_types.get(
                    controller_provider,
                    "",
                )
                if controller_type != "vertex":
                    raise ValueError(
                        "Bake Timeline morph output has ambiguous non-vertex controller "
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
        if bake_timeline_dense_sampling:
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

        stream_candidate_rows: dict[
            str, list[tuple[str, str, str, Sequence[float], bool]]
        ] = {}
        if frame_sink is not None and bake_timeline_dense_sampling:
            for channel in channels:
                if not channel[4]:
                    stream_candidate_rows.setdefault(str(channel[2]), []).append(channel)
        stream_candidate_ids = {
            name: index
            for index, name in enumerate(sorted(stream_candidate_rows))
            if len(stream_candidate_rows[name]) == 1
        }
        candidate_spool = None
        candidate_first: dict[int, tuple[int, float]] = {}
        candidate_varies: set[int] = set()
        candidate_frame_counts: dict[int, int] = {}
        candidate_nonzero_frame_counts: dict[int, int] = {}
        stream_last_vmd_frame: dict[str, int] = {}
        protected_by_name: dict[str, set[int]] = {}
        for _node, _attr, morph_name, ranged_source_frames, _direct_single in channels:
            protected = protected_by_name.setdefault(
                str(morph_name), set(protected_vmd_frames or ())
            )
            protected.update(
                _vmd_frame_number(frame, time_converter)
                for frame in ranged_source_frames
            )
        if frame_sink is not None and stream_candidate_ids:
            candidate_spool = tempfile.TemporaryFile(mode="w+b")
            self._diagnostics.setdefault("morph_collection", {})[
                "candidate_spool"
            ] = True
        reducers: dict[str, _ExactRunReducer] = {}

        def emit_reduced(payload: Mapping[str, Any]) -> None:
            if frame_sink is None:
                frames.append(payload)
                return
            if not exact_run_reduction:
                if key_reduction_report is not None:
                    key_reduction_report["input"] += 1
                    key_reduction_report["output"] += 1
                frame_sink(payload)
                return
            name = str(payload["morph_name"])
            reducer = reducers.get(name)
            if reducer is None:
                reducer = _ExactRunReducer(
                    frame_sink,
                    ("weight",),
                    protected_by_name.get(name, set(protected_vmd_frames or ())),
                    key_reduction_report,
                    name,
                )
                reducers[name] = reducer
            reducer.add(payload)

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
                if not _should_emit_morph_frame({"morph_name": morph_name}):
                    self._record_unencodable_morph_omission(
                        str(morph_name),
                        frame_count=1,
                        nonzero_frame_count=int(not is_default),
                    )
                    self._record_track_selection(
                        "morph",
                        morph_name,
                        "omitted_default" if is_default else "omitted_unrepresentable",
                        "direct_single_default"
                        if is_default
                        else "vmd_name_not_cp932",
                        0 if static_keyless else len(source_frames),
                        0,
                    )
                    return
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
            vmd_frame = _vmd_frame_number(frame_number, time_converter)
            if frame_sink is not None:
                stream_name = str(morph_name)
                # Samples are evaluated in ascending Maya time.  Fixed-rate
                # conversion can map adjacent samples to one VMD frame; keep
                # the first, matching legacy post-conversion deduplication,
                # without retaining every emitted frame number.
                if stream_last_vmd_frame.get(stream_name) == vmd_frame:
                    return
                stream_last_vmd_frame[stream_name] = vmd_frame
            candidate_id = stream_candidate_ids.get(str(morph_name))
            if candidate_spool is not None and candidate_id is not None:
                candidate_spool.write(
                    struct.pack("<Iqd", candidate_id, vmd_frame, float(weight))
                )
                return
            payload = {
                "morph_name": morph_name,
                "frame_number": vmd_frame,
                "weight": weight,
            }
            emit_reduced(payload)

        native_morph_samples = None
        try:
            if timeline_evaluation and channels:
                # One shared frame-major pass.  Streaming Bake Timeline deliberately
                # avoids a dense-frame set per channel.
                if frame_sink is not None and dense_sample and dense_frame_samples is not None:
                    sample_times = set(dense_frame_samples)
                    for _node, _attr, _name, source_frames, direct_single in channels:
                        if direct_single:
                            sample_times.update(source_frames)
                    sample_times = sorted(sample_times)
                else:
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
                if morph_channel_sampler is not None:
                    sample_method = getattr(
                        morph_channel_sampler,
                        "sample_dense_scalar_channels",
                        None,
                    )
                    if not callable(sample_method):
                        raise RuntimeError(
                            "native Morph sampler has no dense scalar method"
                        )
                    native_morph_samples = sample_method(
                        sample_times,
                        [
                            (str(morph_name), str(node), str(attr))
                            for node, attr, morph_name, _source_frames, _direct_single in channels
                        ],
                    )
                    native_tracks = {
                        str(morph_name): native_morph_samples.scalar_track(
                            str(morph_name)
                        )
                        for _node, _attr, morph_name, _source_frames, _direct_single in channels
                    }
                    self._diagnostics["native_morph_sampler"] = dict(
                        getattr(native_morph_samples, "diagnostics", {}) or {}
                    )
                    for frame_index, frame_number in enumerate(sample_times):
                        for node, attr, morph_name, ranged_source_frames, direct_single in channels:
                            if not direct_single and dense_sample and dense_frame_samples is not None:
                                selected = True
                            else:
                                selected = frame_number in ranged_source_frames
                            if not selected:
                                continue
                            append_frame(
                                node,
                                attr,
                                morph_name,
                                frame_number,
                                native_tracks[str(morph_name)].values[frame_index],
                                ranged_source_frames,
                                direct_single,
                            )
                else:
                    # Retained for non-production legacy callers. Standard
                    # streaming Bake Timeline always supplies the native sampler.
                    with _MayaTimelineReader() as timeline_reader:
                        for frame_number in sample_times:
                            timeline_reader.set_frame(frame_number)
                            for node, attr, morph_name, ranged_source_frames, direct_single in channels:
                                if not direct_single and dense_sample and dense_frame_samples is not None:
                                    selected = True
                                else:
                                    selected = frame_number in ranged_source_frames
                                if not selected:
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
        except BaseException:
            if candidate_spool is not None:
                candidate_spool.close()
                candidate_spool = None
            raise
        finally:
            if native_morph_samples is not None:
                _close_native_samples(native_morph_samples)
        if candidate_spool is not None:
            try:
                candidate_spool.flush()
                candidate_spool.seek(0)
                while True:
                    record = candidate_spool.read(20)
                    if not record:
                        break
                    candidate_id, frame_number, weight = struct.unpack("<Iqd", record)
                    candidate_frame_counts[candidate_id] = (
                        candidate_frame_counts.get(candidate_id, 0) + 1
                    )
                    if weight != 0.0:
                        candidate_nonzero_frame_counts[candidate_id] = (
                            candidate_nonzero_frame_counts.get(candidate_id, 0) + 1
                        )
                    first = candidate_first.get(candidate_id)
                    if first is None:
                        candidate_first[candidate_id] = (frame_number, weight)
                    elif first[1] != weight:
                        candidate_varies.add(candidate_id)
                names_by_id = {value: key for key, value in stream_candidate_ids.items()}
                replay_candidate_ids = set()
                for candidate_id, first in sorted(candidate_first.items()):
                    name = names_by_id[candidate_id]
                    node, attr, _name, ranged_source_frames, _direct_single = (
                        stream_candidate_rows[name][0]
                    )
                    source_key_count = len(ranged_source_frames)
                    keyless_dependency = (node, attr) in keyless_dependency_channels
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
                    encodable = _should_emit_morph_frame({"morph_name": name})
                    nonzero_frame_count = candidate_nonzero_frame_counts.get(
                        candidate_id, 0
                    )
                    if not encodable:
                        self._record_unencodable_morph_omission(
                            name,
                            frame_count=candidate_frame_counts.get(candidate_id, 0),
                            nonzero_frame_count=nonzero_frame_count,
                        )
                    if nonzero_frame_count == 0:
                        self._record_track_selection(
                            "morph",
                            name,
                            "omitted_default",
                            "dense_exact_zero",
                            source_key_count,
                            0,
                        )
                        continue
                    if not encodable:
                        self._record_track_selection(
                            "morph",
                            name,
                            "omitted_unrepresentable",
                            "vmd_name_not_cp932",
                            source_key_count,
                            0,
                        )
                        continue
                    if candidate_id in candidate_varies:
                        self._record_track_selection(
                            "morph",
                            name,
                            "dependency_baked" if dependency else "authored_sampled",
                            reason,
                            source_key_count,
                            len(dense_frame_samples or ()),
                        )
                        replay_candidate_ids.add(candidate_id)
                    else:
                        self._record_track_selection(
                            "morph",
                            name,
                            "constant_one_key",
                            "dense_exact_constant",
                            source_key_count,
                            1,
                        )
                        payload = {
                            "morph_name": name,
                            "frame_number": first[0],
                            "weight": first[1],
                        }
                        if key_reduction_report is not None:
                            key_reduction_report["input"] += 1
                            key_reduction_report["output"] += 1
                        frame_sink(payload)
                if replay_candidate_ids:
                    # Replay every varying candidate in one aggregate pass.
                    # Rows were already first-win deduplicated before spooling,
                    # so no per-track dense frame set is needed here.
                    candidate_spool.seek(0)
                    while True:
                        record = candidate_spool.read(20)
                        if not record:
                            break
                        candidate_id, frame_number, weight = struct.unpack(
                            "<Iqd", record
                        )
                        if candidate_id not in replay_candidate_ids:
                            continue
                        emit_reduced(
                            {
                                "morph_name": names_by_id[candidate_id],
                                "frame_number": frame_number,
                                "weight": weight,
                            }
                        )
            finally:
                candidate_spool.close()
                candidate_spool = None
        for reducer in reducers.values():
            reducer.finish()
        if dense_sample:
            diagnostic_rows = {}
            for node, _attr, morph_name, ranged_source_frames, direct_single in channels:
                if direct_single:
                    continue
                if str(morph_name) in stream_candidate_ids:
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
        if frame_sink is not None:
            return []
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
        motion_scale: float = 1.0,
        time_converter=None,
        dense_sample: bool = False,
        dense_frame_samples: Optional[Sequence[float]] = None,
        timeline_evaluation: bool = False,
        frame_sink=None,
        progress_callback=None,
        cancel_requested=None,
    ) -> list[dict]:
        """Collect MMD camera frames, including dense rigless camera baking."""
        if abs(float(motion_scale)) < 1e-12:
            raise ValueError("motion_scale must not be zero")
        time_converter = time_converter or _scene_maya_time_to_vmd_frame()
        frames = [] if frame_sink is None else None
        restore_time = None
        rigless_target_reasons = {
            "plane_hit": 0,
            "distance_capped": 0,
            "previous_distance": 0,
            "center_of_interest": 0,
            "default": 0,
        }
        timeline_reader = _MayaTimelineReader() if timeline_evaluation else None
        with timeline_reader or nullcontext():
            try:
                for camera in cameras:
                    camera_target = _camera_target_node(camera)
                    camera_root = _camera_root_node(camera)
                    camera_shape = _camera_shape(camera)
                    ancestor_frames = {
                        frame
                        for ancestor in _transform_ancestors(camera)
                        for frame in _key_times(ancestor, _TRANSFORM_EXPORT_ATTRS)
                    }
                    source_frames = sorted(
                        set(_key_times(camera, _CAMERA_EXPORT_ATTRS))
                        | ancestor_frames
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
                    previous_rigless_distance = None
                    keyed_frames = (
                        sorted(set(dense_frame_samples))
                        if dense_sample
                        and dense_frame_samples is not None
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
                        uses_rigless_camera = bool(
                            camera_shape
                            and not uses_aim_roll_rig
                            and not uses_raw_mmd_attrs
                            and not _has_attr(camera, ATTR_MMD_CAMERA)
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
                            camera_motion_scale = _camera_motion_scale(camera)
                            eye, forward, up = _camera_world_pose(camera)
                            target = om.MVector(
                                *cmds.xform(
                                    camera_target,
                                    query=True,
                                    worldSpace=True,
                                    translation=True,
                                )
                            )
                            position = (
                                float(target.x) / camera_motion_scale,
                                float(target.y) / camera_motion_scale,
                                -float(target.z) / camera_motion_scale,
                            )
                            distance = (
                                _signed_camera_distance(eye, target, forward)
                                / camera_motion_scale
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
                        elif uses_rigless_camera:
                            if timeline_reader is None:
                                if restore_time is None:
                                    restore_time = _query_current_time()
                                cmds.currentTime(frame_number, edit=True)
                            eye, forward, up = _camera_world_pose(camera)
                            center_of_interest = (
                                read_value(
                                    camera_shape,
                                    "centerOfInterest",
                                    frame_number,
                                )
                                if _has_attr(camera_shape, "centerOfInterest")
                                else None
                            )
                            target_distance, target_reason = (
                                _rigless_camera_target_distance(
                                    eye.y,
                                    forward.y,
                                    previous_distance=previous_rigless_distance,
                                    center_of_interest=center_of_interest,
                                )
                            )
                            previous_rigless_distance = target_distance
                            rigless_target_reasons[target_reason] += 1
                            target = om.MVector(
                                eye.x + forward.x * target_distance,
                                eye.y + forward.y * target_distance,
                                eye.z + forward.z * target_distance,
                            )
                            position = (
                                float(target.x) / motion_scale,
                                float(target.y) / motion_scale,
                                -float(target.z) / motion_scale,
                            )
                            distance = -float(target_distance) / motion_scale
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
                        if not uses_aim_roll_rig and not uses_rigless_camera:
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
                        payload = {
                            "frame_number": _vmd_frame_number(
                                frame_number, time_converter
                            ),
                            "distance": distance,
                            "position": position,
                            "rotation": rotation,
                            # The current scene has no separate camera
                            # interpolation authoring surface.  Keep the
                            # native VMD contract explicit rather than
                            # relying on the writer's implicit fallback.
                            "interpolation": _DEFAULT_CAMERA_INTERPOLATION,
                            "viewing_angle": viewing_angle,
                            "perspective": perspective,
                        }
                        if frame_sink is None:
                            frames.append(payload)
                        else:
                            frame_sink(payload)
                        _poll_export_control(progress_callback, cancel_requested)
            finally:
                if restore_time is not None:
                    cmds.currentTime(restore_time, edit=True)
        if frame_sink is None:
            frames.sort(key=lambda item: item["frame_number"])
        if any(rigless_target_reasons.values()):
            self._diagnostics["rigless_camera_target"] = {
                "plane": "XZ (Y=0)",
                "max_distance": _RIGLESS_CAMERA_MAX_TARGET_DISTANCE,
                "samples": rigless_target_reasons,
            }
        if frame_sink is None:
            return frames
        return []

    def collect_light_frames(
        self,
        lights: Sequence[str],
        start_frame: Optional[float] = None,
        end_frame: Optional[float] = None,
        time_converter=None,
        dense_sample: bool = False,
        dense_frame_samples: Optional[Sequence[float]] = None,
        timeline_evaluation: bool = False,
        frame_sink=None,
        progress_callback=None,
        cancel_requested=None,
    ) -> list[dict]:
        """Collect keyed MMD light controller frames."""
        time_converter = time_converter or _scene_maya_time_to_vmd_frame()
        frames = [] if frame_sink is None else None
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
                    payload = {
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
                    if frame_sink is None:
                        frames.append(payload)
                    else:
                        frame_sink(payload)
                    _poll_export_control(progress_callback, cancel_requested)
        if frame_sink is None:
            frames.sort(key=lambda item: item["frame_number"])
            return frames
        return []

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
                "Reason: VMD export cannot choose between multiple camera/light "
                "targets automatically; remove the extra marker or pass one "
                "explicit node"
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
            raise RuntimeError("Bake Timeline playback state query failed") from exc
        if playing:
            raise RuntimeError("Bake Timeline sampling cannot run during playback")
        try:
            self._entry_time = float(cmds.currentTime(query=True))
        except Exception as exc:
            raise RuntimeError("Bake Timeline entry time query failed") from exc
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
                "Bake Timeline samples must be evaluated in ascending order"
            )
        if sample_time == self._sample_time:
            self._has_sampled = True
            return
        try:
            cmds.currentTime(sample_time, edit=True)
        except Exception as exc:
            raise RuntimeError(
                f"Bake Timeline evaluation failed at frame {sample_time:g}"
            ) from exc
        self._sample_time = sample_time
        self._has_sampled = True

    def __exit__(self, _exc_type, _exc, _traceback) -> bool:
        if self._entry_time is None:
            return False
        try:
            cmds.currentTime(self._entry_time, edit=True)
        except Exception as exc:
            raise RuntimeError("Bake Timeline time restoration failed") from exc
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
    """Return one-frame integer samples for a Bake Timeline animation range."""
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


def _bake_timeline_earliest_integer_sample(
    dense_frame_samples: Optional[Sequence[float]],
    start_frame: Optional[float],
    end_frame: Optional[float],
) -> Optional[float]:
    """Resolve one requested-range integer sample for keyless Bake Timeline tracks."""

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


def _poll_export_control(progress_callback=None, cancel_requested=None) -> None:
    """Pump the host-owned progress/cancel seam for scene-only tracks."""

    if callable(progress_callback):
        progress_callback("payload_collection")
    if callable(cancel_requested):
        try:
            cancelled = bool(cancel_requested())
        except Exception:
            cancelled = False
        if cancelled:
            raise RuntimeError("VMD Camera/Light export was cancelled")


def _camera_shape(camera: str) -> Optional[str]:
    shapes = cmds.listRelatives(camera, shapes=True, type="camera") or []
    return shapes[0] if shapes else None


def _camera_world_pose(camera: str) -> tuple[om.MVector, om.MVector, om.MVector]:
    """Read a camera's world eye, forward, and up vectors at current time."""

    eye = om.MVector(
        *cmds.xform(
            camera,
            query=True,
            worldSpace=True,
            translation=True,
        )
    )
    matrix = om.MMatrix(cmds.getAttr(f"{camera}.worldMatrix[0]"))
    forward = om.MVector(0.0, 0.0, -1.0) * matrix
    up = om.MVector(0.0, 1.0, 0.0) * matrix
    if forward.length() > 1e-12:
        forward.normalize()
    if up.length() > 1e-12:
        up.normalize()
    return eye, forward, up


def _transform_ancestors(node: str) -> list[str]:
    """Return transform ancestors nearest-first without escaping DAG cycles."""

    result = []
    visited = {str(node)}
    current = str(node)
    while current:
        parents = cmds.listRelatives(current, parent=True, fullPath=True) or []
        if not parents:
            break
        parent = str(parents[0])
        if parent in visited:
            break
        visited.add(parent)
        result.append(parent)
        current = parent
    return result


def _rigless_camera_target_distance(
    eye_y: float,
    forward_y: float,
    *,
    previous_distance: Optional[float] = None,
    center_of_interest: Optional[float] = None,
    plane_y: float = _RIGLESS_CAMERA_TARGET_PLANE_Y,
    max_distance: float = _RIGLESS_CAMERA_MAX_TARGET_DISTANCE,
) -> tuple[float, str]:
    """Choose a bounded forward distance for a rigless camera target."""

    limit = max(float(max_distance), _RIGLESS_CAMERA_PLANE_EPSILON)
    if abs(float(forward_y)) > _RIGLESS_CAMERA_PLANE_EPSILON:
        intersection_distance = (float(plane_y) - float(eye_y)) / float(forward_y)
        if math.isfinite(intersection_distance) and intersection_distance > 0.0:
            if intersection_distance > limit:
                return limit, "distance_capped"
            return intersection_distance, "plane_hit"
    if previous_distance is not None:
        value = float(previous_distance)
        if math.isfinite(value) and value > 0.0:
            return min(value, limit), "previous_distance"
    if center_of_interest is not None:
        value = float(center_of_interest)
        if math.isfinite(value) and value > 0.0:
            return min(value, limit), "center_of_interest"
    return min(_RIGLESS_CAMERA_DEFAULT_TARGET_DISTANCE, limit), "default"


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


def _validate_direct_rotation_export_indices(
    context_joints: Sequence[str],
    selected_joints: Sequence[str],
) -> None:
    """Require an unambiguous indexed parent context for direct export."""

    indices = {}
    for joint in {
        str((cmds.ls(item, long=True) or [item])[0]) for item in context_joints
    }:
        if not _has_attr(joint, "mmd_bone_index"):
            continue
        index = int(cmds.getAttr(f"{joint}.mmd_bone_index"))
        prior = indices.get(index)
        if prior is not None and prior != joint:
            raise ValueError(
                "direct Control Rig rotation context has duplicate bone index "
                f"{index}: {prior}, {joint}"
            )
        indices[index] = joint
    indexed = set(indices.values())
    for item in selected_joints:
        joint = str((cmds.ls(item, long=True) or [item])[0])
        if joint not in indexed:
            raise ValueError(
                f"direct Control Rig rotation context has unindexed selected joint: {joint}"
            )
        while True:
            parents = cmds.listRelatives(joint, parent=True, fullPath=True) or []
            if not parents:
                break
            parent = str((cmds.ls(parents[0], long=True) or [parents[0]])[0])
            if str(cmds.nodeType(parent) or "") != "joint":
                break
            if parent not in indexed:
                raise ValueError(
                    "direct Control Rig rotation context has an unindexed parent: "
                    f"{parent} -> {joint}"
                )
            joint = parent


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


def _resolve_bake_timeline_track_targets(options: Mapping[str, Any]) -> set[str]:
    """Resolve explicit Bake Timeline VMD track targets.

    The legacy stream contract had no target selector and therefore retains
    character-only behavior when the option is absent.  New UI/API callers
    pass ``track_targets`` explicitly; choosing Camera or Light then scopes
    the payload to those sections and never appends character, IK, or shadow
    tracks.
    """

    option_name = "export_target" if "export_target" in options else "track_targets"
    if option_name not in options:
        return {"character"}
    value = options.get(option_name)
    if isinstance(value, str):
        value = (value,)
    try:
        if option_name == "export_target":
            target = str(value[0] if isinstance(value, (list, tuple)) and len(value) == 1 else value).strip().lower()
            targets = {
                "character": {"character"},
                "camera": {"camera"},
                "light": {"light"},
                "camera+light": {"camera", "light"},
                "camera_light": {"camera", "light"},
            }.get(target, {target})
        else:
            targets = {str(item).strip().lower() for item in value}
    except TypeError as exc:
        raise ValueError("export_target must be a supported target name") from exc
    if not targets or not targets.issubset(_BAKE_TIMELINE_TRACK_TARGETS):
        invalid = sorted(targets - _BAKE_TIMELINE_TRACK_TARGETS)
        if invalid:
            raise ValueError(f"unsupported VMD export target: {', '.join(invalid)}")
        raise ValueError("export_target must contain at least one target")
    if "character" in targets and len(targets) > 1:
        raise ValueError("character VMD export cannot be mixed with camera or light targets")
    return targets


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
                    "Bake Timeline physics solver ownership is ambiguous for "
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
