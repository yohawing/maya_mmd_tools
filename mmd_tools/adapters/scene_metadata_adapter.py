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

from mmd_tools.core.model_authoring_spec import (
    SCHEMA_VERSION,
    MmdBoneSpec,
    MmdMaterialSpec,
    MmdModelAuthoringSpec,
    MmdModelSpec,
    MmdMorphSpec,
)


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


class SceneMetadataAdapter:
    """Build immutable authoring specs from an injected normalized backend."""

    def __init__(self, backend: SceneMetadataBackend) -> None:
        self._backend = backend

    def write_spec(self, model_root: str, spec: MmdModelAuthoringSpec) -> None:
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
        if not isinstance(spec, MmdModelAuthoringSpec):
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

    def read_spec(self, model_root: str) -> MmdModelAuthoringSpec:
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
        bones = self._read_collection(model_root, "bones", "iter_bone_metadata", MmdBoneSpec.from_mapping)
        materials = self._read_collection(
            model_root,
            "materials",
            "iter_material_metadata",
            MmdMaterialSpec.from_mapping,
        )
        morphs = self._read_collection(model_root, "morphs", "iter_morph_metadata", MmdMorphSpec.from_mapping)
        try:
            return MmdModelAuthoringSpec(
                schema_version=SCHEMA_VERSION,
                model=model,
                bones=tuple(sorted(bones, key=lambda item: item.index)),
                materials=tuple(sorted(materials, key=lambda item: item.index)),
                morphs=tuple(sorted(morphs, key=lambda item: item.index)),
            )
        except Exception as exc:
            raise SceneMetadataError(f"invalid semantic metadata for model root {model_root!r}: {exc}") from exc

    def _read_model(self, model_root: str) -> MmdModelSpec:
        try:
            reader = getattr(self._backend, "read_model_metadata")
            raw = reader(model_root)
        except Exception as exc:
            raise SceneMetadataError(f"failed to read model metadata for root {model_root!r}: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise SceneMetadataError(f"model metadata for root {model_root!r} must be a mapping")
        try:
            return MmdModelSpec.from_mapping(raw)
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
