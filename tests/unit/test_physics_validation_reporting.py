"""Pure-Python coverage for Physics presenter validation reporting."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from maya import cmds  # noqa: E402

from mmd_tools.core.physics_form_validation import PhysicsFormValidationError  # noqa: E402
from mmd_tools.ui.presenters.physics_presenter import PhysicsPresenter, UITranslator  # noqa: E402


class _Translator:
    def translate(self, key, category):
        return {
            ("mass", "fields"): "Mass:",
            ("translation_limit_min", "fields"): "Translation Min:",
            ("physics_validation_minimum", "messages"): "must be at least {minimum}",
            ("physics_validation_maximum", "messages"): "must be at most {maximum}",
            ("physics_validation_error", "messages"): "{field}: {reason}",
        }[(key, category)]


class TestPhysicsValidationReporting(unittest.TestCase):
    """Reporting only uses presenter state, translator, and warning output."""

    def test_localized_error_is_emitted_to_status_bar_and_script_editor(self):
        status_messages = []
        presenter = object.__new__(PhysicsPresenter)
        presenter.app_state = SimpleNamespace(emit_status=status_messages.append)
        error = PhysicsFormValidationError("mass", "physics_validation_minimum", minimum=0.0)

        with patch.object(UITranslator, "instance", return_value=_Translator()), patch.object(cmds, "warning") as warning:
            message = presenter._report_validation_error(error)

        self.assertEqual(message, "Mass: must be at least 0.0")
        self.assertEqual(status_messages, [message])
        warning.assert_called_once_with(message)

    def test_componentwise_limit_error_uses_existing_localized_maximum_message(self):
        status_messages = []
        presenter = object.__new__(PhysicsPresenter)
        presenter.app_state = SimpleNamespace(emit_status=status_messages.append)
        error = PhysicsFormValidationError(
            "translation_limit_min",
            "physics_validation_maximum",
            maximum=0.0,
        )

        with patch.object(UITranslator, "instance", return_value=_Translator()), patch.object(cmds, "warning") as warning:
            message = presenter._report_validation_error(error)

        self.assertEqual(message, "Translation Min: must be at most 0.0")
        self.assertEqual(status_messages, [message])
        warning.assert_called_once_with(message)
