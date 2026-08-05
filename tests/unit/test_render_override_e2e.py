"""Unit tests for the host-side R1 GUI RenderOverride runner."""

from __future__ import annotations

import json
import struct
import unittest
import zlib
from pathlib import Path
from unittest import mock

from tools import render_override_e2e


class RenderOverrideE2eTest(unittest.TestCase):
    """Keep commandPort dispatch and strict paired-capture comparison stable."""

    def test_capture_comparison_accepts_backend_variance_only(self):
        root = Path(self.id().replace(".", "_"))
        temp_dir = Path.cwd() / "build" / "unit" / root
        temp_dir.mkdir(parents=True, exist_ok=True)
        reference = temp_dir / "reference.png"
        within_variance = temp_dir / "within_variance.png"
        divergent = temp_dir / "divergent.png"
        self._write_rgb_png(reference, [(32, 64, 96)] * 4)
        self._write_rgb_png(within_variance, [(33, 64, 96)] + [(32, 64, 96)] * 3)
        self._write_rgb_png(divergent, [(36, 64, 96)] * 4)

        self.assertTrue(render_override_e2e._compare_captures(reference, within_variance)["pass"])
        self.assertFalse(render_override_e2e._compare_captures(reference, divergent)["pass"])

    def test_oracle_comparison_uses_normalized_mean_absolute_error(self):
        root = Path(self.id().replace(".", "_"))
        temp_dir = Path.cwd() / "build" / "unit" / root
        temp_dir.mkdir(parents=True, exist_ok=True)
        reference = temp_dir / "reference.png"
        candidate = temp_dir / "candidate.png"
        self._write_rgb_png(reference, [(255, 255, 255)] * 4)
        self._write_rgb_png(candidate, [(251, 255, 255)] + [(255, 255, 255)] * 3)

        comparison = render_override_e2e._compare_oracle_capture(reference, candidate, 0.002)

        self.assertTrue(comparison["pass"])
        self.assertEqual(comparison["metric"], "normalized_mean_absolute_error")
        self.assertAlmostEqual(comparison["normalizedMeanAbsoluteError"], 4 / (4 * 3 * 255))
        self.assertFalse(comparison["strictComparison"]["pass"])

    @staticmethod
    def _write_rgb_png(path: Path, pixels: list[tuple[int, int, int]]) -> None:
        """Write one deterministic 2x2 RGB fixture with standard PNG filters."""
        rows = []
        for row in range(2):
            row_pixels = pixels[row * 2 : row * 2 + 2]
            rows.append(b"\x00" + b"".join(bytes(pixel) for pixel in row_pixels))
        raw = b"".join(rows)
        header = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)

        def chunk(kind: bytes, data: bytes) -> bytes:
            return (
                struct.pack(">I", len(data))
                + kind
                + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
            )

        path.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b"")
        )

    def test_main_dispatches_short_import_command(self):
        report = {
            "status": "fail",
            "captures": {},
            "checks": {},
            "errors": ["expected host fixture"],
        }
        with mock.patch.object(render_override_e2e, "run_maya_e2e", return_value=report) as run_gate, mock.patch.object(
            render_override_e2e.sys,
            "argv",
            [
                "render_override_e2e.py",
                "--maya",
                "2026",
                "--port",
                "7799",
                "--vp2-device",
                "dx11",
                "--target-probe",
                "--r32f-binding-probe",
            ],
        ):
            self.assertEqual(render_override_e2e.main(), 1)
        command = run_gate.call_args.kwargs["command"]
        self.assertIn("from tools.render_override_e2e import run_probe", command)
        self.assertIn("run_probe(", command)
        self.assertIn("'kDirectX11'", command)
        self.assertIn("True", command)
        self.assertEqual(run_gate.call_args.kwargs["port"], 7799)
        self.assertEqual(
            run_gate.call_args.kwargs["env_overrides"],
            {"MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11"},
        )


    def test_main_forwards_native_shadow_request(self):
        report = {"status": "fail", "captures": {}, "checks": {}, "errors": []}
        with mock.patch.object(
            render_override_e2e, "run_maya_e2e", return_value=report
        ) as run_gate, mock.patch.object(
            render_override_e2e.sys,
            "argv",
            ["render_override_e2e.py", "--native-shadow-request"],
        ):
            self.assertEqual(render_override_e2e.main(), 1)
        command = run_gate.call_args.kwargs["command"]
        self.assertIn("None, False, True, False, False)", command)

    def test_main_forwards_native_shadow_binding_probe_with_request(self):
        report = {"status": "fail", "captures": {}, "checks": {}, "errors": []}
        with mock.patch.object(
            render_override_e2e, "run_maya_e2e", return_value=report
        ) as run_gate, mock.patch.object(
            render_override_e2e.sys,
            "argv",
            [
                "render_override_e2e.py",
                "--native-shadow-request",
                "--native-shadow-binding-probe",
            ],
        ):
            self.assertEqual(render_override_e2e.main(), 1)
        command = run_gate.call_args.kwargs["command"]
        self.assertIn("None, False, True, True, False)", command)

    def test_main_forwards_native_shadow_receiver(self):
        report = {"status": "fail", "captures": {}, "checks": {}, "errors": []}
        with mock.patch.object(
            render_override_e2e, "run_maya_e2e", return_value=report
        ) as run_gate, mock.patch.object(
            render_override_e2e.sys,
            "argv",
            [
                "render_override_e2e.py",
                "--native-shadow-receiver",
                "--model",
                "F:/fixtures/self-shadow.pmx",
            ],
        ):
            self.assertEqual(render_override_e2e.main(), 1)
        command = run_gate.call_args.kwargs["command"]
        self.assertIn("False, False, False, True)", command)

    def test_main_forwards_explicit_camera_and_capture_size(self):
        report = {"status": "fail", "captures": {}, "checks": {}, "errors": []}
        camera = '{"position":[0,24,13],"target":[0,1,0],"fov":40,"near":0.1,"far":100}'
        with mock.patch.object(
            render_override_e2e, "run_maya_e2e", return_value=report
        ) as run_gate, mock.patch.object(
            render_override_e2e.sys,
            "argv",
            [
                "render_override_e2e.py",
                "--native-shadow-receiver",
                "--model",
                "F:/fixtures/self-shadow.pmx",
                "--camera",
                camera,
                "--width",
                "1024",
                "--height",
                "1024",
            ],
        ):
            self.assertEqual(render_override_e2e.main(), 1)
        command = run_gate.call_args.kwargs["command"]
        self.assertIn("camera_config=", command)
        self.assertIn("capture_width=1024", command)
        self.assertIn("capture_height=1024", command)

    def test_camera_loader_accepts_json_object_only(self):
        self.assertEqual(
            render_override_e2e._load_camera_config('{"position":[0, 1, 2]}'),
            {"position": [0, 1, 2]},
        )
        with self.assertRaises(ValueError):
            render_override_e2e._load_camera_config("[1, 2, 3]")

    def test_native_shadow_receiver_validation_requires_draw_and_native_bind(self):
        valid = {
            "enabled": True,
            "status": "released",
            "reason": "receiver-shader-released-before-target",
            "operationName": "mmdToolsNativeShadowReceiver",
            "shaderPath": "MMDNativeShadowReceiver.fx",
            "technique": "MMDNativeShadowReceiver",
            "mapParameter": "Light0ShadowMap",
            "viewProjParameter": "Light0Matrix",
            "selection": {
                "status": "ok",
                "reason": "components-added",
                "components": ["|Mmd_root|Geometry|mesh.f[0:2]"],
                "count": 1,
            },
            "createAttemptCount": 1,
            "createSucceeded": True,
            "preDrawCallbackCount": 1,
            "sourceItemCount": 1,
            "materialBindingCount": 1,
            "bindingAttemptCount": 1,
            "bindingSucceeded": True,
            "resourceHandle": 17,
            "releaseAttemptCount": 1,
            "releaseSucceeded": True,
            "releaseBeforeTarget": True,
            "drawsReceiver": True,
            "receiverComposition": True,
            "claimsSelfShadow": False,
            "context": {"status": "ready"},
            "nativeParameterErrors": [],
        }
        render_override_e2e._validate_native_shadow_receiver(valid, require_components=True)
        with self.assertRaises(RuntimeError):
            render_override_e2e._validate_native_shadow_receiver(
                dict(valid, resourceHandle=0), require_components=True
            )

    def test_native_shadow_binding_validation_requires_positive_map_lifecycle(self):
        valid = {
            "enabled": True,
            "status": "released",
            "reason": "shader-released-before-target",
            "targetName": "mmdToolsNativeShadowBindingProbeR32F",
            "shaderPath": "MMDNativeShadowBindingProbe.fx",
            "technique": "MmdToolsNativeShadowBindingProbe",
            "mapParameter": "MayaShadowMap",
            "viewProjParameter": "MayaShadowViewProj",
            "createAttemptCount": 1,
            "createSucceeded": True,
            "contextExecuteCount": 1,
            "preDrawCallbackCount": 1,
            "bindingAttemptCount": 1,
            "bindingSucceeded": True,
            "resourceHandle": 17,
            "releaseAttemptCount": 1,
            "releaseSucceeded": True,
            "releaseBeforeTarget": True,
            "drawsReceiver": False,
            "receiverComposition": False,
            "claimsSelfShadow": False,
            "context": {"lights": [{"lightType": "directionalLight"}]},
            "output": {"status": "sampled", "readbackCount": 1},
        }
        render_override_e2e._validate_native_shadow_binding_probe(valid)
        with self.assertRaises(RuntimeError):
            render_override_e2e._validate_native_shadow_binding_probe(
                dict(valid, resourceHandle=0)
            )

    def test_native_shadow_request_validation_requires_full_release_lifecycle(self):
        valid = {
            "enabled": True,
            "status": "released",
            "reason": "native-shadow-request-released",
            "source": "maya-renderer-light-request",
            "lights": [
                {
                    "shape": "|renderOverrideParityLight|renderOverrideParityLightShape",
                    "requestSucceeded": True,
                    "releaseSucceeded": True,
                }
            ],
            "lightCount": 1,
            "requestAttemptCount": 1,
            "requestSucceeded": True,
            "releaseAttemptCount": 1,
            "releaseSucceeded": True,
        }
        render_override_e2e._validate_native_shadow_request(valid)
        with self.assertRaises(RuntimeError):
            render_override_e2e._validate_native_shadow_request(
                dict(valid, releaseSucceeded=False)
            )



    def test_main_accepts_native_shadow_binding_without_request(self):
        report = {"status": "fail", "captures": {}, "checks": {}, "errors": []}
        with mock.patch.object(
            render_override_e2e, "run_maya_e2e", return_value=report
        ), mock.patch.object(
            render_override_e2e.sys,
            "argv",
            ["render_override_e2e.py", "--native-shadow-binding-probe"],
        ):
            self.assertEqual(render_override_e2e.main(), 1)


    def test_r32f_binding_probe_diagnostic_is_explicitly_non_rendering(self):
        render_override_e2e._validate_r32f_binding_probe(
            {
                "enabled": True,
                "status": "released",
                "reason": "shader-released",
                "targetName": "mmdToolsSelfShadowR32F",
                "parameter": "MmdToolsR32FTarget",
                "bindingAttemptCount": 1,
                "bindingSucceeded": True,
                "releaseAttemptCount": 1,
                "releaseSucceeded": True,
                "drawsReceiver": False,
            }
        )
        with self.assertRaises(RuntimeError):
            render_override_e2e._validate_r32f_binding_probe(
                {"status": "bound", "drawsReceiver": True}
            )
        with self.assertRaises(RuntimeError):
            render_override_e2e._validate_r32f_binding_probe(
                {
                    "enabled": True,
                    "status": "unsupported",
                    "reason": "target-binding-failed",
                    "targetName": "mmdToolsSelfShadowR32F",
                    "parameter": "MmdToolsR32FTarget",
                    "bindingAttemptCount": 1,
                    "bindingSucceeded": False,
                    "releaseAttemptCount": 0,
                    "releaseSucceeded": False,
                    "drawsReceiver": False,
                }
            )

    def test_main_forwards_model_path_as_escaped_command_literal(self):
        model = Path("F:/fixtures/pmx/モデル'self-shadow.pmx")
        report = {"status": "fail", "captures": {}, "checks": {}, "errors": []}
        with mock.patch.object(
            render_override_e2e, "run_maya_e2e", return_value=report
        ) as run_gate, mock.patch.object(
            render_override_e2e.sys,
            "argv",
            [
                "render_override_e2e.py",
                "--target-probe",
                "--model",
                str(model),
            ],
        ):
            self.assertEqual(render_override_e2e.main(), 1)
        command = run_gate.call_args.kwargs["command"]
        self.assertIn(
            json.dumps(str(model.resolve()), ensure_ascii=True),
            command,
        )
        self.assertIn("True", command)

    def test_caster_selection_requires_components_only_for_real_model(self):
        empty = {
            "status": "empty",
            "reason": "no-components",
            "components": [],
            "count": 0,
        }
        render_override_e2e._validate_target_probe_caster_selection(empty)
        with self.assertRaises(RuntimeError):
            render_override_e2e._validate_target_probe_caster_selection(
                empty, require_components=True
            )
        render_override_e2e._validate_target_probe_caster_selection(
            {
                "status": "ok",
                "reason": "components-added",
                "components": ["|fixture_root|mesh.f[0:2]"],
                "count": 1,
            },
            require_components=True,
        )

    def test_target_probe_occupancy_accepts_explicit_states_and_rejects_missing_report(self):
        report = {
            "occupancy": {"status": "occupied"},
            "colorOccupancy": {"status": "unsupported"},
            "depthOccupancy": {"status": "occupied"},
        }
        render_override_e2e._validate_target_probe_occupancy(report, require_components=True)
        render_override_e2e._validate_target_probe_occupancy(
            {
                "occupancy": {"status": "empty"},
                "colorOccupancy": {"status": "empty"},
                "depthOccupancy": {"status": "empty"},
            }
        )
        with self.assertRaises(RuntimeError):
            render_override_e2e._validate_target_probe_occupancy(
                {
                    "occupancy": {"status": "not-run"},
                    "colorOccupancy": {"status": "not-run"},
                    "depthOccupancy": {"status": "not-run"},
                },
                require_components=True,
            )

    def test_main_rejects_model_without_target_probe(self):
        with mock.patch.object(
            render_override_e2e, "run_maya_e2e"
        ) as run_gate, mock.patch.object(
            render_override_e2e.sys,
            "argv",
            ["render_override_e2e.py", "--model", "F:/fixtures/self-shadow.pmx"],
        ):
            with self.assertRaises(SystemExit) as raised:
                render_override_e2e.main()
        self.assertEqual(raised.exception.code, 2)
        run_gate.assert_not_called()

    def test_main_rejects_binding_probe_without_target_probe(self):
        with mock.patch.object(
            render_override_e2e, "run_maya_e2e"
        ) as run_gate, mock.patch.object(
            render_override_e2e.sys,
            "argv",
            ["render_override_e2e.py", "--r32f-binding-probe"],
        ):
            with self.assertRaises(SystemExit) as raised:
                render_override_e2e.main()
        self.assertEqual(raised.exception.code, 2)
        run_gate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
