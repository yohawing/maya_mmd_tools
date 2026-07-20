"""UI-neutral HumanIK setup, source/target preview, and bake orchestration.

This module is the narrow frontend boundary used by UI or command adapters.  It
keeps Maya ``cmds``/MEL dependencies injectable so lifecycle behavior can be
tested without opening HumanIK panels.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, Optional, Tuple

from mmd_tools.core.humanik_bake import HumanIkBakeResult, bake_humanik_target_preview
from mmd_tools.core.humanik_builder import (
    HumanIkCharacterCreationError,
    create_humanik_control_rig,
    create_humanik_definition,
    delete_humanik_character,
    lock_humanik_definition,
    resolve_scene_humanik_assignments,
)
from mmd_tools.core.humanik_constraints import (
    classify_humanik_constraints,
    collect_humanik_constraint_facts,
)
from mmd_tools.core.humanik_preview import (
    BLOCKING_CLASSIFICATIONS,
    HumanIkTargetPreview,
    begin_humanik_target_preview,
    stop_humanik_target_preview,
)
from mmd_tools.core.humanik_resolver import (
    HumanIkBoneAssignment,
    HumanIkResolveResult,
)


FINGER_HIK_MARKERS = ("Index", "Middle", "Ring", "Pinky", "Thumb")
FRONTEND_ASSIGNMENT_PROFILE = "body-only"
REFERENCE_QUALITY_DIAGNOSTICS = {
    "status": "experimental",
    "referenceS5bBodyMatrixResidual": 0.0298786502441323,
    "referenceS5bBodyMatrixResidualTolerance": 0.001,
    "fingerStatus": "deferred",
}
EXPECTED_BODY_ASSIGNMENT_COUNT = 25
EXPECTED_FINGER_ASSIGNMENT_COUNT = 30


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


def _assignment_row(assignment: HumanIkBoneAssignment) -> Dict[str, Any]:
    return {
        "joint": str(assignment.joint),
        "mmdBone": str(assignment.mmd_bone),
        "hikBone": str(assignment.hik_bone),
        "hikIndex": int(assignment.hik_index),
        "source": str(assignment.source),
        "boneIndex": assignment.bone_index,
    }


@dataclass
class HumanIkFrontendBinding:
    """Characterized body-only HumanIK binding retained by a frontend session."""

    model_root: str
    character: str
    result: HumanIkResolveResult
    excluded_finger_assignments: Tuple[HumanIkBoneAssignment, ...] = ()
    stance: Dict[str, Any] = field(default_factory=dict)
    control_rig_created: bool = False

    @property
    def assignments(self) -> Tuple[HumanIkBoneAssignment, ...]:
        """Return the body-only assignments used for characterization."""
        return self.result.assignments

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe binding summary."""
        return {
            "modelRoot": self.model_root,
            "character": self.character,
            "profile": FRONTEND_ASSIGNMENT_PROFILE,
            "assignmentCount": len(self.assignments),
            "assignments": [_assignment_row(item) for item in self.assignments],
            "required": {
                "genericLockMinimumAssignmentCount": 1,
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

    def __init__(self, cmds_module=None, mel_module=None, ownership_id: str = "mmd-tools:frontend"):
        self._cmds = cmds_module
        self._mel = mel_module
        self._ownership_id = str(ownership_id)
        self._bindings: Dict[str, HumanIkFrontendBinding] = {}
        self._source_model_root: Optional[str] = None
        self._target_model_root: Optional[str] = None
        self._ownership_report: Optional[Dict[str, Any]] = None
        self._preview: Optional[HumanIkTargetPreview] = None
        self._pending_characters: Dict[str, str] = {}

    @property
    def active_preview(self) -> Optional[HumanIkTargetPreview]:
        """Return the active target preview, if any."""
        return self._preview if self._preview and self._preview.active else None

    def setup_and_characterize(
        self,
        model_root: str,
        *,
        stance_confirmed: bool = False,
    ) -> HumanIkFrontendBinding:
        """Resolve, create, lock, and retain a body-only HIK character binding."""
        key = self._require_model_root(model_root)
        if not stance_confirmed:
            raise ValueError("stance_confirmed=True is required before HumanIK characterization")
        self._reject_active_preview_mutation("setup_and_characterize")
        existing = self._bindings.get(key)
        if existing is not None:
            return existing
        result = resolve_scene_humanik_assignments(key, cmds_module=self._cmds)
        body_result, excluded = _split_body_assignments(result)
        if not body_result.assignments:
            raise ValueError(f"HumanIK setup resolved no body assignments for: {key}")
        try:
            character = create_humanik_definition(
                body_result,
                name_hint=self._character_name(key),
                mel_module=self._mel,
                update_ui=False,
            )
        except HumanIkCharacterCreationError as creation_error:
            if creation_error.cleanup_error is not None:
                self._pending_characters[creation_error.character] = key
            raise
        self._pending_characters[character] = key
        try:
            lock_humanik_definition(character, mel_module=self._mel)
        except Exception as lock_error:
            try:
                delete_humanik_character(character, mel_module=self._mel)
            except Exception as cleanup_error:
                raise RuntimeError(
                    f"HumanIK lock failed for {character}; cleanup also failed: {cleanup_error}"
                ) from lock_error
            self._pending_characters.pop(character, None)
            raise
        self._pending_characters.pop(character, None)
        binding = HumanIkFrontendBinding(
            model_root=key,
            character=character,
            result=body_result,
            excluded_finger_assignments=excluded,
            stance={
                "mode": "user-confirmed-current-t-pose",
                "saved": True,
                "userConfirmedCommonTPose": True,
            },
        )
        self._bindings[key] = binding
        return binding

    def enter_source_mode(self, model_root: str) -> HumanIkFrontendBinding:
        """Select a characterized binding as the HumanIK source character."""
        self._reject_active_preview_mutation("enter_source_mode")
        binding = self._require_binding(model_root)
        self._source_model_root = binding.model_root
        return binding

    def enter_target_mode(self, model_root: str) -> HumanIkTargetPreview:
        """Start a target preview after ownership classification and blocker checks."""
        key = self._require_model_root(model_root)
        if self._source_model_root is None:
            raise RuntimeError("HumanIK source mode must be entered before target mode")
        if key == self._source_model_root:
            raise ValueError("HumanIK source and target model roots must differ")
        if self._preview is not None and self._preview.active:
            if self._target_model_root == key:
                return self._preview
            raise RuntimeError("A HumanIK target preview is already active")
        source = self._bindings[self._source_model_root]
        target = self._require_binding(key)
        target_joints = tuple(assignment.joint for assignment in target.assignments)
        report = classify_humanik_constraints(
            collect_humanik_constraint_facts(cmds_module=self._cmds),
            target_joints,
        )
        self._target_model_root = key
        self._ownership_report = report
        blockers = [
            row for row in report.get("rows", [])
            if row.get("classification") in BLOCKING_CLASSIFICATIONS
        ]
        if blockers:
            labels = ", ".join(f"{row['node']}:{row['classification']}" for row in blockers)
            raise RuntimeError(f"HumanIK target preview blocked: {labels}")
        preview = begin_humanik_target_preview(
            self._ownership_id,
            target.character,
            source.character,
            report,
            target_joints,
            cmds_module=self._cmds,
            mel_module=self._mel,
        )
        self._target_model_root = key
        self._preview = preview
        return preview

    def create_control_rig(self, model_root: str) -> bool:
        """Create a control rig on an already characterized binding."""
        self._reject_active_preview_mutation("create_control_rig")
        binding = self._require_binding(model_root)
        created = create_humanik_control_rig(binding.character, mel_module=self._mel)
        binding.control_rig_created = bool(created)
        return bool(created)

    def bake_to_mmd_rig(self, start: int, end: int) -> HumanIkBakeResult:
        """Bake the active target preview into the target MMD rig."""
        preview = self.active_preview
        if preview is None or self._target_model_root is None:
            raise RuntimeError("HumanIK target preview is not active")
        target = self._bindings[self._target_model_root]
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
                self._preview = None
                self._target_model_root = None
                self._ownership_report = None
            raise
        self._preview = None
        self._target_model_root = None
        self._ownership_report = None
        return result

    def restore_mmd_rig(self) -> bool:
        """Stop and clear an active preview; repeated calls are idempotent."""
        preview = self._preview
        restored = False
        first_error = None
        try:
            if preview is not None and preview.active:
                stop_humanik_target_preview(preview, cmds_module=self._cmds, mel_module=self._mel)
                restored = True
        except Exception as exc:
            first_error = exc
        finally:
            if preview is not None and not preview.active:
                self._preview = None
                self._target_model_root = None
                self._ownership_report = None
        for character in list(self._pending_characters):
            try:
                delete_humanik_character(character, mel_module=self._mel)
            except Exception as exc:
                if first_error is None:
                    first_error = exc
            else:
                self._pending_characters.pop(character, None)
                restored = True
        if first_error is not None:
            raise first_error
        return restored

    def inspect_model(self, model_root: str) -> Dict[str, Any]:
        """Resolve a model into body and deferred finger assignments read-only.

        The returned report is JSON-safe and does not create, lock, or mutate a
        HumanIK character.  UI callers can display it before requesting
        ``setup_and_characterize`` with explicit stance confirmation.
        """
        key = self._require_model_root(model_root)
        result = resolve_scene_humanik_assignments(key, cmds_module=self._cmds)
        body_result, excluded = _split_body_assignments(result)
        assignments = [_assignment_row(item) for item in body_result.assignments]
        excluded_rows = [_assignment_row(item) for item in excluded]
        unresolved = {
            "missingMmdBones": list(body_result.missing_mmd_bones),
            "unindexedMmdBones": list(body_result.unindexed_mmd_bones),
        }
        ambiguous = [_assignment_row(item) for item in body_result.duplicate_assignments]
        return {
            "modelRoot": key,
            "profile": FRONTEND_ASSIGNMENT_PROFILE,
            "assignments": assignments,
            "bodyAssignments": assignments,
            "assignmentCount": len(assignments),
            "excludedFingerAssignments": excluded_rows,
            "excludedFingerCount": len(excluded_rows),
            "unresolved": unresolved,
            "missingMmdBones": list(unresolved["missingMmdBones"]),
            "unindexedMmdBones": list(unresolved["unindexedMmdBones"]),
            "ambiguous": ambiguous,
            "duplicateAssignments": ambiguous,
            "blocked": [] if assignments else ["no_resolved_assignments"],
        }

    def inspect_target_ownership(self, model_root: str) -> Dict[str, Any]:
        """Classify target writers without starting a HumanIK preview."""
        model_report = self.inspect_model(model_root)
        target_joints = tuple(row["joint"] for row in model_report["assignments"])
        ownership = classify_humanik_constraints(
            collect_humanik_constraint_facts(cmds_module=self._cmds),
            target_joints,
        )
        blockers = [
            row for row in ownership.get("rows", [])
            if row.get("classification") in BLOCKING_CLASSIFICATIONS
        ]
        return {
            **model_report,
            "ownership": ownership,
            "constraintCounts": dict(ownership.get("counts", {})),
            "constraintRows": list(ownership.get("rows", [])),
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
        blockers = [
            row for row in ownership_rows
            if row.get("classification") in BLOCKING_CLASSIFICATIONS
        ]
        ownership_counts = (self._ownership_report or {}).get("counts", {})
        selected_summary = selected.to_dict() if selected else None
        return {
            "modelRoot": str(model_root) if model_root else None,
            "character": selected.character if selected else None,
            "profile": FRONTEND_ASSIGNMENT_PROFILE,
            "source": {
                "modelRoot": source.model_root if source else None,
                "character": source.character if source else None,
                "assignmentCount": len(source.assignments) if source else 0,
            },
            "target": {
                "modelRoot": target.model_root if target else None,
                "character": target.character if target else None,
                "assignmentCount": len(target.assignments) if target else 0,
            },
            "assignments": selected_summary or {
                "assignmentCount": 0,
                "required": {
                    "genericLockMinimumAssignmentCount": 1,
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
                "journalAvailable": bool(self.active_preview and self.active_preview.journal),
            },
            "pendingRecovery": {
                "characterCount": len(self._pending_characters),
                "characters": sorted(self._pending_characters),
            },
            "profileCoverage": {
                "expectedBodyAssignmentCount": EXPECTED_BODY_ASSIGNMENT_COUNT,
                "expectedFingerExcludedCount": EXPECTED_FINGER_ASSIGNMENT_COUNT,
                "actualBodyAssignmentCount": len(selected.assignments) if selected else 0,
                "actualFingerExcludedCount": len(selected.excluded_finger_assignments) if selected else 0,
            },
            "quality": dict(REFERENCE_QUALITY_DIAGNOSTICS),
        }

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
    def _character_name(model_root: str) -> str:
        leaf = model_root.replace("|", "_").replace(":", "_").strip("_") or "Model"
        return f"MMDFrontend_{leaf}"


HumanIkFrontendController = HumanIkFrontendSession
