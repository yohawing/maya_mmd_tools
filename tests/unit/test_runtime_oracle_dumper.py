"""runtime_oracle_dumperのMaya非依存ロジックを検証するテスト。"""

import unittest
from pathlib import Path

from mmd_tools.tools.runtime_oracle_dumper import (
    _default_output_path,
    _focus_targets,
)


class TestRuntimeOracleDumper(unittest.TestCase):
    """GoldenOracle比較用dumperの補助ロジックを検証する。"""

    def test_default_output_path_includes_offset_and_ik_options(self):
        manifest_path = r"F:\GoldenOracle\manifests\motion-numeric.json"
        case = {
            "oracle": {
                "path": "../runs/motion-numeric/sample/oracle.actual.jsonl",
            }
        }

        path = _default_output_path(
            manifest_path=Path(manifest_path),
            case=case,
            sample_frame_offset=1.0,
            ik_tolerance=0.0,
            ik_max_iterations_cap=32,
        )

        self.assertEqual(path.name, "runtime.offset1.tol0.ikcap32.actual.jsonl")

    def test_case_focus_overrides_default_focus(self):
        manifest = {
            "defaults": {"focus": {"bones": ["default-bone"], "morphs": ["default-morph"]}}
        }
        case = {"metadata": {"focus": {"bones": ["case-bone"]}}}

        self.assertEqual(_focus_targets(manifest, case, "bones"), ["case-bone"])
        self.assertEqual(_focus_targets(manifest, case, "morphs"), ["default-morph"])


if __name__ == "__main__":
    unittest.main()
