"""Pure-Python contract checks for the physics cycle probe Playblast trace."""

from __future__ import annotations

import importlib.util
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]
_PROBE_PATH = _ROOT / "tests" / "viewport" / "physics_solver_cycle_probe.py"


def _load_probe():
    spec = importlib.util.spec_from_file_location("physics_solver_cycle_probe_playblast", _PROBE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeCmds:
    """Minimal Maya command surface needed by ``_playblast_observation``."""

    def __init__(self):
        self.frame = 4

    def currentTime(self, value=None, query=False, edit=False):
        if query:
            return self.frame
        self.frame = value
        return self.frame

    def refresh(self, force=False):
        return None

    def objExists(self, node):
        return node == "solver1"

    def attributeQuery(self, attr, node, exists=False, **kwargs):
        return bool(exists and attr == "outStatus")

    def getAttr(self, plug, **kwargs):
        if plug == "solver1.outStatus":
            return "stepped"
        return 0

    def playblast(self, filename, frame, **kwargs):
        output = Path(filename).with_suffix(".png")
        output.write_bytes(b"png")
        return str(output)


class _FailingPlayblastCmds(_FakeCmds):
    def playblast(self, filename, frame, **kwargs):
        raise RuntimeError("offscreen backend unavailable")


def test_mode_playblast_path_is_stable_and_png(tmp_path):
    probe = _load_probe()
    base = tmp_path / "trace"
    assert probe._mode_playblast_path(base, "parallel") == (tmp_path / "trace_parallel.png").resolve()


def test_playblast_observation_records_two_same_frame_pulls(tmp_path):
    probe = _load_probe()
    fake = _FakeCmds()
    original = probe.cmds
    probe.cmds = fake
    try:
        result = probe._playblast_observation(
            solver="solver1",
            frame=7,
            output_path=tmp_path / "trace_parallel.png",
            width=32,
            height=24,
        )
    finally:
        probe.cmds = original

    assert result["outcome"] == "pass"
    assert result["requestedFrame"] == 7
    assert result["capturedFrame"] == 7
    assert result["solverStateBefore"] == result["beforeSolverState"]
    assert result["solverStateAfter"] == result["afterSolverState"]
    assert len(result["sameFramePulls"]) == 4
    assert [pull["status"] for pull in result["beforeSameFramePulls"]] == ["stepped", "stepped"]
    assert [pull["status"] for pull in result["afterSameFramePulls"]] == ["stepped", "stepped"]
    assert result["playblast"]["offScreen"] is True
    assert result["playblast"]["fileWritten"] is True
    assert result["playblast"]["actualOutputPath"].endswith("trace_parallel.png")


def test_playblast_error_is_preserved_in_trace(tmp_path):
    probe = _load_probe()
    original = probe.cmds
    probe.cmds = _FailingPlayblastCmds()
    try:
        result = probe._playblast_observation(
            solver="solver1",
            frame=7,
            output_path=tmp_path / "trace_error.png",
            width=32,
            height=24,
        )
    finally:
        probe.cmds = original

    assert result["outcome"] == "error"
    assert result["fileWritten"] is False
    assert any("offscreen backend unavailable" in error for error in result["errors"])
