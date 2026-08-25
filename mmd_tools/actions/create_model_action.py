"""Injected action boundary for creating a product MMD model template."""

from __future__ import annotations

from typing import Any

from dataclasses import dataclass, fields, is_dataclass


class CreateModelActionError(RuntimeError):
    """Raised when the Create Model action has no safe injected boundary."""


@dataclass(frozen=True)
class CreateModelRequest:
    """Validated UI request passed across the Create Model action boundary."""

    template_id: str
    model_name: str
    model_name_english: str = ""


def normalize_create_model_request(request: Any) -> CreateModelRequest:
    """Rehydrate a strictly equivalent request from an older module generation."""
    observed_type = type(request)
    if observed_type is not CreateModelRequest:
        expected_fields = tuple(field.name for field in fields(CreateModelRequest))
        observed_fields = tuple(
            field.name for field in fields(request)
        ) if is_dataclass(request) and not isinstance(request, type) else ()
        if (
            observed_type.__module__ != CreateModelRequest.__module__
            or observed_type.__qualname__ != CreateModelRequest.__qualname__
            or observed_fields != expected_fields
        ):
            raise CreateModelActionError(
                "Create Model requires a CreateModelRequest; received "
                f"{observed_type.__module__}.{observed_type.__qualname__}"
            )
    values = (
        getattr(request, "template_id", None),
        getattr(request, "model_name", None),
        getattr(request, "model_name_english", None),
    )
    if any(not isinstance(value, str) for value in values):
        raise CreateModelActionError("CreateModelRequest fields must be strings")
    return CreateModelRequest(*values)
class CreateModelAction:
    """Delegate Create Model UI requests to an explicitly injected initializer."""

    def __init__(self, initializer: Any | None = None) -> None:
        self._initializer = initializer

    def execute(
        self,
        request: CreateModelRequest,
    ) -> Any:
        """Create one model or fail safely when Maya wiring was not injected."""
        request = normalize_create_model_request(request)
        initializer = self._initializer
        if initializer is None or not callable(getattr(initializer, "create", None)):
            raise CreateModelActionError("Create Model requires an injected template initializer")
        try:
            return initializer.create(request.template_id, request.model_name, request.model_name_english)
        except CreateModelActionError:
            raise
        except Exception as exc:
            raise CreateModelActionError(f"Create Model failed: {exc}") from exc

__all__ = [
    "CreateModelAction",
    "CreateModelActionError",
    "CreateModelRequest",
    "normalize_create_model_request",
]
