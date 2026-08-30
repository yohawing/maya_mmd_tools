"""Presenter for the Animator Toolset tab."""

from __future__ import annotations

from dataclasses import replace
import json
import math
from typing import TYPE_CHECKING

from ...core.constants import (
    ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_BONE_NAME_EN,
    ATTR_MMD_DISPLAY_FRAMES_JSON,
    ATTR_MMD_MORPH_DATA,
)
from ...core.display_frame_resolver import PickerGroup, resolve_display_frames
from ...core.logger import get_logger
from ...core.maya_identity import same_node_identity
from ...core.mmd_bone_names import normalize_mmd_bone_name
from ...core.mmd_control_rig_builder import inspect_mmd_control_rig
from ...core.morph_metadata_reader import (
    CategorizedMorphs,
    MorphInfo,
    categorize_morphs,
    parse_blendshape_morph_entries,
    morph_info_from_presenter_entry,
)
from ...core.model_registry import (
    REGISTRY_CATEGORY_MORPH,
    list_model_registry_members_from_adapter,
)
from ...core.visibility_state import (
    VisibilityState,
    get_visibility_state,
    get_visibility_group_state,
    resolve_visibility_group,
    set_visibility_group_state,
    set_visibility_state,
    set_visibility_category,
    sync_visibility_connections,
)
from ..combo_box_utils import add_combo_item_with_tooltip

if TYPE_CHECKING:
    from ..application_state import ApplicationState

logger = get_logger(__name__)

_USER_ROLE = 0x0100  # Qt.UserRole
_SELECTION_VISIBLE = "visible"
_SELECTION_BLOCKED = "blocked"
_SELECTION_UNKNOWN = "unknown"
_IK_AUTHORITY_UNKNOWN = "UNKNOWN"

_IK_BONE_NAMES = {
    "left": "左足ＩＫ",
    "right": "右足ＩＫ",
}
_TOE_IK_BONE_NAMES = {
    "left": "左つま先ＩＫ",
    "right": "右つま先ＩＫ",
}
_CONTROL_IK_ROLES = {
    "left": "left_foot_ik",
    "right": "right_foot_ik",
}
_CONTROL_TOE_IK_ROLES = {
    "left": "left_toe_ik",
    "right": "right_toe_ik",
}
_LEG_FK_REGIONS = {
    "left": {"left_lower_leg", "left_foot"},
    "right": {"right_lower_leg", "right_foot"},
}
_TOE_FK_REGIONS = {
    "left": {"left_toe"},
    "right": {"right_toe"},
}


class AnimationPresenter:
    """Drives the AnimationTab (Body/Finger/Morph/Display picker + tools)."""

    def __init__(
        self,
        view,
        app_state: ApplicationState,
        maya_adapter=None,
    ):
        self.view = view
        self.app_state = app_state
        if maya_adapter is None:
            from ...adapters.maya_cmds_adapter import MayaCmdsAdapter

            maya_adapter = MayaCmdsAdapter()
        self.maya_adapter = maya_adapter
        self._picker_groups: list[PickerGroup] = []
        self._bone_name_to_joint: dict[str, str] = {}
        self._picker_english_tooltips: dict[str, dict[str, str]] = {
            "body": {},
            "finger": {},
        }
        self._morph_sliders: dict[str, object] = {}
        self._morph_rows: dict[str, object] = {}
        self._morph_group_headers: list[tuple[object, str, int]] = []
        self._morph_targets: dict[str, list[tuple[str, int]]] = {}
        self._network_morph_targets: dict[str, list[str]] = {}
        self._morph_indices: dict[str, int] = {}
        self._morph_controller: str | None = None
        self._morph_edit_open = False
        self._morph_refresh_timer = None
        self._last_morph_refresh_time: float | None = None
        self._pose_clipboard: dict | None = None
        self._all_model_joints: list[str] = []
        self._ik_nodes_by_side: dict[str, str] = {}
        self._toe_ik_nodes_by_side: dict[str, str] = {}
        self._control_ik_nodes_by_side: dict[str, str] = {}
        self._control_toe_ik_nodes_by_side: dict[str, str] = {}
        # Ownership is unknown until the model metadata has been read.  A
        # failed read must not make the legacy solver look authoritative.
        self._ik_authority_owner = _IK_AUTHORITY_UNKNOWN
        self._ik_authority_model_root: str | None = None
        self._last_ik_states: dict[str, bool] = {}
        self._visibility_history_jobs: list[int] = []
        self._selection_sync_jobs: list[int] = []
        self._disposed = False
        # Animator Reset Pose is a one-shot selection action, distinct from
        # Bone Editor's model-wide Go to Bind Pose inspection command.
        self.connect_signals()
        self._install_visibility_history_jobs()
        self._install_selection_sync_job()
        self._start_morph_refresh_timer()

        if self.app_state.current_model_root:
            self._reload_for_model(self.app_state.current_model_root)
        else:
            self._sync_visibility_controls(None)

    def connect_signals(self):
        self.app_state.current_model_changed.connect(self.on_current_model_changed)
        self.app_state.model_list_updated.connect(self.on_model_list_updated)
        self.view.model_combo.currentTextChanged.connect(self.on_model_selected)
        self.view.refresh_btn.clicked.connect(self.on_refresh_clicked)
        self.view.clear_btn.clicked.connect(self.on_clear_clicked)
        display_pressed = getattr(self.view.display_frame_tree, "itemPressed", None)
        if display_pressed is not None:
            display_pressed.connect(self.on_display_frame_item_clicked)
        else:
            self.view.display_frame_tree.itemClicked.connect(self.on_display_frame_item_clicked)
        self.view.body_picker.region_clicked.connect(self.on_body_region_clicked)
        if hasattr(self.view.body_picker, "regions_selected"):
            self.view.body_picker.regions_selected.connect(self.on_body_regions_selected)
        self.view.body_picker.goto_finger_clicked.connect(self.on_goto_finger)
        if hasattr(self.view.body_picker, "reset_pose_clicked"):
            self.view.body_picker.reset_pose_clicked.connect(self._on_reset_pose)
        if hasattr(self.view.body_picker, "select_all_clicked"):
            self.view.body_picker.select_all_clicked.connect(self.on_select_all)
        if hasattr(self.view.body_picker, "clear_selection_clicked"):
            self.view.body_picker.clear_selection_clicked.connect(self.on_clear_clicked)
        for picker in (self.view.body_picker, self.view.finger_picker):
            if hasattr(picker, "background_clicked"):
                picker.background_clicked.connect(self.on_clear_clicked)
        if hasattr(self.view.body_picker, "ik_toggled"):
            self.view.body_picker.ik_toggled.connect(self.on_ik_toggled)
        if hasattr(self.view.body_picker, "ik_enable_toggle_clicked"):
            self.view.body_picker.ik_enable_toggle_clicked.connect(
                self.on_ik_enable_toggle_clicked
            )
        self.view.finger_picker.region_clicked.connect(self.on_finger_region_clicked)
        if hasattr(self.view.finger_picker, "regions_selected"):
            self.view.finger_picker.regions_selected.connect(self.on_finger_regions_selected)
        self.view.finger_picker.goto_body_clicked.connect(self.on_goto_body)
        if hasattr(self.view, "select_all_btn"):
            self.view.select_all_btn.clicked.connect(self.on_select_all)
        for key, cb in self.view.vis_checkboxes.items():
            capability = getattr(cb, "is_tri_state", None)
            if capability is None:
                capability = getattr(cb, "isTriState", False)
            if callable(capability):
                capability = capability()
            is_tri_state = bool(capability)
            if is_tri_state:
                visibility_signal = getattr(cb, "visibilityStateChanged", None)
                if visibility_signal is None:
                    continue
                visibility_signal.connect(
                    lambda state, k=key: self._on_visibility_state_changed(k, state)
                )
                continue
            # Keep the legacy bool signal for third-party and headless views.
            cb.stateChanged.connect(
                lambda state, k=key: self._on_visibility_changed(k, state != 0)
            )
        for key, btn in self.view.tool_buttons.items():
            btn.clicked.connect(
                lambda _checked=False, k=key: self._on_tool_clicked(k)
            )
        common_actions = getattr(self.view, "common_action_buttons", {})
        callbacks = {
            "reset": self._on_reset_pose,
            "mirror": self._on_mirror_pose,
        }
        for key, btn in common_actions.items():
            callback = callbacks.get(key)
            if callback is not None:
                btn.clicked.connect(lambda _checked=False, cb=callback: cb())
        self._sync_common_action_state()

    def disconnect_signals(self):
        """Release shared listeners and timers exactly once.

        Maya may destroy a docked view through its workspaceControl before the
        Python window receives ``closeEvent``.  Mark the presenter disposed
        first so teardown cannot touch already-deleted Qt objects.
        """
        if self._disposed:
            return
        self._disposed = True
        self._end_morph_edit()
        if self._morph_refresh_timer is not None:
            try:
                self._morph_refresh_timer.stop()
            except RuntimeError:
                pass
            self._morph_refresh_timer = None
        self._remove_visibility_history_jobs()
        self._remove_selection_sync_jobs()
        try:
            self.app_state.current_model_changed.disconnect(self.on_current_model_changed)
        except (RuntimeError, TypeError):
            pass
        try:
            self.app_state.model_list_updated.disconnect(self.on_model_list_updated)
        except (RuntimeError, TypeError):
            pass

    def _install_visibility_history_jobs(self) -> None:
        """Read back scene-authoritative visibility after Maya undo/redo."""

        cmds_module = getattr(self.maya_adapter, "_cmds", None)
        script_job = getattr(cmds_module, "scriptJob", None)
        if not callable(script_job):
            return
        for event_name in ("Undo", "Redo"):
            try:
                job_id = script_job(
                    event=[event_name, self._schedule_visibility_history_sync],
                    protected=True,
                )
                self._visibility_history_jobs.append(int(job_id))
            except Exception:
                logger.debug("Could not install %s visibility callback", event_name)

    def _remove_visibility_history_jobs(self) -> None:
        """Remove presenter-owned Maya history callbacks."""

        cmds_module = getattr(self.maya_adapter, "_cmds", None)
        script_job = getattr(cmds_module, "scriptJob", None)
        jobs, self._visibility_history_jobs = self._visibility_history_jobs, []
        if not callable(script_job):
            return
        for job_id in jobs:
            try:
                if script_job(exists=job_id):
                    script_job(kill=job_id, force=True)
            except Exception:
                logger.debug("Could not remove visibility callback %s", job_id)

    def _install_selection_sync_job(self) -> None:
        """Observe viewport/Outliner selection without writing Maya state."""

        cmds_module = getattr(self.maya_adapter, "_cmds", None)
        script_job = getattr(cmds_module, "scriptJob", None)
        if not callable(script_job):
            return
        try:
            job_id = script_job(
                event=["SelectionChanged", self._schedule_selection_sync],
                protected=True,
            )
            self._selection_sync_jobs.append(int(job_id))
        except Exception:
            logger.debug("Could not install SelectionChanged callback", exc_info=True)

    def _remove_selection_sync_jobs(self) -> None:
        """Remove presenter-owned selection callbacks during teardown."""

        cmds_module = getattr(self.maya_adapter, "_cmds", None)
        script_job = getattr(cmds_module, "scriptJob", None)
        jobs, self._selection_sync_jobs = self._selection_sync_jobs, []
        if not callable(script_job):
            return
        for job_id in jobs:
            try:
                if script_job(exists=job_id):
                    script_job(kill=job_id, force=True)
            except Exception:
                logger.debug("Could not remove SelectionChanged callback %s", job_id)

    def _sync_visibility_after_history(self) -> None:
        """Refresh buttons without repairing connections or mutating the scene."""

        if self._disposed:
            return
        self._sync_visibility_controls(
            self.app_state.current_model_root or None,
            ensure_connections=False,
        )

    def _schedule_visibility_history_sync(self) -> None:
        """Defer readback until Maya has finished applying undo or redo."""

        try:
            from ..qt_compat import QTimer

            # Script jobs run on Maya idle; defer once more so Qt repaint and
            # scene-authoritative plug readback share the same event turn.
            QTimer.singleShot(0, self._sync_visibility_after_history)
        except Exception:
            self._sync_visibility_after_history()

    def _schedule_selection_sync(self) -> None:
        """Defer selection readback until Maya finishes the selection event."""

        if self._disposed:
            return
        try:
            from ..qt_compat import QTimer

            QTimer.singleShot(0, self._sync_picker_to_actual_selection)
        except Exception:
            self._sync_picker_to_actual_selection()

    def retranslate_ui(self):
        """Retranslate presenter-owned dynamic controls without reloading the model."""

        for header, category_key, count in self._morph_group_headers:
            expanded = header.isChecked()
            title = self.view.tr(category_key, "animation_toolset")
            header.setText(f"{'▾' if expanded else '▸'}  {title}    {count}")
        self._retranslate_picker_bone_tooltips()
        self._sync_common_action_state()

    # -- Signal handlers -----------------------------------------------

    def on_current_model_changed(self, model_root: str):
        if model_root:
            self._reload_for_model(model_root)
        else:
            self._clear_all()

    def on_model_list_updated(self, models: list):
        self._update_model_combo(models)

    def on_model_selected(self, model_text: str):
        if model_text and model_text != self.app_state.current_model_root:
            self.app_state.current_model_root = model_text

    def on_refresh_clicked(self):
        self.app_state.refresh_model_list()
        model = self.app_state.current_model_root
        if model:
            self._reload_for_model(model)

    def refresh_for_scene_change(self) -> None:
        """Refresh model/picker state after Maya replaces the current scene.

        Scene callbacks run after the old DAG has been discarded.  Reusing a
        stale model root here would make UUID-backed Control Rig metadata point
        at unrelated nodes, so clear the cache first and let ApplicationState
        resolve the new model list.
        """

        if self._disposed:
            return
        # A scene replacement may reuse the same DAG path.  Do not let the
        # same-root transient-failure preservation below carry UUID-owned
        # controls from the previous scene into the new one.
        self._ik_authority_owner = _IK_AUTHORITY_UNKNOWN
        self._ik_authority_model_root = None
        self._control_ik_nodes_by_side = {}
        self._control_toe_ik_nodes_by_side = {}
        self._last_ik_states = {}
        try:
            self.app_state.clear_cache()
        except Exception:
            logger.debug("Animator scene refresh cache clear failed", exc_info=True)
        try:
            self.app_state.refresh_model_list()
        except Exception:
            logger.debug("Animator scene refresh failed", exc_info=True)
            self._clear_all()
            return

        # A newly opened scene may contain a model with the same DAG path as
        # the previous scene.  ApplicationState intentionally suppresses its
        # change signals when the string-valued model list is unchanged, so a
        # scene callback must explicitly reload UUID-backed rig metadata and
        # picker state instead of leaving the previous scene cached in the UI.
        model = self.app_state.current_model_root
        if model:
            self._reload_for_model(model)
        else:
            self._clear_all()

    def on_clear_clicked(self):
        try:
            self._select_nodes([])
        except Exception:
            pass
        self.view.status_label.setText("")

    def on_ik_toggled(self, side: str, enabled: bool) -> None:
        """Set one leg IK solver state without authoring animation keys."""

        side_label = {"left": "L", "right": "R"}.get(side, side.upper())
        node = self._active_ik_nodes_by_side().get(side)
        if not node:
            self._set_status("ik_not_found", side=side_label)
            return
        try:
            self.maya_adapter.set_attr(
                f"{node}.{self._active_ik_attribute()}", bool(enabled)
            )
        except Exception as exc:
            logger.debug("IK toggle failed for %s: %s", node, exc)
            self._set_status("ik_toggle_failed", error=exc)
            return
        self._sync_ik_picker_state(force=True)
        self._set_status("ik_enabled" if enabled else "ik_disabled", side=side_label)

    def on_ik_enable_toggle_clicked(self, side: str) -> None:
        """Toggle one side's foot and toe IK without authoring animation keys."""

        side_label = "L" if side == "left" else "R"
        nodes = tuple(
            dict.fromkeys(
                node
                for node in (
                    self._active_ik_nodes_by_side().get(side),
                    self._active_toe_ik_nodes_by_side().get(side),
                )
                if node
            )
        )
        if not nodes:
            self._set_status("ik_not_found", side=side_label)
            return
        current_states = {}
        for node in nodes:
            try:
                current_states[node] = bool(
                    self.maya_adapter.get_attr(
                        f"{node}.{self._active_ik_attribute()}"
                    )
                )
            except Exception:
                current_states[node] = False
        enabled = not all(current_states.values())
        updated_nodes = []
        try:
            for node in nodes:
                self.maya_adapter.set_attr(
                    f"{node}.{self._active_ik_attribute()}", enabled
                )
                updated_nodes.append(node)
        except Exception as exc:
            for node in reversed(updated_nodes):
                try:
                    self.maya_adapter.set_attr(
                        f"{node}.{self._active_ik_attribute()}", current_states[node]
                    )
                except Exception:
                    logger.debug("Failed to restore IK Enable for %s", node)
            logger.debug("IK Enable toggle failed: %s", exc)
            self._sync_ik_picker_state(force=True)
            self._set_status("ik_toggle_failed", error=exc)
            return
        self._sync_ik_picker_state(force=True)
        self._set_status("ik_enabled" if enabled else "ik_disabled", side=side_label)

    def _set_status(self, key: str, **values) -> None:
        """Show a localized Animator status message."""

        message = self.view.tr(key, "animation_toolset")
        self.view.status_label.setText(message.format(**values))

    def _sync_common_action_state(self) -> None:
        """Keep the shared one-shot Reset Pose action localized."""

        buttons = getattr(self.view, "common_action_buttons", {})
        button = buttons.get("reset") if hasattr(buttons, "get") else None
        if button is None:
            return
        try:
            button.setText(self.view.tr("reset", "animation_toolset"))
            if hasattr(button, "setToolTip"):
                button.setToolTip(
                    self.view.tr("reset_pose_tooltip", "animation_toolset")
                )
            if hasattr(button, "setEnabled"):
                button.setEnabled(True)
        except Exception:
            logger.debug("Could not sync common Rest Pose action state", exc_info=True)

    def on_select_all(self):
        """Select every indexed joint belonging to the current MMD model."""

        joints = [
            preferred
            for joint in self._all_model_joints
            if (preferred := self._preferred_rig_control(joint))
        ]
        if not joints:
            self._set_status("no_selectable_bones")
            return
        try:
            accepted = self._select_nodes(joints)
            if accepted:
                self._set_status("selected_all_bones", count=len(accepted))
            else:
                self._set_status("no_selectable_bones")
        except Exception:
            self._set_status("select_all_failed")

    def on_display_frame_item_clicked(self, item, _column=0):
        node_name = item.data(0, _USER_ROLE)
        if not node_name:
            return
        try:
            accepted = self._select_nodes([self._preferred_rig_control(node_name)])
            if accepted:
                self.view.status_label.setText(item.text(0))
            else:
                self._set_status("no_selectable_bones")
        except Exception:
            self._set_status("node_not_found", name=node_name)

    def on_body_region_clicked(self, region_id: str):
        self._select_picker_regions(
            [region_id],
            picker="body",
            additive=self.view.body_picker.additive_selection,
            subtractive=getattr(self.view.body_picker, "subtractive_selection", False),
        )

    def on_body_regions_selected(self, region_ids: list[str]):
        self._select_picker_regions(
            region_ids,
            picker="body",
            additive=self.view.body_picker.additive_selection,
            subtractive=getattr(self.view.body_picker, "subtractive_selection", False),
        )

    def on_finger_region_clicked(self, region_id: str):
        self._select_picker_regions(
            [region_id],
            picker="finger",
            additive=self.view.finger_picker.additive_selection,
            subtractive=getattr(self.view.finger_picker, "subtractive_selection", False),
        )

    def on_finger_regions_selected(self, region_ids: list[str]):
        self._select_picker_regions(
            region_ids,
            picker="finger",
            additive=self.view.finger_picker.additive_selection,
            subtractive=getattr(self.view.finger_picker, "subtractive_selection", False),
        )

    def _select_picker_regions(
        self,
        region_ids: list[str],
        *,
        picker: str,
        additive: bool = False,
        subtractive: bool = False,
    ) -> None:
        """Resolve one or more picker regions and update the UI before Maya blocks."""

        if picker == "body":
            from ..widgets.body_picker_widget import _BODY_REGIONS as regions
        else:
            from ..widgets.finger_picker_widget import _FINGER_REGIONS as regions

        by_id = {region["id"]: region["bone_name"] for region in regions}
        labels = [by_id[region_id] for region_id in region_ids if region_id in by_id]
        joints = []
        for label in labels:
            normalized = normalize_mmd_bone_name(label) or label
            joint = self._bone_name_to_joint.get(normalized)
            if joint and joint not in joints:
                preferred = self._preferred_rig_control(joint)
                if preferred:
                    joints.append(preferred)

        self.view.status_label.setText("、".join(labels))
        if not joints:
            if labels:
                self._set_status("unassigned_bones", names="、".join(labels))
            try:
                self._set_picker_selection_from_nodes(
                    self.maya_adapter.ls(selection=True) or []
                )
            except Exception:
                pass
            return
        try:
            if subtractive:
                self._deselect_nodes(joints)
                return
            accepted = self._select_nodes(joints, replace=not additive)
            if not accepted:
                self._set_status("no_selectable_bones")
        except Exception:
            self._set_status("selection_failed", names="、".join(labels))

    def _set_picker_selection_from_nodes(self, nodes: list[str]) -> None:
        """Reflect Maya joint names as strong picker highlights synchronously."""

        from ..widgets.body_picker_widget import _BODY_REGIONS
        from ..widgets.finger_picker_widget import _FINGER_REGIONS

        active_plugs = tuple(
            self._canonical_morph_selection_plug(node) for node in (nodes or [])
        )
        resolved_nodes = []
        for node in active_plugs:
            joint = self._joint_for_rig_control(node)
            if joint:
                resolved_nodes.append(joint)
        nodes = resolved_nodes

        # Maya may return a short name while UUID-backed rig resolution returns
        # a full DAG path (or vice versa).  The picker map belongs to one model,
        # so its namespace-preserving leaf name is the stable comparison key.
        def node_key(node: str) -> str:
            return str(node).rsplit("|", 1)[-1]

        joint_to_bone = {
            node_key(joint): bone for bone, joint in self._bone_name_to_joint.items()
        }
        selected_bones = {
            joint_to_bone[node_key(node)]
            for node in nodes
            if node_key(node) in joint_to_bone
        }

        def selected_ids(regions):
            return [
                region["id"]
                for region in regions
                if (normalize_mmd_bone_name(region["bone_name"]) or region["bone_name"])
                in selected_bones
            ]

        if hasattr(self.view.body_picker, "set_selected_regions"):
            self.view.body_picker.set_selected_regions(selected_ids(_BODY_REGIONS))
        if hasattr(self.view.finger_picker, "set_selected_regions"):
            self.view.finger_picker.set_selected_regions(selected_ids(_FINGER_REGIONS))

        # Morph rows are selected only when Maya's active plug set is exactly
        # the row's authoritative set.  This intentionally clears every row
        # for mixed node/plug selections or an unrelated external selection.
        for row in tuple(getattr(self, "_morph_rows", {}).values()):
            set_selected = getattr(row, "set_selected", None)
            if callable(set_selected):
                plugs = tuple(str(plug) for plug in (getattr(row, "plugs", ()) or ()))
                selected = bool(plugs) and len(active_plugs) == len(plugs) and set(
                    active_plugs
                ) == set(plugs)
                set_selected(selected)

    def _select_nodes(self, nodes: list[str], *, replace: bool = True) -> list[str]:
        """Select only candidates inside currently visible model boundaries.

        Selection is deliberately guarded at this single write point so body,
        finger, display-tree, mirror and Select All callers cannot highlight a
        node that Maya will not display or allow to pick.  Candidates are
        resolved to one full DAG path before classification; unresolved or
        ambiguous paths are rejected fail-closed.  An empty explicit request
        remains the one operation that clears Maya's active selection.

        Returns:
            The accepted candidate spellings, preserving input order and
            de-duplicated by resolved full DAG identity.
        """

        requested = list(nodes or [])
        if not requested:
            self._write_selection([], replace=True)
            self._sync_picker_to_actual_selection()
            return []

        boundaries, known_joints = self._selection_visibility_boundaries()
        accepted = []
        seen_paths = set()
        for candidate in requested:
            resolved = self._resolve_selection_path(candidate)
            if resolved is None or resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            if self._selection_path_blocked(resolved, boundaries, known_joints):
                continue
            accepted.append(candidate)

        # A blocked/invalid batch must not clear or replace an existing Maya
        # selection.  This is especially important for additive picker clicks:
        # guard the requested candidates, then preserve the current selection.
        if accepted:
            self._write_selection(accepted, replace=replace)
        self._sync_picker_to_actual_selection()
        return accepted

    def _deselect_nodes(self, nodes: list[str]) -> list[str]:
        """Remove resolved picker targets while preserving every other selection."""

        accepted = []
        seen_paths = set()
        for node in nodes:
            path = self._resolve_selection_path(node)
            if path is None or path in seen_paths:
                continue
            seen_paths.add(path)
            accepted.append(node)

        if accepted:
            deselect_fast = getattr(self.maya_adapter, "deselect_fast", None)
            if callable(deselect_fast):
                deselect_fast(accepted)
            else:
                self.maya_adapter.deselect(accepted)
        self._sync_picker_to_actual_selection()
        return accepted

    def _write_selection(self, nodes: list[str], *, replace: bool = True) -> None:
        """Write an already-validated selection through the fastest adapter path."""

        select_fast = getattr(self.maya_adapter, "select_fast", None)
        if callable(select_fast):
            select_fast(nodes, replace=replace)
        else:
            self.maya_adapter.select(nodes, replace=replace)

    def _sync_picker_to_actual_selection(self) -> None:
        """Read Maya's selection after a guard decision and update highlights."""

        try:
            actual = self.maya_adapter.ls(selection=True) or []
        except Exception:
            actual = []
        self._set_picker_selection_from_nodes(actual)

    def _canonical_morph_selection_plug(self, item: str) -> str:
        """Resolve Maya's alias spelling back to the authored plug path."""

        plug = str(item)
        node, separator, attribute = plug.partition(".")
        if not separator:
            return plug
        try:
            aliases = self.maya_adapter.alias_attr(node, query=True) or []
        except Exception:
            return plug
        for index in range(0, len(aliases) - 1, 2):
            if str(aliases[index]) == attribute:
                return f"{node}.{aliases[index + 1]}"
        return plug

    def _resolve_selection_path(self, candidate: str) -> str | None:
        """Resolve one candidate to exactly one full DAG path."""

        if not isinstance(candidate, str) or not candidate:
            return None
        try:
            resolved = self.maya_adapter.ls(candidate, long=True) or []
        except Exception:
            return None
        if len(resolved) != 1 or not isinstance(resolved[0], str):
            return None
        path = str(resolved[0])
        return path if path.startswith("|") else None

    def _selection_visibility_boundaries(self):
        """Read selection ownership and fail-closed visibility context.

        Boundary resolution is intentionally separate from the generic
        visibility helpers.  Those helpers preserve legacy fail-open behavior
        for UI toggles; picker selection must instead reject model-owned nodes
        when a group or one of its plugs cannot be proven readable.
        """

        root = self.app_state.current_model_root
        if not root:
            return (), set()

        boundaries = []
        try:
            skeleton = resolve_visibility_group(self.maya_adapter, root, "joints")
        except Exception:
            skeleton = None
        if skeleton:
            skeleton_state = self._read_selection_group_state(skeleton)
            boundaries.append(
                {
                    "group": skeleton,
                    "state": skeleton_state,
                    "owned": set(),
                }
            )
        else:
            # Keep an explicit unknown Skeleton boundary so known model joints
            # are rejected below even when the direct group is ambiguous or
            # missing from a partially-authored scene.
            boundaries.append(
                {"group": None, "state": _SELECTION_UNKNOWN, "owned": set()}
            )

        # The normal path classifies directly from one resolved group prefix.
        # Resolve the full model-joint identity set only when Skeleton
        # ownership is unknown, avoiding hundreds of Maya ls round-trips per
        # ordinary picker click on larger rigs.
        skeleton_context = boundaries[0]
        known_joints = set()
        if (
            skeleton_context["state"] == _SELECTION_UNKNOWN
            and not skeleton_context["group"]
        ):
            candidates = dict.fromkeys(
                (*self._all_model_joints, *self._bone_name_to_joint.values())
            )
            for candidate in candidates:
                resolved = self._resolve_selection_path(candidate)
                if resolved:
                    known_joints.add(resolved)

        cmds_module = getattr(self.maya_adapter, "_cmds", None)
        control_inspection = None
        control_error = False
        if root and cmds_module is not None:
            try:
                control_inspection = inspect_mmd_control_rig(
                    root, cmds_module=cmds_module
                )
            except Exception:
                control_error = True

        if control_inspection is not None:
            control_group = getattr(control_inspection, "control_group", None)
            if isinstance(control_group, str) and control_group:
                controls = set()
                for node in getattr(control_inspection, "controls", {}).values():
                    resolved = self._resolve_selection_path(node)
                    if resolved:
                        controls.add(resolved)
                boundaries.append(
                    {
                        "group": control_group,
                        "state": self._read_selection_group_state(control_group),
                        "owned": controls,
                    }
                )
        elif control_error and cmds_module is not None:
            # A failed topology inspection must not turn validated UUIDs into
            # selectable nodes.  Readable metadata is only used to resolve
            # exact control identities; its topology/group is not trusted.
            try:
                from ...core.mmd_control_rig_builder import read_mmd_control_rig_metadata

                metadata = read_mmd_control_rig_metadata(
                    root, cmds_module=cmds_module
                )
            except Exception:
                metadata = None
            if isinstance(metadata, dict):
                owned = set()
                for uuid in (metadata.get("controls") or {}).values():
                    resolved = self._resolve_selection_path(uuid)
                    if resolved:
                        owned.add(resolved)
                if owned:
                    boundaries.append(
                        {
                            "group": None,
                            "state": _SELECTION_UNKNOWN,
                            "owned": owned,
                        }
                    )
        return tuple(boundaries), known_joints

    def _read_selection_group_state(self, group: str) -> str:
        """Read raw visibility/override plugs with fail-closed error handling."""

        try:
            visibility = self.maya_adapter.get_attr(f"{group}.visibility")
            override_enabled = self.maya_adapter.get_attr(
                f"{group}.overrideEnabled"
            )
            display_type = self.maya_adapter.get_attr(
                f"{group}.overrideDisplayType"
            )
            if visibility is None or override_enabled is None or display_type is None:
                return _SELECTION_UNKNOWN
            if not bool(visibility):
                return _SELECTION_BLOCKED
            if bool(override_enabled) and int(display_type) == 2:
                return _SELECTION_BLOCKED
        except Exception:
            return _SELECTION_UNKNOWN
        return _SELECTION_VISIBLE

    @staticmethod
    def _selection_path_blocked(
        path: str, boundaries, known_joints: set[str]
    ) -> bool:
        """Return whether a resolved path is inside a non-visible boundary."""

        for boundary in boundaries:
            group = boundary["group"]
            state = boundary["state"]
            owned = boundary["owned"]
            if path in owned:
                return state != _SELECTION_VISIBLE
            if group and (path == group or path.startswith(f"{group}|")):
                return state != _SELECTION_VISIBLE
        # If Skeleton ownership itself is unknown, known model joints are
        # rejected even without a usable group prefix.  A valid but blocked
        # group only blocks descendants; unrelated paths remain selectable.
        skeleton = boundaries[0] if boundaries else None
        if (
            path in known_joints
            and skeleton is not None
            and skeleton["state"] == _SELECTION_UNKNOWN
        ):
            return True
        return False

    def _preferred_rig_control(self, joint: str) -> str | None:
        """Prefer the owned curve corresponding to a picker joint."""
        root = self.app_state.current_model_root
        if not root:
            return joint
        adapter_cmds = getattr(self.maya_adapter, "_cmds", None)
        try:
            cmds = adapter_cmds
            if cmds is None:
                from maya import cmds

            from ...core.mmd_control_rig_builder import (
                read_mmd_control_rig_metadata,
                resolve_mmd_control_rig_binding_joint,
            )

            metadata = read_mmd_control_rig_metadata(root, cmds_module=cmds)
            # Picker selection follows the motion owner.  In MMD-owned mode,
            # joints remain the authoring surface; only Control-owned mode may
            # select UUID-backed controller transforms.
            owner = metadata.get("owner") if metadata else None
            if owner is None and metadata:
                # Older presenter extensions supplied only ``controls`` and
                # ``bindings``.  Keep that compatibility path; validated
                # scene metadata always includes the explicit owner field.
                owner = "CONTROL_OWNED"
            if not metadata or owner not in {"MMD_OWNED", "CONTROL_OWNED"}:
                return joint
            target_nodes = cmds.ls(joint, long=True) or []
            if len(target_nodes) != 1:
                return None
            target = str(target_nodes[0])
            matched_controls = []
            for role, binding in metadata.get("bindings", {}).items():
                bound = resolve_mmd_control_rig_binding_joint(cmds, binding)
                if bound != target:
                    continue
                control_uuid = metadata.get("controls", {}).get(role)
                nodes = cmds.ls(control_uuid, long=True) if control_uuid else []
                if len(nodes) == 1:
                    if str(nodes[0]) not in matched_controls:
                        matched_controls.append(str(nodes[0]))
            if len(matched_controls) == 1:
                return matched_controls[0]
            # A stale/ambiguous UUID must not silently select a same-named
            # joint or another character's controller.
            return None
        except Exception:
            logger.debug("MMD Control Rig picker lookup failed", exc_info=True)
        # Lightweight presenter test adapters do not expose a cmds module;
        # their model has no UUID-backed Control Rig authority to resolve.
        return joint if adapter_cmds is None else None

    def _joint_for_rig_control(self, node: str) -> str | None:
        """Map an active Control Rig node to one UUID-owned binding joint.

        A malformed or ambiguous Control Rig selection must not fall through
        to a same-named joint.  Returning ``None`` is the fail-closed signal
        consumed by ``_set_picker_selection_from_nodes``.
        """
        root = self.app_state.current_model_root
        if not root:
            return node
        adapter_cmds = getattr(self.maya_adapter, "_cmds", None)
        try:
            cmds = adapter_cmds
            if cmds is None:
                from maya import cmds

            from ...core.mmd_control_rig_builder import (
                read_mmd_control_rig_metadata,
                resolve_mmd_control_rig_binding_joint,
            )

            metadata = read_mmd_control_rig_metadata(root, cmds_module=cmds)
            owner = metadata.get("owner") if metadata else None
            if owner is None and metadata:
                owner = "CONTROL_OWNED"
            if not metadata or owner not in {"MMD_OWNED", "CONTROL_OWNED"}:
                return node
            selected_uuids = cmds.ls(node, uuid=True) or []
            if len(selected_uuids) != 1:
                return None
            selected = str(selected_uuids[0])
            selected_paths = cmds.ls(node, long=True) or []
            if len(selected_paths) != 1:
                return None
            selected_path = str(selected_paths[0])
            matched_joints = []
            for role, uuid in metadata.get("controls", {}).items():
                binding = metadata.get("bindings", {}).get(role)
                if not binding:
                    continue
                joint = resolve_mmd_control_rig_binding_joint(cmds, binding)
                if uuid == selected or joint == selected_path:
                    if joint not in matched_joints:
                        matched_joints.append(joint)
            if len(matched_joints) == 1:
                return matched_joints[0]
            return None
        except Exception:
            logger.debug("MMD Control Rig reverse picker lookup failed", exc_info=True)
        return node if adapter_cmds is None else None

    def on_goto_finger(self):
        self.view.picker_tabs.setCurrentIndex(self.view.TAB_FINGER)

    def on_goto_body(self):
        self.view.picker_tabs.setCurrentIndex(self.view.TAB_BODY)

    def _mirror_bind_translation(self, joint: str):
        """Read an imported joint's absolute local bind translation."""

        attribute = "mmd_vmd_bind_translate"
        try:
            if not self.maya_adapter.attribute_exists(attribute, joint):
                raise RuntimeError(f"MMD mirror bind translation is unavailable: {joint}")
            raw = self.maya_adapter.get_attr(f"{joint}.{attribute}")
            values = json.loads(raw) if isinstance(raw, str) else raw
            values = tuple(float(value) for value in values)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"MMD mirror bind translation is invalid: {joint}") from exc
        except Exception as exc:
            raise RuntimeError(f"MMD mirror bind translation is unavailable: {joint}") from exc
        if len(values) != 3 or not all(math.isfinite(value) for value in values):
            raise RuntimeError(f"MMD mirror bind translation is invalid: {joint}")
        return values

    def _mirror_joint_contexts(self, joints):
        """Build JO and bind-world records without changing Maya time."""

        import maya.api.OpenMaya as om

        cmds = self.maya_adapter._cmds
        canonical = {}
        pending = list(joints)
        visited = set()
        while pending:
            item = pending.pop()
            paths = cmds.ls(item, long=True) or []
            if len(paths) != 1:
                continue
            joint = str(paths[0])
            if joint in visited:
                continue
            visited.add(joint)
            if not cmds.attributeQuery("mmd_bone_index", node=joint, exists=True):
                continue
            index = int(cmds.getAttr(f"{joint}.mmd_bone_index"))
            previous = canonical.get(index)
            if previous is not None and previous != joint:
                raise RuntimeError(f"MMD mirror bone index is ambiguous: {index}")
            canonical[index] = joint
            parents = cmds.listRelatives(joint, parent=True, fullPath=True) or []
            if len(parents) == 1:
                parent = str(parents[0])
                if str(cmds.nodeType(parent) or "") == "joint":
                    pending.append(parent)
        index_by_joint = {joint: index for index, joint in canonical.items()}
        parent_by_index = {}
        bind_space_by_index = {}
        local_records = {}
        for index, joint in canonical.items():
            parents = cmds.listRelatives(joint, parent=True, fullPath=True) or []
            parent = str(parents[0]) if parents else None
            parent_index = index_by_joint.get(parent)
            if parent and str(cmds.nodeType(parent) or "") == "joint" and parent_index is None:
                raise RuntimeError(f"MMD mirror parent context is unavailable: {joint}")
            parent_by_index[index] = parent_index
            bind_space_by_index[index] = parent if parent_index is None else None
            try:
                orient_values = tuple(
                    float(value) for value in cmds.getAttr(f"{joint}.jointOrient")[0]
                )
            except Exception as exc:
                raise RuntimeError(f"MMD mirror jointOrient is unavailable: {joint}") from exc
            if len(orient_values) != 3 or not all(
                math.isfinite(value) for value in orient_values
            ):
                raise RuntimeError(f"MMD mirror jointOrient is invalid: {joint}")
            orient = om.MEulerRotation(
                *(math.radians(value) for value in orient_values)
            ).asQuaternion()
            translation = self._mirror_bind_translation(joint)
            transform = om.MTransformationMatrix()
            transform.setTranslation(om.MVector(*translation), om.MSpace.kTransform)
            transform.setRotation(orient)
            local_records[index] = (transform.asMatrix(), orient, translation)

        bind_worlds = {}
        resolved_spaces = {}

        def resolve(index):
            if index in bind_worlds:
                return bind_worlds[index]
            local, _orient, _translation = local_records[index]
            parent_index = parent_by_index[index]
            world = local * resolve(parent_index) if parent_index is not None else local
            bind_worlds[index] = world
            return world

        def resolve_space(index):
            if index in resolved_spaces:
                return resolved_spaces[index]
            parent_index = parent_by_index[index]
            space = (
                bind_space_by_index[index]
                if parent_index is None
                else resolve_space(parent_index)
            )
            resolved_spaces[index] = space
            return space

        result = {}
        for index, joint in canonical.items():
            world = resolve(index)
            _local, orient, _translation = local_records[index]
            result[joint] = {
                "joint_orient": tuple(
                    float(getattr(orient, component)) for component in ("x", "y", "z", "w")
                ),
                "bind_world_matrix": tuple(float(value) for value in world),
                "bind_space_node": resolve_space(index),
            }
        return result

    def _mirror_entries(self):
        """Build UUID-authoritative joint/control entries for this model."""

        root = self.app_state.current_model_root
        cmds = getattr(self.maya_adapter, "_cmds", None)
        if not root:
            raise RuntimeError("No MMD model selected")
        owner = "MMD_OWNED"
        model_uuid = None
        metadata = {}
        if cmds is not None:
            from ...core.mmd_control_rig_builder import read_mmd_control_rig_metadata

            metadata = read_mmd_control_rig_metadata(root, cmds_module=cmds) or {}
            owner = str(metadata.get("owner") or "MMD_OWNED")
            if owner not in {"MMD_OWNED", "CONTROL_OWNED"}:
                raise RuntimeError(f"unsupported MMD Control Rig owner: {owner}")
            root_uuids = cmds.ls(root, uuid=True) or []
            if len(root_uuids) != 1:
                raise RuntimeError("MMD model UUID is unavailable")
            model_uuid = str(root_uuids[0])

        candidates = list(dict.fromkeys((*self._all_model_joints, *self._bone_name_to_joint.values())))
        joint_contexts = {}
        if owner == "MMD_OWNED" and cmds is not None:
            joint_contexts = self._mirror_joint_contexts(candidates)

        control_bases = {}
        if owner == "CONTROL_OWNED" and cmds is not None:
            from ...core.mmd_control_rig_basis import validate_basis_record

            basis_records = metadata.get("authoringBases") or {}
            for role, control_uuid in (metadata.get("controls") or {}).items():
                control_paths = cmds.ls(control_uuid, long=True) or []
                record = basis_records.get(role)
                if len(control_paths) != 1 or record is None:
                    raise RuntimeError(f"Control Rig mirror basis is unavailable: {role}")
                path = str(control_paths[0])
                basis = validate_basis_record(record).quaternion
                previous = control_bases.get(path)
                if previous is not None and previous != basis:
                    raise RuntimeError(f"ambiguous Control Rig mirror basis: {path}")
                control_bases[path] = basis

        entries = []
        seen = set()
        seen_nodes = {}
        for candidate in candidates:
            if not candidate:
                continue
            node = str(candidate)
            if cmds is not None:
                paths = cmds.ls(node, long=True) or []
                uuids = cmds.ls(node, uuid=True) or []
                if len(paths) != 1 or len(uuids) != 1:
                    continue
                joint = str(paths[0])
                identity = str(uuids[0])
                node = joint
            else:
                joint = node
                identity = node
            if identity in seen:
                # The same UUID can appear once through the indexed map and
                # once through the name map; it is still one authoritative
                # node, not an ambiguous pair.
                continue
            names = []
            for attr in (ATTR_MMD_BONE_NAME, ATTR_MMD_BONE_NAME_EN):
                try:
                    value = self.maya_adapter.get_attr(f"{candidate}.{attr}")
                except Exception:
                    value = None
                if value:
                    names.append(str(value))
            if not names:
                for bone_name, mapped in self._bone_name_to_joint.items():
                    if mapped == candidate:
                        names.append(str(bone_name))
            if not names:
                continue
            joint_orient = (0.0, 0.0, 0.0, 1.0)
            bind_world_matrix = None
            bind_space_node = None
            if owner == "MMD_OWNED" and cmds is not None:
                joint_context = joint_contexts.get(joint)
                if joint_context is None:
                    raise RuntimeError(f"MMD mirror joint context is unavailable: {joint}")
                joint_orient = joint_context["joint_orient"]
                bind_world_matrix = joint_context["bind_world_matrix"]
                bind_space_node = joint_context["bind_space_node"]
            if owner == "CONTROL_OWNED":
                node = self._preferred_rig_control(joint)
                if not node:
                    continue
                if cmds is not None:
                    control_paths = cmds.ls(node, long=True) or []
                    if len(control_paths) != 1:
                        continue
                    node = str(control_paths[0])
                authoring_basis = control_bases.get(node)
                if authoring_basis is None:
                    raise RuntimeError(f"Control Rig mirror basis is unavailable: {node}")
            else:
                authoring_basis = (0.0, 0.0, 0.0, 1.0)
            previous_identity = seen_nodes.get(node)
            if previous_identity is not None and previous_identity != identity:
                raise RuntimeError("ambiguous Control Rig mirror node ownership")
            entries.append(
                {
                    "identity": identity,
                    "node": node,
                    "joint": joint,
                    "names": tuple(dict.fromkeys(names)),
                    "authoring_basis": authoring_basis,
                    "joint_orient": joint_orient,
                    "bind_world_matrix": bind_world_matrix,
                    "bind_space_node": bind_space_node,
                }
            )
            seen.add(identity)
            seen_nodes[node] = identity
        from ..mirror_actions import MirrorEntry

        return [MirrorEntry(**entry) for entry in entries], owner, model_uuid

    def _mirror_mappings_for_selection(self):
        """Resolve selected nodes to paired UUID-owned entries."""

        from ..mirror_actions import MirrorMapping, build_mirror_pairs

        entries, owner, model_uuid = self._mirror_entries()
        selected = self.maya_adapter.ls(selection=True) or []
        if not selected:
            raise RuntimeError("No joints selected")
        paths = []
        for node in selected:
            path = self._resolve_selection_path(node) if getattr(self.maya_adapter, "_cmds", None) else str(node)
            if path:
                paths.append(path)
        entry_by_node = {entry.node: entry for entry in entries}
        pair_by_identity = build_mirror_pairs(entries)
        mappings = []
        for node in paths:
            entry = entry_by_node.get(node)
            if entry is None:
                raise RuntimeError(f"selection is not a validated MMD node: {node}")
            target = pair_by_identity.get(entry.identity)
            if target is None:
                raise RuntimeError(f"no unique mirror pair for {node}")
            mappings.append(MirrorMapping(entry, target))
        if owner == "CONTROL_OWNED" and getattr(self.maya_adapter, "_cmds", None):
            identity_basis = (0.0, 0.0, 0.0, 1.0)
            oriented = [
                mapping
                for mapping in mappings
                if mapping.source.authoring_basis != identity_basis
                and mapping.target.authoring_basis != identity_basis
            ]
            if oriented:
                contexts = self._mirror_joint_contexts(
                    [entry.joint for mapping in oriented for entry in (mapping.source, mapping.target)]
                )

                def with_context(entry):
                    context = contexts.get(entry.joint)
                    if context is None:
                        raise RuntimeError(
                            f"MMD mirror joint context is unavailable: {entry.joint}"
                        )
                    return replace(
                        entry,
                        joint_orient=context["joint_orient"],
                        bind_world_matrix=context["bind_world_matrix"],
                        bind_space_node=context["bind_space_node"],
                    )

                mappings = [
                    MirrorMapping(with_context(mapping.source), with_context(mapping.target))
                    if mapping in oriented
                    else mapping
                    for mapping in mappings
                ]
        return mappings, owner, model_uuid

    # -- Visibility -------------------------------------------------------

    def _on_visibility_changed(self, category: str, visible: bool):
        """Compatibility entry point for legacy bool visibility widgets."""

        model_root = self.app_state.current_model_root
        if not model_root:
            return
        if category == "control_rig":
            group = self._control_rig_group(model_root)
            if group:
                try:
                    self.maya_adapter.set_attr(f"{group}.visibility", visible)
                except Exception as exc:
                    logger.debug("Control Rig visibility toggle failed: %s", exc)
            return
        if category == "morphs":
            return
        try:
            set_visibility_category(self.maya_adapter, model_root, category, visible)
            sync_visibility_connections(self.maya_adapter, model_root, category)
        except Exception as exc:
            logger.debug("Visibility toggle failed for %s: %s", category, exc)
        self._sync_visibility_controls(model_root)

    def _on_visibility_state_changed(self, category: str, state: str) -> None:
        """Apply a tri-state transition and immediately read the scene back."""

        model_root = self.app_state.current_model_root
        if not model_root:
            self._sync_visibility_controls(None)
            return
        if category == "morphs":
            return
        normalized = self._coerce_visibility_state(state)
        if normalized is None:
            self._sync_visibility_controls(model_root)
            return

        success = False
        if category == "control_rig":
            group = self._control_rig_group(model_root)
            if group:
                success = set_visibility_group_state(
                    self.maya_adapter,
                    group,
                    normalized,
                    label="Set MMD Control Rig Visibility",
                )
        else:
            try:
                success = set_visibility_state(
                    self.maya_adapter, model_root, category, normalized
                )
            except Exception as exc:
                logger.debug("Visibility state transition failed for %s: %s", category, exc)
        # Correct optimistic UI state from actual scene plugs after both
        # successful and rejected writes.
        self._sync_visibility_controls(model_root, ensure_connections=False)
        if not success:
            logger.debug("Visibility state transition was rejected for %s", category)

    # -- Internal ------------------------------------------------------

    def _reload_for_model(self, model_root: str):
        previous_owner = self._ik_authority_owner
        previous_model_root = self._ik_authority_model_root
        previous_control_ik = self._control_ik_nodes_by_side
        previous_control_toe_ik = self._control_toe_ik_nodes_by_side
        bone_map = self._build_bone_index_map(model_root)
        self._all_model_joints = [bone_map[index] for index in sorted(bone_map)]
        self._bone_name_to_joint = self._build_bone_name_map(model_root)
        self._ik_nodes_by_side = self._collect_leg_ik_nodes(model_root)
        self._toe_ik_nodes_by_side = self._collect_toe_ik_nodes(model_root)
        (
            control_ik_nodes,
            control_toe_ik_nodes,
            authority_owner,
        ) = self._collect_control_rig_ik_nodes(model_root)
        # If a known Control-owned model temporarily loses readable metadata,
        # retain that authority and its UUID-resolved controls for this same
        # model. Never carry those nodes across a model-root replacement.
        if (
            authority_owner == _IK_AUTHORITY_UNKNOWN
            and previous_owner == "CONTROL_OWNED"
            and previous_model_root == model_root
        ):
            control_ik_nodes = previous_control_ik
            control_toe_ik_nodes = previous_control_toe_ik
            authority_owner = previous_owner
        self._control_ik_nodes_by_side = control_ik_nodes
        self._control_toe_ik_nodes_by_side = control_toe_ik_nodes
        self._ik_authority_owner = authority_owner
        self._ik_authority_model_root = model_root
        self._last_ik_states = {}
        self._sync_picker_regions()
        self._sync_ik_picker_state(force=True)
        self._build_picker_english_tooltips()
        self._retranslate_picker_bone_tooltips()
        bone_display_names = self._build_bone_display_name_map(bone_map)
        morph_metadata = self._read_morph_metadata(model_root)
        self._morph_controller = self._find_morph_controller(model_root)
        morph_display_names = {
            index: info.name for index, info in morph_metadata.items()
        }
        display_json = self._read_display_frames_json(model_root)
        self._picker_groups = resolve_display_frames(
            display_json,
            bone_map,
            bone_display_name_map=bone_display_names,
            morph_display_name_map=morph_display_names,
        )
        self._populate_display_frame_tree(self._picker_groups)
        self._reload_morph_tab(model_root, morph_metadata)
        self._sync_visibility_controls(model_root)
        self.view.status_label.setText("")
        self._sync_common_action_state()
        self._sync_picker_to_actual_selection()

    def _sync_picker_regions(self):
        """Keep missing bones non-interactive while navigation stays available."""

        from ..widgets.body_picker_widget import _BODY_REGIONS
        from ..widgets.finger_picker_widget import _FINGER_REGIONS

        available_names = set(self._bone_name_to_joint)
        body_ids = {
            region["id"]
            for region in _BODY_REGIONS
            if (normalize_mmd_bone_name(region["bone_name"]) or region["bone_name"])
            in available_names
        }
        body_ids.update(
            {
                "select_all",
                "clear_selection",
                "reset_pose",
                "fingers_left",
                "fingers_right",
            }
        )
        for side in _IK_BONE_NAMES:
            if (
                side in self._ik_nodes_by_side
                or side in self._toe_ik_nodes_by_side
                or side in self._control_ik_nodes_by_side
                or side in self._control_toe_ik_nodes_by_side
            ):
                body_ids.add(f"ik_enable_{side}")
        if hasattr(self.view.body_picker, "set_enabled_regions"):
            self.view.body_picker.set_enabled_regions(body_ids)

        finger_ids = {
            region["id"]
            for region in _FINGER_REGIONS
            if (normalize_mmd_bone_name(region["bone_name"]) or region["bone_name"])
            in available_names
        }
        finger_ids.add("back_to_body")
        if hasattr(self.view.finger_picker, "set_enabled_regions"):
            self.view.finger_picker.set_enabled_regions(finger_ids)

    def _collect_leg_ik_nodes(self, model_root: str) -> dict[str, str]:
        """Resolve the current model's owned left/right foot IK solvers."""

        try:
            from ...converters.vmd_ik_enabled_animation import collect_ik_nodes_by_bone_name

            nodes = collect_ik_nodes_by_bone_name(target_model=model_root)
        except Exception as exc:
            logger.debug("Failed to collect IK nodes for %s: %s", model_root, exc)
            return {}
        normalized = {
            normalize_mmd_bone_name(name) or name: node for name, node in nodes.items()
        }
        return {
            side: normalized[name]
            for side, raw_name in _IK_BONE_NAMES.items()
            if (name := normalize_mmd_bone_name(raw_name) or raw_name) in normalized
        }

    def _collect_toe_ik_nodes(self, model_root: str) -> dict[str, str]:
        """Resolve the current model's owned left/right toe IK solvers."""

        try:
            from ...converters.vmd_ik_enabled_animation import collect_ik_nodes_by_bone_name

            nodes = collect_ik_nodes_by_bone_name(target_model=model_root)
        except Exception as exc:
            logger.debug("Failed to collect toe IK nodes for %s: %s", model_root, exc)
            return {}
        normalized = {
            normalize_mmd_bone_name(name) or name: node for name, node in nodes.items()
        }
        return {
            side: normalized[name]
            for side, raw_name in _TOE_IK_BONE_NAMES.items()
            if (name := normalize_mmd_bone_name(raw_name) or raw_name) in normalized
        }

    def _collect_control_rig_ik_nodes(
        self, model_root: str
    ) -> tuple[dict[str, str], dict[str, str], str]:
        """Resolve owned Control Rig IK controls without changing the scene.

        Legacy ``mmdCcdIk.enabled`` remains authoritative for MMD-owned
        scenes.  Once the metadata owner is ``CONTROL_OWNED``, the control's
        ``ikEnabled`` plug is the only accepted source; falling back to a
        legacy solver in that state would violate the single-writer contract.
        """

        # A model without Control Rig metadata is the normal MMD-owned route.
        # Once metadata has been read successfully, a known CONTROL_OWNED
        # value is retained even if a later topology inspection fails.
        owner = _IK_AUTHORITY_UNKNOWN
        try:
            from ...core.mmd_control_rig_builder import (
                CONTROL_RIG_CONTROL_OWNED,
                inspect_mmd_control_rig,
                read_mmd_control_rig_metadata,
            )

            metadata = read_mmd_control_rig_metadata(model_root)
            # ``None`` is a successful read for a model without a Control
            # Rig; only an exception means that ownership is unknown.
            owner = str((metadata or {}).get("owner") or "MMD_OWNED")
            if owner not in {"MMD_OWNED", CONTROL_RIG_CONTROL_OWNED}:
                return {}, {}, _IK_AUTHORITY_UNKNOWN
            if owner != CONTROL_RIG_CONTROL_OWNED:
                return {}, {}, owner
            rig = inspect_mmd_control_rig(model_root)
            bindings = (metadata or {}).get("bindings") or {}
            if rig is None or not isinstance(bindings, dict):
                return {}, {}, owner

            from ...core.mmd_control_rig_analyzer import INPUT_IK_CONTROLLER

            def resolve_roles(role_map):
                resolved = {}
                for side, role in role_map.items():
                    binding = bindings.get(role)
                    control = getattr(rig, "controls", {}).get(role)
                    if (
                        isinstance(binding, dict)
                        and binding.get("inputKind") == INPUT_IK_CONTROLLER
                        and control
                    ):
                        try:
                            if self.maya_adapter.attribute_exists("ikEnabled", control):
                                resolved[side] = str(control)
                        except Exception:
                            continue
                return resolved

            return (
                resolve_roles(_CONTROL_IK_ROLES),
                resolve_roles(_CONTROL_TOE_IK_ROLES),
                owner,
            )
        except Exception as exc:
            logger.debug(
                "Failed to collect Control Rig IK controls for %s: %s",
                model_root,
                exc,
            )
            # Preserve a known CONTROL_OWNED authority on transient
            # inspection failures; using a legacy solver here would violate
            # the single-writer contract.  The next refresh retries.
            return {}, {}, (
                owner
                if owner == CONTROL_RIG_CONTROL_OWNED
                else _IK_AUTHORITY_UNKNOWN
            )

    def _active_ik_nodes_by_side(self) -> dict[str, str]:
        """Return the IK source selected by the persisted motion owner."""

        if self._ik_authority_owner == "CONTROL_OWNED":
            return self._control_ik_nodes_by_side
        if self._ik_authority_owner == "MMD_OWNED":
            return self._ik_nodes_by_side
        return {}

    def _active_toe_ik_nodes_by_side(self) -> dict[str, str]:
        """Return the toe IK source selected by the persisted motion owner."""

        if self._ik_authority_owner == "CONTROL_OWNED":
            return self._control_toe_ik_nodes_by_side
        if self._ik_authority_owner == "MMD_OWNED":
            return self._toe_ik_nodes_by_side
        return {}

    def _active_ik_attribute(self) -> str | None:
        """Return the read/write plug attribute for the active owner."""

        if self._ik_authority_owner == "CONTROL_OWNED":
            return "ikEnabled"
        if self._ik_authority_owner == "MMD_OWNED":
            return "enabled"
        return None

    def _refresh_ik_authority(self) -> None:
        """Re-read ownership metadata when a Control Rig transaction changes it."""

        model_root = self.app_state.current_model_root
        if not model_root:
            return
        try:
            from ...core.mmd_control_rig_builder import read_mmd_control_rig_metadata

            metadata = read_mmd_control_rig_metadata(model_root)
            owner = str((metadata or {}).get("owner") or "MMD_OWNED")
            if owner not in {"MMD_OWNED", "CONTROL_OWNED"}:
                owner = _IK_AUTHORITY_UNKNOWN
        except Exception:
            # A failed metadata read is not evidence that the scene is
            # MMD-owned. Keep a known Control-owned authority safe, but
            # disable legacy writes for every other prior state.
            if self._ik_authority_owner != "CONTROL_OWNED":
                self._ik_authority_owner = _IK_AUTHORITY_UNKNOWN
                self._control_ik_nodes_by_side = {}
                self._control_toe_ik_nodes_by_side = {}
                self._last_ik_states = {}
                self._sync_picker_regions()
            return
        if owner != self._ik_authority_owner or (
            owner == "CONTROL_OWNED"
            and not (
                self._control_ik_nodes_by_side or self._control_toe_ik_nodes_by_side
            )
        ):
            (
                self._control_ik_nodes_by_side,
                self._control_toe_ik_nodes_by_side,
                self._ik_authority_owner,
            ) = self._collect_control_rig_ik_nodes(model_root)
            self._ik_authority_model_root = model_root
            self._last_ik_states = {}
            self._sync_picker_regions()

    def _sync_ik_picker_state(self, *, force: bool = False) -> None:
        """Hide a side's FK controls while its evaluated leg IK is enabled."""

        self._refresh_ik_authority()
        active_foot_nodes = self._active_ik_nodes_by_side()
        active_toe_nodes = self._active_toe_ik_nodes_by_side()
        attribute = self._active_ik_attribute()
        states = {}
        for side, node in active_foot_nodes.items():
            try:
                states[side] = bool(self.maya_adapter.get_attr(f"{node}.{attribute}"))
            except Exception:
                states[side] = False
        toe_states = {}
        for side, node in active_toe_nodes.items():
            try:
                toe_states[side] = bool(self.maya_adapter.get_attr(f"{node}.{attribute}"))
            except Exception:
                toe_states[side] = False
        combined_states = {
            **{f"foot:{side}": enabled for side, enabled in states.items()},
            **{f"toe:{side}": enabled for side, enabled in toe_states.items()},
        }
        if not force and combined_states == self._last_ik_states:
            return
        self._last_ik_states = combined_states
        hidden = set()
        for side in _IK_BONE_NAMES:
            foot_enabled = states.get(side, False)
            toe_enabled = toe_states.get(side, False)
            if foot_enabled:
                hidden.update(_LEG_FK_REGIONS[side])
            else:
                hidden.add(f"{side}_ik")
            if toe_enabled:
                hidden.update(_TOE_FK_REGIONS[side])
            else:
                hidden.add(f"{side}_toe_ik")
        # The common action bar owns Reset Pose. Hide the legacy Body SVG hit
        # region in the real widget so the same action is not presented twice.
        if hasattr(self.view.body_picker, "_region_paths"):
            hidden.add("reset_pose")
        if hasattr(self.view.body_picker, "set_hidden_regions"):
            self.view.body_picker.set_hidden_regions(hidden)
        if hasattr(self.view.body_picker, "set_region_dim_levels"):
            dim_levels = {}
            for side in _IK_BONE_NAMES:
                values = [
                    enabled
                    for key, enabled in (
                        (f"foot:{side}", states.get(side)),
                        (f"toe:{side}", toe_states.get(side)),
                    )
                    if key in combined_states
                ]
                if not values or not any(values):
                    dim_levels[f"ik_enable_{side}"] = 0.65
                elif not all(values):
                    dim_levels[f"ik_enable_{side}"] = 0.3
            self.view.body_picker.set_region_dim_levels(dim_levels)

    def _build_picker_english_tooltips(self) -> None:
        """Cache PMX English bone names for locale-aware picker tooltips."""

        from ..widgets.body_picker_widget import _BODY_REGIONS
        from ..widgets.finger_picker_widget import _FINGER_REGIONS

        for picker, regions in (("body", _BODY_REGIONS), ("finger", _FINGER_REGIONS)):
            english = {}
            for region in regions:
                normalized = normalize_mmd_bone_name(region["bone_name"]) or region["bone_name"]
                joint = self._bone_name_to_joint.get(normalized)
                if not joint:
                    continue
                try:
                    if self.maya_adapter.attribute_exists(ATTR_MMD_BONE_NAME_EN, joint):
                        name = self.maya_adapter.get_attr(f"{joint}.{ATTR_MMD_BONE_NAME_EN}")
                        if name:
                            english[region["id"]] = str(name)
                except Exception:
                    continue
            self._picker_english_tooltips[picker] = english

    def _retranslate_picker_bone_tooltips(self) -> None:
        """Use PMX English names outside Japanese, falling back to PMX Japanese."""

        from ..widgets.body_picker_widget import _BODY_REGIONS
        from ..widgets.finger_picker_widget import _FINGER_REGIONS

        language = self.view.current_language()
        for picker, widget, regions in (
            ("body", self.view.body_picker, _BODY_REGIONS),
            ("finger", self.view.finger_picker, _FINGER_REGIONS),
        ):
            english = self._picker_english_tooltips[picker]
            tooltips = {}
            for region in regions:
                region_id = region["id"]
                if language == "ja":
                    tooltips[region_id] = region["bone_name"]
                    continue
                translated = (
                    self.view.tr(region_id, "animation_picker")
                    if picker == "body"
                    else region_id
                )
                tooltips[region_id] = (
                    translated
                    if translated != region_id
                    else english.get(region_id, region["bone_name"])
                )
            widget.update_region_texts(tooltips=tooltips)

    def _clear_all(self):
        self._picker_groups = []
        self._all_model_joints = []
        self._ik_nodes_by_side = {}
        self._toe_ik_nodes_by_side = {}
        self._control_ik_nodes_by_side = {}
        self._control_toe_ik_nodes_by_side = {}
        self._ik_authority_owner = _IK_AUTHORITY_UNKNOWN
        self._ik_authority_model_root = None
        self._last_ik_states = {}
        self._bone_name_to_joint.clear()
        self._sync_picker_regions()
        if hasattr(self.view.body_picker, "set_hidden_regions"):
            hidden = {"reset_pose"} if hasattr(
                self.view.body_picker, "_region_paths"
            ) else set()
            self.view.body_picker.set_hidden_regions(hidden)
        if hasattr(self.view.body_picker, "set_region_dim_levels"):
            self.view.body_picker.set_region_dim_levels({})
        self.view.display_frame_tree.clear()
        self._clear_morph_tab()
        self._sync_visibility_controls(None)
        self.view.status_label.setText("")
        self._sync_common_action_state()
        self._set_picker_selection_from_nodes([])

    def _sync_visibility_controls(
        self, model_root: str | None, *, ensure_connections: bool = True
    ):
        try:
            if model_root and ensure_connections:
                sync_visibility_connections(self.maya_adapter, model_root)
            for key, cb in self.view.vis_checkboxes.items():
                if key == "control_rig":
                    group = self._control_rig_group(model_root) if model_root else None
                    cb._control_rig_available = bool(group)
                    self._set_visibility_button_available(cb, bool(group))
                    self._set_visibility_button_state(
                        cb, get_visibility_group_state(self.maya_adapter, group)
                    )
                    continue
                if key == "morphs":
                    continue
                group = resolve_visibility_group(self.maya_adapter, model_root, key)
                self._set_visibility_button_available(cb, bool(group))
                state = (
                    get_visibility_state(self.maya_adapter, model_root, key)
                    if model_root
                    else VisibilityState.VISIBLE
                )
                self._set_visibility_button_state(cb, state)
            if hasattr(self.view, "refresh_development_mode_visibility"):
                self.view.refresh_development_mode_visibility()
        except Exception as exc:
            logger.debug("Visibility control sync failed: %s", exc)

    @staticmethod
    def _coerce_visibility_state(state: str | VisibilityState) -> VisibilityState | None:
        if isinstance(state, VisibilityState):
            return state
        try:
            return VisibilityState(str(state).strip().lower())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _set_visibility_button_state(button, state: VisibilityState) -> None:
        setter = getattr(button, "setVisibilityState", None)
        if callable(setter):
            setter(state.value)
            return
        setter = getattr(button, "set_visibility_state", None)
        if callable(setter):
            setter(state.value)
            return
        if hasattr(button, "setChecked"):
            # Legacy bool widgets cannot expose Reference independently.
            button.setChecked(state is not VisibilityState.HIDDEN)

    @staticmethod
    def _set_visibility_button_available(button, available: bool) -> None:
        setter = getattr(button, "setVisibilityAvailable", None)
        if callable(setter):
            setter(available)
            return
        setter = getattr(button, "set_visibility_available", None)
        if callable(setter):
            setter(available)
            return
        if hasattr(button, "setEnabled"):
            button.setEnabled(bool(available))
        button._visibility_available = bool(available)

    def _control_rig_group(self, model_root: str) -> str | None:
        """Resolve the UUID-owned Control Rig display group, if it is valid."""

        cmds_module = getattr(self.maya_adapter, "_cmds", None)
        if not model_root or cmds_module is None:
            return None
        try:
            inspected = inspect_mmd_control_rig(
                model_root, cmds_module=cmds_module
            )
            group = getattr(inspected, "control_group", None)
            return str(group) if isinstance(group, str) and group else None
        except Exception:
            logger.debug("Control Rig visibility group lookup failed", exc_info=True)
            return None

    def _update_model_combo(self, models: list):
        combo = self.view.model_combo
        combo.blockSignals(True)
        combo.clear()
        for model in models:
            add_combo_item_with_tooltip(combo, model)
        current = self.app_state.current_model_root
        if current:
            idx = combo.findText(current)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _build_bone_index_map(self, model_root: str) -> dict[int, str]:
        try:
            joints = self.maya_adapter.ls(
                self.maya_adapter.list_relatives(model_root, allDescendents=True, type="joint") or [],
                type="joint",
            ) or []
        except Exception:
            return {}

        bone_map: dict[int, str] = {}
        for joint in joints:
            try:
                if not self.maya_adapter.attribute_exists(ATTR_MMD_BONE_INDEX, joint):
                    continue
                idx = int(self.maya_adapter.get_attr(f"{joint}.{ATTR_MMD_BONE_INDEX}"))
                bone_map[idx] = joint
            except Exception:
                continue
        return bone_map

    def _build_bone_name_map(self, model_root: str) -> dict[str, str]:
        try:
            joints = self.maya_adapter.ls(
                self.maya_adapter.list_relatives(model_root, allDescendents=True, type="joint") or [],
                type="joint",
            ) or []
        except Exception:
            return {}

        name_map: dict[str, str] = {}
        for joint in joints:
            try:
                if not self.maya_adapter.attribute_exists(ATTR_MMD_BONE_NAME, joint):
                    continue
                bone_name = self.maya_adapter.get_attr(f"{joint}.{ATTR_MMD_BONE_NAME}")
                if bone_name:
                    normalized = normalize_mmd_bone_name(bone_name) or bone_name
                    name_map[normalized] = joint
            except Exception:
                continue
        return name_map

    def _build_bone_display_name_map(self, bone_map: dict[int, str]) -> dict[int, str]:
        names = {}
        for index, joint in bone_map.items():
            try:
                if self.maya_adapter.attribute_exists(ATTR_MMD_BONE_NAME, joint):
                    name = self.maya_adapter.get_attr(f"{joint}.{ATTR_MMD_BONE_NAME}")
                    if name:
                        names[index] = str(name)
            except Exception:
                continue
        return names

    def _read_morph_metadata(self, model_root: str) -> dict[int, MorphInfo]:
        """Read authoritative PMX morph names/panels keyed by global index."""

        try:
            if not self.maya_adapter.attribute_exists(ATTR_MMD_MORPH_DATA, model_root):
                return {}
            raw = self.maya_adapter.get_attr(f"{model_root}.{ATTR_MMD_MORPH_DATA}")
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError, OSError):
            return {}

        entries = []
        if isinstance(parsed, list):
            entries = [(str(position), entry, True) for position, entry in enumerate(parsed)]
        elif isinstance(parsed, dict):
            entries = [(str(key), entry, False) for key, entry in parsed.items()]

        result = {}
        for fallback_key, raw_entry, is_raw_pmx in entries:
            if not isinstance(raw_entry, dict):
                continue
            entry = dict(raw_entry)
            if is_raw_pmx:
                entry["_pmx_type_raw"] = True
            name = str(entry.get("name_jp") or entry.get("name") or fallback_key)
            info = morph_info_from_presenter_entry(name, entry)
            index = info.index
            if index < 0:
                try:
                    index = int(fallback_key)
                except (TypeError, ValueError):
                    continue
                info = MorphInfo(
                    name=info.name,
                    name_english=info.name_english,
                    panel=info.panel,
                    morph_type=info.morph_type,
                    index=index,
                )
            result[index] = info
        return result

    def _read_display_frames_json(self, model_root: str) -> str | None:
        try:
            if not self.maya_adapter.attribute_exists(ATTR_MMD_DISPLAY_FRAMES_JSON, model_root):
                return {}
            return self.maya_adapter.get_attr(f"{model_root}.{ATTR_MMD_DISPLAY_FRAMES_JSON}")
        except Exception:
            return None

    def _populate_display_frame_tree(self, groups: list[PickerGroup]):
        from ..qt_compat import QTreeWidgetItem

        tree = self.view.display_frame_tree
        tree.clear()

        for group in groups:
            label = group.name or group.name_english
            group_item = QTreeWidgetItem([label])

            for picker_item in group.items:
                display = self._item_display_text(picker_item)
                child = QTreeWidgetItem([display])
                child.setData(0, _USER_ROLE, picker_item.resolved_name or None)
                group_item.addChild(child)

            tree.addTopLevelItem(group_item)
            group_item.setExpanded(False)

    @staticmethod
    def _item_display_text(picker_item) -> str:
        if picker_item.display_name:
            return picker_item.display_name
        name = picker_item.resolved_name
        if not name:
            kind = "bone" if picker_item.element_type == 0 else "morph"
            return f"[{kind} #{picker_item.index}]"
        short = name.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
        return short

    # -- Morph tab ---------------------------------------------------------

    def _reload_morph_tab(
        self,
        model_root: str,
        morph_metadata: dict[int, MorphInfo] | None = None,
    ):
        self._clear_morph_tab()
        morph_infos = self._collect_morph_infos(model_root, morph_metadata or {})
        categorized = categorize_morphs(morph_infos)
        self._populate_morph_groups(categorized)

    def _clear_morph_tab(self):
        self._end_morph_edit()
        self._last_morph_refresh_time = None
        for row in tuple(self._morph_rows.values()):
            set_selected = getattr(row, "set_selected", None)
            if callable(set_selected):
                set_selected(False)
        self._morph_sliders.clear()
        self._morph_rows.clear()
        self._morph_group_headers.clear()
        self._morph_targets.clear()
        self._network_morph_targets.clear()
        self._morph_indices.clear()
        layout = self.view.morph_groups_layout
        while layout.count() > 1:
            child = layout.takeAt(0)
            widget = child.widget()
            if widget:
                widget.deleteLater()

    def _collect_morph_infos(
        self,
        model_root: str,
        morph_metadata: dict[int, MorphInfo] | None = None,
    ) -> list[MorphInfo]:
        metadata = morph_metadata or {}
        self._morph_indices.update({info.name: info.index for info in metadata.values()})
        metadata_by_name = {info.name: info for info in metadata.values()}
        blend_nodes = self._find_blend_shape_nodes(model_root)
        seen_names: set[str] = set()
        unique_morphs: list[MorphInfo] = []
        for bs_node in blend_nodes:
            entries = self._read_blend_morph_entries(bs_node)
            for weight_index, entry in entries.items():
                raw_name = str(entry.get("name", ""))
                global_index = entry.get("index")
                info = metadata.get(global_index) if isinstance(global_index, int) else None
                info = info or metadata_by_name.get(raw_name)
                if info is None:
                    resolved_index = global_index if isinstance(global_index, int) else weight_index
                    info = MorphInfo(raw_name, "", 4, "vertex", resolved_index)
                if isinstance(global_index, int):
                    self._morph_indices[info.name] = global_index
                self._morph_targets.setdefault(info.name, []).append((bs_node, weight_index))
                if info.name not in seen_names:
                    seen_names.add(info.name)
                    unique_morphs.append(info)

        self._collect_network_morph_targets(model_root, metadata, unique_morphs, seen_names)
        for info in sorted(metadata.values(), key=lambda item: item.index):
            if info.name not in seen_names:
                seen_names.add(info.name)
                unique_morphs.append(info)
        return unique_morphs

    def _find_morph_controller(self, model_root: str) -> str | None:
        try:
            if not self.maya_adapter.attribute_exists("mmd_morph_controller", model_root):
                return None
            controllers = self.maya_adapter.list_connections(
                f"{model_root}.mmd_morph_controller",
                source=True,
                destination=False,
            ) or []
            return controllers[0] if len(controllers) == 1 else None
        except Exception:
            return None

    def _collect_network_morph_targets(
        self,
        model_root: str,
        metadata: dict[int, MorphInfo],
        morphs: list[MorphInfo],
        seen_names: set[str],
    ) -> None:
        try:
            registry_members = list_model_registry_members_from_adapter(
                self.maya_adapter,
                model_root,
                REGISTRY_CATEGORY_MORPH,
            )
            network_nodes = (
                registry_members
                if registry_members is not None
                else self.maya_adapter.ls(type="network") or []
            )
        except Exception:
            return
        for node in network_nodes:
            try:
                if not self.maya_adapter.attribute_exists("mmd_morph_type", node):
                    continue
                if registry_members is None and self.maya_adapter.attribute_exists("mmd_model_root", node):
                    roots = self.maya_adapter.list_connections(f"{node}.mmd_model_root") or []
                    if roots and not any(
                        same_node_identity(self.maya_adapter, model_root, root)
                        for root in roots
                    ):
                        continue
                name = self.maya_adapter.get_attr(f"{node}.mmd_morph_name") or node
                index = -1
                if self.maya_adapter.attribute_exists("mmd_morph_index", node):
                    index = int(self.maya_adapter.get_attr(f"{node}.mmd_morph_index"))
                info = metadata.get(index) or MorphInfo(str(name), "", 4, "other", index)
                self._network_morph_targets.setdefault(info.name, []).append(f"{node}.weight")
                if info.name not in seen_names:
                    seen_names.add(info.name)
                    morphs.append(info)
            except Exception:
                continue

    def _find_blend_shape_nodes(self, model_root: str) -> list[str]:
        try:
            meshes = self.maya_adapter.list_relatives(
                model_root, allDescendents=True, type="mesh"
            ) or []
            bs_nodes = []
            for mesh in meshes:
                history = self.maya_adapter.list_history(mesh) or []
                for node in history:
                    try:
                        if self.maya_adapter.node_type(node) == "blendShape":
                            if node not in bs_nodes:
                                bs_nodes.append(node)
                    except Exception:
                        continue
            return bs_nodes
        except Exception:
            return []

    def _read_blend_morph_entries(self, bs_node: str) -> dict[int, dict[str, object]]:
        try:
            if not self.maya_adapter.attribute_exists(
                ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, bs_node
            ):
                return {}
            raw = self.maya_adapter.get_attr(
                f"{bs_node}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}"
            )
            if not raw:
                return {}
            return parse_blendshape_morph_entries(json.loads(raw))
        except Exception:
            return {}

    def _populate_morph_groups(self, categorized: CategorizedMorphs):
        from ..qt_compat import (
            QGridLayout,
            QPushButton,
            QSlider,
            QVBoxLayout,
            QWidget,
            Qt,
        )
        from ..widgets.morph_editor_widgets import (
            ElidedMorphLabel,
            MorphRowWidget,
            MorphWeightSpinBox,
            create_morph_type_icon,
        )

        layout = self.view.morph_groups_layout
        categories = [
            ("category_brow", categorized.eyebrow),
            ("category_eye", categorized.eye),
            ("category_mouth", categorized.mouth),
            ("category_other", categorized.other),
        ]

        for category_key, morphs in categories:
            if not morphs:
                continue
            cat_name = self.view.tr(category_key, "animation_toolset")
            group = QWidget()
            group.setObjectName("MorphPickerGroup")
            group.setStyleSheet(
                "QWidget#MorphPickerGroup { background: #383838; border: none; }"
            )
            group_layout = QVBoxLayout()
            group_layout.setContentsMargins(0, 0, 0, 0)
            group_layout.setSpacing(2)
            header = QPushButton(f"▾  {cat_name}    {len(morphs)}")
            header.setCheckable(True)
            header.setChecked(True)
            self._morph_group_headers.append((header, category_key, len(morphs)))
            header.setStyleSheet(
                "QPushButton { text-align: left; padding: 5px 7px; background: #454545; "
                "color: #dedede; border: none; font-weight: 600; } "
                "QPushButton:hover { background: #505050; }"
            )
            group_layout.addWidget(header)

            content = QWidget()
            content_layout = QGridLayout(content)
            content_layout.setContentsMargins(8, 4, 6, 5)
            content_layout.setHorizontalSpacing(7)
            content_layout.setVerticalSpacing(3)
            content_layout.setColumnMinimumWidth(0, 22)
            content_layout.setColumnMinimumWidth(1, 116)
            content_layout.setColumnMinimumWidth(3, 72)
            content_layout.setColumnStretch(2, 1)

            for row_index, morph in enumerate(morphs):
                tooltip_parts = [morph.name]
                if morph.name_english:
                    tooltip_parts.append(morph.name_english)
                tooltip_parts.append(f"Type: {morph.morph_type}")
                tooltip = "\n".join(tooltip_parts)
                icon = create_morph_type_icon(morph.morph_type)
                label = ElidedMorphLabel(morph.name, tooltip)

                slider = QSlider(Qt.Horizontal)
                slider.setRange(0, 100)
                slider.setStyleSheet(
                    "QSlider::groove:horizontal { height: 3px; background: #252525; } "
                    "QSlider::sub-page:horizontal { background: #5d8faa; } "
                    "QSlider::handle:horizontal { width: 10px; margin: -4px 0; "
                    "border-radius: 5px; background: #aeb4b8; } "
                    "QSlider::handle:horizontal:hover { background: #79cfff; }"
                )
                editor = MorphWeightSpinBox()
                plugs = self._morph_plugs(morph.name)
                enabled = bool(plugs)
                slider.setEnabled(enabled)
                editor.setEnabled(enabled)

                morph_name = morph.name
                slider.sliderPressed.connect(self._begin_morph_edit)
                slider.sliderReleased.connect(self._end_morph_edit)
                slider.valueChanged.connect(
                    lambda value, name=morph_name: self._on_morph_weight_changed(
                        name, value / 100.0
                    )
                )
                editor.edit_started.connect(self._begin_morph_edit)
                editor.edit_finished.connect(self._end_morph_edit)
                editor.valueChanged.connect(
                    lambda value, name=morph_name: self._on_morph_weight_changed(
                        name, value
                    )
                )
                row_widgets = MorphRowWidget(icon, label, slider, editor, plugs)
                row_widgets.set_value(self._morph_value(morph_name))
                row_widgets.set_animation_state(self._morph_animation_state(plugs))
                row_widgets.activated.connect(
                    lambda name=morph_name: self._on_morph_row_activated(name)
                )
                self._morph_sliders[morph_name] = slider
                self._morph_rows[morph_name] = row_widgets
                content_layout.addWidget(row_widgets, row_index, 0, 1, 4)

            group.setLayout(group_layout)
            group_layout.addWidget(content)

            def toggle_group(
                expanded,
                panel=content,
                button=header,
                title_key=category_key,
                count=len(morphs),
            ):
                panel.setVisible(expanded)
                title = self.view.tr(title_key, "animation_toolset")
                button.setText(f"{'▾' if expanded else '▸'}  {title}    {count}")

            header.toggled.connect(toggle_group)
            insert_pos = max(0, layout.count() - 1)
            layout.insertWidget(insert_pos, group)

    def _on_morph_row_activated(self, morph_name: str) -> None:
        """Select exactly one Morph's authoritative plugs for Maya keying."""

        plugs = self._morph_plugs(morph_name)
        if not plugs:
            self._sync_picker_to_actual_selection()
            return
        try:
            # Morph plugs are intentionally selected directly: unlike picker
            # nodes they are not subject to DAG visibility guards, and Maya's
            # standard Set Key command consumes this active plug selection.
            self._write_selection(plugs, replace=True)
        except Exception:
            logger.debug("Morph plug selection failed for %s", morph_name, exc_info=True)
        self._sync_picker_to_actual_selection()

    def _on_morph_slider_changed(self, morph_name: str, value: int, label):
        """Compatibility entry point for existing extensions and tests."""
        if label is not None:
            label.setText(str(value))
        self._on_morph_weight_changed(morph_name, value / 100.0)

    def _on_morph_weight_changed(self, morph_name: str, weight: float):
        implicit_chunk = not self._morph_edit_open
        if implicit_chunk:
            self._begin_morph_edit()
        self._set_morph_weight(morph_name, max(0.0, min(1.0, float(weight))))
        if implicit_chunk:
            self._end_morph_edit()

    def _set_morph_weight(self, morph_name: str, weight: float) -> None:
        morph_index = self._morph_indices.get(morph_name, -1)
        if self._morph_controller and morph_index >= 0:
            try:
                self.maya_adapter.set_attr(
                    f"{self._morph_controller}.inputWeight[{morph_index}]",
                    weight,
                )
            except Exception as exc:
                logger.debug("Morph controller weight set failed for %s: %s", morph_name, exc)
            return
        targets = self._morph_targets.get(morph_name, [])
        for bs_node, weight_idx in targets:
            try:
                self.maya_adapter.set_attr(f"{bs_node}.weight[{weight_idx}]", weight)
            except Exception as exc:
                logger.debug("Morph slider set failed for %s: %s", morph_name, exc)
        for plug in self._network_morph_targets.get(morph_name, []):
            try:
                self.maya_adapter.set_attr(plug, weight)
            except Exception as exc:
                logger.debug("Morph network weight set failed for %s: %s", morph_name, exc)

    def _morph_plugs(self, morph_name: str) -> tuple[str, ...]:
        """Resolve controller authority or every split legacy target."""
        morph_index = self._morph_indices.get(morph_name, -1)
        if self._morph_controller and morph_index >= 0:
            return (f"{self._morph_controller}.inputWeight[{morph_index}]",)
        targets = self._morph_targets.get(morph_name, [])
        plugs = [f"{node}.weight[{index}]" for node, index in targets]
        plugs.extend(self._network_morph_targets.get(morph_name, []))
        return tuple(plugs)

    def _morph_value(self, morph_name: str) -> float:
        morph_index = self._morph_indices.get(morph_name, -1)
        if self._morph_controller and morph_index >= 0:
            try:
                return float(
                    self.maya_adapter.get_attr(
                        f"{self._morph_controller}.inputWeight[{morph_index}]"
                    )
                )
            except Exception:
                pass
        targets = self._morph_targets.get(morph_name, [])
        plugs = [f"{node}.weight[{index}]" for node, index in targets]
        plugs.extend(self._network_morph_targets.get(morph_name, []))
        for plug in plugs:
            try:
                value = self.maya_adapter.get_attr(plug)
                if value is not None:
                    return float(value)
            except Exception:
                continue
        return 0.0

    def _start_morph_refresh_timer(self) -> None:
        """Poll evaluated values only for a visible real Qt view."""
        if not hasattr(self.view, "isVisible"):
            return
        from ..qt_compat import QTimer

        self._morph_refresh_timer = QTimer(self.view)
        self._morph_refresh_timer.setInterval(200)
        self._morph_refresh_timer.timeout.connect(self._refresh_dynamic_ui)
        self._morph_refresh_timer.start()

    def _refresh_dynamic_ui(self) -> None:
        """Refresh evaluated picker and morph state for the visible sub-tab."""

        if not self.view.isVisible():
            return
        if self.view.picker_tabs.currentIndex() == self.view.TAB_BODY:
            self._sync_ik_picker_state()
        self._refresh_morph_rows()

    def _refresh_morph_rows(self) -> None:
        if (
            not self._morph_rows
            or not self.view.isVisible()
            or self.view.picker_tabs.currentIndex() != self.view.TAB_MORPH
        ):
            return
        try:
            current_time = float(self.maya_adapter.current_time())
        except Exception:
            current_time = None
        refresh_animation = current_time != self._last_morph_refresh_time
        self._last_morph_refresh_time = current_time
        for morph_name, row in tuple(self._morph_rows.items()):
            if not row.editor.is_editing:
                row.set_value(self._morph_value(morph_name))
            if refresh_animation:
                row.set_animation_state(self._morph_animation_state(row.plugs))

    def _morph_animation_state(self, plugs) -> str:
        if isinstance(plugs, str):
            plugs = (plugs,)
        if not plugs or not hasattr(self.maya_adapter, "keyframe"):
            return "static"
        try:
            curves = []
            for plug in plugs:
                for curve in self.maya_adapter.keyframe(
                    plug, query=True, name=True
                ) or []:
                    if curve not in curves:
                        curves.append(curve)
            if not curves:
                return "static"
            time = self.maya_adapter.current_time()
            for curve in curves:
                count = self.maya_adapter.keyframe(
                    curve,
                    query=True,
                    time=(time, time),
                    keyframeCount=True,
                )
                if count:
                    return "key"
            return "animated"
        except Exception:
            return "static"

    def _begin_morph_edit(self) -> None:
        if self._morph_edit_open:
            return
        try:
            self.maya_adapter.undo_info(openChunk=True, chunkName="Edit MMD Morph")
        except Exception:
            return
        self._morph_edit_open = True

    def _end_morph_edit(self) -> None:
        if not self._morph_edit_open:
            return
        try:
            self.maya_adapter.undo_info(closeChunk=True)
        except Exception:
            logger.debug("Could not close morph edit undo chunk", exc_info=True)
        finally:
            self._morph_edit_open = False

    # -- Tools section ----------------------------------------------------

    _TOOL_HANDLERS = {
        "copy": "_on_copy_pose",
        "paste": "_on_paste_pose",
        "mirror": "_on_mirror_pose",
        "reset": "_on_reset_pose",
        "clean": "_on_clean_curves",
        "bake": "_on_bake_animation",
    }

    def _on_tool_clicked(self, tool_key: str):
        handler_name = self._TOOL_HANDLERS.get(tool_key)
        if handler_name:
            getattr(self, handler_name)()

    def _selected_joints(self) -> list[str]:
        try:
            return self.maya_adapter.ls(selection=True, type="joint") or []
        except Exception:
            return []

    def _on_copy_pose(self):
        from ...actions.pose_actions import CopyPoseAction, CopyPoseRequest

        joints = self._selected_joints()
        if not joints:
            self._set_status("no_joints_selected")
            return
        result = CopyPoseAction(self.maya_adapter).execute(
            CopyPoseRequest(joints=joints)
        )
        if result.succeeded:
            self._pose_clipboard = result.pose
            self._set_status("copied_pose", count=len(result.pose))
        else:
            self._set_status("copy_failed", error=result.error)

    def _on_paste_pose(self):
        from ...actions.pose_actions import PastePoseAction, PastePoseRequest

        if not self._pose_clipboard:
            self._set_status("no_pose_copied")
            return
        result = PastePoseAction(self.maya_adapter).execute(
            PastePoseRequest(pose=self._pose_clipboard)
        )
        if result.succeeded:
            self._set_status("pasted_pose", count=result.applied_count)
        else:
            self._set_status("paste_failed", error=result.error)

    def _on_reset_pose(self):
        """Apply a one-shot Reset Pose to the selection or whole model."""

        targets, bind_translations = self._rest_pose_targets()
        if not targets:
            self._set_status("no_joints_selected")
            return

        cmds = getattr(self.maya_adapter, "_cmds", None)
        if cmds is None:
            from ...actions.pose_actions import ResetPoseAction, ResetPoseRequest

            result = ResetPoseAction(self.maya_adapter).execute(
                ResetPoseRequest(
                    joints=targets,
                    bind_translations=bind_translations,
                )
            )
            if result.succeeded:
                self._set_status("reset_pose_applied", count=result.reset_count)
            else:
                self._set_status("reset_pose_failed", error=result.error)
            return

        try:
            from ..rest_pose_transaction import ResetPoseTransaction

            root = self.app_state.current_model_root
            roots = cmds.ls(root, uuid=True) if root else []
            if not root or len(roots) != 1:
                raise RuntimeError("MMD model UUID is unavailable")
            authored_plugs_by_target = self._rest_pose_authored_plugs(
                root,
                targets,
                cmds,
            )
            if authored_plugs_by_target is not None:
                routed_targets = []
                for target in targets:
                    paths = cmds.ls(target, long=True) or []
                    if len(paths) == 1 and str(paths[0]) in authored_plugs_by_target:
                        routed_targets.append(str(paths[0]))
                if not routed_targets:
                    raise RuntimeError("Reset Pose has no authored semantic targets")
                targets = routed_targets
            transaction = ResetPoseTransaction(
                self.maya_adapter,
                model_root=root,
                model_uuid=str(roots[0]),
                targets=targets,
                bind_translations=bind_translations,
                authored_plugs_by_target=authored_plugs_by_target,
                scope_roots=self._rest_pose_scope_roots(root, cmds),
            )
            count = transaction.apply()
            self._set_status("reset_pose_applied", count=count)
        except Exception as exc:
            self._set_status("reset_pose_failed", error=exc)

    def _rest_pose_targets(self) -> tuple[list[str], dict[str, tuple[float, float, float]]]:
        """Resolve selected or all joint/controllers in the current model UUID.

        A valid model-owned selection resets only that selection. With no
        selection the whole current model is reset. A non-model selection
        resolves to no targets so the action cannot unexpectedly reset all.
        """

        root = self.app_state.current_model_root
        cmds = getattr(self.maya_adapter, "_cmds", None)
        if not root or cmds is None:
            joints = self._selected_joints()
            if root and not joints:
                joints = list(self._all_model_joints)
            return joints, self._selected_bind_translations(joints)
        try:
            from ...core.mmd_control_rig_builder import read_mmd_control_rig_metadata

            metadata = read_mmd_control_rig_metadata(root, cmds_module=cmds) or {}
            owner = str(metadata.get("owner") or "MMD_OWNED")
            if owner not in {"MMD_OWNED", "CONTROL_OWNED"}:
                return [], {}
            if len(cmds.ls(root, long=True) or []) != 1:
                return [], {}
            joints = self._rest_pose_model_joints(root, cmds)
            if owner == "MMD_OWNED":
                targets = self._selected_or_all_reset_targets(joints, cmds)
                return targets, self._selected_bind_translations(targets)

            # In CONTROL_OWNED mode each UUID-backed controller is the only
            # accepted writer.  Binding metadata provides the complete model
            # set, including a master control intentionally outside the model
            # DAG; exact UUID resolution prevents cross-model fallthrough.
            from ...core.mmd_control_rig_builder import (
                resolve_mmd_control_rig_binding_joint,
            )

            targets = []
            for role, control_uuid in (metadata.get("controls") or {}).items():
                control_paths = cmds.ls(control_uuid, long=True) or []
                binding = (metadata.get("bindings") or {}).get(role)
                if len(control_paths) != 1 or not binding:
                    continue
                target = str(control_paths[0])
                bound = resolve_mmd_control_rig_binding_joint(cmds, binding)
                if not bound:
                    continue
                bound_paths = cmds.ls(bound, long=True) or []
                if len(bound_paths) != 1:
                    continue
                bound = str(bound_paths[0])
                if target not in targets:
                    targets.append(target)
            targets = self._selected_or_all_reset_targets(targets, cmds)
            # Controllers are authored relative to their ZERO groups. A bound
            # joint's bind translation is in a different local basis and must
            # never be applied to its controller.
            return targets, {target: (0.0, 0.0, 0.0) for target in targets}
        except Exception:
            logger.debug("Rest Pose target resolution failed", exc_info=True)
            return [], {}

    @staticmethod
    def _rest_pose_authored_plugs(
        root: str,
        targets: list[str],
        cmds,
    ) -> dict[str, tuple[str, ...]] | None:
        """Resolve MMD-owned semantic joints to their safe authoring inputs."""

        from ...core.mmd_control_rig_analyzer import (
            INPUT_SOLVER_OUTPUT,
            analyze_mmd_control_rig,
        )
        from ...core.mmd_control_rig_builder import read_mmd_control_rig_metadata

        metadata = read_mmd_control_rig_metadata(root, cmds_module=cmds) or {}
        owner = str(metadata.get("owner") or "MMD_OWNED")
        if owner == "CONTROL_OWNED":
            return None
        if owner != "MMD_OWNED":
            raise RuntimeError(f"unsupported Reset Pose owner: {owner}")
        spec = analyze_mmd_control_rig(root, cmds_module=cmds)
        bindings = {str(binding.joint): binding for binding in spec.bones}
        role_bindings = {}
        for role in spec.roles:
            binding = role.binding
            if binding is None or binding.blocked or not binding.authored_plugs:
                continue
            role_bindings.setdefault(str(binding.joint), []).append(binding)
        result = {}
        for target in targets:
            paths = cmds.ls(target, long=True) or []
            if len(paths) != 1:
                raise RuntimeError(f"ambiguous Reset Pose target: {target}")
            resolved = str(paths[0])
            binding = bindings.get(resolved)
            candidates = []
            if binding is not None and not binding.blocked and binding.authored_plugs:
                candidates.append(tuple(str(plug) for plug in binding.authored_plugs))
            candidates.extend(
                tuple(str(plug) for plug in role_binding.authored_plugs)
                for role_binding in role_bindings.get(resolved, ())
            )
            if (
                not candidates
                and binding is not None
                and binding.input_kind == INPUT_SOLVER_OUTPUT
            ):
                candidates.append(
                    AnimationPresenter._rest_pose_solver_input_route(
                        binding,
                        resolved,
                        cmds,
                    )
                )
            routes = tuple(dict.fromkeys(candidates))
            if len(routes) != 1:
                raise RuntimeError(
                    f"Reset Pose authoring route is unavailable: {resolved}"
                )
            result[resolved] = routes[0]
        return result

    @staticmethod
    def _rest_pose_solver_input_route(
        binding,
        target: str,
        cmds,
    ) -> tuple[str, ...]:
        """Map one CCD output index through chain metadata to its bone-slot input."""

        rows = [
            row
            for row in binding.incoming
            if str(row.source_node_type) == "mmdCcdIk"
        ]
        if len(rows) != 1:
            raise RuntimeError(
                f"Reset Pose solver output route is ambiguous: {target}"
            )
        row = rows[0]
        if str(row.destination_plug) != f"{target}.rotate":
            raise RuntimeError(
                f"Reset Pose solver output destination is unsupported: {target}"
            )
        source = str(row.source_plug)
        node, separator, attribute = source.rpartition(".")
        prefix = "outputRotate["
        if (
            not separator
            or not node
            or not attribute.startswith(prefix)
            or not attribute.endswith("]")
        ):
            raise RuntimeError(
                f"Reset Pose solver output index is unavailable: {target}"
            )
        index = attribute[len(prefix) : -1]
        if not index.isdigit():
            raise RuntimeError(
                f"Reset Pose solver output index is unavailable: {target}"
            )
        try:
            chain = json.loads(cmds.getAttr(f"{node}.chainJson"))
            links = chain["links"]
            if not isinstance(links, list):
                raise TypeError("links must be a list")
            slots = []
            for link in links:
                if not isinstance(link, dict):
                    raise TypeError("link must be an object")
                slot = link["bone_slot"]
                if isinstance(slot, bool) or not isinstance(slot, int) or slot < 0:
                    raise ValueError("bone_slot must be a non-negative integer")
                slots.append(slot)
            if len(set(slots)) != len(slots):
                raise ValueError("bone_slot values must be unique")
            slot = slots[int(index)]
        except (
            KeyError,
            IndexError,
            RuntimeError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise RuntimeError(
                f"Reset Pose solver chain metadata is unavailable: {target}"
            ) from exc
        compound = f"{node}.inputRotate[{slot}]"
        children = tuple(
            f"{compound}.inputRotateElement{axis}" for axis in "XYZ"
        )
        exists = getattr(cmds, "objExists", None)
        if not callable(exists) or not exists(compound) or not all(
            exists(child) for child in children
        ):
            raise RuntimeError(
                f"Reset Pose solver input is unavailable: {target}"
            )
        return children

    def _selected_or_all_reset_targets(self, targets: list[str], cmds) -> list[str]:
        """Use a valid model-owned selection, otherwise the complete model."""

        raw_selection = self.maya_adapter.ls(selection=True) or []
        if not raw_selection:
            return list(targets)
        valid = set(targets)
        selected = []
        for node in raw_selection:
            paths = cmds.ls(node, long=True) or []
            if len(paths) == 1 and str(paths[0]) in valid:
                selected.append(str(paths[0]))
        return list(dict.fromkeys(selected))

    def _rest_pose_model_joints(self, root: str, cmds) -> list[str]:
        """Resolve all model joints to unique full DAG paths."""

        candidates = []
        list_relatives = getattr(cmds, "listRelatives", None)
        if callable(list_relatives):
            candidates.extend(
                list_relatives(
                    root,
                    allDescendents=True,
                    type="joint",
                    fullPath=True,
                )
                or []
            )
        if not candidates:
            candidates.extend(self._all_model_joints)
            candidates.extend(self._bone_name_to_joint.values())
        result = []
        for candidate in candidates:
            paths = cmds.ls(candidate, long=True) or []
            if len(paths) != 1:
                continue
            path = str(paths[0])
            if path not in result:
                result.append(path)
        return result

    @staticmethod
    def _rest_pose_scope_roots(root: str, cmds) -> tuple[str, ...]:
        """Include the detached Control Rig group when it is outside the DAG."""

        scopes = [str(root)]
        try:
            from ...core.mmd_control_rig_builder import read_mmd_control_rig_metadata

            metadata = read_mmd_control_rig_metadata(root, cmds_module=cmds) or {}
            group_uuid = metadata.get("controlGroupUuid")
            if group_uuid:
                paths = cmds.ls(group_uuid, long=True) or []
                if len(paths) == 1:
                    scopes.append(str(paths[0]))
        except Exception:
            logger.debug("Could not resolve Control Rig Rest Pose scope", exc_info=True)
        return tuple(dict.fromkeys(scopes))

    def _selected_bind_translations(
        self,
        joints: list[str],
    ) -> dict[str, tuple[float, float, float]]:
        """Read persisted import-time local translations for selected joints."""
        bind_translations: dict[str, tuple[float, float, float]] = {}
        for joint in joints:
            try:
                if not self.maya_adapter.attribute_exists(
                    "mmd_vmd_bind_translate", joint
                ):
                    continue
                raw_value = self.maya_adapter.get_attr(
                    f"{joint}.mmd_vmd_bind_translate"
                )
                values = json.loads(raw_value)
                if not isinstance(values, (list, tuple)) or len(values) != 3:
                    continue
                bind_translations[joint] = tuple(float(value) for value in values)
            except (TypeError, ValueError, json.JSONDecodeError):
                logger.debug("Invalid bind translate metadata on %s", joint)
            except Exception:
                logger.debug("Failed to read bind translate metadata on %s", joint, exc_info=True)
        return bind_translations

    def _on_mirror_pose(self):
        try:
            if not (self.maya_adapter.ls(selection=True) or []):
                self._set_status("no_joints_selected")
                return
            mappings, _owner, model_uuid = self._mirror_mappings_for_selection()
            root = self.app_state.current_model_root
            cmds = getattr(self.maya_adapter, "_cmds", None)
            if cmds is None:
                from ..mirror_actions import MirrorPoseTransaction

                count = MirrorPoseTransaction(
                    self.maya_adapter,
                    model_root=root or "",
                    model_uuid=model_uuid or "",
                    mappings=mappings,
                ).apply()
            else:
                roots = cmds.ls(root, uuid=True) if root else []
                if len(roots) != 1 or model_uuid != str(roots[0]):
                    raise RuntimeError("MMD model UUID is unavailable")
                from ..mirror_actions import MirrorPoseTransaction

                count = MirrorPoseTransaction(
                    self.maya_adapter,
                    model_root=root,
                    model_uuid=str(roots[0]),
                    mappings=mappings,
                    scope_roots=self._rest_pose_scope_roots(root, cmds),
                ).apply()
            self._set_status("mirrored_pose", count=count)
        except Exception as exc:
            # Keep the old headless status contract for the pre-Maya action
            # test double while production always reports the real blocker.
            error = exc
            if getattr(self.maya_adapter, "_cmds", None) is None:
                error = NotImplementedError("Mirror Pose not yet implemented")
            self._set_status("mirror_failed", error=error)

    def _on_bake_animation(self):
        from ...actions.pose_actions import BakeAnimationAction, BakeAnimationRequest

        joints = self._selected_joints()
        if not joints:
            self._set_status("no_joints_selected")
            return
        result = BakeAnimationAction(self.maya_adapter).execute(
            BakeAnimationRequest(joints=joints)
        )
        if result.succeeded:
            self._set_status("baked_animation")
        else:
            self._set_status("bake_failed", error=result.error)

    def _on_clean_curves(self):
        from ...actions.pose_actions import CleanCurvesAction, CleanCurvesRequest

        joints = self._selected_joints()
        if not joints:
            self._set_status("no_joints_selected")
            return
        result = CleanCurvesAction(self.maya_adapter).execute(
            CleanCurvesRequest(joints=joints)
        )
        if result.succeeded:
            self._set_status("cleaned_curves")
        else:
            self._set_status("clean_failed", error=result.error)
