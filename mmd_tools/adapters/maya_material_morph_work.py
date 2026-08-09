"""Temporary StandardSurface editing bindings for PMX material morph offsets."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from mmd_tools.core import model_registry
from mmd_tools.core.model_authoring_spec import MmdMaterialSpec, MmdMorphSpec
from mmd_tools.core.model_registry import REGISTRY_CATEGORY_MATERIAL_MORPH_WORK


_ATTR_MARKER = "mmd_material_morph_work"
_ATTR_MORPH_INDEX = "mmd_work_morph_index"
_ATTR_OFFSET_INDEX = "mmd_work_offset_index"
_ATTR_MATERIAL_INDEX = "mmd_work_material_index"
_ATTR_OPERATION = "mmd_work_operation_type"
_ATTR_MORPH_NAME = "mmd_work_morph_name"
_ATTR_MATERIAL_NAME = "mmd_work_material_name"
_ATTR_DIFFUSE_ALPHA = "mmd_work_diffuse_alpha"
_ATTR_SPECULAR_COEFFICIENT = "mmd_work_specular_coefficient"
_ATTR_AMBIENT = "mmd_work_ambient"
_ATTR_EDGE_COLOR = "mmd_work_edge_color"
_ATTR_EDGE_ALPHA = "mmd_work_edge_alpha"
_ATTR_EDGE_SIZE = "mmd_work_edge_size"
_FACTOR_FIELDS = ("texture_factor", "sphere_texture_factor", "toon_texture_factor")


class MayaMaterialMorphWorkError(RuntimeError):
    """Raised before ambiguous work-material state can affect raw offsets."""


class MayaMaterialMorphWork:
    """Own one temporary work shader per model root and apply it via coordinator."""

    def __init__(self, adapter: Any, coordinator: Any, *, registry_api: Any = model_registry) -> None:
        self._cmds = adapter
        self._coordinator = coordinator
        self._registry = registry_api

    def create(self, root: str, morph_index: int, offset_index: int) -> str:
        """Create an undoable work shader without changing canonical raw data."""
        spec, morph, offset, material = self._resolve(root, morph_index, offset_index)
        del spec
        if self._work_members(root):
            raise MayaMaterialMorphWorkError("the model already owns a material morph work binding")
        values = _apply_offset(material, offset)
        self._open_undo("Create Material Morph Work")
        try:
            shader = str(
                self._cmds.shading_node(
                    "standardSurface",
                    asShader=True,
                    name=f"mmdMaterialMorphWork_{morph.index}_{offset_index}",
                )
            )
            if not shader or shader.startswith("|") or not shader.isascii():
                raise MayaMaterialMorphWorkError("work shader identity must be an ASCII DG node name")
            self._write_work(shader, morph, offset_index, offset, material, values)
            registry = self._registry.ensure_model_registry(root)
            self._registry.register_model_members(
                registry,
                REGISTRY_CATEGORY_MATERIAL_MORPH_WORK,
                [shader],
            )
            self._close_undo()
        except Exception:
            self._rollback_undo()
            raise
        select = getattr(self._cmds, "select", None)
        if callable(select):
            select(shader, replace=True)
        return shader

    def apply(self, root: str, morph_index: int, offset_index: int):
        """Convert the owned work shader back to one canonical raw offset."""
        spec, morph, offset, material = self._resolve(root, morph_index, offset_index)
        members = self._work_members(root)
        if len(members) != 1:
            raise MayaMaterialMorphWorkError("exactly one owned work binding is required")
        shader = members[0]
        self._validate_work_identity(shader, morph, offset_index, offset)
        work_values = self._read_work(shader)
        updated = _offset_from_work(material, offset, work_values)
        offsets = [dict(value) for value in morph.offsets]
        offsets[offset_index] = updated
        # Coordinator owns the one semantic undo transaction and runtime rebuild.
        return self._coordinator.replace_morph_offsets(root, morph.index, offsets)

    def clear(self, root: str) -> None:
        """Unregister and delete the work shader without changing raw offsets."""
        members = self._work_members(root)
        if len(members) != 1:
            raise MayaMaterialMorphWorkError("exactly one owned work binding is required")
        shader = members[0]
        self._open_undo("Clear Material Morph Work")
        try:
            registry = self._registry.ensure_model_registry(root)
            self._registry.unregister_model_members(
                registry,
                REGISTRY_CATEGORY_MATERIAL_MORPH_WORK,
                [shader],
            )
            self._cmds.delete(shader)
            self._close_undo()
        except Exception:
            self._rollback_undo()
            raise

    def _resolve(
        self, root: str, morph_index: int, offset_index: int
    ) -> tuple[Any, MmdMorphSpec, Mapping[str, Any], MmdMaterialSpec]:
        if not isinstance(morph_index, int) or isinstance(morph_index, bool):
            raise MayaMaterialMorphWorkError("morph index must be an integer")
        if not isinstance(offset_index, int) or isinstance(offset_index, bool):
            raise MayaMaterialMorphWorkError("offset index must be an integer")
        spec = self._coordinator.read_spec(root)
        morph = next((item for item in spec.morphs if item.index == morph_index), None)
        if morph is None or morph.morph_type != "material":
            raise MayaMaterialMorphWorkError("selected morph must be a material morph")
        if offset_index < 0 or offset_index >= len(morph.offsets):
            raise MayaMaterialMorphWorkError("selected material morph offset does not exist")
        offset = morph.offsets[offset_index]
        material_index = offset.get("material_index")
        operation = offset.get("operation_type")
        if material_index == -1:
            raise MayaMaterialMorphWorkError("all-material offsets have no single work target")
        if operation not in (0, 1):
            raise MayaMaterialMorphWorkError("material morph operation must be multiply(0) or add(1)")
        material = next((item for item in spec.materials if item.index == material_index), None)
        if material is None:
            raise MayaMaterialMorphWorkError(f"unknown target material index {material_index!r}")
        return spec, morph, offset, material

    def _work_members(self, root: str) -> list[str]:
        members = self._registry.list_model_registry_members(
            root, REGISTRY_CATEGORY_MATERIAL_MORPH_WORK
        )
        return [str(member) for member in (members or [])]

    def _write_work(
        self,
        shader: str,
        morph: MmdMorphSpec,
        offset_index: int,
        offset: Mapping[str, Any],
        material: MmdMaterialSpec,
        values: Mapping[str, Any],
    ) -> None:
        for attr, value in (
            (_ATTR_MARKER, True),
            (_ATTR_MORPH_INDEX, morph.index),
            (_ATTR_OFFSET_INDEX, offset_index),
            (_ATTR_MATERIAL_INDEX, material.index),
            (_ATTR_OPERATION, offset["operation_type"]),
        ):
            self._set_scalar(shader, attr, value, "bool" if isinstance(value, bool) else "long")
        self._set_scalar(shader, _ATTR_MORPH_NAME, morph.name, "string")
        self._set_scalar(shader, _ATTR_MATERIAL_NAME, material.name, "string")
        self._cmds.set_attr(f"{shader}.baseColor", *values["diffuse"][:3], type="float3")
        self._cmds.set_attr(f"{shader}.specularColor", *values["specular"], type="float3")
        self._set_scalar(shader, _ATTR_DIFFUSE_ALPHA, values["diffuse"][3], "double")
        self._set_scalar(
            shader, _ATTR_SPECULAR_COEFFICIENT, values["specular_coefficient"], "double"
        )
        self._set_vector(shader, _ATTR_AMBIENT, values["ambient"])
        self._set_vector(shader, _ATTR_EDGE_COLOR, values["edge_color"][:3])
        self._set_scalar(shader, _ATTR_EDGE_ALPHA, values["edge_color"][3], "double")
        self._set_scalar(shader, _ATTR_EDGE_SIZE, values["edge_size"], "double")
        for field in _FACTOR_FIELDS:
            for component, value in zip("RGBA", offset[field]):
                self._set_scalar(shader, f"mmd_work_{field}_{component.lower()}", value, "double")

    def _read_work(self, shader: str) -> dict[str, Any]:
        diffuse = (*_vector(self._cmds.get_attr(f"{shader}.baseColor"), 3, "baseColor"),)
        diffuse = (*diffuse, _number(self._cmds.get_attr(f"{shader}.{_ATTR_DIFFUSE_ALPHA}"), _ATTR_DIFFUSE_ALPHA))
        values = {
            "diffuse": diffuse,
            "specular": _vector(self._cmds.get_attr(f"{shader}.specularColor"), 3, "specularColor"),
            "specular_coefficient": _number(
                self._cmds.get_attr(f"{shader}.{_ATTR_SPECULAR_COEFFICIENT}"),
                _ATTR_SPECULAR_COEFFICIENT,
            ),
            "ambient": _vector(self._cmds.get_attr(f"{shader}.{_ATTR_AMBIENT}"), 3, _ATTR_AMBIENT),
            "edge_color": (
                *_vector(self._cmds.get_attr(f"{shader}.{_ATTR_EDGE_COLOR}"), 3, _ATTR_EDGE_COLOR),
                _number(self._cmds.get_attr(f"{shader}.{_ATTR_EDGE_ALPHA}"), _ATTR_EDGE_ALPHA),
            ),
            "edge_size": _number(self._cmds.get_attr(f"{shader}.{_ATTR_EDGE_SIZE}"), _ATTR_EDGE_SIZE),
        }
        for field in _FACTOR_FIELDS:
            values[field] = tuple(
                _number(
                    self._cmds.get_attr(f"{shader}.mmd_work_{field}_{component.lower()}"),
                    field,
                )
                for component in "RGBA"
            )
        return values

    def _validate_work_identity(
        self, shader: str, morph: MmdMorphSpec, offset_index: int, offset: Mapping[str, Any]
    ) -> None:
        expected = {
            _ATTR_MARKER: True,
            _ATTR_MORPH_INDEX: morph.index,
            _ATTR_OFFSET_INDEX: offset_index,
            _ATTR_MATERIAL_INDEX: offset["material_index"],
            _ATTR_OPERATION: offset["operation_type"],
        }
        for attr, value in expected.items():
            if not self._cmds.attribute_exists(attr, shader):
                raise MayaMaterialMorphWorkError(f"work binding is missing {attr}")
            if self._cmds.get_attr(f"{shader}.{attr}") != value:
                raise MayaMaterialMorphWorkError(f"work binding {attr} does not match selection")

    def _set_scalar(self, node: str, attr: str, value: Any, attr_type: str) -> None:
        if not self._cmds.attribute_exists(attr, node):
            kwargs = {"longName": attr, "attributeType": attr_type}
            if attr_type == "string":
                kwargs = {"longName": attr, "dataType": "string"}
            self._cmds.add_attr(node, **kwargs)
        if attr_type == "string":
            self._cmds.set_attr(f"{node}.{attr}", value, type="string")
        else:
            self._cmds.set_attr(f"{node}.{attr}", value)

    def _set_vector(self, node: str, attr: str, values: Sequence[float]) -> None:
        if not self._cmds.attribute_exists(attr, node):
            self._cmds.add_attr(node, longName=attr, attributeType="double3")
            for axis in "XYZ":
                self._cmds.add_attr(
                    node,
                    longName=f"{attr}{axis}",
                    attributeType="double",
                    parent=attr,
                )
        self._cmds.set_attr(f"{node}.{attr}", *values, type="double3")

    def _open_undo(self, name: str) -> None:
        self._cmds.undo_info(openChunk=True, chunkName=name)

    def _close_undo(self) -> None:
        self._cmds.undo_info(closeChunk=True)

    def _rollback_undo(self) -> None:
        try:
            self._cmds.undo_info(closeChunk=True)
        finally:
            self._cmds.undo()


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MayaMaterialMorphWorkError(f"work shader {field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise MayaMaterialMorphWorkError(f"work shader {field} must be finite")
    return result


def _vector(value: Any, size: int, field: str) -> tuple[float, ...]:
    while isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], (list, tuple)):
        value = value[0]
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise MayaMaterialMorphWorkError(f"work shader {field} must contain {size} values")
    return tuple(_number(component, field) for component in value)


def _combine(base: Sequence[float], value: Sequence[float], operation: int) -> tuple[float, ...]:
    if operation == 0:
        return tuple(float(left) * float(right) for left, right in zip(base, value))
    return tuple(float(left) + float(right) for left, right in zip(base, value))


def _apply_offset(material: MmdMaterialSpec, offset: Mapping[str, Any]) -> dict[str, Any]:
    operation = int(offset["operation_type"])
    return {
        "diffuse": _combine(material.diffuse, offset["diffuse"], operation),
        "specular": _combine(material.specular, offset["specular"], operation),
        "specular_coefficient": _combine(
            (material.specular_coefficient,), (offset["specular_coefficient"],), operation
        )[0],
        "ambient": _combine(material.ambient, offset["ambient"], operation),
        "edge_color": _combine(material.edge_color, offset["edge_color"], operation),
        "edge_size": _combine((material.edge_size,), (offset["edge_size"],), operation)[0],
    }


def _inverse(
    base: Sequence[float], work: Sequence[float], original: Sequence[float], operation: int, field: str
) -> list[float]:
    if operation == 1:
        return [float(value) - float(base_value) for base_value, value in zip(base, work)]
    result = []
    for base_value, work_value, original_value in zip(base, work, original):
        if abs(float(base_value)) <= 1.0e-12:
            if abs(float(work_value)) > 1.0e-12:
                raise MayaMaterialMorphWorkError(
                    f"{field} cannot represent a multiply result over a zero base component"
                )
            result.append(float(original_value))
        else:
            result.append(float(work_value) / float(base_value))
    return result


def _offset_from_work(
    material: MmdMaterialSpec, original: Mapping[str, Any], work: Mapping[str, Any]
) -> dict[str, Any]:
    operation = int(original["operation_type"])
    result = {
        "material_index": int(original["material_index"]),
        "operation_type": operation,
        "diffuse": _inverse(material.diffuse, work["diffuse"], original["diffuse"], operation, "diffuse"),
        "specular": _inverse(material.specular, work["specular"], original["specular"], operation, "specular"),
        "specular_coefficient": _inverse(
            (material.specular_coefficient,),
            (work["specular_coefficient"],),
            (original["specular_coefficient"],),
            operation,
            "specular_coefficient",
        )[0],
        "ambient": _inverse(material.ambient, work["ambient"], original["ambient"], operation, "ambient"),
        "edge_color": _inverse(
            material.edge_color, work["edge_color"], original["edge_color"], operation, "edge_color"
        ),
        "edge_size": _inverse(
            (material.edge_size,), (work["edge_size"],), (original["edge_size"],), operation, "edge_size"
        )[0],
    }
    for field in _FACTOR_FIELDS:
        result[field] = list(work[field])
    return result


__all__ = ["MayaMaterialMorphWork", "MayaMaterialMorphWorkError"]
