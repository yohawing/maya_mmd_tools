"""Strict semantic read/write boundary for model authoring metadata.

This adapter consumes one injected, normalized backend for an explicit model
root and builds or persists the immutable authoring contract.  Maya
enumeration, attribute alias resolution, and storage details remain owned by
the backend; this module is Maya-independent and writes only through its
explicit transaction hooks.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import copy
from typing import Any, Protocol

from mmd_tools.core import model_authoring_spec as _authoring_spec


class SceneMetadataError(ValueError):
    """Raised when canonical scene metadata cannot form a valid authoring spec."""


class SceneMetadataBackend(Protocol):
    """Normalized backend owned by Maya integration code.

    Write hooks form one small transaction: begin, apply each semantic
    section, commit, and rollback if any post-begin operation fails.
    """

    def read_model_metadata(self, model_root: str) -> Mapping[str, Any]:
        """Read canonical model metadata for one explicit model root."""

    def iter_bone_metadata(self, model_root: str) -> Iterable[Mapping[str, Any]]:
        """Iterate canonical bone metadata for one explicit model root."""

    def iter_material_metadata(self, model_root: str) -> Iterable[Mapping[str, Any]]:
        """Iterate canonical material metadata for one explicit model root."""

    def iter_morph_metadata(self, model_root: str) -> Iterable[Mapping[str, Any]]:
        """Iterate canonical morph metadata for one explicit model root."""

    def begin_write(self, model_root: str) -> None:
        """Begin a write transaction for one explicit model root."""

    def apply_model_metadata(self, model_root: str, metadata: Mapping[str, Any]) -> None:
        """Apply fresh canonical model metadata inside the transaction."""

    def apply_bone_metadata(self, model_root: str, metadata: Iterable[Mapping[str, Any]]) -> None:
        """Apply fresh canonical bone metadata inside the transaction."""

    def apply_material_metadata(self, model_root: str, metadata: Iterable[Mapping[str, Any]]) -> None:
        """Apply fresh canonical material metadata inside the transaction."""

    def apply_morph_metadata(self, model_root: str, metadata: Iterable[Mapping[str, Any]]) -> None:
        """Apply fresh canonical morph metadata inside the transaction."""

    def commit_write(self, model_root: str) -> None:
        """Commit the active write transaction."""

    def rollback_write(self, model_root: str) -> None:
        """Roll back the active write transaction."""

    def begin_material_value_patch(
        self,
        model_root: str,
        binding: str,
        old_material: _authoring_spec.MmdMaterialSpec,
        new_material: _authoring_spec.MmdMaterialSpec,
    ) -> None:
        """Begin a selected-shader-only patch transaction."""

    def read_material_value_by_index(
        self,
        model_root: str,
        index: int,
    ) -> _authoring_spec.MmdMaterialSpec:
        """Read one registry-owned material selected by PMX index."""

    def next_material_index(self, model_root: str) -> int:
        """Return the next trailing material index."""

    def begin_material_create(self, model_root: str, index: int) -> None:
        """Begin a selected-material-only create transaction."""

    def commit_material_create(
        self,
        model_root: str,
        material: _authoring_spec.MmdMaterialSpec,
    ) -> None:
        """Verify and commit a selected-material-only create transaction."""

    def read_bone_value(
        self,
        model_root: str,
        binding: str,
        index: int | None = None,
    ) -> _authoring_spec.MmdBoneSpec:
        """Read one selected bone without enumerating model metadata."""
        ...

    def begin_bone_value_patch(
        self,
        model_root: str,
        binding: str,
        old_bone: _authoring_spec.MmdBoneSpec,
        new_bone: _authoring_spec.MmdBoneSpec,
    ) -> None:
        """Begin a selected-bone-only patch transaction."""

    def begin_bone_register(
        self,
        model_root: str,
        bone: _authoring_spec.MmdBoneSpec,
    ) -> None:
        """Begin a selected-joint-only bone registration transaction."""
        ...

    def read_material_value(
        self,
        model_root: str,
        binding: str,
        index: int | None = None,
    ) -> _authoring_spec.MmdMaterialSpec:
        """Read one selected material without enumerating other metadata."""

    def read_morph_value(
        self,
        model_root: str,
        binding: str,
        index: int | None = None,
    ) -> _authoring_spec.MmdMorphSpec:
        """Read one selected morph without enumerating other metadata."""
        ...

    def begin_morph_value_patch(
        self,
        model_root: str,
        binding: str,
        old_morph: _authoring_spec.MmdMorphSpec,
        new_morph: _authoring_spec.MmdMorphSpec,
    ) -> None:
        """Begin a selected-morph-only patch transaction."""
        ...

    def commit_morph_value_patch(
        self,
        model_root: str,
        binding: str,
        morph: _authoring_spec.MmdMorphSpec,
    ) -> None:
        """Verify and commit a selected-morph-only patch transaction."""
        ...

    def commit_material_value_patch(
        self,
        model_root: str,
        binding: str,
        material: _authoring_spec.MmdMaterialSpec,
    ) -> None:
        """Verify and commit a selected-shader-only patch transaction."""

    def commit_bone_value_patch(
        self,
        model_root: str,
        binding: str,
        bone: _authoring_spec.MmdBoneSpec,
    ) -> None:
        """Verify and commit a selected-bone-only patch transaction."""
        ...

    def commit_bone_register(
        self,
        model_root: str,
        bone: _authoring_spec.MmdBoneSpec,
    ) -> None:
        """Verify and commit a selected-joint-only bone registration."""
        ...

    def commit_material_reindex(
        self,
        model_root: str,
        result: Any,
    ) -> None:
        """Verify and commit a narrow adjacent-material transaction.

        The backend validates only the narrow transaction state and must not
        invoke any full model/bone/material/morph metadata read or write hooks.
        """
        ...

    def begin_material_reindex(
        self,
        model_root: str,
        index: int,
        new_position: int,
    ) -> None:
        """Begin a narrow adjacent-material transaction."""
        ...

    def commit_morph_reindex(
        self,
        model_root: str,
        result: Any,
    ) -> None:
        """Verify and commit a narrow adjacent-morph transaction."""
        ...

    def begin_morph_reindex(
        self,
        model_root: str,
        index: int,
        new_position: int,
    ) -> None:
        """Begin a narrow adjacent-morph transaction."""
        ...

    def commit_morph_create(
        self,
        model_root: str,
        morph: _authoring_spec.MmdMorphSpec,
    ) -> None:
        """Verify and commit a narrow morph creation transaction."""
        ...

    def begin_morph_create(
        self,
        model_root: str,
        morph: _authoring_spec.MmdMorphSpec,
    ) -> int:
        """Begin a narrow morph creation transaction and return its index."""
        ...


class SceneMetadataAdapter:
    """Build immutable authoring specs from an injected normalized backend."""

    def __init__(self, backend: SceneMetadataBackend) -> None:
        self._backend = backend

    def write_spec(
        self,
        model_root: str,
        spec: _authoring_spec.MmdModelAuthoringSpec,
    ) -> None:
        """Persist one immutable authoring spec through a backend transaction.

        Validation and serialization happen before ``begin_write`` so invalid
        input produces zero backend writes.  The backend receives fresh
        mappings and explicit indices exactly as represented by ``spec``; no
        reindexing or sorting is performed on write.

        Args:
            model_root: Non-empty model-root identity.
            spec: Schema-v1 immutable authoring specification.

        Raises:
            SceneMetadataError: If input validation, backend application,
                commit, or rollback fails.
        """
        self._validate_root(model_root)
        if not isinstance(spec, _authoring_spec.MmdModelAuthoringSpec):
            raise SceneMetadataError("spec must be an MmdModelAuthoringSpec")
        try:
            payload = spec.to_mapping()
        except Exception as exc:
            raise SceneMetadataError(f"failed to serialize spec for root {model_root!r}: {exc}") from exc

        transaction_started = False
        try:
            self._write_call(model_root, "begin", "begin_write")
            transaction_started = True
            self._write_call(model_root, "model", "apply_model_metadata", payload["model"])
            self._write_call(model_root, "bones", "apply_bone_metadata", payload["bones"])
            self._write_call(model_root, "materials", "apply_material_metadata", payload["materials"])
            self._write_call(model_root, "morphs", "apply_morph_metadata", payload["morphs"])
            self._write_call(model_root, "commit", "commit_write")
        except SceneMetadataError as exc:
            if transaction_started:
                self._rollback_write(model_root, exc)
            raise
        except Exception as exc:
            error = SceneMetadataError(f"failed to write semantic metadata for root {model_root!r}: {exc}")
            if transaction_started:
                self._rollback_write(model_root, error)
            raise error from exc

    def _write_call(self, model_root: str, section: str, method_name: str, payload: Any = None) -> None:
        try:
            method = getattr(self._backend, method_name)
            if payload is None:
                method(model_root)
            else:
                # The spec owns immutable data; each hook receives a fresh
                # mutable JSON-shaped value that it may normalize or retain.
                method(model_root, copy.deepcopy(payload))
        except Exception as exc:
            raise SceneMetadataError(f"failed to write {section} metadata for root {model_root!r}: {exc}") from exc

    def _rollback_write(self, model_root: str, original_error: SceneMetadataError) -> None:
        try:
            method = getattr(self._backend, "rollback_write")
            method(model_root)
        except Exception as rollback_error:
            raise SceneMetadataError(
                f"{original_error}; rollback failed for root {model_root!r}: {rollback_error}"
            ) from rollback_error

    def begin_material_reindex(
        self,
        model_root: str,
        index: int,
        new_position: int,
    ) -> None:
        """Begin a narrow adjacent material swap."""
        self._validate_root(model_root)
        if isinstance(index, bool) or not isinstance(index, int):
            raise SceneMetadataError("material index must be an integer")
        if isinstance(new_position, bool) or not isinstance(new_position, int):
            raise SceneMetadataError("material new position must be an integer")
        try:
            self._backend.begin_material_reindex(model_root, index, new_position)
        except Exception as exc:
            raise SceneMetadataError(
                f"failed to begin material reindex for root {model_root!r}: {exc}"
            ) from exc

    def commit_material_reindex(
        self,
        model_root: str,
        result: Any,
    ) -> None:
        """Commit an adjacent material swap after narrow Maya writes.

        The backend performs a strict narrow-state check.  No semantic
        metadata section is written here; the shader/Material Morph and native
        queue writes have already happened inside the same undo chunk.
        """
        self._validate_root(model_root)
        if result is None:
            raise SceneMetadataError("material reindex commit requires a narrow result")
        try:
            self._backend.commit_material_reindex(model_root, result)
        except Exception as exc:
            raise SceneMetadataError(
                f"failed to commit material reindex for root {model_root!r}: {exc}"
            ) from exc

    def commit_morph_reindex(self, model_root: str, result: Any) -> None:
        """Commit an adjacent morph swap after strict selected readback."""
        self._validate_root(model_root)
        if result is None or not hasattr(result, "swapped_indices"):
            raise SceneMetadataError("morph reindex commit requires a narrow result")
        try:
            self._backend.commit_morph_reindex(model_root, result)
        except Exception as exc:
            raise SceneMetadataError(
                f"failed to commit morph reindex for root {model_root!r}: {exc}"
            ) from exc

    def commit_morph_create(
        self,
        model_root: str,
        morph: _authoring_spec.MmdMorphSpec,
    ) -> None:
        """Commit one narrow morph creation after strict readback."""
        self._validate_root(model_root)
        if not isinstance(morph, _authoring_spec.MmdMorphSpec):
            raise SceneMetadataError("morph creation commit requires an MmdMorphSpec")
        try:
            self._backend.commit_morph_create(model_root, morph)
        except Exception as exc:
            raise SceneMetadataError(
                f"failed to commit morph creation for root {model_root!r}: {exc}"
            ) from exc

    def commit_material_value_patch(
        self,
        model_root: str,
        binding: str,
        material: _authoring_spec.MmdMaterialSpec,
    ) -> None:
        """Commit a selected-shader value patch after strict readback."""
        self._validate_root(model_root)
        if not isinstance(binding, str) or not binding.strip():
            raise SceneMetadataError("material value patch binding must be a non-empty string")
        if not isinstance(material, _authoring_spec.MmdMaterialSpec):
            raise SceneMetadataError("material value patch requires an MmdMaterialSpec")
        try:
            self._backend.commit_material_value_patch(model_root, binding, material)
        except Exception as exc:
            raise SceneMetadataError(
                f"failed to commit material value patch for root {model_root!r}: {exc}"
            ) from exc

    def read_material_value_by_index(
        self,
        model_root: str,
        index: int,
    ) -> _authoring_spec.MmdMaterialSpec:
        """Read one selected material by index through the narrow backend."""
        self._validate_root(model_root)
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise SceneMetadataError("material index must be a non-negative integer")
        try:
            material = self._backend.read_material_value_by_index(model_root, index)
        except Exception as exc:
            raise SceneMetadataError(
                f"failed to read selected material index {index} for root {model_root!r}: {exc}"
            ) from exc
        if not isinstance(material, _authoring_spec.MmdMaterialSpec):
            raise SceneMetadataError("backend returned an invalid selected material")
        if material.index != index:
            raise SceneMetadataError("backend returned the wrong selected material index")
        return material

    def next_material_index(self, model_root: str) -> int:
        """Read only registry material indices to allocate a trailing index."""
        self._validate_root(model_root)
        try:
            index = self._backend.next_material_index(model_root)
        except Exception as exc:
            raise SceneMetadataError(
                f"failed to allocate next material index for root {model_root!r}: {exc}"
            ) from exc
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise SceneMetadataError("backend returned an invalid next material index")
        return index

    def commit_material_create(
        self,
        model_root: str,
        material: _authoring_spec.MmdMaterialSpec,
    ) -> None:
        """Commit a selected-material create after strict shader readback."""
        self._validate_root(model_root)
        if not isinstance(material, _authoring_spec.MmdMaterialSpec):
            raise SceneMetadataError("material create commit requires an MmdMaterialSpec")
        try:
            self._backend.commit_material_create(model_root, material)
        except Exception as exc:
            raise SceneMetadataError(
                f"failed to commit material create for root {model_root!r}: {exc}"
            ) from exc

    def read_bone_value(
        self,
        model_root: str,
        binding: str,
        index: int | None = None,
    ) -> _authoring_spec.MmdBoneSpec:
        """Read one selected bone through the normalized backend."""
        self._validate_root(model_root)
        if not isinstance(binding, str) or not binding.strip():
            raise SceneMetadataError("bone value binding must be a non-empty string")
        if index is not None and (isinstance(index, bool) or not isinstance(index, int)):
            raise SceneMetadataError("bone value index must be an integer")
        try:
            bone = self._backend.read_bone_value(model_root, binding, index)
        except Exception as exc:
            raise SceneMetadataError(
                f"failed to read selected bone value for root {model_root!r}: {exc}"
            ) from exc
        if not isinstance(bone, _authoring_spec.MmdBoneSpec):
            raise SceneMetadataError("backend returned an invalid selected bone")
        return bone

    def commit_bone_value_patch(
        self,
        model_root: str,
        binding: str,
        bone: _authoring_spec.MmdBoneSpec,
    ) -> None:
        """Commit a selected-bone value patch after strict readback."""
        self._validate_root(model_root)
        if not isinstance(binding, str) or not binding.strip():
            raise SceneMetadataError("bone value patch binding must be a non-empty string")
        if not isinstance(bone, _authoring_spec.MmdBoneSpec):
            raise SceneMetadataError("bone value patch requires an MmdBoneSpec")
        try:
            self._backend.commit_bone_value_patch(model_root, binding, bone)
        except Exception as exc:
            raise SceneMetadataError(
                f"failed to commit bone value patch for root {model_root!r}: {exc}"
            ) from exc

    def commit_bone_register(
        self,
        model_root: str,
        bone: _authoring_spec.MmdBoneSpec,
    ) -> None:
        """Commit a selected-joint-only registration after strict readback."""
        self._validate_root(model_root)
        if not isinstance(bone, _authoring_spec.MmdBoneSpec):
            raise SceneMetadataError("bone registration requires an MmdBoneSpec")
        try:
            self._backend.commit_bone_register(model_root, bone)
        except Exception as exc:
            raise SceneMetadataError(
                f"failed to commit bone registration for root {model_root!r}: {exc}"
            ) from exc

    def read_material_value(
        self,
        model_root: str,
        binding: str,
        index: int | None = None,
    ) -> _authoring_spec.MmdMaterialSpec:
        """Read one selected material through the normalized backend."""
        self._validate_root(model_root)
        if not isinstance(binding, str) or not binding.strip():
            raise SceneMetadataError("material value binding must be a non-empty string")
        if index is not None and (isinstance(index, bool) or not isinstance(index, int)):
            raise SceneMetadataError("material value index must be an integer")
        try:
            material = self._backend.read_material_value(model_root, binding, index)
        except Exception as exc:
            raise SceneMetadataError(
                f"failed to read selected material value for root {model_root!r}: {exc}"
            ) from exc
        if not isinstance(material, _authoring_spec.MmdMaterialSpec):
            raise SceneMetadataError("backend returned an invalid selected material")
        return material

    def read_morph_value(
        self,
        model_root: str,
        binding: str,
        index: int | None = None,
    ) -> _authoring_spec.MmdMorphSpec:
        """Read one selected morph through the normalized backend."""
        self._validate_root(model_root)
        if not isinstance(binding, str) or not binding.strip():
            raise SceneMetadataError("morph value binding must be a non-empty string")
        if index is not None and (isinstance(index, bool) or not isinstance(index, int)):
            raise SceneMetadataError("morph value index must be an integer")
        try:
            morph = self._backend.read_morph_value(model_root, binding, index)
        except Exception as exc:
            raise SceneMetadataError(
                f"failed to read selected morph value for root {model_root!r}: {exc}"
            ) from exc
        if not isinstance(morph, _authoring_spec.MmdMorphSpec):
            raise SceneMetadataError("backend returned an invalid selected morph")
        return morph

    def commit_morph_value_patch(
        self,
        model_root: str,
        binding: str,
        morph: _authoring_spec.MmdMorphSpec,
    ) -> None:
        """Commit a selected-morph value patch after strict readback."""
        self._validate_root(model_root)
        if not isinstance(binding, str) or not binding.strip():
            raise SceneMetadataError("morph value patch binding must be a non-empty string")
        if not isinstance(morph, _authoring_spec.MmdMorphSpec):
            raise SceneMetadataError("morph value patch requires an MmdMorphSpec")
        try:
            self._backend.commit_morph_value_patch(model_root, binding, morph)
        except Exception as exc:
            raise SceneMetadataError(
                f"failed to commit morph value patch for root {model_root!r}: {exc}"
            ) from exc

    def read_spec(self, model_root: str) -> _authoring_spec.MmdModelAuthoringSpec:
        """Read and strictly validate one model root's semantic metadata.

        Backend iteration order is not semantic order.  Parsed collections are
        therefore normalized by their explicit PMX indices before constructing
        the immutable contract.

        Args:
            model_root: Non-empty Maya model-root identity.  It is passed to
                every backend read unchanged.

        Returns:
            An immutable schema-v1 authoring specification.

        Raises:
            SceneMetadataError: If the root, backend output, or semantic
                payload is invalid.
        """
        self._validate_root(model_root)
        model = self._read_model(model_root)
        bones = self._read_collection(
            model_root,
            "bones",
            "iter_bone_metadata",
            _authoring_spec.MmdBoneSpec.from_mapping,
        )
        materials = self._read_collection(
            model_root,
            "materials",
            "iter_material_metadata",
            _authoring_spec.MmdMaterialSpec.from_mapping,
        )
        morphs = self._read_collection(
            model_root,
            "morphs",
            "iter_morph_metadata",
            _authoring_spec.MmdMorphSpec.from_mapping,
        )
        try:
            return _authoring_spec.MmdModelAuthoringSpec(
                schema_version=_authoring_spec.SCHEMA_VERSION,
                model=model,
                bones=tuple(sorted(bones, key=lambda item: item.index)),
                materials=tuple(sorted(materials, key=lambda item: item.index)),
                morphs=tuple(sorted(morphs, key=lambda item: item.index)),
            )
        except Exception as exc:
            raise SceneMetadataError(f"invalid semantic metadata for model root {model_root!r}: {exc}") from exc

    def _read_model(self, model_root: str) -> _authoring_spec.MmdModelSpec:
        try:
            reader = getattr(self._backend, "read_model_metadata")
            raw = reader(model_root)
        except Exception as exc:
            raise SceneMetadataError(f"failed to read model metadata for root {model_root!r}: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise SceneMetadataError(f"model metadata for root {model_root!r} must be a mapping")
        try:
            return _authoring_spec.MmdModelSpec.from_mapping(raw)
        except Exception as exc:
            raise SceneMetadataError(f"invalid model metadata for root {model_root!r}: {exc}") from exc

    @staticmethod
    def _validate_root(model_root: Any) -> None:
        if not isinstance(model_root, str) or not model_root.strip():
            raise SceneMetadataError("model_root must be a non-empty string")

    def _read_collection(self, model_root: str, section: str, reader_name: str, parser: Any) -> tuple[Any, ...]:
        try:
            reader = getattr(self._backend, reader_name)
            raw_items = reader(model_root)
            if isinstance(raw_items, (str, bytes, bytearray)):
                raise TypeError("collection must be iterable metadata mappings")
            items = tuple(raw_items)
        except SceneMetadataError:
            raise
        except Exception as exc:
            raise SceneMetadataError(f"failed to read {section} metadata for root {model_root!r}: {exc}") from exc

        parsed: list[Any] = []
        for ordinal, raw in enumerate(items):
            if not isinstance(raw, Mapping):
                raise SceneMetadataError(
                    f"invalid {section} metadata entry {ordinal} for root {model_root!r}: entry must be a mapping"
                )
            try:
                parsed.append(parser(raw))
            except Exception as exc:
                raise SceneMetadataError(
                    f"invalid {section} metadata entry {ordinal} for root {model_root!r}: {exc}"
                ) from exc
        return tuple(parsed)


__all__ = ["SceneMetadataError", "SceneMetadataBackend", "SceneMetadataAdapter"]
