"""Host-side unit coverage for Physics panel capture helpers.

Does not require Maya. Covers path quoting / commandPort payload safety and
host-side log marker polling (including pre-written markers).
"""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.viewport import gui_physics_panel_capture as capture_module
from tests.viewport.gui_physics_panel_capture import (
    CAPTURE_LANGUAGE,
    COMPLETION_MARKER,
    LANGUAGE_SETTING_KEY,
    LAUNCH_TOKEN_ENV,
    acquire_capture_main_window,
    apply_language_state,
    apply_post_capture_ui_cleanup,
    build_commandport_bootstrap_mel,
    build_run_capture_command,
    build_token_probe_command,
    claim_session_ownership,
    decide_post_capture_ui_cleanup,
    default_launch_mode_for_host,
    ensure_translator_language,
    find_existing_main_window,
    generate_launch_token,
    invalidate_stale_capture_output,
    make_run_tag,
    physics_panel_import_options,
    read_language_state,
    restore_language_state,
    should_quit_owned_maya,
    temporary_setting,
    tokens_match,
    _is_png_visually_diverse,
)


class _FakeTranslator(object):
    """Minimal stand-in for UITranslator (host-side only)."""

    def __init__(self, language="ja"):
        self._language = language
        self.set_calls = []

    def get_language(self):
        return self._language

    def set_language(self, language):
        self.set_calls.append(language)
        self._language = language


class _FakeWindow(object):
    """Minimal MainWindow stand-in for retranslate / dev-mode refresh."""

    def __init__(self):
        self.retranslate_calls = 0
        self.refresh_dev_calls = 0

    def retranslate_all_tabs(self):
        self.retranslate_calls += 1

    def refresh_development_mode_visibility(self):
        self.refresh_dev_calls += 1


class TestPngVisualDiversity(unittest.TestCase):
    """A uniform white Qt grab must not count as a rendered panel."""

    def test_uniform_white_is_rejected(self):
        self.assertFalse(
            _is_png_visually_diverse(
                {
                    "max_channel": 255,
                    "nonblank_pixels": 640 * 480,
                    "channel_range": 0,
                    "unique_colors_capped": 1,
                }
            )
        )

    def test_multicolor_panel_is_accepted(self):
        self.assertTrue(
            _is_png_visually_diverse(
                {
                    "max_channel": 240,
                    "nonblank_pixels": 1000,
                    "channel_range": 220,
                    "unique_colors_capped": 64,
                }
            )
        )


class _FakeSettings(object):
    """Minimal stand-in for mmd_tools.core.settings (host-side only)."""

    def __init__(self, initial=None):
        self._values = dict(initial or {})
        self.ops = []

    def get(self, key, default=None):
        if key in self._values:
            return self._values[key]
        return default

    def set(self, key, value):
        self.ops.append((key, value))
        self._values[key] = value


class TestPhysicsPanelImportOptions(unittest.TestCase):
    """Capture must not depend on user import.native.use_cpp_fast_load."""

    def test_forces_legacy_full_physics_path(self):
        opts = physics_panel_import_options()
        self.assertIs(opts["use_cpp_fast_load"], False)
        self.assertIs(opts["import_physics"], True)
        self.assertIs(opts["create_physics_joints"], True)
        self.assertIs(opts["create_mmd_shaders"], False)

    def test_returns_fresh_dict_so_callers_cannot_mutate_module_defaults(self):
        first = physics_panel_import_options()
        first["use_cpp_fast_load"] = True
        second = physics_panel_import_options()
        self.assertIs(second["use_cpp_fast_load"], False)
        self.assertIs(second["import_physics"], True)

    def test_run_capture_source_uses_helper(self):
        """Guard regression: import must call physics_panel_import_options()."""
        source = Path(capture_module.__file__).read_text(encoding="utf-8")
        self.assertIn("options=physics_panel_import_options()", source)
        self.assertIn('"use_cpp_fast_load": False', source)


class TestTemporarySetting(unittest.TestCase):
    """Contract for minimized optionVar mutation windows (no Maya)."""

    def test_restores_previous_on_normal_exit(self):
        settings = _FakeSettings({"import.model.create_mmd_shaders": True})
        with temporary_setting(
            settings, "import.model.create_mmd_shaders", False, default=True
        ) as previous:
            self.assertIs(previous, True)
            self.assertIs(settings.get("import.model.create_mmd_shaders"), False)
        self.assertIs(settings.get("import.model.create_mmd_shaders"), True)
        self.assertEqual(
            settings.ops,
            [
                ("import.model.create_mmd_shaders", False),
                ("import.model.create_mmd_shaders", True),
            ],
        )

    def test_restores_previous_on_exception(self):
        settings = _FakeSettings({"ui.general.development_mode": False})
        with self.assertRaises(RuntimeError):
            with temporary_setting(
                settings, "ui.general.development_mode", True, default=False
            ) as previous:
                self.assertIs(previous, False)
                self.assertIs(settings.get("ui.general.development_mode"), True)
                raise RuntimeError("ui construction failed")
        self.assertIs(settings.get("ui.general.development_mode"), False)
        self.assertEqual(
            settings.ops,
            [
                ("ui.general.development_mode", True),
                ("ui.general.development_mode", False),
            ],
        )

    def test_mutation_window_does_not_span_adjacent_operations(self):
        """Import override ends before UI override begins (capture contract)."""
        settings = _FakeSettings(
            {
                "import.model.create_mmd_shaders": True,
                "ui.general.development_mode": False,
            }
        )
        observed = []

        with temporary_setting(
            settings, "import.model.create_mmd_shaders", False, default=True
        ):
            observed.append(
                (
                    "during_import",
                    settings.get("import.model.create_mmd_shaders"),
                    settings.get("ui.general.development_mode"),
                )
            )
        observed.append(
            (
                "between",
                settings.get("import.model.create_mmd_shaders"),
                settings.get("ui.general.development_mode"),
            )
        )
        with temporary_setting(
            settings, "ui.general.development_mode", True, default=False
        ):
            observed.append(
                (
                    "during_ui",
                    settings.get("import.model.create_mmd_shaders"),
                    settings.get("ui.general.development_mode"),
                )
            )
        observed.append(
            (
                "after",
                settings.get("import.model.create_mmd_shaders"),
                settings.get("ui.general.development_mode"),
            )
        )

        self.assertEqual(
            observed,
            [
                ("during_import", False, False),
                ("between", True, False),
                ("during_ui", True, True),
                ("after", True, False),
            ],
        )

    def test_uses_default_when_key_missing(self):
        settings = _FakeSettings()
        with temporary_setting(
            settings, "ui.general.development_mode", True, default=False
        ) as previous:
            self.assertIs(previous, False)
            self.assertIs(settings.get("ui.general.development_mode"), True)
        self.assertIs(settings.get("ui.general.development_mode"), False)


class TestCaptureLanguageState(unittest.TestCase):
    """Language must stay consistent with settings + translator across open_main_window."""

    def test_read_apply_restore_roundtrip_when_settings_and_cache_match(self):
        settings = _FakeSettings({LANGUAGE_SETTING_KEY: "ja"})
        translator = _FakeTranslator("ja")
        prior = read_language_state(settings, translator)
        self.assertEqual(prior, {"settings": "ja", "translator": "ja"})

        apply_language_state(settings, translator, CAPTURE_LANGUAGE)
        self.assertEqual(settings.get(LANGUAGE_SETTING_KEY), "en")
        self.assertEqual(translator.get_language(), "en")

        restore_language_state(settings, translator, prior)
        self.assertEqual(settings.get(LANGUAGE_SETTING_KEY), "ja")
        self.assertEqual(translator.get_language(), "ja")

    def test_restore_keeps_divergent_settings_and_translator_independent(self):
        """Persisted language and cache can diverge; restore both independently."""
        settings = _FakeSettings({LANGUAGE_SETTING_KEY: "zh-TW"})
        translator = _FakeTranslator("ja")
        prior = read_language_state(settings, translator)
        self.assertEqual(prior["settings"], "zh-TW")
        self.assertEqual(prior["translator"], "ja")

        apply_language_state(settings, translator, "en")
        restore_language_state(settings, translator, prior)
        self.assertEqual(settings.get(LANGUAGE_SETTING_KEY), "zh-TW")
        self.assertEqual(translator.get_language(), "ja")

    def test_temporary_setting_plus_translator_survives_open_main_window_reload(self):
        """Simulate open_main_window reloading translator from settings mid-capture."""
        settings = _FakeSettings({LANGUAGE_SETTING_KEY: "ja"})
        translator = _FakeTranslator("ja")
        prior = read_language_state(settings, translator)
        window = _FakeWindow()

        with temporary_setting(
            settings, LANGUAGE_SETTING_KEY, CAPTURE_LANGUAGE, default="ja"
        ):
            translator.set_language(CAPTURE_LANGUAGE)
            # open_main_window reads settings (en) then mistakenly sets ja if a
            # stale path ignored settings — ensure_translator_language fixes it.
            translator.set_language("ja")
            retranslated = ensure_translator_language(
                translator, CAPTURE_LANGUAGE, window=window
            )
            self.assertTrue(retranslated)
            self.assertEqual(translator.get_language(), "en")
            self.assertEqual(window.retranslate_calls, 1)
            # Already English: no second retranslate.
            self.assertFalse(
                ensure_translator_language(translator, CAPTURE_LANGUAGE, window=window)
            )
            self.assertEqual(window.retranslate_calls, 1)

        restore_language_state(settings, translator, prior)
        self.assertEqual(settings.get(LANGUAGE_SETTING_KEY), "ja")
        self.assertEqual(translator.get_language(), "ja")

    def test_run_capture_source_forces_language_setting_not_only_translator(self):
        """Guard: capture must mutate ui.general.language, not only the cache."""
        source = Path(capture_module.__file__).read_text(encoding="utf-8")
        self.assertIn("LANGUAGE_SETTING_KEY", source)
        self.assertIn("read_language_state", source)
        self.assertIn("restore_language_state", source)
        self.assertIn("ensure_translator_language", source)
        self.assertIn('temporary_setting(\n            mmd_settings,\n            LANGUAGE_SETTING_KEY', source)


class TestPostCaptureUiCleanup(unittest.TestCase):
    """Attach-existing must not leave English or development Physics UI visible."""

    def test_decide_closes_only_harness_created_window(self):
        self.assertEqual(
            decide_post_capture_ui_cleanup(window_created=True),
            "close_capture_window",
        )
        self.assertEqual(
            decide_post_capture_ui_cleanup(window_created=True, window=_FakeWindow()),
            "close_capture_window",
        )
        # No window and not harness-created → nothing to clean up.
        self.assertEqual(decide_post_capture_ui_cleanup(window_created=False), "none")
        self.assertEqual(
            decide_post_capture_ui_cleanup(window_created=False, window=None),
            "none",
        )

    def test_decide_refreshes_reused_user_window_without_closing(self):
        window = _FakeWindow()
        self.assertEqual(
            decide_post_capture_ui_cleanup(window_created=False, window=window),
            "refresh_existing_window",
        )

    def test_apply_close_invokes_close_main_window(self):
        calls = []
        window = _FakeWindow()
        logs = []
        apply_post_capture_ui_cleanup(
            "close_capture_window",
            lambda: calls.append("closed"),
            window=window,
            log=logs.append,
        )
        self.assertEqual(calls, ["closed"])
        self.assertEqual(window.retranslate_calls, 0)
        self.assertEqual(window.refresh_dev_calls, 0)
        self.assertTrue(any("closed capture-created" in line for line in logs))

    def test_apply_close_falls_back_to_refresh_on_close_failure(self):
        window = _FakeWindow()
        logs = []

        def boom():
            raise RuntimeError("close failed")

        apply_post_capture_ui_cleanup(
            "close_capture_window",
            boom,
            window=window,
            log=logs.append,
        )
        self.assertEqual(window.refresh_dev_calls, 1)
        self.assertEqual(window.retranslate_calls, 1)
        self.assertTrue(any("close capture window" in line for line in logs))

    def test_apply_refresh_existing_window(self):
        window = _FakeWindow()
        apply_post_capture_ui_cleanup(
            "refresh_existing_window",
            lambda: None,
            window=window,
        )
        self.assertEqual(window.refresh_dev_calls, 1)
        self.assertEqual(window.retranslate_calls, 1)

    def test_run_capture_source_records_cleanup_before_final_diag(self):
        source = Path(capture_module.__file__).read_text(encoding="utf-8")
        self.assertIn("decide_post_capture_ui_cleanup", source)
        self.assertIn("apply_post_capture_ui_cleanup", source)
        self.assertIn("capture_window_created", source)
        self.assertIn("close_main_window", source)
        self.assertIn("acquire_capture_main_window", source)
        self.assertIn("post_capture_ui_cleanup", source)
        # Final diagnostics must be written after cleanup state is recorded.
        cleanup_idx = source.index('diag["post_capture_ui_cleanup"]')
        write_idx = source.index("_write_diag()")
        marker_idx = source.index("COMPLETION_MARKER")
        # The last _write_diag / marker in run_capture finally must follow cleanup.
        # Use rindex for the final finally-path write (early intermediate writes removed).
        write_idx = source.rindex("_write_diag()")
        marker_idx = source.rindex("COMPLETION_MARKER")
        self.assertLess(cleanup_idx, write_idx)
        self.assertLess(write_idx, marker_idx)


class TestAcquireCaptureMainWindow(unittest.TestCase):
    """UI-window ownership is independent of session ownership."""

    def test_reuses_existing_window_without_calling_open(self):
        existing = _FakeWindow()
        open_calls = []

        def open_fn(dockable=False):
            open_calls.append(dockable)
            return _FakeWindow()

        window, created = acquire_capture_main_window(
            open_fn,
            find_existing_fn=lambda: existing,
        )
        self.assertIs(window, existing)
        self.assertFalse(created)
        self.assertEqual(open_calls, [])

    def test_opens_new_window_when_none_exists(self):
        created_window = _FakeWindow()
        open_calls = []

        def open_fn(dockable=False):
            open_calls.append(dockable)
            return created_window

        window, created = acquire_capture_main_window(
            open_fn,
            find_existing_fn=lambda: None,
        )
        self.assertIs(window, created_window)
        self.assertTrue(created)
        self.assertEqual(open_calls, [False])

    def test_open_returning_none_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            acquire_capture_main_window(
                lambda dockable=False: None,
                find_existing_fn=lambda: None,
            )
        self.assertIn("open_main_window returned None", str(ctx.exception))

    def test_find_existing_prefers_plugin_main_owned_window(self):
        class _Plugin(object):
            def __init__(self, window):
                self._main_window = window

        owned = _FakeWindow()
        owned.objectName = lambda: "MMDToolsMainWindow"  # type: ignore[method-assign]
        found = find_existing_main_window(
            plugin_main_module=_Plugin(owned),
            qapplication_cls=None,
            window_name="MMDToolsMainWindow",
        )
        self.assertIs(found, owned)

    def test_find_existing_scans_qapplication_when_no_plugin_owned(self):
        class _Widget(object):
            def __init__(self, name):
                self._name = name

            def objectName(self):
                return self._name

        target = _Widget("MMDToolsMainWindow")
        other = _Widget("Other")

        class _App(object):
            def allWidgets(self):
                return [other, target]

        class _QApp(object):
            @staticmethod
            def instance():
                return _App()

        found = find_existing_main_window(
            plugin_main_module=type("P", (), {"_main_window": None})(),
            qapplication_cls=_QApp,
            window_name="MMDToolsMainWindow",
        )
        self.assertIs(found, target)

    def test_find_existing_returns_none_when_absent(self):
        class _QApp(object):
            @staticmethod
            def instance():
                return None

        found = find_existing_main_window(
            plugin_main_module=type("P", (), {"_main_window": None})(),
            qapplication_cls=_QApp,
            window_name="MMDToolsMainWindow",
        )
        self.assertIsNone(found)


class TestInvalidateStaleCaptureOutput(unittest.TestCase):
    """Failed runs must not leave a prior PNG at the requested --out path."""

    def test_removes_existing_file_and_returns_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "physics_panel.png"
            out.write_bytes(b"stale-png-bytes")
            self.assertTrue(out.is_file())
            self.assertTrue(invalidate_stale_capture_output(out))
            self.assertFalse(out.exists())

    def test_missing_path_returns_false_without_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "missing.png"
            self.assertFalse(invalidate_stale_capture_output(out))
            self.assertFalse(out.exists())

    def test_does_not_remove_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "physics_panel.png"
            out_dir.mkdir()
            self.assertFalse(invalidate_stale_capture_output(out_dir))
            self.assertTrue(out_dir.is_dir())

    def test_main_source_invalidates_before_launch_or_attach(self):
        source = Path(capture_module.__file__).read_text(encoding="utf-8")
        inv_idx = source.index("invalidate_stale_capture_output(out_path)")
        # Host main must clear --out before attach/launch/import failure modes.
        attach_idx = source.index("args.attach_existing")
        launch_idx = source.index("_launch_maya_for_capture")
        self.assertLess(inv_idx, attach_idx)
        self.assertLess(inv_idx, launch_idx)


class TestDefaultLaunchModeForHost(unittest.TestCase):
    """Nox must not force explorer; harness picks platform-safe default."""

    def test_windows_prefers_explorer(self):
        self.assertEqual(default_launch_mode_for_host("Windows"), "explorer")

    def test_non_windows_uses_direct(self):
        self.assertEqual(default_launch_mode_for_host("Darwin"), "direct")
        self.assertEqual(default_launch_mode_for_host("Linux"), "direct")
        self.assertEqual(default_launch_mode_for_host("FreeBSD"), "direct")

    def test_argparse_default_uses_host_helper(self):
        source = Path(capture_module.__file__).read_text(encoding="utf-8")
        self.assertIn("default=default_launch_mode_for_host()", source)
        # Compatibility alias retained for older call sites / docs.
        self.assertIn("def _launch_mode_for_host", source)


class TestBuildRunCaptureCommand(unittest.TestCase):
    def _compile(self, source):
        return compile(source, "<physics-panel-command>", "exec")

    def test_repr_literals_handle_apostrophe_and_backslash_paths(self):
        # Machine paths without apostrophes are not enough: force the hazard cases.
        project_root = Path(r"C:\Develop\maya's_tools")
        log_path = Path(r"C:\tmp\o'reilly\capture.log")
        model_path = Path(r"F:\MMD\model's\hair.pmx")
        out_png = Path(r"C:\out\panel's.png")
        diag_json = Path(r"C:\out\panel's.diag.json")

        command = build_run_capture_command(
            project_root=project_root,
            log_path=log_path,
            model_path=model_path,
            out_png=out_png,
            diag_json=diag_json,
            width=960,
            height=720,
            allow_scene_reset=True,
        )

        # Must parse as valid Python (r'...' interpolation would break on apostrophes).
        tree = ast.parse(command)
        self.assertIsInstance(tree, ast.Module)
        self._compile(command)

        # Embedded via repr, not raw r'...' framing.
        self.assertIn(repr(str(project_root)), command)
        self.assertIn(repr(str(log_path)), command)
        self.assertIn(repr(str(model_path)), command)
        self.assertIn(repr(str(out_png)), command)
        self.assertIn(repr(str(diag_json)), command)
        self.assertNotIn("Path(r'", command)
        self.assertNotIn("run_capture(r'", command)
        self.assertIn("True", command)

    def test_allow_scene_reset_false_emits_false_literal(self):
        command = build_run_capture_command(
            project_root=Path("F:/repo"),
            log_path=Path("F:/repo/log.txt"),
            model_path=Path("F:/repo/model.pmx"),
            out_png=Path("F:/repo/out.png"),
            diag_json=Path("F:/repo/diag.json"),
            allow_scene_reset=False,
        )
        self.assertIn("False", command)
        # Last positional arg to run_capture is allow_scene_reset.
        self.assertTrue(command.rstrip().endswith("False)"))
        self._compile(command)

    @staticmethod
    def _string_constants(module):
        """Collect string literals; support both Py3.7 (ast.Str) and 3.8+ (Constant)."""
        values = []
        for node in ast.walk(module):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                values.append(node.value)
            elif isinstance(node, getattr(ast, "Str", ())):
                values.append(node.s)
        return values

    def test_roundtrip_path_values_via_ast_constants(self):
        paths = {
            "project_root": r"D:\work\user's\repo",
            "log_path": r"D:\work\user's\log.txt",
            "model_path": r"D:\assets\o'brien\a.pmx",
            "out_png": r"D:\work\user's\out.png",
            "diag_json": r"D:\work\user's\diag.json",
        }
        command = build_run_capture_command(
            project_root=paths["project_root"],
            log_path=paths["log_path"],
            model_path=paths["model_path"],
            out_png=paths["out_png"],
            diag_json=paths["diag_json"],
            width=640,
            height=480,
            allow_scene_reset=True,
        )
        module = ast.parse(command)
        string_constants = self._string_constants(module)
        for expected in paths.values():
            self.assertIn(expected, string_constants)


class TestTailUntilMarker(unittest.TestCase):
    """Host-side marker polling without real second delays."""

    def test_marker_already_present_before_polling(self):
        """Existing log must be read from the start, not sought to EOF."""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "capture.log"
            log_path.write_text(
                "=== physics panel capture begin ===\n"
                "scene_reset_guard\n"
                + COMPLETION_MARKER
                + "\n",
                encoding="utf-8",
            )
            with mock.patch.object(capture_module.time, "sleep") as sleep_mock:
                capture_module._tail_until_marker(log_path, timeout=60)
            sleep_mock.assert_not_called()

    def test_log_appears_after_polling_then_marker_appended(self):
        """Missing log is waited on (not created); later append is observed."""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "capture.log"
            self.assertFalse(log_path.exists())
            state = {"n": 0}

            def fake_sleep(_seconds):
                state["n"] += 1
                if state["n"] == 1:
                    # First poll: Maya creates the log without a marker yet.
                    log_path.write_text("=== physics panel capture begin ===\n", encoding="utf-8")
                elif state["n"] == 2:
                    # Second poll: completion after host already opened the file.
                    with log_path.open("a", encoding="utf-8") as handle:
                        handle.write(COMPLETION_MARKER + "\n")

            with mock.patch.object(capture_module.time, "sleep", side_effect=fake_sleep):
                capture_module._tail_until_marker(log_path, timeout=60)

            self.assertGreaterEqual(state["n"], 2)
            self.assertTrue(log_path.is_file())
            self.assertIn(COMPLETION_MARKER, log_path.read_text(encoding="utf-8"))

    def test_does_not_create_missing_log_on_timeout(self):
        """Waiting must not touch-create a file that changes open semantics."""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "never_created.log"
            # Advance time so the wait loop exits without real sleeps.
            times = iter([100.0, 100.1, 200.0])

            def fake_time():
                try:
                    return next(times)
                except StopIteration:
                    return 200.0

            with mock.patch.object(capture_module.time, "time", side_effect=fake_time):
                with mock.patch.object(capture_module.time, "sleep") as sleep_mock:
                    with self.assertRaises(TimeoutError) as ctx:
                        capture_module._tail_until_marker(log_path, timeout=5)
            self.assertIn("did not appear", str(ctx.exception))
            self.assertFalse(log_path.exists())
            sleep_mock.assert_called()


class TestShouldQuitOwnedMaya(unittest.TestCase):
    """Cleanup may only quit a Maya session this harness launched and owns."""

    def test_port_occupied_rejection_does_not_quit(self):
        # Launch refused before ownership is claimed (port already open).
        self.assertFalse(should_quit_owned_maya(leave_open=False, session_owned=False))

    def test_attach_existing_never_owns_session(self):
        # Attach path never sets session_owned; leave-open is orthogonal.
        self.assertFalse(should_quit_owned_maya(leave_open=False, session_owned=False))
        self.assertFalse(should_quit_owned_maya(leave_open=True, session_owned=False))

    def test_owned_launch_is_quit_unless_leave_open(self):
        # Explorer launch returns proc=None but still owns the session after token match.
        self.assertTrue(should_quit_owned_maya(leave_open=False, session_owned=True))
        self.assertFalse(should_quit_owned_maya(leave_open=True, session_owned=True))

    def test_truthy_owned_flag_is_enough(self):
        # Ownership is a boolean claim, not a process handle.
        self.assertTrue(should_quit_owned_maya(False, True))
        self.assertFalse(should_quit_owned_maya(False, None))
        self.assertFalse(should_quit_owned_maya(False, 0))

    def test_token_mismatch_leaves_session_unowned_so_no_quit(self):
        # Mismatch aborts before session_owned=True; cleanup must not quit.
        self.assertFalse(should_quit_owned_maya(leave_open=False, session_owned=False))


class TestRunTag(unittest.TestCase):
    """Per-run log/diag/token paths must never collide on the same port."""

    def test_consecutive_same_port_tags_differ(self):
        a = make_run_tag(7727)
        b = make_run_tag(7727)
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("7727_"))
        self.assertTrue(b.startswith("7727_"))

    def test_run_tag_is_filesystem_safe(self):
        tag = make_run_tag(7727)
        # Port + underscore + uuid hex only — safe on Windows/POSIX filenames.
        self.assertRegex(tag, r"^[0-9]+_[0-9a-f]+$")
        self.assertNotIn(" ", tag)
        self.assertNotIn(":", tag)
        self.assertNotIn("/", tag)
        self.assertNotIn("\\", tag)
        self.assertNotIn(".", tag)

    def test_run_tag_includes_port_and_unique_suffix(self):
        tag = make_run_tag(9001)
        port_part, suffix = tag.split("_", 1)
        self.assertEqual(port_part, "9001")
        self.assertGreaterEqual(len(suffix), 32)
        self.assertRegex(suffix, r"^[0-9a-f]+$")


class TestLaunchTokenHandshake(unittest.TestCase):
    """Unique-token ownership before capture/reset/quit."""

    def test_generate_launch_token_is_unique_hex(self):
        a = generate_launch_token()
        b = generate_launch_token()
        self.assertTrue(a)
        self.assertTrue(b)
        self.assertNotEqual(a, b)
        self.assertRegex(a, r"^[0-9a-f]+$")
        self.assertRegex(b, r"^[0-9a-f]+$")

    def test_bootstrap_mel_embeds_token_and_commandport(self):
        token = "deadbeefcafebabe0123456789abcdef"
        mel = build_commandport_bootstrap_mel(7727, token)
        self.assertIn('putenv "{0}" "{1}";'.format(LAUNCH_TOKEN_ENV, token), mel)
        self.assertIn('commandPort -name ":7727" -sourceType "python";', mel)
        # Token is hex-only; MEL must not introduce shell metacharacters.
        self.assertNotIn("'", token)

    def test_token_probe_command_embeds_path_and_env_key(self):
        path = Path(r"C:\tmp\o'reilly\token.txt")
        command = build_token_probe_command(path)
        compile(command, "<token-probe>", "exec")
        self.assertIn(repr(str(path)), command)
        self.assertIn(repr(LAUNCH_TOKEN_ENV), command)
        self.assertIn("os.environ.get", command)
        self.assertIn("write_text", command)

    def test_tokens_match_requires_exact_nonempty(self):
        token = generate_launch_token()
        self.assertTrue(tokens_match(token, token))
        self.assertFalse(tokens_match(token, token + "x"))
        self.assertFalse(tokens_match(token, ""))
        self.assertFalse(tokens_match(token, None))
        self.assertFalse(tokens_match("", ""))
        self.assertFalse(tokens_match(None, token))

    def test_claim_session_ownership_accepts_exact_match(self):
        token = generate_launch_token()
        self.assertTrue(claim_session_ownership(token, token, port=7727))

    def test_claim_session_ownership_rejects_mismatch(self):
        expected = generate_launch_token()
        remote = generate_launch_token()
        with self.assertRaises(RuntimeError) as ctx:
            claim_session_ownership(expected, remote, port=7727)
        message = str(ctx.exception)
        self.assertIn("Launch token mismatch", message)
        self.assertIn(repr(expected), message)
        self.assertIn(repr(remote), message)
        self.assertIn(":7727", message)

    def test_claim_session_ownership_rejects_missing_remote(self):
        expected = generate_launch_token()
        with self.assertRaises(RuntimeError) as ctx:
            claim_session_ownership(expected, None, port=7727)
        self.assertIn("Launch token missing", str(ctx.exception))

    def test_claim_session_ownership_rejects_empty_remote(self):
        expected = generate_launch_token()
        with self.assertRaises(RuntimeError) as ctx:
            claim_session_ownership(expected, "", port=7727)
        self.assertIn("Launch token mismatch", str(ctx.exception))

    def test_claim_session_ownership_rejects_missing_expected(self):
        with self.assertRaises(RuntimeError) as ctx:
            claim_session_ownership("", "anything", port=7727)
        self.assertIn("Launch token missing on host", str(ctx.exception))

    def test_verify_launched_session_ownership_mismatch_never_owns(self):
        """End-to-end host helper: query + claim, mismatch raises before ownership."""
        expected = generate_launch_token()
        with tempfile.TemporaryDirectory() as tmp:
            token_path = Path(tmp) / "token.txt"

            def fake_query(port, path, timeout=30):
                del port, path, timeout
                return "not-the-expected-token"

            with mock.patch.object(
                capture_module, "query_remote_launch_token", side_effect=fake_query
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    capture_module.verify_launched_session_ownership(
                        7727, expected, token_path
                    )
            self.assertIn("Launch token mismatch", str(ctx.exception))

    def test_verify_launched_session_ownership_match(self):
        expected = generate_launch_token()
        with tempfile.TemporaryDirectory() as tmp:
            token_path = Path(tmp) / "token.txt"

            def fake_query(port, path, timeout=30):
                del port, path, timeout
                return expected

            with mock.patch.object(
                capture_module, "query_remote_launch_token", side_effect=fake_query
            ):
                self.assertTrue(
                    capture_module.verify_launched_session_ownership(
                        7727, expected, token_path
                    )
                )


if __name__ == "__main__":
    unittest.main()
