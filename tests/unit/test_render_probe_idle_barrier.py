"""Scene initialization must drain before the render probe starts editing."""

import sys
import types
import unittest
from unittest.mock import Mock, patch

from tools.render_override import separation_e2e


class TestRenderProbeIdleBarrier(unittest.TestCase):
    def test_only_initial_scene_setup_waits_for_low_priority_idle(self):
        deferred = []
        timers = []
        cmds = Mock()
        cmds.evalDeferred.side_effect = lambda fn, **kw: deferred.append((fn, kw))
        qt = types.ModuleType("PySide6.QtCore")
        qt.QTimer = Mock()
        qt.QTimer.singleShot.side_effect = lambda delay, fn: timers.append((delay, fn))
        maya = types.ModuleType("maya")
        maya.cmds = cmds
        completed = []

        def steps():
            completed.append("scene setup")
            yield
            completed.append("edit")
            yield
            completed.append("done")

        with patch.dict(sys.modules, {"maya": maya, "PySide6.QtCore": qt}), patch.object(
            separation_e2e, "_probe_steps", return_value=steps()
        ):
            separation_e2e.run_probe("out", "plugin")
            deferred.pop(0)[0]()
            self.assertEqual(["scene setup"], completed)
            self.assertFalse(timers)
            barrier, options = deferred.pop(0)
            self.assertEqual({"lowestPriority": True}, options)
            barrier()
            self.assertEqual(["scene setup"], completed)
            timers.pop(0)[1]()
            self.assertEqual(["scene setup", "edit"], completed)
            self.assertFalse(deferred)
            timers.pop(0)[1]()
            self.assertEqual(["scene setup", "edit", "done"], completed)
            self.assertFalse(timers)


if __name__ == "__main__":
    unittest.main()
