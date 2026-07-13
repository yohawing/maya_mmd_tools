"""Resolve imported MMD joints into Maya HumanIK bone assignments.

This module is intentionally scene-independent.  The future HumanIK builder can
collect Maya joints and call this resolver before mutating HIK character nodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple
import unicodedata

from mmd_tools.config.humanik_mapping import MMD_TO_HIK_BONE, MMD_TO_HIK_BONE_INDEX
from mmd_tools.validation.bone_validator import BoneValidator


_BONE_VALIDATOR = BoneValidator()
_NO_BONE_INDEX = 1_000_000_000


@dataclass(frozen=True)
class HumanIkJointCandidate:
    """MMD joint metadata needed for HumanIK assignment resolution."""

    node: str
    mmd_name: str = ""
    english_name: str = ""
    bone_index: Optional[int] = None


@dataclass(frozen=True)
class HumanIkBoneAssignment:
    """Resolved HumanIK assignment for one Maya joint candidate."""

    joint: str
    mmd_bone: str
    hik_bone: str
    hik_index: int
    source: str
    bone_index: Optional[int] = None


@dataclass(frozen=True)
class HumanIkResolveResult:
    """Result of resolving MMD joints into indexed HumanIK assignments."""

    assignments: Tuple[HumanIkBoneAssignment, ...]
    missing_mmd_bones: Tuple[str, ...]
    unindexed_mmd_bones: Tuple[str, ...]
    duplicate_assignments: Tuple[HumanIkBoneAssignment, ...]

    @property
    def assignments_by_hik_index(self) -> Dict[int, HumanIkBoneAssignment]:
        """Return assignments keyed by HumanIK bone index."""
        return {assignment.hik_index: assignment for assignment in self.assignments}


def normalize_mmd_bone_name(name: str) -> Optional[str]:
    """Return the canonical MMD standard bone name for a user or scene name."""
    normalized = unicodedata.normalize("NFKC", (name or "").strip())
    if not normalized:
        return None
    return _BONE_VALIDATOR.name_to_standard.get(normalized.lower())


def resolve_humanik_assignments(candidates: Iterable[HumanIkJointCandidate]) -> HumanIkResolveResult:
    """Resolve candidate joints into deterministic HumanIK bone assignments.

    Metadata stored by the importer is preferred over English names and joint
    DAG names.  If multiple candidates resolve to the same HumanIK slot, the
    earlier PMX/PMD bone index wins.
    """
    best_by_hik_index: Dict[int, Tuple[Tuple[int, int, str], HumanIkBoneAssignment]] = {}
    duplicates = []
    unindexed = set()

    for candidate in candidates:
        resolved = _resolve_candidate(candidate)
        if resolved is None:
            continue
        mmd_bone, hik_bone, source, source_rank = resolved
        if mmd_bone not in MMD_TO_HIK_BONE_INDEX:
            unindexed.add(mmd_bone)
            continue

        assignment = HumanIkBoneAssignment(
            joint=candidate.node,
            mmd_bone=mmd_bone,
            hik_bone=hik_bone,
            hik_index=MMD_TO_HIK_BONE_INDEX[mmd_bone],
            source=source,
            bone_index=candidate.bone_index,
        )
        rank = (source_rank, _sort_bone_index(candidate.bone_index), candidate.node)
        current = best_by_hik_index.get(assignment.hik_index)
        if current is None:
            best_by_hik_index[assignment.hik_index] = (rank, assignment)
        elif rank < current[0]:
            duplicates.append(current[1])
            best_by_hik_index[assignment.hik_index] = (rank, assignment)
        else:
            duplicates.append(assignment)

    assignments = tuple(
        assignment
        for _, assignment in sorted(
            best_by_hik_index.values(),
            key=lambda item: item[1].hik_index,
        )
    )
    assigned_mmd_bones = {assignment.mmd_bone for assignment in assignments}
    missing_mmd_bones = tuple(mmd_bone for mmd_bone in MMD_TO_HIK_BONE_INDEX if mmd_bone not in assigned_mmd_bones)

    return HumanIkResolveResult(
        assignments=assignments,
        missing_mmd_bones=missing_mmd_bones,
        unindexed_mmd_bones=tuple(sorted(unindexed)),
        duplicate_assignments=tuple(sorted(duplicates, key=lambda item: (item.hik_index, item.joint))),
    )


def _resolve_candidate(candidate: HumanIkJointCandidate) -> Optional[Tuple[str, str, str, int]]:
    for source_rank, (source, value) in enumerate(
        (
            ("mmd_name", candidate.mmd_name),
            ("english_name", candidate.english_name),
            ("node", candidate.node.rsplit("|", 1)[-1]),
        )
    ):
        mmd_bone = normalize_mmd_bone_name(value)
        if mmd_bone and mmd_bone in MMD_TO_HIK_BONE:
            return mmd_bone, MMD_TO_HIK_BONE[mmd_bone], source, source_rank
    return None


def _sort_bone_index(index: Optional[int]) -> int:
    return index if index is not None else _NO_BONE_INDEX
