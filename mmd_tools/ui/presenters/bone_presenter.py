import math
from collections.abc import Sequence
from dataclasses import replace
from typing import Protocol

from mmd_tools.adapters import MayaCmdsAdapter
from mmd_tools.core.bone_authoring import BoneResetPlan
from mmd_tools.core.model_authoring_spec import MmdBoneSpec
from ...core.logger import get_logger
from ...core.maya_attribute_utils import (
    get_attribute,
    set_custom_attributes,
)
from ...core.maya_scene_utils import object_exists
from ...core.constants import (
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_BONE_NAME_EN,
    ATTR_MMD_BONE_FLAGS,
    ATTR_MMD_DEFORM_LAYER,
    ATTR_MMD_BONE_OFFSET,
    ATTR_MMD_CONNECTION_BONE,
    ATTR_MMD_IK_TARGET,
    ATTR_MMD_IK_TARGET_INDEX,
    ATTR_MMD_IK_LOOP,
    ATTR_MMD_IK_LIMIT_ANGLE,
    ATTR_MMD_IK_LINKS,
    ATTR_MMD_GRANT_PARENT,
    ATTR_MMD_GRANT_RATE,
    ATTR_MMD_FIXED_AXIS,
    ATTR_MMD_LOCAL_X_AXIS,
    ATTR_MMD_LOCAL_Z_AXIS,
    ATTR_MMD_EXTERNAL_PARENT_KEY,
    ATTR_MMD_BONE_INDEX,
)
from ...core.pmx_data.bone import PmxBoneFlag
from ..qt_compat import (
    QListWidgetItem,
    Qt,
    QCheckBox,
    QTableWidgetItem,
    QTimer,
)
from .list_presenter_helpers import (
    apply_list_filter,
    format_indexed_node_label,
    reload_for_current_model_change,
    select_existing_user_role_nodes,
    tr_message,
    tr_message_format,
)

logger = get_logger(__name__)


class BoneAuthoringCoordinator(Protocol):
    """Transactional semantic/binding boundary for Bone Tab registration."""

    def register_bone(self, model_root: str, bone: MmdBoneSpec) -> object: ...

    def capture_rest(self, model_root: str, bone_index: int, joint: str) -> MmdBoneSpec: ...

    def read_spec(self, model_root: str) -> object: ...

    def read_bone_value(self, model_root: str, bone_index: int, binding: str) -> MmdBoneSpec: ...

    def apply_bone_value_patch(self, model_root: str, bone: MmdBoneSpec) -> MmdBoneSpec: ...

    def replace_bone(
        self,
        model_root: str,
        bone: MmdBoneSpec,
        world_position: Sequence[float],
    ) -> object: ...

    def replace_bone_semantic(self, model_root: str, bone: MmdBoneSpec) -> object: ...

    def reindex_bones(self, model_root: str, ordered_indices: Sequence[int]) -> object: ...

    def unregister_bone(self, model_root: str, bone_index: int) -> object: ...

    def plan_bone_reset(self, model_root: str, requested_order=None) -> BoneResetPlan: ...

    def reset_bones(self, model_root: str, plan: BoneResetPlan, requested_order=None) -> object: ...


class BonePresenter:
    def __init__(
        self,
        view,
        app_state,
        maya_adapter=None,
        authoring_coordinator=None,
    ):
        self.view = view
        self.app_state = app_state
        self.maya_adapter = maya_adapter or MayaCmdsAdapter()
        self.authoring_coordinator = authoring_coordinator
        self.current_bone = None
        self.current_bone_index = None
        self._model_root_valid = False
        self._registered_indices = {}
        self._reindex_dirty = False
        self._pending_order = []
        self._reset_plan = None
        self.bone_data = {}  # Store original bone data for reset
        self.bone_list_items = {}  # Map bone name to list item
        self.all_bones = []  # All bones list
        self.is_updating = False  # Prevent feedback loops
        self.connect_signals()
        self._update_authoring_actions()

        # 既に選択されているモデルがある場合はロード
        if self.app_state.current_model_root:
            # Qt のイベントループが安定してから実行
            QTimer.singleShot(100, self.load_bones)

    def connect_signals(self):
        # ApplicationStateのシグナル
        self.app_state.current_model_changed.connect(self.on_current_model_changed)

        # リストビューのシグナル
        self.view.bone_list.currentItemChanged.connect(self.on_bone_selected)
        self.view.bone_list.itemSelectionChanged.connect(self.on_selection_changed_maya)
        self.view.refresh_btn.clicked.connect(self.load_bones)
        for button_name, handler in (
            ("reindex_up_btn", lambda: self.move_reindex(-1)),
            ("reindex_down_btn", lambda: self.move_reindex(1)),
            ("reset_authoring_btn", self.reset_authoring),
        ):
            button = getattr(self.view, button_name, None)
            if button is not None:
                button.clicked.connect(handler)
        self.view.search_edit.textChanged.connect(self.filter_bones)

        # ボーン選択ボタン
        self.view.select_ik_target_btn.clicked.connect(lambda: self.select_bone_dialog("ik_target"))
        self.view.select_grant_parent_btn.clicked.connect(lambda: self.select_bone_dialog("grant_parent"))

        # フラグチェックボックス
        self.view.ik_enabled_check.toggled.connect(self.on_ik_enabled_toggled)
        self.view.rotation_grant_check.toggled.connect(self.on_grant_toggled)
        self.view.move_grant_check.toggled.connect(self.on_grant_toggled)
        self.view.fixed_axis_check.toggled.connect(self.on_axis_toggled)
        self.view.local_axis_check.toggled.connect(self.on_axis_toggled)
        self.view.external_parent_check.toggled.connect(self.on_external_parent_toggled)
        # IKリンクテーブル
        self.view.add_ik_link_btn.clicked.connect(self.add_ik_link)
        self.view.remove_ik_link_btn.clicked.connect(self.remove_ik_link)
        self.view.move_up_btn.clicked.connect(lambda: self.move_ik_link(-1))
        self.view.move_down_btn.clicked.connect(lambda: self.move_ik_link(1))

        # 適用/リセットボタン
        self.view.apply_btn.clicked.connect(self.apply_changes)
        self.view.reset_btn.clicked.connect(self.reset_changes)

    def disconnect_signals(self):
        """Release presenter-owned resources when the owning window closes."""

    def on_current_model_changed(self, model_root):
        """現在のモデルが変更されたときの処理"""
        self.current_bone = None
        self.current_bone_index = None
        self._model_root_valid = False
        self._registered_indices.clear()
        self._reindex_dirty = False
        self._pending_order = []
        self._reset_plan = None
        self._update_authoring_actions()
        reload_for_current_model_change(logger, "BonePresenter", model_root, self.load_bones)

    def load_bones(self):
        """ボーンリストをロード"""
        self.view.bone_list.clear()
        self.bone_list_items.clear()
        self.all_bones.clear()
        self.current_bone = None
        self.current_bone_index = None
        self._model_root_valid = False
        self._registered_indices.clear()
        self._reindex_dirty = False
        self._pending_order = []
        self._reset_plan = None
        self.view.set_bone_details_enabled(False)

        current_model_root = self.app_state.current_model_root
        logger.debug(f"Current model root: {current_model_root}")

        if not current_model_root or not self.maya_adapter.object_exists(current_model_root):
            logger.warning(f"Model root does not exist: {current_model_root}")
            self._update_authoring_actions()
            return
        self._model_root_valid = True
        self._update_authoring_actions()

        # ジョイントを検索する複数の方法を試す
        # Store canonical DAG paths in rows.  Short joint names can differ from
        # the coordinator's binding identity and make a valid UI selection
        # fail its narrow selected-bone transaction.
        joints = self.maya_adapter.list_relatives(
            current_model_root,
            allDescendents=True,
            type="joint",
            fullPath=True,
        ) or []
        logger.debug(f"Found {len(joints)} joints using listRelatives")

        # もしジョイントが見つからない場合、別の方法を試す
        if not joints:
            # ルートノードの子を確認
            children = self.maya_adapter.list_relatives(
                current_model_root, children=True, fullPath=True
            ) or []
            logger.debug(f"Direct children of root: {children}")

            # 全ての子孫を取得してジョイントをフィルタ
            all_descendants = self.maya_adapter.list_relatives(
                current_model_root, allDescendents=True, fullPath=True
            ) or []
            joints = [node for node in all_descendants if self.maya_adapter.node_type(node) == "joint"]
            logger.debug(f"Found {len(joints)} joints using nodeType filter from {len(all_descendants)} descendants")

        if not joints:
            logger.info("No bones found in the model")
            return

        # mmd_bone_indexでソート
        joints_with_index = []
        for joint in joints:
            bone_index = get_attribute(joint, ATTR_MMD_BONE_INDEX)
            if type(bone_index) is int and bone_index >= 0:
                self._registered_indices[joint] = bone_index
            joints_with_index.append((joint, bone_index))

        # インデックスでソート（インデックスがない場合は最後に）
        joints_with_index.sort(key=lambda x: x[1] if x[1] is not None and x[1] >= 0 else float("inf"))

        # ソートされたジョイントリストを作成
        sorted_joints = [joint for joint, _ in joints_with_index]

        # ボーンをリストに追加
        self.all_bones = sorted_joints
        self._pending_order = [joint for joint in sorted_joints if joint in self._registered_indices]
        for idx, joint in enumerate(sorted_joints):
            # ボーン情報を取得
            name_jp = get_attribute(joint, ATTR_MMD_BONE_NAME)
            name_en = get_attribute(joint, ATTR_MMD_BONE_NAME_EN)
            bone_index = get_attribute(joint, ATTR_MMD_BONE_INDEX)

            index_label = bone_index if bone_index is not None and bone_index >= 0 else "-"
            display_text = format_indexed_node_label(index_label, name_jp, joint, name_en)

            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, joint)  # 実際のジョイント名を保存
            item.setToolTip(joint)
            self.view.bone_list.addItem(item)
            self.bone_list_items[joint] = item
        self._update_authoring_actions()

        logger.debug(f"Loaded {len(joints)} bones for model: {current_model_root}")

    def _get_bone_type(self, joint):
        """ボーンのタイプを判定"""
        flags = self._get_bone_flags(joint)

        if flags & PmxBoneFlag.IK:
            return "IK"
        elif flags & PmxBoneFlag.GRANT_PARENT_ROTATE or flags & PmxBoneFlag.GRANT_PARENT_MOVE:
            return "付与"
        elif flags & PmxBoneFlag.DEFORM_AFTER_PHYSICS:
            return "物理後"
        else:
            return "通常"

    def _get_bone_flags(self, joint):
        """Return a safe PMX flag value for a joint.

        Imported or user-created joints can exist briefly without the custom
        ``mmd_bone_flags`` attribute.  The UI should treat that state as a
        normal, unflagged bone instead of raising while a row is selected.
        """
        raw_flags = get_attribute(joint, ATTR_MMD_BONE_FLAGS)
        if raw_flags is None:
            return PmxBoneFlag(0)
        try:
            return PmxBoneFlag(int(raw_flags))
        except (TypeError, ValueError):
            logger.warning(f"Invalid bone flags on {joint!r}: {raw_flags!r}; using 0")
            return PmxBoneFlag(0)

    def filter_bones(self, text):
        """ボーンを検索フィルタリング"""
        apply_list_filter(
            self.bone_list_items.values(),
            text,
            self._bone_filter_terms,
        )

    def _bone_filter_terms(self, item):
        """Return searchable terms for a bone list item."""
        joint = item.data(Qt.UserRole)
        return (
            item.text(),
            joint,
            get_attribute(joint, ATTR_MMD_BONE_NAME) if joint else "",
            get_attribute(joint, ATTR_MMD_BONE_NAME_EN) if joint else "",
        )

    def on_bone_selected(self, current, previous):
        """ボーンが選択されたときの処理"""
        if not current:
            self.current_bone = None
            self.current_bone_index = None
            self.view.set_bone_details_enabled(False)
            self._update_authoring_actions()
            return

        self.current_bone = current.data(Qt.UserRole)
        if not self.current_bone or not object_exists(self.current_bone):
            self.current_bone_index = None
            self.view.set_bone_details_enabled(False)
            self._update_authoring_actions()
            return

        self.current_bone_index = self._registered_indices.get(self.current_bone)

        logger.debug(f"Selected bone: {self.current_bone}")
        self.view.set_bone_details_enabled(True)
        self._update_authoring_actions()
        self.load_bone_properties()

    def _update_authoring_actions(self):
        available = self._authoring_available() and self._model_root_valid
        registered = available and type(self.current_bone_index) is int
        translate = getattr(self.view, "tr", None)
        if callable(translate):
            reason_unavailable = translate("authoring_unavailable", "tooltips")
            reason_selection = translate("authoring_selection_required", "tooltips")
        else:
            reason_unavailable = "Authoring coordinator is not available"
            reason_selection = "Select an item first"
        for button_name, enabled, reason, reason_key in (
            ("reindex_up_btn", registered, "" if registered else (reason_selection if available else reason_unavailable), "" if registered else ("authoring_selection_required" if available else "authoring_unavailable")),
            ("reindex_down_btn", registered, "" if registered else (reason_selection if available else reason_unavailable), "" if registered else ("authoring_selection_required" if available else "authoring_unavailable")),
            ("reset_authoring_btn", available, "" if available else reason_unavailable, "" if available else "authoring_unavailable"),
            # Compatibility for injected legacy views; BoneTab no longer
            # creates these individual-operation buttons.
            ("register_joint_btn", False, reason_unavailable, "authoring_unavailable"),
            ("capture_rest_btn", False, reason_unavailable, "authoring_unavailable"),
            ("apply_reindex_btn", False, reason_unavailable, "authoring_unavailable"),
            ("unregister_btn", False, reason_unavailable, "authoring_unavailable"),
        ):
            button = getattr(self.view, button_name, None)
            if button is not None:
                set_reason = getattr(button, "set_disabled_reason", None)
                if callable(set_reason):
                    set_reason(reason, reason_key)
                button.setEnabled(bool(enabled))

    def _authoring_available(self):
        if self.authoring_coordinator is None:
            return False
        if not all(
            callable(getattr(self.authoring_coordinator, method, None))
            for method in ("read_spec", "replace_bone_semantic")
        ):
            return False
        return all(
            callable(getattr(self.authoring_coordinator, method, None))
            for method in ("plan_bone_reset", "reset_bones")
        ) or all(
            callable(getattr(self.authoring_coordinator, method, None))
            for method in ("register_bone", "capture_rest", "reindex_bones", "unregister_bone")
        )

    def _authoring_root(self):
        root = self.app_state.current_model_root
        if not root or not self.maya_adapter.object_exists(root):
            self._model_root_valid = False
            self._update_authoring_actions()
            self.app_state.emit_status(tr_message("bone_authoring_root_missing"))
            return None
        if not self._authoring_available():
            self.app_state.emit_status(tr_message("bone_authoring_unavailable"))
            return None
        self._model_root_valid = True
        return root

    def _run_authoring(self, operation, *args):
        root = self._authoring_root()
        if root is None:
            return False
        try:
            getattr(self.authoring_coordinator, operation)(root, *args)
        except Exception as exc:
            logger.error("Bone authoring %s failed", operation, exc_info=True)
            self.app_state.emit_status(
                tr_message_format("bone_authoring_failed", operation=operation, error=str(exc))
            )
            return False
        self.load_bones()
        self.app_state.emit_status(tr_message(f"bone_{operation}_succeeded"))
        return True

    def register_selected_joint(self):
        """Register exactly one selected Maya joint and append its row."""
        try:
            selected = tuple(
                self.maya_adapter.ls(selection=True, type="joint", long=True) or ()
            )
        except Exception:
            selected = ()
        if len(selected) != 1:
            self.app_state.emit_status(tr_message("bone_register_selection_required"))
            return False
        joint = selected[0]
        root = self._authoring_root()
        if root is None:
            return False
        try:
            result = self.authoring_coordinator.register_selected_joint(root, joint)
        except Exception as exc:
            logger.error("Bone authoring register_selected_joint failed", exc_info=True)
            self.app_state.emit_status(
                tr_message_format(
                    "bone_authoring_failed", operation="register_selected_joint", error=str(exc)
                )
            )
            return False
        if not isinstance(result, MmdBoneSpec) or not result.binding_identity:
            self.app_state.emit_status(
                tr_message_format(
                    "bone_authoring_failed",
                    operation="register_selected_joint",
                    error="invalid registered bone result",
                )
            )
            return False
        binding = result.binding_identity
        item = QListWidgetItem(
            format_indexed_node_label(result.index, result.name, binding, result.name_english)
        )
        item.setData(Qt.UserRole, binding)
        item.setToolTip(binding)
        self.view.bone_list.addItem(item)
        self.bone_list_items[binding] = item
        self.all_bones.append(binding)
        self._registered_indices[binding] = result.index
        self._pending_order.append(binding)
        self.current_bone = binding
        self.current_bone_index = result.index
        self.view.bone_list.setCurrentItem(item)
        self.view.set_bone_details_enabled(True)
        self._update_authoring_actions()
        self.app_state.emit_status(tr_message("bone_register_selected_joint_succeeded"))
        return result

    def capture_rest(self):
        """Capture rest separately for the currently registered bone."""
        if type(self.current_bone_index) is not int or not self.current_bone:
            self.app_state.emit_status(tr_message("bone_registered_selection_required"))
            return False
        root = self._authoring_root()
        if root is None:
            return False
        try:
            result = self.authoring_coordinator.capture_rest(
                root, self.current_bone_index, self.current_bone
            )
        except Exception as exc:
            logger.error("Bone authoring capture_rest failed", exc_info=True)
            self.app_state.emit_status(
                tr_message_format("bone_authoring_failed", operation="capture_rest", error=str(exc))
            )
            return False
        self._update_selected_row_after_patch(result)
        self.app_state.emit_status(tr_message("bone_capture_rest_succeeded"))
        return result

    def move_reindex(self, direction):
        """Move one registered row in the explicit pending reindex order."""
        if type(self.current_bone_index) is not int or direction not in (-1, 1):
            return False
        row = self.view.bone_list.currentRow()
        target = row + direction
        if row < 0 or target < 0 or target >= len(self.all_bones):
            return False
        target_bone = self.all_bones[target]
        if target_bone not in self._registered_indices:
            return False
        item = self.view.bone_list.takeItem(row)
        self.view.bone_list.insertItem(target, item)
        self.view.bone_list.setCurrentItem(item)
        bone = self.all_bones.pop(row)
        self.all_bones.insert(target, bone)
        self._pending_order = [item for item in self.all_bones if item in self._registered_indices]
        self._reindex_dirty = True
        self._update_authoring_actions()
        self.app_state.emit_status(tr_message("bone_reindex_pending"))
        return True

    def reset_authoring(self):
        """Preview then atomically reconcile all descendant joints and PMX data."""
        root = self._authoring_root()
        if root is None:
            return False
        requested = tuple(self._pending_order or [item for item in self.all_bones if item in self._registered_indices])
        try:
            if not callable(getattr(self.authoring_coordinator, "plan_bone_reset", None)):
                # Legacy injected presenters are kept operational for external
                # integrations; the production BoneTab always takes the atomic
                # planner path above.
                ordered_indices = tuple(self._registered_indices[item] for item in requested)
                return bool(self.authoring_coordinator.reindex_bones(root, ordered_indices))
            plan = self._plan_reset()
            if plan.blockers:
                self.app_state.emit_status(
                    tr_message_format("bone_reset_blocked", blockers="; ".join(plan.blockers))
                )
                return False
            result = self.authoring_coordinator.reset_bones(root, plan)
        except Exception as exc:
            logger.error("Bone authoring reset failed", exc_info=True)
            self.app_state.emit_status(
                tr_message_format("bone_authoring_failed", operation="reset", error=str(exc))
            )
            return False
        self._reindex_dirty = False
        self.load_bones()
        diff = plan.diff
        self.app_state.emit_status(
            tr_message_format(
                "bone_reset_succeeded",
                added=diff["added"], removed=diff["removed"], rest=diff["rest_updated"],
            )
        )
        return result

    def _plan_reset(self):
        """Build and display the immutable reset diff without applying it."""
        root = self._authoring_root()
        if root is None:
            return None
        requested = tuple(self._pending_order or [item for item in self.all_bones if item in self._registered_indices])
        plan = self.authoring_coordinator.plan_bone_reset(root, requested_order=requested)
        self._reset_plan = plan
        warning_text = "; ".join(plan.warnings)
        label = getattr(self.view, "animation_warning_label", None)
        if label is not None:
            label.setText(warning_text)
            label.setVisible(bool(warning_text))
        return plan


    def unregister_bone(self):
        """Confirm and unregister the current semantic bone."""
        if type(self.current_bone_index) is not int:
            self.app_state.emit_status(tr_message("bone_registered_selection_required"))
            return False
        from ..qt_compat import QMessageBox

        reply = QMessageBox.question(
            self.view,
            tr_message("bone_unregister_title"),
            tr_message("bone_unregister_confirm"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return False
        return self._run_authoring("unregister_bone", self.current_bone_index)

    def on_selection_changed_maya(self):
        """リスト選択が変更されたときにMayaでも選択する"""
        if self.is_updating:
            return

        select_existing_user_role_nodes(
            self.view.bone_list,
            self.maya_adapter,
            Qt.UserRole,
            exists=object_exists,
            logger=logger,
            label="joints",
        )

    def load_bone_properties(self):
        """選択されたボーンのプロパティをロード"""
        if not self.current_bone:
            return

        self.is_updating = True
        try:
            # 基本情報
            self.view.bone_name_jp_edit.setText(get_attribute(self.current_bone, ATTR_MMD_BONE_NAME))
            self.view.bone_name_en_edit.setText(get_attribute(self.current_bone, ATTR_MMD_BONE_NAME_EN))

            # 親ボーン
            parent = self.maya_adapter.list_relatives(self.current_bone, parent=True, type="joint")
            self.view.parent_bone_edit.setText(parent[0] if parent else "")

            # 変形階層
            self.view.deform_layer_spin.setValue(get_attribute(self.current_bone, ATTR_MMD_DEFORM_LAYER))

            # ボーンフラグ
            flags = self._get_bone_flags(self.current_bone)

            # 基本フラグ
            self.view.rotatable_check.setChecked(bool(flags & PmxBoneFlag.ROTATABLE))
            self.view.movable_check.setChecked(bool(flags & PmxBoneFlag.MOVABLE))
            self.view.visible_check.setChecked(bool(flags & PmxBoneFlag.DISPLAY))
            self.view.enabled_check.setChecked(bool(flags & PmxBoneFlag.OPERATABLE))

            # 特殊フラグ
            self.view.after_physics_check.setChecked(bool(flags & PmxBoneFlag.DEFORM_AFTER_PHYSICS))
            self.view.external_parent_check.setChecked(bool(flags & PmxBoneFlag.EXTERNAL_PARENT_DEFORM))

            # IK設定
            self.view.ik_enabled_check.setChecked(bool(flags & PmxBoneFlag.IK))
            self._load_ik_settings()

            # 付与設定
            self.view.rotation_grant_check.setChecked(bool(flags & PmxBoneFlag.GRANT_PARENT_ROTATE))
            self.view.move_grant_check.setChecked(bool(flags & PmxBoneFlag.GRANT_PARENT_MOVE))
            self._load_grant_settings()

            # 軸制限
            self.view.fixed_axis_check.setChecked(bool(flags & PmxBoneFlag.AXIS_FIXED))
            self.view.local_axis_check.setChecked(bool(flags & PmxBoneFlag.LOCAL_AXIS))
            self._load_axis_settings()

            # 外部親
            if flags & PmxBoneFlag.EXTERNAL_PARENT_DEFORM:
                key = get_attribute(self.current_bone, ATTR_MMD_EXTERNAL_PARENT_KEY)
                self.view.external_parent_key_spin.setValue(key)

            # 初期状態の表示/非表示を設定
            self.on_grant_toggled()
            self.on_axis_toggled()
            self.on_external_parent_toggled(self.view.external_parent_check.isChecked())

            # データを保存（リセット用）
            self._store_bone_data()

        finally:
            self.is_updating = False

    def _load_ik_settings(self):
        """IK設定をロード"""
        if not self.view.ik_enabled_check.isChecked():
            self.view.ik_settings_group.setVisible(False)
            self.view.ik_links_group.setVisible(False)
            return

        self.view.ik_settings_group.setVisible(True)
        self.view.ik_links_group.setVisible(True)

        # IKターゲット
        ik_target_index = get_attribute(self.current_bone, ATTR_MMD_IK_TARGET_INDEX)
        if isinstance(ik_target_index, int) and 0 <= ik_target_index < len(self.all_bones):
            ik_target = self.all_bones[ik_target_index]
            display_name = self._get_bone_display_name(ik_target)
        else:
            display_name = ""
        self.view.ik_target_edit.setText(display_name)

        # IKループ回数
        ik_loop = get_attribute(self.current_bone, ATTR_MMD_IK_LOOP)
        self.view.ik_loop_spin.setValue(ik_loop)

        # 制限角度（ラジアンから度に変換）
        ik_limit_rad = get_attribute(self.current_bone, ATTR_MMD_IK_LIMIT_ANGLE)
        ik_limit_deg = math.degrees(ik_limit_rad)
        self.view.ik_limit_angle_spin.setValue(ik_limit_deg)

        # IKリンクをロード
        self._load_ik_links()

    def _load_ik_links(self):
        """IKリンクをロード"""
        self.view.ik_links_table.setRowCount(0)

        # IKリンクデータを取得
        ik_links = self._get_attr_safe(self.current_bone, ATTR_MMD_IK_LINKS, [])
        logger.debug(f"Loading IK links for {self.current_bone}: {ik_links}")

        for link_data in ik_links:
            if isinstance(link_data, dict):
                bone_index = link_data.get("bone", "")
                logger.debug(f"Processing IK link with bone index: {bone_index}")

                # ボーンインデックスから実際のボーン名を取得
                if isinstance(bone_index, int) and bone_index < len(self.all_bones):
                    bone_name = self.all_bones[bone_index]
                else:
                    bone_name = str(bone_index)

                display_name = self._get_bone_display_name(bone_name)
                self._add_ik_link_row(
                    display_name,
                    link_data.get("limit_enabled", False),
                    link_data.get("lower_limit", [0.0, 0.0, 0.0]),
                    link_data.get("upper_limit", [0.0, 0.0, 0.0]),
                )

    def _add_ik_link_row(self, bone_name, limit_enabled, lower_limit, upper_limit):
        """IKリンクテーブルに行を追加"""
        row = self.view.ik_links_table.rowCount()
        self.view.ik_links_table.insertRow(row)

        # ボーン名
        self.view.ik_links_table.setItem(row, 0, QTableWidgetItem(bone_name))

        # 角度制限チェックボックス
        limit_check = QCheckBox()
        limit_check.setChecked(limit_enabled)
        self.view.ik_links_table.setCellWidget(row, 1, limit_check)

        # 下限
        for i, value in enumerate(lower_limit[:3]):
            item = QTableWidgetItem(f"{math.degrees(value):.1f}")
            self.view.ik_links_table.setItem(row, 2 + i, item)

        # 上限
        for i, value in enumerate(upper_limit[:3]):
            item = QTableWidgetItem(f"{math.degrees(value):.1f}")
            self.view.ik_links_table.setItem(row, 5 + i, item)

    def _load_grant_settings(self):
        """付与設定をロード"""
        grant_enabled = self.view.rotation_grant_check.isChecked() or self.view.move_grant_check.isChecked()
        self.view.grant_settings_group.setVisible(grant_enabled)

        if not grant_enabled:
            return

        # 付与親
        grant_parent = get_attribute(self.current_bone, ATTR_MMD_GRANT_PARENT)
        display_name = self._get_bone_display_name(grant_parent)
        self.view.grant_parent_edit.setText(display_name)

        # 付与率
        grant_rate = get_attribute(self.current_bone, ATTR_MMD_GRANT_RATE)
        self.view.grant_rate_spin.setValue(grant_rate)

        # ローカル付与
        flags = self._get_bone_flags(self.current_bone)
        self.view.local_grant_check.setChecked(bool(flags & 0x0080))

    def _load_axis_settings(self):
        """軸制限設定をロード"""
        # 軸固定
        self.view.fixed_axis_group.setVisible(self.view.fixed_axis_check.isChecked())
        if self.view.fixed_axis_check.isChecked():
            fixed_axis = self._get_attr_safe(self.current_bone, ATTR_MMD_FIXED_AXIS, [0.0, 0.0, 1.0])
            # リストまたはタプルであることを確認
            if isinstance(fixed_axis, (list, tuple)) and len(fixed_axis) >= 3:
                self.view.fixed_axis_x_spin.setValue(float(fixed_axis[0]))
                self.view.fixed_axis_y_spin.setValue(float(fixed_axis[1]))
                self.view.fixed_axis_z_spin.setValue(float(fixed_axis[2]))
            else:
                # デフォルト値を使用
                self.view.fixed_axis_x_spin.setValue(0.0)
                self.view.fixed_axis_y_spin.setValue(0.0)
                self.view.fixed_axis_z_spin.setValue(1.0)

        # ローカル軸
        self.view.local_axis_group.setVisible(self.view.local_axis_check.isChecked())
        if self.view.local_axis_check.isChecked():
            local_x = get_attribute(self.current_bone, ATTR_MMD_LOCAL_X_AXIS)
            if local_x is None:
                local_x = [1.0, 0.0, 0.0]

            local_z = get_attribute(self.current_bone, ATTR_MMD_LOCAL_Z_AXIS)
            if local_z is None:
                local_z = [0.0, 0.0, 1.0]

            # リストまたはタプルであることを確認し、リストに変換
            if isinstance(local_x, (list, tuple)):
                local_x = list(local_x)
            else:
                logger.warning(f"local_x is not a list or tuple: {type(local_x)}")
                local_x = [1.0, 0.0, 0.0]

            if isinstance(local_z, (list, tuple)):
                local_z = list(local_z)
            else:
                logger.warning(f"local_z is not a list or tuple: {type(local_z)}")
                local_z = [0.0, 0.0, 1.0]

            # 各要素を個別に取得して型を確認
            x_val = float(local_x[0]) if len(local_x) > 0 else 1.0
            y_val = float(local_x[1]) if len(local_x) > 1 else 0.0
            z_val = float(local_x[2]) if len(local_x) > 2 else 0.0

            self.view.local_x_axis_x_spin.setValue(x_val)
            self.view.local_x_axis_y_spin.setValue(y_val)
            self.view.local_x_axis_z_spin.setValue(z_val)

            x_val = float(local_z[0]) if len(local_z) > 0 else 0.0
            y_val = float(local_z[1]) if len(local_z) > 1 else 0.0
            z_val = float(local_z[2]) if len(local_z) > 2 else 1.0

            self.view.local_z_axis_x_spin.setValue(x_val)
            self.view.local_z_axis_y_spin.setValue(y_val)
            self.view.local_z_axis_z_spin.setValue(z_val)

    def _store_bone_data(self):
        """現在のボーンデータを保存（リセット用）"""
        if not self.current_bone:
            return

        self.bone_data = {
            "name_jp": self.view.bone_name_jp_edit.text(),
            "name_en": self.view.bone_name_en_edit.text(),
            "deform_layer": self.view.deform_layer_spin.value(),
            "flags": int(self._get_bone_flags(self.current_bone)),
            "structural": self._structural_ui_state(),
            "all_settings": self._gather_all_settings(),
        }

    def select_bone_dialog(self, target_type):
        """ボーン選択ダイアログを表示"""
        # 簡易的な実装：現在のMaya選択を使用
        selected = self.maya_adapter.ls(selection=True, type="joint")
        if not selected:
            self.app_state.emit_status(tr_message("select_joint"))
            return

        bone = selected[0]
        display_name = self._get_bone_display_name(bone)

        if target_type == "ik_target":
            self.view.ik_target_edit.setText(display_name)
        elif target_type == "grant_parent":
            self.view.grant_parent_edit.setText(display_name)

    def _is_descendant(self, parent, child):
        """childがparentの子孫かどうかをチェック"""
        if not parent or not child:
            return False

        descendants = self.maya_adapter.list_relatives(parent, allDescendents=True, type="joint") or []
        return child in descendants

    def on_ik_enabled_toggled(self, checked):
        """IK有効化トグル時の処理"""
        self.view.ik_settings_group.setVisible(checked)
        self.view.ik_links_group.setVisible(checked)

    def on_grant_toggled(self):
        """付与設定トグル時の処理"""
        enabled = self.view.rotation_grant_check.isChecked() or self.view.move_grant_check.isChecked()
        self.view.grant_settings_group.setVisible(enabled)

    def on_axis_toggled(self):
        """軸制限トグル時の処理"""
        self.view.fixed_axis_group.setVisible(self.view.fixed_axis_check.isChecked())
        self.view.local_axis_group.setVisible(self.view.local_axis_check.isChecked())

    def on_external_parent_toggled(self, checked):
        """外部親変形トグル時の処理"""
        self.view.external_parent_key_label.setVisible(checked)
        self.view.external_parent_key_spin.setVisible(checked)

    def add_ik_link(self):
        """IKリンクを追加"""
        selected = self.maya_adapter.ls(selection=True, type="joint")
        if not selected:
            self.app_state.emit_status(tr_message("select_joint_for_ik_link"))
            return

        bone = selected[0]
        display_name = self._get_bone_display_name(bone)
        self._add_ik_link_row(display_name, False, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0])

    def remove_ik_link(self):
        """選択されたIKリンクを削除"""
        current_row = self.view.ik_links_table.currentRow()
        if current_row >= 0:
            self.view.ik_links_table.removeRow(current_row)

    def move_ik_link(self, direction):
        """IKリンクの順序を変更"""
        current_row = self.view.ik_links_table.currentRow()
        if current_row < 0:
            return

        new_row = current_row + direction
        if new_row < 0 or new_row >= self.view.ik_links_table.rowCount():
            return

        # 行データを保存
        row_data = []
        for col in range(self.view.ik_links_table.columnCount()):
            if col == 1:  # チェックボックス
                widget = self.view.ik_links_table.cellWidget(current_row, col)
                row_data.append(widget.isChecked() if widget else False)
            else:
                item = self.view.ik_links_table.item(current_row, col)
                row_data.append(item.text() if item else "")

        # 行を削除して新しい位置に挿入
        self.view.ik_links_table.removeRow(current_row)
        self.view.ik_links_table.insertRow(new_row)

        # データを復元
        for col, data in enumerate(row_data):
            if col == 1:  # チェックボックス
                check = QCheckBox()
                check.setChecked(data)
                self.view.ik_links_table.setCellWidget(new_row, col, check)
            else:
                self.view.ik_links_table.setItem(new_row, col, QTableWidgetItem(str(data)))

        # 選択を維持
        self.view.ik_links_table.setCurrentCell(new_row, 0)

    def apply_changes(self):
        """Apply selected value edits narrowly; keep structural edits on full route."""
        if not self.current_bone or not self.maya_adapter.object_exists(self.current_bone):
            return

        try:
            root = self._authoring_root()
            if root is None or type(self.current_bone_index) is not int:
                return
            selected_reader = getattr(self.authoring_coordinator, "read_bone_value", None)
            selected_writer = getattr(self.authoring_coordinator, "apply_bone_value_patch", None)
            if callable(selected_reader) and callable(selected_writer):
                existing = selected_reader(root, self.current_bone_index, self.current_bone)
                if not isinstance(existing, MmdBoneSpec):
                    existing = None
            if callable(selected_reader) and callable(selected_writer) and isinstance(existing, MmdBoneSpec):
                ui_flags = self._calculate_bone_flags(existing.flags)
                structural_mask = int(
                    PmxBoneFlag.CONNECT_BONE
                    | PmxBoneFlag.IK
                    | PmxBoneFlag.LOCAL
                    | PmxBoneFlag.GRANT_PARENT_ROTATE
                    | PmxBoneFlag.GRANT_PARENT_MOVE
                    | PmxBoneFlag.EXTERNAL_PARENT_DEFORM
                )
                value_mask = int(
                    PmxBoneFlag.ROTATABLE
                    | PmxBoneFlag.MOVABLE
                    | PmxBoneFlag.DISPLAY
                    | PmxBoneFlag.OPERATABLE
                    | PmxBoneFlag.AXIS_FIXED
                    | PmxBoneFlag.LOCAL_AXIS
                    | PmxBoneFlag.DEFORM_AFTER_PHYSICS
                )
                structural_changed = (
                    (int(existing.flags) ^ int(ui_flags)) & structural_mask
                ) or (
                    (int(existing.flags) ^ int(ui_flags)) & ~(structural_mask | value_mask)
                ) or self._structural_ui_state() != self.bone_data.get("structural")
                if not structural_changed:
                    flags = (int(existing.flags) & ~value_mask) | (int(ui_flags) & value_mask)
                    replacement = replace(
                        existing,
                        name=self.view.bone_name_jp_edit.text(),
                        name_english=self.view.bone_name_en_edit.text(),
                        transform_layer=self.view.deform_layer_spin.value(),
                        flags=flags,
                        fixed_axis=(
                            (
                                self.view.fixed_axis_x_spin.value(),
                                self.view.fixed_axis_y_spin.value(),
                                self.view.fixed_axis_z_spin.value(),
                            )
                            if flags & PmxBoneFlag.AXIS_FIXED
                            else None
                        ),
                        local_axis_x=(
                            (
                                self.view.local_x_axis_x_spin.value(),
                                self.view.local_x_axis_y_spin.value(),
                                self.view.local_x_axis_z_spin.value(),
                            )
                            if flags & PmxBoneFlag.LOCAL_AXIS
                            else None
                        ),
                        local_axis_z=(
                            (
                                self.view.local_z_axis_x_spin.value(),
                                self.view.local_z_axis_y_spin.value(),
                                self.view.local_z_axis_z_spin.value(),
                            )
                            if flags & PmxBoneFlag.LOCAL_AXIS
                            else None
                        ),
                    )
                    result = selected_writer(root, replacement)
                    self._update_selected_row_after_patch(result)
                    logger.info("Applied narrow value changes to bone '%s'", self.current_bone)
                    self.app_state.emit_status(
                        tr_message_format("bone_changes_applied", bone=self.current_bone)
                    )
                    return

            spec = self.authoring_coordinator.read_spec(root)
            existing = next(
                (bone for bone in spec.bones if bone.index == self.current_bone_index),
                None,
            )
            if existing is None or existing.binding_identity != self.current_bone:
                raise ValueError("selected bone is not the current registered binding")

            # Position, connect target, and tail offset are derived/restored by
            # Reset and persisted authoring metadata.  Normal Apply only edits
            # the explicitly semantic UI fields and carries those values over.
            flags = self._calculate_bone_flags(existing.flags)

            grant_enabled = bool(
                flags & (PmxBoneFlag.GRANT_PARENT_ROTATE | PmxBoneFlag.GRANT_PARENT_MOVE)
            )
            grant_parent_index = (
                self._resolve_bone_reference(
                    spec,
                    self.view.grant_parent_edit.text(),
                    "grant parent",
                )
                if grant_enabled
                else None
            )

            ik_target_index = None
            ik_links = ()
            ik_loop_count = 0
            ik_limit_radian = None
            if flags & PmxBoneFlag.IK:
                ik_target_index = self._resolve_bone_reference(
                    spec,
                    self.view.ik_target_edit.text(),
                    "IK target",
                )
                ik_loop_count = self.view.ik_loop_spin.value()
                ik_limit_radian = math.radians(self.view.ik_limit_angle_spin.value())
                links = []
                for row in range(self.view.ik_links_table.rowCount()):
                    bone_item = self.view.ik_links_table.item(row, 0)
                    if bone_item is None:
                        continue
                    limit_widget = self.view.ik_links_table.cellWidget(row, 1)
                    links.append(
                        {
                            "bone": self._resolve_bone_reference(
                                spec,
                                bone_item.text(),
                                f"IK link {row}",
                            ),
                            "limit_enabled": limit_widget.isChecked() if limit_widget else False,
                            "lower_limit": [
                                math.radians(float(self.view.ik_links_table.item(row, col).text()))
                                for col in range(2, 5)
                            ],
                            "upper_limit": [
                                math.radians(float(self.view.ik_links_table.item(row, col).text()))
                                for col in range(5, 8)
                            ],
                        }
                    )
                ik_links = tuple(links)

            replacement = replace(
                existing,
                name=self.view.bone_name_jp_edit.text(),
                name_english=self.view.bone_name_en_edit.text(),
                transform_layer=self.view.deform_layer_spin.value(),
                flags=int(flags),
                connect_bone_index=existing.connect_bone_index,
                tail_offset=existing.tail_offset,
                grant_parent_index=grant_parent_index,
                grant_ratio=self.view.grant_rate_spin.value() if grant_enabled else 0.0,
                grant_local=bool(flags & PmxBoneFlag.LOCAL),
                fixed_axis=(
                    (
                        self.view.fixed_axis_x_spin.value(),
                        self.view.fixed_axis_y_spin.value(),
                        self.view.fixed_axis_z_spin.value(),
                    )
                    if flags & PmxBoneFlag.AXIS_FIXED
                    else None
                ),
                local_axis_x=(
                    (
                        self.view.local_x_axis_x_spin.value(),
                        self.view.local_x_axis_y_spin.value(),
                        self.view.local_x_axis_z_spin.value(),
                    )
                    if flags & PmxBoneFlag.LOCAL_AXIS
                    else None
                ),
                local_axis_z=(
                    (
                        self.view.local_z_axis_x_spin.value(),
                        self.view.local_z_axis_y_spin.value(),
                        self.view.local_z_axis_z_spin.value(),
                    )
                    if flags & PmxBoneFlag.LOCAL_AXIS
                    else None
                ),
                external_parent_key=(
                    self.view.external_parent_key_spin.value()
                    if flags & PmxBoneFlag.EXTERNAL_PARENT_DEFORM
                    else None
                ),
                ik_target_index=ik_target_index,
                ik_loop_count=ik_loop_count,
                ik_limit_radian=ik_limit_radian,
                ik_links=ik_links,
            )
            self.authoring_coordinator.replace_bone_semantic(root, replacement)
            self.load_bones()

            logger.info(f"Applied changes to bone '{self.current_bone}'")
            self.app_state.emit_status(tr_message_format("bone_changes_applied", bone=self.current_bone))

        except Exception as e:
            logger.error(f"Failed to apply bone changes: {e}", exc_info=True)
            self.app_state.emit_status(tr_message_format("bone_changes_failed", error=str(e)))

    def _structural_ui_state(self):
        """Return only controls that require reference/topology resolution."""
        existing_flags = self._get_bone_flags(self.current_bone) if self.current_bone else 0
        flags = self._calculate_bone_flags(existing_flags)
        links = []
        for row in range(self.view.ik_links_table.rowCount()):
            values = []
            for col in range(self.view.ik_links_table.columnCount()):
                if col == 1:
                    widget = self.view.ik_links_table.cellWidget(row, col)
                    values.append(bool(widget.isChecked()) if widget else False)
                else:
                    item = self.view.ik_links_table.item(row, col)
                    values.append(item.text() if item else "")
            links.append(tuple(values))
        return {
            "flags": int(flags)
            & int(
                PmxBoneFlag.CONNECT_BONE
                | PmxBoneFlag.IK
                | PmxBoneFlag.LOCAL
                | PmxBoneFlag.GRANT_PARENT_ROTATE
                | PmxBoneFlag.GRANT_PARENT_MOVE
                | PmxBoneFlag.EXTERNAL_PARENT_DEFORM
            ),
            "grant_parent": self.view.grant_parent_edit.text(),
            "grant_rate": self.view.grant_rate_spin.value(),
            "ik_target": self.view.ik_target_edit.text(),
            "ik_loop": self.view.ik_loop_spin.value(),
            "ik_limit": self.view.ik_limit_angle_spin.value(),
            "ik_links": tuple(links),
            "external_parent_key": self.view.external_parent_key_spin.value(),
            "parent": self.view.parent_bone_edit.text(),
        }

    def _update_selected_row_after_patch(self, result):
        """Refresh only the selected row/state after a narrow value patch."""
        if isinstance(result, MmdBoneSpec):
            bone = result
        else:
            bone = None
        item = self.bone_list_items.get(self.current_bone)
        if item is not None and bone is not None:
            item.setText(
                format_indexed_node_label(
                    bone.index, bone.name, self.current_bone, bone.name_english
                )
            )
        self._registered_indices[self.current_bone] = self.current_bone_index
        self._store_bone_data()

    def _resolve_bone_reference(self, spec, display_name, field):
        """Resolve one UI label to a unique registered semantic bone index."""
        matches = []
        for bone in spec.bones:
            binding = bone.binding_identity
            labels = {bone.name, bone.name_english}
            if binding:
                labels.update((binding, self._get_bone_display_name(binding)))
            if display_name in labels:
                matches.append(bone.index)
        if len(matches) != 1:
            raise ValueError(f"{field} must identify exactly one registered bone")
        return matches[0]

    def reset_changes(self):
        """変更をリセット"""
        if self.current_bone and self.bone_data:
            self.load_bone_properties()
            self.app_state.emit_status(tr_message("changes_reset"))

    def _calculate_bone_flags(self, existing_flags=0):
        """UIの状態からボーンフラグを計算"""
        # CONNECT_BONE is persisted PMX tail semantics, not a normal-form
        # toggle. Preserve it from the loaded spec while rebuilding the
        # remaining editable flags from visible controls.
        flags = int(existing_flags) & int(PmxBoneFlag.CONNECT_BONE)

        # 基本フラグ
        if self.view.rotatable_check.isChecked():
            flags |= PmxBoneFlag.ROTATABLE
        if self.view.movable_check.isChecked():
            flags |= PmxBoneFlag.MOVABLE
        if self.view.visible_check.isChecked():
            flags |= PmxBoneFlag.DISPLAY
        if self.view.enabled_check.isChecked():
            flags |= PmxBoneFlag.OPERATABLE

        # IK
        if self.view.ik_enabled_check.isChecked():
            flags |= PmxBoneFlag.IK

        # 付与
        if self.view.local_grant_check.isChecked():
            flags |= PmxBoneFlag.LOCAL
        if self.view.rotation_grant_check.isChecked():
            flags |= PmxBoneFlag.GRANT_PARENT_ROTATE
        if self.view.move_grant_check.isChecked():
            flags |= PmxBoneFlag.GRANT_PARENT_MOVE

        # 軸制限
        if self.view.fixed_axis_check.isChecked():
            flags |= PmxBoneFlag.AXIS_FIXED
        if self.view.local_axis_check.isChecked():
            flags |= PmxBoneFlag.LOCAL_AXIS

        # 特殊
        if self.view.after_physics_check.isChecked():
            flags |= PmxBoneFlag.DEFORM_AFTER_PHYSICS
        if self.view.external_parent_check.isChecked():
            flags |= PmxBoneFlag.EXTERNAL_PARENT_DEFORM

        return flags

    def _gather_all_settings(self):
        """全ての設定を収集"""
        # 現在のUI状態を辞書として返す
        return {
            # ここに全ての設定を追加（必要に応じて）
        }

    def _get_bone_display_name(self, bone_name):
        """ボーンの表示名を取得（Maya名:日本語名）"""
        if not bone_name or not object_exists(bone_name):
            return bone_name or ""

        # MMD日本語名を取得
        name_jp = get_attribute(bone_name, ATTR_MMD_BONE_NAME)
        if name_jp and name_jp != bone_name:
            return f"{bone_name}:{name_jp}"
        return bone_name

    def _extract_bone_name(self, display_name):
        """表示名から実際のボーン名を抽出"""
        if not display_name:
            return ""

        # "Maya名:日本語名"形式の場合、Maya名を抽出
        if ":" in display_name:
            return display_name.split(":")[0]
        return display_name

    def _get_attr_safe(self, node, attr, default):
        """属性を安全に取得"""
        try:
            if self.maya_adapter.attribute_exists(attr, node):
                value = get_attribute(node, attr)
                # IKリンクの場合、JSON文字列をパース
                if attr == ATTR_MMD_IK_LINKS and isinstance(value, str):
                    import json

                    try:
                        parsed_value = json.loads(value)
                        logger.debug(f"Successfully parsed JSON for {attr}: {parsed_value}")
                        return parsed_value
                    except Exception as e:
                        logger.warning(f"Failed to parse JSON for {attr}: {e}, value: {value}")
                        return default
                return value if value is not None else default
        except Exception as e:
            logger.debug(f"Failed to get attribute {attr} from {node}: {e}")
            pass
        return default

    def _ensure_mmd_attributes(self, joint):
        """MMD用カスタム属性が存在することを確認"""
        attrs = [
            (ATTR_MMD_BONE_NAME, "string", ""),
            (ATTR_MMD_BONE_NAME_EN, "string", ""),
            (
                ATTR_MMD_BONE_FLAGS,
                "long",
                int(PmxBoneFlag.ROTATABLE | PmxBoneFlag.DISPLAY),
            ),
            (ATTR_MMD_DEFORM_LAYER, "long", 0),
            (ATTR_MMD_BONE_OFFSET, "double3", None),
            (ATTR_MMD_CONNECTION_BONE, "string", ""),
            (ATTR_MMD_IK_TARGET, "string", ""),
            (ATTR_MMD_IK_LOOP, "long", 10),
            (ATTR_MMD_IK_LIMIT_ANGLE, "double", 2.0),
            (ATTR_MMD_IK_LINKS, "string", "[]"),  # JSON文字列として保存
            (ATTR_MMD_GRANT_PARENT, "string", ""),
            (ATTR_MMD_GRANT_RATE, "double", 1.0),
            (ATTR_MMD_FIXED_AXIS, "double3", None),
            (ATTR_MMD_LOCAL_X_AXIS, "double3", None),
            (ATTR_MMD_LOCAL_Z_AXIS, "double3", None),
            (ATTR_MMD_EXTERNAL_PARENT_KEY, "long", -1),
        ]

        for attr_name, attr_type, default in attrs:
            if not self.maya_adapter.attribute_exists(attr_name, joint):
                if attr_type == "double3":
                    # double3アトリビュートを作成
                    defaults = {
                        ATTR_MMD_BONE_OFFSET: [0.0, -1.0, 0.0],
                        ATTR_MMD_FIXED_AXIS: [0.0, 0.0, 1.0],
                        ATTR_MMD_LOCAL_X_AXIS: [1.0, 0.0, 0.0],
                        ATTR_MMD_LOCAL_Z_AXIS: [0.0, 0.0, 1.0],
                    }
                    default_value = defaults.get(attr_name, [0.0, 0.0, 0.0])
                    set_custom_attributes(joint, {attr_name: default_value})
                else:
                    # その他のアトリビュートを作成
                    if default is not None:
                        set_custom_attributes(joint, {attr_name: default})
                    else:
                        # デフォルト値がない場合はタイプに応じて初期値を設定
                        if attr_type == "string":
                            set_custom_attributes(joint, {attr_name: ""})
                        elif attr_type == "double":
                            set_custom_attributes(joint, {attr_name: 0.0})
                        elif attr_type == "long":
                            set_custom_attributes(joint, {attr_name: 0})
                        elif attr_type == "bool":
                            set_custom_attributes(joint, {attr_name: False})
