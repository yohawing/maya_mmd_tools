"""Behavior checks for the native hard-shadow witness helpers."""

from tools.render_override_native_caster_e2e import (
    HARD_SHADOW_BIAS,
    _hard_shadow_body_states,
    _hard_shadow_witness_passes,
    _set_hard_shadow,
    _viewport_probe_aba_passes,
)


def _witness(**overrides):
    value = {
        "frameComplete": True,
        "hardShadowRequested": False,
        "hardShadowEffective": False,
        "hardShadowFrameEffective": False,
        "hardShadowBias": HARD_SHADOW_BIAS,
        "hardShadowBindSuccess": True,
        "matrixHash": "same-frame",
        "casterMatrixHash": "same-frame",
        "receiverMatrixHash": "same-frame",
        "receiverShaderRegistered": 1,
        "receiverAssignmentSuccess": 1,
        "receiverAssignmentFailure": 0,
        "receiverTargetResourceHandleNonNull": True,
        "receiverTargetSameFrame": True,
        "receiverTargetsRetained": True,
        "released": False,
    }
    value.update(overrides)
    return value


def test_hard_shadow_witness_requires_requested_and_effective_state() -> None:
    assert _hard_shadow_witness_passes(_witness(), False)
    assert _hard_shadow_witness_passes(
        _witness(hardShadowRequested=True, hardShadowFrameEffective=True), True
    )
    assert not _hard_shadow_witness_passes(
        _witness(hardShadowRequested=True, hardShadowEffective=False), True
    )


def test_hard_shadow_witness_requires_same_frame_assignment_and_retention() -> None:
    assert not _hard_shadow_witness_passes(
        _witness(receiverMatrixHash="different"), False
    )
    assert not _hard_shadow_witness_passes(
        _witness(receiverAssignmentSuccess=0), False
    )
    assert not _hard_shadow_witness_passes(
        _witness(receiverTargetsRetained=False), False
    )
    assert not _hard_shadow_witness_passes(
        _witness(receiverTargetResourceHandleNonNull=False), False
    )


def test_hard_shadow_body_states_accepts_dominant_green_and_blue_pixels() -> None:
    # Eight green-dominant lit pixels and eight blue-dominant occluded pixels
    # exceed the anti-aliasing-safe minimum for this 16-pixel body.
    buffer = bytes(
        [channel for color in ([(0, 240, 16, 255)] * 8 + [(8, 16, 240, 255)] * 8) for channel in color]
    )
    result = _hard_shadow_body_states(buffer, 16, 1, bytes([1] * 16))

    assert result["pass"]
    assert result["source"] == "image"
    assert result["litPixels"] == 8
    assert result["occludedPixels"] == 8


def test_hard_shadow_body_states_rejects_single_color_capture() -> None:
    buffer = bytes([0, 240, 16, 255] * 16)
    result = _hard_shadow_body_states(buffer, 16, 1, bytes([1] * 16))

    assert not result["pass"]
    assert result["litPixels"] == 16
    assert result["occludedPixels"] == 0


def test_hard_shadow_aba_default_changes_body_and_restores_exactly() -> None:
    baseline = bytes([20, 20, 20, 255] * 2)
    control = bytes([0, 240, 16, 255, 8, 16, 240, 255])
    result = _viewport_probe_aba_passes(
        baseline,
        control,
        baseline,
        width=2,
        height=1,
        body_mask=bytes([1, 1]),
    )

    assert result["pass"]
    assert result["aToB"]["outsideBodyDifferingPixels"] == 0
    assert result["aToRestored"]["differingPixels"] == 0


def test_hard_shadow_aba_static_negative_requires_exact_pixels() -> None:
    baseline = bytes([20, 20, 20, 255] * 2)
    control = bytes([0, 240, 16, 255, 8, 16, 240, 255])
    result = _viewport_probe_aba_passes(
        baseline,
        control,
        baseline,
        width=2,
        height=1,
        body_mask=bytes([1, 1]),
        expect_static=True,
    )

    assert not result["pass"]
    assert result["expectStatic"] is True


def test_set_hard_shadow_uses_command_flags_and_parses_witness() -> None:
    calls = []

    class FakeCmds:
        def mmdNativeCasterWitness(self, **kwargs):
            calls.append(kwargs)
            return '{"hardShadowRequested":true}'

    result = _set_hard_shadow(FakeCmds(), lambda _message: None, True)

    assert result["hardShadowRequested"] is True
    assert calls == [
        {"hardShadowCompare": True, "hardShadowBias": HARD_SHADOW_BIAS}
    ]
