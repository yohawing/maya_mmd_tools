"""Injected action boundary for creating a product MMD model template."""

from __future__ import annotations

from typing import Any

from dataclasses import dataclass


class CreateModelActionError(RuntimeError):
    """Raised when the Create Model action has no safe injected boundary."""


@dataclass(frozen=True)
class CreateModelRequest:
    """Validated UI request passed across the Create Model action boundary."""

    template_id: str
    model_name: str
    model_name_english: str = ""


class CreateModelAction:
    """Delegate Create Model UI requests to an explicitly injected initializer."""

    def __init__(self, initializer: Any | None = None) -> None:
        self._initializer = initializer

    def execute(
        self,
        request: CreateModelRequest,
    ) -> Any:
        """Create one model or fail safely when Maya wiring was not injected."""
        if not isinstance(request, CreateModelRequest):
            raise CreateModelActionError("Create Model requires a CreateModelRequest")
        initializer = self._initializer
        if initializer is None or not callable(getattr(initializer, "create", None)):
            raise CreateModelActionError("Create Model requires an injected template initializer")
        try:
            return initializer.create(request.template_id, request.model_name, request.model_name_english)
        except CreateModelActionError:
            raise
        except Exception as exc:
            raise CreateModelActionError(f"Create Model failed: {exc}") from exc


def execute_create_model(
    initializer: Any | None,
    request: CreateModelRequest,
) -> Any:
    """Function form used by thin UI composition code and tests."""
    return CreateModelAction(initializer).execute(request)


__all__ = ["CreateModelAction", "CreateModelActionError", "CreateModelRequest", "execute_create_model"]
