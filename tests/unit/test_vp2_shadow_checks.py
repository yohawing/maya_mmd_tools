"""The shadow comparison must preserve comparison and lifecycle contracts."""

from pathlib import Path

import pytest

from tools.render_override.vp2_shadow_checks import image_difference


def test_shadow_difference_rejects_same_pixel_count_with_different_dimensions():
    pixels = [(0, 0, 0), (255, 255, 255)]
    with pytest.raises(ValueError, match="dimensions"):
        image_difference((1, 2, pixels), (2, 1, pixels))


def test_shadow_difference_counts_pixels_not_channels():
    assert image_difference((2, 1, [(0, 0, 0), (1, 2, 3)]),
                            (2, 1, [(0, 0, 0), (4, 5, 6)])) == 1


def test_shadow_probe_releases_native_shadow_requests_and_names_control_honestly():
    source = Path("tools/render_override/vp2_shadow_checks.py").read_text(encoding="utf-8")
    assert "setLightRequiresShadows(light, False)" in source
    assert '"native_on_control"' in source
    assert '"mode2"' not in source
