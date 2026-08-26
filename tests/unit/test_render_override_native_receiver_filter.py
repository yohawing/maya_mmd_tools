"""Behavior checks for the native receiver-filter probe expectations."""

from tools.render_override.render_override_native_caster_e2e import (
    _receiver_eligible_non_outline_count,
    _viewport_probe_aba_passes,
)


def test_receiver_probe_default_requires_visible_body_change_and_restore() -> None:
    baseline = bytes((32, 64, 96, 255))
    control = bytes((64, 96, 128, 255))
    restored = baseline

    result = _viewport_probe_aba_passes(
        baseline,
        control,
        restored,
        width=1,
        height=1,
        body_mask=bytes((1,)),
    )

    assert result["pass"]
    assert result["expectStatic"] is False
    assert result["aToB"]["differingPixels"] == 1
    assert result["aToRestored"]["differingPixels"] == 0


def test_receiver_probe_static_requires_exact_aba_pixels() -> None:
    baseline = bytes((32, 64, 96, 255))

    result = _viewport_probe_aba_passes(
        baseline,
        baseline,
        baseline,
        width=1,
        height=1,
        body_mask=bytes((1,)),
        expect_static=True,
    )

    assert result["pass"]
    assert result["expectStatic"] is True
    assert result["aToB"]["differingPixels"] == 0
    assert result["aToRestored"]["differingPixels"] == 0


def test_receiver_probe_static_rejects_any_control_difference() -> None:
    baseline = bytes((32, 64, 96, 255))
    control = bytes((32, 64, 97, 255))

    result = _viewport_probe_aba_passes(
        baseline,
        control,
        baseline,
        width=1,
        height=1,
        body_mask=bytes((1,)),
        expect_static=True,
    )

    assert not result["pass"]
    assert result["aToB"]["differingPixels"] == 1
    assert result["aToRestored"]["differingPixels"] == 0


def test_receiver_probe_static_rejects_any_restore_difference() -> None:
    baseline = bytes((32, 64, 96, 255))
    restored = bytes((32, 64, 97, 255))

    result = _viewport_probe_aba_passes(
        baseline,
        baseline,
        restored,
        width=1,
        height=1,
        body_mask=bytes((1,)),
        expect_static=True,
    )

    assert not result["pass"]
    assert result["aToB"]["differingPixels"] == 0
    assert result["aToRestored"]["differingPixels"] == 1


def test_receiver_diagnostics_count_only_non_outline_self_shadow_items() -> None:
    witnesses = {
        "body": {
            "items": [
                {"selfShadow": True, "outline": False},
                {"selfShadow": False, "outline": False},
                {"selfShadow": True, "outline": True},
            ]
        },
        "empty": {"items": []},
    }

    assert _receiver_eligible_non_outline_count(witnesses) == 1
