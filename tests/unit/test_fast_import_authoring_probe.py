"""Ensure the GUI parity oracle rejects missing and invalid geometry evidence."""

import unittest

from tools.smoke.maya_fast_import_authoring import _call_native, _max_error, _motion_delta, _require_native_route


class TestAuthoringSamples(unittest.TestCase):
    def test_failed_native_call_followed_by_python_fallback_is_rejected(self):
        calls = []

        def unavailable(**kwargs):
            raise RuntimeError("native unavailable")

        with self.assertRaises(RuntimeError):
            _call_native(unavailable, calls, f="fixture.pmx")
        with self.assertRaises(RuntimeError):
            _require_native_route("cpp", calls)
        with self.assertRaises(RuntimeError):
            _require_native_route("cpp", [])

    def test_native_result_contract_is_required(self):
        for result in (None, [], ["root"], "root"):
            calls = []
            _call_native(lambda: result, calls)
            with self.subTest(result=result), self.assertRaises(RuntimeError):
                _require_native_route("cpp", calls)
        calls = []
        _call_native(lambda: ["root", "mesh"], calls)
        _require_native_route("cpp", calls)
        with self.assertRaises(RuntimeError):
            _require_native_route("python", calls)

    def test_looping_motion_is_not_mistaken_for_a_static_import(self):
        self.assertEqual(_motion_delta({"0": {"mesh": [[0, 0, 0]]},
                                        "15": {"mesh": [[1, 0, 0]]},
                                        "60": {"mesh": [[0, 0, 0]]}}), 1.0)

    def test_mismatched_meshes_and_topology_fail(self):
        for left, right in (({}, {}), ({"a": [[0, 0, 0]]}, {"b": [[0, 0, 0]]}),
                            ({"a": [[0, 0, 0]]}, {"a": []})):
            with self.subTest(left=left, right=right), self.assertRaises(ValueError):
                _max_error(left, right)

    def test_nonfinite_positions_cannot_hide_in_matching_samples(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _max_error({"mesh": [[value, 0, 0]]}, {"mesh": [[value, 0, 0]]})

    def test_reports_largest_vertex_displacement_across_frames(self):
        self.assertAlmostEqual(
            _max_error({"0": {"mesh": [[0, 0, 0]]}, "60": {"mesh": [[1, 2, 3]]}},
                       {"0": {"mesh": [[0, 0, 0]]}, "60": {"mesh": [[1, 2.25, 3]]}}),
            0.25,
        )
