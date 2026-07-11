"""GUI capture of the actual MMD Tools Physics tab with imported physics.

Host side launches Maya GUI with a commandPort (powershell/Start-Process on
Windows by default to avoid direct-console licensing failures). Maya side loads
the plugin, imports the hair physics fixture, opens the real MainWindow Physics
tab with a selected rigid body, grabs the tab widget to PNG, and writes
diagnostics proving non-blank pixels and populated list/details.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import struct
import subprocess
import sys
import time
import uuid
import zlib
from contextlib import contextmanager
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.common import maya_commandport

DEFAULT_MAYA_VERSION = "2024"
COMMAND_PORT = 7727
COMPLETION_MARKER = "//-- PHYSICS PANEL CAPTURE FINISHED --//"
CAPTURE_TIMEOUT = 600
LOG_POLL_INTERVAL = 1
# Installed into the launched Maya via MEL putenv + process env (BAT / env_overrides).
# Queried over commandPort before any capture/reset/quit claim.
LAUNCH_TOKEN_ENV = "MMD_PHYSICS_PANEL_LAUNCH_TOKEN"
TOKEN_PROBE_TIMEOUT = 30

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _force_utf8_stdio():
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", errors="replace", closefd=False)
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = open(sys.stderr.fileno(), mode="w", encoding="utf-8", errors="replace", closefd=False)


def _png_scanline_stats(path):
    """Return simple RGB stats for an 8-bit non-interlaced PNG using stdlib only."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Not a PNG: {0}".format(path))

    offset = 8
    width = height = color_type = bit_depth = None
    idat = bytearray()
    while offset < len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        chunk_type = data[offset + 4:offset + 8]
        chunk_data = data[offset + 8:offset + 8 + length]
        offset += 12 + length
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _compression, _filter, interlace = struct.unpack(
                ">IIBBBBB", chunk_data
            )
            if bit_depth != 8 or interlace != 0 or color_type not in (2, 6):
                raise ValueError(
                    "Unsupported PNG format: bit_depth={0}, color_type={1}, interlace={2}".format(
                        bit_depth, color_type, interlace
                    )
                )
        elif chunk_type == b"IDAT":
            idat.extend(chunk_data)
        elif chunk_type == b"IEND":
            break

    if width is None or height is None or color_type is None:
        raise ValueError("PNG header missing: {0}".format(path))

    channels = 4 if color_type == 6 else 3
    stride = width * channels
    raw = zlib.decompress(bytes(idat))
    previous = bytearray(stride)
    nonblank = 0
    min_channel = 255
    max_channel = 0
    colors = set()
    cursor = 0
    for _row in range(height):
        filter_type = raw[cursor]
        cursor += 1
        scan = bytearray(raw[cursor:cursor + stride])
        cursor += stride
        for i in range(stride):
            left = scan[i - channels] if i >= channels else 0
            up = previous[i]
            up_left = previous[i - channels] if i >= channels else 0
            if filter_type == 1:
                scan[i] = (scan[i] + left) & 0xFF
            elif filter_type == 2:
                scan[i] = (scan[i] + up) & 0xFF
            elif filter_type == 3:
                scan[i] = (scan[i] + ((left + up) >> 1)) & 0xFF
            elif filter_type == 4:
                p = left + up - up_left
                pa = abs(p - left)
                pb = abs(p - up)
                pc = abs(p - up_left)
                predictor = left if pa <= pb and pa <= pc else (up if pb <= pc else up_left)
                scan[i] = (scan[i] + predictor) & 0xFF
            elif filter_type != 0:
                raise ValueError("Unsupported PNG filter: {0}".format(filter_type))
        for i in range(0, stride, channels):
            r, g, b = scan[i], scan[i + 1], scan[i + 2]
            min_channel = min(min_channel, r, g, b)
            max_channel = max(max_channel, r, g, b)
            if len(colors) < 256:
                colors.add((r, g, b))
            if max(r, g, b) > 12:
                nonblank += 1
        previous = scan

    return {
        "width": int(width),
        "height": int(height),
        "nonblank_pixels": nonblank,
        "min_channel": min_channel,
        "max_channel": max_channel,
        "channel_range": max_channel - min_channel,
        "unique_colors_capped": len(colors),
    }


def _is_png_visually_diverse(stats):
    """Reject blank, uniform-black, and uniform-white widget grabs."""
    return (
        stats["max_channel"] >= 16
        and stats["nonblank_pixels"] >= 100
        and stats["channel_range"] >= 16
        and stats["unique_colors_capped"] >= 4
    )


def build_run_capture_command(
    project_root,
    log_path,
    model_path,
    out_png,
    diag_json,
    width=960,
    height=720,
    allow_scene_reset=False,
):
    """Build commandPort Python that calls run_capture with safe string literals.

    Paths are embedded via ``repr()`` so apostrophes, backslashes, and quotes in
    filesystem paths cannot produce invalid Python (unlike ``r'...'`` format).
    """
    return (
        "import importlib\n"
        "import sys\n"
        "from pathlib import Path\n"
        "project_root = Path({project_root})\n"
        "if str(project_root) not in sys.path:\n"
        "    sys.path.insert(0, str(project_root))\n"
        "from tests.viewport import gui_physics_panel_capture as capture_module\n"
        "capture_module = importlib.reload(capture_module)\n"
        "capture_module.run_capture("
        "{log_path}, {model_path}, {out_png}, {diag_json}, "
        "{width}, {height}, {allow_scene_reset})\n"
    ).format(
        project_root=repr(str(project_root)),
        log_path=repr(str(log_path)),
        model_path=repr(str(model_path)),
        out_png=repr(str(out_png)),
        diag_json=repr(str(diag_json)),
        width=int(width),
        height=int(height),
        allow_scene_reset=repr(bool(allow_scene_reset)),
    )


@contextmanager
def temporary_setting(settings, key, value, default=None):
    """Set a persistent settings key only for the duration of the with-block.

    Reads the previous value (via ``settings.get``), applies *value*, yields the
    previous value, and always restores in a ``finally`` — including when the
    body raises. Callers may still re-apply the previous value in an outer
    ``finally`` as an idempotent crash/hang fallback.

    *default* is forwarded to ``settings.get`` when provided (same contract as
    the capture path's optionVar reads).
    """
    if default is None:
        previous = settings.get(key)
    else:
        previous = settings.get(key, default)
    settings.set(key, value)
    try:
        yield previous
    finally:
        settings.set(key, previous)


# Persisted UI language key (must match setting_keys.UI_GENERAL_LANGUAGE).
LANGUAGE_SETTING_KEY = "ui.general.language"
CAPTURE_LANGUAGE = "en"
LANGUAGE_SETTING_DEFAULT = "ja"


def read_language_state(settings, translator, default=LANGUAGE_SETTING_DEFAULT):
    """Return prior persisted language and translator-cache language.

    ``open_main_window`` reloads the translator from the settings key, so both
    must be captured and restored independently when they diverge.
    """
    return {
        "settings": settings.get(LANGUAGE_SETTING_KEY, default),
        "translator": translator.get_language(),
    }


def apply_language_state(settings, translator, language):
    """Force both the persisted language and the translator cache."""
    settings.set(LANGUAGE_SETTING_KEY, language)
    translator.set_language(language)


def restore_language_state(settings, translator, prior_state):
    """Restore persisted language and translator cache from *prior_state*."""
    if not prior_state:
        return
    settings_lang = prior_state.get("settings")
    translator_lang = prior_state.get("translator")
    if settings_lang is not None:
        settings.set(LANGUAGE_SETTING_KEY, settings_lang)
    if translator_lang is not None:
        translator.set_language(translator_lang)


def ensure_translator_language(translator, language, window=None):
    """Ensure the translator cache is *language*; retranslate *window* if needed.

    Returns True when a retranslate was requested because the cache was wrong
    (e.g. open_main_window reloaded a non-English setting mid-capture).
    """
    current = translator.get_language()
    if current == language:
        return False
    translator.set_language(language)
    if window is not None and hasattr(window, "retranslate_all_tabs"):
        window.retranslate_all_tabs()
    return True


def _widget_is_alive(widget):
    """Return True when *widget* is a live Qt object (not deleted)."""
    if widget is None:
        return False
    try:
        # Accessing a property fails on deleted C++/sip wrappers.
        _ = widget.objectName()
        return True
    except Exception:
        return False


def find_existing_main_window(
    plugin_main_module=None,
    qapplication_cls=None,
    window_name=None,
):
    """Return a live MMD Tools MainWindow if one is already open, else None.

    Host-testable: inject *plugin_main_module* / *qapplication_cls* / *window_name*
    instead of importing Maya Qt. Prefers ``plugin_main._main_window``, then
    scans the QApplication widget tree by object name.
    """
    if window_name is None:
        try:
            from mmd_tools.ui.main_window import MainWindow

            window_name = MainWindow.WINDOW_NAME
        except Exception:
            window_name = "MMDToolsMainWindow"

    if plugin_main_module is not None:
        owned = getattr(plugin_main_module, "_main_window", None)
        if _widget_is_alive(owned):
            return owned
    else:
        try:
            from mmd_tools import plugin_main as _plugin_main

            owned = getattr(_plugin_main, "_main_window", None)
            if _widget_is_alive(owned):
                return owned
        except Exception:
            pass

    app = None
    if qapplication_cls is not None:
        try:
            app = qapplication_cls.instance()
        except Exception:
            app = None
    else:
        try:
            from mmd_tools.ui.qt_compat import QApplication

            app = QApplication.instance()
        except Exception:
            app = None

    if app is not None:
        try:
            widgets = list(app.allWidgets())
        except Exception:
            widgets = []
        for widget in widgets:
            try:
                if widget.objectName() == window_name and _widget_is_alive(widget):
                    return widget
            except Exception:
                continue
    return None


def acquire_capture_main_window(open_main_window_fn, find_existing_fn=None, log=None):
    """Acquire a MainWindow for capture without destroying a user-owned instance.

    Session ownership (whether this harness launched Maya) is independent of
    UI-window ownership. When a MainWindow is already open (typical
    ``--attach-existing``), reuse it and report ``created=False`` so cleanup
    never closes a window the harness did not create.

    Returns:
        ``(window, created)`` where *created* is True only when this harness
        opened a new window via *open_main_window_fn*.
    """
    def _log(message):
        if log is not None:
            log(message)

    finder = find_existing_fn if find_existing_fn is not None else find_existing_main_window
    existing = finder()
    if existing is not None:
        _log("reusing existing MMD Tools main window (not harness-owned)")
        return existing, False

    window = open_main_window_fn(dockable=False)
    if window is None:
        raise RuntimeError("open_main_window returned None")
    _log("opened new MMD Tools main window for capture")
    return window, True


def decide_post_capture_ui_cleanup(window_created, window=None):
    """Choose how to leave Maya UI after capture.

    Distinguishes harness-created windows from user-owned ones:

    - *window_created* True → close the disposable capture window.
    - reused existing *window* → never close; refresh language / development
      mode visibility after settings restore.
    - no live window → nothing to clean up.

    Session quit policy (``should_quit_owned_maya``) is separate from this.
    """
    if window_created:
        return "close_capture_window"
    if window is not None:
        return "refresh_existing_window"
    return "none"


def apply_post_capture_ui_cleanup(action, close_main_window_fn, window=None, log=None):
    """Execute the post-capture UI cleanup chosen by decide_post_capture_ui_cleanup.

    *close_main_window_fn* is injected so host unit tests can assert the call
    without importing Maya. ``refresh_existing_window`` retranslates a
    pre-existing user window after language / development_mode restore without
    closing it.
    """
    def _log(message):
        if log is not None:
            log(message)

    if action == "close_capture_window":
        try:
            close_main_window_fn()
            _log("closed capture-created main window after restore")
        except Exception as exc:
            _log("WARN close capture window: {0}".format(exc))
            # Last resort: try to retranslate/hide dev UI on the live window.
            if window is not None:
                try:
                    if hasattr(window, "refresh_development_mode_visibility"):
                        window.refresh_development_mode_visibility()
                    if hasattr(window, "retranslate_all_tabs"):
                        window.retranslate_all_tabs()
                    _log("refreshed existing window after close failure")
                except Exception as refresh_exc:
                    _log("WARN refresh window after close failure: {0}".format(refresh_exc))
        return

    if action == "refresh_existing_window" and window is not None:
        if hasattr(window, "refresh_development_mode_visibility"):
            window.refresh_development_mode_visibility()
        if hasattr(window, "retranslate_all_tabs"):
            window.retranslate_all_tabs()
        _log("refreshed existing main window language/dev-mode visibility")


def invalidate_stale_capture_output(out_path):
    """Remove a pre-existing capture PNG so a failed run cannot publish stale success.

    Called on the host at invocation start, before launch/attach/import. Only
    unlinks a regular file at *out_path*; directories are never removed.

    Returns:
        True when a file was removed, False when nothing was present.
    """
    path = Path(out_path)
    if not path.is_file():
        return False
    path.unlink()
    return True


def physics_panel_import_options():
    """Return ``import_mmd_file`` options that force Bullet physics generation.

    Always disables C++ fast load (``use_cpp_fast_load=False``) so a user
    ``import.native.use_cpp_fast_load`` optionVar cannot short-circuit past the
    legacy/full Physics conversion path this capture gate depends on.
    """
    return {
        "import_physics": True,
        "create_physics_joints": True,
        "create_mmd_shaders": False,
        "use_cpp_fast_load": False,
    }


def run_capture(log_path, model_path, out_png, diag_json, width=960, height=720, allow_scene_reset=False):
    """Maya-side capture entrypoint imported through commandPort."""
    import traceback

    import maya.cmds as cmds

    mmd_settings = None
    translator = None
    previous_development_mode = None
    previous_create_mmd_shaders = None
    previous_language_state = None
    capture_window = None
    capture_window_created = False
    previous_skip_shader_override = os.environ.get("MMD_TOOLS_SKIP_SHADER_OVERRIDE")
    diag_written = False

    def _log(message):
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(str(message) + "\n")
        try:
            print(message)
        except Exception:
            pass

    def _write_diag():
        """Persist diagnostics fully before any COMPLETION_MARKER is emitted."""
        nonlocal diag_written
        path = Path(diag_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        # write_text closes the handle before returning; host can then read safely.
        path.write_text(json.dumps(diag, ensure_ascii=False, indent=2), encoding="utf-8")
        diag_written = True

    diag = {
        "capture_type": "gui_physics_panel",
        "model": model_path,
        "out_png": out_png,
        "capture_failed": False,
        "maya_batch": bool(cmds.about(batch=True)),
        "allow_scene_reset": bool(allow_scene_reset),
        "failure_class": None,
    }
    try:
        _log("=== physics panel capture begin ===")
        if cmds.about(batch=True):
            raise RuntimeError("Physics panel capture requires Maya GUI (not batch/mayapy)")
        if not allow_scene_reset:
            diag["failure_class"] = "scene_reset_guard"
            raise RuntimeError(
                "Physics panel capture requires an empty disposable scene; "
                "pass --allow-scene-reset to confirm cmds.file(new=True, force=True). "
                "This is an application guard, not a Maya launch or licensing failure."
            )

        cmds.file(new=True, force=True)
        root = Path(__file__).resolve().parents[2]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))

        os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = "1"
        plugin_path = root / "mmd_tools" / "plugin_main.py"
        try:
            cmds.loadPlugin(str(plugin_path), quiet=True)
            _log("plugin loaded: {0}".format(plugin_path))
        except Exception as exc:
            _log("WARN plugin load: {0}".format(exc))

        from mmd_tools.core import settings as mmd_settings
        from mmd_tools.io.mmd_importer import import_mmd_file
        from mmd_tools.plugin_main import open_main_window
        from mmd_tools.ui.qt_compat import QApplication
        from mmd_tools.ui.translations import UITranslator

        # Minimize persistent optionVar mutation windows: each key is set only
        # for the operation that needs it, restored in a local finally, with the
        # outer finally below as an idempotent last defense.
        with temporary_setting(
            mmd_settings,
            "import.model.create_mmd_shaders",
            False,
            default=True,
        ) as previous_create_mmd_shaders:
            scene_root = import_mmd_file(
                str(model_path),
                options=physics_panel_import_options(),
            )
        # create_mmd_shaders restored here even if import raises.

        diag["imported_root"] = scene_root
        _log("imported root: {0}".format(scene_root))
        if not scene_root or not cmds.objExists(scene_root):
            raise RuntimeError("import did not produce a model root")

        bullet_shapes = cmds.listRelatives(
            scene_root,
            allDescendents=True,
            type="bulletRigidBodyShape",
            fullPath=True,
        ) or []
        constraint_shapes = cmds.listRelatives(
            scene_root,
            allDescendents=True,
            type="bulletRigidBodyConstraintShape",
            fullPath=True,
        ) or []
        diag["bullet_rigid_body_shape_count"] = len(bullet_shapes)
        diag["bullet_constraint_shape_count"] = len(constraint_shapes)
        _log(
            "bullet shapes: rigid={0} constraint={1}".format(
                len(bullet_shapes), len(constraint_shapes)
            )
        )
        if not bullet_shapes:
            raise RuntimeError("No bulletRigidBodyShape under imported root")

        translator = UITranslator.instance()
        # Capture both persisted language and translator cache before forcing EN.
        # open_main_window() reloads translator from settings, so setting only
        # the cache is not enough on a non-English Maya.
        previous_language_state = read_language_state(mmd_settings, translator)
        diag["prior_language_settings"] = previous_language_state.get("settings")
        diag["prior_language_translator"] = previous_language_state.get("translator")

        # Keep English for the entire capture (window construction + grab), not
        # only during open_main_window. temporary_setting restores the key after.
        with temporary_setting(
            mmd_settings,
            LANGUAGE_SETTING_KEY,
            CAPTURE_LANGUAGE,
            default=LANGUAGE_SETTING_DEFAULT,
        ) as previous_language_setting:
            # Align yielded prior with the snapshot (settings source of truth).
            if previous_language_state is not None:
                previous_language_state["settings"] = previous_language_setting
            translator.set_language(CAPTURE_LANGUAGE)
            diag["capture_language"] = CAPTURE_LANGUAGE
            diag["translator_language_before_window"] = translator.get_language()

            # Physics tab is development-mode only; keep the flag only while
            # constructing/showing the window that installs the tab.
            with temporary_setting(
                mmd_settings,
                "ui.general.development_mode",
                True,
                default=False,
            ) as previous_development_mode:
                # Prefer a pre-existing MainWindow (attach-existing) so we never
                # destroy/replace a user-owned UI via open_main_window.
                window, capture_window_created = acquire_capture_main_window(
                    open_main_window,
                    find_existing_fn=find_existing_main_window,
                    log=_log,
                )
                capture_window = window
                if not capture_window_created:
                    # Existing windows opened without dev mode lack Physics tab.
                    if hasattr(window, "refresh_development_mode_visibility"):
                        window.refresh_development_mode_visibility()
                    if hasattr(window, "retranslate_all_tabs"):
                        window.retranslate_all_tabs()
                window.resize(max(int(width), 900), max(int(height), 640))
                window.show()
                window.raise_()
                window.activateWindow()
                QApplication.processEvents()
            # development_mode restored here even if open/show raises.

            # open_main_window may have re-applied settings language; force EN
            # again so the PNG and labels stay English on non-English hosts.
            if ensure_translator_language(translator, CAPTURE_LANGUAGE, window=window):
                _log("re-applied capture language after open_main_window reload")
                QApplication.processEvents()
            diag["translator_language_after_window"] = translator.get_language()
            if translator.get_language() != CAPTURE_LANGUAGE:
                raise RuntimeError(
                    "Capture language is {0!r}, expected {1!r}".format(
                        translator.get_language(), CAPTURE_LANGUAGE
                    )
                )

            physics_tab = getattr(window, "physics_tab", None)
            physics_presenter = getattr(window, "physics_presenter", None)
            if physics_tab is None or physics_presenter is None:
                raise RuntimeError(
                    "Physics tab missing; development_mode may not have been applied"
                )

            # Switch to Physics tab in the main tab widget.
            tab_widget = window.tab_widget
            physics_index = tab_widget.indexOf(physics_tab)
            if physics_index < 0:
                raise RuntimeError("Physics tab is not present in the main tab widget")
            tab_widget.setCurrentIndex(physics_index)
            QApplication.processEvents()

            # Real post-import path (ImportExportPresenter): refresh available models,
            # then select the imported root so current_model_changed reloads tabs.
            #
            # MainWindow constructs HeaderWidget before PhysicsPresenter. HeaderWidget
            # already calls refresh_model_list() and may auto-select this root while no
            # physics listener is connected. A second assignment of the same root is a
            # no-op in ApplicationState, so the Physics list stays empty unless we take
            # the public Refresh route (refresh_physics / load_physics).
            window.app_state.refresh_model_list()
            available = list(window.app_state.available_models or [])
            diag["available_models"] = available
            selected_root = scene_root
            if scene_root not in available:
                # Prefer the listed form when short/long names differ.
                for model in available:
                    short = model.split("|")[-1]
                    if model == scene_root or short == scene_root or model.endswith("|" + scene_root):
                        selected_root = model
                        break
            previous_root = window.app_state.current_model_root
            window.app_state.current_model_root = selected_root
            diag["selected_model_root"] = selected_root
            diag["previous_model_root"] = previous_root
            QApplication.processEvents()

            # If root was already selected (typical after HeaderWidget auto-select),
            # force the public model-reload path used by the Physics Refresh button.
            if physics_tab.rigid_body_list.count() <= 0:
                if hasattr(physics_presenter, "refresh_physics"):
                    physics_presenter.refresh_physics()
                elif hasattr(physics_presenter, "load_physics"):
                    physics_presenter.load_physics()
                else:
                    # Last resort: clear then re-set to force current_model_changed.
                    window.app_state.current_model_root = None
                    QApplication.processEvents()
                    window.app_state.current_model_root = selected_root
                QApplication.processEvents()
                time.sleep(0.3)
                QApplication.processEvents()
            else:
                time.sleep(0.2)
                QApplication.processEvents()

            rigid_count = physics_tab.rigid_body_list.count()
            joint_count = physics_tab.joint_list.count()
            diag["rigid_list_count"] = rigid_count
            diag["joint_list_count"] = joint_count
            diag["physics_tab_index"] = physics_index
            diag["physics_tab_title"] = tab_widget.tabText(physics_index)
            diag["app_state_current_model"] = window.app_state.current_model_root
            _log(
                "lists: rigid={0} joint={1} root={2!r}".format(
                    rigid_count, joint_count, selected_root
                )
            )
            if rigid_count <= 0:
                raise RuntimeError("Physics rigid body list is empty after model set")

            # Select first rigid body so the details form shows readable values.
            physics_tab.list_tabs.setCurrentIndex(0)
            item = physics_tab.rigid_body_list.item(0)
            physics_tab.rigid_body_list.setCurrentItem(item)
            try:
                item.setSelected(True)
            except Exception:
                pass
            QApplication.processEvents()
            # Presenter listens to itemSelectionChanged; force a re-read if needed.
            if hasattr(physics_presenter, "on_rigid_body_selection_changed"):
                physics_presenter.on_rigid_body_selection_changed()
            QApplication.processEvents()
            time.sleep(0.2)
            QApplication.processEvents()

            diag["details_enabled"] = bool(physics_tab.physics_details_content.isEnabled())
            diag["detail_name"] = physics_tab.detail_name_value.text()
            diag["detail_type"] = physics_tab.detail_type_value.text()
            diag["detail_node"] = physics_tab.detail_node_value.text()
            diag["rigid_form_name"] = physics_tab.rigid_name_edit.text()
            diag["rigid_form_mass"] = physics_tab.rigid_mass_edit.text()
            diag["rigid_shape_readonly"] = not physics_tab.rigid_shape_combo.isEnabled()
            diag["apply_enabled"] = bool(physics_tab.apply_btn.isEnabled())
            diag["reset_enabled"] = bool(physics_tab.reset_btn.isEnabled())
            diag["list_tab_texts"] = [
                physics_tab.list_tabs.tabText(0),
                physics_tab.list_tabs.tabText(1),
            ]
            diag["refresh_label"] = physics_tab.refresh_btn.text()
            _log(
                "details: enabled={0} name={1!r} mass={2!r}".format(
                    diag["details_enabled"],
                    diag["detail_name"],
                    diag["rigid_form_mass"],
                )
            )
            if not diag["details_enabled"]:
                raise RuntimeError("Physics details pane stayed disabled after selection")
            if not diag["rigid_form_name"] and not diag["detail_name"]:
                raise RuntimeError("Physics form/details have no readable name after selection")

            # Capture the actual Physics tab (not an isolated offscreen widget).
            physics_tab.resize(max(int(width) - 40, 800), max(int(height) - 80, 560))
            QApplication.processEvents()
            time.sleep(0.2)
            QApplication.processEvents()

            pixmap = physics_tab.grab()
            if pixmap is None or pixmap.isNull():
                # Fallback: whole main window if tab grab fails.
                pixmap = window.grab()
                diag["grab_target"] = "main_window_fallback"
            else:
                diag["grab_target"] = "physics_tab"
            diag["pixmap_width"] = int(pixmap.width())
            diag["pixmap_height"] = int(pixmap.height())

            out = Path(out_png)
            out.parent.mkdir(parents=True, exist_ok=True)
            if out.exists():
                try:
                    out.unlink()
                except Exception:
                    pass
            ok = bool(pixmap.save(str(out), "PNG"))
            diag["png_exists"] = out.is_file()
            diag["png_size"] = out.stat().st_size if out.is_file() else 0
            diag["pixmap_save_ok"] = ok
            _log(
                "saved png: {0} size={1} ok={2} target={3}".format(
                    out, diag["png_size"], ok, diag["grab_target"]
                )
            )
            if not ok or not out.is_file() or diag["png_size"] <= 0:
                raise RuntimeError("Failed to write non-empty Physics tab PNG")
        # ui.general.language restored here (after grab) even if capture raises.
        # Final diagnostics are written in finally after cleanup state is recorded.
    except Exception:
        diag["capture_failed"] = True
        diag["exception"] = traceback.format_exc()
        if not diag.get("failure_class"):
            exception_text = str(diag["exception"])
            if "allow-scene-reset" in exception_text or "empty disposable scene" in exception_text:
                diag["failure_class"] = "scene_reset_guard"
            else:
                diag["failure_class"] = "capture_application"
        _log("EXCEPTION:\n" + str(diag["exception"]))
    finally:
        # Restore language (settings + translator cache) before UI cleanup so a
        # refresh path retranslates into the user's real language.
        try:
            if mmd_settings is not None and translator is not None and previous_language_state is not None:
                restore_language_state(mmd_settings, translator, previous_language_state)
            elif translator is not None and previous_language_state is not None:
                translator_lang = previous_language_state.get("translator")
                if translator_lang is not None:
                    translator.set_language(translator_lang)
            if mmd_settings is not None:
                if previous_development_mode is not None:
                    mmd_settings.set("ui.general.development_mode", previous_development_mode)
                if previous_create_mmd_shaders is not None:
                    mmd_settings.set("import.model.create_mmd_shaders", previous_create_mmd_shaders)
                # Idempotent language restore if temporary_setting already restored it.
                if previous_language_state is not None and previous_language_state.get("settings") is not None:
                    mmd_settings.set(
                        LANGUAGE_SETTING_KEY,
                        previous_language_state["settings"],
                    )
            # Harness-created windows are closed; user-owned windows are refreshed
            # only (never closed). Session quit is a separate ownership decision.
            cleanup_action = decide_post_capture_ui_cleanup(
                capture_window_created,
                window=capture_window,
            )
            diag["post_capture_ui_cleanup"] = cleanup_action
            diag["capture_window_created"] = bool(capture_window_created)
            if cleanup_action != "none":
                try:
                    from mmd_tools.plugin_main import close_main_window as _close_main_window
                except Exception:
                    _close_main_window = None
                if _close_main_window is not None:
                    apply_post_capture_ui_cleanup(
                        cleanup_action,
                        _close_main_window,
                        window=capture_window,
                        log=_log,
                    )
                elif capture_window is not None:
                    # Close helper unavailable: never invent a close; refresh live UI.
                    apply_post_capture_ui_cleanup(
                        "refresh_existing_window",
                        lambda: None,
                        window=capture_window,
                        log=_log,
                    )
            if previous_skip_shader_override is None:
                os.environ.pop("MMD_TOOLS_SKIP_SHADER_OVERRIDE", None)
            else:
                os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = previous_skip_shader_override
        except Exception as restore_exc:
            diag["restore_exception"] = traceback.format_exc()
            _log("WARN restore/cleanup failure: {0}".format(restore_exc))
        # Always write final diagnostics after cleanup state is recorded, then
        # emit the completion marker. Early success/failure paths must not skip
        # this rewrite (post_capture_ui_cleanup would otherwise be missing).
        try:
            _write_diag()
        except Exception as write_exc:
            _log("WARN failed to write final diag before marker: {0}".format(write_exc))
        # Signal completion only after settings, language, UI, and environment
        # state have been restored and diagnostics have been closed.
        _log(COMPLETION_MARKER)


def _tail_until_marker(log_path, timeout):
    """Poll *log_path* until COMPLETION_MARKER appears.

    Reads existing content from the start so a marker written before polling
    begins (fast scene-reset guard or fast failure) is not missed. Does not
    create the log file; waits for Maya to create it so open/create semantics
    stay host-side read-only.
    """
    start = time.time()
    while not log_path.exists() and time.time() - start < timeout:
        time.sleep(LOG_POLL_INTERVAL)
    if not log_path.exists():
        raise TimeoutError(
            "physics panel capture log did not appear within {0}s: {1}".format(
                timeout, log_path
            )
        )
    with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
        # Do not seek to EOF: marker may already be present.
        while time.time() - start < timeout:
            line = handle.readline()
            if line:
                print(line, end="")
                if COMPLETION_MARKER in line:
                    return
            else:
                time.sleep(LOG_POLL_INTERVAL)
    raise TimeoutError("physics panel capture did not finish within {0}s".format(timeout))


def _validate_diag(diag_path):
    diag = json.loads(diag_path.read_text(encoding="utf-8"))
    errors = []
    restore_exception = str(diag.get("restore_exception") or "").strip()
    if restore_exception:
        errors.append(
            "Maya-side restore/cleanup failed: {0}".format(
                restore_exception.splitlines()[-1].strip()
            )
        )
    if diag.get("capture_failed"):
        failure_class = diag.get("failure_class") or "capture_application"
        errors.append(
            "Maya-side capture_failed flag is true (failure_class={0})".format(failure_class)
        )
        exception_text = str(diag.get("exception") or "").strip()
        if exception_text:
            # Keep one short line so host logs stay readable.
            first_line = exception_text.splitlines()[-1].strip()
            errors.append("Maya-side exception: {0}".format(first_line))
        # Guard / early abort never produced capture artifacts; skip secondary noise.
        if failure_class == "scene_reset_guard":
            diag_path.write_text(json.dumps(diag, ensure_ascii=False, indent=2), encoding="utf-8")
            raise RuntimeError(
                "Physics panel capture diagnostics failed:\n- " + "\n- ".join(errors)
            )
    if diag.get("maya_batch") is True:
        errors.append("Capture ran in batch mode (need Maya GUI)")
    if int(diag.get("rigid_list_count") or 0) <= 0:
        errors.append("Physics rigid body list was empty")
    if int(diag.get("bullet_rigid_body_shape_count") or 0) <= 0:
        errors.append("No bulletRigidBodyShape nodes after import")
    if not diag.get("details_enabled"):
        errors.append("Physics details pane was not enabled")
    if not (diag.get("rigid_form_name") or diag.get("detail_name")):
        errors.append("No readable rigid body name in details/form")
    if diag.get("grab_target") not in ("physics_tab", "main_window_fallback"):
        errors.append("Unknown grab target: {0}".format(diag.get("grab_target")))

    png = Path(str(diag.get("out_png") or ""))
    if not png.is_file():
        errors.append("PNG missing: {0}".format(png))
    else:
        stats = _png_scanline_stats(png)
        diag["png_stats"] = stats
        if not _is_png_visually_diverse(stats):
            errors.append("PNG is blank-like: {0}".format(stats))
        if stats["width"] < 200 or stats["height"] < 200:
            errors.append("PNG is too small for a panel review: {0}".format(stats))
    diag_path.write_text(json.dumps(diag, ensure_ascii=False, indent=2), encoding="utf-8")
    if errors:
        raise RuntimeError("Physics panel capture diagnostics failed:\n- " + "\n- ".join(errors))
    return diag


def default_launch_mode_for_host(system_name=None):
    """Prefer explorer-style detached launch on Windows (licensing-safe).

    Non-Windows hosts use ``direct`` (explorer.exe / BAT is Windows-only).
    *system_name* is injectable for host unit tests (defaults to ``platform.system()``).
    """
    if system_name is None:
        system_name = platform.system()
    if system_name == "Windows":
        return "explorer"
    return "direct"


def _launch_mode_for_host():
    """Compatibility alias for default_launch_mode_for_host()."""
    return default_launch_mode_for_host()


def should_quit_owned_maya(leave_open, session_owned):
    """Return True only when this harness may send cmds.quit() on the capture port.

    Ownership is independent of whether a subprocess.Popen handle exists: the
    Windows explorer launch path returns None for *proc* even after a successful
    start. Callers must set *session_owned* only after a unique launch token
    installed at bootstrap is queried over commandPort and matches exactly.
    Port-already-open rejections, token mismatch/missing, and --attach-existing
    leave session_owned False so an unrelated Maya is never quit.
    """
    return (not leave_open) and bool(session_owned)


def generate_launch_token():
    """Return a unique hex token safe to embed in MEL, BAT, and env vars."""
    return uuid.uuid4().hex


def make_run_tag(port):
    """Return a collision-resistant tag for per-run log/diag/token filenames.

    Port is kept for human identification in output dirs; uniqueness comes from
    a UUID hex suffix so two same-port runs never share paths (even within the
    same second). Tags are filesystem-safe: digits, underscore, and hex only.
    """
    return "{0}_{1}".format(int(port), uuid.uuid4().hex)


def build_commandport_bootstrap_mel(port, launch_token):
    """MEL that stamps the launch token into the process env and opens commandPort.

    Token is uuid hex only (no quotes/spaces), so double-quoted MEL is safe.
    """
    return (
        'putenv "{env}" "{token}";\n'
        'commandPort -name ":{port}" -sourceType "python";\n'
    ).format(env=LAUNCH_TOKEN_ENV, token=launch_token, port=int(port))


def build_token_probe_command(token_path):
    """Build commandPort Python that writes the process launch token to *token_path*."""
    return (
        "import os\n"
        "from pathlib import Path\n"
        "Path({path}).write_text(\n"
        "    os.environ.get({env_key}) or '',\n"
        "    encoding='utf-8',\n"
        ")\n"
    ).format(path=repr(str(token_path)), env_key=repr(LAUNCH_TOKEN_ENV))


def tokens_match(expected_token, remote_token):
    """Return True only when both sides are non-empty and equal."""
    if not expected_token or remote_token is None:
        return False
    return expected_token == remote_token


def claim_session_ownership(expected_token, remote_token, port=None):
    """Raise unless *remote_token* exactly matches *expected_token*.

    Used after the port is open so a delayed/failed launch cannot claim a
    different Maya that happened to listen on the same port.
    """
    if not expected_token:
        raise RuntimeError("Launch token missing on host; refusing session ownership")
    if remote_token is None:
        raise RuntimeError(
            "Launch token missing from commandPort session{0}; refusing ownership".format(
                " :{0}".format(port) if port is not None else ""
            )
        )
    if not tokens_match(expected_token, remote_token):
        raise RuntimeError(
            "Launch token mismatch on commandPort{0}: expected {1!r}, got {2!r}. "
            "Refusing ownership; another Maya may have opened the port.".format(
                " :{0}".format(port) if port is not None else "",
                expected_token,
                remote_token,
            )
        )
    return True


def query_remote_launch_token(port, token_path, timeout=TOKEN_PROBE_TIMEOUT):
    """Ask Maya on *port* to write its launch-token env value to *token_path*."""
    token_path = Path(token_path)
    if token_path.is_file():
        try:
            token_path.unlink()
        except OSError:
            pass
    maya_commandport.send_python(
        port,
        build_token_probe_command(token_path),
        label="<physics-panel-launch-token>",
    )
    start = time.time()
    while time.time() - start < timeout:
        if token_path.is_file():
            try:
                return token_path.read_text(encoding="utf-8")
            except OSError:
                pass
        time.sleep(0.2)
    raise TimeoutError(
        "Timed out waiting for launch token probe at {0}".format(token_path)
    )


def verify_launched_session_ownership(port, expected_token, token_path, timeout=TOKEN_PROBE_TIMEOUT):
    """Query the remote token and claim ownership only on exact match."""
    remote = query_remote_launch_token(port, token_path, timeout=timeout)
    claim_session_ownership(expected_token, remote, port=port)
    return True


def _prepend_env_path(env, name, path):
    existing = env.get(name)
    env[name] = str(path) if not existing else "{0}{1}{2}".format(path, os.pathsep, existing)


def _launch_maya_for_capture(version, project_root, output_dir, port, launch_mode, launch_token):
    """Launch Maya GUI with a Python commandPort and a unique ownership token.

    ``explorer`` writes a tiny .mel bootstrap and starts Maya detached / via
    explorer-friendly process creation so Autodesk licensing does not fail the
    way a direct console-child maya.exe spawn can.

    *launch_token* is installed through process env (and MEL putenv for the
    explorer/script bootstrap path) so the host can prove which Maya opened
    the port before claiming ownership.
    """
    if not launch_token:
        raise ValueError("launch_token is required for an owned Maya launch")
    env_overrides = {
        "MMD_TOOLS_SKIP_SHADER_OVERRIDE": "1",
        LAUNCH_TOKEN_ENV: str(launch_token),
    }
    if launch_mode in ("powershell", "direct"):
        return maya_commandport.launch_maya(
            version=version,
            project_root=project_root,
            output_dir=output_dir,
            port=port,
            launch_mode="powershell" if launch_mode == "powershell" else "direct",
            env_overrides=env_overrides,
        )

    # launch_mode == "explorer" (Windows preferred)
    executable = maya_commandport.maya_exe(version)
    if not executable.is_file():
        raise FileNotFoundError("maya.exe not found: {0}".format(executable))
    output_dir.mkdir(parents=True, exist_ok=True)
    bootstrap = output_dir / "physics_panel_commandport_{0}.mel".format(port)
    bootstrap.write_text(
        build_commandport_bootstrap_mel(port, launch_token),
        encoding="utf-8",
    )
    env = os.environ.copy()
    _prepend_env_path(env, "PYTHONPATH", project_root)
    _prepend_env_path(env, "MAYA_MODULE_PATH", project_root)
    env.update(env_overrides)

    # cmd `start` breaks the console-child relationship that triggers
    # ADLSDK_STATUS_LICENSE_CHECKOUT_ERROR under some automation shells.
    if platform.system() == "Windows":
        bat = output_dir / "physics_panel_launch_{0}.bat".format(port)
        bat_lines = [
            "@echo off",
            'set "PYTHONPATH={0};%PYTHONPATH%"'.format(project_root),
            'set "MAYA_MODULE_PATH={0};%MAYA_MODULE_PATH%"'.format(project_root),
            "set MMD_TOOLS_SKIP_SHADER_OVERRIDE=1",
            "set {0}={1}".format(LAUNCH_TOKEN_ENV, launch_token),
            'start "" /D "{0}" "{1}" -script "{2}"'.format(
                project_root,
                executable,
                bootstrap,
            ),
        ]
        bat.write_text("\r\n".join(bat_lines) + "\r\n", encoding="utf-8")
        # explorer.exe open of the .bat is the AGENTS.md-proven path.
        subprocess.run(
            ["explorer.exe", str(bat)],
            cwd=str(project_root),
            check=False,
            env=env,
        )
        logger.info("launched Maya via explorer .bat + -script bootstrap: %s", bat)
        return None

    stdout = (output_dir / "maya_stdout.log").open("w", encoding="utf-8", errors="replace")
    stderr = (output_dir / "maya_stderr.log").open("w", encoding="utf-8", errors="replace")
    process = subprocess.Popen(
        [str(executable), "-script", str(bootstrap)],
        cwd=str(project_root),
        env=env,
        stdout=stdout,
        stderr=stderr,
    )
    process._mmt_stdout = stdout  # type: ignore[attr-defined]
    process._mmt_stderr = stderr  # type: ignore[attr-defined]
    logger.info("launched Maya via -script bootstrap: %s", bootstrap)
    return process


def main():
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="GUI capture of MMD Tools Physics tab")
    parser.add_argument("--maya", default=DEFAULT_MAYA_VERSION)
    parser.add_argument("--model", default=str(Path("tests/data/physics/test_hair_physics.pmx")))
    parser.add_argument("--out", default="build/captures/gui-physics-panel/physics_panel.png")
    parser.add_argument("--port", type=int, default=COMMAND_PORT)
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument(
        "--attach-existing",
        action="store_true",
        help="Use an already-open commandPort instead of launching Maya.",
    )
    parser.add_argument(
        "--leave-open",
        action="store_true",
        help="Do not send cmds.quit() after capture.",
    )
    parser.add_argument(
        "--allow-scene-reset",
        action="store_true",
        help="Explicitly allow the capture to discard the current Maya scene.",
    )
    parser.add_argument(
        "--launch-mode",
        choices=("explorer", "powershell", "direct"),
        default=default_launch_mode_for_host(),
        help="Maya launch strategy (default: explorer on Windows, direct elsewhere).",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = (project_root / out_path).resolve()
    # Invalidate stale success PNG before launch/attach/import can fail so a
    # failed current run never leaves/publishes a prior capture at --out.
    if invalidate_stale_capture_output(out_path):
        logger.info("removed stale capture PNG at %s", out_path)
    model_path = Path(args.model)
    if not model_path.is_absolute():
        model_path = (project_root / model_path).resolve()
    if not model_path.is_file():
        raise FileNotFoundError("Model not found: {0}".format(model_path))

    output_dir = out_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    # Unique names avoid WinError 32 when a previous Maya still holds the log,
    # and prevent _tail_until_marker from accepting a stale completion marker.
    run_tag = make_run_tag(args.port)
    log_path = output_dir / "gui_physics_panel_capture_{0}.log".format(run_tag)
    diag_path = output_dir / "gui_physics_panel_capture_{0}.diag.json".format(run_tag)
    # Stable aliases for humans / nox consumers.
    stable_log = output_dir / "gui_physics_panel_capture.log"
    stable_diag = output_dir / "gui_physics_panel_capture.diag.json"

    maya_exe = maya_commandport.maya_exe(args.maya)
    logger.info("Maya executable: %s", maya_exe)
    stdout_path = output_dir / "maya_stdout.log"
    stderr_path = output_dir / "maya_stderr.log"
    proc = None
    # True only after launch-token handshake proves this harness owns the port.
    # Explorer launches return proc=None, so ownership cannot key off proc alone.
    # --attach-existing never becomes owned and is never quit.
    session_owned = False
    try:
        if not args.attach_existing:
            if maya_commandport.is_port_open(args.port):
                raise RuntimeError(
                    "commandPort :{0} is already open; pass --attach-existing or free the port".format(
                        args.port
                    )
                )
            launch_token = generate_launch_token()
            proc = _launch_maya_for_capture(
                version=args.maya,
                project_root=project_root,
                output_dir=output_dir,
                port=args.port,
                launch_mode=args.launch_mode,
                launch_token=launch_token,
            )
            # Do not claim ownership yet: port open alone is not proof of identity.
            maya_commandport.wait_for_port(args.port, timeout=180, process=proc)
            logger.info("commandPort :%d open; verifying launch token", args.port)
            token_path = output_dir / "gui_physics_panel_launch_token_{0}.txt".format(run_tag)
            verify_launched_session_ownership(
                args.port,
                launch_token,
                token_path,
                timeout=TOKEN_PROBE_TIMEOUT,
            )
            session_owned = True
            logger.info("session ownership verified via launch token")
        else:
            maya_commandport.wait_for_port(args.port, timeout=180, process=proc)
            logger.info("commandPort :%d open (attach-existing; not owned)", args.port)

        command = build_run_capture_command(
            project_root=project_root,
            log_path=log_path,
            model_path=model_path,
            out_png=out_path,
            diag_json=diag_path,
            width=args.width,
            height=args.height,
            allow_scene_reset=bool(not args.attach_existing or args.allow_scene_reset),
        )
        maya_commandport.send_python(args.port, command, label="<physics-panel-command>")
        logger.info("capture command sent (%d bytes)", len(command))
        _tail_until_marker(log_path, CAPTURE_TIMEOUT)
        diag = _validate_diag(diag_path)
        try:
            stable_diag.write_text(json.dumps(diag, ensure_ascii=False, indent=2), encoding="utf-8")
            if log_path.is_file():
                stable_log.write_text(log_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        except OSError as copy_exc:
            logger.warning("Could not write stable capture aliases: %s", copy_exc)
        logger.info("physics panel capture passed: %s", diag_path)
        print(json.dumps(diag, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        logger.error("physics panel capture failed: %s", exc)
        error_text = str(exc)
        maya_side = {}
        if diag_path.is_file():
            try:
                maya_side = json.loads(diag_path.read_text(encoding="utf-8"))
            except Exception:
                maya_side = {}
        combined = "\n".join(
            part
            for part in (
                error_text,
                str(maya_side.get("exception") or ""),
                str(maya_side.get("failure_class") or ""),
            )
            if part
        )
        failure_class = maya_side.get("failure_class")
        if not failure_class:
            if (
                "allow-scene-reset" in combined
                or "empty disposable scene" in combined
                or "scene_reset_guard" in combined
            ):
                failure_class = "scene_reset_guard"
            elif (
                "Timed out waiting for commandPort" in combined
                or "commandPort did not open" in combined
                or "Maya exited before commandPort" in combined
                or "commandPort :{0} is already open".format(args.port) in combined
                or "ADLSDK_STATUS_LICENSE_CHECKOUT_ERROR" in combined
                or "Launch token mismatch" in combined
                or "Launch token missing" in combined
                or "Timed out waiting for launch token" in combined
            ):
                failure_class = "maya_launch_or_port"
            else:
                failure_class = "capture_application"

        host_diag = {
            "capture_type": "gui_physics_panel",
            "capture_failed": True,
            "host_error": error_text,
            "failure_class": failure_class,
            "maya": args.maya,
            "port": args.port,
            "launch_mode": args.launch_mode,
            "attach_existing": bool(args.attach_existing),
            "allow_scene_reset": bool(args.allow_scene_reset),
            "log_path": str(log_path),
            "diag_path": str(diag_path),
        }
        if failure_class == "scene_reset_guard":
            host_diag["remediation"] = (
                "Re-run with --allow-scene-reset only on a disposable attach "
                "session. This failure is an intentional application guard, "
                "not a Maya launch or licensing problem."
            )
        elif failure_class == "maya_launch_or_port":
            host_diag["environment_blocker"] = (
                "Maya GUI commandPort did not open or Maya exited early. "
                "Automated maya.exe spawns from this shell hit Autodesk licensing "
                "(ADLSDK_STATUS_LICENSE_CHECKOUT_ERROR / exit 253). Launch Maya "
                "interactively (explorer), run "
                "commandPort -name \":{0}\" -sourceType \"python\"; then re-run "
                "with --attach-existing --leave-open."
            ).format(args.port)
        else:
            host_diag["remediation"] = (
                "Maya-side capture or host validation failed after commandPort "
                "was available. Inspect log_path / diag_path exception details; "
                "do not treat this as a Maya licensing launch failure."
            )
        try:
            if maya_side:
                # Preserve Maya-side details; host classification wins on shared keys.
                merged = dict(maya_side)
                merged.update(host_diag)
                host_diag = merged
            payload = json.dumps(host_diag, ensure_ascii=False, indent=2)
            diag_path.write_text(payload, encoding="utf-8")
            try:
                stable_diag.write_text(payload, encoding="utf-8")
            except OSError:
                pass
            print(payload)
        except Exception:
            if diag_path.is_file():
                try:
                    print(diag_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
        return 1
    finally:
        if should_quit_owned_maya(args.leave_open, session_owned):
            maya_commandport.quit_maya(args.port)
        # --leave-open: never wait/kill a launched Maya process. Only release
        # host-owned log handles so an attach session (or intentional keep-alive)
        # is not blocked for 30s and then killed.
        if proc is not None and not args.leave_open:
            try:
                proc.wait(timeout=30)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        maya_commandport.close_process_logs(proc)
        # Only dump process logs for launches we started. Attach-existing can leave
        # stale maya_stdout.log content from prior runs (including licensing noise).
        if proc is not None and not args.leave_open:
            for label, path in (("MAYA STDOUT", stdout_path), ("MAYA STDERR", stderr_path)):
                try:
                    text = path.read_text(encoding="utf-8", errors="replace").strip()
                except Exception:
                    text = ""
                if text:
                    print("\n===== {0} (tail) =====".format(label))
                    print("\n".join(text.splitlines()[-40:]))
                    print("===== end {0} =====".format(label))


if __name__ == "__main__":
    raise SystemExit(main())
