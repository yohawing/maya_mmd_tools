import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tests.common import maya_commandport


class TestMayaCommandPort(unittest.TestCase):
    def test_wait_for_port_close_polls_until_closed(self):
        with mock.patch.object(
            maya_commandport,
            "is_port_open",
            side_effect=[True, True, False],
        ) as is_port_open, mock.patch.object(maya_commandport.time, "sleep") as sleep:
            maya_commandport.wait_for_port_close(7788, timeout=5)

        self.assertEqual(is_port_open.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_launch_maya_explorer_writes_detached_launcher(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable = root / "maya.exe"
            executable.touch()
            output = root / "out"
            with mock.patch.object(maya_commandport, "maya_exe", return_value=executable), mock.patch.object(
                maya_commandport.platform, "system", return_value="Windows"
            ), mock.patch.object(maya_commandport.subprocess, "run") as run:
                result = maya_commandport.launch_maya(
                    version="2025",
                    project_root=root,
                    output_dir=output,
                    port=7788,
                    launch_mode="explorer",
                    env_overrides={
                        "MAYA_APP_DIR": str(root / "isolated-maya-app"),
                        "MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceGLCore",
                    },
                )
            self.assertIsNone(result)
            startup_script = (output / "commandport_7788.mel").read_text(encoding="utf-8")
            self.assertEqual(
                startup_script,
                'commandPort -name ":7788" -sourceType "python";\n',
            )
            batch = (output / "launch_maya_2025_7788.bat").read_text(encoding="utf-8")
            self.assertIn(f"MAYA_APP_DIR={root / 'isolated-maya-app'}", batch)
            self.assertIn("MAYA_VP2_DEVICE_OVERRIDE=VirtualDeviceGLCore", batch)
            self.assertEqual("explorer.exe", run.call_args.args[0][0])

    def test_send_python_wraps_code_as_compiled_exec_payload(self):
        received = []
        ready = threading.Event()

        def server():
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen(1)
                received.append(listener.getsockname()[1])
                ready.set()
                conn, _addr = listener.accept()
                with conn:
                    chunks = []
                    while True:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        chunks.append(chunk)
                    received.append(b"".join(chunks).decode("utf-8"))

        thread = threading.Thread(target=server)
        thread.start()
        self.assertTrue(ready.wait(timeout=5.0))

        maya_commandport.send_python(received[0], "print('ok')\n", label="<unit-label>")

        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())
        payload = received[1]
        self.assertIn("exec(compile(", payload)
        self.assertIn("print('ok')", payload)
        self.assertIn("'<unit-label>'", payload)
        self.assertTrue(payload.endswith("\n"))

    def test_launch_maya_rejects_unstable_direct_mode_on_windows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            root.mkdir()
            maya_exe = Path(tmpdir) / "maya.exe"
            maya_exe.write_text("", encoding="utf-8")
            output_dir = Path(tmpdir) / "logs"

            with mock.patch.object(maya_commandport, "maya_exe", return_value=maya_exe), mock.patch.object(
                maya_commandport.platform, "system", return_value="Windows"
            ), self.assertRaisesRegex(ValueError, "license-checkout exit 253"):
                maya_commandport.launch_maya(
                    version="2026",
                    project_root=root,
                    output_dir=output_dir,
                    port=7722,
                    launch_mode="direct",
                )

    def test_launch_maya_does_not_force_dx11_without_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            root.mkdir()
            maya_exe = Path(tmpdir) / "maya.exe"
            maya_exe.write_text("", encoding="utf-8")
            process = mock.Mock()

            with mock.patch.dict(maya_commandport.os.environ, {}, clear=True), mock.patch.object(
                maya_commandport,
                "maya_exe",
                return_value=maya_exe,
            ), mock.patch.object(
                maya_commandport.platform,
                "system",
                return_value="Linux",
            ), mock.patch.object(maya_commandport.subprocess, "Popen", return_value=process) as popen:
                maya_commandport.launch_maya(
                    version="2026",
                    project_root=root,
                    output_dir=Path(tmpdir) / "logs",
                    port=7722,
                    launch_mode="direct",
                )

            try:
                self.assertNotIn("MAYA_VP2_DEVICE_OVERRIDE", popen.call_args.kwargs["env"])
                self.assertEqual(
                    str((Path(tmpdir) / "logs" / "maya-app-2026-7722").resolve()),
                    popen.call_args.kwargs["env"]["MAYA_APP_DIR"],
                )
            finally:
                maya_commandport.close_process_logs(process)


if __name__ == "__main__":
    unittest.main()
