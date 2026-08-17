"""Fail-closed runtime witnesses for lightweight Qt action coverage.

The coverage report's ``action_count`` must come from a handler invocation,
never from a fixture default.  Wrap the handler before connecting it to the Qt
signal, exercise the visible/enabled control, then build its surface witness.
"""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable, Dict, Optional


class ActionInvocationSpy:
    """Callable wrapper that records how often one action handler ran."""

    def __init__(
        self, action_name: str, handler: Callable[..., Any], source_control: Any
    ) -> None:
        if not action_name.strip():
            raise ValueError("action_name must be non-empty")
        if not callable(handler):
            raise TypeError("handler must be callable")
        self.action_name = action_name
        self._handler = handler
        if source_control is None:
            raise ValueError("source_control must be provided")
        self.source_control = source_control
        self._coverage_source_required = True
        self.calls = []  # type: list[tuple[tuple[Any, ...], Dict[str, Any]]]

    @property
    def action_count(self) -> int:
        return len(self.calls)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, dict(kwargs)))
        return self._handler(*args, **kwargs)

    @classmethod
    def wrap(
        cls, action_name: str, handler: Callable[..., Any], source_control: Any
    ) -> "ActionInvocationSpy":
        """Return a metadata-preserving wrapper suitable for signal.connect."""
        spy = cls(action_name, handler, source_control)
        wraps(handler)(spy)
        return spy


class PreconstructionMethodSpy:
    """Wrap a production class method before a view binds its Qt signals."""

    def __init__(self, action_name: str, owner: Any, method_name: str) -> None:
        if not action_name.strip():
            raise ValueError("action_name must be non-empty")
        original = getattr(owner, method_name)
        if not callable(original):
            raise TypeError("handler must be callable")
        self.action_name = action_name
        self.owner = owner
        self.method_name = method_name
        self.original = original
        self.source_control = None
        self._coverage_source_required = True
        self.calls = []  # type: list[tuple[tuple[Any, ...], Dict[str, Any]]]

    @property
    def action_count(self) -> int:
        return len(self.calls)

    def wrapper(self, instance: Any, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(((instance,) + args, dict(kwargs)))
        return self.original(instance, *args, **kwargs)

    def install(self, monkeypatch: Any) -> "PreconstructionMethodSpy":
        spy = self

        @wraps(self.original)
        def wrapped(instance: Any, *args: Any, **kwargs: Any) -> Any:
            return spy.wrapper(instance, *args, **kwargs)

        monkeypatch.setattr(self.owner, self.method_name, wrapped)
        return self


class QtSignalInvocationSpy:
    """Count a control's primary Qt action signal through a real connection."""

    def __init__(self, action_name: str, signal: Any, source_control: Any) -> None:
        if not action_name.strip():
            raise ValueError("action_name must be non-empty")
        if source_control is None:
            raise ValueError("source_control must be provided")
        self.action_name = action_name
        self.calls = []  # type: list[tuple[Any, ...]]
        self._signal = signal
        self.source_control = source_control
        self._coverage_source_required = True
        signal.connect(self._record)

    @property
    def action_count(self) -> int:
        return len(self.calls)

    def _record(self, *args: Any) -> None:
        self.calls.append(args)

    def stop(self) -> "QtSignalInvocationSpy":
        """Freeze the measured delta before later actions reuse the signal."""
        self._signal.disconnect(self._record)
        return self


def build_surface_witness(
    *,
    surface_id: str,
    case_id: str,
    interaction: str,
    oracle: str,
    action_spy: Any,
    control: Any,
    interaction_control: Any = None,
    control_ready: Optional[bool] = None,
    interaction_ready: Optional[bool] = None,
    selector: Optional[str] = None,
    attribute: Optional[str] = None,
    status: str = "pass",
) -> Dict[str, Any]:
    """Build the existing report schema from a measured handler invocation.

    A witness is evidence only after its interaction and oracle completed.  A
    blocked/not-run surface, a missing handler call, or duplicate dispatch is
    rejected rather than being serialised as a passing surface.
    """
    locators = [value for value in (selector, attribute) if value]
    if len(locators) != 1:
        raise AssertionError("runtime witness requires exactly one locator")
    if status != "pass":
        raise AssertionError("runtime witness status must be pass")
    interaction_control = interaction_control or control
    source_control = getattr(action_spy, "source_control", None)
    if getattr(action_spy, "_coverage_source_required", False) and source_control is None:
        raise AssertionError("action spy source control is required")
    if source_control is not None and source_control is not interaction_control:
        raise AssertionError("action spy source must match the interaction control")
    control_ready = (
        bool(control.isVisible() and control.isEnabled())
        if control_ready is None
        else control_ready
    )
    interaction_ready = (
        bool(interaction_control.isVisible() and interaction_control.isEnabled())
        if interaction_ready is None
        else interaction_ready
    )
    if not control_ready:
        raise AssertionError("runtime witness control must be visible and enabled")
    if not interaction_ready:
        raise AssertionError("runtime witness interaction control must be visible and enabled")
    if action_spy.action_count != 1:
        raise AssertionError(
            "action handler must fire exactly once: {} fired {} time(s)".format(
                action_spy.action_name, action_spy.action_count
            )
        )
    for field_name, value in (
        ("surface_id", surface_id),
        ("case_id", case_id),
        ("interaction", interaction),
        ("oracle", oracle),
    ):
        if not value.strip():
            raise AssertionError("{} must be non-empty".format(field_name))

    witness = {
        "surface_id": surface_id,
        "case_id": case_id,
        "status": "pass",
        "runtime_witness": {
            "interaction": interaction,
            "fired_action": action_spy.action_name,
            "oracle": oracle,
            "action_count": action_spy.action_count,
        },
    }  # type: Dict[str, Any]
    if selector is not None:
        witness["selector"] = selector
    else:
        witness["attribute"] = attribute
    return witness
