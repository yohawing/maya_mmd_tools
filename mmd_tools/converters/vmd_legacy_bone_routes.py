"""Legacy bone-key routing helpers for VMD conversion."""

from __future__ import annotations

import json
from typing import Dict, List

import maya.cmds as cmds

from ..core.mmd_control_rig_motion import control_rig_edit_routes_for_joints
from .vmd_runtime_rig_helper import _ls_mmd_ccd_ik_nodes


def collect_ik_link_joints() -> dict:
    """Collect IK link joints driven by mmdCcdIk outputRotate."""
    ik_link_joints: dict = {}
    for node in _ls_mmd_ccd_ik_nodes():
        try:
            raw_chain = cmds.getAttr(f"{node}.chainJson")
            cfg = json.loads(raw_chain) if raw_chain else {}
        except Exception:
            continue

        links = cfg.get("links", [])
        for link_index, link in enumerate(links):
            dests = cmds.listConnections(
                f"{node}.outputRotate[{link_index}]",
                s=False,
                d=True,
                p=True,
            ) or []
            bone_slot = link.get("bone_slot", link_index)
            for dest in dests:
                jnt = dest.split(".", 1)[0]
                info = {"solver": node, "slot": bone_slot}
                ik_link_joints[jnt] = info
                try:
                    for long_name in cmds.ls(jnt, long=True) or []:
                        ik_link_joints[long_name] = info
                except Exception:
                    pass
    return ik_link_joints


def native_ik_handle_link_joints(handle: str) -> List[str]:
    """Return native IK handle link joints recorded on a Maya node."""
    if not cmds.attributeQuery("mmd_ik_link_joints_json", node=handle, exists=True):
        return []
    try:
        raw = cmds.getAttr(f"{handle}.mmd_ik_link_joints_json") or "[]"
        links = json.loads(raw)
    except Exception:
        return []
    return [j for j in links if isinstance(j, str) and cmds.objExists(j)]


def build_legacy_bone_key_routes(converter) -> Dict[str, dict]:
    """Build per-joint key routes for legacy sparse VMD bone animation."""
    append_info = converter._collect_append_info()
    ik_link_joints = converter._collect_ik_link_joints()
    control_routes = control_rig_edit_routes_for_joints(converter.bone_name_mapping.values())
    routes: Dict[str, dict] = {}

    for joint in set(converter.bone_name_mapping.values()):
        ik_info = ik_link_joints.get(joint)
        route = {
            "attr_targets": {},
            "skip_rotate": joint in ik_link_joints,
            "ik_solver_rotate": ik_info,
        }
        info = append_info.get(joint)
        if info:
            append_node = info.get("node")
            for src_attr, dst_attr in info.get("attr_map", {}).items():
                if append_node:
                    route["attr_targets"][src_attr] = (append_node, dst_attr)

        # In EDIT, the owned curve is the authored animation input. Unsupported
        # bones and solver-output links retain the established legacy route.
        route["attr_targets"].update(control_routes.get(joint, {}))

        if route["attr_targets"] or route["skip_rotate"] or ik_info:
            routes[joint] = route

    return routes
