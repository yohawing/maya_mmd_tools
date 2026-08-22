"""Analyze safe authored inputs for an MMD-native curve control rig.

The analyzer is deliberately report-only.  It reads imported MMD joint
metadata and incoming dependency-graph connections, then produces a stable
specification for later control-rig builders.  It never creates nodes, changes
connections, or claims solver-owned output channels as controller inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from mmd_tools.core.constants import (
    ATTR_MMD_BONE_FLAGS,
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_DISPLAY_FRAMES_JSON,
    ATTR_MMD_FIXED_AXIS,
    ATTR_MMD_AXIS_DIRECTION,
)
from mmd_tools.core.display_frame_metadata import display_frames_from_json
from mmd_tools.core.humanik_utils import maya_cmds
from mmd_tools.core.mmd_bone_names import normalize_mmd_bone_name
from mmd_tools.core.pmx_data.bone import PmxBoneFlag


CONTROL_RIG_SPEC_SCHEMA = "mmd_tools.mmd_control_rig_spec"
CONTROL_RIG_SPEC_VERSION = 1

INPUT_DIRECT_CHANNEL = "direct_channel"
INPUT_APPEND_BASE = "append_base"
INPUT_BONE_MORPH_BASE = "bone_morph_base"
INPUT_IK_CONTROLLER = "ik_controller"
INPUT_IK_LINK_INPUT = "ik_link_input"
INPUT_SOLVER_OUTPUT = "solver_output"
INPUT_UNSUPPORTED = "unsupported"

STATUS_READY = "ready"
STATUS_FALLBACK = "fallback"
STATUS_MISSING = "missing"
STATUS_BLOCKED = "blocked"

_ANIMATION_NODE_TYPES = frozenset(
    {
        "pairBlend",
        "unitConversion",
    }
)
_INPUT_ATTRS = (
    "translate",
    "translateX",
    "translateY",
    "translateZ",
    "rotate",
    "rotateX",
    "rotateY",
    "rotateZ",
)


@dataclass(frozen=True)
class MmdControlRigConnectionFact:
    """One incoming connection observed on an imported MMD joint."""

    source_plug: str
    destination_plug: str
    source_node_type: str

    def to_dict(self) -> Dict[str, str]:
        """Return a deterministic JSON-safe representation."""
        return {
            "sourcePlug": self.source_plug,
            "destinationPlug": self.destination_plug,
            "sourceNodeType": self.source_node_type,
        }


@dataclass(frozen=True)
class MmdControlRigBoneFact:
    """MMD metadata and graph facts needed to classify one joint."""

    joint: str
    mmd_name: str
    bone_index: Optional[int] = None
    pmx_flags: int = 0
    incoming: Tuple[MmdControlRigConnectionFact, ...] = ()
    ik_solvers: Tuple[str, ...] = ()
    solver_input_plugs: Tuple[str, ...] = ()
    bone_morph_base_plugs: Tuple[str, ...] = ()
    fixed_axis: Optional[Tuple[float, float, float]] = None


@dataclass(frozen=True)
class MmdControlRigBoneBinding:
    """Safe authored-input classification for one imported MMD joint."""

    joint: str
    mmd_name: str
    bone_index: Optional[int]
    pmx_flags: int
    input_kind: str
    authored_plugs: Tuple[str, ...]
    warnings: Tuple[str, ...] = ()
    blockers: Tuple[str, ...] = ()
    ik_solvers: Tuple[str, ...] = ()
    incoming: Tuple[MmdControlRigConnectionFact, ...] = ()
    fixed_axis: Optional[Tuple[float, float, float]] = None

    @property
    def blocked(self) -> bool:
        """Return whether a later builder must fail closed for this bone."""
        return bool(self.blockers) or self.input_kind in {
            INPUT_SOLVER_OUTPUT,
            INPUT_UNSUPPORTED,
        }

    def to_dict(self) -> Dict[str, Any]:
        """Return a deterministic JSON-safe representation."""
        return {
            "joint": self.joint,
            "mmdName": self.mmd_name,
            "boneIndex": self.bone_index,
            "pmxFlags": self.pmx_flags,
            "inputKind": self.input_kind,
            "authoredPlugs": list(self.authored_plugs),
            "warnings": list(self.warnings),
            "blockers": list(self.blockers),
            "ikSolvers": list(self.ik_solvers),
            "incoming": [connection.to_dict() for connection in self.incoming],
            "fixedAxis": list(self.fixed_axis) if self.fixed_axis is not None else None,
        }


@dataclass(frozen=True)
class MmdControlRigRoleBinding:
    """Resolved semantic controller role and its safe MMD input binding."""

    role: str
    status: str
    binding: Optional[MmdControlRigBoneBinding] = None
    fallback: Optional[str] = None
    warnings: Tuple[str, ...] = ()
    blockers: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        """Return a deterministic JSON-safe representation."""
        return {
            "role": self.role,
            "status": self.status,
            "binding": self.binding.to_dict() if self.binding is not None else None,
            "fallback": self.fallback,
            "warnings": list(self.warnings),
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True)
class MmdControlRigSpec:
    """Versioned report consumed by future MMD control-rig builders."""

    model_root: str
    roles: Tuple[MmdControlRigRoleBinding, ...]
    bones: Tuple[MmdControlRigBoneBinding, ...]
    display_frames: Tuple[Mapping[str, Any], ...] = ()
    warnings: Tuple[str, ...] = ()
    blockers: Tuple[str, ...] = ()
    schema: str = CONTROL_RIG_SPEC_SCHEMA
    version: int = CONTROL_RIG_SPEC_VERSION

    @property
    def can_build_mvp(self) -> bool:
        """Return whether every MVP role has a usable non-blocked binding."""
        return not self.blockers and all(
            role.status in {STATUS_READY, STATUS_FALLBACK}
            and role.binding is not None
            and not role.binding.blocked
            for role in self.roles
            if role.role in _MVP_ROLE_NAMES
        )

    @property
    def roles_by_name(self) -> Dict[str, MmdControlRigRoleBinding]:
        """Return semantic role bindings keyed by their stable role names."""
        return {role.role: role for role in self.roles}

    def to_dict(self) -> Dict[str, Any]:
        """Return a deterministic JSON-safe representation."""
        return {
            "schema": self.schema,
            "version": self.version,
            "modelRoot": self.model_root,
            "canBuildMvp": self.can_build_mvp,
            "warnings": list(self.warnings),
            "blockers": list(self.blockers),
            "roles": [role.to_dict() for role in self.roles],
            "bones": [bone.to_dict() for bone in self.bones],
            "displayFrames": [dict(frame) for frame in self.display_frames],
        }


@dataclass(frozen=True)
class _RoleDefinition:
    role: str
    mmd_names: Tuple[str, ...]
    fallback_role: Optional[str] = None
    fallback_model_root: bool = False
    requires_ik_solver: bool = False
    uses_solver_input: bool = False
    twist_primary: bool = False


_MVP_ROLE_DEFINITIONS = (
    _RoleDefinition("master", ("全ての親",), fallback_model_root=True),
    _RoleDefinition("center", ("センター",)),
    _RoleDefinition("groove", ("グルーブ",), fallback_role="center"),
    _RoleDefinition("left_foot_ik", ("左足IK",), requires_ik_solver=True),
    _RoleDefinition("right_foot_ik", ("右足IK",), requires_ik_solver=True),
)
_MVP_ROLE_NAMES = frozenset(definition.role for definition in _MVP_ROLE_DEFINITIONS)

_FINGER_DEFINITIONS = (
    ("thumb", ("親指",), (0, 1, 2)),
    ("index", ("人指", "人差指"), (1, 2, 3)),
    ("middle", ("中指",), (1, 2, 3)),
    ("ring", ("薬指",), (1, 2, 3)),
    ("pinky", ("小指",), (1, 2, 3)),
)
_FINGER_ROLE_DEFINITIONS = tuple(
    _RoleDefinition(
        f"{side}_{finger}_{index}",
        tuple(f"{mmd_side}{mmd_finger}{index}" for mmd_finger in mmd_fingers),
    )
    for side, mmd_side in (("left", "左"), ("right", "右"))
    for finger, mmd_fingers, indexes in _FINGER_DEFINITIONS
    for index in indexes
)

_OPTIONAL_FK_ROLE_DEFINITIONS = (
    _RoleDefinition("waist", ("腰",)),
    _RoleDefinition("left_foot_ik_parent", ("左足IK親",)),
    _RoleDefinition("right_foot_ik_parent", ("右足IK親",)),
    _RoleDefinition("left_toe_ik", ("左つま先IK",), requires_ik_solver=True),
    _RoleDefinition("right_toe_ik", ("右つま先IK",), requires_ik_solver=True),
    _RoleDefinition("lower_body", ("下半身",)),
    _RoleDefinition("upper_body", ("上半身",)),
    _RoleDefinition("upper_body2", ("上半身2",)),
    _RoleDefinition("neck", ("首",)),
    _RoleDefinition("head", ("頭",)),
    _RoleDefinition("both_eyes", ("両目",)),
    _RoleDefinition("left_shoulder", ("左肩",)),
    _RoleDefinition("left_arm", ("左腕",)),
    _RoleDefinition("left_elbow", ("左ひじ", "左肘")),
    _RoleDefinition("left_wrist", ("左手首",)),
    _RoleDefinition("right_shoulder", ("右肩",)),
    _RoleDefinition("right_arm", ("右腕",)),
    _RoleDefinition("right_elbow", ("右ひじ", "右肘")),
    _RoleDefinition("right_wrist", ("右手首",)),
    _RoleDefinition("left_leg", ("左足",), uses_solver_input=True),
    _RoleDefinition("left_knee", ("左ひざ", "左膝"), uses_solver_input=True),
    _RoleDefinition("right_leg", ("右足",), uses_solver_input=True),
    _RoleDefinition("right_knee", ("右ひざ", "右膝"), uses_solver_input=True),
) + _FINGER_ROLE_DEFINITIONS

# Optional ring controllers are deliberately limited to the four canonical
# primary twist bones. Their secondary 捩1〜3 children remain outside this
# role table and keep the existing mmdAppend distribution.
_OPTIONAL_TWIST_ROLE_DEFINITIONS = tuple(
    _RoleDefinition(role, names, twist_primary=True)
    for role, names in (
        ("left_arm_twist", ("左腕捩",)),
        ("right_arm_twist", ("右腕捩",)),
        ("left_wrist_twist", ("左手捩",)),
        ("right_wrist_twist", ("右手捩",)),
    )
)


def classify_mmd_control_rig(
    model_root: str,
    bone_facts: Iterable[MmdControlRigBoneFact],
    display_frames: Iterable[Mapping[str, Any]] = (),
) -> MmdControlRigSpec:
    """Classify bone facts into a deterministic, report-only rig specification."""
    facts = tuple(sorted(bone_facts, key=_bone_fact_sort_key))
    bindings = tuple(_classify_bone_input(fact) for fact in facts)
    bindings_by_joint = {binding.joint: binding for binding in bindings}
    facts_by_name: Dict[str, List[MmdControlRigBoneFact]] = {}
    for fact in facts:
        normalized = normalize_mmd_bone_name(fact.mmd_name) or ""
        facts_by_name.setdefault(normalized, []).append(fact)

    roles: List[MmdControlRigRoleBinding] = []
    resolved_roles: Dict[str, MmdControlRigRoleBinding] = {}
    report_warnings: List[str] = []
    report_blockers: List[str] = []
    for definition in _MVP_ROLE_DEFINITIONS:
        role = _resolve_role(
            definition,
            facts_by_name,
            bindings_by_joint,
            resolved_roles,
            model_root,
        )
        roles.append(role)
        resolved_roles[role.role] = role
        report_warnings.extend(role.warnings)
        report_blockers.extend(role.blockers)

    for definition in _OPTIONAL_FK_ROLE_DEFINITIONS:
        role = _resolve_role(
            definition,
            facts_by_name,
            bindings_by_joint,
            resolved_roles,
            model_root,
            optional=True,
        )
        roles.append(role)
        resolved_roles[role.role] = role
        report_warnings.extend(role.warnings)

    for definition in _OPTIONAL_TWIST_ROLE_DEFINITIONS:
        role = _resolve_role(
            definition,
            facts_by_name,
            bindings_by_joint,
            resolved_roles,
            model_root,
            optional=True,
        )
        roles.append(role)
        resolved_roles[role.role] = role
        report_warnings.extend(role.warnings)

    return MmdControlRigSpec(
        model_root=str(model_root),
        roles=tuple(roles),
        bones=bindings,
        display_frames=tuple(dict(frame) for frame in display_frames),
        warnings=tuple(_unique_sorted(report_warnings)),
        blockers=tuple(_unique_sorted(report_blockers)),
    )


def analyze_mmd_control_rig(model_root: str, cmds_module=None) -> MmdControlRigSpec:
    """Collect one Maya model's graph facts and return its control-rig spec.

    Args:
        model_root: Imported MMD model root whose descendant joints are read.
        cmds_module: Optional Maya ``cmds`` compatible module for tests.

    Raises:
        ValueError: If the model root is missing or contains no indexed MMD joints.
    """
    cmds = cmds_module or maya_cmds()
    roots = cmds.ls(model_root, long=True) or []
    if not roots:
        raise ValueError("MMD control-rig analysis requires an existing model root")
    canonical_root = str(roots[0])
    joints = list(
        cmds.listRelatives(
            canonical_root,
            allDescendents=True,
            type="joint",
            fullPath=True,
        )
        or []
    )
    if cmds.nodeType(canonical_root) == "joint":
        joints.append(canonical_root)

    bone_rows = []
    for joint in sorted(set(str(node) for node in joints)):
        if not _has_attr(cmds, joint, ATTR_MMD_BONE_INDEX):
            continue
        fixed_axis = _fixed_axis_value(cmds, joint)
        bone_rows.append(
            {
                "joint": joint,
                "mmd_name": str(_get_attr(cmds, joint, ATTR_MMD_BONE_NAME, "") or ""),
                "bone_index": int(_get_attr(cmds, joint, ATTR_MMD_BONE_INDEX, -1)),
                "pmx_flags": int(_get_attr(cmds, joint, ATTR_MMD_BONE_FLAGS, 0)),
                "incoming": _collect_incoming_connections(cmds, joint),
                "fixed_axis": fixed_axis,
            }
        )
    if not bone_rows:
        raise ValueError(f"{canonical_root}: no indexed MMD joints")

    owned_joints = {row["joint"] for row in bone_rows}
    ik_solvers_by_name = _collect_owned_ik_solvers(cmds, owned_joints)
    facts = [
        MmdControlRigBoneFact(
            joint=row["joint"],
            mmd_name=row["mmd_name"],
            bone_index=row["bone_index"],
            pmx_flags=row["pmx_flags"],
            incoming=row["incoming"],
            ik_solvers=tuple(
                ik_solvers_by_name.get(
                    normalize_mmd_bone_name(row["mmd_name"]) or "",
                    (),
                )
            ),
            solver_input_plugs=_solver_input_rotate_plugs(cmds, row["incoming"]),
            bone_morph_base_plugs=_bone_morph_base_plugs(
                cmds,
                row["joint"],
                row["incoming"],
            ),
            fixed_axis=row["fixed_axis"],
        )
        for row in bone_rows
    ]
    display_frames = display_frames_from_json(
        str(
            _get_attr(
                cmds,
                canonical_root,
                ATTR_MMD_DISPLAY_FRAMES_JSON,
                "",
            )
            or ""
        )
    )
    return classify_mmd_control_rig(
        canonical_root,
        facts,
        display_frames=display_frames,
    )


def _classify_bone_input(fact: MmdControlRigBoneFact) -> MmdControlRigBoneBinding:
    incoming = tuple(
        sorted(
            fact.incoming,
            key=lambda item: (
                item.destination_plug,
                item.source_node_type,
                item.source_plug,
            ),
        )
    )
    physics = [row for row in incoming if row.source_node_type == "mmdPhysicsBoneDriver"]
    solver_outputs = [
        row
        for row in incoming
        if row.source_node_type == "mmdCcdIk"
        and ".outputRotate" in row.source_plug
    ]
    append_outputs = [
        row
        for row in incoming
        if row.source_node_type == "mmdAppend"
        and (
            ".outputRotate" in row.source_plug
            or ".outputTranslate" in row.source_plug
        )
    ]
    bone_morph_outputs = [
        row
        for row in incoming
        if row.source_node_type == "mmdBoneMorphAccum"
        and (
            ".outputRotate" in row.source_plug
            or ".outputTranslate" in row.source_plug
        )
    ]
    bone_morph_base_plugs = tuple(
        _unique_sorted(fact.bone_morph_base_plugs)
    )
    unsupported = [
        row
        for row in incoming
        if row not in physics
        and row not in solver_outputs
        and row not in append_outputs
        and row not in bone_morph_outputs
        and not _is_animation_source(row.source_node_type)
    ]

    if fact.pmx_flags & int(PmxBoneFlag.DEFORM_AFTER_PHYSICS):
        blocker = "after-physics bone cannot be claimed by the MVP control rig"
        return _bone_binding(
            fact,
            INPUT_UNSUPPORTED,
            (),
            incoming,
            blockers=(blocker,),
        )
    if physics:
        blockers = tuple(
            _unique_sorted(
                "physics-owned input cannot be claimed: " + row.destination_plug
                for row in physics
            )
        )
        return _bone_binding(fact, INPUT_UNSUPPORTED, (), incoming, blockers=blockers)
    if solver_outputs:
        blockers = tuple(
            _unique_sorted(
                "solver output cannot be used as a controller input: " + row.destination_plug
                for row in solver_outputs
            )
        )
        return _bone_binding(fact, INPUT_SOLVER_OUTPUT, (), incoming, blockers=blockers)
    if unsupported:
        blockers = tuple(
            _unique_sorted(
                "external writer requires explicit ownership policy: " + row.source_plug
                for row in unsupported
            )
        )
        return _bone_binding(fact, INPUT_UNSUPPORTED, (), incoming, blockers=blockers)
    if bone_morph_base_plugs:
        if fact.ik_solvers:
            # IK controllers remain responsible for solver state, but their
            # authored TRS must enter the accumulator so morph and control
            # values have one effective writer before the solver goal.
            return _bone_binding(
                fact,
                INPUT_IK_CONTROLLER,
                bone_morph_base_plugs,
                incoming,
            )
        return _bone_binding(
            fact,
            INPUT_BONE_MORPH_BASE,
            bone_morph_base_plugs,
            incoming,
        )
    if append_outputs:
        authored = []
        for row in append_outputs:
            node = row.source_plug.split(".", 1)[0]
            if ".outputRotate" in row.source_plug:
                authored.append(f"{node}.baseRotate")
            if ".outputTranslate" in row.source_plug:
                authored.append(f"{node}.baseTranslate")
        return _bone_binding(
            fact,
            INPUT_APPEND_BASE,
            tuple(_unique_sorted(authored)),
            incoming,
        )
    if fact.ik_solvers:
        return _bone_binding(
            fact,
            INPUT_IK_CONTROLLER,
            (f"{fact.joint}.translate", f"{fact.joint}.rotate"),
            incoming,
        )
    return _bone_binding(
        fact,
        INPUT_DIRECT_CHANNEL,
        (f"{fact.joint}.translate", f"{fact.joint}.rotate"),
        incoming,
    )


def _bone_binding(
    fact: MmdControlRigBoneFact,
    input_kind: str,
    authored_plugs: Tuple[str, ...],
    incoming: Tuple[MmdControlRigConnectionFact, ...],
    *,
    warnings: Tuple[str, ...] = (),
    blockers: Tuple[str, ...] = (),
) -> MmdControlRigBoneBinding:
    return MmdControlRigBoneBinding(
        joint=fact.joint,
        mmd_name=fact.mmd_name,
        bone_index=fact.bone_index,
        pmx_flags=fact.pmx_flags,
        input_kind=input_kind,
        authored_plugs=authored_plugs,
        warnings=warnings,
        blockers=blockers,
        ik_solvers=tuple(sorted(set(fact.ik_solvers))),
        incoming=incoming,
        fixed_axis=fact.fixed_axis,
    )


def _resolve_role(
    definition: _RoleDefinition,
    facts_by_name: Mapping[str, Sequence[MmdControlRigBoneFact]],
    bindings_by_joint: Mapping[str, MmdControlRigBoneBinding],
    resolved_roles: Mapping[str, MmdControlRigRoleBinding],
    model_root: str,
    *,
    optional: bool = False,
) -> MmdControlRigRoleBinding:
    candidates: List[MmdControlRigBoneFact] = []
    for name in definition.mmd_names:
        normalized = normalize_mmd_bone_name(name) or ""
        candidates.extend(facts_by_name.get(normalized, ()))
    candidates.sort(key=_bone_fact_sort_key)

    if not candidates:
        if definition.fallback_role:
            fallback_role = resolved_roles.get(definition.fallback_role)
            if fallback_role is not None and fallback_role.binding is not None:
                warning = (
                    f"{definition.role}: missing MMD bone; using "
                    f"{definition.fallback_role} as fallback"
                )
                return MmdControlRigRoleBinding(
                    role=definition.role,
                    status=STATUS_FALLBACK,
                    binding=fallback_role.binding,
                    fallback=definition.fallback_role,
                    warnings=(warning,),
                )
        if definition.fallback_model_root:
            warning = f"{definition.role}: missing MMD bone; model root fallback requires builder support"
            fallback_binding = MmdControlRigBoneBinding(
                joint=model_root,
                mmd_name="",
                bone_index=None,
                pmx_flags=0,
                input_kind=INPUT_DIRECT_CHANNEL,
                authored_plugs=(f"{model_root}.translate", f"{model_root}.rotate"),
                warnings=(warning,),
            )
            return MmdControlRigRoleBinding(
                role=definition.role,
                status=STATUS_FALLBACK,
                binding=fallback_binding,
                fallback="model_root",
                warnings=(warning,),
            )
        if optional:
            warning = f"{definition.role}: optional MMD bone is missing; control omitted"
            return MmdControlRigRoleBinding(
                role=definition.role,
                status=STATUS_MISSING,
                warnings=(warning,),
            )
        blocker = f"{definition.role}: required MMD bone is missing"
        return MmdControlRigRoleBinding(
            role=definition.role,
            status=STATUS_MISSING,
            blockers=(blocker,),
        )

    winner = candidates[0]
    binding = bindings_by_joint[winner.joint]
    warnings = []
    blockers = list(binding.blockers)
    if definition.twist_primary:
        if not (binding.pmx_flags & int(PmxBoneFlag.AXIS_FIXED)):
            blockers.append(
                f"{definition.role}: primary twist ring requires PMX fixed-axis authority"
            )
        if binding.fixed_axis is None:
            blockers.append(
                f"{definition.role}: fixed-axis metadata is missing or degenerate"
            )
        if binding.input_kind not in {INPUT_DIRECT_CHANNEL, INPUT_APPEND_BASE}:
            blockers.append(
                f"{definition.role}: primary twist ring requires direct or Append-authored input"
            )
    if definition.uses_solver_input:
        authored_plugs = winner.bone_morph_base_plugs or winner.solver_input_plugs
        if authored_plugs:
            binding = MmdControlRigBoneBinding(
                joint=winner.joint,
                mmd_name=winner.mmd_name,
                bone_index=winner.bone_index,
                pmx_flags=winner.pmx_flags,
                input_kind=INPUT_IK_LINK_INPUT,
                authored_plugs=authored_plugs,
                ik_solvers=tuple(
                    sorted({plug.split(".", 1)[0] for plug in winner.solver_input_plugs})
                ),
                incoming=winner.incoming,
                fixed_axis=winner.fixed_axis,
            )
            blockers = []
        else:
            blockers.append(
                f"{definition.role}: MMD IK link has no solver pre-rotation input"
            )
    if len(candidates) > 1:
        duplicates = ", ".join(candidate.joint for candidate in candidates[1:])
        warnings.append(
            f"{definition.role}: duplicate MMD bone candidates ignored: {duplicates}"
        )
    if definition.requires_ik_solver and not binding.ik_solvers:
        blockers.append(
            f"{definition.role}: MMD IK controller has no owned mmdCcdIk solver"
        )
    status = STATUS_BLOCKED if binding.blocked or blockers else STATUS_READY
    return MmdControlRigRoleBinding(
        role=definition.role,
        status=status,
        binding=binding,
        warnings=tuple(warnings),
        blockers=tuple(_unique_sorted(blockers)),
    )


def _solver_input_rotate_plugs(
    cmds,
    incoming: Iterable[MmdControlRigConnectionFact],
) -> Tuple[str, ...]:
    """Resolve an IK link's writable pre-solver rotation children."""

    plugs = []
    for row in incoming:
        if row.source_node_type != "mmdCcdIk" or ".outputRotate[" not in row.source_plug:
            continue
        node, attribute = row.source_plug.split(".", 1)
        match = re.search(r"outputRotate\[(\d+)\]", attribute)
        if match is None:
            continue
        try:
            chain = json.loads(cmds.getAttr(f"{node}.chainJson") or "{}")
            links = chain.get("links") or []
            link_index = int(match.group(1))
            slot = int(links[link_index].get("bone_slot", link_index))
        except (
            AttributeError,
            IndexError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            continue
        base = f"{node}.inputRotate[{slot}]"
        plugs.extend(f"{base}.inputRotateElement{axis}" for axis in "XYZ")
    return tuple(_unique_sorted(plugs))


def _bone_morph_base_plugs(
    cmds,
    joint: str,
    incoming: Iterable[MmdControlRigConnectionFact],
) -> Tuple[str, ...]:
    """Resolve supported accumulator outputs to their writable base inputs.

    ``mmdBoneMorphAccum`` is an intermediate composer, never an authoring
    output.  Only the known MMD routes are followed: accumulator output to a
    joint, an ``mmdAppend`` base, or an ``mmdCcdIk`` pre-input.  Unknown
    composer/writer graphs intentionally return no route and remain blocked by
    :func:`_classify_bone_input`.
    """

    plugs = []
    for row in incoming:
        source_node, source_attr = row.source_plug.split(".", 1)
        if row.source_node_type == "mmdBoneMorphAccum":
            base = _accum_base_from_output(source_node, source_attr)
            if base:
                plugs.append(base)
            continue
        if row.source_node_type == "mmdAppend":
            append_base = _append_base_from_output(source_attr)
            plugs.extend(
                _accum_bases_from_destination(
                    cmds,
                    f"{source_node}.{append_base}" if append_base else None,
                )
            )
            continue
        if row.source_node_type == "mmdCcdIk" and ".outputRotate" in source_attr:
            input_plug = _ccd_ik_input_compound_from_output(
                cmds,
                source_node,
                source_attr,
            )
            plugs.extend(_accum_bases_from_destination(cmds, input_plug))

    # A solver's input may be the effective destination even when its output
    # is the only source visible on the authored link joint.  Resolve the
    # solver pre-input separately so thigh/knee FK does not bypass the morph
    # composer.
    for target in _solver_input_rotate_plugs(cmds, incoming):
        input_compound = target.rsplit(".inputRotateElement", 1)[0]
        plugs.extend(_accum_bases_from_destination(cmds, input_compound))

    return tuple(_unique_sorted(plugs))


def _accum_base_from_output(node: str, source_attr: str) -> Optional[str]:
    if source_attr.startswith("outputRotate"):
        return f"{node}.baseRotate"
    if source_attr.startswith("outputTranslate"):
        return f"{node}.baseTranslate"
    return None


def _append_base_from_output(source_attr: str) -> Optional[str]:
    if source_attr.startswith("outputRotate"):
        return "baseRotate"
    if source_attr.startswith("outputTranslate"):
        return "baseTranslate"
    return None


def _accum_bases_from_destination(cmds, destination: Optional[str]) -> Tuple[str, ...]:
    """Find accumulator sources feeding a known pre-input destination."""
    if not destination:
        return ()
    plugs = []
    destinations = [destination]
    if ".inputRotate[" in destination:
        destinations.extend(
            f"{destination}.inputRotateElement{axis}" for axis in "XYZ"
        )
    elif destination.endswith(("baseRotate", "baseTranslate")):
        destinations.extend(f"{destination}{axis}" for axis in "XYZ")
    for target in destinations:
        try:
            sources = cmds.listConnections(
                target,
                source=True,
                destination=False,
                plugs=True,
            ) or []
        except Exception:
            sources = []
        for source in sources:
            source_node, source_attr = str(source).split(".", 1)
            try:
                source_type = str(cmds.nodeType(source_node))
            except Exception:
                continue
            if source_type != "mmdBoneMorphAccum":
                continue
            base = _accum_base_from_output(source_node, source_attr)
            if base:
                plugs.append(base)
    return tuple(_unique_sorted(plugs))


def _ccd_ik_input_compound_from_output(
    cmds,
    ik_node: str,
    source_attr: str,
) -> Optional[str]:
    link_index = _array_index(source_attr, "outputRotate")
    if link_index is None:
        return None
    try:
        chain = json.loads(cmds.getAttr(f"{ik_node}.chainJson") or "{}")
        links = chain.get("links") or []
        slot = int(links[link_index].get("bone_slot", link_index))
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        slot = link_index
    return f"{ik_node}.inputRotate[{slot}]"


def _array_index(source_attr: str, array_name: str) -> Optional[int]:
    match = re.match(rf"^{re.escape(array_name)}\[(\d+)\]", source_attr)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def _collect_incoming_connections(cmds, joint: str) -> Tuple[MmdControlRigConnectionFact, ...]:
    rows = {}
    for attribute in _INPUT_ATTRS:
        destination = f"{joint}.{attribute}"
        for source in cmds.listConnections(
            destination,
            source=True,
            destination=False,
            plugs=True,
        ) or []:
            source = str(source)
            source_node = source.split(".", 1)[0]
            row = MmdControlRigConnectionFact(
                source_plug=source,
                destination_plug=destination,
                source_node_type=str(cmds.nodeType(source_node)),
            )
            rows[(row.source_plug, row.destination_plug)] = row
    return tuple(
        sorted(
            rows.values(),
            key=lambda item: (item.destination_plug, item.source_plug),
        )
    )


def _collect_owned_ik_solvers(cmds, owned_joints: Iterable[str]) -> Dict[str, Tuple[str, ...]]:
    owned = set(owned_joints)
    result: Dict[str, List[str]] = {}
    for node in sorted(str(item) for item in (cmds.ls(type="mmdCcdIk") or [])):
        connected = set()
        for joint in cmds.listConnections(
            node,
            source=True,
            destination=True,
            type="joint",
        ) or []:
            connected.update(str(item) for item in (cmds.ls(joint, long=True) or []))
        if not connected.intersection(owned):
            continue
        if not _has_attr(cmds, node, "mmd_ik_bone_name"):
            continue
        mmd_name = normalize_mmd_bone_name(
            str(_get_attr(cmds, node, "mmd_ik_bone_name", "") or "")
        )
        if mmd_name:
            result.setdefault(mmd_name, []).append(node)
    return {
        name: tuple(sorted(set(nodes)))
        for name, nodes in sorted(result.items())
    }


def _is_animation_source(node_type: str) -> bool:
    return (
        node_type in _ANIMATION_NODE_TYPES
        or node_type.startswith("animCurve")
        or node_type.startswith("animBlendNode")
    )


def _bone_fact_sort_key(fact: MmdControlRigBoneFact) -> Tuple[int, str]:
    index = fact.bone_index if fact.bone_index is not None else 1_000_000_000
    return int(index), fact.joint


def _has_attr(cmds, node: str, attribute: str) -> bool:
    try:
        return bool(cmds.attributeQuery(attribute, node=node, exists=True))
    except Exception:
        return False


def _get_attr(cmds, node: str, attribute: str, default=None):
    if not _has_attr(cmds, node, attribute):
        return default
    try:
        return cmds.getAttr(f"{node}.{attribute}")
    except Exception:
        return default


def _fixed_axis_value(cmds, node: str) -> Optional[Tuple[float, float, float]]:
    """Read and validate imported PMX fixed-axis metadata."""

    value = _get_attr(cmds, node, ATTR_MMD_FIXED_AXIS, None)
    if value is None:
        value = _get_attr(cmds, node, ATTR_MMD_AXIS_DIRECTION, None)
    if isinstance(value, (list, tuple)) and len(value) == 1:
        value = value[0]
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        vector = tuple(float(component) for component in value)
    except (TypeError, ValueError):
        return None
    length = sum(component * component for component in vector) ** 0.5
    return vector if length > 1.0e-8 and all(math.isfinite(component) for component in vector) else None


def _unique_sorted(values: Iterable[str]) -> List[str]:
    return sorted(set(str(value) for value in values if value))
