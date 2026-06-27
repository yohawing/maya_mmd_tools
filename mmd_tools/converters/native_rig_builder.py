"""
mmd-anim rig primitive API を使用して IK / 付与変形を構築するモジュール。

MAYA_HANDOFF.md の契約に基づき、PMX バイト列から manifest JSON を取得し、
IK チェーンと付与 solver を native (C) 側で生成する。

Maya DG へのノード接続は rig_converter.py が担当する。
このモジュールは Maya 非依存の純 Python ロジックのみを含む。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from mmd_tools.core.logger import get_logger
from mmd_tools.core.native.mmd_anim_runtime import (
    MmdAppendSolver,
    MmdIkChain,
    MmdRigSpec,
    is_rig_primitive_available,
)

logger = get_logger(__name__)


class RigManifest:
    """
    PMX rig spec manifest のパース結果を保持する。

    Attributes:
        bone_count: ボーン数
        bones: ボーン情報のリスト
        ik_chains: IK チェーン定義のリスト
        grants: 付与変形定義のリスト
    """

    def __init__(self, manifest: Dict[str, Any]):
        self.raw = manifest
        self.bone_count: int = manifest.get("boneCount", 0)
        self.bones: List[Dict[str, Any]] = manifest.get("bones", [])
        self.ik_chain_count: int = manifest.get("ikChainCount", 0)
        self.ik_chains: List[Dict[str, Any]] = manifest.get("ikChains", [])
        self.grant_count: int = manifest.get("grantCount", 0)
        self.grants: List[Dict[str, Any]] = manifest.get("grants", [])

    @classmethod
    def from_pmx_bytes(cls, pmx_bytes: bytes) -> Optional["RigManifest"]:
        if not is_rig_primitive_available():
            return None
        spec = MmdRigSpec.from_pmx_bytes(pmx_bytes)
        if spec is None:
            return None
        manifest = spec.manifest_json()
        spec.free()
        if manifest is None:
            return None
        return cls(manifest)


def build_ik_mini_chain(
    manifest: RigManifest,
    ik_chain_def: Dict[str, Any],
) -> Optional[Tuple[MmdIkChain, Dict[str, Any]]]:
    """
    manifest の ikChains エントリ 1 つから IK ミニチェーンを構築する。

    HANDOFF.md ミニチェーン構築ルール:
    1. controller, target, 全 links のボーン + 祖先を列挙
    2. PMX index 昇順で slot 0, 1, 2... とする
    3. parent_slot はミニチェーン内の親 slot (-1 = chain root)
    4. target_bone_slot, links[].bone_slot をリマップ

    Returns:
        (MmdIkChain, mapping_info) or None
        mapping_info: {"pmx_to_slot": {pmx_idx: slot}, "slot_to_pmx": {slot: pmx_idx},
                       "link_slots": [slot_indices]}
    """
    all_bones = manifest.bones
    controller_idx = ik_chain_def["controllerBoneIndex"]
    target_idx = ik_chain_def["targetBoneIndex"]
    link_defs = ik_chain_def.get("links", [])

    involved = set()
    involved.add(controller_idx)
    involved.add(target_idx)
    for lk in link_defs:
        involved.add(lk["boneIndex"])

    def _ancestors(idx: int) -> List[int]:
        result = []
        visited = set()
        while True:
            if idx < 0 or idx >= len(all_bones) or idx in visited:
                break
            visited.add(idx)
            parent = all_bones[idx].get("parentIndex", -1)
            if parent < 0 or parent >= len(all_bones):
                break
            result.append(parent)
            idx = parent
        return result

    for idx in list(involved):
        involved.update(_ancestors(idx))

    sorted_indices = sorted(involved)
    pmx_to_slot = {pmx_idx: slot for slot, pmx_idx in enumerate(sorted_indices)}
    slot_to_pmx = {slot: pmx_idx for pmx_idx, slot in pmx_to_slot.items()}

    # Collect absolute positions first, then convert to local (parent-relative).
    # The rig primitive solver expects local rest positions.
    abs_positions = []
    bones_for_chain = []
    for slot, pmx_idx in enumerate(sorted_indices):
        bone_data = all_bones[pmx_idx]
        parent_pmx = bone_data.get("parentIndex", -1)
        parent_slot = pmx_to_slot.get(parent_pmx, -1)

        abs_pos = bone_data.get("restPosition", [0, 0, 0])
        abs_positions.append(abs_pos)
        flags = 0
        fixed_axis = bone_data.get("fixedAxis")
        if fixed_axis is not None:
            from mmd_tools.core.native.mmd_anim_runtime import MMD_RUNTIME_RIG_BONE_FIXED_AXIS
            flags |= MMD_RUNTIME_RIG_BONE_FIXED_AXIS

        bones_for_chain.append({
            "parent_slot": parent_slot,
            "rest_position": None,
            "flags": flags,
            "fixed_axis": fixed_axis or [0, 0, 0],
        })

    for slot, bone in enumerate(bones_for_chain):
        abs_pos = abs_positions[slot]
        parent_slot = bone["parent_slot"]
        if parent_slot >= 0:
            parent_abs = abs_positions[parent_slot]
            bone["rest_position"] = [abs_pos[j] - parent_abs[j] for j in range(3)]
        else:
            bone["rest_position"] = list(abs_pos)

    links_for_chain = []
    link_slots = []
    for lk in link_defs:
        bone_slot = pmx_to_slot[lk["boneIndex"]]
        link_slots.append(bone_slot)
        links_for_chain.append({
            "bone_slot": bone_slot,
            "has_angle_limit": lk.get("hasAngleLimit", False),
            "angle_limit_min": lk.get("angleLimitMin", [0, 0, 0]),
            "angle_limit_max": lk.get("angleLimitMax", [0, 0, 0]),
        })

    target_slot = pmx_to_slot[target_idx]

    chain = MmdIkChain.create(
        bones=bones_for_chain,
        target_bone_slot=target_slot,
        links=links_for_chain,
        iteration_count=ik_chain_def.get("iterationCount", 40),
        limit_angle=ik_chain_def.get("limitAngle", 2.0),
    )
    if chain is None:
        logger.warning(
            f"IK chain creation failed: controller={controller_idx}, target={target_idx}"
        )
        return None

    mapping = {
        "pmx_to_slot": pmx_to_slot,
        "slot_to_pmx": slot_to_pmx,
        "link_slots": link_slots,
        "controller_pmx_index": controller_idx,
        "target_pmx_index": target_idx,
    }
    return chain, mapping


def build_append_solver(
    grant_def: Dict[str, Any],
) -> Optional[Tuple[MmdAppendSolver, Dict[str, Any]]]:
    """
    manifest の grants エントリ 1 つから append solver を構築する。

    Returns:
        (MmdAppendSolver, grant_info) or None
        grant_info: {"target_pmx_index", "source_pmx_index", "affect_rotation", "affect_translation"}
    """
    ratio = grant_def.get("ratio", 0.0)
    affect_rot = grant_def.get("affectRotation", False)
    affect_trans = grant_def.get("affectTranslation", False)

    if not affect_rot and not affect_trans:
        return None

    solver = MmdAppendSolver.create(
        ratio=ratio,
        affect_rotation=affect_rot,
        affect_translation=affect_trans,
    )
    if solver is None:
        return None

    info = {
        "target_pmx_index": grant_def["targetBoneIndex"],
        "source_pmx_index": grant_def["sourceBoneIndex"],
        "ratio": ratio,
        "affect_rotation": affect_rot,
        "affect_translation": affect_trans,
    }
    return solver, info


class NativeRigPrimitives:
    """
    PMX から構築した IK chain / append solver 群を保持するコンテナ。
    Maya ノードとの接続は別モジュール (rig_converter) が担当。
    """

    def __init__(self):
        self.ik_chains: List[Tuple[MmdIkChain, Dict[str, Any]]] = []
        self.append_solvers: List[Tuple[MmdAppendSolver, Dict[str, Any]]] = []
        self.manifest: Optional[RigManifest] = None

    @classmethod
    def from_pmx_bytes(cls, pmx_bytes: bytes) -> Optional["NativeRigPrimitives"]:
        manifest = RigManifest.from_pmx_bytes(pmx_bytes)
        if manifest is None:
            return None

        prims = cls()
        prims.manifest = manifest

        for ik_def in manifest.ik_chains:
            result = build_ik_mini_chain(manifest, ik_def)
            if result is not None:
                prims.ik_chains.append(result)

        for grant_def in manifest.grants:
            result = build_append_solver(grant_def)
            if result is not None:
                prims.append_solvers.append(result)

        logger.info(
            f"NativeRigPrimitives: {len(prims.ik_chains)} IK chains, "
            f"{len(prims.append_solvers)} append solvers built"
        )
        return prims

    def free(self) -> None:
        for chain, _ in self.ik_chains:
            chain.free()
        for solver, _ in self.append_solvers:
            solver.free()
        self.ik_chains.clear()
        self.append_solvers.clear()

    def __del__(self) -> None:
        self.free()
