"""Focused regression for Maya-path Mirror Pose errors."""

from types import SimpleNamespace

import pytest

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.presenters.animation_presenter import AnimationPresenter  # noqa: E402
from mmd_tools.ui.mirror_actions import MirrorEntry  # noqa: E402


class _NamespacedSelectionAdapter:
    """Minimal Maya-path adapter that exposes a short selected name."""

    _cmds = object()

    def __init__(self):
        self.selected = ["left_arm"]

    def ls(self, nodes=None, *, selection=False, long=False, **_kwargs):
        if selection:
            return list(self.selected)
        if not long:
            return [nodes] if nodes is not None else []
        paths = {
            "left_arm": "|char:Hero|Skeleton|left_arm",
            "right_arm": "|char:Hero|Skeleton|right_arm",
            "|char:Hero|Skeleton|left_arm": "|char:Hero|Skeleton|left_arm",
            "|char:Hero|Skeleton|right_arm": "|char:Hero|Skeleton|right_arm",
        }
        path = paths.get(str(nodes))
        return [path] if path else []


def test_mirror_pose_real_maya_error_keeps_original_exception():
    adapter = _NamespacedSelectionAdapter()
    presenter = AnimationPresenter.__new__(AnimationPresenter)
    presenter.maya_adapter = adapter
    presenter.app_state = SimpleNamespace(current_model_root="|char:Hero|MMDModel")
    presenter._mirror_mappings_for_selection = lambda: (_ for _ in ()).throw(
        RuntimeError("blocked by incoming writer")
    )
    presenter.status_by_test = []
    presenter._set_status = lambda key, **values: presenter.status_by_test.append(
        (key, values)
    )

    presenter._on_mirror_pose()

    assert presenter.status_by_test[0][0] == "mirror_failed"
    assert isinstance(presenter.status_by_test[0][1]["error"], RuntimeError)


def test_mmd_owned_mirror_requires_persisted_bind_translation():
    presenter = AnimationPresenter.__new__(AnimationPresenter)
    presenter.maya_adapter = SimpleNamespace(
        attribute_exists=lambda _attribute, _joint: False,
    )

    with pytest.raises(RuntimeError, match="bind translation is unavailable"):
        presenter._mirror_bind_translation("|model|left_arm")


def test_control_identity_basis_mirror_does_not_require_bind_context():
    presenter = AnimationPresenter.__new__(AnimationPresenter)
    presenter.maya_adapter = SimpleNamespace(
        _cmds=object(),
        ls=lambda **kwargs: ["|model|left_ik"] if kwargs.get("selection") else [],
    )
    presenter._resolve_selection_path = lambda node: node
    presenter._mirror_entries = lambda: (
        [
            MirrorEntry("left", "|model|left_ik", "|model|left_joint", ("左足ＩＫ",)),
            MirrorEntry("right", "|model|right_ik", "|model|right_joint", ("右足ＩＫ",)),
        ],
        "CONTROL_OWNED",
        "model-uuid",
    )
    presenter._mirror_joint_contexts = lambda _joints: pytest.fail(
        "identity controls must keep the direct mirror path"
    )

    mappings, owner, model_uuid = presenter._mirror_mappings_for_selection()

    assert [(mapping.source.identity, mapping.target.identity) for mapping in mappings] == [
        ("left", "right")
    ]
    assert owner == "CONTROL_OWNED"
    assert model_uuid == "model-uuid"
