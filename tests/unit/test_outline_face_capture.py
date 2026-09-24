"""Fail-closed image comparison for the local face-outline regression probe."""

from copy import deepcopy

import pytest

from tools.render_override import outline_face_capture as probe


@pytest.fixture
def captures(monkeypatch):
    reference = {"status": "pass", "mayaVersion": "2024", "cameraMatrix": [1, 0],
                 "face": {"flags": 31, "width": 0.5}, "image": "reference",
                 "hashes": {"plugin": "dll", "model": "pmx", "sharedShader": "shared"}}
    candidate = deepcopy(reference)
    candidate["image"] = "candidate"
    pixels = {"reference": (2, 1, [(0, 0, 0), (0, 0, 0)]),
              "candidate": (2, 1, [(0, 0, 0), (255, 0, 0)])}
    monkeypatch.setattr(probe, "read_png_rgb", lambda path: pixels[path])
    return candidate, reference


def test_comparison_reports_changes_outside_the_roi(captures):
    candidate, reference = captures
    probe.compare_capture(candidate, reference, [0, 0, 1, 1])
    assert candidate["imageAcceptance"] == "pass"
    assert candidate["comparison"]["wholeImageChangedPixels"] == 1


def test_comparison_rejects_changed_outline_pixels(captures):
    candidate, reference = captures
    with pytest.raises(ValueError, match="ROI changed"):
        probe.compare_capture(candidate, reference, [0, 0, 2, 1])
    assert candidate["imageAcceptance"] == "fail"


def test_comparison_rejects_a_reused_image_path(captures):
    candidate, reference = captures
    candidate["image"] = reference["image"]
    with pytest.raises(ValueError, match="separate image paths"):
        probe.compare_capture(candidate, reference, [0, 0, 1, 1])


@pytest.mark.parametrize("roi", ([0, 0, 0, 1], [0, 0, 3, 1], [-1, 0, 1, 1]))
def test_comparison_cannot_pass_an_empty_or_outside_roi(captures, roi):
    candidate, reference = captures
    with pytest.raises(ValueError, match="ROI"):
        probe.compare_capture(candidate, reference, roi)


@pytest.mark.parametrize("field,value", (("cameraMatrix", [0, 1]), ("status", "fail")))
def test_comparison_requires_matching_completed_captures(captures, field, value):
    candidate, reference = captures
    candidate[field] = value
    with pytest.raises(ValueError):
        probe.compare_capture(candidate, reference, [0, 0, 1, 1])
