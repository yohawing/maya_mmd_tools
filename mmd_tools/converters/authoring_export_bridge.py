"""Project immutable authoring semantics onto an existing export payload.

The scene collector remains the geometry and provenance oracle.  This bridge
only overlays semantic model, bone, material, and morph fields while retaining
writer-owned geometry, texture indices, display frames, physics, SDEF, and
other raw payload data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import copy
import math
from typing import Any

from mmd_tools.core.model_authoring_spec import MmdModelAuthoringSpec
from mmd_tools.core.pmx_data.bone import PmxBoneFlag
from mmd_tools.validation.export_validator import ExportValidationIssue, ExportValidationReport


class AuthoringExportIntegrationError(ValueError):
    """Raised when authoring semantics cannot be safely projected to an oracle."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "AUTHORING_SPEC_INVALID",
        path: str = "",
    ) -> None:
        super().__init__(message)
        self.report = ExportValidationReport(
            "pmx",
            (
                ExportValidationIssue(
                    code=code,
                    severity="error",
                    blocking=True,
                    path=path,
                    message=message,
                ),
            ),
        )


_MISSING = object()
def _fail(message: str) -> None:
    raise AuthoringExportIntegrationError(message)


def _require_spec(spec: Any) -> MmdModelAuthoringSpec:
    if not isinstance(spec, MmdModelAuthoringSpec):
        _fail("spec must be an MmdModelAuthoringSpec")
    return spec


def _require_mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{field} must be a mapping")
    return value


def _require_sequence(value: Any, *, field: str, allow_none: bool = False) -> Sequence[Any]:
    if value is None and allow_none:
        return ()
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        _fail(f"{field} must be a sequence")
    return value


def _require_index(value: Any, *, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(f"{field} must be an integer >= {minimum}")
    return value


def _require_contiguous_indices(items: Sequence[Any], *, field: str, key: str) -> dict[int, Mapping[str, Any]]:
    by_index: dict[int, Mapping[str, Any]] = {}
    for position, raw_item in enumerate(items):
        item = _require_mapping(raw_item, field=f"{field}[{position}]")
        raw_index = item.get(key, _MISSING)
        if raw_index is _MISSING:
            _fail(f"{field}[{position}] is missing {key}")
        index = _require_index(raw_index, field=f"{field}[{position}].{key}")
        if index in by_index:
            _fail(f"{field} contains duplicate {key} {index}")
        by_index[index] = item
    expected = set(range(len(items)))
    if set(by_index) != expected:
        _fail(f"{field} indices must be contiguous 0..{len(items) - 1}")
    return by_index


def _require_material_indices(
    items: Sequence[Any],
    *,
    field: str,
    material_count: int,
) -> dict[int, Mapping[str, Any]]:
    """Validate sparse material provenance against the semantic index range.

    Maya's standard shading-engine membership can leave leading or middle PMX
    materials without any faces.  The collector therefore emits only the
    materials it observes.  Every observed source index must still be an
    explicit, unique integer in the authoring range; absent indices are filled
    by the caller with zero-face placeholders.
    """
    by_index: dict[int, Mapping[str, Any]] = {}
    for position, raw_item in enumerate(items):
        item = _require_mapping(raw_item, field=f"{field}[{position}]")
        raw_index = item.get("source_material_index", _MISSING)
        if raw_index is _MISSING:
            _fail(f"{field}[{position}] is missing source_material_index")
        index = _require_index(raw_index, field=f"{field}[{position}].source_material_index")
        if index >= material_count:
            _fail(
                f"{field}[{position}].source_material_index {index} is out of range "
                f"for {material_count} semantic materials"
            )
        if index in by_index:
            _fail(f"{field} contains duplicate source_material_index {index}")
        by_index[index] = item
    return by_index


def _require_morph_indices(items: Sequence[Any]) -> dict[int, Mapping[str, Any]]:
    """Use collector list order when its validated payload omits explicit indices."""
    by_index: dict[int, Mapping[str, Any]] = {}
    for position, raw_item in enumerate(items):
        item = _require_mapping(raw_item, field=f"oracle.morphs[{position}]")
        if "index" in item:
            index = _require_index(item["index"], field=f"oracle.morphs[{position}].index")
            if index != position:
                _fail(f"oracle.morphs[{position}].index must equal collection position {position}")
        else:
            index = position
        by_index[index] = item
    return by_index


def _require_spec_indices(spec: MmdModelAuthoringSpec) -> None:
    for field, items in (
        ("bones", spec.bones),
        ("materials", spec.materials),
        ("morphs", spec.morphs),
    ):
        indices = [item.index for item in items]
        if indices != list(range(len(items))):
            _fail(f"spec.{field} indices must be contiguous 0..{len(items) - 1}")


def _copy_vector(value: Sequence[float]) -> list[float]:
    return [float(component) for component in value]


def _overlay_bone(oracle: Mapping[str, Any], bone: Any) -> dict[str, Any]:
    result = dict(oracle)
    result.update(
        {
            "name": bone.name,
            "name_english": bone.name_english,
            "parent_index": bone.parent_index,
            "position": _copy_vector(bone.rest_position),
            "transform_layer": bone.transform_layer,
            "bone_flag": bone.flags,
            "connect_bone_index": bone.connect_bone_index if bone.connect_bone_index is not None else -1,
            "connect_position_offset": _copy_vector(bone.tail_offset or (0.0, 0.0, 0.0)),
            "grant_parent_bone_index": bone.grant_parent_index if bone.grant_parent_index is not None else -1,
            "grant_rate": bone.grant_ratio,
            "axis_direction": _copy_vector(bone.fixed_axis or (0.0, 0.0, 0.0)),
            "x_axis_direction": _copy_vector(bone.local_axis_x or (1.0, 0.0, 0.0)),
            "z_axis_direction": _copy_vector(bone.local_axis_z or (0.0, 0.0, 1.0)),
            "key_value": bone.external_parent_key if bone.external_parent_key is not None else 0,
        }
    )
    # Conditional PMX payload must not be emitted as zero/default metadata
    # when its corresponding flag is inactive.  The exporter validator treats
    # fields such as ``ik_loop_count=0`` as authored IK data and correctly
    # rejects it without the IK flag.  Start from the oracle (which may carry
    # collector provenance) and only overlay fields enabled by the semantic
    # flag word.
    conditional = {
        "connect_bone_index": bool(bone.flags & PmxBoneFlag.CONNECT_BONE),
        # Relative tail offsets are semantic for offset-tail bones as well as
        # connected-tail bones; keep this writer field unconditionally.
        "connect_position_offset": True,
        "grant_parent_bone_index": bool(
            bone.flags & (PmxBoneFlag.GRANT_PARENT_ROTATE | PmxBoneFlag.GRANT_PARENT_MOVE)
        ),
        "grant_rate": bool(bone.flags & (PmxBoneFlag.GRANT_PARENT_ROTATE | PmxBoneFlag.GRANT_PARENT_MOVE)),
        "axis_direction": bool(bone.flags & PmxBoneFlag.AXIS_FIXED),
        "x_axis_direction": bool(bone.flags & PmxBoneFlag.LOCAL_AXIS),
        "z_axis_direction": bool(bone.flags & PmxBoneFlag.LOCAL_AXIS),
        "key_value": bool(bone.flags & PmxBoneFlag.EXTERNAL_PARENT_DEFORM),
    }
    for key, enabled in conditional.items():
        if not enabled:
            result.pop(key, None)
    if bone.flags & PmxBoneFlag.IK:
        result.update(
            {
                "ik_target_bone_index": bone.ik_target_index,
                "ik_loop_count": bone.ik_loop_count,
                "ik_limit_angle": bone.ik_limit_radian,
                "ik_links": [dict(link) for link in bone.ik_links],
            }
        )
    else:
        for key in ("ik_target_bone_index", "ik_loop_count", "ik_limit_angle", "ik_links"):
            result.pop(key, None)
    return result


def _overlay_material(oracle: Mapping[str, Any], material: Any) -> dict[str, Any]:
    result = dict(oracle)
    # Texture indices, source_material_index, and face_count intentionally stay
    # oracle-owned provenance.  The writer consumes these exact snake_case keys.
    result.update(
        {
            "name": material.name,
            "name_english": material.name_english,
            "diffuse": _copy_vector(material.diffuse),
            "specular": _copy_vector(material.specular),
            "specular_coefficient": material.specular_coefficient,
            "ambient": _copy_vector(material.ambient),
            "draw_flag": material.draw_flags,
            "edge_color": _copy_vector(material.edge_color),
            "edge_size": material.edge_size,
            "sphere_mode": material.sphere_mode,
            "shared_toon_flag": 1 if material.shared_toon else 0,
            "memo": material.memo,
        }
    )
    # The collector may have recorded stale ``semantic_missing`` provenance
    # for fields that were absent when the scene was read.  Every semantic
    # material field is replaced from the immutable authoring spec here, and
    # texture indices/table entries are rebuilt by ``_project_material_texture_fields``.
    # Retaining that stale marker would make the validator reject an otherwise
    # complete authoring projection.
    result.pop("semantic_missing", None)
    return result


def _require_texture_table(value: Any) -> list[str]:
    if value is None:
        return []
    values = _require_sequence(value, field="oracle.textures")
    if not all(isinstance(path, str) for path in values):
        _fail("oracle.textures must contain only strings")
    return list(values)


def _project_material_texture_fields(
    materials: Sequence[Any],
    projected: list[dict[str, Any]],
    oracle_textures: Any,
) -> list[str]:
    """Resolve semantic source paths against a stable, append-only texture table."""
    texture_table = _require_texture_table(oracle_textures)
    first_index_by_path: dict[str, int] = {}
    for index, path in enumerate(texture_table):
        first_index_by_path.setdefault(path, index)

    def resolve(path: str | None) -> int:
        if not path:
            return -1
        if path in first_index_by_path:
            return first_index_by_path[path]
        index = len(texture_table)
        texture_table.append(path)
        first_index_by_path[path] = index
        return index

    for material, output in zip(materials, projected):
        output["texture_path"] = material.texture_path
        output["texture_index"] = resolve(material.texture_path)
        output["sphere_texture_path"] = material.sphere_texture_path
        output["sphere_texture_index"] = resolve(material.sphere_texture_path)
        output["toon_texture_path"] = material.toon_texture_path
        if material.shared_toon:
            shared_index = material.toon_texture_index
            if (
                isinstance(shared_index, bool)
                or not isinstance(shared_index, int)
                or not 0 <= shared_index <= 9
            ):
                _fail("shared toon materials require a toon_texture_index between 0 and 9")
            output["shared_toon_flag"] = 1
            output["toon_texture_index"] = shared_index
        else:
            output["shared_toon_flag"] = 0
            output["toon_texture_index"] = resolve(material.toon_texture_path)
    return texture_table


def _overlay_morph(oracle: Mapping[str, Any], morph: Any) -> dict[str, Any]:
    result = dict(oracle)
    if morph.morph_type == "vertex":
        # blendShape inputTarget data is the only vertex-offset authority.
        # The immutable spec still carries an offsets-shaped writer payload,
        # but that value is intentionally ignored here.
        oracle_offsets = oracle.get("offsets", _MISSING)
        if oracle_offsets is _MISSING:
            _fail("vertex morph oracle is missing blendShape offsets")
        oracle_offsets = _require_sequence(oracle_offsets, field="oracle.vertex.offsets")
        result_offsets = []
        for offset_index, raw_offset in enumerate(oracle_offsets):
            field = f"oracle.vertex.offsets[{offset_index}]"
            offset = _require_mapping(raw_offset, field=field)
            _require_index(offset.get("vertex_index", _MISSING), field=f"{field}.vertex_index")
            position = offset.get("position_offset", _MISSING)
            if isinstance(position, (str, bytes, bytearray)) or not isinstance(position, Sequence) or len(position) != 3:
                _fail(f"{field}.position_offset must contain exactly three numbers")
            if any(
                isinstance(component, bool)
                or not isinstance(component, (int, float))
                or not math.isfinite(component)
                for component in position
            ):
                _fail(f"{field}.position_offset must contain only finite numbers")
            result_offsets.append(dict(offset))
    else:
        result_offsets = [dict(offset) for offset in morph.offsets]
    result.update(
        {
            "type": morph.morph_type,
            "name": morph.name,
            "name_english": morph.name_english,
            "panel": morph.panel,
            "offsets": result_offsets,
        }
    )
    return result


def project_authoring_spec(
    spec: MmdModelAuthoringSpec,
    oracle_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Project *spec* onto a fresh writer payload sourced from *oracle_payload*.

    The oracle owns geometry and provenance.  Semantic collections are paired
    by explicit contiguous PMX index and overlaid in canonical index order.
    """
    spec = _require_spec(spec)
    oracle = _require_mapping(oracle_payload, field="oracle_payload")
    _require_spec_indices(spec)
    try:
        result = copy.deepcopy(dict(oracle))
    except (TypeError, ValueError) as exc:
        raise AuthoringExportIntegrationError("oracle_payload must be deepcopyable") from exc

    oracle_bones = _require_sequence(result.get("bones"), field="oracle.bones", allow_none=True)
    oracle_materials = _require_sequence(result.get("materials"), field="oracle.materials")
    oracle_morphs = _require_sequence(result.get("morphs"), field="oracle.morphs", allow_none=True)
    if len(oracle_bones) != len(spec.bones):
        _fail(f"bone count mismatch: spec={len(spec.bones)} oracle={len(oracle_bones)}")
    if len(oracle_materials) > len(spec.materials):
        _fail(f"material count mismatch: spec={len(spec.materials)} oracle={len(oracle_materials)}")
    if len(oracle_morphs) != len(spec.morphs):
        _fail(f"morph count mismatch: spec={len(spec.morphs)} oracle={len(oracle_morphs)}")

    material_by_index = _require_material_indices(
        oracle_materials,
        field="oracle.materials",
        material_count=len(spec.materials),
    )
    # A registry-owned but currently unassigned material has no collector face
    # entry.  Keep it exportable by supplying a writer-safe zero-face
    # provenance placeholder; semantic fields are overlaid below.  This also
    # covers leading and middle holes, not only the historical tail case.
    for index in range(len(spec.materials)):
        if index in material_by_index:
            continue
        material_by_index[index] = {
            "source_material_index": index,
            "face_count": 0,
            "texture_index": -1,
            "sphere_texture_index": -1,
            "toon_texture_index": -1,
        }
    morph_by_index = _require_morph_indices(oracle_morphs) if oracle_morphs else {}

    for index, (bone, oracle_bone_raw) in enumerate(zip(spec.bones, oracle_bones)):
        oracle_bone = _require_mapping(oracle_bone_raw, field=f"oracle.bones[{index}]")
        source_joint = oracle_bone.get("source_joint", _MISSING)
        if bone.binding_identity is None:
            if source_joint not in (_MISSING, None):
                _fail(f"bones[{index}] binding_identity does not match oracle source_joint")
        elif source_joint is _MISSING or source_joint != bone.binding_identity:
            _fail(f"bones[{index}] binding_identity does not match oracle source_joint")

    result["model_name"] = spec.model.name
    result["model_name_english"] = spec.model.name_english
    result["comment"] = spec.model.comment
    result["comment_english"] = spec.model.comment_english
    result["bones"] = [_overlay_bone(oracle_bones[index], spec.bones[index]) for index in range(len(spec.bones))]
    result["materials"] = [
        _overlay_material(material_by_index[index], spec.materials[index])
        for index in range(len(spec.materials))
    ]
    projected_morphs: list[dict[str, Any]] = []
    for index, morph in enumerate(spec.morphs):
        oracle_morph = morph_by_index[index]
        if oracle_morph.get("type", _MISSING) != morph.morph_type:
            _fail(f"oracle.morphs[{index}].type does not match spec morph type")
        projected_morphs.append(_overlay_morph(oracle_morph, morph))
    result["morphs"] = projected_morphs
    result["textures"] = _project_material_texture_fields(
        spec.materials,
        result["materials"],
        result.get("textures"),
    )
    return result


__all__ = ["AuthoringExportIntegrationError", "project_authoring_spec"]
