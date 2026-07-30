"""Focused regressions for Maya-path mirror selection and pose errors."""

from types import SimpleNamespace

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.mirror_actions import MirrorEntry  # noqa: E402
from mmd_tools.ui.presenters.animation_presenter import AnimationPresenter  # noqa: E402


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


def _presenter_for_mirror_selection(adapter):
    presenter = AnimationPresenter.__new__(AnimationPresenter)
    presenter.maya_adapter = adapter
    presenter.app_state = SimpleNamespace(current_model_root="|char:Hero|MMDModel")
    presenter._mirror_entries = lambda: (
        [
            MirrorEntry(
                identity="left-uuid",
                node="|char:Hero|Skeleton|left_arm",
                joint="|char:Hero|Skeleton|left_arm",
                names=("Left Arm",),
            ),
            MirrorEntry(
                identity="right-uuid",
                node="|char:Hero|Skeleton|right_arm",
                joint="|char:Hero|Skeleton|right_arm",
                names=("Right Arm",),
            ),
        ],
        "MMD_OWNED",
        "model-uuid",
    )
    presenter.selected_by_test = []
    presenter._select_nodes = lambda nodes: (
        presenter.selected_by_test.extend(nodes) or list(nodes)
    )
    presenter.status_by_test = []
    presenter._set_status = lambda key, **values: presenter.status_by_test.append(
        (key, values)
    )
    return presenter


def test_mirror_selection_resolves_short_names_to_namespaced_full_paths():
    adapter = _NamespacedSelectionAdapter()
    presenter = _presenter_for_mirror_selection(adapter)

    presenter.on_mirror_selection()

    assert presenter.selected_by_test == ["|char:Hero|Skeleton|right_arm"]
    assert presenter.status_by_test[0][0] == "mirrored_selection"


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
