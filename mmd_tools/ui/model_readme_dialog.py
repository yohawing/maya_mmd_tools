"""Shared PMX/PMD model-readme extraction and dialog policy.

The import presenters and the Maya drag-and-drop path both consume the same
root metadata.  Keeping extraction and the modal adapter here prevents the two
production entry points from drifting in their empty/comment or Development
Mode handling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from maya import cmds

from ..core.constants import ATTR_MMD_COMMENT, ATTR_MMD_COMMENT_EN


@dataclass(frozen=True)
class ModelReadme:
    """Lossless Japanese and English model comments stored on an imported root."""

    japanese: str = ""
    english: str = ""

    @property
    def has_content(self) -> bool:
        """Return whether either language contains non-whitespace text."""
        return bool(self.japanese.strip() or self.english.strip())

    def to_plain_text(self) -> str:
        """Format available languages as one selectable plain-text document."""
        sections = []
        if self.japanese.strip():
            sections.append("Japanese (JP):\n" + self.japanese)
        if self.english.strip():
            sections.append("English (EN):\n" + self.english)
        return "\n\n".join(sections)


def read_model_readme(scene_model_service: Any, model_root: Any) -> Optional[ModelReadme]:
    """Read imported PMX/PMD comments from a model root.

    ``SceneModelService.get_attr_safe`` deliberately returns the original Maya
    string.  Only the presence check strips whitespace, so displayed content is
    not normalized or otherwise changed.
    """
    if not model_root or scene_model_service is None:
        return None
    get_attr_safe = getattr(scene_model_service, "get_attr_safe", None)
    if not callable(get_attr_safe):
        return None
    japanese = get_attr_safe(model_root, ATTR_MMD_COMMENT, "")
    english = get_attr_safe(model_root, ATTR_MMD_COMMENT_EN, "")
    japanese = "" if japanese is None else str(japanese)
    english = "" if english is None else str(english)
    readme = ModelReadme(japanese=japanese, english=english)
    return readme if readme.has_content else None


def _maya_batch_default() -> bool:
    """Return Maya's batch state without treating truthy test stubs as batch."""
    try:
        # Maya returns a real bool.  ``is True`` keeps MagicMock-based unit
        # stubs from silently suppressing a test's injected adapter.
        return cmds.about(batch=True) is True
    except Exception:
        return False


class ModelReadmeDialogAdapter:
    """Show model readmes when the current host/policy permits a modal."""

    def __init__(
        self,
        *,
        development_mode_getter: Optional[Callable[[], bool]] = None,
        batch_getter: Optional[Callable[[], bool]] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        self._development_mode_getter = development_mode_getter or (lambda: False)
        self._batch_getter = batch_getter or _maya_batch_default
        self._enabled = enabled

    def should_show(self) -> bool:
        """Apply explicit skip, Development Mode, and batch-mode gates."""
        if self._enabled is False:
            return False
        try:
            if self._development_mode_getter():
                return False
        except Exception:
            # A failed settings query must not make a modal unexpectedly fatal.
            return False
        try:
            if self._batch_getter():
                return False
        except Exception:
            return False
        return True

    def show(
        self,
        readme: Optional[ModelReadme],
        *,
        model_path: str = "",
        parent: Any = None,
    ) -> bool:
        """Show one readme dialog and return whether it was displayed."""
        if readme is None or not readme.has_content or not self.should_show():
            return False

        from .qt_compat import QDialog, QLabel, QPushButton, QTextEdit, QVBoxLayout

        dialog = QDialog(parent)
        dialog.setWindowTitle("MMD Model Readme")
        layout = QVBoxLayout()
        dialog.setLayout(layout)
        if model_path:
            layout.addWidget(QLabel(model_path))

        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(readme.to_plain_text())
        text_edit.setMinimumWidth(640)
        text_edit.setMinimumHeight(400)
        layout.addWidget(text_edit)

        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button)

        exec_method = getattr(dialog, "exec", None) or getattr(dialog, "exec_", None)
        if callable(exec_method):
            exec_method()
        return True


class NoOpModelReadmeDialogAdapter(ModelReadmeDialogAdapter):
    """Explicit test/automation adapter that never opens a modal."""

    def __init__(self) -> None:
        super().__init__(enabled=False)

    def show(self, readme=None, *, model_path="", parent=None) -> bool:
        del readme, model_path, parent
        return False
