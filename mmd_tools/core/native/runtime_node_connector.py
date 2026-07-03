"""
Maya DG wiring helpers for the native mmdRuntimeInstance node.

The ctypes runtime wrapper owns FFI handles and sampling APIs. This module owns
scene mutation for the experimental C++ runtime node path so the wrapper does
not also carry Maya graph construction logic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from mmd_tools.core.constants import ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, ATTR_MMD_BONE_INDEX


_MATRIX_ATTR_MAP = [
    (0, "in00"),
    (1, "in01"),
    (2, "in02"),
    (3, "in03"),
    (4, "in10"),
    (5, "in11"),
    (6, "in12"),
    (7, "in13"),
    (8, "in20"),
    (9, "in21"),
    (10, "in22"),
    (11, "in23"),
    (12, "in30"),
    (13, "in31"),
    (14, "in32"),
    (15, "in33"),
]


def create_runtime_node_for_model(model_root: str, pmx_path: str, vmd_path: str = None) -> str:
    """
    Create an mmdRuntimeInstance node and associate it with a Maya model root.

    Args:
        model_root: Root transform of the imported MMD model.
        pmx_path: Source PMX path to store on the runtime node.
        vmd_path: Optional source VMD path to store on the runtime node.

    Returns:
        Created runtime node name.
    """
    import maya.cmds as cmds

    node = cmds.createNode("mmdRuntimeInstance", name="mmdRuntimeInstance#")

    cmds.setAttr(f"{node}.pmxData", pmx_path, type="string")
    if vmd_path:
        cmds.setAttr(f"{node}.vmdData", vmd_path, type="string")

    try:
        cmds.connectAttr("time1.outTime", f"{node}.time", force=True)
    except Exception:
        pass

    if cmds.objExists(model_root):
        try:
            if not cmds.attributeQuery("mmdRuntimeNode", node=model_root, exists=True):
                cmds.addAttr(model_root, ln="mmdRuntimeNode", at="message")
            existing_connections = (
                cmds.listConnections(f"{model_root}.mmdRuntimeNode", source=True, destination=False, plugs=True)
                or []
            )
            for source in existing_connections:
                if source == f"{node}.message":
                    break
                try:
                    cmds.disconnectAttr(source, f"{model_root}.mmdRuntimeNode")
                except Exception:
                    pass
            cmds.connectAttr(f"{node}.message", f"{model_root}.mmdRuntimeNode", force=True)
        except Exception:
            pass

    return node


def connect_runtime_node_outputs_to_model(
    node: str,
    model_root: str,
    pmx_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Connect an mmdRuntimeInstance node output to existing joints and morphs.

    For each bone with a matching joint:
      1. Build a fourByFourMatrix from the 16 raw runtime float values.
      2. Apply the MMD-to-Maya Z-flip via S * M * S.
      3. Multiply by the DAG parent's worldInverseMatrix[0].
      4. Decompose to local translate/rotate and connect the joint.

    For morphs, when pmx_path resolves, PMX vertex morph names are matched to
    blendShape aliases or stored raw morph name metadata.
    """
    import maya.cmds as cmds

    result: Dict[str, Any] = {
        "connected_bones": [],
        "connected_morphs": [],
        "skipped": [],
        "warnings": [],
        "utility_nodes": [],
    }

    if not cmds.objExists(node):
        result["skipped"].append(f"Runtime node {node!r} does not exist")
        return result
    if not cmds.objExists(model_root):
        result["skipped"].append(f"Model root {model_root!r} does not exist")
        return result

    def _make_zflip_node() -> str:
        """Create a shared fourByFourMatrix representing S = diag(1,1,-1,1)."""
        flip = cmds.createNode("fourByFourMatrix", name=f"{node}_zflip")
        result["utility_nodes"].append(flip)
        for row in range(4):
            for col in range(4):
                if row == col == 0:
                    val = 1.0
                elif row == col == 1:
                    val = 1.0
                elif row == col == 2:
                    val = -1.0
                elif row == col == 3:
                    val = 1.0
                else:
                    val = 0.0
                cmds.setAttr(f"{flip}.in{row}{col}", val)
        return flip

    joints_by_index: Dict[int, str] = {}
    for joint in cmds.listRelatives(model_root, allDescendents=True, type="joint") or []:
        if not cmds.attributeQuery(ATTR_MMD_BONE_INDEX, node=joint, exists=True):
            continue
        try:
            bone_index = int(cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}"))
        except Exception:
            continue
        if bone_index in joints_by_index:
            result["warnings"].append(
                f"Duplicate mmd_bone_index={bone_index}: {joints_by_index[bone_index]} and {joint}"
            )
        joints_by_index[bone_index] = joint

    if not joints_by_index:
        result["skipped"].append("No joints with mmd_bone_index found")
        return result

    unsupported_orientation = []
    for bone_idx in sorted(joints_by_index.keys()):
        joint = joints_by_index[bone_idx]
        try:
            joint_orient = cmds.getAttr(f"{joint}.jointOrient")[0]
            if any(abs(v) > 1e-6 for v in joint_orient):
                unsupported_orientation.append(
                    f"{joint} (bone_idx={bone_idx}) has non-zero jointOrient {joint_orient}"
                )
        except Exception:
            pass
        try:
            rotate_axis = cmds.getAttr(f"{joint}.rotateAxis")[0]
            if any(abs(v) > 1e-6 for v in rotate_axis):
                unsupported_orientation.append(
                    f"{joint} (bone_idx={bone_idx}) has non-zero rotateAxis {rotate_axis}"
                )
        except Exception:
            pass
    if unsupported_orientation:
        result["skipped"].append(
            "Live DG connection skipped because jointOrient/rotateAxis is not yet supported: "
            + "; ".join(unsupported_orientation)
        )
        return result

    zflip = _make_zflip_node()

    for bone_idx in sorted(joints_by_index.keys()):
        joint = joints_by_index[bone_idx]

        fbf = cmds.createNode("fourByFourMatrix", name=f"{joint}_fbf")
        result["utility_nodes"].append(fbf)

        base_idx = bone_idx * 16
        for offset, attr_name in _MATRIX_ATTR_MAP:
            src = f"{node}.worldMatrices[{base_idx + offset}]"
            dst = f"{fbf}.{attr_name}"
            try:
                cmds.connectAttr(src, dst, force=True)
            except Exception as exc:
                result["warnings"].append(f"Failed to connect {src} -> {dst}: {exc}")

        mm_world = cmds.createNode("multMatrix", name=f"{joint}_mm_world")
        result["utility_nodes"].append(mm_world)
        cmds.connectAttr(f"{zflip}.output", f"{mm_world}.matrixIn[0]", force=True)
        cmds.connectAttr(f"{fbf}.output", f"{mm_world}.matrixIn[1]", force=True)
        cmds.connectAttr(f"{zflip}.output", f"{mm_world}.matrixIn[2]", force=True)

        parents = cmds.listRelatives(joint, parent=True, fullPath=True) or []
        if parents:
            parent_node = parents[0]
            mm_local = cmds.createNode("multMatrix", name=f"{joint}_mm_local")
            result["utility_nodes"].append(mm_local)
            cmds.connectAttr(f"{mm_world}.matrixSum", f"{mm_local}.matrixIn[0]", force=True)
            cmds.connectAttr(
                f"{parent_node}.worldInverseMatrix[0]",
                f"{mm_local}.matrixIn[1]",
                force=True,
            )
            matrix_source = f"{mm_local}.matrixSum"
        else:
            matrix_source = f"{mm_world}.matrixSum"

        dm = cmds.createNode("decomposeMatrix", name=f"{joint}_dm")
        result["utility_nodes"].append(dm)
        cmds.connectAttr(matrix_source, f"{dm}.inputMatrix", force=True)

        try:
            rotate_order = int(cmds.getAttr(f"{joint}.rotateOrder"))
            cmds.setAttr(f"{dm}.inputRotateOrder", rotate_order)
        except Exception:
            pass

        try:
            cmds.connectAttr(f"{dm}.outputTranslate", f"{joint}.translate", force=True)
            cmds.connectAttr(f"{dm}.outputRotate", f"{joint}.rotate", force=True)
        except Exception as exc:
            result["warnings"].append(f"Failed to connect {dm} outputs to {joint}: {exc}")
            continue

        result["connected_bones"].append((joint, bone_idx))

    _connect_runtime_morph_outputs(cmds, node, model_root, pmx_path, result)
    return result


def _connect_runtime_morph_outputs(cmds: Any, node: str, model_root: str, pmx_path: Optional[str], result: Dict[str, Any]) -> None:
    """Connect runtime morphWeights outputs to blendShape weights when possible."""
    if not pmx_path:
        result["warnings"].append(
            "pmx_path not provided; morphWeights -> blendShape connection skipped. "
            "Pass pmx_path to enable morph resolution."
        )
        return

    try:
        from mmd_tools.core.maya_utils import sanitize_text
        from mmd_tools.core.native.mmd_anim_runtime import MmdParsedModel
        from mmd_tools.core.native.native_pmx_parser import parse_pmx_native
        from mmd_tools.core.pmx_data.morph import PmxMorphType

        pmx_bytes = Path(pmx_path).read_bytes()
        parsed = MmdParsedModel.from_pmx_bytes(pmx_bytes)
        pmx_morph_names = []
        pmx_morph_spans = []
        if parsed is not None and parsed.vertex_morph_count > 0:
            pmx_morph_names = parsed.vertex_morph_names or []
            pmx_morph_spans = parsed.vertex_morph_spans or []
            parsed.free()
        elif parsed is not None:
            parsed.free()

        if not pmx_morph_names:
            pmx_data = parse_pmx_native(pmx_path)
            if pmx_data is not None:
                for pmx_index, morph in enumerate(getattr(pmx_data, "morphs", []) or []):
                    if getattr(morph, "morph_type", None) != PmxMorphType.VertexMorph:
                        continue
                    pmx_morph_spans.append((0, 0, pmx_index))
                    pmx_morph_names.append(getattr(morph, "name", "") or "")

        if not pmx_morph_names:
            return

        vtx_idx_to_global = {}
        for vmi, span in enumerate(pmx_morph_spans):
            if len(span) >= 3:
                vtx_idx_to_global[vmi] = int(span[2])
            else:
                vtx_idx_to_global[vmi] = vmi

        mesh_shapes = cmds.listRelatives(
            model_root,
            allDescendents=True,
            type="mesh",
            fullPath=True,
        ) or []
        model_blend_shapes = []
        for shape in mesh_shapes:
            for history_node in cmds.listHistory(shape, pruneDagObjects=True) or []:
                if cmds.nodeType(history_node) != "blendShape":
                    continue
                if history_node not in model_blend_shapes:
                    model_blend_shapes.append(history_node)

        for bs_node in model_blend_shapes:
            _connect_blendshape_morph_outputs(
                cmds,
                node,
                bs_node,
                pmx_morph_names,
                vtx_idx_to_global,
                sanitize_text,
                result,
            )
    except Exception as exc:
        result["warnings"].append(f"Morph resolution skipped (could not read PMX morph names): {exc}")


def _connect_blendshape_morph_outputs(
    cmds: Any,
    node: str,
    bs_node: str,
    pmx_morph_names: list,
    vtx_idx_to_global: dict,
    sanitize_text: Any,
    result: Dict[str, Any],
) -> None:
    """Connect matching runtime morph output plugs for one blendShape node."""
    weight_count = cmds.blendShape(bs_node, query=True, weightCount=True) or 0

    stored_raw_to_index = {}
    if cmds.attributeQuery(ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, node=bs_node, exists=True):
        try:
            parsed_names = json.loads(cmds.getAttr(f"{bs_node}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}") or "{}")
            if isinstance(parsed_names, dict):
                for stored_index, stored_name in parsed_names.items():
                    stored_raw_to_index[str(stored_name)] = int(stored_index)
        except (TypeError, ValueError):
            stored_raw_to_index = {}

    for vmi, pmx_name in enumerate(pmx_morph_names):
        if not pmx_name:
            continue
        global_idx = vtx_idx_to_global.get(vmi, vmi)
        sanitized_alias = sanitize_text(pmx_name)
        stored_wi = stored_raw_to_index.get(pmx_name)

        for weight_index in range(weight_count):
            if stored_wi is not None:
                if weight_index != stored_wi:
                    continue
            else:
                alias = cmds.aliasAttr(f"{bs_node}.weight[{weight_index}]", query=True)
                if not alias:
                    continue
                if not (alias == sanitized_alias or alias == pmx_name):
                    continue
            try:
                src = f"{node}.morphWeights[{global_idx}]"
                dst = f"{bs_node}.weight[{weight_index}]"
                existing_sources = (
                    cmds.listConnections(
                        dst,
                        source=True,
                        destination=False,
                        plugs=True,
                    )
                    or []
                )
                if src not in existing_sources:
                    cmds.connectAttr(src, dst, force=True)
                result["connected_morphs"].append((pmx_name, global_idx, bs_node, weight_index))
            except Exception as exc:
                result["warnings"].append(
                    f"Failed to connect morphWeights[{global_idx}] -> {bs_node}.weight[{weight_index}]: {exc}"
                )
            break
