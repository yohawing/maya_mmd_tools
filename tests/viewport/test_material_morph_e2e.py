"""Headless CLI/payload tests for the material morph commandPort runner."""

import argparse
import tempfile
import unittest
from pathlib import Path

from tests.viewport.material_morph_e2e import (
    BACKENDS,
    DEFAULT_MORPHS,
    _maya_code,
    build_parser,
    exception_summary,
    is_diffuse_alpha_only_offsets,
    parse_morph,
    rgba_pixel_stats,
    safe_capture_dir,
    trace_weight_source_chains,
)


class MaterialMorphE2ETest(unittest.TestCase):
    def test_representative_defaults(self):
        self.assertEqual(((158, "制服"), (31, "瞳消し"), (143, "照れ")), DEFAULT_MORPHS)

    def test_parse_selector(self):
        self.assertEqual((158, "制服"), parse_morph("158:制服"))
        self.assertEqual((31, ""), parse_morph("31"))
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_morph("-1:bad")

    def test_backend_and_lifecycle_cli(self):
        args = build_parser().parse_args(["--model", "fixture.pmx", "--backend", "glsl", "--leave-open"])
        self.assertEqual("VirtualDeviceGLCore", BACKENDS[args.backend]["device"])
        self.assertTrue(args.leave_open)

    def test_payload_contains_required_gates(self):
        code = _maya_code({"sentinel": True})
        for token in (
            "mmd_morph_index",
            "mmd_material_morph_offsets_json",
            "deviceInformation",
            "nonzeroRgb",
            "alphaOnlyInvariant",
            "mmdMaterialMorphEval",
        ):
            self.assertIn(token, code)

    def test_true_add_and_multiply_diffuse_alpha_only(self):
        self.assertTrue(
            is_diffuse_alpha_only_offsets([{"operation_type": 1, "diffuse": [0, 0, 0, -0.5]}])
        )
        self.assertTrue(
            is_diffuse_alpha_only_offsets([{"operation_type": 0, "diffuse": [1, 1, 1, 0.25]}])
        )

    def test_composite_channels_are_not_diffuse_alpha_only(self):
        base = {"operation_type": 1, "diffuse": [0, 0, 0, -0.5]}
        for channel, value in (
            ("specular", [0.1, 0, 0]),
            ("ambient", [0, 0.1, 0]),
            ("edge_color", [0, 0, 0, 0.5]),
            ("edge_size", 0.1),
            ("texture_factor", [0, 0, 0.1, 0]),
            ("sphere_texture_factor", [0, 0, 0, 0.1]),
            ("toon_texture_factor", [0.1, 0, 0, 0]),
        ):
            with self.subTest(channel=channel):
                self.assertFalse(is_diffuse_alpha_only_offsets([{**base, channel: value}]))

    def test_multiply_defaults_use_one_as_neutral(self):
        self.assertFalse(
            is_diffuse_alpha_only_offsets(
                [{"operation_type": 0, "diffuse": [1, 1, 1, 0.5], "specular": [1, 0.5, 1]}]
            )
        )

    def test_attach_guard_precedes_scene_reset_and_import(self):
        code = _maya_code({"sentinel": True})
        guard = 'if P["attach_existing"] and cmds.file(query=True, modified=True):'
        self.assertLess(code.index(guard), code.index("cmds.file(new=True, force=True)"))
        self.assertLess(code.index(guard), code.index("import_mmd_file(P[\"model\"])"))

    def test_safe_capture_dir_sanitizes_traversal_and_forbidden_chars(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = safe_capture_dir(root, 31, "../../CON:<bad>|?*\\name")
            self.assertEqual(root.resolve(), target.parent)
            self.assertTrue(target.name.startswith("031_"))
            self.assertFalse(any(char in target.name for char in '<>:"/\\|?*'))

    def test_safe_capture_dir_preserves_japanese_and_bounds_length(self):
        with tempfile.TemporaryDirectory() as temp:
            target = safe_capture_dir(Path(temp), 158, "制服" * 100)
            self.assertTrue(target.name.startswith("158_制服"))
            self.assertLessEqual(len(target.name), 68)

    def test_safe_capture_dir_has_indexed_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            target = safe_capture_dir(Path(temp), 143, "..\\/\x01")
            self.assertEqual("143_material-morph", target.name)

    @staticmethod
    def _trace(edges, types, start="eval.contribution[0].weight", target="ns:morph.weight"):
        return trace_weight_source_chains(
            start,
            target,
            lambda plug: edges.get(plug, []),
            lambda node: types.get(node, "network"),
        )

    def test_weight_trace_direct(self):
        edges = {"eval.contribution[0].weight": ["ns:morph.weight"]}
        self.assertEqual(
            [["eval.contribution[0].weight", "ns:morph.weight"]],
            self._trace(edges, {}),
        )

    def test_weight_trace_plus_and_group_multiply_chain(self):
        edges = {
            "eval.contribution[0].weight": ["sum.output1D"],
            "sum.output1D": ["sum.input1D[0]", "sum.input1D[1]"],
            "sum.input1D[0]": ["ns:morph.weight"],
            "sum.input1D[1]": ["groupMult.outputX"],
            "groupMult.outputX": ["groupMult.input1X", "groupMult.input2X"],
            "groupMult.input1X": ["ns:group.weight"],
        }
        chains = self._trace(edges, {"sum": "plusMinusAverage", "groupMult": "multiplyDivide"})
        self.assertEqual(
            [["eval.contribution[0].weight", "sum.output1D", "sum.input1D[0]", "ns:morph.weight"]],
            chains,
        )

    def test_weight_trace_ignores_unrelated_helper(self):
        edges = {
            "eval.contribution[0].weight": ["sum.output1D"],
            "sum.output1D": ["sum.input1D[0]"],
            "sum.input1D[0]": ["other.weight"],
        }
        self.assertEqual([], self._trace(edges, {"sum": "plusMinusAverage"}))

    def test_weight_trace_cycle_is_safe(self):
        edges = {
            "eval.contribution[0].weight": ["sum.output1D"],
            "sum.output1D": ["sum.input1D[0]"],
            "sum.input1D[0]": ["sum.output1D"],
        }
        self.assertEqual([], self._trace(edges, {"sum": "plusMinusAverage"}))

    def test_settings_restoration_wraps_success_and_failure(self):
        code = _maya_code({"sentinel": True})
        snapshot = code.index("_setting_optionvars = snapshot_settings(_settings_impl)")
        first_set = code.index('settings.set("import.model.create_mmd_shaders", True)')
        exception_handler = code.index("except Exception as exc:")
        restore = code.index("restore_settings(_settings_impl, _setting_optionvars, _setting_memory_values)")
        report_write = code.index('with (OUT / "material-morph-report.json").open')
        marker = code.index("log('//-- MAYA MATERIAL MORPH E2E FINISHED --//')")
        self.assertLess(snapshot, first_set)
        self.assertLess(exception_handler, restore)
        self.assertLess(restore, report_write)
        self.assertLess(restore, marker)

    def test_settings_restoration_removes_previously_absent_optionvar(self):
        code = _maya_code({"sentinel": True})
        self.assertIn('"existed": existed', code)
        self.assertIn('if not prior["existed"]:', code)
        self.assertIn("cmds.optionVar(remove=option_key)", code)

    def test_rgba_pixel_stats_uses_exact_bounded_buffer(self):
        # Trailing bytes must not affect the two declared pixels.
        pixels = bytearray([0, 10, 20, 255, 200, 100, 50, 255] + [255] * 1024)
        stats = rgba_pixel_stats(pixels, 2, 1)
        self.assertEqual(0, stats["rgbMin"])
        self.assertEqual(200, stats["rgbMax"])
        self.assertEqual(5, stats["nonzeroRgb"])
        with self.assertRaises(ValueError):
            rgba_pixel_stats(bytearray(7), 2, 1)

    def test_blank_exception_summary_keeps_type(self):
        self.assertEqual("MemoryError", exception_summary(MemoryError()))
        self.assertEqual("ValueError: detail", exception_summary(ValueError("detail")))


if __name__ == "__main__":
    unittest.main()
