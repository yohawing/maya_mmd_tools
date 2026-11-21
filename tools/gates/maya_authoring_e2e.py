"""Run the product model-authoring round-trip gate inside Maya.

The callable in this module is intentionally a composition-level integration
boundary.  It performs one real template creation, exercises the coordinator
operations, exports a PMX, imports that PMX into a fresh scene, and compares
the strict semantic specification before and after the round trip.  Maya node
identities are normalized out of the comparison; semantic names, indices,
offsets, and the resulting deterministic fingerprint remain authoritative.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import copy
import os
from pathlib import Path
import time
from typing import Any

from mmd_tools.actions.export_model_action import ExportModelAction, ExportModelRequest
from mmd_tools.core.model_authoring_spec import (
    MmdBoneSpec,
    MmdMaterialSpec,
    MmdModelAuthoringSpec,
    MmdMorphSpec,
)
from mmd_tools.core.morph_authoring import MorphReindexResult
from mmd_tools.validation.snapshot import fingerprint_payload


REQUIRED_OPERATIONS = (
    "material.create",
    "material.edit",
    "material.reindex",
    # The operation name is retained for gate/report compatibility; the
    # implementation below is Maya-standard ``sets(forceElement=...)`` rather
    # than a product Assign route.
    "material.assign",
    "material.delete",
    "bone.register",
    "bone.capture_rest",
    "bone.reindex",
    "bone.unregister",
    "morph.create",
    "morph.edit",
    "morph.reindex",
    "export.pmx",
    "import.fresh_scene",
    "spec.read",
)


def _material_morph_offset(material_index: int) -> dict[str, Any]:
    """Return one complete Material Morph offset for transaction probes."""
    return {
        "material_index": material_index,
        "operation_type": 0,
        "diffuse": [1.0, 1.0, 1.0, 1.0],
        "specular": [0.0, 0.0, 0.0],
        "specular_coefficient": 0.0,
        "ambient": [0.0, 0.0, 0.0],
        "edge_color": [0.0, 0.0, 0.0, 1.0],
        "edge_size": 1.0,
        "texture_factor": [1.0, 1.0, 1.0, 1.0],
        "sphere_texture_factor": [1.0, 1.0, 1.0, 1.0],
        "toon_texture_factor": [1.0, 1.0, 1.0, 1.0],
    }


def writer_not_called_case(output_path: str | Path | None = None) -> dict[str, Any]:
    """Prove blocking PMX preflight dispatches no writer and preserves output.

    This host-independent negative case intentionally executes the same
    :class:`ExportModelAction` used by the authoring E2E path.  A recorder is
    injected as the PMX writer, while a Flip morph makes validation blocking.
    The target is retained only as an unchanged sentinel if it already exists.
    """
    if output_path is None:
        root = Path(__file__).resolve().parents[2] / "build" / "temp"
        output = root / f"authoring-negative-{os.getpid()}.pmx"
    else:
        output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    before_exists = output.is_file()
    before_bytes = output.read_bytes() if before_exists else None

    class _Recorder:
        def __init__(self) -> None:
            self.calls: list[tuple[str, Mapping[str, Any]]] = []

        def export_pmx_model(self, path: str, payload: Mapping[str, Any]) -> None:
            self.calls.append((path, payload))

    payload = {
        "model_name": "authoring-negative",
        "vertices": [
            {
                "position": [0.0, 0.0, 0.0],
                "normal": [0.0, 0.0, 1.0],
                "uv": [0.0, 0.0],
                "bone_indices": [0],
            }
        ],
        "faces": [[0, 0, 0]],
        "materials": [{"name": "mat", "diffuse": [1.0, 1.0, 1.0, 1.0], "face_count": 3}],
        "bones": None,
        "morphs": [{"type": "flip", "offsets": []}],
    }
    recorder = _Recorder()
    result = ExportModelAction(pmx_exporter=recorder, collector=None).execute(
        ExportModelRequest(
            file_path=str(output),
            options={"export_format": "pmx", "model_data": payload},
        )
    )
    after_exists = output.is_file()
    after_bytes = output.read_bytes() if after_exists else None
    blocking = bool(result.validation_report and result.validation_report.is_blocking)
    unchanged = before_exists == after_exists and before_bytes == after_bytes
    passed = not result.succeeded and blocking and not recorder.calls and unchanged
    return {
        "name": "writer_not_called",
        "status": "pass" if passed else "fail",
        "writer_calls": len(recorder.calls),
        "result_succeeded": bool(result.succeeded),
        "blocking": blocking,
        "target_unchanged": unchanged,
        "reason": None
        if passed
        else "blocking PMX preflight did not preserve the target or dispatch-free writer contract",
    }


class MayaAuthoringE2EError(RuntimeError):
    """Raised when an E2E operation did not complete genuinely."""


def _require_string(value: Any, *, field: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise MayaAuthoringE2EError(f"{field} must be a non-empty string")
    return value


def normalize_spec_payload(spec: MmdModelAuthoringSpec | Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached semantic payload with Maya bindings normalized.

    Fresh Maya imports necessarily receive different DAG/DG identities.  The
    identities are replaced by stable collection/index tokens before the
    fingerprint is calculated, so the fingerprint and field comparison use
    exactly the same normalized payload.
    """
    if isinstance(spec, MmdModelAuthoringSpec):
        payload = spec.to_mapping()
    elif isinstance(spec, Mapping):
        payload = copy.deepcopy(dict(spec))
    else:
        raise TypeError("spec must be an MmdModelAuthoringSpec or mapping")
    expected = {"schema_version", "model", "bones", "materials", "morphs"}
    if set(payload) != expected:
        raise MayaAuthoringE2EError(
            f"strict spec payload has unexpected fields: {sorted(set(payload) ^ expected)!r}"
        )

    def _quantize(value: Any) -> Any:
        if isinstance(value, bool) or isinstance(value, int) or value is None or isinstance(value, str):
            return value
        if isinstance(value, float):
            return round(value, 6)
        if isinstance(value, list):
            return [_quantize(item) for item in value]
        if isinstance(value, dict):
            return {key: _quantize(item) for key, item in value.items()}
        raise MayaAuthoringE2EError(f"strict spec contains unsupported value: {value!r}")

    payload = _quantize(payload)
    for section in ("bones", "materials", "morphs"):
        items = payload.get(section)
        if isinstance(items, (str, bytes, bytearray)) or not isinstance(items, list):
            raise MayaAuthoringE2EError(f"spec.{section} must be a list")
        for item in items:
            if not isinstance(item, dict) or "index" not in item:
                raise MayaAuthoringE2EError(f"spec.{section} contains malformed item")
            item["binding_identity"] = f"{section}:{item['index']}"
    payload["fingerprint"] = fingerprint_payload(payload)
    return payload


def _omit_unassigned_material(payload: dict[str, Any], index: int) -> dict[str, Any]:
    """Normalize fresh-import comparison when a writer omits zero-face slots.

    The PMX export contract permits a registry-owned material with no faces;
    some import paths do not recreate such an empty slot.  Keep the export
    evidence in the operation report while comparing the shared semantic
    subset that both scenes can represent.
    """
    materials = payload.get("materials")
    if isinstance(materials, list):
        payload["materials"] = [item for item in materials if item.get("index") != index]
    payload.pop("fingerprint", None)
    payload["fingerprint"] = fingerprint_payload(payload)
    return payload


def _operation(name: str, *, details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"name": name, "status": "pass"}
    if details:
        result.update(dict(details))
    return result


def _safe_output_path(asset_paths: Mapping[str, str] | None) -> Path:
    """Resolve output only under the repository build directory."""
    root = Path(__file__).resolve().parents[2]
    build_root = (root / "build").resolve()
    configured = (asset_paths or {}).get("output")
    path = Path(configured) if configured else build_root / "reports" / "model-authoring-e2e.pmx"
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    if resolved == build_root or build_root not in resolved.parents:
        raise MayaAuthoringE2EError(f"output must resolve under {build_root}: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _mesh_transform(cmds_adapter: Any, root: str) -> str:
    shapes = cmds_adapter.list_relatives(
        root, allDescendents=True, fullPath=True, type="mesh"
    ) or []
    if isinstance(shapes, (str, bytes, bytearray)):
        raise MayaAuthoringE2EError(f"mesh discovery returned a scalar: {shapes!r}")
    # Maya construction history can expose an intermediate ``Orig`` shape
    # beside the renderable shape.  It is not an export target and must not be
    # treated as a second mesh fixture.
    renderable = [
        shape
        for shape in shapes
        if isinstance(shape, str) and not shape.rsplit("|", 1)[-1].endswith("Orig")
    ]
    if len(renderable) != 1:
        raise MayaAuthoringE2EError(
            f"template must contain exactly one renderable mesh shape below {root!r}; got {shapes!r}"
        )
    parents = cmds_adapter.list_relatives(renderable[0], parent=True, fullPath=True) or []
    if len(parents) != 1 or not isinstance(parents[0], str) or not parents[0].startswith("|"):
        raise MayaAuthoringE2EError(f"mesh shape has no unique canonical transform: {renderable[0]!r}")
    return parents[0]


def _canonical_joint(cmds_adapter: Any, created: Any) -> str:
    values = created if isinstance(created, (list, tuple)) else [created]
    if len(values) != 1 or not isinstance(values[0], str):
        raise MayaAuthoringE2EError(f"joint creation returned an invalid identity: {created!r}")
    paths = cmds_adapter.ls(values[0], long=True) or []
    if len(paths) != 1 or not isinstance(paths[0], str) or not paths[0].startswith("|"):
        raise MayaAuthoringE2EError(f"joint identity is not canonical: {created!r}")
    return paths[0]


def _edit_material(
    coordinator: Any,
    cmds_adapter: Any,
    material_authoring: Any,
    root: str,
    material_index: int,
) -> tuple[MmdModelAuthoringSpec, float]:
    """Perform a real semantic material edit through the coordinator boundary."""
    current = coordinator.read_spec(root)
    old = next((item for item in current.materials if item.index == material_index), None)
    if not isinstance(old, MmdMaterialSpec):
        raise MayaAuthoringE2EError(f"material index {material_index} was not created")

    value_patch = getattr(coordinator, "apply_material_value_patch", None)
    read_material_value = getattr(coordinator, "read_material_value", None)
    if not callable(value_patch) or not callable(read_material_value):
        raise MayaAuthoringE2EError("material edit requires the selected-material value patch API")
    value_edited = replace(
        old,
        name=f"{old.name} (edited)",
        name_english=f"{old.name_english} edited",
        diffuse=(0.72, 0.48, 0.36, 0.85),
        specular_coefficient=18.0,
        draw_flags=old.draw_flags ^ 0x10,
    )
    patch_started = time.perf_counter()
    patched = value_patch(root, value_edited)
    patch_elapsed_ms = (time.perf_counter() - patch_started) * 1000.0
    if not isinstance(patched, MmdMaterialSpec) or patched != value_edited:
        raise MayaAuthoringE2EError("material value patch returned an invalid material")
    selected = read_material_value(root, material_index, old.binding_identity)
    if selected != value_edited:
        raise MayaAuthoringE2EError("material value patch did not persist selected shader values")
    undo = getattr(cmds_adapter, "undo", None)
    redo = getattr(cmds_adapter, "redo", None)
    if not callable(undo) or not callable(redo):
        raise MayaAuthoringE2EError("material value patch E2E requires Maya undo and redo")
    undo()
    if read_material_value(root, material_index, old.binding_identity) != old:
        raise MayaAuthoringE2EError("material value patch undo did not restore the selected shader")
    redo()
    if read_material_value(root, material_index, old.binding_identity) != value_edited:
        raise MayaAuthoringE2EError("material value patch redo did not restore the edited shader")

    binding_patch = getattr(coordinator, "apply_material_binding_patch", None)
    if not callable(binding_patch):
        raise MayaAuthoringE2EError("material edit requires the selected-material binding patch API")
    edited = replace(
        value_edited,
        resolved_texture_path=(value_edited.resolved_texture_path or "C:/mmd_e2e_texture.png")
        + ".edited",
        sphere_texture_path="textures/e2e_sphere.spa",
        toon_texture_path="textures/e2e_toon.bmp",
    )
    target = replace(
        current,
        materials=tuple(edited if item.index == material_index else item for item in current.materials),
    )
    result = binding_patch(root, edited)
    if not isinstance(result, MmdMaterialSpec) or result != edited:
        raise MayaAuthoringE2EError("material binding patch returned an invalid material")
    if read_material_value(root, material_index, old.binding_identity) != edited:
        raise MayaAuthoringE2EError("material binding patch did not persist the selected shader")
    undo()
    if read_material_value(root, material_index, old.binding_identity) != value_edited:
        raise MayaAuthoringE2EError("material binding patch undo did not restore the selected shader")
    redo()
    if read_material_value(root, material_index, old.binding_identity) != edited:
        raise MayaAuthoringE2EError("material binding patch redo did not restore the edited shader")
    observed = coordinator.read_spec(root)
    if observed.materials != target.materials:
        raise MayaAuthoringE2EError("material edit did not persist semantic values")
    return observed, patch_elapsed_ms


def _assign_material_with_standard_maya_sets(
    cmds_adapter: Any,
    material_authoring: Any,
    root: str,
    mesh: str,
    material: MmdMaterialSpec,
) -> None:
    """Assign a shader through Maya's normal shadingEngine membership API."""
    resolver = getattr(material_authoring, "resolve_material", None)
    if not callable(resolver):
        raise MayaAuthoringE2EError("material standard assignment requires resolve_material")
    binding = resolver(root, material)
    if (
        not isinstance(binding, tuple)
        or len(binding) != 2
        or not all(isinstance(item, str) and item for item in binding)
    ):
        raise MayaAuthoringE2EError("material binding resolver returned an invalid shader/SG pair")
    _shader, shading_group = binding
    sets = getattr(cmds_adapter, "sets", None)
    if not callable(sets):
        raise MayaAuthoringE2EError("Maya standard material assignment requires cmds.sets")
    sets(mesh, e=True, forceElement=shading_group)


def _require_exported_unassigned_material(parser: Any, material: MmdMaterialSpec) -> dict[str, Any]:
    """Validate one zero-face material slot from the parsed PMX payload."""
    parsed_materials = getattr(parser, "materials", None)
    if (
        isinstance(parsed_materials, (str, bytes, bytearray))
        or not isinstance(parsed_materials, (list, tuple))
        or len(parsed_materials) <= material.index
    ):
        raise MayaAuthoringE2EError("PMX parse did not retain the unassigned material slot")
    parsed = parsed_materials[material.index]
    parsed_index = getattr(parsed, "material_index", material.index)
    parsed_name = getattr(parsed, "name", None)
    parsed_name_english = getattr(parsed, "name_english", None)
    parsed_face_count = getattr(parsed, "face_count", None)
    if parsed_index != material.index:
        raise MayaAuthoringE2EError(
            "PMX parse reordered the unassigned material slot: "
            f"expected index {material.index}, got {parsed_index!r}"
        )
    if parsed_name != material.name or parsed_name_english != material.name_english:
        raise MayaAuthoringE2EError("PMX parse changed the unassigned material slot name/order")
    if parsed_face_count != 0:
        raise MayaAuthoringE2EError(
            "PMX parse did not preserve zero-face provenance for the unassigned material"
        )
    return {
        "index": parsed_index,
        "name": parsed_name,
        "name_english": parsed_name_english,
        "face_count": parsed_face_count,
    }


def _negative_cases() -> list[dict[str, Any]]:
    """Run the two host-independent negative policy checks required by the gate."""
    from mmd_tools.validation.export_validator import validate_model_data

    base = {
        "model_name": "authoring-negative",
        "vertices": [{"position": [0.0, 0.0, 0.0], "bone_indices": [0]}],
        "faces": [[0, 0, 0]],
        "materials": [{"name": "mat", "face_count": 3}],
        "bones": None,
    }
    flip_report = validate_model_data(
        {**base, "morphs": [{"type": "flip", "offsets": []}]}, "pmx"
    )
    impulse_report = validate_model_data(
        {**base, "morphs": [{"type": "impulse", "offsets": []}]}, "pmx"
    )
    unsupported = flip_report.is_blocking and impulse_report.is_blocking
    return [
        writer_not_called_case(),
        {
            "name": "unsupported_flip_impulse_reject",
            "status": "pass" if unsupported else "fail",
            "blocked_types": ["flip", "impulse"] if unsupported else [],
        },
    ]


def run_authoring_e2e(
    *,
    initializer: Any,
    template_id: str,
    model_name: str,
    model_name_english: str = "",
    asset_paths: Mapping[str, str] | None = None,
    coordinator: Any,
    metadata_adapter: Any,
    cmds_adapter: Any,
    material_authoring: Any,
    export_action: ExportModelAction | None = None,
    pmx_parser: Any | None = None,
    pmx_importer: Any | None = None,
) -> dict[str, Any]:
    """Run the complete authoring CRUD/export/fresh-import sequence.

    All dependencies are injected by the production composition.  This keeps
    the function deterministic and straightforward to unit-test without
    importing Maya in ordinary Python.
    """
    _require_string(template_id, field="template_id")
    _require_string(model_name, field="model_name")
    _require_string(model_name_english, field="model_name_english", allow_empty=True)
    if not isinstance(asset_paths, (Mapping, type(None))):
        raise MayaAuthoringE2EError("asset_paths must be a mapping or None")

    created = initializer.create(template_id, model_name, model_name_english)
    root = getattr(created, "root", None)
    if not isinstance(root, str) or not root.startswith("|"):
        raise MayaAuthoringE2EError("initializer did not return a canonical model root")
    mesh = _mesh_transform(cmds_adapter, root)
    operations: list[dict[str, Any]] = []

    # Material CRUD -----------------------------------------------------
    material_create_started = time.perf_counter()
    created_material = coordinator.create_material(root)
    material_create_elapsed_ms = (time.perf_counter() - material_create_started) * 1000.0
    if not isinstance(created_material, MmdMaterialSpec):
        raise MayaAuthoringE2EError("material.create did not return a material")
    if created_material.index == 0 or not created_material.binding_identity:
        raise MayaAuthoringE2EError("material.create did not create a bound material")
    cmds_adapter.undo()
    try:
        coordinator.read_material_value(
            root, created_material.index, created_material.binding_identity
        )
    except Exception:
        pass
    else:
        raise MayaAuthoringE2EError("material.create undo did not remove the new binding")
    cmds_adapter.redo()
    if coordinator.read_material_value(
        root, created_material.index, created_material.binding_identity
    ) != created_material:
        raise MayaAuthoringE2EError("material.create redo did not restore the new binding")
    operations.append(
        _operation(
            "material.create",
            details={
                "index": created_material.index,
                "elapsed_ms": material_create_elapsed_ms,
                "undo_redo_verified": True,
            },
        )
    )
    current, value_patch_elapsed_ms = _edit_material(
        coordinator,
        cmds_adapter,
        material_authoring,
        root,
        created_material.index,
    )
    operations.append(
        _operation(
            "material.edit",
            details={
                "index": created_material.index,
                "value_patch_elapsed_ms": value_patch_elapsed_ms,
                "routes": ["selected_shader_value_patch", "selected_shader_binding_patch"],
                "undo_redo_verified": True,
            },
        )
    )
    original_material_index = created_material.index
    reindex_probe = coordinator.create_morph(
        root,
        MmdMorphSpec(
            name="Material Reindex Probe",
            name_english="Material Reindex Probe",
            panel=4,
            morph_type="material",
        ),
    )
    coordinator.replace_morph_offsets(
        root,
        reindex_probe.index,
        (_material_morph_offset(original_material_index),),
    )

    reindex_started = time.perf_counter()
    reordered = coordinator.move_material_fast(root, original_material_index, 0)
    reindex_elapsed_ms = (time.perf_counter() - reindex_started) * 1000.0
    if (reordered.first_index, reordered.second_index) != (0, original_material_index):
        raise MayaAuthoringE2EError("material.reindex returned the wrong swapped indices")
    created_material = coordinator.read_material_value(
        root,
        0,
        created_material.binding_identity,
    )
    if created_material.index != 0:
        raise MayaAuthoringE2EError("material.reindex did not move the created binding to index 0")
    moved_probe = coordinator.read_morph_value(
        root, reindex_probe.index, reindex_probe.binding_identity
    )
    if moved_probe.offsets[0]["material_index"] != 0:
        raise MayaAuthoringE2EError("material.reindex did not remap Material Morph offsets")

    cmds_adapter.undo()
    coordinator.read_material_value(
        root,
        original_material_index,
        created_material.binding_identity,
    )
    undo_probe = coordinator.read_morph_value(
        root, reindex_probe.index, reindex_probe.binding_identity
    )
    if undo_probe.offsets[0]["material_index"] != original_material_index:
        raise MayaAuthoringE2EError("material.reindex undo did not restore Material Morph offsets")

    cmds_adapter.redo()
    created_material = coordinator.read_material_value(
        root,
        0,
        created_material.binding_identity,
    )
    redo_probe = coordinator.read_morph_value(
        root, reindex_probe.index, reindex_probe.binding_identity
    )
    if redo_probe.offsets[0]["material_index"] != 0:
        raise MayaAuthoringE2EError("material.reindex redo did not restore Material Morph offsets")
    coordinator.delete_morph(root, reindex_probe.index)
    operations.append(
        _operation(
            "material.reindex",
            details={
                "binding": created_material.binding_identity,
                "index": created_material.index,
                "elapsed_ms": reindex_elapsed_ms,
                "undo_redo_verified": True,
                "material_morph_remap_verified": True,
            },
        )
    )
    _assign_material_with_standard_maya_sets(
        cmds_adapter,
        material_authoring,
        root,
        mesh,
        created_material,
    )
    # Refresh through the strict metadata read after Maya set membership was
    # edited.  Face ownership remains collector-owned for export; this read
    # only proves that the semantic material registry still resolves cleanly.
    current = coordinator.read_spec(root)
    if not any(item.index == created_material.index for item in current.materials):
        raise MayaAuthoringE2EError("standard Maya material assignment removed the material")
    operations.append(
        _operation(
            "material.assign",
            details={"index": created_material.index, "route": "maya.sets(forceElement=SG)"},
        )
    )
    current = coordinator.delete_material(root, created_material.index)
    if len(current.materials) != 1 or current.materials[0].index != 0:
        raise MayaAuthoringE2EError("material.delete did not compact to the template material")
    operations.append(_operation("material.delete", details={"index": created_material.index}))
    # Keep one registry-owned, zero-face material through export.  The export
    # bridge must synthesize its missing oracle provenance instead of blocking
    # PMX projection.
    unassigned_material = coordinator.create_material(root)
    if not isinstance(unassigned_material, MmdMaterialSpec):
        raise MayaAuthoringE2EError("material.create did not return a material")
    if unassigned_material.index == 0 or not unassigned_material.binding_identity:
        raise MayaAuthoringE2EError("material.create did not preserve an unassigned binding")

    # Bone CRUD ---------------------------------------------------------
    joint = _canonical_joint(
        cmds_adapter,
        cmds_adapter.create_node("joint", name="e2eBone", parent=root),
    )
    bone_register_started = time.perf_counter()
    registered = coordinator.register_selected_joint(root, joint)
    bone_register_elapsed_ms = (time.perf_counter() - bone_register_started) * 1000.0
    if not isinstance(registered, MmdBoneSpec) or registered.binding_identity != joint:
        raise MayaAuthoringE2EError("bone.register did not persist the new joint")
    read_bone_value = getattr(coordinator, "read_bone_value", None)
    if not callable(read_bone_value):
        raise MayaAuthoringE2EError("bone.capture_rest requires the selected-bone reader")
    cmds_adapter.undo()
    try:
        read_bone_value(root, registered.index, joint)
    except Exception:
        pass
    else:
        raise MayaAuthoringE2EError("bone.register undo did not remove the new binding")
    cmds_adapter.redo()
    if read_bone_value(root, registered.index, joint) != registered:
        raise MayaAuthoringE2EError("bone.register redo did not restore the new binding")
    operations.append(
        _operation(
            "bone.register",
            details={
                "index": registered.index,
                "elapsed_ms": bone_register_elapsed_ms,
                "undo_redo_verified": True,
            },
        )
    )
    before_capture = read_bone_value(root, registered.index, joint)
    cmds_adapter.xform(joint, translation=(2.0, 3.0, 4.0), worldSpace=True)
    capture_started = time.perf_counter()
    captured = coordinator.capture_rest(root, registered.index, joint)
    capture_elapsed_ms = (time.perf_counter() - capture_started) * 1000.0
    if not isinstance(captured, MmdBoneSpec) or captured.rest_position == before_capture.rest_position:
        raise MayaAuthoringE2EError("bone.capture_rest did not patch the selected Rest value")
    cmds_adapter.undo()
    if read_bone_value(root, registered.index, joint) != before_capture:
        raise MayaAuthoringE2EError("bone.capture_rest undo did not restore the selected bone")
    cmds_adapter.redo()
    if read_bone_value(root, registered.index, joint) != captured:
        raise MayaAuthoringE2EError("bone.capture_rest redo did not restore the captured Rest value")
    operations.append(
        _operation(
            "bone.capture_rest",
            details={
                "index": registered.index,
                "elapsed_ms": capture_elapsed_ms,
                "undo_redo_verified": True,
            },
        )
    )
    current = coordinator.read_spec(root)
    current = coordinator.reindex_bones(root, [registered.index, 0])
    if [item.index for item in current.bones] != [0, 1]:
        raise MayaAuthoringE2EError("bone.reindex did not produce canonical indices")
    operations.append(_operation("bone.reindex", details={"order": [registered.index, 0]}))
    current = coordinator.unregister_bone(root, 0)
    if len(current.bones) != 1 or current.bones[0].index != 0:
        raise MayaAuthoringE2EError("bone.unregister did not compact the survivor")
    operations.append(_operation("bone.unregister", details={"removed": 0}))

    # Create empty Morph bindings through the narrow route; offset payloads
    # are a separate follow-up edit operation.
    morph_a = MmdMorphSpec(
        name="E2E Morph A",
        name_english="E2E Morph A",
        panel=4,
        morph_type="bone",
    )
    morph_create_started = time.perf_counter()
    created_morph_a = coordinator.create_morph(root, morph_a)
    morph_create_elapsed_ms = (time.perf_counter() - morph_create_started) * 1000.0
    if not isinstance(created_morph_a, MmdMorphSpec) or not created_morph_a.binding_identity:
        raise MayaAuthoringE2EError("morph.create did not return a bound morph")
    cmds_adapter.undo()
    try:
        coordinator.read_morph_value(root, created_morph_a.index, created_morph_a.binding_identity)
    except Exception:
        pass
    else:
        raise MayaAuthoringE2EError("morph.create undo did not remove the new binding")
    cmds_adapter.redo()
    if coordinator.read_morph_value(
        root, created_morph_a.index, created_morph_a.binding_identity
    ) != created_morph_a:
        raise MayaAuthoringE2EError("morph.create redo did not restore the new binding")
    morph_b = replace(morph_a, name="E2E Morph B", name_english="E2E Morph B")
    coordinator.create_morph(root, morph_b)
    coordinator.create_morph(
        root,
        MmdMorphSpec(
            name="E2E Vertex",
            name_english="E2E Vertex",
            panel=4,
            morph_type="vertex",
        ),
    )
    morph_group = MmdMorphSpec(
        name="E2E Group",
        name_english="E2E Group",
        panel=4,
        morph_type="group",
    )
    coordinator.create_morph(root, morph_group)
    material_offset = _material_morph_offset(0)
    coordinator.create_morph(
        root,
        MmdMorphSpec(
            name="E2E Material",
            name_english="E2E Material",
            panel=4,
            morph_type="material",
        ),
    )
    coordinator.create_morph(
        root,
        MmdMorphSpec(
            name="E2E UV",
            name_english="E2E UV",
            panel=4,
            morph_type="uv",
        ),
    )
    coordinator.create_morph(
        root,
        MmdMorphSpec(
            name="E2E Additional UV1",
            name_english="E2E Additional UV1",
            panel=4,
            morph_type="additional_uv1",
        ),
    )
    current = coordinator.read_spec(root)
    expected_types = ["bone", "bone", "vertex", "group", "material", "uv", "additional_uv1"]
    if len(current.morphs) != len(expected_types) or [item.morph_type for item in current.morphs] != expected_types:
        raise MayaAuthoringE2EError("morph.create did not persist all supported fixture types")
    operations.append(
        _operation(
            "morph.create",
            details={
                "count": len(current.morphs),
                "created_types": expected_types,
                "elapsed_ms": morph_create_elapsed_ms,
                "undo_redo_verified": True,
            },
        )
    )

    # Edit the existing Bone Morph payload through the selected-binding value
    # transaction, then exercise the structural routes for the remaining
    # families which do not all expose a selected-only runtime contract.
    read_morph_value = getattr(coordinator, "read_morph_value", None)
    apply_morph_value_patch = getattr(coordinator, "apply_morph_value_patch", None)
    if not callable(read_morph_value) or not callable(apply_morph_value_patch):
        raise MayaAuthoringE2EError("morph.edit requires the selected-morph value patch API")
    current = coordinator.replace_morph_offsets(
        root,
        0,
        (
            {
                "bone_index": 0,
                "translation": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
            },
        ),
    )
    old_bone_morph = read_morph_value(root, 0, current.morphs[0].binding_identity)
    edited_bone_morph = replace(
        old_bone_morph,
        offsets=(
            {
                "bone_index": 0,
                "translation": [0.125, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
            },
        ),
    )
    morph_patch_started = time.perf_counter()
    patched_bone_morph = apply_morph_value_patch(root, edited_bone_morph)
    morph_patch_elapsed_ms = (time.perf_counter() - morph_patch_started) * 1000.0
    if not isinstance(patched_bone_morph, MmdMorphSpec) or patched_bone_morph != edited_bone_morph:
        raise MayaAuthoringE2EError("morph value patch returned an invalid morph")
    if read_morph_value(root, 0, old_bone_morph.binding_identity) != edited_bone_morph:
        raise MayaAuthoringE2EError("morph value patch did not persist selected offsets")
    cmds_adapter.undo()
    if read_morph_value(root, 0, old_bone_morph.binding_identity) != old_bone_morph:
        raise MayaAuthoringE2EError("morph value patch undo did not restore selected offsets")
    cmds_adapter.redo()
    if read_morph_value(root, 0, old_bone_morph.binding_identity) != edited_bone_morph:
        raise MayaAuthoringE2EError("morph value patch redo did not restore selected offsets")
    current = coordinator.read_spec(root)
    current = coordinator.replace_morph_offsets(
        root,
        2,
        ({"vertex_index": 0, "position_offset": [0.0, 0.25, 0.0]},),
    )
    edited_vertex = next(item for item in current.morphs if item.index == 2)
    if edited_vertex.offsets[0]["position_offset"] != (0.0, 0.25, 0.0):
        raise MayaAuthoringE2EError("morph.edit did not persist vertex offsets")
    current = coordinator.replace_morph_offsets(root, 3, ({"morph_index": 0, "morph_rate": 0.75},))
    current = coordinator.replace_morph_offsets(
        root,
        4,
        (dict(material_offset, diffuse=[0.9, 1.0, 1.0, 1.0]),),
    )
    current = coordinator.replace_morph_offsets(root, 5, ({"vertex_index": 0, "uv_offset": [0.1, 0.0, 0.0, 0.0]},))
    current = coordinator.replace_morph_offsets(
        root,
        6,
        ({"vertex_index": 0, "uv_offset": [0.0, 0.1, 0.0, 0.0]},),
    )
    edited_types = ["vertex", "bone", "group", "material"]
    roundtrip_types = ["uv", "additional_uv1"]
    operations.append(
        _operation(
            "morph.edit",
            details={
                "edited_types": edited_types,
                "roundtrip_types": roundtrip_types,
                "value_patch_elapsed_ms": morph_patch_elapsed_ms,
                "undo_redo_verified": True,
            },
        )
    )
    before_move = coordinator.read_spec(root)
    old_first, old_second = before_move.morphs[0], before_move.morphs[1]
    move_started = time.perf_counter()
    move_result = coordinator.move_morph(root, 0, 1)
    move_elapsed_ms = (time.perf_counter() - move_started) * 1000.0
    if not isinstance(move_result, MorphReindexResult) or move_result.swapped_indices != (0, 1):
        raise MayaAuthoringE2EError("morph.reindex did not return the adjacent swap result")
    moved_first = read_morph_value(root, 1, old_first.binding_identity)
    moved_second = read_morph_value(root, 0, old_second.binding_identity)
    if moved_first.name != old_first.name or moved_second.name != old_second.name:
        raise MayaAuthoringE2EError("morph.reindex did not preserve binding identity")
    cmds_adapter.undo()
    if read_morph_value(root, 0, old_first.binding_identity) != old_first:
        raise MayaAuthoringE2EError("morph.reindex undo did not restore the first morph")
    if read_morph_value(root, 1, old_second.binding_identity) != old_second:
        raise MayaAuthoringE2EError("morph.reindex undo did not restore the second morph")
    cmds_adapter.redo()
    if read_morph_value(root, 1, old_first.binding_identity) != moved_first:
        raise MayaAuthoringE2EError("morph.reindex redo did not restore the adjacent swap")
    current = coordinator.read_spec(root)
    operations.append(
        _operation(
            "morph.reindex",
            details={
                "swapped_indices": [0, 1],
                "elapsed_ms": move_elapsed_ms,
                "undo_redo_verified": True,
            },
        )
    )

    before_spec = metadata_adapter.read_spec(root)
    if not isinstance(before_spec, MmdModelAuthoringSpec):
        raise MayaAuthoringE2EError("pre-export metadata read did not return strict Spec")

    # Export and parse --------------------------------------------------
    output_path = _safe_output_path(asset_paths)
    action = export_action or ExportModelAction()
    result = action.execute(
        ExportModelRequest(
            str(output_path),
            {
                "target_model": root,
                "export_format": "pmx",
                "authoring_semantics": "auto",
                "validation_report_dir": str(output_path.parent / "authoring-e2e-validation"),
            },
        )
    )
    if not getattr(result, "succeeded", False) or not output_path.is_file():
        raise MayaAuthoringE2EError(f"export.pmx failed: {getattr(result, 'error', result)!r}")
    if pmx_parser is None:
        from mmd_tools.core.mmd_parser import parse_pmx_file

        pmx_parser = parse_pmx_file
    parser = pmx_parser(str(output_path))
    if parser is None:
        raise MayaAuthoringE2EError("PmxData parser returned no parser")
    parsed_unassigned = _require_exported_unassigned_material(parser, unassigned_material)
    operations.append(
        _operation(
            "export.pmx",
            details={
                "path": str(output_path),
                "unassigned_material_index": unassigned_material.index,
                "unassigned_material_exportable": True,
                "unassigned_material_parsed": {
                    **parsed_unassigned,
                },
            },
        )
    )

    # Fresh scene import and strict read-back --------------------------
    cmds_adapter.new_scene(force=True)
    if pmx_importer is None:
        from mmd_tools.io.pmx_importer import import_pmx_file

        pmx_importer = import_pmx_file
    fresh_root = pmx_importer(parser, str(output_path), options={"import_physics": False})
    if not isinstance(fresh_root, str) or not fresh_root:
        raise MayaAuthoringE2EError("fresh PMX import returned no model root")
    operations.append(_operation("import.fresh_scene", details={"root": fresh_root}))
    after_spec = metadata_adapter.read_spec(fresh_root)
    if not isinstance(after_spec, MmdModelAuthoringSpec):
        raise MayaAuthoringE2EError("fresh scene metadata read did not return strict Spec")
    operations.append(_operation("spec.read", details={"root": fresh_root}))

    before = normalize_spec_payload(before_spec)
    after = normalize_spec_payload(after_spec)
    before = _omit_unassigned_material(before, unassigned_material.index)
    after = _omit_unassigned_material(after, unassigned_material.index)
    changed_sections = [
        section
        for section in ("model", "materials", "bones", "morphs")
        if before.get(section) != after.get(section)
    ]
    if changed_sections:
        raise MayaAuthoringE2EError(
            "semantic Spec changed during PMX round-trip: "
            f"{changed_sections!r}; before={before!r}; after={after!r}"
        )
    if before["fingerprint"] != after["fingerprint"]:
        raise MayaAuthoringE2EError("normalized Spec fingerprint changed during PMX round-trip")
    return {
        "operations": operations,
        "before": before,
        "after": after,
        "negative_cases": _negative_cases(),
    }


__all__ = [
    "MayaAuthoringE2EError",
    "REQUIRED_OPERATIONS",
    "normalize_spec_payload",
    "run_authoring_e2e",
]
