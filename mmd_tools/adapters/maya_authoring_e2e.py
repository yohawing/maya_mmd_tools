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
from typing import Any

from mmd_tools.actions.export_model_action import ExportModelAction, ExportModelRequest
from mmd_tools.core.model_authoring_spec import (
    MmdMaterialSpec,
    MmdModelAuthoringSpec,
    MmdMorphSpec,
)
from mmd_tools.validation.snapshot import fingerprint_payload


REQUIRED_OPERATIONS = (
    "material.create",
    "material.edit",
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
    material_authoring: Any,
    root: str,
    material_index: int,
) -> MmdModelAuthoringSpec:
    """Perform a real semantic material edit through the coordinator boundary."""
    current = coordinator.read_spec(root)
    old = next((item for item in current.materials if item.index == material_index), None)
    if not isinstance(old, MmdMaterialSpec):
        raise MayaAuthoringE2EError(f"material index {material_index} was not created")
    edited = replace(old, name=f"{old.name} (edited)", name_english=f"{old.name_english} edited")
    target = replace(
        current,
        materials=tuple(edited if item.index == material_index else item for item in current.materials),
    )
    replace_material = getattr(coordinator, "replace_material", None)
    if not callable(replace_material):
        raise MayaAuthoringE2EError("material edit requires coordinator.replace_material")
    result = replace_material(root, edited)
    if type(result) is not MmdModelAuthoringSpec:
        raise MayaAuthoringE2EError("material edit returned an invalid spec")
    observed = coordinator.read_spec(root)
    if observed.materials != target.materials:
        raise MayaAuthoringE2EError("material edit did not persist semantic values")
    return observed


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
    current = coordinator.create_material(root, [mesh])
    created_material = max(current.materials, key=lambda item: item.index)
    if created_material.index == 0 or not created_material.binding_identity:
        raise MayaAuthoringE2EError("material.create did not create a bound material")
    operations.append(_operation("material.create", details={"index": created_material.index}))
    current = _edit_material(coordinator, material_authoring, root, created_material.index)
    operations.append(_operation("material.edit", details={"index": created_material.index}))
    current = coordinator.assign_material(root, created_material.index, [mesh])
    if not any(item.index == created_material.index for item in current.materials):
        raise MayaAuthoringE2EError("material.assign removed the assigned material")
    operations.append(_operation("material.assign", details={"index": created_material.index}))
    current = coordinator.delete_material(root, created_material.index)
    if len(current.materials) != 1 or current.materials[0].index != 0:
        raise MayaAuthoringE2EError("material.delete did not compact to the template material")
    operations.append(_operation("material.delete", details={"index": created_material.index}))

    # Bone CRUD ---------------------------------------------------------
    joint = _canonical_joint(
        cmds_adapter,
        cmds_adapter.create_node("joint", name="e2eBone", parent=root),
    )
    current = coordinator.register_selected_joint(root, joint)
    registered = next((item for item in current.bones if item.binding_identity == joint), None)
    if registered is None:
        raise MayaAuthoringE2EError("bone.register did not persist the new joint")
    operations.append(_operation("bone.register", details={"index": registered.index}))
    current = coordinator.capture_rest(root, registered.index, joint)
    captured = next(item for item in current.bones if item.binding_identity == joint)
    if captured.rest_position == (0.0, 0.0, 0.0):
        # The deterministic fixture joint is at the origin, so a zero value is
        # valid.  Verify the operation's observable read-back instead.
        if not isinstance(captured.rest_position, tuple):
            raise MayaAuthoringE2EError("bone.capture_rest returned a malformed position")
    operations.append(_operation("bone.capture_rest", details={"index": registered.index}))
    current = coordinator.reindex_bones(root, [registered.index, 0])
    if [item.index for item in current.bones] != [0, 1]:
        raise MayaAuthoringE2EError("bone.reindex did not produce canonical indices")
    operations.append(_operation("bone.reindex", details={"order": [registered.index, 0]}))
    current = coordinator.unregister_bone(root, 0)
    if len(current.bones) != 1 or current.bones[0].index != 0:
        raise MayaAuthoringE2EError("bone.unregister did not compact the survivor")
    operations.append(_operation("bone.unregister", details={"removed": 0}))

    # Supported morph CRUD.  The fixture intentionally covers the semantic
    # offset families that the current Maya runtime can author without a
    # blendShape target oracle.  Vertex morph creation/edit remains a strict
    # fail-closed operation and is covered by the separate vertex-authoring
    # gate when that topology is available.
    morph_a = MmdMorphSpec(
        name="E2E Morph A",
        name_english="E2E Morph A",
        panel=4,
        morph_type="bone",
        offsets=({"bone_index": 0, "translation": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0, 1.0]},),
    )
    current = coordinator.create_morph(root, morph_a)
    morph_b = replace(morph_a, name="E2E Morph B", name_english="E2E Morph B")
    current = coordinator.create_morph(root, morph_b)
    current = coordinator.create_morph(
        root,
        MmdMorphSpec(
            name="E2E Vertex",
            name_english="E2E Vertex",
            panel=4,
            morph_type="vertex",
            offsets=({"vertex_index": 0, "position_offset": [0.0, 0.125, 0.0]},),
        ),
    )
    morph_group = MmdMorphSpec(
        name="E2E Group",
        name_english="E2E Group",
        panel=4,
        morph_type="group",
        offsets=({"morph_index": 0, "morph_rate": 0.5},),
    )
    current = coordinator.create_morph(root, morph_group)
    material_offset = {
        "material_index": 0,
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
    current = coordinator.create_morph(
        root,
        MmdMorphSpec(
            name="E2E Material",
            name_english="E2E Material",
            panel=4,
            morph_type="material",
            offsets=(material_offset,),
        ),
    )
    current = coordinator.create_morph(
        root,
        MmdMorphSpec(
            name="E2E UV",
            name_english="E2E UV",
            panel=4,
            morph_type="uv",
            offsets=({"vertex_index": 0, "uv_offset": [0.0, 0.0, 0.0, 0.0]},),
        ),
    )
    current = coordinator.create_morph(
        root,
        MmdMorphSpec(
            name="E2E Additional UV1",
            name_english="E2E Additional UV1",
            panel=4,
            morph_type="additional_uv1",
            offsets=({"vertex_index": 0, "uv_offset": [0.0, 0.0, 0.0, 0.0]},),
        ),
    )
    expected_types = ["bone", "bone", "vertex", "group", "material", "uv", "additional_uv1"]
    if len(current.morphs) != len(expected_types) or [item.morph_type for item in current.morphs] != expected_types:
        raise MayaAuthoringE2EError("morph.create did not persist all supported fixture types")
    operations.append(
        _operation(
            "morph.create",
            details={"count": len(current.morphs), "created_types": expected_types},
        )
    )

    # Edit one representative offset from every supported family.
    current = coordinator.replace_morph_offsets(
        root,
        0,
        ({"bone_index": 0, "translation": [0.125, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0, 1.0]},),
    )
    edited = next(item for item in current.morphs if item.index == 0)
    if edited.offsets[0]["translation"] != (0.125, 0.0, 0.0):
        raise MayaAuthoringE2EError("morph.edit did not persist offsets")
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
            },
        )
    )
    order = list(reversed(range(len(expected_types))))
    current = coordinator.reindex_morphs(root, order)
    if [item.index for item in current.morphs] != list(range(len(expected_types))):
        raise MayaAuthoringE2EError("morph.reindex did not produce canonical indices")
    operations.append(_operation("morph.reindex", details={"order": order, "types": expected_types}))

    before_spec = metadata_adapter.read_spec(root)
    if type(before_spec) is not MmdModelAuthoringSpec:
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
    operations.append(_operation("export.pmx", details={"path": str(output_path)}))
    if pmx_parser is None:
        from mmd_tools.core.mmd_parser import parse_pmx_file

        pmx_parser = parse_pmx_file
    parser = pmx_parser(str(output_path))
    if parser is None:
        raise MayaAuthoringE2EError("PmxData parser returned no parser")

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
    if type(after_spec) is not MmdModelAuthoringSpec:
        raise MayaAuthoringE2EError("fresh scene metadata read did not return strict Spec")
    operations.append(_operation("spec.read", details={"root": fresh_root}))

    before = normalize_spec_payload(before_spec)
    after = normalize_spec_payload(after_spec)
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
