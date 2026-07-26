"""UI-neutral HumanIK setup, source/target preview, and bake orchestration.

This module is the narrow frontend boundary used by UI or command adapters.  It
keeps Maya ``cmds``/MEL dependencies injectable so lifecycle behavior can be
tested without opening HumanIK panels.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from mmd_tools.core.humanik_bake import HumanIkBakeResult, bake_humanik_target_preview
from mmd_tools.core.humanik_builder import (
    HumanIkCharacterCreationError,
    create_humanik_definition,
    delete_humanik_character,
    get_humanik_definition_lock_state,
    lock_humanik_definition,
    resolve_scene_humanik_assignments,
)
from mmd_tools.core.humanik_constraints import (
    BLOCKING_CLASSIFICATIONS,
    collect_hik_ownership_report,
    is_preisolated_mmd_ccdik_feedback_row,
    is_supported_mmd_ccdik_feedback_row,
    preisolated_mmd_ccdik_nodes_from_disconnected_edges,
)
from mmd_tools.core.humanik_control_rig import (
    HumanIkControlRigBakeResult,
    HumanIkControlRigTransaction,
    bake_humanik_control_rig,
    begin_humanik_control_rig,
    delete_orphaned_control_rig,
    register_control_rig_transaction,
    stop_humanik_control_rig,
)
from mmd_tools.core.humanik_preview import (
    HumanIkTargetPreview,
    begin_humanik_target_preview,
    stop_humanik_target_preview,
)
from mmd_tools.core.humanik_resolver import (
    HumanIkBoneAssignment,
    HumanIkResolveResult,
)
from mmd_tools.core.humanik_retarget import (
    HIK_CHARACTER_NODE_TYPE,
    HUMANIK_DIRECT_INPUT_TYPE,
    describe_humanik_import_lock,
    find_humanik_character_for_model,
    list_scene_hik_characters,
)
from mmd_tools.core.humanik_stance import HumanIkStanceTransaction, canonical_stance_targets
from mmd_tools.core.humanik_transaction import (
    load_humanik_restore_state,
    persist_humanik_restore_state,
)
from mmd_tools.core.humanik_utils import maya_cmds, maya_mel, mel_string
from mmd_tools.core.logger import get_logger
from mmd_tools.services.scene_model_service import SceneModelService


logger = get_logger(__name__)


FINGER_HIK_MARKERS = ("Index", "Middle", "Ring", "Pinky", "Thumb")

# --- Frontend mode constants -------------------------------------------------
#
# These describe the mutually-exclusive high-level state ``describe_frontend_state``
# reports for UI consumption.  Preview and Control Rig transactions cannot be
# simultaneously active on the *same* session (``create_control_rig`` and every
# other mutating method reject while a preview is active via
# ``_reject_active_preview_mutation``), so TARGET_PREVIEW always takes priority
# over CONTROL_RIG when both would otherwise apply.
FRONTEND_MODE_NEUTRAL = "neutral"
FRONTEND_MODE_SOURCE = "source"
FRONTEND_MODE_TARGET_PREVIEW = "target_preview"
FRONTEND_MODE_CONTROL_RIG = "control_rig"

# --- Reason codes -------------------------------------------------------------
#
# One code per guard condition currently enforced by
# ``HumanIkFrontendSession``'s mutating methods.  ``describe_frontend_state``
# mirrors these guards to predict allowed/blocked without executing them; the
# guard logic itself remains the source of truth.
REASON_PREVIEW_ACTIVE = "preview_active"
REASON_NOT_CHARACTERIZED = "not_characterized"
REASON_NO_SOURCE = "no_source"
REASON_TARGET_IS_SOURCE = "target_is_source"
REASON_PROFILE_MISMATCH = "profile_mismatch"
REASON_MODEL_IS_SOURCE = "model_is_source"
REASON_NO_ACTIVE_PREVIEW = "no_active_preview"
REASON_NO_ACTIVE_CONTROL_RIG = "no_active_control_rig"
REASON_ALREADY_CHARACTERIZED_OTHER_PROFILE = "already_characterized_other_profile"
REASON_NOTHING_TO_RESTORE = "nothing_to_restore"
REASON_MODEL_REQUIRED = "model_required"

# VMD import lock reasons, mirroring ``humanik_retarget.HumanIkImportLock.blocked``.
REASON_IMPORT_BLOCKED_TARGET_PREVIEW = "import_blocked_target_preview"
REASON_IMPORT_BLOCKED_CONTROL_RIG = "import_blocked_control_rig"
_IMPORT_LOCK_REASON_BY_BLOCKED = {
    "target_preview": REASON_IMPORT_BLOCKED_TARGET_PREVIEW,
    "control_rig": REASON_IMPORT_BLOCKED_CONTROL_RIG,
}


def _action_allowed() -> Dict[str, Any]:
    """Return an ``actions`` entry describing a permitted operation."""
    return {"allowed": True, "reasonCode": None, "reasonText": None}


def _action_blocked(reason_code: str, reason_text: str) -> Dict[str, Any]:
    """Return an ``actions`` entry describing a blocked operation."""
    return {"allowed": False, "reasonCode": reason_code, "reasonText": reason_text}

# Historical name: this is the body-only profile constant, kept from when the
# frontend's default assignment profile excluded fingers. Since Phase B6 the
# UI default is ``FULL_ASSIGNMENT_PROFILE``; the name is unchanged for
# backward compatibility with existing callers/tests.
FRONTEND_ASSIGNMENT_PROFILE = "body-only"
FULL_ASSIGNMENT_PROFILE = "full"
_ASSIGNMENT_PROFILES = frozenset({FRONTEND_ASSIGNMENT_PROFILE, FULL_ASSIGNMENT_PROFILE})
REFERENCE_QUALITY_DIAGNOSTICS = {
    "status": "experimental",
    "referenceS5bBodyMatrixResidual": 0.0298786502441323,
    "referenceS5bBodyMatrixResidualTolerance": 0.001,
    "fingerStatus": "deferred",
}
EXPECTED_BODY_ASSIGNMENT_COUNT = 25
EXPECTED_FINGER_ASSIGNMENT_COUNT = 30
EXPECTED_FULL_ASSIGNMENT_COUNT = EXPECTED_BODY_ASSIGNMENT_COUNT + EXPECTED_FINGER_ASSIGNMENT_COUNT


# --- HUMANIK-RESTORE-GAPS-1 slice 1c: orphaned Control Rig recovery --------
#
# Structured warnings attached to every orphaned Control Rig this session
# recovers via scene facts alone (see ``_recover_orphaned_control_rigs``).
# There is no surviving ``HumanIkRestoreState`` for these -- the
# in-memory transaction table was either lost (scene reopen) or never
# existed for this Control Rig (created outside ``create_control_rig``) --
# so the writer-isolation reconnection and the pre-characterize stance
# restore that a *tracked* teardown performs cannot happen here. These
# strings must stay visible in the recovery report rather than letting a
# "recovered" entry read as a full restore.
ORPHAN_RECOVERY_WARNING_RESTORE_STATE_UNAVAILABLE = (
    "restore_state_unavailable: no MMD-writer-isolation restore_state survived for this "
    "Control Rig (scene reopen, or created outside create_control_rig), so "
    "any muted MMD writer edge could not be reconnected automatically."
)
ORPHAN_RECOVERY_WARNING_STANCE_UNAVAILABLE = (
    "stance_unavailable: the pre-characterize stance snapshot for this "
    "recovery is not tracked either, so the model's pose was not reverted "
    "to its pre-HumanIK stance."
)
ORPHAN_RECOVERY_UNRECOVERABLE_WARNINGS = (
    ORPHAN_RECOVERY_WARNING_RESTORE_STATE_UNAVAILABLE,
    ORPHAN_RECOVERY_WARNING_STANCE_UNAVAILABLE,
)


def _find_mmd_model_root_for_character(character: str, cmds) -> Optional[str]:
    """Return the MMD model root HIK-characterized as ``character``, from scene facts.

    Deliberately scene-fact based -- unlike ``find_binding_by_character`` this
    never consults a session's in-memory ``_bindings`` -- so it still answers
    correctly after a scene reopen or when the Control Rig was created by
    something other than this session entirely (HUMANIK-RESTORE-GAPS-1).  This
    is the safety gate for ``restore_mmd_rig``'s orphaned Control Rig
    recovery: a Control Rig is only ever deleted here when its character's
    joints resolve back to a real ``*_root``/MMD model in the scene, so a
    Control Rig belonging to an unrelated (non-MMD) HIK character is never
    touched.

    Any Maya query failure or an empty ``list_mmd_models()`` returns
    ``None`` -- "cannot prove this is MMD-driven" -- never raises.
    """
    try:
        model_roots = SceneModelService(cmds_module=cmds).list_mmd_models()
    except Exception:
        return None
    for model_root in model_roots:
        try:
            found = find_humanik_character_for_model(model_root, cmds_module=cmds)
        except Exception:
            continue
        if found and found == character:
            return model_root
    return None


def is_humanik_finger_assignment(assignment: HumanIkBoneAssignment) -> bool:
    """Return whether a HIK assignment belongs to a finger slot."""
    name = str(assignment.hik_bone)
    return any(marker in name for marker in FINGER_HIK_MARKERS)


def filter_humanik_body_assignments(result: HumanIkResolveResult) -> HumanIkResolveResult:
    """Return a body-only resolve result while retaining roll assignments."""
    return replace(
        result,
        assignments=tuple(
            assignment
            for assignment in result.assignments
            if not is_humanik_finger_assignment(assignment)
        ),
    )


def _split_body_assignments(
    result: HumanIkResolveResult,
) -> Tuple[HumanIkResolveResult, Tuple[HumanIkBoneAssignment, ...]]:
    excluded = tuple(
        assignment for assignment in result.assignments if is_humanik_finger_assignment(assignment)
    )
    return filter_humanik_body_assignments(result), excluded


def _normalize_assignment_profile(
    profile: Optional[str],
    include_fingers: Optional[bool],
) -> Tuple[str, bool]:
    """Resolve the explicit profile/finger flag pair used by setup and inspect."""
    if profile is None or not str(profile).strip():
        selected = FULL_ASSIGNMENT_PROFILE if include_fingers else FRONTEND_ASSIGNMENT_PROFILE
    else:
        selected = str(profile).strip().lower()
    if selected not in _ASSIGNMENT_PROFILES:
        allowed = ", ".join(sorted(_ASSIGNMENT_PROFILES))
        raise ValueError(f"Unknown HumanIK assignment profile: {selected}; expected one of: {allowed}")
    expected_include_fingers = selected == FULL_ASSIGNMENT_PROFILE
    if include_fingers is not None and bool(include_fingers) != expected_include_fingers:
        raise ValueError(
            "HumanIK assignment profile/include_fingers mismatch: "
            f"profile={selected}, include_fingers={bool(include_fingers)}"
        )
    return selected, expected_include_fingers


def _select_profile_result(
    result: HumanIkResolveResult,
    profile: str,
) -> Tuple[HumanIkResolveResult, Tuple[HumanIkBoneAssignment, ...]]:
    """Select assignments for a profile while retaining excluded finger evidence."""
    body_result, excluded = _split_body_assignments(result)
    if profile == FULL_ASSIGNMENT_PROFILE:
        return result, ()
    return body_result, excluded


def _maya_node_matches_assignment(actual_node: Any, expected_joint: str) -> bool:
    """Return whether a HIK readback node identifies ``expected_joint``.

    ``hikGetSkNode`` commonly returns a short DAG name even when the resolver
    kept a long path.  Comparing both the complete spelling and the leaf keeps
    the adoption check strict enough to reject a definition wired to another
    model while remaining compatible with Maya's normal short-name readback.
    """
    if actual_node is None:
        return False
    actual = str(actual_node).strip()
    expected = str(expected_joint or "").strip()
    if not actual or not expected:
        return False
    return actual == expected or actual.rsplit("|", 1)[-1] == expected.rsplit("|", 1)[-1]


def _assignment_row(assignment: HumanIkBoneAssignment) -> Dict[str, Any]:
    return {
        "joint": str(assignment.joint),
        "mmdBone": str(assignment.mmd_bone),
        "hikBone": str(assignment.hik_bone),
        "hikIndex": int(assignment.hik_index),
        "source": str(assignment.source),
        "boneIndex": assignment.bone_index,
    }


def _source_node_uuid(cmds: Any, node: str) -> Optional[str]:
    """Return one current Maya UUID, or ``None`` when identity is ambiguous."""
    try:
        values = cmds.ls(str(node), uuid=True) or []
    except Exception:
        return None
    return str(values[0]) if len(values) == 1 and values[0] else None


def _record_disconnected_source_uuids(transaction: Any, cmds: Any) -> None:
    """Attach current source-node identities before persisting a transaction."""
    for edge in getattr(transaction, "disconnected", ()) or ():
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source", ""))
        if "." not in source:
            continue
        source_node = source.rsplit(".", 1)[0]
        source_uuid = _source_node_uuid(cmds, source_node)
        if source_uuid is not None:
            edge["sourceNodeUuid"] = source_uuid


def _preisolated_feedback_nodes(
    transaction: Any,
    assignments: Iterable[Any],
    cmds_module: Any,
) -> Tuple[str, ...]:
    """Return active Control Rig foot nodes trusted for TARGET preflight.

    Feedback and ``mute_for_hik`` rows both retain their exact writer edges in
    ``transaction.disconnected``.  Only edges whose persisted source UUID
    still matches the current Maya node may authorize the post-isolation
    ``manual`` ownership shape.
    """
    if transaction is None or not bool(getattr(transaction, "active", False)):
        return ()
    edges = getattr(transaction, "disconnected", ()) or ()
    source_nodes = {
        str(edge.get("source", "")).rsplit(".", 1)[0]
        for edge in edges
        if isinstance(edge, Mapping) and "." in str(edge.get("source", ""))
    }
    try:
        cmds = cmds_module or maya_cmds()
    except Exception:
        return ()
    node_uuids = {
        node: source_uuid
        for node in source_nodes
        if (source_uuid := _source_node_uuid(cmds, node)) is not None
    }
    return preisolated_mmd_ccdik_nodes_from_disconnected_edges(
        edges,
        assignments,
        node_uuids,
    )


@dataclass
class HumanIkFrontendBinding:
    """Characterized HumanIK binding retained by a frontend session."""

    model_root: str
    character: str
    result: HumanIkResolveResult
    profile: str = FRONTEND_ASSIGNMENT_PROFILE
    excluded_finger_assignments: Tuple[HumanIkBoneAssignment, ...] = ()
    stance: Dict[str, Any] = field(default_factory=dict)
    control_rig_created: bool = False

    @property
    def assignments(self) -> Tuple[HumanIkBoneAssignment, ...]:
        """Return the assignments selected by this binding's profile."""
        return self.result.assignments

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe binding summary."""
        return {
            "modelRoot": self.model_root,
            "character": self.character,
            "profile": self.profile,
            "includeFingers": self.profile == FULL_ASSIGNMENT_PROFILE,
            "assignmentCount": len(self.assignments),
            "assignments": [_assignment_row(item) for item in self.assignments],
            "required": {
                "genericLockMinimumAssignmentCount": 1,
                "expectedAssignmentCount": (
                    EXPECTED_FULL_ASSIGNMENT_COUNT
                    if self.profile == FULL_ASSIGNMENT_PROFILE
                    else EXPECTED_BODY_ASSIGNMENT_COUNT
                ),
                "resolvedAssignmentCount": len(self.assignments),
            },
            "excludedFingerCount": len(self.excluded_finger_assignments),
            "excludedFingerAssignments": [
                _assignment_row(item) for item in self.excluded_finger_assignments
            ],
            "unresolved": {
                "missingMmdBones": list(self.result.missing_mmd_bones),
                "unindexedMmdBones": list(self.result.unindexed_mmd_bones),
            },
            "ambiguous": [_assignment_row(item) for item in self.result.duplicate_assignments],
            "blocked": [] if self.assignments else ["no_resolved_assignments"],
            "stance": dict(self.stance),
            "controlRigCreated": bool(self.control_rig_created),
        }

    def __getitem__(self, key: str) -> Any:
        """Provide mapping-style access for UI adapters."""
        return self.to_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        """Return a summary field without exposing Maya objects."""
        return self.to_dict().get(key, default)


class HumanIkFrontendSession:
    """Manage UI-independent HumanIK character and preview lifecycle."""

    def __init__(
        self,
        cmds_module=None,
        mel_module=None,
        ownership_id: str = "mmd-tools:frontend",
        stance_transaction_factory: Optional[Callable[..., HumanIkStanceTransaction]] = None,
    ):
        self._cmds = cmds_module
        self._mel = mel_module
        self._ownership_id = str(ownership_id)
        self._bindings: Dict[str, HumanIkFrontendBinding] = {}
        self._source_model_root: Optional[str] = None
        # HUMANIK-EXTERNAL-SOURCE-1 ES-1: a SOURCE character already
        # characterized+locked in the scene by something other than this
        # session (mocap, plain HumanIK UI use, ...). Mutually exclusive
        # with ``_source_model_root`` -- selecting one clears the other --
        # since HumanIK has exactly one active retarget input per TARGET.
        self._external_source_character: Optional[str] = None
        self._target_model_root: Optional[str] = None
        self._ownership_report: Optional[Dict[str, Any]] = None
        self._preview: Optional[HumanIkTargetPreview] = None
        self._control_rig_transactions: Dict[str, HumanIkControlRigTransaction] = {}
        # A successful native Control Rig bake switches the TARGET character
        # away from the selected SOURCE and onto its Control Rig.  Retain that
        # fact separately from ``_preview``: the preview object still owns the
        # reversible MMD writer state needed by Bake From Control Rig, but the
        # user may safely clear the SOURCE selection without deleting the
        # baked Control Rig or its animation.
        self._control_rig_baked_roots: set[str] = set()
        self._pending_characters: Dict[str, str] = {}
        self._pending_stances: Dict[str, HumanIkStanceTransaction] = {}
        self._stance_transaction_factory = stance_transaction_factory or HumanIkStanceTransaction
        self._last_orphan_recovery: Dict[str, List[Dict[str, Any]]] = {
            "recovered": [],
            "skipped": [],
            "failed": [],
        }
        self._load_persisted_control_rig_transactions()

    def _load_persisted_control_rig_transactions(self) -> None:
        """Rebuild active Control Rig transactions from scene metadata.

        The scene payload is advisory and validated twice: first by the
        restore_state schema loader, then by proving that the recorded character is
        still the HIK character for a real MMD model root.  Invalid/stale or
        foreign records are ignored, never auto-adopted or deleted.
        """
        try:
            rows = load_humanik_restore_state(cmds_module=self._cmds)
            cmds = self._cmds or maya_cmds()
        except Exception:
            return
        for row in rows:
            try:
                model_root = str(row["modelRoot"])
                character = str(row["character"])
                if not model_root or not character:
                    continue
                if not cmds.objExists(model_root) or not cmds.objExists(character):
                    continue
                if _find_mmd_model_root_for_character(character, cmds) != model_root:
                    continue
                transaction = HumanIkControlRigTransaction.from_scene_dict(row)
                if not transaction.active:
                    continue
            except Exception as exc:  # noqa: BLE001 - stale/foreign metadata is rejected
                logger.warning("HumanIK persisted transaction rejected: %s", exc)
                continue
            self._control_rig_transactions[model_root] = transaction
            if transaction.baked:
                self._control_rig_baked_roots.add(model_root)
            register_control_rig_transaction(character, transaction)

    def _persist_control_rig_transactions(self, records=None) -> None:
        """Best-effort scene persistence for active Control Rig transactions."""
        if records is None:
            records = []
            for model_root, transaction in self._control_rig_transactions.items():
                if not getattr(transaction, "active", False):
                    continue
                to_scene_dict = getattr(transaction, "to_scene_dict", None)
                if to_scene_dict is None:
                    continue
                try:
                    records.append(to_scene_dict(model_root))
                except Exception as exc:  # noqa: BLE001 - test doubles/foreign transactions
                    logger.warning("HumanIK transaction persistence skipped: %s", exc)
        persist_humanik_restore_state(records, cmds_module=self._cmds)

    def _describe_scene_retarget_pair(self, model_root: str) -> Optional[Dict[str, Any]]:
        """Read an active native HIK SOURCE/TARGET pair from scene facts.

        A reopened scene can retain Maya's direct Character Input while the
        process-owned frontend session has no ``_bindings`` or preview object.
        This helper is deliberately read-only: it restores truthful UI labels
        without pretending the frontend owns the missing bake/restore context.
        """
        try:
            target_character = find_humanik_character_for_model(
                model_root,
                cmds_module=self._cmds,
            )
            if not target_character:
                return None
            mel = self._mel or maya_mel()
            input_type = int(
                mel.eval(f"hikGetInputType({mel_string(target_character)})")
            )
            source_character = str(
                mel.eval(
                    f"hikGetRetargetCharacterInput({mel_string(target_character)})"
                )
                or ""
            ).strip()
            # Maya's direct character input is enum 3.  A retained retargeter
            # node while Control Rig/Stance is the active input must not be
            # presented as the current SOURCE.
            if input_type != HUMANIK_DIRECT_INPUT_TYPE or not source_character:
                return None
            cmds = self._cmds or maya_cmds()
            source_model_root = _find_mmd_model_root_for_character(
                source_character,
                cmds,
            )
        except Exception:
            return None
        return {
            "source": {
                "modelRoot": source_model_root,
                "character": source_character,
                "external": source_model_root is None,
                "sceneDerived": True,
            },
            "target": {
                "modelRoot": model_root,
                "character": target_character,
                "sceneDerived": True,
            },
            "inputType": input_type,
        }

    def _reconcile_scene_retarget_context(
        self,
        model_root: str,
    ) -> Optional[Dict[str, Any]]:
        """Rebuild the usable frontend context for a persisted scene pair.

        Maya persists the direct Character Input and this plugin persists the
        Control Rig transaction, while ``_bindings`` and ``_preview`` are
        process-local.  When all scene facts agree, adopt the characterized
        SOURCE/TARGET definitions and rebuild the reversible preview handle
        from the persisted transaction.  No Maya scene data is edited.
        """
        scene_pair = self._describe_scene_retarget_pair(model_root)
        if scene_pair is None or self.active_preview is not None:
            return scene_pair

        transaction = self._control_rig_transactions.get(model_root)
        persisted_preview = getattr(transaction, "preview", None)
        if (
            transaction is None
            or not transaction.active
            or persisted_preview is None
            or not persisted_preview.active
        ):
            return scene_pair

        target_character = str(scene_pair["target"]["character"])
        source_character = str(scene_pair["source"]["character"])
        if str(transaction.character) != target_character:
            logger.warning(
                "HumanIK scene pair transaction character mismatch: target=%s, transaction=%s",
                target_character,
                transaction.character,
            )
            return scene_pair
        if (
            persisted_preview.target_character != target_character
            or persisted_preview.source_character != source_character
        ):
            logger.warning(
                "HumanIK persisted preview does not match the native scene pair: "
                "target=%s, source=%s",
                target_character,
                source_character,
            )
            return scene_pair

        try:
            target_binding = self._bindings.get(model_root) or self._adopt_existing_character(
                model_root
            )
            if target_binding is None or target_binding.character != target_character:
                raise RuntimeError(
                    "persisted TARGET character does not match the characterized model"
                )

            source_model_root = scene_pair["source"]["modelRoot"]
            if source_model_root is not None:
                source_binding = self._bindings.get(
                    source_model_root
                ) or self._adopt_existing_character(source_model_root)
                if source_binding is None or source_binding.character != source_character:
                    raise RuntimeError(
                        "persisted SOURCE character does not match the characterized model"
                    )
                self._source_model_root = source_model_root
                self._external_source_character = None
            else:
                self._source_model_root = None
                self._external_source_character = source_character
        except Exception as exc:
            logger.warning(
                "HumanIK scene pair frontend reconciliation failed for %s: %s",
                model_root,
                exc,
            )
            return scene_pair

        self._target_model_root = model_root
        self._preview = persisted_preview
        return scene_pair

    @property
    def active_preview(self) -> Optional[HumanIkTargetPreview]:
        """Return the active target preview, if any."""
        return self._preview if self._preview and self._preview.active else None

    def _adopt_existing_character(
        self,
        model_root: str,
    ) -> Optional[HumanIkFrontendBinding]:
        """Adopt a complete, locked MMD HumanIK character already in the scene.

        A frontend session is deliberately ephemeral: a new Maya Python
        session must be able to recover a character created by an earlier
        session without calling ``hikCreateCharacter`` or touching its lock or
        stance.  The scene-fact association is proved by
        :func:`find_humanik_character_for_model`; the assignment profile is
        then inferred from authoritative ``hikGetSkNode`` readback.

        ``None`` means that no character is associated with ``model_root`` and
        the normal characterize path should proceed.  Once a character is
        found, every validation failure raises instead of falling through to
        characterization: silently replacing an existing definition would
        trigger Maya's unlock dialog and could overwrite user edits.
        """
        character = find_humanik_character_for_model(model_root, cmds_module=self._cmds)
        if not character:
            return None

        try:
            locked = bool(
                get_humanik_definition_lock_state(character, mel_module=self._mel)
            )
        except Exception as exc:
            raise RuntimeError(
                "Could not query HumanIK definition lock state for existing character: "
                f"character={character}, model={model_root}: {exc}"
            ) from exc
        if not locked:
            raise RuntimeError(
                "Existing HumanIK character is not locked; refusing to re-characterize "
                f"model={model_root}, character={character}"
            )

        result = resolve_scene_humanik_assignments(model_root, cmds_module=self._cmds)
        body_result, excluded = _split_body_assignments(result)
        body_assignments = tuple(body_result.assignments)
        finger_assignments = tuple(excluded)
        if not body_assignments:
            raise RuntimeError(
                "Existing HumanIK character definition is incomplete: "
                f"model={model_root}, character={character}, "
                "resolved body assignments=0"
            )

        mel = self._mel or maya_mel()
        body_missing = []
        body_mismatched = []
        finger_mapped = 0
        finger_mismatched = []
        finger_errors = []
        for assignment in result.assignments:
            command = (
                f"hikGetSkNode({mel_string(character)}, {int(assignment.hik_index)});"
            )
            try:
                actual_node = mel.eval(command)
            except Exception as exc:
                if is_humanik_finger_assignment(assignment):
                    finger_errors.append(f"{assignment.hik_bone}: {exc}")
                else:
                    body_missing.append(f"{assignment.hik_bone}: {exc}")
                continue
            if is_humanik_finger_assignment(assignment):
                if actual_node is None or not str(actual_node).strip():
                    continue
                if not _maya_node_matches_assignment(actual_node, assignment.joint):
                    finger_mismatched.append(
                        f"{assignment.hik_bone}: expected={assignment.joint}, "
                        f"actual={actual_node}"
                    )
                else:
                    finger_mapped += 1
            elif actual_node is None or not str(actual_node).strip():
                body_missing.append(assignment.hik_bone)
            elif not _maya_node_matches_assignment(actual_node, assignment.joint):
                body_mismatched.append(
                    f"{assignment.hik_bone}: expected={assignment.joint}, actual={actual_node}"
                )

        if body_missing or body_mismatched:
            details = "; ".join(body_missing + body_mismatched)
            raise RuntimeError(
                "Existing HumanIK character definition is incomplete or does not match "
                f"the MMD model: model={model_root}, character={character}; {details}"
            )
        if finger_errors or finger_mismatched:
            details = "; ".join(finger_errors + finger_mismatched)
            raise RuntimeError(
                "Existing HumanIK character finger definition is incomplete or does not "
                f"match the MMD model: model={model_root}, character={character}; {details}"
            )
        if finger_mapped not in (0, len(finger_assignments)):
            raise RuntimeError(
                "Existing HumanIK character has a partial finger definition; refusing "
                f"to adopt model={model_root}, character={character}, "
                f"mapped={finger_mapped}, expected={len(finger_assignments)}"
            )

        actual_profile = (
            FULL_ASSIGNMENT_PROFILE
            if finger_assignments and finger_mapped == len(finger_assignments)
            else FRONTEND_ASSIGNMENT_PROFILE
        )
        selected_result, selected_excluded = _select_profile_result(result, actual_profile)
        binding = HumanIkFrontendBinding(
            model_root=model_root,
            character=str(character),
            result=selected_result,
            profile=actual_profile,
            excluded_finger_assignments=selected_excluded,
            # No stance transaction is created or mutated while adopting an
            # existing definition; this field intentionally remains empty.
            stance={},
        )
        self._bindings[model_root] = binding
        return binding

    def setup_and_characterize(
        self,
        model_root: str,
        *,
        profile: Optional[str] = None,
        include_fingers: Optional[bool] = None,
    ) -> HumanIkFrontendBinding:
        """Automatically pose, characterize, lock, and restore one model."""
        key = self._require_model_root(model_root)
        selected_profile, _selected_include_fingers = _normalize_assignment_profile(
            profile,
            include_fingers,
        )
        self._reject_active_preview_mutation("setup_and_characterize")
        existing = self._bindings.get(key)
        if existing is not None:
            if existing.profile != selected_profile:
                raise ValueError(
                    "HumanIK model is already characterized with a different assignment profile: "
                    f"model={key}, existing={existing.profile}, requested={selected_profile}; "
                    "restore/reload the model before changing profile"
                )
            return existing
        adopted = self._adopt_existing_character(key)
        if adopted is not None:
            return adopted
        result = resolve_scene_humanik_assignments(key, cmds_module=self._cmds)
        selected_result, excluded = _select_profile_result(result, selected_profile)
        if not selected_result.assignments:
            raise ValueError(
                f"HumanIK setup resolved no assignments for: {key} (profile={selected_profile})"
            )
        stance = self._stance_transaction_factory(
            key,
            tuple(selected_result.assignments),
            cmds_module=self._cmds,
            mel_module=self._mel,
            ownership_report=None,
            ownership_id=f"{self._ownership_id}:stance:{key}",
        )
        self._pending_stances[key] = stance
        character = None
        operation_error = None
        character_cleanup_pending = False
        try:
            stance.prepare()
            stance.enter()
            try:
                character = create_humanik_definition(
                    selected_result,
                    name_hint=self._character_name(key),
                    mel_module=self._mel,
                    update_ui=False,
                )
            except HumanIkCharacterCreationError as creation_error:
                character = creation_error.character
                if creation_error.cleanup_error is not None:
                    self._pending_characters[creation_error.character] = key
                    character_cleanup_pending = True
                operation_error = creation_error
            else:
                self._pending_characters[character] = key
                try:
                    lock_humanik_definition(character, mel_module=self._mel)
                except Exception as lock_error:
                    operation_error = lock_error
                else:
                    # Capture the post-lock character state.  Restoring the
                    # pose transaction must not silently unlock a character
                    # that setup just characterized and locked.
                    stance.attach_character(character)
        except Exception as error:
            operation_error = error
        finally:
            try:
                stance.restore()
            except Exception as stance_error:
                self._pending_stances[key] = stance
                if operation_error is not None:
                    raise RuntimeError(
                        f"HumanIK setup failed for {key}; stance restore also failed: {stance_error}"
                    ) from operation_error
                raise
            else:
                self._pending_stances.pop(key, None)
        if operation_error is not None:
            if character and character in self._pending_characters and not character_cleanup_pending:
                try:
                    delete_humanik_character(character, mel_module=self._mel)
                except Exception as cleanup_error:
                    raise RuntimeError(
                        f"HumanIK setup failed for {character}; cleanup also failed: {cleanup_error}"
                    ) from operation_error
                self._pending_characters.pop(character, None)
            raise operation_error
        self._pending_characters.pop(character, None)
        binding = HumanIkFrontendBinding(
            model_root=key,
            character=character,
            result=selected_result,
            profile=selected_profile,
            excluded_finger_assignments=excluded,
            stance={
                **stance.stance_evidence,
            },
        )
        self._bindings[key] = binding
        return binding

    def list_source_candidates(self) -> List[Dict[str, Any]]:
        """Return every scene ``HIKCharacterNode``, MMD-bound or external.

        Thin, read-only wrapper around
        ``humanik_retarget.list_scene_hik_characters`` for a future SOURCE
        picker (HUMANIK-EXTERNAL-SOURCE-1 ES-3): each row is
        ``{"character", "isMmd", "modelRoot", "locked"}``. Fails soft to an
        empty list on any Maya query error, matching every other scene-fact
        helper this session's read-only methods use.
        """
        return list_scene_hik_characters(cmds_module=self._cmds, mel_module=self._mel)

    def enter_source_mode(self, model_root: str) -> HumanIkFrontendBinding:
        """Select a characterized MMD binding as the HumanIK source character.

        Replaces any external source selection (``enter_external_source_mode``)
        -- HumanIK has exactly one active retarget input per TARGET, so the two
        SOURCE kinds are mutually exclusive on this session.
        """
        self._reject_active_preview_mutation("enter_source_mode")
        binding = self._require_binding(model_root)
        self._source_model_root = binding.model_root
        self._external_source_character = None
        return binding

    def enter_external_source_mode(self, character: str) -> Dict[str, Any]:
        """Select an existing, locked, non-MMD HIK character as SOURCE.

        Unlike ``enter_source_mode`` (which requires a binding this session
        itself created via ``setup_and_characterize``), this accepts any
        ``HIKCharacterNode`` already present and locked in the scene -- for
        example a mocap performer characterized outside mmd_tools
        (HUMANIK-EXTERNAL-SOURCE-1). The character's scene content (its
        joints, animation, characterization) is never mutated here or by
        ``restore_mmd_rig``: this session only remembers its name so
        ``enter_target_mode``/``begin_humanik_target_preview`` can pass it to
        ``hikSetCharacterInput`` as the retarget input, exactly as it would an
        MMD source character.

        Replaces any MMD source selection (``enter_source_mode``) -- the two
        SOURCE kinds are mutually exclusive on this session.

        Args:
            character: The scene's ``HIKCharacterNode`` name to use as SOURCE.

        Raises:
            RuntimeError: While a HumanIK target preview is active; when
                ``character`` is not a ``HIKCharacterNode`` present in the
                scene; or when its HumanIK definition is not locked.
            ValueError: When ``character`` is empty.
        """
        self._reject_active_preview_mutation("enter_external_source_mode")
        character = str(character or "").strip()
        if not character:
            raise ValueError("character is required")
        cmds = self._cmds or maya_cmds()
        try:
            scene_characters = {
                str(item) for item in (cmds.ls(type=HIK_CHARACTER_NODE_TYPE) or [])
            }
        except Exception as exc:
            raise RuntimeError(
                f"Could not query scene HumanIK characters: {exc}"
            ) from exc
        if character not in scene_characters:
            raise RuntimeError(
                f"HumanIK external source character not found in scene: {character}"
            )
        try:
            locked = bool(
                get_humanik_definition_lock_state(character, mel_module=self._mel)
            )
        except Exception as exc:
            raise RuntimeError(
                f"Could not query HumanIK lock state for external source: {character}: {exc}"
            ) from exc
        if not locked:
            raise RuntimeError(
                "HumanIK external source character must be characterized and "
                f"locked before use as SOURCE: {character}"
            )
        self._source_model_root = None
        self._external_source_character = character
        return {"character": character, "external": True, "locked": True}

    def disconnect_retarget(self) -> bool:
        """Clear the selected SOURCE without destroying a baked Control Rig.

        Before a Control Rig bake, disconnecting an active SOURCE/TARGET
        preview restores the TARGET's pre-preview input and writer ownership.
        After :meth:`bake_to_control_rig`, Maya has already switched the
        TARGET input to the Control Rig.  In that state the preview object is
        retained only as the reversible bake context required by
        :meth:`bake_from_control_rig`; deleting the Control Rig here would
        discard the animation the user just baked.  The same decision is used
        after scene reopen because ``HumanIkControlRigTransaction.baked`` is
        persisted with the transaction.

        Returns:
            ``True`` when a preview or SOURCE selection was cleared, otherwise
            ``False``.
        """
        preview = self._preview
        target_root = self._target_model_root
        transaction = (
            self._control_rig_transactions.get(target_root)
            if target_root is not None
            else None
        )
        keep_baked_control_rig = bool(
            preview is not None
            and preview.active
            and transaction is not None
            and transaction.active
            and (
                bool(getattr(transaction, "baked", False))
                or target_root in self._control_rig_baked_roots
            )
        )

        disconnected = False
        if preview is not None and preview.active and not keep_baked_control_rig:
            # Fail closed: if restoring TARGET ownership fails, keep the
            # SOURCE selection and preview context so Restore can retry.
            stop_humanik_target_preview(
                preview,
                cmds_module=self._cmds,
                mel_module=self._mel,
            )
            disconnected = True
        if preview is not None and not preview.active:
            if transaction is not None:
                transaction.preview = None
            self._preview = None
            self._target_model_root = None
            self._ownership_report = None
            self._persist_control_rig_transactions()

        if self._source_model_root is not None or self._external_source_character is not None:
            self._source_model_root = None
            self._external_source_character = None
            disconnected = True
        return disconnected

    def enter_target_mode(self, model_root: str) -> HumanIkTargetPreview:
        """Start a target preview after ownership classification and blocker checks.

        SOURCE may be either an MMD binding (``enter_source_mode``) or an
        external HIK character already characterized and locked in the scene
        (``enter_external_source_mode``, HUMANIK-EXTERNAL-SOURCE-1). The
        source/target assignment-profile check only applies to an MMD
        source -- an external source has no MMD assignment profile to
        compare.
        """
        key = self._require_model_root(model_root)
        has_source = self._source_model_root is not None or self._external_source_character is not None
        if not has_source:
            raise RuntimeError("HumanIK source mode must be entered before target mode")
        if self._source_model_root is not None and key == self._source_model_root:
            raise ValueError("HumanIK source and target model roots must differ")
        if self._preview is not None and self._preview.active:
            if self._target_model_root == key:
                return self._preview
            raise RuntimeError("A HumanIK target preview is already active")
        target = self._require_binding(key)
        if self._external_source_character is not None:
            if target.character == self._external_source_character:
                raise ValueError("HumanIK source and target characters must differ")
            source_character = self._external_source_character
        else:
            source = self._bindings[self._source_model_root]
            if source.profile != target.profile:
                raise ValueError(
                    "HumanIK source/target assignment profile mismatch: "
                    f"source={source.profile}, target={target.profile}; "
                    "Restore both models and reconnect them so they both characterize "
                    f"with the same profile (default: {FULL_ASSIGNMENT_PROFILE}) before target mode"
                )
            source_character = source.character
        target_joints = tuple(assignment.joint for assignment in target.assignments)
        report = collect_hik_ownership_report(target_joints, cmds_module=self._cmds)
        transaction = self._control_rig_transactions.get(key)
        preisolated_feedback_nodes = _preisolated_feedback_nodes(
            transaction,
            target.assignments,
            self._cmds,
        )
        self._target_model_root = key
        self._ownership_report = report
        blockers = [
            row for row in report.get("rows", [])
            if row.get("classification") in BLOCKING_CLASSIFICATIONS
            and not is_supported_mmd_ccdik_feedback_row(row, target.assignments)
            and not is_preisolated_mmd_ccdik_feedback_row(
                row, preisolated_feedback_nodes
            )
        ]
        if blockers:
            labels = ", ".join(f"{row['node']}:{row['classification']}" for row in blockers)
            raise RuntimeError(f"HumanIK target preview blocked: {labels}")
        preview = begin_humanik_target_preview(
            self._ownership_id,
            target.character,
            source_character,
            report,
            target_joints,
            cmds_module=self._cmds,
            mel_module=self._mel,
            assignments=target.assignments,
            preisolated_feedback_nodes=preisolated_feedback_nodes,
        )
        self._target_model_root = key
        self._preview = preview
        if transaction is not None and transaction.active:
            transaction.preview = preview
            self._persist_control_rig_transactions()
        return preview

    def create_control_rig(self, model_root: str) -> bool:
        """Create a control rig on an already characterized binding.

        This wraps ``hikCreateControlRig()`` in the same transaction shape
        ``enter_target_mode`` uses for TARGET preview -- restore_state, isolate MMD
        writers that would otherwise feed a HIK-assigned joint, pre-cycle
        gate, create, re-scan/re-isolate, post-cycle gate (see
        ``humanik_control_rig.begin_humanik_control_rig``) -- so Control Rig
        creation cannot introduce the ``HIKState2SK -> pairBlend -> joint``
        DG cycle reported for an un-isolated ``hikCreateControlRig()`` call
        (``HUMANIK-CONTROL-RIG-CYCLE-1``).

        Fail-closed before any scene mutation:

        * while a HumanIK TARGET preview is active for any model (mutation
          guard shared with every other session operation);
        * while ``model_root`` is the session's active SOURCE (Control Rig
          creation would otherwise mutate the character another preview is
          reading motion from);
        * while ``model_root`` already has an active transactional control
          rig (idempotent no-op instead of a duplicate ``hikCreateControlRig``
          call);
        * when scene-fact ownership classification finds a blocker
          (``physics_blocker``/``feedback_blocker``/``manual``) for this
          model's HIK joints.
        """
        self._reject_active_preview_mutation("create_control_rig")
        key = self._require_model_root(model_root)
        binding = self._require_binding(key)
        if key == self._source_model_root:
            raise RuntimeError(
                f"Cannot create_control_rig while model is the active HumanIK SOURCE: {key}"
            )
        existing = self._control_rig_transactions.get(key)
        if existing is not None and existing.active:
            return True
        joints = tuple(assignment.joint for assignment in binding.assignments)
        transaction = begin_humanik_control_rig(
            f"{self._ownership_id}:control-rig:{key}",
            binding.character,
            joints,
            cmds_module=self._cmds,
            mel_module=self._mel,
            assignments=binding.assignments,
        )
        try:
            cmds = self._cmds or maya_cmds()
        except Exception:
            cmds = None
        if cmds is not None:
            _record_disconnected_source_uuids(transaction, cmds)
        self._control_rig_transactions[key] = transaction
        binding.control_rig_created = True
        self._persist_control_rig_transactions()
        return True

    def bake_to_mmd_rig(self, start: int, end: int) -> HumanIkBakeResult:
        """Bake the active target preview into the target MMD rig."""
        preview = self.active_preview
        if preview is None or self._target_model_root is None:
            raise RuntimeError("HumanIK target preview is not active")
        target_root = self._target_model_root
        target = self._bindings[target_root]
        transaction = self._control_rig_transactions.get(target_root)
        try:
            result = bake_humanik_target_preview(
                preview,
                (assignment.joint for assignment in target.assignments),
                int(start),
                int(end),
                cmds_module=self._cmds,
                mel_module=self._mel,
            )
        except Exception:
            if not preview.active:
                if transaction is not None:
                    transaction.preview = None
                self._preview = None
                self._target_model_root = None
                self._ownership_report = None
                self._persist_control_rig_transactions()
            raise
        if transaction is not None:
            transaction.preview = None
        self._preview = None
        self._target_model_root = None
        self._ownership_report = None
        self._persist_control_rig_transactions()
        return result

    def bake_to_control_rig(self, start: int, end: int) -> HumanIkControlRigBakeResult:
        """Bake the active SOURCE/VMD retarget onto the target Control Rig.

        The target preview and its Control Rig transaction are both required.
        The native HumanIK bake switches the character input to the Control
        Rig; this method therefore leaves ``_preview`` and the transaction in
        place so the Control Rig remains active/editable until
        :meth:`restore_mmd_rig` is explicitly requested.  Successful calls
        persist the transaction's ``baked`` flag so a reopened scene keeps the
        same non-destructive Keep path available.
        """
        preview = self.active_preview
        if preview is None or self._target_model_root is None:
            raise RuntimeError("HumanIK target preview is not active")
        key = self._target_model_root
        transaction = self._control_rig_transactions.get(key)
        if transaction is None or not transaction.active:
            raise RuntimeError("HumanIK target Control Rig transaction is not active")
        binding = self._bindings.get(key)
        if binding is None:
            raise RuntimeError(f"HumanIK target model binding is missing: {key}")
        if str(transaction.character) != str(binding.character):
            raise RuntimeError(
                "HumanIK target Control Rig character does not match the target binding: "
                f"transaction={transaction.character}, binding={binding.character}"
            )
        result = bake_humanik_control_rig(
            transaction,
            int(start),
            int(end),
            cmds_module=self._cmds,
            mel_module=self._mel,
        )
        transaction.baked = True
        self._control_rig_baked_roots.add(key)
        self._persist_control_rig_transactions()
        return result

    def bake_from_control_rig(self, start: int, end: int) -> HumanIkBakeResult:
        """Bake edited Control Rig output back to the target MMD rig.

        This is the terminal half of the Control Rig round trip.  It reuses
        ``bake_to_mmd_rig``'s fail-safe sampling/route splice/rollback path,
        then releases the target Control Rig transaction only after authoring
        succeeds.  A failed authoring attempt leaves the transaction available
        for the normal Restore MMD Rig recovery path.
        """
        preview = self.active_preview
        if preview is None or self._target_model_root is None:
            raise RuntimeError("HumanIK target preview is not active")
        key = self._target_model_root
        transaction = self._control_rig_transactions.get(key)
        if transaction is None or not transaction.active:
            raise RuntimeError("HumanIK target Control Rig transaction is not active")
        binding = self._bindings.get(key)
        if binding is None:
            raise RuntimeError(f"HumanIK target model binding is missing: {key}")
        if str(transaction.character) != str(binding.character):
            raise RuntimeError(
                "HumanIK target Control Rig character does not match the target binding: "
                f"transaction={transaction.character}, binding={binding.character}"
            )

        result = self.bake_to_mmd_rig(start, end)
        teardown_error = None
        try:
            stop_humanik_control_rig(
                transaction,
                cmds_module=self._cmds,
                mel_module=self._mel,
            )
        except Exception as exc:  # fail-closed teardown retains the transaction for retry
            teardown_error = exc
            # ``stop_humanik_control_rig`` is fail-closed: a deletion or
            # restore_state failure leaves the transaction active for retry.
            # Keep every ownership marker and persist that retryable state.
            self._persist_control_rig_transactions()
        else:
            self._control_rig_transactions.pop(key, None)
            self._control_rig_baked_roots.discard(key)
            binding.control_rig_created = False
            self._persist_control_rig_transactions()
        if teardown_error is not None:
            raise teardown_error
        return result

    def _has_active_baked_control_rig(self) -> bool:
        """Return whether a tracked active Control Rig already has a bake."""
        return any(
            transaction.active
            and (
                bool(getattr(transaction, "baked", False))
                or model_root in self._control_rig_baked_roots
            )
            for model_root, transaction in self._control_rig_transactions.items()
        )

    def restore_mmd_rig(self, delete_baked_control_rig: bool = False) -> bool:
        """Restore tracked HumanIK state and recover eligible orphaned rigs.

        Unbaked transactions are deleted through HumanIK and retried on a
        later call if teardown fails. Orphaned Control Rigs are removed only
        when their character resolves to an MMD model; recovery is fail-soft
        and reports state that cannot be reconstructed without restore data.

        Baked rigs use the non-destructive disconnect path by default so Bake
        From remains available. Pass ``delete_baked_control_rig=True`` to
        request destructive teardown.
        """
        if not delete_baked_control_rig and self._has_active_baked_control_rig():
            return self.disconnect_retarget()
        preview = self._preview
        restored = False
        first_error = None
        for model_root, transaction in list(self._control_rig_transactions.items()):
            if not transaction.active:
                self._control_rig_transactions.pop(model_root, None)
                continue
            try:
                stop_humanik_control_rig(transaction, cmds_module=self._cmds, mel_module=self._mel)
            except Exception as exc:
                if first_error is None:
                    first_error = exc
                if not transaction.active:
                    self._control_rig_transactions.pop(model_root, None)
                continue
            self._control_rig_transactions.pop(model_root, None)
            self._control_rig_baked_roots.discard(model_root)
            binding = self._bindings.get(model_root)
            if binding is not None:
                binding.control_rig_created = False
            restored = True
        # Delete Control Rig transactions before restoring preview/MMD writer
        # state.  In particular, a native-baked rig may still be connected to
        # the HIK character; restoring preview writers first would leave those
        # writers connected while the Control Rig remains live.
        active_control_rig_remaining = any(
            transaction.active for transaction in self._control_rig_transactions.values()
        )
        if active_control_rig_remaining:
            if first_error is None:
                first_error = RuntimeError(
                    "Cannot restore preview writers while a HumanIK Control Rig remains active"
                )
            # Do not advance any other destructive recovery while the live
            # Control Rig foundation is still present.  In particular,
            # restoring a pending stance, deleting a pending character, or
            # recovering orphan nodes could invalidate the rig and make the
            # retained transaction impossible to retry safely.
            self._persist_control_rig_transactions()
            self._control_rig_baked_roots.intersection_update(
                self._control_rig_transactions
            )
            raise first_error
        else:
            try:
                if preview is not None and preview.active:
                    stop_humanik_target_preview(preview, cmds_module=self._cmds, mel_module=self._mel)
                    restored = True
            except Exception as exc:
                if first_error is None:
                    first_error = exc
            finally:
                if preview is not None and not preview.active:
                    self._preview = None
                    self._target_model_root = None
                    self._ownership_report = None
        failed_stance_roots = set()
        for model_root, stance in list(self._pending_stances.items()):
            try:
                stance.restore()
            except Exception as exc:
                failed_stance_roots.add(model_root)
                if first_error is None:
                    first_error = exc
            else:
                self._pending_stances.pop(model_root, None)
                restored = True
        for character in list(self._pending_characters):
            model_root = self._pending_characters[character]
            if model_root in failed_stance_roots:
                continue
            try:
                delete_humanik_character(character, mel_module=self._mel)
            except Exception as exc:
                if first_error is None:
                    first_error = exc
            else:
                self._pending_characters.pop(character, None)
                restored = True
        orphan_recovery = self._recover_orphaned_control_rigs()
        self._last_orphan_recovery = orphan_recovery
        if orphan_recovery["recovered"]:
            restored = True
        if self._external_source_character is not None:
            # HUMANIK-EXTERNAL-SOURCE-1 ES-1: clear this session's SOURCE
            # selection. The external character itself (its joints,
            # animation, characterization) is untouched -- it is not owned
            # by this session, unlike an MMD binding's pending
            # character/stance handled above.
            self._external_source_character = None
            restored = True
        self._persist_control_rig_transactions()
        self._control_rig_baked_roots.intersection_update(
            self._control_rig_transactions
        )
        if first_error is not None:
            raise first_error
        return restored

    def _recover_orphaned_control_rigs(self) -> Dict[str, List[Dict[str, Any]]]:
        """Best-effort scene-facts recovery for untracked Control Rigs.

        Reuses ``_describe_orphaned_control_rigs``'s cheap
        ``HIKControlSetNode`` scan (no ownership classification) to find
        every Control Rig this session has no active transaction for, then
        for each one:

        * skips it with ``skippedReason="unknown_character"`` when the node
          has no connected ``HIKCharacterNode`` (nothing to act on);
        * skips it with ``skippedReason="not_mmd_driven"`` when
          ``_find_mmd_model_root_for_character`` cannot resolve the
          character back to a real MMD model root -- this is the hard
          safety gate: a Control Rig for an unrelated HIK character is
          never deleted, matching the "MMD-driven only" constraint in
          ``TODO.md`` (auto-adopt of arbitrary Control Rigs was explicitly
          rejected);
        * otherwise attempts ``delete_orphaned_control_rig`` and records the
          outcome as ``recovered`` (with
          ``ORPHAN_RECOVERY_UNRECOVERABLE_WARNINGS`` attached -- there is no
          restore_state, so writer/stance restore did not happen) or ``failed``
          (the MEL call raised -- for example no HumanIK UI in a batch/mayapy
          process) without ever raising out of this method.

        Returns:
            ``{"recovered": [...], "skipped": [...], "failed": [...]}``,
            each a list of JSON-safe dicts.
        """
        report: Dict[str, List[Dict[str, Any]]] = {"recovered": [], "skipped": [], "failed": []}
        orphans = self._describe_orphaned_control_rigs()
        if not orphans:
            return report
        cmds = self._cmds or maya_cmds()
        try:
            mel = self._mel or maya_mel()
        except Exception as exc:
            for row in orphans:
                report["skipped"].append(
                    {**row, "skippedReason": "mel_unavailable", "detail": str(exc)}
                )
            return report
        for row in orphans:
            node = row["controlSetNode"]
            character = row.get("character")
            entry: Dict[str, Any] = {"controlSetNode": node, "character": character}
            if not character:
                entry["skippedReason"] = "unknown_character"
                report["skipped"].append(entry)
                continue
            model_root = _find_mmd_model_root_for_character(character, cmds)
            if model_root is None:
                entry["skippedReason"] = "not_mmd_driven"
                report["skipped"].append(entry)
                continue
            entry["modelRoot"] = model_root
            try:
                delete_orphaned_control_rig(character, cmds_module=cmds, mel_module=mel)
            except Exception as exc:
                entry["error"] = str(exc)
                report["failed"].append(entry)
                logger.warning(
                    "HumanIK orphaned Control Rig recovery failed: character=%s "
                    "modelRoot=%s controlSetNode=%s: %s",
                    character, model_root, node, exc,
                )
                continue
            entry["unrecoverableWarnings"] = list(ORPHAN_RECOVERY_UNRECOVERABLE_WARNINGS)
            report["recovered"].append(entry)
            logger.warning(
                "HumanIK recovered an orphaned Control Rig outside this session's "
                "tracked transactions (character=%s modelRoot=%s controlSetNode=%s); "
                "no restore_state survived for it, so muted MMD writer edges and the "
                "pre-characterize stance were not restored -- see "
                "ORPHAN_RECOVERY_UNRECOVERABLE_WARNINGS.",
                character, model_root, node,
            )
        return report

    def describe_last_orphan_recovery(self) -> Dict[str, List[Dict[str, Any]]]:
        """Return the outcome of the most recent ``restore_mmd_rig`` orphan pass.

        JSON-safe copy of the report ``_recover_orphaned_control_rigs``
        produced on the last ``restore_mmd_rig`` call (empty lists before the
        first call). ``describe_frontend_state()`` surfaces the same data as
        ``restoreHint.lastOrphanRecovery`` for UI consumption.
        """
        return {
            "recovered": [dict(item) for item in self._last_orphan_recovery.get("recovered", [])],
            "skipped": [dict(item) for item in self._last_orphan_recovery.get("skipped", [])],
            "failed": [dict(item) for item in self._last_orphan_recovery.get("failed", [])],
        }

    def inspect_model(
        self,
        model_root: str,
        *,
        profile: Optional[str] = None,
        include_fingers: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Resolve a model into the requested body-only or full assignment profile.

        The returned report is JSON-safe and does not create, lock, or mutate a
        HumanIK character.  UI callers can display it before requesting the
        automatic snapshot/canonical-pose/characterize/restore transaction.
        """
        key = self._require_model_root(model_root)
        existing = self._bindings.get(key)
        if profile is None and include_fingers is None and existing is not None:
            profile = existing.profile
        profile, include_fingers = _normalize_assignment_profile(profile, include_fingers)
        result = resolve_scene_humanik_assignments(key, cmds_module=self._cmds)
        body_result, _all_excluded = _split_body_assignments(result)
        selected_result, excluded = _select_profile_result(result, profile)
        assignments = [_assignment_row(item) for item in selected_result.assignments]
        body_assignments = [_assignment_row(item) for item in body_result.assignments]
        excluded_rows = [_assignment_row(item) for item in excluded]
        unresolved = {
            "missingMmdBones": list(selected_result.missing_mmd_bones),
            "unindexedMmdBones": list(selected_result.unindexed_mmd_bones),
        }
        ambiguous = [_assignment_row(item) for item in selected_result.duplicate_assignments]
        return {
            "modelRoot": key,
            "profile": profile,
            "includeFingers": include_fingers,
            "assignments": assignments,
            "bodyAssignments": body_assignments,
            "assignmentCount": len(assignments),
            "excludedFingerAssignments": excluded_rows,
            "excludedFingerCount": len(excluded_rows),
            "unresolved": unresolved,
            "missingMmdBones": list(unresolved["missingMmdBones"]),
            "unindexedMmdBones": list(unresolved["unindexedMmdBones"]),
            "ambiguous": ambiguous,
            "duplicateAssignments": ambiguous,
            "blocked": [] if assignments else ["no_resolved_assignments"],
            "automaticStance": canonical_stance_targets(selected_result.assignments),
        }

    def inspect_target_ownership(
        self,
        model_root: str,
        *,
        profile: Optional[str] = None,
        include_fingers: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Classify target writers without starting a HumanIK preview."""
        key = self._require_model_root(model_root)
        existing = self._bindings.get(key)
        if profile is None and include_fingers is None and existing is not None:
            profile = existing.profile
        profile, include_fingers = _normalize_assignment_profile(profile, include_fingers)
        model_report = self.inspect_model(
            key,
            profile=profile,
            include_fingers=include_fingers,
        )
        target_joints = tuple(row["joint"] for row in model_report["assignments"])
        ownership = collect_hik_ownership_report(target_joints, cmds_module=self._cmds)
        transaction = self._control_rig_transactions.get(key)
        preisolated_feedback_nodes = _preisolated_feedback_nodes(
            transaction,
            model_report["assignments"],
            self._cmds,
        )
        blockers = [
            row for row in ownership.get("rows", [])
            if row.get("classification") in BLOCKING_CLASSIFICATIONS
            and not is_supported_mmd_ccdik_feedback_row(row, model_report["assignments"])
            and not is_preisolated_mmd_ccdik_feedback_row(
                row, preisolated_feedback_nodes
            )
        ]
        automatic_stance = dict(model_report.get("automaticStance", {}))
        automatic_stance["ownership"] = {
            "disconnect": [
                {
                    "node": str(row.get("node", "")),
                    "edges": list(row.get("writes", [])),
                }
                for row in ownership.get("rows", [])
                if row.get("classification") == "mute_for_hik"
            ],
            "retain": [
                {
                    "node": str(row.get("node", "")),
                    "edges": list(row.get("writes", [])),
                }
                for row in ownership.get("rows", [])
                if row.get("classification") == "keep_post"
            ],
            "blockers": [
                {
                    "node": str(row.get("node", "")),
                    "classification": str(row.get("classification", "")),
                }
                for row in blockers
            ],
        }
        return {
            **model_report,
            "ownership": ownership,
            "constraintCounts": dict(ownership.get("counts", {})),
            "constraintRows": list(ownership.get("rows", [])),
            "automaticStance": automatic_stance,
            "blockers": [
                {
                    "node": str(row.get("node", "")),
                    "classification": str(row.get("classification", "")),
                }
                for row in blockers
            ],
        }

    def diagnostics(self, model_root: Optional[str] = None) -> Dict[str, Any]:
        """Return JSON-safe lifecycle, assignment, ownership, and quality diagnostics."""
        selected = self._bindings.get(str(model_root)) if model_root else None
        source = self._bindings.get(self._source_model_root) if self._source_model_root else None
        target = self._bindings.get(self._target_model_root) if self._target_model_root else None
        ownership_rows = (self._ownership_report or {}).get("rows", [])
        transaction = (
            self._control_rig_transactions.get(self._target_model_root)
            if self._target_model_root is not None
            else None
        )
        preisolated_feedback_nodes = _preisolated_feedback_nodes(
            transaction,
            target.assignments if target is not None else (),
            self._cmds,
        )
        blockers = [
            row for row in ownership_rows
            if row.get("classification") in BLOCKING_CLASSIFICATIONS
            and not is_supported_mmd_ccdik_feedback_row(
                row,
                target.assignments if target is not None else (),
            )
            and not is_preisolated_mmd_ccdik_feedback_row(
                row, preisolated_feedback_nodes
            )
        ]
        ownership_counts = (self._ownership_report or {}).get("counts", {})
        selected_summary = selected.to_dict() if selected else None
        active_profile = (
            selected.profile
            if selected
            else source.profile
            if source
            else target.profile
            if target
            else FRONTEND_ASSIGNMENT_PROFILE
        )
        selected_body_count = (
            sum(not is_humanik_finger_assignment(item) for item in selected.assignments)
            if selected
            else 0
        )
        quality = dict(REFERENCE_QUALITY_DIAGNOSTICS)
        quality["assignmentProfile"] = active_profile
        if active_profile == FULL_ASSIGNMENT_PROFILE:
            quality["fingerStatus"] = "included-experimental"
        return {
            "modelRoot": str(model_root) if model_root else None,
            "character": selected.character if selected else None,
            "profile": active_profile,
            "source": {
                "modelRoot": source.model_root if source else None,
                "character": source.character if source else self._external_source_character,
                "profile": source.profile if source else None,
                "includeFingers": bool(source and source.profile == FULL_ASSIGNMENT_PROFILE),
                "assignmentCount": len(source.assignments) if source else 0,
                "excludedFingerCount": len(source.excluded_finger_assignments) if source else 0,
                "external": source is None and self._external_source_character is not None,
            },
            "target": {
                "modelRoot": target.model_root if target else None,
                "character": target.character if target else None,
                "profile": target.profile if target else None,
                "includeFingers": bool(target and target.profile == FULL_ASSIGNMENT_PROFILE),
                "assignmentCount": len(target.assignments) if target else 0,
                "excludedFingerCount": len(target.excluded_finger_assignments) if target else 0,
            },
            "assignments": selected_summary or {
                "profile": active_profile,
                "includeFingers": active_profile == FULL_ASSIGNMENT_PROFILE,
                "assignmentCount": 0,
                "required": {
                    "genericLockMinimumAssignmentCount": 1,
                    "expectedAssignmentCount": (
                        EXPECTED_FULL_ASSIGNMENT_COUNT
                        if active_profile == FULL_ASSIGNMENT_PROFILE
                        else EXPECTED_BODY_ASSIGNMENT_COUNT
                    ),
                    "resolvedAssignmentCount": 0,
                },
                "excludedFingerCount": 0,
                "unresolved": {},
                "ambiguous": [],
                "blocked": [],
            },
            "ownership": {
                "muteForHik": int(ownership_counts.get("mute_for_hik", 0)),
                "keepPost": int(ownership_counts.get("keep_post", 0)),
                "blockers": [
                    {
                        "node": str(row.get("node", "")),
                        "classification": str(row.get("classification", "")),
                    }
                    for row in blockers
                ],
            },
            "preview": {
                "active": bool(self.active_preview),
                "restoreStateAvailable": bool(self.active_preview and self.active_preview.restore_state),
            },
            "pendingRecovery": {
                "characterCount": len(self._pending_characters),
                "characters": sorted(self._pending_characters),
                "stanceCount": len(self._pending_stances),
                "stanceModelRoots": sorted(self._pending_stances),
                "stances": [
                    self._pending_stances[root].to_dict()
                    for root in sorted(self._pending_stances)
                ],
            },
            "profileCoverage": {
                "profile": active_profile,
                "expectedBodyAssignmentCount": EXPECTED_BODY_ASSIGNMENT_COUNT,
                "expectedAssignmentCount": (
                    EXPECTED_FULL_ASSIGNMENT_COUNT
                    if active_profile == FULL_ASSIGNMENT_PROFILE
                    else EXPECTED_BODY_ASSIGNMENT_COUNT
                ),
                "expectedFingerExcludedCount": (
                    0
                    if active_profile == FULL_ASSIGNMENT_PROFILE
                    else EXPECTED_FINGER_ASSIGNMENT_COUNT
                ),
                "actualBodyAssignmentCount": selected_body_count,
                "actualFingerExcludedCount": len(selected.excluded_finger_assignments) if selected else 0,
            },
            "quality": quality,
        }

    def describe_frontend_state(self, model_root: Optional[str] = None) -> Dict[str, Any]:
        """Return a read-only, JSON-safe snapshot of the frontend lifecycle.

        ``actions`` cheaply mirrors common fail-closed guards for UI state;
        mutating methods remain authoritative and may reject conditions that
        require a scene scan or arguments absent here. Ownership classification
        is intentionally excluded from refresh-time work.

        Args:
            model_root: Optional model root the caller is about to act on.
                When ``None``, every action whose guard depends on a specific
                model reports ``allowed=False`` with ``REASON_MODEL_REQUIRED``
                instead of guessing a model.

        ``restoreHint`` includes untracked Control Rigs and the most recent
        fail-soft recovery outcome, including unrecoverable state warnings.

        Returns:
            A dict with keys ``mode``, ``source``, ``target``,
            ``previewActive``, ``controlRigs``, ``restoreHint``, ``actions``,
            and (only when ``model_root`` is given) ``importLock``.
        """
        key = self._optional_model_root(model_root)
        scene_pair = (
            self._reconcile_scene_retarget_context(key) if key is not None else None
        )
        preview_active = bool(self.active_preview)
        source_binding = (
            self._bindings.get(self._source_model_root) if self._source_model_root else None
        )
        target_binding = (
            self._bindings.get(self._target_model_root)
            if preview_active and self._target_model_root
            else None
        )
        control_rig_rows = []
        for control_root, transaction in self._control_rig_transactions.items():
            if not transaction.active:
                continue
            binding = self._bindings.get(control_root)
            control_rig_rows.append(
                {
                    "modelRoot": control_root,
                    "character": (
                        binding.character
                        if binding
                        else str(getattr(transaction, "character", "")) or None
                    ),
                    "baked": bool(getattr(transaction, "baked", False)),
                }
            )

        external_source = self._external_source_character
        if scene_pair is None and key is not None and source_binding is None and external_source is None:
            scene_pair = self._describe_scene_retarget_pair(key)
        baked_control_rig_detached = bool(
            preview_active
            and source_binding is None
            and external_source is None
            and (
                self._target_model_root in self._control_rig_baked_roots
                or bool(
                    getattr(
                        self._control_rig_transactions.get(self._target_model_root),
                        "baked",
                        False,
                    )
                )
            )
        )

        if preview_active and not baked_control_rig_detached:
            mode = FRONTEND_MODE_TARGET_PREVIEW
        elif control_rig_rows:
            mode = FRONTEND_MODE_CONTROL_RIG
        elif scene_pair is not None:
            mode = FRONTEND_MODE_TARGET_PREVIEW
        elif source_binding is not None or external_source is not None:
            mode = FRONTEND_MODE_SOURCE
        else:
            mode = FRONTEND_MODE_NEUTRAL

        nothing_to_restore = not (
            preview_active
            or control_rig_rows
            or self._pending_stances
            or self._pending_characters
            or external_source is not None
        )

        actions = {
            "setup_and_characterize": self._describe_setup_and_characterize_action(
                key, preview_active
            ),
            "enter_source_mode": self._describe_enter_source_mode_action(key, preview_active),
            "enter_external_source_mode": self._describe_enter_external_source_mode_action(
                preview_active
            ),
            "enter_target_mode": self._describe_enter_target_mode_action(key, preview_active),
            "create_control_rig": self._describe_create_control_rig_action(key, preview_active),
            "bake_to_mmd_rig": self._describe_bake_to_mmd_rig_action(),
            "bake_to_control_rig": self._describe_bake_to_control_rig_action(),
            "bake_from_control_rig": self._describe_bake_from_control_rig_action(),
            "restore_mmd_rig": self._describe_restore_mmd_rig_action(nothing_to_restore),
            "diagnostics": {"allowed": True, "reasonCode": None, "reasonText": None},
        }

        if source_binding is not None:
            source_state: Optional[Dict[str, Any]] = {
                "modelRoot": source_binding.model_root,
                "character": source_binding.character,
                "external": False,
            }
        elif external_source is not None:
            source_state = {
                "modelRoot": None,
                "character": external_source,
                "external": True,
            }
        else:
            source_state = scene_pair["source"] if scene_pair is not None else None

        state: Dict[str, Any] = {
            "mode": mode,
            "source": source_state,
            "target": (
                {"modelRoot": target_binding.model_root, "character": target_binding.character}
                if target_binding is not None
                else scene_pair["target"]
                if scene_pair is not None
                else None
            ),
            "previewActive": preview_active,
            "sceneRetargetActive": scene_pair is not None,
            "controlRigs": control_rig_rows,
            "restoreHint": {
                "hasPreview": preview_active,
                "controlRigCount": len(control_rig_rows),
                "pendingStanceCount": len(self._pending_stances),
                "pendingCharacterCount": len(self._pending_characters),
                "orphanedControlRigs": self._describe_orphaned_control_rigs(),
                "lastOrphanRecovery": self.describe_last_orphan_recovery(),
            },
            "actions": actions,
        }

        # ``importLock`` needs a specific model to inspect; without one there
        # is nothing scene-fact-based to report, so it is omitted rather than
        # guessing SOURCE/TARGET (design decision -- callers that need the
        # import-lock state for "the current model" must pass it explicitly).
        if key is not None:
            state["importLock"] = self._describe_import_lock(key)
        return state

    def _describe_setup_and_characterize_action(
        self, key: Optional[str], preview_active: bool
    ) -> Dict[str, Any]:
        """Mirror ``setup_and_characterize``'s guards, in the order it checks them."""
        if preview_active:
            return _action_blocked(
                REASON_PREVIEW_ACTIVE,
                "Cannot setup_and_characterize while a HumanIK target preview is active",
            )
        if key is None:
            return _action_blocked(REASON_MODEL_REQUIRED, "Select a model to characterize")
        return _action_allowed()

    def _describe_enter_source_mode_action(
        self, key: Optional[str], preview_active: bool
    ) -> Dict[str, Any]:
        """Mirror ``enter_source_mode``'s guards, in the order it checks them."""
        if preview_active:
            return _action_blocked(
                REASON_PREVIEW_ACTIVE,
                "Cannot enter_source_mode while a HumanIK target preview is active",
            )
        if key is None:
            return _action_blocked(REASON_MODEL_REQUIRED, "Select a model to enter SOURCE mode")
        if key not in self._bindings:
            return _action_blocked(
                REASON_NOT_CHARACTERIZED, f"HumanIK model is not characterized: {key}"
            )
        return _action_allowed()

    def _describe_enter_external_source_mode_action(self, preview_active: bool) -> Dict[str, Any]:
        """Mirror ``enter_external_source_mode``'s guards.

        Unlike ``enter_source_mode``/``enter_target_mode`` this action takes a
        scene character name, not a ``model_root`` -- this read-only snapshot
        has no character argument to check against the scene, so only the
        preview-active guard (identical for every mutating method) is
        mirrored here; the "character exists and is locked" checks require a
        live Maya scene scan and are exercised by the real call itself.
        """
        if preview_active:
            return _action_blocked(
                REASON_PREVIEW_ACTIVE,
                "Cannot enter_external_source_mode while a HumanIK target preview is active",
            )
        return _action_allowed()

    def _describe_enter_target_mode_action(
        self, key: Optional[str], preview_active: bool
    ) -> Dict[str, Any]:
        """Mirror ``enter_target_mode``'s guards, in the order it checks them."""
        if key is None:
            return _action_blocked(REASON_MODEL_REQUIRED, "Select a model to enter TARGET mode")
        has_source = self._source_model_root is not None or self._external_source_character is not None
        if not has_source:
            return _action_blocked(
                REASON_NO_SOURCE, "HumanIK source mode must be entered before target mode"
            )
        if self._source_model_root is not None and key == self._source_model_root:
            return _action_blocked(
                REASON_TARGET_IS_SOURCE, "HumanIK source and target model roots must differ"
            )
        if preview_active and self._target_model_root != key:
            return _action_blocked(
                REASON_PREVIEW_ACTIVE, "A HumanIK target preview is already active"
            )
        if key not in self._bindings:
            return _action_blocked(
                REASON_NOT_CHARACTERIZED, f"HumanIK model is not characterized: {key}"
            )
        target_binding = self._bindings[key]
        if self._external_source_character is not None:
            if target_binding.character == self._external_source_character:
                return _action_blocked(
                    REASON_TARGET_IS_SOURCE, "HumanIK source and target characters must differ"
                )
            return _action_allowed()
        source_binding = self._bindings.get(self._source_model_root)
        if source_binding is not None and source_binding.profile != target_binding.profile:
            return _action_blocked(
                REASON_PROFILE_MISMATCH,
                "HumanIK source/target assignment profile mismatch: "
                f"source={source_binding.profile}, target={target_binding.profile}. "
                f"Restore both models and reconnect them so they both characterize with the "
                f"same profile (default: {FULL_ASSIGNMENT_PROFILE}).",
            )
        return _action_allowed()

    def _describe_create_control_rig_action(
        self, key: Optional[str], preview_active: bool
    ) -> Dict[str, Any]:
        """Mirror ``create_control_rig``'s guards, in the order it checks them."""
        if preview_active:
            return _action_blocked(
                REASON_PREVIEW_ACTIVE,
                "Cannot create_control_rig while a HumanIK target preview is active",
            )
        if key is None:
            return _action_blocked(REASON_MODEL_REQUIRED, "Select a model to create a control rig")
        if key not in self._bindings:
            return _action_blocked(
                REASON_NOT_CHARACTERIZED, f"HumanIK model is not characterized: {key}"
            )
        if key == self._source_model_root:
            return _action_blocked(
                REASON_MODEL_IS_SOURCE,
                f"Cannot create_control_rig while model is the active HumanIK SOURCE: {key}",
            )
        return _action_allowed()

    def _describe_bake_to_mmd_rig_action(self) -> Dict[str, Any]:
        """Mirror ``bake_to_mmd_rig``'s guard: an active preview is required."""
        if self.active_preview is None or self._target_model_root is None:
            return _action_blocked(
                REASON_NO_ACTIVE_PREVIEW, "HumanIK target preview is not active"
            )
        return _action_allowed()

    def _describe_bake_to_control_rig_action(self) -> Dict[str, Any]:
        """Mirror ``bake_to_control_rig``'s preview/transaction guards."""
        if self.active_preview is None or self._target_model_root is None:
            return _action_blocked(
                REASON_NO_ACTIVE_PREVIEW, "HumanIK target preview is not active"
            )
        transaction = self._control_rig_transactions.get(self._target_model_root)
        if transaction is None or not transaction.active:
            return _action_blocked(
                REASON_NO_ACTIVE_CONTROL_RIG,
                "HumanIK target Control Rig transaction is not active",
            )
        return _action_allowed()

    def _describe_bake_from_control_rig_action(self) -> Dict[str, Any]:
        """Mirror ``bake_from_control_rig``'s preview/transaction guards."""
        if self.active_preview is None or self._target_model_root is None:
            return _action_blocked(
                REASON_NO_ACTIVE_PREVIEW, "HumanIK target preview is not active"
            )
        transaction = self._control_rig_transactions.get(self._target_model_root)
        if transaction is None or not transaction.active:
            return _action_blocked(
                REASON_NO_ACTIVE_CONTROL_RIG,
                "HumanIK target Control Rig transaction is not active",
            )
        return _action_allowed()

    def _describe_restore_mmd_rig_action(self, nothing_to_restore: bool) -> Dict[str, Any]:
        """Mirror ``restore_mmd_rig``'s no-op case.

        Unlike the other guards, calling ``restore_mmd_rig`` with nothing
        pending does not raise -- it simply returns ``False``.  ``allowed``
        here is a UI convenience (disable the button, nothing to do), not a
        prediction of an exception.
        """
        if nothing_to_restore:
            return _action_blocked(
                REASON_NOTHING_TO_RESTORE, "No preview, control rig, or pending recovery state"
            )
        return _action_allowed()

    def _describe_orphaned_control_rigs(self) -> List[Dict[str, Any]]:
        """Return scene ``HIKControlSetNode``s not owned by an active transaction.

        See ``describe_frontend_state``'s docstring for why this exists
        (HUMANIK-RESTORE-GAPS-1: scene reopen or a Control Rig created
        outside ``create_control_rig`` leaves nothing in
        ``self._control_rig_transactions`` for ``restore_mmd_rig`` to tear
        down). Read-only and intentionally cheap: one ``cmds.ls`` scan plus a
        per-node ``listConnections`` lookup for the owning ``HIKCharacterNode``,
        never ``collect_hik_ownership_report``/``classify_humanik_constraints``.
        Any query failure (no Maya ``cmds``, HumanIK plugin not loaded, a
        non-Maya test process) is swallowed and reported as no orphans found,
        the same fail-soft policy ``_describe_import_lock`` uses.
        """
        try:
            cmds = self._cmds or maya_cmds()
            control_set_nodes = cmds.ls(type="HIKControlSetNode") or []
        except Exception:
            return []
        owned_nodes = set()
        for transaction in self._control_rig_transactions.values():
            if transaction.active:
                owned_nodes.update(getattr(transaction, "created_nodes", ()) or ())
        rows = []
        for node in sorted(str(item) for item in control_set_nodes):
            if node in owned_nodes:
                continue
            character = None
            try:
                connected = cmds.listConnections(node, type="HIKCharacterNode") or []
                character = str(connected[0]) if connected else None
            except Exception:
                character = None
            binding = self.find_binding_by_character(character) if character else None
            rows.append(
                {
                    "controlSetNode": node,
                    "character": character,
                    "modelRoot": binding.model_root if binding else None,
                }
            )
        return rows

    def _describe_import_lock(self, key: str) -> Dict[str, Any]:
        """Return the ``describe_humanik_import_lock`` snapshot for ``key``.

        Detection failures (missing HumanIK plugin/MEL, non-Maya test
        process) are swallowed the same way ``describe_humanik_import_lock``
        itself swallows most Maya query failures, and are reported as an
        unblocked, uncharacterized lock rather than propagating.
        """
        try:
            lock = describe_humanik_import_lock(key, cmds_module=self._cmds, mel_module=self._mel)
        except Exception:
            return {
                "blocked": False,
                "reasonCode": None,
                "character": None,
                "hasControlRig": False,
            }
        return {
            "blocked": bool(lock.blocked),
            "reasonCode": _IMPORT_LOCK_REASON_BY_BLOCKED.get(lock.blocked),
            "character": lock.character,
            "hasControlRig": bool(lock.has_control_rig),
        }

    def find_binding_by_character(self, character: str) -> Optional[HumanIkFrontendBinding]:
        """Return the characterized binding for ``character``, if tracked.

        Used by ``humanik_control_rig_watch`` to map a HIK character name
        (read off a newly created ``HIKState2SK`` node) back to the
        characterized MMD model and its HIK joints, without exposing the
        session's internal ``_bindings`` dict directly.
        """
        for binding in self._bindings.values():
            if binding.character == character:
                return binding
        return None

    def _require_binding(self, model_root: str) -> HumanIkFrontendBinding:
        key = self._require_model_root(model_root)
        binding = self._bindings.get(key)
        if binding is None:
            raise RuntimeError(f"HumanIK model is not characterized: {key}")
        return binding

    def _reject_active_preview_mutation(self, operation: str) -> None:
        if self._preview is not None and self._preview.active:
            raise RuntimeError(f"Cannot {operation} while a HumanIK target preview is active")

    @staticmethod
    def _require_model_root(model_root: str) -> str:
        value = str(model_root or "").strip()
        if not value:
            raise ValueError("model_root is required")
        return value

    @staticmethod
    def _optional_model_root(model_root: Optional[str]) -> Optional[str]:
        """Return a stripped ``model_root``, or ``None`` -- never raises.

        Non-raising counterpart of ``_require_model_root`` for read-only
        state description, where a missing model is reported as a
        ``REASON_MODEL_REQUIRED`` action reason rather than an exception.
        """
        if model_root is None:
            return None
        value = str(model_root).strip()
        return value or None

    @staticmethod
    def _character_name(model_root: str) -> str:
        leaf = model_root.replace("|", "_").replace(":", "_").strip("_") or "Model"
        return f"MMDFrontend_{leaf}"


HumanIkFrontendController = HumanIkFrontendSession
