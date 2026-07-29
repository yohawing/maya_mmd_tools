"""Define safe keyable channels for generated MMD Control Rig controls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Tuple


TRANSLATE_CHANNELS = ("translateX", "translateY", "translateZ")
ROTATE_CHANNELS = ("rotateX", "rotateY", "rotateZ")
ALL_CHANNELS = (
    *TRANSLATE_CHANNELS,
    *ROTATE_CHANNELS,
    "scaleX",
    "scaleY",
    "scaleZ",
    "visibility",
)

_SUPPORTED_INPUT_KINDS = frozenset(
    {
        "direct_channel",
        "append_base",
        "bone_morph_base",
        "ik_controller",
        "ik_link_input",
    }
)
_ROTATE_ONLY_ROLES = frozenset(
    {
        "waist",
        "lower_body",
        "upper_body",
        "upper_body2",
        "neck",
        "head",
        "left_shoulder",
        "left_arm",
        "left_elbow",
        "left_wrist",
        "right_shoulder",
        "right_arm",
        "right_elbow",
        "right_wrist",
        "left_leg",
        "left_knee",
        "right_leg",
        "right_knee",
    }
)
_TRANSLATE_ROTATE_ROLES = frozenset({"master", "center", "groove"})


@dataclass(frozen=True)
class MmdControlRigChannelPolicy:
    """Maya child-channel state for one generated control."""

    allowed_families: Tuple[str, ...]
    keyable_channels: Tuple[str, ...]
    channel_box_channels: Tuple[str, ...]
    locked_channels: Tuple[str, ...]


def union_mmd_control_rig_channel_policies(
    policies: Tuple[MmdControlRigChannelPolicy, ...],
) -> MmdControlRigChannelPolicy:
    """Union safe policies for aliases sharing one physical control.

    A policy with no exposed authored channel is treated as invalid rather
    than allowing another alias to broaden it.  Callers must separately
    verify that each source binding is supported before invoking this helper.
    """

    if not policies or any(not policy.keyable_channels for policy in policies):
        return _closed_policy()
    keyable = tuple(
        channel
        for channel in (*TRANSLATE_CHANNELS, *ROTATE_CHANNELS)
        if any(channel in policy.keyable_channels for policy in policies)
    )
    if not keyable:
        return _closed_policy()
    families = tuple(
        family
        for family in ("translate", "rotate")
        if any(channel.startswith(family) for channel in keyable)
    )
    return MmdControlRigChannelPolicy(
        allowed_families=families,
        keyable_channels=keyable,
        channel_box_channels=(),
        locked_channels=tuple(channel for channel in ALL_CHANNELS if channel not in keyable),
    )


def derive_mmd_control_rig_channel_policy(
    role: str,
    binding: Any,
) -> MmdControlRigChannelPolicy:
    """Derive a fail-closed policy from role and safe authored plugs."""
    if not role or not _binding_is_supported(binding):
        return _closed_policy()
    keyable = _authored_channels(
        _binding_value(binding, "authored_plugs", "authoredPlugs")
    )
    if role in _ROTATE_ONLY_ROLES or _is_finger_role(role):
        keyable = tuple(channel for channel in keyable if channel.startswith("rotate"))
    elif role not in _TRANSLATE_ROTATE_ROLES and not _is_ik_role(role):
        return _closed_policy()
    if not keyable:
        return _closed_policy()

    families = tuple(
        family
        for family in ("translate", "rotate")
        if any(channel.startswith(family) for channel in keyable)
    )
    return MmdControlRigChannelPolicy(
        allowed_families=families,
        keyable_channels=keyable,
        # Maya's keyable and explicit Channel Box flags are mutually exclusive.
        channel_box_channels=(),
        locked_channels=tuple(channel for channel in ALL_CHANNELS if channel not in keyable),
    )


def apply_mmd_control_rig_channel_policy(
    cmds,
    control: str,
    policy: MmdControlRigChannelPolicy,
) -> None:
    """Apply a derived policy to a newly created Maya control."""
    for channel in ALL_CHANNELS:
        cmds.setAttr(
            f"{control}.{channel}",
            lock=False,
            keyable=False,
            channelBox=False,
        )
    for channel in policy.keyable_channels:
        plug = f"{control}.{channel}"
        cmds.setAttr(plug, lock=False)
        cmds.setAttr(plug, keyable=True)
    for channel in policy.channel_box_channels:
        plug = f"{control}.{channel}"
        cmds.setAttr(plug, lock=False)
        cmds.setAttr(plug, channelBox=True)
    for channel in policy.locked_channels:
        cmds.setAttr(
            f"{control}.{channel}",
            lock=True,
            keyable=False,
            channelBox=False,
        )


def _closed_policy() -> MmdControlRigChannelPolicy:
    return MmdControlRigChannelPolicy((), (), (), ALL_CHANNELS)


def _binding_is_supported(binding: Any) -> bool:
    blockers = _binding_value(binding, "blockers", "blockers")
    return bool(
        binding is not None
        and not _binding_value(binding, "blocked", "blocked")
        and not blockers
        and _binding_value(binding, "input_kind", "inputKind")
        in _SUPPORTED_INPUT_KINDS
    )


def _binding_value(binding: Any, attribute: str, serialized: str) -> Any:
    if isinstance(binding, Mapping):
        return binding.get(serialized, binding.get(attribute))
    return getattr(binding, attribute, None)


def _authored_channels(plugs: Any) -> Tuple[str, ...]:
    if not plugs or isinstance(plugs, (str, bytes)):
        return ()
    channels = set()
    try:
        values = tuple(plugs)
    except TypeError:
        return ()
    for plug in values:
        if not isinstance(plug, str) or "." not in plug:
            return ()
        authored = _attribute_channels(plug.rsplit(".", 1)[-1])
        if not authored:
            return ()
        channels.update(authored)
    return tuple(channel for channel in (*TRANSLATE_CHANNELS, *ROTATE_CHANNELS) if channel in channels)


def _attribute_channels(attribute: str) -> Tuple[str, ...]:
    for family, family_channels in (
        ("translate", TRANSLATE_CHANNELS),
        ("rotate", ROTATE_CHANNELS),
    ):
        title = family.title()
        if attribute in {family, f"base{title}"}:
            return family_channels
        for axis, channel in zip("XYZ", family_channels):
            if attribute in {
                f"{family}{axis}",
                f"base{title}{axis}",
                f"input{title}Element{axis}",
            }:
                return (channel,)
    return ()


def _is_finger_role(role: str) -> bool:
    return role.startswith(("left_", "right_")) and any(
        f"_{finger}_" in role
        for finger in ("thumb", "index", "middle", "ring", "pinky")
    )


def _is_ik_role(role: str) -> bool:
    return role.endswith(("_ik", "_ik_parent"))
