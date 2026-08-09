"""Shared icon toolbar for model-authoring list operations.

The toolbar only creates controls and owns their presentation.  Presenters
continue to connect the existing button attributes and remain responsible for
all transactions and validation.
"""

from collections.abc import Callable, Iterable, Mapping

from ..qt_compat import QHBoxLayout, Qt, QWidget
from .symbol_tool_button import SymbolToolButton

CANONICAL_ACTION_ORDER = ("refresh", "create", "duplicate", "delete", "move_up", "move_down")
ACTION_SYMBOLS = {
    "refresh": "refresh",
    "create": "create",
    "duplicate": "duplicate",
    "delete": "delete",
    "move_up": "move_up",
    "move_down": "move_down",
}


def ordered_actions(actions: Iterable[str] | None = None) -> tuple[str, ...]:
    """Return canonical operations first, preserving extras in stable order."""

    requested = tuple(actions or CANONICAL_ACTION_ORDER)
    unique = tuple(dict.fromkeys(str(action) for action in requested))
    canonical = tuple(action for action in CANONICAL_ACTION_ORDER if action in unique)
    extras = tuple(action for action in unique if action not in CANONICAL_ACTION_ORDER)
    return canonical + extras


class AuthoringToolbar(QWidget):
    """Thin shared presentation component for authoring operation buttons."""

    def __init__(
        self,
        actions: Iterable[str] | None = None,
        labels: Mapping[str, str] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("AuthoringToolbar")
        self._actions = ordered_actions(actions)
        self._labels = dict(labels or {})
        self.buttons: dict[str, SymbolToolButton] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        for action in self._actions:
            label = self._labels.get(action, action.replace("_", " ").title())
            symbol = ACTION_SYMBOLS.get(action, action)
            button = SymbolToolButton(symbol, label, self)
            button.setFocusPolicy(Qt.StrongFocus)
            self.buttons[action] = button
            layout.addWidget(button)
        layout.addStretch()

    def button(self, action: str) -> SymbolToolButton:
        """Return the button for an operation id."""

        return self.buttons[str(action)]

    def set_action_text(self, action: str, text: str) -> None:
        """Update localized label, tooltip, and accessible name."""

        button = self.buttons.get(str(action))
        if button is not None:
            self._labels[str(action)] = str(text)
            button.setText(str(text))

    def set_action_enabled(
        self, action: str, enabled: bool, reason: str = "", reason_key: str = ""
    ) -> None:
        """Set an operation state and expose a localized disabled reason."""

        button = self.buttons.get(str(action))
        if button is None:
            return
        button.set_disabled_reason(reason, reason_key)
        button.setEnabled(bool(enabled))

    def retranslate(
        self,
        labels: Mapping[str, str] | None = None,
        reason_resolver: Callable[[str], str] | None = None,
    ) -> None:
        """Refresh labels without replacing icon-only presentation."""

        if labels:
            self._labels.update({str(key): str(value) for key, value in labels.items()})
        for action, button in self.buttons.items():
            button.setText(self._labels.get(action, action.replace("_", " ").title()))
            if not button.isEnabled() and button.disabled_reason_key and reason_resolver:
                key = button.disabled_reason_key
                button.set_disabled_reason(reason_resolver(key), key)


def create_authoring_toolbar(
    actions: Iterable[str] | None = None,
    labels: Mapping[str, str] | None = None,
    parent=None,
) -> AuthoringToolbar:
    """Factory used by tabs to construct a canonical operation toolbar."""

    return AuthoringToolbar(actions=actions, labels=labels, parent=parent)
