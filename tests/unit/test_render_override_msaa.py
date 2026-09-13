"""Reject the observed blank-AA-off regression, including two blank captures."""

from tools.render_override.render_override_visual_gate import (
    compare_msaa_coverage,
    compare_model_coverage,
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


def test_model_gate_rejects_a_surviving_unrelated_object(tmp_path):
    off, on, hidden = (tmp_path / name for name in ("off.png", "on.png", "hidden.png"))
    control = [(90, 90, 90)] * 10000
    control[200:700] = [(20, 220, 20)] * 500
    for path in (off, on, hidden):
        write_png_rgb(path, 100, 100, control)
    # Old silhouette-only comparison accepts this matching surviving cube.
    assert compare_msaa_coverage(off, on)["pass"]
    assert not compare_model_coverage(off, on, hidden, hidden)["pass"]
    body = control.copy()
    body[2500:7000] = [(220, 180, 160)] * 4500
    write_png_rgb(on, 100, 100, body)
    assert not compare_model_coverage(off, on, hidden, hidden)["pass"]
    write_png_rgb(off, 100, 100, body)
    assert compare_model_coverage(off, on, hidden, hidden)["pass"]
