"""Unit checks for the Maya DX11 shader semantic visual gate."""

from __future__ import annotations

import json
import struct
import zlib

from tests.viewport import shader_visual_semantic_gate as gate


def _write_png(path, width, height, pixel_at):
    """Write a small dependency-free RGB PNG fixture."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend(pixel_at(x, y))

    def chunk(kind, payload):
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw)))
        + chunk(b"IEND", b"")
    )


def _case_result(tmp_path, *, name=gate.OUTLINE_CASE, technique="MMDTechnique", edge_size=0.0, magenta=False, visible=True):
    image_path = tmp_path / f"{name}.png"

    def pixel_at(x, y):
        if magenta and 10 <= x <= 30 and 10 <= y <= 30:
            return (255, 0, 255)
        if visible and 20 <= x <= 80 and 20 <= y <= 80:
            return (120, 210, 140)
        return (255, 255, 255)

    _write_png(image_path, 100, 100, pixel_at)

    diagnostics_path = tmp_path / f"{name}.json"
    diagnostics_path.write_text(
        json.dumps(
            {
                "debug_actions": {
                    "outlineSentinel": [
                        {
                            "shader": "semanticShader",
                            "technique": technique,
                            "edgeColorRGB": [1.0, 0.0, 1.0],
                            "edgeSize": edge_size,
                        }
                    ]
                },
                "shaders": [
                    {
                        "name": "semanticShader",
                        "attrs": {"technique": technique, "EdgeSize": edge_size},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return {
        "name": name,
        "ok": True,
        "actual_png": str(image_path),
        "diagnostics": str(diagnostics_path),
    }


def test_semantic_gate_accepts_visible_no_edge_capture(tmp_path):
    result = gate._validate_case(_case_result(tmp_path))

    assert result["status"] == "pass"
    assert result["metrics"]["sentinelMagentaPixels"] == 0


def test_semantic_gate_rejects_outline_sentinel_leak(tmp_path):
    result = gate._validate_case(_case_result(tmp_path, edge_size=2.5, magenta=True))

    assert result["status"] == "fail"
    assert any("non-zero EdgeSize" in failure for failure in result["failures"])
    assert any("sentinel leaked" in failure for failure in result["failures"])


def test_semantic_gate_rejects_disappearing_hair_capture(tmp_path):
    result = gate._validate_case(_case_result(tmp_path, name=gate.HAIR_CASE, visible=False))

    assert result["status"] == "fail"
    assert any("foreground coverage" in failure for failure in result["failures"])
    assert any("center pixel is background-like" in failure for failure in result["failures"])
