"""Pure-Python contracts for transactional physics enable pre-roll."""

from __future__ import annotations

import unittest

from mmd_tools.core.physics_preroll import (
    PREROLL_CANCELLED,
    PREROLL_CURRENT_BEFORE_START,
    PREROLL_EVALUATION_FAILED,
    PREROLL_RANGE_EXCEEDS_LIMIT,
    PREROLL_RESTORE_FAILED,
    PhysicsPrerollError,
    run_physics_preroll,
)


class _FakeCmds:
    def __init__(self):
        self.current_time = 3.0
        self.playing = True
        self.selection = ["|selected"]
        self.evaluation_modes = ["parallel"]
        self.attrs = {
            "|world.enable": False,
            "|world.resetGeneration": 7,
            "|world.startFrame": 0,
            "|solverA.enable": True,
            "|solverB.enable": True,
            "|solverA.outStatus": "stepped",
        }
        self.time_writes = []
        self.set_calls = []
        self.solved_reads = []
        self.dirtied = []
        self.fail_frame = None
        self.fail_selection_restore = False
        self.progress_calls = []
        self.progress_updates = 0
        self.cancel_after_progress_updates = None

    def ls(self, nodes=None, **kwargs):
        if kwargs.get("selection"):
            return list(self.selection)
        if kwargs.get("type") == "mmdPhysicsSolver":
            return ["|solverA", "|solverB"]
        if isinstance(nodes, (list, tuple)):
            return list(nodes)
        return [nodes] if nodes else []

    def listConnections(self, plug, **_kwargs):
        if plug in {"|world.message", "|world.outSettingsVersion"}:
            return ["|solverA", "|solverB"]
        if plug in {"|solverA.inWorldSettings", "|solverB.inWorldSettings"}:
            return ["|world"]
        return []

    def nodeType(self, node):
        return "mmdPhysicsSolver" if node in {"|solverA", "|solverB"} else "unknown"

    def connectionInfo(self, plug, **kwargs):
        if kwargs.get("sourceFromDestination") and plug in {
            "|solverA.inWorldSettings",
            "|solverB.inWorldSettings",
        }:
            return "|world.message"
        return ""

    def evaluationManager(self, **kwargs):
        if kwargs.get("query"):
            return list(self.evaluation_modes)
        if "mode" in kwargs:
            self.evaluation_modes = [kwargs["mode"]]
        return None

    def currentTime(self, value=None, **kwargs):
        if kwargs.get("query"):
            return self.current_time
        self.current_time = float(value)
        self.time_writes.append(self.current_time)
        return self.current_time

    def play(self, **kwargs):
        if kwargs.get("query"):
            return self.playing
        if "state" in kwargs:
            self.playing = bool(kwargs["state"])
        return None

    def select(self, nodes=None, **kwargs):
        if self.fail_selection_restore:
            raise RuntimeError("selection restore failed")
        if kwargs.get("clear"):
            self.selection = []
        elif kwargs.get("replace"):
            self.selection = list(nodes or [])

    def getAttr(self, plug):
        if plug == "|solverA.outSolved":
            self.solved_reads.append(self.current_time)
            return self.current_time != self.fail_frame
        return self.attrs[plug]

    def setAttr(self, plug, value):
        self.attrs[plug] = value
        self.set_calls.append((plug, value))

    def progressWindow(self, **kwargs):
        self.progress_calls.append(dict(kwargs))
        if kwargs.get("query") and kwargs.get("isCancelled"):
            return (
                self.cancel_after_progress_updates is not None
                and self.progress_updates >= self.cancel_after_progress_updates
            )
        if kwargs.get("edit"):
            self.progress_updates += 1
        return False

    def dgdirty(self, node, **_kwargs):
        self.dirtied.append(node)


class TestPhysicsPreroll(unittest.TestCase):
    def test_success_steps_from_saved_start_and_restores_interaction(self):
        cmds = _FakeCmds()

        result = run_physics_preroll("|world", ["|solverA"], maya_cmds=cmds)

        self.assertEqual(result.start_frame, 0.0)
        self.assertEqual(result.target_frame, 3.0)
        self.assertEqual(result.step_count, 3)
        self.assertEqual(cmds.solved_reads, [0.0, 1.0, 2.0, 3.0])
        self.assertEqual(cmds.attrs["|world.enable"], True)
        self.assertEqual(cmds.attrs["|world.resetGeneration"], 8)
        self.assertEqual(cmds.attrs["|solverB.enable"], True)
        self.assertEqual(cmds.current_time, 3.0)
        self.assertTrue(cmds.playing)
        self.assertEqual(cmds.selection, ["|selected"])
        self.assertEqual(cmds.evaluation_modes, ["parallel"])
        self.assertIn(("|solverB.enable", False), cmds.set_calls)

    def test_cancel_rolls_back_physics_and_interaction_exactly(self):
        cmds = _FakeCmds()

        with self.assertRaises(PhysicsPrerollError) as raised:
            run_physics_preroll(
                "|world",
                ["|solverA"],
                maya_cmds=cmds,
                progress_callback=lambda completed, _total, _frame: completed < 1,
            )

        self.assertEqual(raised.exception.reason_code, PREROLL_CANCELLED)
        self.assertEqual(cmds.attrs["|world.enable"], False)
        self.assertEqual(cmds.attrs["|world.resetGeneration"], 7)
        self.assertEqual(cmds.attrs["|solverA.enable"], True)
        self.assertEqual(cmds.attrs["|solverB.enable"], True)
        self.assertEqual(cmds.current_time, 3.0)
        self.assertTrue(cmds.playing)
        self.assertEqual(cmds.selection, ["|selected"])
        self.assertEqual(cmds.evaluation_modes, ["parallel"])
        self.assertEqual(cmds.dirtied, ["|solverA", "|solverA"])

    def test_solver_failure_rolls_back_with_stable_reason(self):
        cmds = _FakeCmds()
        cmds.fail_frame = 2.0
        cmds.attrs["|solverA.outStatus"] = "failed"

        with self.assertRaises(PhysicsPrerollError) as raised:
            run_physics_preroll("|world", ["|solverA"], maya_cmds=cmds)

        self.assertEqual(raised.exception.reason_code, PREROLL_EVALUATION_FAILED)
        self.assertIn("frame=2.0", str(raised.exception))
        self.assertFalse(cmds.attrs["|world.enable"])
        self.assertEqual(cmds.attrs["|world.resetGeneration"], 7)

    def test_range_limit_and_current_before_start_fail_before_mutation(self):
        cmds = _FakeCmds()
        with self.assertRaises(PhysicsPrerollError) as limited:
            run_physics_preroll("|world", ["|solverA"], max_steps=2, maya_cmds=cmds)
        self.assertEqual(limited.exception.reason_code, PREROLL_RANGE_EXCEEDS_LIMIT)
        self.assertEqual(cmds.set_calls, [])

        cmds.attrs["|world.startFrame"] = 4
        with self.assertRaises(PhysicsPrerollError) as before_start:
            run_physics_preroll("|world", ["|solverA"], maya_cmds=cmds)
        self.assertEqual(before_start.exception.reason_code, PREROLL_CURRENT_BEFORE_START)
        self.assertEqual(cmds.set_calls, [])

    def test_restore_failure_rolls_physics_back_and_reports_fail_closed(self):
        cmds = _FakeCmds()
        cmds.fail_selection_restore = True

        with self.assertRaises(PhysicsPrerollError) as raised:
            run_physics_preroll("|world", ["|solverA"], maya_cmds=cmds)

        self.assertEqual(raised.exception.reason_code, PREROLL_RESTORE_FAILED)
        self.assertFalse(cmds.attrs["|world.enable"])
        self.assertEqual(cmds.attrs["|world.resetGeneration"], 7)

    def test_long_range_uses_progress_window_and_cancel_rolls_back(self):
        cmds = _FakeCmds()
        cmds.current_time = 121.0
        cmds.cancel_after_progress_updates = 1

        with self.assertRaises(PhysicsPrerollError) as raised:
            run_physics_preroll("|world", ["|solverA"], maya_cmds=cmds)

        self.assertEqual(raised.exception.reason_code, PREROLL_CANCELLED)
        self.assertTrue(any(call.get("title") == "MMD Physics Pre-roll" for call in cmds.progress_calls))
        self.assertTrue(any(call.get("endProgress") for call in cmds.progress_calls))
        self.assertFalse(cmds.attrs["|world.enable"])
        self.assertEqual(cmds.current_time, 121.0)


if __name__ == "__main__":
    unittest.main()
