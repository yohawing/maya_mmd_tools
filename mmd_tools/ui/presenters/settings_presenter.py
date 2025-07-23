from ...core.logger import get_logger
from ... import settings

logger = get_logger(__name__)

class SettingsPresenter:
    def __init__(self, view):
        self.view = view
        self.load_settings()
        self.connect_signals()

    def connect_signals(self):
        self.view.language_combo.currentTextChanged.connect(self.save_settings)
        self.view.log_level_combo.currentTextChanged.connect(self.save_settings)
        self.view.import_physics_check.stateChanged.connect(self.save_settings)

    def load_settings(self):
        # Disconnect signals to prevent feedback loops
        self.view.language_combo.currentTextChanged.disconnect(self.save_settings)
        self.view.log_level_combo.currentTextChanged.disconnect(self.save_settings)
        self.view.import_physics_check.stateChanged.disconnect(self.save_settings)

        language = settings.get("general.language", "English")
        self.view.language_combo.setCurrentText(language)

        log_level = settings.get("logging.level", "INFO")
        self.view.log_level_combo.setCurrentText(log_level)

        import_physics = settings.get("import.physics.import_physics", True)
        self.view.import_physics_check.setChecked(import_physics)

        # Reconnect signals
        self.view.language_combo.currentTextChanged.connect(self.save_settings)
        self.view.log_level_combo.currentTextChanged.connect(self.save_settings)
        self.view.import_physics_check.stateChanged.connect(self.save_settings)

    def save_settings(self):
        settings.set("general.language", self.view.language_combo.currentText())
        settings.set("logging.level", self.view.log_level_combo.currentText())
        settings.set("import.physics.import_physics", self.view.import_physics_check.isChecked())
        settings.save()
        logger.info("Settings saved.")
