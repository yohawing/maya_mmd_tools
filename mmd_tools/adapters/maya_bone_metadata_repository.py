"""Read strict PMX bone metadata from an injected Maya adapter."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Callable

from mmd_tools.adapters.maya_metadata_read_support import MayaMetadataReadSupport
from mmd_tools.core.constants import (
    ATTR_MMD_AXIS_DIRECTION,
    ATTR_MMD_BONE_FLAGS,
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_BONE_NAME_EN,
    ATTR_MMD_BONE_OFFSET,
    ATTR_MMD_BONE_PARENT_INDEX,
    ATTR_MMD_CONNECT_BONE_INDEX,
    ATTR_MMD_CONNECTION_BONE,
    ATTR_MMD_CONNECT_INDEX,
    ATTR_MMD_DEFORM_LAYER,
    ATTR_MMD_EXTERNAL_PARENT_KEY,
    ATTR_MMD_FIXED_AXIS,
    ATTR_MMD_GRANT_PARENT,
    ATTR_MMD_GRANT_PARENT_INDEX,
    ATTR_MMD_GRANT_RATE,
    ATTR_MMD_IK_LIMIT_ANGLE,
    ATTR_MMD_IK_LINKS,
    ATTR_MMD_IK_LOOP,
    ATTR_MMD_IK_TARGET,
    ATTR_MMD_IK_TARGET_INDEX,
    ATTR_MMD_LOCAL_X_AXIS,
    ATTR_MMD_LOCAL_Z_AXIS,
    ATTR_MMD_PMX_REST_POSITION,
    ATTR_MMD_X_AXIS_DIRECTION,
    ATTR_MMD_Z_AXIS_DIRECTION,
)
from mmd_tools.core.model_authoring_spec import MmdBoneSpec
from mmd_tools.core.pmx_data.bone import PmxBoneFlag


class MayaBoneMetadataRepository:
    """Read the schema-v1 Bone aggregate without owning transactions."""

    def __init__(
        self,
        read_support: MayaMetadataReadSupport,
        *,
        error_factory: Callable[[str], Exception],
    ) -> None:
        self._read = read_support
        self._error = error_factory

    def read_bone_value(
        self,
        model_root: str,
        binding: str,
        index: int | None = None,
    ) -> MmdBoneSpec:
        """Read one selected bone without enumerating model collections."""
        root = self._read.canonical_identity(model_root)
        joint = self._read.canonical_identity(binding)
        self._require_selected_bone(root, joint, index)
        data = self._read_bone(joint)
        flags = int(data["flags"])
        if flags & PmxBoneFlag.CONNECT_BONE:
            data["connect_bone_index"] = self._agreed_int_alias(
                joint,
                (ATTR_MMD_CONNECT_INDEX, ATTR_MMD_CONNECT_BONE_INDEX),
                minimum=-1,
                required=False,
            )
            data["tail_offset"] = (0.0, -1.0, 0.0)
        else:
            data["tail_offset"] = self._read.required_vector(joint, ATTR_MMD_BONE_OFFSET)
        grant_flags = PmxBoneFlag.GRANT_PARENT_ROTATE | PmxBoneFlag.GRANT_PARENT_MOVE
        if flags & grant_flags:
            data["grant_parent_index"] = self._agreed_int_alias(
                joint, (ATTR_MMD_GRANT_PARENT_INDEX,), minimum=0, required=False
            )
            data["grant_ratio"] = self._read.required_number(joint, ATTR_MMD_GRANT_RATE)
        if flags & PmxBoneFlag.AXIS_FIXED:
            data["fixed_axis"] = self._agreed_vector_alias(
                joint, (ATTR_MMD_FIXED_AXIS, ATTR_MMD_AXIS_DIRECTION)
            )
        if flags & PmxBoneFlag.LOCAL_AXIS:
            data["local_axis_x"] = self._agreed_vector_alias(
                joint, (ATTR_MMD_LOCAL_X_AXIS, ATTR_MMD_X_AXIS_DIRECTION)
            )
            data["local_axis_z"] = self._agreed_vector_alias(
                joint, (ATTR_MMD_LOCAL_Z_AXIS, ATTR_MMD_Z_AXIS_DIRECTION)
            )
        if flags & PmxBoneFlag.EXTERNAL_PARENT_DEFORM:
            data["external_parent_key"] = self._read.required_int(
                joint, ATTR_MMD_EXTERNAL_PARENT_KEY
            )
        if flags & PmxBoneFlag.IK:
            data["ik_target_index"] = self._agreed_int_alias(
                joint, (ATTR_MMD_IK_TARGET_INDEX,), minimum=0, required=False
            )
            data["ik_loop_count"] = self._read.required_int(joint, ATTR_MMD_IK_LOOP, minimum=0)
            data["ik_limit_radian"] = self._read.required_number(joint, ATTR_MMD_IK_LIMIT_ANGLE)
            raw_links = self._read.required(joint, ATTR_MMD_IK_LINKS)
            if isinstance(raw_links, str):
                try:
                    raw_links = json.loads(raw_links)
                except (TypeError, ValueError) as exc:
                    raise self._error(
                        f"{joint}.{ATTR_MMD_IK_LINKS} must contain JSON list: {exc}"
                    ) from exc
            if isinstance(raw_links, (str, bytes, bytearray)) or not isinstance(raw_links, Sequence):
                raise self._error(f"{joint}.{ATTR_MMD_IK_LINKS} must be a JSON/list payload")
            data["ik_links"] = list(raw_links)
        return MmdBoneSpec.from_mapping(data)

    def iter_bone_metadata(self, root: str) -> Iterable[Mapping[str, Any]]:
        """Yield canonical PMX bone mappings for tagged descendant joints."""
        self._read.require_root(root)
        joints = self._read.call_adapter(
            "list_relatives",
            root,
            allDescendents=True,
            fullPath=True,
            type="joint",
        ) or []
        seen_bindings: set[str] = set()
        tagged: list[dict[str, Any]] = []
        for joint in joints:
            if not isinstance(joint, str) or not joint.startswith("|"):
                raise self._error(
                    f"{root!r}: joint binding identity must be a canonical long path"
                )
            if joint in seen_bindings:
                raise self._error(f"{root!r}: duplicate joint binding identity {joint!r}")
            seen_bindings.add(joint)
            if not self._read.has_attr(joint, ATTR_MMD_BONE_INDEX):
                continue
            metadata = self._read_bone(joint)
            index = metadata["index"]
            if any(item["index"] == index for item in tagged):
                raise self._error(f"{root!r}: duplicate mmd_bone_index {index}")
            tagged.append(metadata)
        references = self._build_references(tagged)
        for metadata in tagged:
            joint = metadata["binding_identity"]
            self._read_connect(joint, metadata["flags"], metadata, references)
            self._read_grant(joint, metadata["flags"], metadata, references)
            self._read_axes(joint, metadata["flags"], metadata)
            self._read_external_parent(joint, metadata["flags"], metadata)
            self._read_ik(joint, metadata["flags"], metadata, references)
            yield metadata

    def _read_bone(self, joint: str) -> dict[str, Any]:
        flags = self._read.required_int(joint, ATTR_MMD_BONE_FLAGS, minimum=0)
        return {
            "name": self._read.required_string(joint, ATTR_MMD_BONE_NAME),
            "name_english": self._read.required_string(joint, ATTR_MMD_BONE_NAME_EN),
            "index": self._read.required_int(joint, ATTR_MMD_BONE_INDEX, minimum=0),
            "parent_index": self._read.required_int(
                joint, ATTR_MMD_BONE_PARENT_INDEX, minimum=-1
            ),
            "rest_position": self._read.required_vector(joint, ATTR_MMD_PMX_REST_POSITION),
            "transform_layer": self._read.required_int(joint, ATTR_MMD_DEFORM_LAYER, minimum=0),
            "flags": flags,
            "connect_bone_index": None,
            "tail_offset": None,
            "grant_parent_index": None,
            "grant_ratio": 0.0,
            "grant_local": bool(flags & PmxBoneFlag.LOCAL),
            "fixed_axis": None,
            "local_axis_x": None,
            "local_axis_z": None,
            "external_parent_key": None,
            "ik_target_index": None,
            "ik_loop_count": 0,
            "ik_limit_radian": None,
            "ik_links": [],
            "binding_identity": joint,
        }

    def _read_connect(
        self,
        joint: str,
        flags: int,
        data: dict[str, Any],
        references: Mapping[str, set[int]],
    ) -> None:
        attrs = (ATTR_MMD_CONNECT_INDEX, ATTR_MMD_CONNECT_BONE_INDEX)
        if flags & PmxBoneFlag.CONNECT_BONE:
            data["connect_bone_index"] = self._resolve_reference(
                joint,
                attrs,
                ATTR_MMD_CONNECTION_BONE,
                references,
                minimum=-1,
            )
            # BonePresenter creates this editable field on every joint.  Its
            # exact UI default is inactive for index-connected bones, while a
            # different value is stale authored payload and must fail closed.
            self._reject_non_default(
                joint, ATTR_MMD_BONE_OFFSET, (0.0, -1.0, 0.0), "tail_offset"
            )
        else:
            self._reject_present(joint, attrs + (ATTR_MMD_CONNECTION_BONE,), "connect_bone_index")
            data["tail_offset"] = self._read.required_vector(joint, ATTR_MMD_BONE_OFFSET)

    def _read_grant(
        self,
        joint: str,
        flags: int,
        data: dict[str, Any],
        references: Mapping[str, set[int]],
    ) -> None:
        grant_flags = PmxBoneFlag.GRANT_PARENT_ROTATE | PmxBoneFlag.GRANT_PARENT_MOVE
        if flags & grant_flags:
            data["grant_parent_index"] = self._resolve_reference(
                joint, (ATTR_MMD_GRANT_PARENT_INDEX,), ATTR_MMD_GRANT_PARENT, references
            )
            data["grant_ratio"] = self._read.required_number(joint, ATTR_MMD_GRANT_RATE)
        else:
            self._reject_non_default(joint, ATTR_MMD_GRANT_PARENT_INDEX, None, "grant payload")
            self._reject_non_default(joint, ATTR_MMD_GRANT_PARENT, "", "grant payload")
            self._reject_non_default(joint, ATTR_MMD_GRANT_RATE, 1.0, "grant payload")

    def _read_axes(self, joint: str, flags: int, data: dict[str, Any]) -> None:
        fixed = (ATTR_MMD_FIXED_AXIS, ATTR_MMD_AXIS_DIRECTION)
        local_x = (ATTR_MMD_LOCAL_X_AXIS, ATTR_MMD_X_AXIS_DIRECTION)
        local_z = (ATTR_MMD_LOCAL_Z_AXIS, ATTR_MMD_Z_AXIS_DIRECTION)
        if flags & PmxBoneFlag.AXIS_FIXED:
            data["fixed_axis"] = self._agreed_vector_alias(joint, fixed)
        else:
            self._reject_non_default(joint, ATTR_MMD_FIXED_AXIS, (0.0, 0.0, 1.0), "fixed_axis")
            self._reject_present(joint, (ATTR_MMD_AXIS_DIRECTION,), "fixed_axis")
        if flags & PmxBoneFlag.LOCAL_AXIS:
            data["local_axis_x"] = self._agreed_vector_alias(joint, local_x)
            data["local_axis_z"] = self._agreed_vector_alias(joint, local_z)
        else:
            self._reject_non_default(joint, ATTR_MMD_LOCAL_X_AXIS, (1.0, 0.0, 0.0), "local_axis")
            self._reject_non_default(joint, ATTR_MMD_LOCAL_Z_AXIS, (0.0, 0.0, 1.0), "local_axis")
            self._reject_present(
                joint, (ATTR_MMD_X_AXIS_DIRECTION, ATTR_MMD_Z_AXIS_DIRECTION), "local_axis"
            )

    def _read_external_parent(self, joint: str, flags: int, data: dict[str, Any]) -> None:
        if flags & PmxBoneFlag.EXTERNAL_PARENT_DEFORM:
            data["external_parent_key"] = self._read.required_int(
                joint, ATTR_MMD_EXTERNAL_PARENT_KEY
            )
        else:
            self._reject_non_default(joint, ATTR_MMD_EXTERNAL_PARENT_KEY, -1, "external_parent_key")

    def _read_ik(
        self,
        joint: str,
        flags: int,
        data: dict[str, Any],
        references: Mapping[str, set[int]],
    ) -> None:
        if not flags & PmxBoneFlag.IK:
            self._reject_non_default(joint, ATTR_MMD_IK_TARGET_INDEX, None, "IK payload")
            self._reject_non_default(joint, ATTR_MMD_IK_TARGET, "", "IK payload")
            self._reject_non_default(joint, ATTR_MMD_IK_LOOP, 10, "IK payload")
            self._reject_non_default(joint, ATTR_MMD_IK_LIMIT_ANGLE, 2.0, "IK payload")
            self._reject_non_default(joint, ATTR_MMD_IK_LINKS, "[]", "IK payload")
            return
        data["ik_target_index"] = self._resolve_reference(
            joint, (ATTR_MMD_IK_TARGET_INDEX,), ATTR_MMD_IK_TARGET, references
        )
        data["ik_loop_count"] = self._read.required_int(joint, ATTR_MMD_IK_LOOP, minimum=0)
        data["ik_limit_radian"] = self._read.required_number(joint, ATTR_MMD_IK_LIMIT_ANGLE)
        raw_links = self._read.required(joint, ATTR_MMD_IK_LINKS)
        if isinstance(raw_links, str):
            try:
                raw_links = json.loads(raw_links)
            except (TypeError, ValueError) as exc:
                raise self._error(
                    f"{joint}.{ATTR_MMD_IK_LINKS} must contain JSON list: {exc}"
                ) from exc
        if isinstance(raw_links, (str, bytes, bytearray)) or not isinstance(raw_links, Sequence):
            raise self._error(f"{joint}.{ATTR_MMD_IK_LINKS} must be a JSON/list payload")
        if not all(isinstance(link, Mapping) for link in raw_links):
            raise self._error(f"{joint}.{ATTR_MMD_IK_LINKS} entries must be mappings")
        data["ik_links"] = list(raw_links)

    @staticmethod
    def _build_references(metadata: Sequence[Mapping[str, Any]]) -> dict[str, set[int]]:
        references: dict[str, set[int]] = {}
        for item in metadata:
            index = item["index"]
            binding = item["binding_identity"]
            for alias in (binding, binding.rsplit("|", 1)[-1], item["name"], item["name_english"]):
                if alias:
                    references.setdefault(alias, set()).add(index)
        return references

    def _resolve_reference(
        self,
        joint: str,
        numeric_attrs: tuple[str, ...],
        name_attr: str,
        references: Mapping[str, set[int]],
        *,
        minimum: int = 0,
    ) -> int:
        numeric = self._agreed_int_alias(joint, numeric_attrs, minimum=minimum, required=False)
        name_value = None
        if self._read.has_attr(joint, name_attr):
            name_value = self._read.required_string(joint, name_attr)
            if not name_value:
                name_value = None
        named = None
        if name_value is not None:
            matches = references.get(name_value, set())
            if len(matches) != 1:
                problem = "unknown" if not matches else "ambiguous"
                raise self._error(
                    f"{joint}.{name_attr} has {problem} bone alias {name_value!r}"
                )
            named = next(iter(matches))
        if numeric is None and named is None:
            raise self._error(f"{joint}: missing required bone reference")
        if numeric is not None and named is not None and numeric != named:
            raise self._error(f"{joint}: conflicting numeric and name bone references")
        return numeric if numeric is not None else named  # type: ignore[return-value]

    def _agreed_int_alias(
        self,
        joint: str,
        attrs: tuple[str, ...],
        *,
        minimum: int,
        required: bool = True,
    ) -> int | None:
        values = [
            (attr, self._read.required_int(joint, attr, minimum=minimum))
            for attr in attrs
            if self._read.has_attr(joint, attr)
        ]
        if not values:
            if required:
                raise self._error(f"{joint}: missing required alias fields {attrs!r}")
            return None
        if len({value for _, value in values}) != 1:
            raise self._error(f"{joint}: conflicting alias fields {attrs!r}")
        return values[0][1]

    def _agreed_vector_alias(
        self, joint: str, attrs: tuple[str, ...]
    ) -> tuple[float, float, float]:
        values = [
            (attr, self._read.required_vector(joint, attr))
            for attr in attrs
            if self._read.has_attr(joint, attr)
        ]
        if not values:
            raise self._error(f"{joint}: missing required alias fields {attrs!r}")
        if len({value for _, value in values}) != 1:
            raise self._error(f"{joint}: conflicting alias fields {attrs!r}")
        return values[0][1]

    def _reject_present(self, node: str, attrs: tuple[str, ...], field: str) -> None:
        present = [attr for attr in attrs if self._read.has_attr(node, attr)]
        if present:
            raise self._error(f"{node}: stale {field} fields present: {present!r}")

    def _reject_non_default(self, node: str, attr: str, default: Any, field: str) -> None:
        if not self._read.has_attr(node, attr):
            return
        value = self._read.required(node, attr)
        if default is None:
            matches = False
        elif isinstance(default, tuple):
            try:
                matches = self._read.required_vector(node, attr) == default
            except Exception:
                matches = False
        else:
            matches = value == default and type(value) is type(default)
        if not matches:
            raise self._error(f"{node}: stale {field} field {attr!r} has non-default payload")

    def _require_selected_bone(self, root: str, joint: str, index: int | None) -> int:
        """Validate selected-joint ownership using only root/path/index attrs."""
        if not self._read.call_adapter("object_exists", joint):
            raise self._error(f"selected bone does not exist: {joint!r}")
        if joint == root or not joint.startswith(root.rstrip("|") + "|"):
            raise self._error(f"selected bone {joint!r} is not owned by root {root!r}")
        observed = self._read.required_int(joint, ATTR_MMD_BONE_INDEX, minimum=0)
        if index is not None and observed != index:
            raise self._error(f"selected bone index mismatch: expected {index}, got {observed}")
        return observed


__all__ = ["MayaBoneMetadataRepository"]
