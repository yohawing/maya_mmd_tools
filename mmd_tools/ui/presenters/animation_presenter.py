"""Presenter for the Animator Toolset tab."""

from __future__ import annotations

import json
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
from ...core.mmd_bone_names import normalize_mmd_bone_name
from ...core.mmd_control_rig_builder import inspect_mmd_control_rig
from ...core.morph_metadata_reader import (
    CategorizedMorphs,
    MorphInfo,
    categorize_morphs,
    parse_blendshape_morph_entries,
    morph_info_from_presenter_entry,
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

_IK_BONE_NAMES = {
    "left": "左足ＩＫ",
    "right": "右足ＩＫ",
}
_TOE_IK_BONE_NAMES = {
    "left": "左つま先ＩＫ",
    "right": "右つま先ＩＫ",
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
        self._last_ik_states: dict[str, bool] = {}
        self._disposed = False
        # Animator Reset Pose is a one-shot selection action, distinct from
        # Bone Editor's model-wide Go to Bind Pose inspection command.
        self.connect_signals()
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
        self.view.body_picker.mirror_selection_clicked.connect(self.on_mirror_selection)
        if hasattr(self.view.body_picker, "reset_pose_clicked"):
            self.view.body_picker.reset_pose_clicked.connect(self._on_reset_pose)
        if hasattr(self.view.body_picker, "select_all_clicked"):
            self.view.body_picker.select_all_clicked.connect(self.on_select_all)
        if hasattr(self.view.body_picker, "clear_selection_clicked"):
            self.view.body_picker.clear_selection_clicked.connect(self.on_clear_clicked)
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
        self.view.finger_picker.mirror_selection_clicked.connect(self.on_mirror_selection)
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
        for key, btn in getattr(self.view, "control_rig_buttons", {}).items():
            btn.clicked.connect(
                lambda _checked=False, k=key: self._on_control_rig_clicked(k)
            )

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
        try:
            self.app_state.current_model_changed.disconnect(self.on_current_model_changed)
        except (RuntimeError, TypeError):
            pass
        try:
            self.app_state.model_list_updated.disconnect(self.on_model_list_updated)
        except (RuntimeError, TypeError):
            pass

    def retranslate_ui(self):
        """Retranslate presenter-owned dynamic controls without reloading the model."""

        for header, category_key, count in self._morph_group_headers:
            expanded = header.isChecked()
            title = self.view.tr(category_key, "animation_toolset")
            header.setText(f"{'▾' if expanded else '▸'}  {title}    {count}")
        self._retranslate_picker_bone_tooltips()

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
        node = self._ik_nodes_by_side.get(side)
        if not node:
            self._set_status("ik_not_found", side=side_label)
            return
        try:
            self.maya_adapter.set_attr(f"{node}.enabled", bool(enabled))
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
                    self._ik_nodes_by_side.get(side),
                    self._toe_ik_nodes_by_side.get(side),
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
                    self.maya_adapter.get_attr(f"{node}.enabled")
                )
            except Exception:
                current_states[node] = False
        enabled = not all(current_states.values())
        updated_nodes = []
        try:
            for node in nodes:
                self.maya_adapter.set_attr(f"{node}.enabled", enabled)
                updated_nodes.append(node)
        except Exception as exc:
            for node in reversed(updated_nodes):
                try:
                    self.maya_adapter.set_attr(
                        f"{node}.enabled", current_states[node]
                    )
                except Exception:
                    logger.debug("Failed to restore IK Enable for %s", node)
            logger.debug("IK Enable toggle failed: %s", exc)
            self._sync_ik_picker_state(force=True)
            self._set_status("ik_toggle_failed", error=exc)
            return
        self._sync_ik_picker_state(force=True)
        self._set_status("ik_enabled" if enabled else "ik_disabled", side=side_label)

    def _on_control_rig_clicked(self, action: str) -> None:
        """Run one explicit MMD-native control-rig state action."""
        root = self.app_state.current_model_root
        if not root:
            self.view.status_label.setText("MMDモデルを選択してください")
            return
        try:
            from ...core.mmd_control_rig_builder import (
                build_mmd_control_rig,
                read_mmd_control_rig_metadata,
                remove_mmd_control_rig,
            )
            from ...core.mmd_control_rig_motion import (
                bake_mmd_control_rig,
                enter_mmd_control_rig_edit,
                restore_mmd_control_rig_attached,
            )

            if action == "create":
                build_mmd_control_rig(root)
                metadata = enter_mmd_control_rig_edit(root)
                message = (
                    "MMD Control Rig (Experimental): "
                    f"{metadata['state']} / {metadata.get('owner', 'CONTROL_OWNED')}"
                )
            elif action == "bake_control":
                metadata = enter_mmd_control_rig_edit(root)
                message = (
                    "MMD Control Rig (Experimental): "
                    f"{metadata['state']} / {metadata.get('owner', 'MMD_OWNED')}"
                )
            elif action == "bake_mmd":
                metadata = bake_mmd_control_rig(root)
                message = (
                    "MMD Control Rig (Experimental): "
                    f"{metadata['state']} / {metadata.get('owner', 'MMD_OWNED')}"
                )
            elif action == "restore":
                metadata = restore_mmd_control_rig_attached(root)
                message = (
                    "MMD Control Rig (Experimental): "
                    f"{metadata['state']} / {metadata.get('owner', 'MMD_OWNED')}"
                )
            elif action == "delete":
                removed = remove_mmd_control_rig(root)
                message = "MMD Control Rig: deleted" if removed else "MMD Control Rig: not found"
            elif action == "diagnostics":
                metadata = read_mmd_control_rig_metadata(root)
                if not metadata:
                    message = "MMD Control Rig: not found"
                else:
                    message = self._format_control_rig_diagnostics(metadata)
            else:
                raise ValueError(f"unknown MMD Control Rig action: {action}")
            self._sync_visibility_controls(root)
            self.view.status_label.setText(message)
        except Exception as exc:
            logger.error("MMD Control Rig action failed", exc_info=True)
            self.view.status_label.setText(f"MMD Control Rig error: {exc}")

    @staticmethod
    def _format_control_rig_diagnostics(metadata: dict) -> str:
        """Render concise, fail-closed diagnostics from scene metadata.

        Metadata is intentionally extensible across CR061 slices.  Unknown
        evidence fields are retained as compact JSON rather than silently
        discarded, while the mandatory owner/state and cycle fields always
        appear in the user-facing report.
        """

        owner = metadata.get("owner") or "unknown"
        state = metadata.get("state") or "unknown"
        bindings = metadata.get("bindings") or {}
        def _items(value):
            if value in (None, "", [], {}):
                return []
            if isinstance(value, (list, tuple, set)):
                return list(value)
            return [value]

        unsupported = _items(metadata.get("unsupportedRoles"))
        unsupported.extend(_items(metadata.get("unsupported")))
        diagnostics = metadata.get("diagnostics")
        if isinstance(diagnostics, dict):
            unsupported.extend(_items(diagnostics.get("unsupportedRoles")))
            unsupported.extend(_items(diagnostics.get("unsupported")))
        for role, binding in bindings.items():
            if not isinstance(binding, dict):
                continue
            if str(binding.get("inputKind", "")).lower() == "unsupported":
                blockers = binding.get("blockers") or binding.get("reasons") or []
                unsupported.append(
                    f"{role} ({', '.join(map(str, blockers))})"
                    if blockers
                    else str(role)
                )
        unsupported = list(dict.fromkeys(str(item) for item in unsupported))

        cycle = metadata.get("cycle")
        if cycle is None:
            cycle = metadata.get("cycleDiagnostics")
        if cycle is None:
            cycle = metadata.get("cycleCount")
        if cycle is None:
            cycle = metadata.get("cycleDetected")
        if cycle is None and isinstance(diagnostics, dict):
            cycle = diagnostics.get("cycle")
            if cycle is None:
                cycle = diagnostics.get("cycleDetected")
        cycle_text = "none recorded" if cycle in (None, "", [], {}) else str(cycle)

        evidence = {}
        for key in (
            "lastBake",
            "lastBakeEvidence",
            "lastOracle",
            "oracleEvidence",
            "oracle",
        ):
            if key in metadata and metadata[key] not in (None, "", [], {}):
                evidence[key] = metadata[key]
        evidence_text = json.dumps(evidence, ensure_ascii=False, sort_keys=True) if evidence else "none recorded"
        return (
            "MMD Control Rig diagnostics (Experimental): "
            f"owner={owner}; state={state}; "
            f"unsupported roles={', '.join(unsupported) if unsupported else 'none'}; "
            f"cycle={cycle_text}; bake/oracle={evidence_text}"
        )

    def _set_status(self, key: str, **values) -> None:
        """Show a localized Animator status message."""

        message = self.view.tr(key, "animation_toolset")
        self.view.status_label.setText(message.format(**values))

    def on_select_all(self):
        """Select every indexed joint belonging to the current MMD model."""

        joints = [self._preferred_rig_control(joint) for joint in self._all_model_joints]
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
            [region_id], picker="body", additive=self.view.body_picker.additive_selection
        )

    def on_body_regions_selected(self, region_ids: list[str]):
        self._select_picker_regions(
            region_ids, picker="body", additive=self.view.body_picker.additive_selection
        )

    def on_finger_region_clicked(self, region_id: str):
        self._select_picker_regions(
            [region_id], picker="finger", additive=self.view.finger_picker.additive_selection
        )

    def on_finger_regions_selected(self, region_ids: list[str]):
        self._select_picker_regions(
            region_ids, picker="finger", additive=self.view.finger_picker.additive_selection
        )

    def _select_picker_regions(
        self, region_ids: list[str], *, picker: str, additive: bool = False
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
                joints.append(self._preferred_rig_control(joint))

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
            accepted = self._select_nodes(joints, replace=not additive)
            if not accepted:
                self._set_status("no_selectable_bones")
        except Exception:
            self._set_status("selection_failed", names="、".join(labels))

    def _set_picker_selection_from_nodes(self, nodes: list[str]) -> None:
        """Reflect Maya joint names as strong picker highlights synchronously."""

        from ..widgets.body_picker_widget import _BODY_REGIONS
        from ..widgets.finger_picker_widget import _FINGER_REGIONS

        nodes = [self._joint_for_rig_control(node) for node in nodes]

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

    def _preferred_rig_control(self, joint: str) -> str:
        """Prefer the owned curve corresponding to a picker joint."""
        root = self.app_state.current_model_root
        if not root:
            return joint
        try:
            from maya import cmds

            from ...core.mmd_control_rig_builder import (
                read_mmd_control_rig_metadata,
                resolve_mmd_control_rig_binding_joint,
            )

            metadata = read_mmd_control_rig_metadata(root)
            # Picker selection follows the motion owner.  In MMD-owned mode,
            # joints remain the authoring surface; only Control-owned mode may
            # select UUID-backed controller transforms.
            owner = metadata.get("owner") if metadata else None
            if owner is None and metadata:
                # Older presenter extensions supplied only ``controls`` and
                # ``bindings``.  Keep that compatibility path; validated
                # scene metadata always includes the explicit owner field.
                owner = "CONTROL_OWNED"
            if not metadata or owner != "CONTROL_OWNED":
                return joint
            target = (cmds.ls(joint, long=True) or [joint])[0]
            for role, binding in metadata.get("bindings", {}).items():
                bound = resolve_mmd_control_rig_binding_joint(cmds, binding)
                if bound != target:
                    continue
                control_uuid = metadata.get("controls", {}).get(role)
                nodes = cmds.ls(control_uuid, long=True) if control_uuid else []
                if len(nodes) == 1:
                    return str(nodes[0])
        except Exception:
            logger.debug("MMD Control Rig picker lookup failed", exc_info=True)
        return joint

    def _joint_for_rig_control(self, node: str) -> str:
        root = self.app_state.current_model_root
        if not root:
            return node
        try:
            from maya import cmds

            from ...core.mmd_control_rig_builder import (
                read_mmd_control_rig_metadata,
                resolve_mmd_control_rig_binding_joint,
            )

            metadata = read_mmd_control_rig_metadata(root)
            owner = metadata.get("owner") if metadata else None
            if owner is None and metadata:
                owner = "CONTROL_OWNED"
            if not metadata or owner != "CONTROL_OWNED":
                return node
            selected = (cmds.ls(node, uuid=True) or [None])[0]
            for role, uuid in metadata.get("controls", {}).items():
                if uuid == selected:
                    binding = metadata.get("bindings", {}).get(role)
                    if binding:
                        return resolve_mmd_control_rig_binding_joint(cmds, binding)
        except Exception:
            logger.debug("MMD Control Rig reverse picker lookup failed", exc_info=True)
        return node

    def on_goto_finger(self):
        self.view.picker_tabs.setCurrentIndex(self.view.TAB_FINGER)

    def on_goto_body(self):
        self.view.picker_tabs.setCurrentIndex(self.view.TAB_BODY)

    def on_mirror_selection(self):
        _MIRROR_PAIRS = {"左": "右", "右": "左"}
        try:
            sel = self.maya_adapter.ls(selection=True) or []
        except Exception:
            return
        joint_to_bone = {v: k for k, v in self._bone_name_to_joint.items()}
        mirrored = []
        for node in sel:
            bone_name = joint_to_bone.get(node)
            if bone_name:
                found = False
                for jp, mirror_jp in _MIRROR_PAIRS.items():
                    if jp in bone_name:
                        mirror_bone = bone_name.replace(jp, mirror_jp, 1)
                        mirror_joint = self._bone_name_to_joint.get(mirror_bone)
                        if mirror_joint:
                            mirrored.append(mirror_joint)
                            found = True
                            break
                if not found:
                    mirrored.append(node)
            else:
                mirrored.append(node)
        if mirrored:
            try:
                accepted = self._select_nodes(mirrored)
                selected_names = [joint_to_bone.get(node, node) for node in accepted]
                self.view.status_label.setText("、".join(selected_names))
            except Exception:
                pass

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
        bone_map = self._build_bone_index_map(model_root)
        self._all_model_joints = [bone_map[index] for index in sorted(bone_map)]
        self._bone_name_to_joint = self._build_bone_name_map(model_root)
        self._ik_nodes_by_side = self._collect_leg_ik_nodes(model_root)
        self._toe_ik_nodes_by_side = self._collect_toe_ik_nodes(model_root)
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
                "mirror_sel",
                "fingers_left",
                "fingers_right",
            }
        )
        for side in _IK_BONE_NAMES:
            if side in self._ik_nodes_by_side or side in self._toe_ik_nodes_by_side:
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

    def _sync_ik_picker_state(self, *, force: bool = False) -> None:
        """Hide a side's FK controls while its evaluated leg IK is enabled."""

        states = {}
        for side, node in self._ik_nodes_by_side.items():
            try:
                states[side] = bool(self.maya_adapter.get_attr(f"{node}.enabled"))
            except Exception:
                states[side] = False
        toe_states = {}
        for side, node in self._toe_ik_nodes_by_side.items():
            try:
                toe_states[side] = bool(self.maya_adapter.get_attr(f"{node}.enabled"))
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
        self._last_ik_states = {}
        self._bone_name_to_joint.clear()
        self._sync_picker_regions()
        if hasattr(self.view.body_picker, "set_hidden_regions"):
            self.view.body_picker.set_hidden_regions(set())
        if hasattr(self.view.body_picker, "set_region_dim_levels"):
            self.view.body_picker.set_region_dim_levels({})
        self.view.display_frame_tree.clear()
        self._clear_morph_tab()
        self._sync_visibility_controls(None)
        self.view.status_label.setText("")

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
                    info = MorphInfo(raw_name, "", 4, "vertex", weight_index)
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
            network_nodes = self.maya_adapter.ls(type="network") or []
        except Exception:
            return
        for node in network_nodes:
            try:
                if not self.maya_adapter.attribute_exists("mmd_morph_type", node):
                    continue
                if self.maya_adapter.attribute_exists("mmd_model_root", node):
                    roots = self.maya_adapter.list_connections(f"{node}.mmd_model_root") or []
                    if roots and model_root not in roots:
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
            MorphRowWidgets,
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
                row_widgets = MorphRowWidgets(icon, label, slider, editor, plugs)
                row_widgets.set_value(self._morph_value(morph_name))
                row_widgets.set_animation_state(self._morph_animation_state(plugs))
                self._morph_sliders[morph_name] = slider
                self._morph_rows[morph_name] = row_widgets
                content_layout.addWidget(icon, row_index, 0)
                content_layout.addWidget(label, row_index, 1)
                content_layout.addWidget(slider, row_index, 2)
                content_layout.addWidget(editor, row_index, 3)

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
        from ...actions.pose_actions import ResetPoseAction, ResetPoseRequest

        joints = self._selected_joints()
        if not joints:
            self._set_status("no_joints_selected")
            return

        bind_translations = self._selected_bind_translations(joints)
        result = ResetPoseAction(self.maya_adapter).execute(
            ResetPoseRequest(
                joints=joints,
                bind_translations=bind_translations,
            )
        )
        if result.succeeded:
            # Reuse the established Animator translation keys; the action is
            # now one-shot, but the existing localized status surface remains
            # the compatibility boundary for all supported languages.
            self._set_status("reset_pose_applied", count=result.reset_count)
        else:
            self._set_status("reset_pose_failed", error=result.error)

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
        from ...actions.pose_actions import MirrorPoseAction, MirrorPoseRequest

        joints = self._selected_joints()
        if not joints:
            self._set_status("no_joints_selected")
            return
        result = MirrorPoseAction(self.maya_adapter).execute(
            MirrorPoseRequest(joints=joints)
        )
        if result.succeeded:
            self._set_status("mirrored_pose")
        else:
            self._set_status("mirror_failed", error=result.error)

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
