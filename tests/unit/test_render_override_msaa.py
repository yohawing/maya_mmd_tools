"""Reject the observed blank-AA-off regression, including two blank captures."""

from tools.render_override.render_override_visual_gate import (
    compare_msaa_coverage,
    write_png_rgb,
)


def test_msaa_coverage_rejects_disappearing_and_empty_surfaces(tmp_path):
    off, on = tmp_path / "off.png", tmp_path / "on.png"
    empty = [(90, 90, 90)] * 100
    body = empty.copy()
    body[22:78] = [(220, 220, 220)] * 56
    write_png_rgb(off, 10, 10, empty)
    write_png_rgb(on, 10, 10, body)
    assert not compare_msaa_coverage(off, on)["pass"]
    write_png_rgb(on, 10, 10, empty)
    assert not compare_msaa_coverage(off, on)["pass"]
    write_png_rgb(off, 10, 10, body)
    body[22] = (90, 90, 90)
    write_png_rgb(on, 10, 10, body)
    assert compare_msaa_coverage(off, on)["pass"]
