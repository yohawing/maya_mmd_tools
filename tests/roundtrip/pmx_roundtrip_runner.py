#!/usr/bin/env python
"""PMX roundtrip runner: import → parse → export → re-import.

For each case in the manifest:
  1. Parse source PMX via PmxData to obtain raw structured data.
  2. New Maya scene, import source PMX (Maya-side sanity).
  3. Convert PmxData → exporter dict (supported sections only).
  4. Export to a new PMX under --out-dir/exports/.
  5. Parse the exported PMX to verify binary integrity.
  6. New Maya scene, import the exported PMX.
  7. Collect diffs/warnings for unsupported PMX data.

Results are written to --out-dir/results.json.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BUILD_ROOT = (ROOT / "build").resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.common.maya_plugin_setup import load_mmd_tools_plugin

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_UNSUPPORTED_WARN = {
    "sdef_vertices": "SDEF vertex weight data skipped (not supported by exporter dict)",
    "additional_uvs": "Additional UV layers skipped (exporter dict supports 1 UV)",
    "soft_bodies": "Soft body data is PMX v2.1-only and unsupported in roundtrip",
    "bone_ik": "IK bone data skipped during PmxData→dict conversion",
    "toon_textures": "Toon textures skipped during PmxData→dict conversion",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_build_path(value: str | Path, option_name: str) -> Path:
    """Resolve an output path and require it to stay under build/."""
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if path != BUILD_ROOT and BUILD_ROOT not in path.parents:
        raise ValueError(f"{option_name} must resolve under {BUILD_ROOT}: {path}")
    return path


def _case_name(raw: str) -> str:
    """Return a filesystem-safe case name."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._")
    return safe[:80] or "case"


def _load_cases(manifest_path: str | Path) -> list[dict[str, Any]]:
    """Load manifest cases and resolve relative paths from the manifest directory."""
    path = Path(manifest_path)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    manifest_dir = path.parent

    cases: list[dict[str, Any]] = data.get("cases", [])
    models: list[str] = data.get("models", [])

    # If no cases but there are models, promote models to single-import cases.
    if not cases and models:
        for m in models:
            mp = Path(m)
            if not mp.is_absolute():
                mp = (manifest_dir / mp).resolve()
            cases.append({"name": _case_name(mp.stem), "model": str(mp)})

    if not cases:
        raise ValueError(f"manifest has no cases and no models: {path}")

    resolved: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        case_name = case.get("name") or f"case_{index:03d}"
        model_path = case.get("model")
        synthetic = case.get("synthetic")

        if model_path:
            model = Path(model_path)
            if not model.is_absolute():
                model = (manifest_dir / model).resolve()
            resolved_model = str(model)
        elif synthetic:
            resolved_model = ""
        else:
            raise ValueError(
                f"case '{case_name}' must specify either 'model' or 'synthetic'"
            )

        resolved.append(
            {
                "name": case_name,
                "model": resolved_model,
                "synthetic": synthetic,
            }
        )
    return resolved


def _initialize_maya() -> None:
    """Initialize Maya and fail fast unless the production nodes are registered."""
    import maya.standalone

    try:
        maya.standalone.initialize(name="python")
    except RuntimeError:
        pass
    load_mmd_tools_plugin(ROOT)


# ---------------------------------------------------------------------------
# PmxData → exporter dict conversion
# ---------------------------------------------------------------------------


def _pmxdata_to_exporter_dict(pmx_data: Any, warn_list: list[str]) -> dict:
    """Convert a PmxData object into the dict format expected by PmxExporter.

    Unsupported sections (SDEF, additional UVs, soft bodies, IK, toon textures)
    are collected as warnings rather than errors.
    """
    from mmd_tools.core.pmx_data.bone import PmxBoneFlag
    from mmd_tools.core.display_frame_metadata import display_frames_to_dicts
    from mmd_tools.core.pmx_data.morph import PmxMorphType

    maya_data: dict[str, Any] = {}
    maya_data["model_name"] = pmx_data.header.model_name or "Untitled"
    if pmx_data.header.model_name_english is not None:
        maya_data["model_name_english"] = pmx_data.header.model_name_english
    if pmx_data.header.comment is not None:
        maya_data["comment"] = pmx_data.header.comment
    if pmx_data.header.comment_english is not None:
        maya_data["comment_english"] = pmx_data.header.comment_english

    # -- vertices -----------------------------------------------------------
    vertices_raw: list[dict] = []
    sdef_count = 0
    additional_uv_count_total = 0
    for v in pmx_data.vertices:
        vd: dict[str, Any] = {
            "position": list(v.position),
            "normal": list(v.normal),
            "uv": list(v.uv),
            "bone_indices": list(v.bone_indices),
            "bone_weights": list(v.bone_weights),
            "edge_magnification": v.edge_magnification,
        }
        # Warn about SDEF (weight_transform_type == 3)
        if v.weight_transform_type == 3:
            sdef_count += 1
        # Warn about additional UVs
        if getattr(v, "additional_uvs", None):
            additional_uv_count_total += 1
        vertices_raw.append(vd)
    maya_data["vertices"] = vertices_raw

    if sdef_count:
        warn_list.append(
            f"{_UNSUPPORTED_WARN['sdef_vertices']} ({sdef_count} vertices)"
        )
    if additional_uv_count_total:
        warn_list.append(
            f"{_UNSUPPORTED_WARN['additional_uvs']} ({additional_uv_count_total} vertices)"
        )

    # -- faces --------------------------------------------------------------
    faces_raw: list[list[int]] = []
    for face in pmx_data.faces:
        faces_raw.append(list(face.indices))
    maya_data["faces"] = faces_raw

    # -- textures -----------------------------------------------------------
    maya_data["textures"] = list(pmx_data.textures)

    if pmx_data.toon_textures:
        warn_list.append(_UNSUPPORTED_WARN["toon_textures"])

    # -- materials ----------------------------------------------------------
    materials_raw: list[dict] = []
    for mat in pmx_data.materials:
        md: dict[str, Any] = {
            "name": mat.name,
            "name_english": mat.name_english,
            "diffuse": list(mat.diffuse),
            "specular": list(mat.specular),
            "specular_coefficient": mat.specular_coefficient,
            "ambient": list(mat.ambient),
            "draw_flag": mat.draw_flag,
            "edge_color": list(mat.edge_color),
            "edge_size": mat.edge_size,
            "texture_index": mat.texture_index,
            "sphere_texture_index": mat.sphere_texture_index,
            "sphere_mode": mat.sphere_mode,
            "shared_toon_flag": mat.shared_toon_flag,
            "toon_texture_index": mat.toon_texture_index,
            "memo": mat.memo,
            "face_count": mat.face_count,
        }
        materials_raw.append(md)
    maya_data["materials"] = materials_raw

    # -- bones --------------------------------------------------------------
    bones_raw: list[dict] = []
    for bone in pmx_data.bones:
        bd: dict[str, Any] = {
            "name": bone.name,
            "name_english": bone.name_english,
            "position": list(bone.position),
            "parent_index": bone.parent_bone_index,
            "transform_layer": bone.transform_layer,
            "bone_flag": int(bone.bone_flag) if hasattr(bone.bone_flag, "value") else bone.bone_flag,
            "connect_position_offset": list(bone.connect_position_offset),
            "connect_bone_index": bone.connect_bone_index,
            "grant_parent_bone_index": bone.grant_parent_bone_index,
            "grant_rate": bone.grant_rate,
            "axis_direction": list(bone.axis_direction),
            "x_axis_direction": list(bone.x_axis_direction),
            "z_axis_direction": list(bone.z_axis_direction),
            "key_value": bone.key_value,
            "ik_target_bone_index": bone.ik_target_bone_index,
            "ik_loop_count": bone.ik_loop_count,
            "ik_limit_angle": bone.ik_limit_angle,
        }
        # IK links -- warn and skip since the exporter doesn't handle them
        if bone.ik_links:
            warn_list.append(
                f"{_UNSUPPORTED_WARN['bone_ik']} (bone '{bone.name}' has {len(bone.ik_links)} IK links)"
            )
        # PMX flag inversion for connect_position_offset vs connect_bone_index
        # If CONNECT_BONE flag is NOT set, the exporter expects connect_position_offset
        if not (int(bd["bone_flag"]) & int(PmxBoneFlag.CONNECT_BONE)):
            bd["connect_bone_index"] = -1
        else:
            bd["connect_position_offset"] = [0.0, 0.0, 0.0]
        bones_raw.append(bd)
    maya_data["bones"] = bones_raw

    # -- morphs -------------------------------------------------------------
    morphs_raw: list[dict] = []
    for morph in pmx_data.morphs:
        morph_type_val = int(morph.morph_type) if hasattr(morph.morph_type, "value") else morph.morph_type
        # Map to string type expected by exporter
        if morph_type_val == int(PmxMorphType.VertexMorph):
            type_str = "vertex"
        elif morph_type_val == int(PmxMorphType.BoneMorph):
            type_str = "bone"
        elif morph_type_val == int(PmxMorphType.MaterialMorph):
            type_str = "material"
        elif int(PmxMorphType.UVMorph) <= morph_type_val <= int(PmxMorphType.AdditionalUVMorph4):
            warn_list.append(f"UV morph skipped: {morph.name}")
            continue
        elif morph_type_val == int(PmxMorphType.FlipMorph):
            warn_list.append(f"Flip morph skipped: {morph.name}")
            continue
        elif morph_type_val == int(PmxMorphType.ImpulseMorph):
            warn_list.append(f"Impulse morph skipped: {morph.name}")
            continue
        else:
            warn_list.append(f"Unknown morph type {morph_type_val} skipped: {morph.name}")
            continue

        md: dict[str, Any] = {
            "name": morph.name,
            "name_english": morph.name_english,
            "panel": morph.panel,
            "type": type_str,
            "offsets": [],
        }
        for off in morph.offsets:
            od: dict[str, Any] = {}
            if type_str == "vertex":
                od["vertex_index"] = off["vertex_index"]
                od["position_offset"] = list(off["position_offset"])
            elif type_str == "bone":
                od["bone_index"] = off["bone_index"]
                od["translation"] = list(off.get("translation", [0.0, 0.0, 0.0]))
                od["rotation"] = list(off.get("rotation", [0.0, 0.0, 0.0, 1.0]))
            elif type_str == "material":
                od["material_index"] = off["material_index"]
                od["operation_type"] = off.get("operation_type", 1)
                od["diffuse"] = list(off.get("diffuse", [0.0, 0.0, 0.0, 0.0]))
                od["specular"] = list(off.get("specular", [0.0, 0.0, 0.0]))
                od["specular_coefficient"] = off.get("specular_coefficient", 0.0)
                od["ambient"] = list(off.get("ambient", [0.0, 0.0, 0.0]))
                od["edge_color"] = list(off.get("edge_color", [0.0, 0.0, 0.0, 0.0]))
                od["edge_size"] = off.get("edge_size", 0.0)
                od["texture_factor"] = list(off.get("texture_factor", [0.0, 0.0, 0.0, 0.0]))
                od["sphere_texture_factor"] = list(off.get("sphere_texture_factor", [0.0, 0.0, 0.0, 0.0]))
                od["toon_texture_factor"] = list(off.get("toon_texture_factor", [0.0, 0.0, 0.0, 0.0]))
            md["offsets"].append(od)
        morphs_raw.append(md)
    maya_data["morphs"] = morphs_raw

    # -- rigid bodies -------------------------------------------------------
    rigid_bodies_raw: list[dict] = []
    for rb in pmx_data.rigid_bodies:
        rbd: dict[str, Any] = {
            "name": rb.name,
            "name_english": rb.name_english,
            "related_bone_index": rb.related_bone_index,
            "group": rb.group,
            "collision_mask": rb.collision_mask,
            "shape_type": rb.shape_type,
            "size": list(rb.size),
            "position": list(rb.position),
            "rotation": list(rb.rotation),
            "mass": rb.mass,
            "velocity_attenuation": rb.velocity_attenuation,
            "rotation_attenuation": rb.rotation_attenuation,
            "elasticity": rb.elasticity,
            "friction": rb.friction,
            "physics_mode": rb.physics_mode,
        }
        rigid_bodies_raw.append(rbd)
    maya_data["rigid_bodies"] = rigid_bodies_raw

    # -- joints -------------------------------------------------------------
    joints_raw: list[dict] = []
    for j in pmx_data.joints:
        jd: dict[str, Any] = {
            "name": j.name,
            "name_english": j.name_english,
            "joint_type": j.joint_type,
            "rigid_body_a_index": j.rigid_body_a_index,
            "rigid_body_b_index": j.rigid_body_b_index,
            "position": list(j.position),
            "rotation": list(j.rotation),
            "translation_limit_min": list(j.translation_limit_min),
            "translation_limit_max": list(j.translation_limit_max),
            "rotation_limit_min": list(j.rotation_limit_min),
            "rotation_limit_max": list(j.rotation_limit_max),
            "spring_translation": list(j.spring_translation),
            "spring_rotation": list(j.spring_rotation),
        }
        joints_raw.append(jd)
    maya_data["joints"] = joints_raw

    # -- display frames -----------------------------------------------------
    maya_data["display_frames"] = display_frames_to_dicts(pmx_data.display_frames)

    # -- soft bodies --------------------------------------------------------
    if getattr(pmx_data, "soft_bodies", None) and len(pmx_data.soft_bodies) > 0:
        warn_list.append(
            f"{_UNSUPPORTED_WARN['soft_bodies']} ({len(pmx_data.soft_bodies)} soft bodies)"
        )

    return maya_data


def _get_field(value: Any, field: str, default: Any = None) -> Any:
    """Read attribute or dict key depending on source type."""
    if isinstance(value, dict):
        return value.get(field, default)
    return getattr(value, field, default)


def _format_value(value: Any) -> str:
    """Serialize values for diff output."""
    return json.dumps(value, ensure_ascii=False)


def _values_equal(a: Any, b: Any, tolerance: float) -> bool:
    """Compare scalars/lists with float tolerance where applicable."""
    if isinstance(a, (tuple, list)) and isinstance(b, (tuple, list)):
        if len(a) != len(b):
            return False
        return all(_values_equal(x, y, tolerance) for x, y in zip(a, b))
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=tolerance, abs_tol=tolerance)
    return a == b


def _compare_numeric_field(
    diffs: list[str],
    section: str,
    idx: int,
    field: str,
    original: Any,
    exported: Any,
    tolerance: float,
) -> None:
    """Compare one field and append a diff message when they do not match."""
    if "[" in section:
        path = f"{section}.{field}"
    else:
        path = f"{section}[{idx}].{field}"
    if _values_equal(original, exported, tolerance):
        return
    diffs.append(
        f"{path}: original={_format_value(original)} exported={_format_value(exported)}"
    )


def _compare_pmx_supported_content(
    original: Any,
    exported: Any,
    name: str,
    tolerance: float = 1e-5,
) -> tuple[list[str], list[str]]:
    """Compare exporter-supported PMX content between two PmxData objects."""
    from mmd_tools.core.pmx_data.morph import PmxMorphType

    diffs: list[str] = []
    compare_warnings: list[str] = []

    def compare_count(section: str, orig_items: Any, exp_items: Any) -> None:
        if len(orig_items) != len(exp_items):
            diffs.append(
                f"{section}.count: original={len(orig_items)} exported={len(exp_items)}"
            )

    # ------------------------------------------------------------------
    # header
    # ------------------------------------------------------------------
    if getattr(original, "header", None) is not None and getattr(exported, "header", None) is not None:
        if not _values_equal(
            _get_field(original.header, "model_name"),
            _get_field(exported.header, "model_name"),
            tolerance,
        ):
            diffs.append(
                f"header.model_name: original={_format_value(_get_field(original.header, 'model_name'))} "
                f"exported={_format_value(_get_field(exported.header, 'model_name'))}"
            )
        orig_name_en = _get_field(original.header, "model_name_english")
        exp_name_en = _get_field(exported.header, "model_name_english")
        if orig_name_en and not exp_name_en:
            compare_warnings.append(
                f"header.model_name_english not compared strictly for '{name}': "
                f"original='{orig_name_en}', exported is empty"
            )
        elif _values_equal(orig_name_en, exp_name_en, tolerance):
            pass
        else:
            diffs.append(
                f"header.model_name_english: original={_format_value(orig_name_en)} "
                f"exported={_format_value(exp_name_en)}"
            )

    # ------------------------------------------------------------------
    # vertices
    # ------------------------------------------------------------------
    compare_count("vertices", original.vertices, exported.vertices)
    for idx, (ov, ev) in enumerate(
        zip(original.vertices, exported.vertices)
    ):
        _compare_numeric_field(
            diffs,
            "vertices",
            idx,
            "position",
            _get_field(ov, "position"),
            _get_field(ev, "position"),
            tolerance,
        )
        _compare_numeric_field(
            diffs,
            "vertices",
            idx,
            "normal",
            _get_field(ov, "normal"),
            _get_field(ev, "normal"),
            tolerance,
        )
        _compare_numeric_field(
            diffs,
            "vertices",
            idx,
            "uv",
            _get_field(ov, "uv"),
            _get_field(ev, "uv"),
            tolerance,
        )
        _compare_numeric_field(
            diffs,
            "vertices",
            idx,
            "bone_indices",
            _get_field(ov, "bone_indices"),
            _get_field(ev, "bone_indices"),
            tolerance,
        )
        _compare_numeric_field(
            diffs,
            "vertices",
            idx,
            "bone_weights",
            _get_field(ov, "bone_weights"),
            _get_field(ev, "bone_weights"),
            tolerance,
        )
        _compare_numeric_field(
            diffs,
            "vertices",
            idx,
            "edge_magnification",
            _get_field(ov, "edge_magnification"),
            _get_field(ev, "edge_magnification"),
            tolerance,
        )

    # ------------------------------------------------------------------
    # faces
    # ------------------------------------------------------------------
    compare_count("faces", original.faces, exported.faces)
    for idx, (ov, ev) in enumerate(zip(original.faces, exported.faces)):
        _compare_numeric_field(
            diffs,
            "faces",
            idx,
            "indices",
            _get_field(ov, "indices"),
            _get_field(ev, "indices"),
            tolerance,
        )

    # ------------------------------------------------------------------
    # textures
    # ------------------------------------------------------------------
    compare_count("textures", original.textures, exported.textures)
    for idx, (ov, ev) in enumerate(zip(original.textures, exported.textures)):
        _compare_numeric_field(
            diffs,
            "textures",
            idx,
            "value",
            ov,
            ev,
            tolerance,
        )

    # ------------------------------------------------------------------
    # materials
    # ------------------------------------------------------------------
    compare_count("materials", original.materials, exported.materials)
    material_fields = [
        "name",
        "name_english",
        "diffuse",
        "specular",
        "specular_coefficient",
        "ambient",
        "draw_flag",
        "edge_color",
        "edge_size",
        "texture_index",
        "sphere_texture_index",
        "sphere_mode",
        "shared_toon_flag",
        "toon_texture_index",
        "memo",
        "face_count",
    ]
    for idx, (om, em) in enumerate(zip(original.materials, exported.materials)):
        for field in material_fields:
            _compare_numeric_field(
                diffs,
                "materials",
                idx,
                field,
                _get_field(om, field),
                _get_field(em, field),
                tolerance,
            )

    # ------------------------------------------------------------------
    # bones
    # ------------------------------------------------------------------
    compare_count("bones", original.bones, exported.bones)
    bone_fields = [
        "name",
        "name_english",
        "position",
        "parent_bone_index",
        "transform_layer",
        "bone_flag",
        "connect_position_offset",
        "connect_bone_index",
        "grant_parent_bone_index",
        "grant_rate",
        "axis_direction",
        "x_axis_direction",
        "z_axis_direction",
        "key_value",
        "ik_target_bone_index",
        "ik_loop_count",
        "ik_limit_angle",
    ]
    for idx, (ob, eb) in enumerate(zip(original.bones, exported.bones)):
        for field in bone_fields:
            if field == "bone_flag":
                original_flag = _get_field(ob, field)
                if hasattr(original_flag, "value"):
                    original_flag = int(original_flag)
                exported_flag = _get_field(eb, field)
                if hasattr(exported_flag, "value"):
                    exported_flag = int(exported_flag)
                _compare_numeric_field(
                    diffs,
                    "bones",
                    idx,
                    field,
                    original_flag,
                    exported_flag,
                    tolerance,
                )
            else:
                _compare_numeric_field(
                    diffs,
                    "bones",
                    idx,
                    field,
                    _get_field(ob, field),
                    _get_field(eb, field),
                    tolerance,
                )

    # ------------------------------------------------------------------
    # morphs
    # ------------------------------------------------------------------
    compare_count("morphs", original.morphs, exported.morphs)

    def normalize_morph_type(value: Any) -> str:
        if isinstance(value, str):
            return value.lower()
        morph_type_int = _get_field(value, "value", value)
        if morph_type_int == int(PmxMorphType.VertexMorph):
            return "vertex"
        if morph_type_int == int(PmxMorphType.BoneMorph):
            return "bone"
        if morph_type_int == int(PmxMorphType.MaterialMorph):
            return "material"
        return str(morph_type_int)

    for idx, (om, em) in enumerate(zip(original.morphs, exported.morphs)):
        orig_type = normalize_morph_type(_get_field(om, "morph_type"))
        exp_type = normalize_morph_type(_get_field(em, "morph_type"))
        for field in ("name", "name_english", "panel", "type"):
            _compare_numeric_field(
                diffs,
                "morphs",
                idx,
                field,
                _get_field(om, field) if field != "type" else orig_type,
                _get_field(em, field) if field != "type" else exp_type,
                tolerance,
            )
        if orig_type != exp_type:
            continue

        orig_offsets = list(_get_field(om, "offsets", []))
        exp_offsets = list(_get_field(em, "offsets", []))
        if len(orig_offsets) != len(exp_offsets):
            diffs.append(
                f"morphs[{idx}].offsets.count: original={len(orig_offsets)} exported={len(exp_offsets)}"
            )
        for off_idx, (ooff, eoff) in enumerate(zip(orig_offsets, exp_offsets)):
            if orig_type == "vertex":
                offset_fields = ("vertex_index", "position_offset")
            elif orig_type == "bone":
                offset_fields = (
                    "bone_index",
                    "translation",
                    "rotation",
                )
            elif orig_type == "material":
                offset_fields = (
                    "material_index",
                    "operation_type",
                    "diffuse",
                    "specular",
                    "specular_coefficient",
                    "ambient",
                    "edge_color",
                    "edge_size",
                    "texture_factor",
                    "sphere_texture_factor",
                    "toon_texture_factor",
                )
            else:
                offset_fields = ()
            for field in offset_fields:
                _compare_numeric_field(
                    diffs,
                    f"morphs[{idx}].offsets[{off_idx}]",
                    0,
                    field,
                    _get_field(ooff, field),
                    _get_field(eoff, field),
                    tolerance,
                )

    # ------------------------------------------------------------------
    # rigid bodies
    # ------------------------------------------------------------------
    compare_count("rigid_bodies", original.rigid_bodies, exported.rigid_bodies)
    rigid_body_fields = [
        "name",
        "name_english",
        "related_bone_index",
        "group",
        "collision_mask",
        "shape_type",
        "size",
        "position",
        "rotation",
        "mass",
        "velocity_attenuation",
        "rotation_attenuation",
        "elasticity",
        "friction",
        "physics_mode",
    ]
    for idx, (orb, erb) in enumerate(zip(original.rigid_bodies, exported.rigid_bodies)):
        for field in rigid_body_fields:
            _compare_numeric_field(
                diffs,
                "rigid_bodies",
                idx,
                field,
                _get_field(orb, field),
                _get_field(erb, field),
                tolerance,
            )

    # ------------------------------------------------------------------
    # joints
    # ------------------------------------------------------------------
    compare_count("joints", original.joints, exported.joints)
    joint_fields = [
        "name",
        "name_english",
        "joint_type",
        "rigid_body_a_index",
        "rigid_body_b_index",
        "position",
        "rotation",
        "translation_limit_min",
        "translation_limit_max",
        "rotation_limit_min",
        "rotation_limit_max",
        "spring_translation",
        "spring_rotation",
    ]
    for idx, (oj, ej) in enumerate(zip(original.joints, exported.joints)):
        for field in joint_fields:
            _compare_numeric_field(
                diffs,
                "joints",
                idx,
                field,
                _get_field(oj, field),
                _get_field(ej, field),
                tolerance,
            )

    # ------------------------------------------------------------------
    # display frames
    # ------------------------------------------------------------------
    compare_count("display_frames", original.display_frames, exported.display_frames)
    display_frame_fields = ["name", "name_english", "special_flag"]
    for idx, (odf, edf) in enumerate(zip(original.display_frames, exported.display_frames)):
        for field in display_frame_fields:
            _compare_numeric_field(
                diffs,
                "display_frames",
                idx,
                field,
                _get_field(odf, field),
                _get_field(edf, field),
                tolerance,
            )
        original_elements = _get_field(odf, "elements", []) or []
        exported_elements = _get_field(edf, "elements", []) or []
        compare_count(
            f"display_frames[{idx}].elements",
            original_elements,
            exported_elements,
        )
        for elem_idx, (original_element, exported_element) in enumerate(zip(original_elements, exported_elements)):
            for field in ("type", "index"):
                _compare_numeric_field(
                    diffs,
                    f"display_frames[{idx}].elements[{elem_idx}]",
                    0,
                    field,
                    _get_field(original_element, field),
                    _get_field(exported_element, field),
                    tolerance,
                )

    return diffs, compare_warnings


# ---------------------------------------------------------------------------
# Synthetic case builder
# ---------------------------------------------------------------------------


def _build_synthetic_supported_full_dict(name: str) -> dict[str, Any]:
    """Build a fully populated exporter dict for supported-section roundtrip."""
    from mmd_tools.core.pmx_data.bone import PmxBoneFlag

    bone_flag = int(
        PmxBoneFlag.DISPLAY
        | PmxBoneFlag.OPERATABLE
        | PmxBoneFlag.ROTATABLE
        | PmxBoneFlag.MOVABLE
    )

    return {
        "model_name": name,
        "model_name_english": f"{name}_en",
        "comment": "Synthesized supported roundtrip fixture",
        "vertices": [
            {
                "position": [0.0, 0.0, 0.0],
                "normal": [0.0, 1.0, 0.0],
                "uv": [0.0, 0.0],
                "bone_indices": [0],
            },
            {
                "position": [1.0, 0.0, 0.0],
                "normal": [0.0, 1.0, 0.0],
                "uv": [1.0, 0.0],
                "bone_indices": [1],
            },
            {
                "position": [0.0, 0.0, 1.0],
                "normal": [0.0, 1.0, 0.0],
                "uv": [0.0, 1.0],
                "bone_indices": [0],
            },
        ],
        "faces": [[0, 1, 2]],
        "materials": [
            {
                "name": "SupportedMaterial",
                "name_english": "SupportedMaterialEN",
                "diffuse": [0.9, 0.8, 0.7, 1.0],
                "specular": [0.2, 0.2, 0.2],
                "specular_coefficient": 15.0,
                "ambient": [0.1, 0.1, 0.1],
                "draw_flag": 0x01 | 0x02 | 0x10,
                "edge_color": [0.0, 0.0, 0.0, 1.0],
                "edge_size": 1.0,
                "texture_index": -1,
                "sphere_texture_index": -1,
                "sphere_mode": 0,
                "shared_toon_flag": 0,
                "toon_texture_index": -1,
                "memo": "synthetic supported fixture",
                "face_count": 3,
            }
        ],
        "bones": [
            {
                "name": "root",
                "name_english": "root_en",
                "position": [0.0, 0.0, 0.0],
                "bone_flag": bone_flag,
                "parent_index": -1,
                "connect_position_offset": [0.0, 0.0, 0.0],
            },
            {
                "name": "child",
                "name_english": "child_en",
                "position": [0.3, 0.0, 0.0],
                "bone_flag": bone_flag,
                "parent_index": 0,
                "connect_position_offset": [0.3, 0.0, 0.0],
            },
        ],
        "morphs": [
            {
                "type": "vertex",
                "name": "vertex_morph",
                "name_english": "vertex_morph_en",
                "panel": 4,
                "offsets": [
                    {
                        "vertex_index": 1,
                        "position_offset": [0.1, 0.0, 0.0],
                    }
                ],
            },
            {
                "type": "bone",
                "name": "bone_morph",
                "name_english": "bone_morph_en",
                "panel": 2,
                "offsets": [
                    {
                        "bone_index": 1,
                        "translation": [0.0, 0.2, 0.0],
                        "rotation": [0.0, 0.0, 0.0, 1.0],
                    }
                ],
            },
            {
                "type": "material",
                "name": "material_morph",
                "name_english": "material_morph_en",
                "panel": 3,
                "offsets": [
                    {
                        "material_index": 0,
                        "operation_type": 1,
                        "diffuse": [0.1, 0.2, 0.3, 0.4],
                        "specular": [0.1, 0.2, 0.3],
                        "specular_coefficient": 0.5,
                        "ambient": [0.2, 0.2, 0.2],
                        "edge_color": [0.0, 0.0, 0.0, 1.0],
                        "edge_size": 0.0,
                        "texture_factor": [0.8, 0.7, 0.6, 1.0],
                        "sphere_texture_factor": [0.0, 0.0, 0.0, 0.0],
                        "toon_texture_factor": [0.0, 0.0, 0.0, 1.0],
                    }
                ],
            },
        ],
        "rigid_bodies": [
            {
                "name": "rb_supported",
                "name_english": "rb_supported_en",
                "related_bone_index": 0,
                "group": 0,
                "collision_mask": 0xFFFF,
                "shape_type": 1,
                "size": [0.5, 0.5, 0.5],
                "position": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0],
                "mass": 1.0,
                "velocity_attenuation": 0.2,
                "rotation_attenuation": 0.3,
                "elasticity": 0.4,
                "friction": 0.5,
                "physics_mode": 0,
            }
        ],
        "joints": [
            {
                "name": "joint_supported",
                "name_english": "joint_supported_en",
                "joint_type": 0,
                "rigid_body_a_index": 0,
                "rigid_body_b_index": -1,
                "position": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0],
                "translation_limit_min": [0.0, 0.0, 0.0],
                "translation_limit_max": [0.0, 0.0, 0.0],
                "rotation_limit_min": [0.0, 0.0, 0.0],
                "rotation_limit_max": [0.0, 0.0, 0.0],
                "spring_translation": [0.0, 0.0, 0.0],
                "spring_rotation": [0.0, 0.0, 0.0],
            }
        ],
    }


def _build_synthetic_source(case: dict[str, Any], out_dir: Path) -> str:
    """Generate a temporary synthetic PMX source for supported-path verification."""
    synthetic_type = case.get("synthetic")
    if synthetic_type != "supported_full":
        raise ValueError(f"unsupported synthetic case type: {synthetic_type}")

    from mmd_tools.io.pmx_exporter import PmxExporter

    safe_name = _case_name(str(case["name"]))
    source_dir = out_dir / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    source_path = source_dir / f"{safe_name}.pmx"

    exporter = PmxExporter()
    exporter.export_pmx_model(
        str(source_path),
        _build_synthetic_supported_full_dict(case["name"]),
    )
    return str(source_path)


# ---------------------------------------------------------------------------
# Case runner
# ---------------------------------------------------------------------------


def run_case(
    case: dict[str, Any],
    out_dir: Path,
) -> dict[str, Any]:
    """Run one PMX roundtrip case and return a serializable result dict."""
    from maya import cmds
    from mmd_tools.core.mmd_parser import parse_pmx_file
    from mmd_tools.io.mmd_importer import import_mmd_file
    from mmd_tools.io.pmx_exporter import PmxExporter

    start = time.perf_counter()
    name = str(case["name"])
    model_path = str(case["model"])
    safe_name = _case_name(name)

    result: dict[str, Any] = {
        "name": name,
        "model": model_path,
        "status": None,
        "error": None,
        "traceback": None,
        "warnings": [],
        "diffs": [],
        "export_path": None,
        "import_original_ok": None,
        "import_roundtrip_ok": None,
        "elapsed_sec": None,
    }

    try:
        if case.get("synthetic"):
            model_path = _build_synthetic_source(case, out_dir)
            result["model"] = model_path

        if not Path(model_path).is_file():
            raise FileNotFoundError(f"model not found: {model_path}")

        exports_dir = out_dir / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        export_path = exports_dir / f"{safe_name}.pmx"
        result["export_path"] = str(export_path)

        # Step 1: Parse source PMX via the PMX-specific structured parser.
        src_pmx = parse_pmx_file(model_path)

        # Step 2: New Maya scene + import source
        cmds.file(new=True, force=True)
        src_root = import_mmd_file(
            model_path,
            options={
                "create_mmd_shaders": False,
                "import_physics": False,
            },
        )
        result["import_original_ok"] = bool(src_root)
        if not src_root:
            raise RuntimeError("source model import returned no root node")

        # Step 3: Convert PmxData → exporter dict
        warn_list: list[str] = []
        exporter_dict = _pmxdata_to_exporter_dict(src_pmx, warn_list)
        result["warnings"] = warn_list
        if not exporter_dict.get("vertices") or not exporter_dict.get("faces"):
            result["warnings"].append(
                "PMX has no exportable mesh vertices/faces; source import was checked, export/re-import skipped"
            )
            result["status"] = "skipped_unsupported"
            result["elapsed_sec"] = round(time.perf_counter() - start, 3)
            return result

        # Step 4: Export to new PMX file
        exporter = PmxExporter()
        exporter.export_pmx_model(str(export_path), exporter_dict)
        if not export_path.exists() or export_path.stat().st_size <= 0:
            raise RuntimeError(f"exported PMX not created or empty: {export_path}")

        # Step 5: Parse exported PMX to verify binary integrity
        exported_pmx = parse_pmx_file(str(export_path))

        # Step 6: Compare section counts
        diffs, compare_warnings = _compare_pmx_supported_content(
            src_pmx, exported_pmx, name
        )
        result["diffs"] = diffs
        if compare_warnings:
            result["warnings"].extend(compare_warnings)

        # Step 7: New Maya scene + import exported PMX
        cmds.file(new=True, force=True)
        exp_root = import_mmd_file(
            str(export_path),
            options={
                "create_mmd_shaders": False,
                "import_physics": False,
            },
        )
        result["import_roundtrip_ok"] = bool(exp_root)
        if not exp_root:
            raise RuntimeError("exported model import returned no root node")

        result["status"] = "passed" if not diffs else "passed_with_diffs"
        if diffs:
            result["status"] = "passed_with_diffs"

    except Exception as exc:  # noqa: BLE001
        result["status"] = "failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()

    result["elapsed_sec"] = round(time.perf_counter() - start, 3)
    return result


# ---------------------------------------------------------------------------
# Manifest runner
# ---------------------------------------------------------------------------


def run_manifest(
    manifest_path: str | Path,
    out_dir: str | Path,
    case_filter: str | None,
    limit: int | None,
    require_clean: bool,
) -> int:
    """Run roundtrip cases and write result JSON."""
    out_path = _require_build_path(out_dir, "--out-dir")
    out_path.mkdir(parents=True, exist_ok=True)
    cases = _load_cases(manifest_path)
    if case_filter:
        cases = [case for case in cases if case_filter.lower() in case["name"].lower()]
    if limit is not None:
        cases = cases[:limit]
    if not cases:
        raise ValueError("no cases selected")

    _initialize_maya()
    results = []
    for index, case in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] {case['name']}", flush=True)
        result = run_case(case, out_path)
        status_icon = {
            "passed": "PASS",
            "passed_with_diffs": "DIFF",
            "skipped_unsupported": "SKIP",
            "failed": "FAIL",
        }.get(result["status"], result["status"])
        print(f"  {status_icon} ({result['elapsed_sec']}s)", flush=True)
        if result.get("warnings"):
            for w in result["warnings"]:
                print(f"    warning: {w}", flush=True)
        if result.get("diffs"):
            for d in result["diffs"]:
                print(f"    diff: {d}", flush=True)
        if result.get("error"):
            print(f"    error: {result['error']}", flush=True)
        results.append(result)

    result_doc = {
        "manifest": str(Path(manifest_path).resolve()),
        "out_dir": str(out_path),
        "total": len(results),
        "passed": sum(1 for r in results if r["status"] == "passed"),
        "passed_with_diffs": sum(1 for r in results if r["status"] == "passed_with_diffs"),
        "skipped_unsupported": sum(1 for r in results if r["status"] == "skipped_unsupported"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "results": results,
    }
    result_file = out_path / "results.json"
    result_file.write_text(
        json.dumps(result_doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"results: {result_file}", flush=True)
    if require_clean:
        return 1 if (
            result_doc["failed"]
            or result_doc["passed_with_diffs"]
            or result_doc["skipped_unsupported"]
        ) else 0
    return 1 if result_doc["failed"] else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        default=None,
        help="Manifest JSON to run.",
    )
    parser.add_argument(
        "--case",
        default=None,
        help="Run only cases whose name contains this string.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit selected case count.",
    )
    parser.add_argument(
        "--out-dir",
        default="build/roundtrip",
        help="Result directory under build/ (default: build/roundtrip).",
    )
    parser.add_argument(
        "--require-clean",
        action="store_true",
        help="Fail unless all selected cases are passed.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = parse_args(argv)
    if not args.manifest:
        raise ValueError("--manifest is required")
    return run_manifest(
        args.manifest,
        args.out_dir,
        args.case,
        args.limit,
        args.require_clean,
    )


if __name__ == "__main__":
    raise SystemExit(main())
