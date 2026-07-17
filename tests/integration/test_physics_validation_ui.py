"""Focused user-visible validation reporting for the Physics presenter."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from maya import cmds

from mmd_tools.core.physics_form_validation import PhysicsFormValidationError
from mmd_tools.ui.presenters.physics_presenter import PhysicsPresenter, UITranslator


class _Translator:
    def translate(self, key, category):
        return {
            ("mass", "fields"): "Mass:",
            ("physics_validation_minimum", "messages"): "must be at least {minimum}",
            ("physics_validation_error", "messages"): "{field}: {reason}",
        }[(key, category)]


class TestPhysicsValidationUI(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
