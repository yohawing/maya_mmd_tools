"""VMDファイルをMayaアニメーションに変換するモジュール

このモジュールは、MikuMikuDance (MMD)のモーションデータファイル（VMD）を
Mayaのアニメーションデータに変換する機能を提供します。

Phase 1 以降:
- mmd-anim runtime を利用した高精度ベイク（Beziér補間、付与変形、IK を runtime で解決）
- レガシーパス（従来の変換）との共存と自動フォールバック
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


import maya.api.OpenMaya as om
import maya.cmds as cmds

from ..core.logger import get_logger
from ..core.native.native_pmx_parser import parse_pmx_native
from ..core.settings import settings
from ..core import settings_keys as setting_keys
from ..core.vmd_data import VmdData
from .vmd_append_decomposition import (
    collect_append_info,
    decompose_append_own_rotation,
    decompose_append_own_translation,
    decompose_append_rotations_for_scene,
    decompose_append_translations_for_scene,
)
from .vmd_anim_layer import add_existing_attrs_to_anim_layer, add_transform_attrs_to_anim_layer
from .vmd_bezier_tangent import apply_vmd_bezier_tangents
from .vmd_bone_animation import convert_bone_animation, set_bone_keyframes
from .vmd_bone_interpolation import (
    get_frame_number,
    parse_vmd_interpolation,
    vmd_interp_channel_for_attr,
)
from .vmd_camera_animation import (
    convert_camera_animation,
    get_or_create_camera,
    parse_vmd_camera_interpolation,
)
from .vmd_context import (
    VmdBoneAnimationContext,
    VmdCameraAnimationContext,
    VmdImportContext,
    VmdImportStateContext,
    VmdIkEnabledAnimationContext,
    VmdKeyingContext,
    VmdLightAnimationContext,
    VmdMorphAnimationContext,
    VmdNameMappingContext,
    VmdRuntimeCacheCollectContext,
    VmdRuntimeLocalDecomposeContext,
    VmdRuntimeRigContext,
    VmdRuntimeSceneApplyContext,
    VmdTimelineContext,
)
from .vmd_import_state import (
    capture_anim_layer_selection,
    clear_existing_camera_motion,
    clear_existing_light_motion,
    clear_existing_motion,
    record_bind_poses,
    restore_anim_layer_selection,
    restore_import_scene_updates,
    restore_import_timeline_state,
    suspend_import_scene_updates,
)
from .vmd_ik_enabled_animation import apply_ik_enabled_animation, collect_ik_nodes_by_bone_name, node_namespace
from .vmd_ik_passthrough import collect_mmd_ik_passthrough_info, key_mmd_ik_passthrough_rotation
from .vmd_joint_rotation import (
    convert_vmd_quat_to_joint_rotate,
    get_joint_orient_cache,
)
from .vmd_legacy_bone_routes import (
    build_legacy_bone_key_routes,
    collect_ik_link_joints,
)
from .vmd_light_animation import convert_light_animation, get_or_create_light
from .vmd_motion_kind import detect_vmd_motion_kind
from .vmd_morph_animation import convert_morph_animation
from .vmd_morph_mapping import (
    build_morph_mappings,
    iter_morph_mappings,
)
from .vmd_name_mapping import build_name_mappings
from .vmd_runtime_rig_helper import (
    disable_mmd_rig_constraints_for_runtime_bake,
    has_live_mmd_rig_for_runtime_target,
    restore_joints_to_bind_pose_for_runtime_bake,
)
from .vmd_runtime_channels import (
    append_bone_locals_to_channel_arrays,
    create_runtime_joint_channel_arrays,
    create_runtime_joint_channel_static_state,
    runtime_joint_attrs,
)
from .vmd_runtime_cache_apply import is_static_channel, scale_motion_translate_from_bind
from .vmd_runtime_cache_collect import collect_runtime_bake_cache
from .vmd_runtime_local_decompose import (
    build_bone_hierarchy_and_order_maps,
    build_runtime_bind_world_maps,
    compute_all_bone_locals,
    compute_all_bone_locals_native,
    compute_native_local_channel_batch,
    get_native_local_decompose_static_inputs,
)
from .vmd_runtime_morph_bake import bake_morph_weight_cache_from_runtime, bake_morph_weights_from_runtime
from .vmd_runtime_sampling import (
    iter_runtime_bake_frame_samples,
    iter_runtime_bake_frames,
    native_local_channel_batch_for_frame,
    runtime_batch_morph_weights_for_frame,
    runtime_batch_world_matrices_for_frame,
)
from .vmd_runtime_scene_apply import (
    apply_runtime_channel_arrays_to_scene,
    apply_runtime_channel_arrays_to_scene_with_undo_disabled,
)
from .vmd_runtime_sources import (
    resolve_runtime_bake_sources,
    resolve_runtime_pmx_bytes_and_morph_names,
    should_use_mmd_runtime_bake,
)
from .vmd_runtime_world_bake import bake_bone_poses_from_world_matrices, convert_mmd_world_matrix_to_maya
from .vmd_scene_keying import (
    batch_create_and_key_curve_arrays,
    batch_create_and_key_curves,
    batch_key_scalar_channels,
    samples_as_anim_layer_deltas,
)
from .vmd_timeline import get_animation_frame_range, setup_timeline
from . import vmd_profile

# mmd-anim runtime (Phase 1+)
try:
    from ..core.native.mmd_anim_runtime import (
        is_mmd_runtime_available,
        is_native_physics_available,
        MmdRuntimeModel,
        MmdRuntimeClip,
        MmdRuntimeInstance,
        MmdRuntimePhysicsWorld,
        get_runtime_feature_flags,
    )
    from ..core.native.mmd_anim_runtime_local_channels import (
        compute_maya_local_channels,
        compute_maya_local_channels_batch,
    )
    HAS_MMD_RUNTIME = True
except Exception:
    HAS_MMD_RUNTIME = False
    def is_mmd_runtime_available():
        return False

    def is_native_physics_available():
        return False

    def get_runtime_feature_flags():
        return 0

    MmdRuntimeModel = MmdRuntimeClip = MmdRuntimeInstance = MmdRuntimePhysicsWorld = None  # type: ignore
    compute_maya_local_channels = None  # type: ignore
    compute_maya_local_channels_batch = None  # type: ignore


class VmdConverter:
    """VMDデータをMayaアニメーションに変換するクラス

    VMDファイルに含まれるボーンアニメーションとモーフアニメーションを
    Mayaのジョイントアニメーションとブレンドシェイプアニメーションに変換します。
    アニメーションレイヤーを使用して、複数のモーションを加算的に適用できます。
    """

    def __init__(self):
        """VmdConverterの初期化"""
        self.logger = get_logger(__name__)
        self.bone_name_mapping: Dict[str, str] = {}  # VMDボーン名 -> Mayaジョイント名
        self.morph_name_mapping: Dict[str, Union[Tuple[str, str, str], List[Tuple[str, str, str]]]] = {}
        # VMDモーフ名 -> [(node, attr, 元名), ...]
        # 単一 mapping の既存コード互換を保つため、値は list / tuple のいずれも許容。
        self.fps = 30.0  # デフォルトのFPS (VMD import setting)
        self.motion_scale = float(settings.get(setting_keys.IMPORT_ANIMATION_MOTION_SCALE, 1.0))
        self._failed_bones = set()  # 変換に失敗したボーン名を記録
        self._bone_bind_poses: Dict[str, Tuple[float, float, float]] = {}  # ボーンの初期位置
        self.use_quaternion_interpolation = True  # Quaternion補間の使用フラグ
        self.anim_layer = None  # 現在のアニメーションレイヤー名
        self.use_animation_layers = True  # アニメーションレイヤーの使用フラグ
        self.import_camera_animation = True
        self.import_light_animation = True
        self._vmd_import_refresh_suspended = False
        self._current_import_live_rig_target = False

        # runtime bake: 静的チャンネル判定の閾値。ワールド行列→ローカル分解で乗る
        # 浮動小数ジッタを吸収し、これ未満しか動かないチャンネルはキーを打たず
        # setAttr 一回で固定する（不要な全フレームキーを抑制）。
        # 並進は Maya linear 単位、回転は度で指定（内部比較時にラジアン換算）。
        self._static_eps_translate = float(
            settings.get(setting_keys.IMPORT_ANIMATION_STATIC_CHANNEL_EPSILON_TRANSLATE, 1e-4)
        )
        self._static_eps_rotate = math.radians(
            float(settings.get(setting_keys.IMPORT_ANIMATION_STATIC_CHANNEL_EPSILON_ROTATE_DEG, 0.01))
        )

    def _keying_context(self) -> VmdKeyingContext:
        """Return keying-only state for split VMD helper modules."""
        return VmdKeyingContext(
            logger=self.logger,
            anim_layer=self.anim_layer,
            use_animation_layers=self.use_animation_layers,
        )

    def _bone_animation_context(self) -> VmdBoneAnimationContext:
        """Return legacy bone-animation state for split VMD helper modules."""
        return VmdBoneAnimationContext(
            logger=self.logger,
            bone_name_mapping=self.bone_name_mapping,
            bone_bind_poses=self._bone_bind_poses,
            failed_bones=self._failed_bones,
            use_animation_layers=self.use_animation_layers,
            anim_layer=self.anim_layer,
            motion_scale=self.motion_scale,
            use_quaternion_interpolation=self.use_quaternion_interpolation,
            set_bone_keyframes=self._set_bone_keyframes,
            build_legacy_bone_key_routes=self._build_legacy_bone_key_routes,
            collect_ik_link_joints=self._collect_ik_link_joints,
            add_objects_to_layer=self._add_objects_to_layer,
            add_attrs_to_anim_layer=self._add_attrs_to_anim_layer,
            vmd_frame_to_maya_time=self.vmd_frame_to_maya_time,
            vmd_interp_channel_for_attr=self._vmd_interp_channel_for_attr,
            convert_vmd_quat_to_joint_rotate=self._convert_vmd_quat_to_joint_rotate,
            samples_as_anim_layer_deltas=self._samples_as_anim_layer_deltas,
            batch_key_scalar_channels=self._batch_key_scalar_channels,
            apply_vmd_bezier_tangents=self._apply_vmd_bezier_tangents,
        )

    def _import_context(
        self,
        vmd_data: VmdData,
        target_namespace: str = None,
        layer_name: str = "VMD_Motion",
        bake_mode: bool = False,
        clear_existing_motion: bool = False,
        vmd_bytes: bytes = None,
        pmx_bytes: bytes = None,
        pmx_path: str = None,
        profile: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[Callable[[int], None]] = None,
        use_native_physics_bake: bool = False,
    ) -> VmdImportContext:
        """Return import-run state for convert() dispatch and split helpers."""
        return VmdImportContext(
            vmd_data=vmd_data,
            target_namespace=target_namespace,
            layer_name=layer_name,
            bake_mode=bool(bake_mode),
            clear_existing_motion=bool(clear_existing_motion),
            vmd_bytes=vmd_bytes,
            pmx_bytes=pmx_bytes,
            pmx_path=pmx_path,
            profile=profile,
            progress_callback=progress_callback,
            import_camera_animation=bool(self.import_camera_animation),
            import_light_animation=bool(self.import_light_animation),
            use_native_physics_bake=bool(use_native_physics_bake),
        )

    def _runtime_local_decompose_context(self) -> VmdRuntimeLocalDecomposeContext:
        """Return runtime local-channel decomposition state for split helpers."""
        for attr_name in (
            "bone_index_to_joint",
            "bone_name_to_index",
            "_bone_parent_map",
            "_bone_rotate_orders",
            "_runtime_bind_world_matrices",
            "_runtime_no_orient_bind_world_matrices",
        ):
            if not hasattr(self, attr_name):
                setattr(self, attr_name, {})

        return VmdRuntimeLocalDecomposeContext(
            logger=self.logger,
            bone_index_to_joint=self.bone_index_to_joint,
            bone_name_to_index=self.bone_name_to_index,
            bone_bind_poses=self._bone_bind_poses,
            bone_parent_map=self._bone_parent_map,
            bone_rotate_orders=self._bone_rotate_orders,
            runtime_bind_world_matrices=self._runtime_bind_world_matrices,
            runtime_no_orient_bind_world_matrices=self._runtime_no_orient_bind_world_matrices,
            native_local_decompose_cache={
                "inputs": getattr(self, "_native_local_decompose_inputs", None),
            },
            convert_mmd_world_matrix_to_maya=self._convert_mmd_world_matrix_to_maya,
            get_joint_orient_cache=self._get_joint_orient_cache,
        )

    def _sync_runtime_local_decompose_context(self, context: VmdRuntimeLocalDecomposeContext) -> None:
        """Keep legacy converter cache attributes visible to existing tests/callers."""
        self._native_local_decompose_inputs = context.native_local_decompose_cache.get("inputs")

    def _runtime_rig_context(self) -> VmdRuntimeRigContext:
        """Return runtime-rig-only state for split VMD helper modules."""
        return VmdRuntimeRigContext(
            logger=self.logger,
            bone_name_mapping=self.bone_name_mapping,
            bone_bind_poses=self._bone_bind_poses,
            runtime_joint_attrs=self._runtime_joint_attrs,
        )

    def _ensure_bone_hierarchy_maps_for_cache_collect(self) -> None:
        """Build bone hierarchy maps when runtime cache collection needs them."""
        if not hasattr(self, "_bone_parent_map") or len(getattr(self, "_bone_parent_map", {})) == 0:
            self._build_bone_hierarchy_and_order_maps()

    def _runtime_cache_collect_context(self) -> VmdRuntimeCacheCollectContext:
        """Return runtime cache collection state for split VMD helper modules."""
        return VmdRuntimeCacheCollectContext(
            logger=self.logger,
            bone_index_to_joint=getattr(self, "bone_index_to_joint", {}),
            outer_refresh_suspended=bool(getattr(self, "_vmd_import_refresh_suspended", False)),
            get_anim_layer=lambda: self.anim_layer,
            set_anim_layer=lambda value: setattr(self, "anim_layer", value),
            create_runtime_joint_channel_arrays=self._create_runtime_joint_channel_arrays,
            create_runtime_joint_channel_static_state=self._create_runtime_joint_channel_static_state,
            compute_native_local_channel_batch=self._compute_native_local_channel_batch,
            runtime_batch_morph_weights_for_frame=self._runtime_batch_morph_weights_for_frame,
            ensure_bone_hierarchy_maps=self._ensure_bone_hierarchy_maps_for_cache_collect,
            native_local_channel_batch_for_frame=self._native_local_channel_batch_for_frame,
            runtime_batch_world_matrices_for_frame=self._runtime_batch_world_matrices_for_frame,
            compute_all_bone_locals=self._compute_all_bone_locals,
            append_bone_locals_to_channel_arrays=self._append_bone_locals_to_channel_arrays,
        )

    def _runtime_scene_apply_context(self) -> VmdRuntimeSceneApplyContext:
        """Return runtime scene-apply state for split VMD helper modules."""
        return VmdRuntimeSceneApplyContext(
            logger=self.logger,
            outer_refresh_suspended=bool(getattr(self, "_vmd_import_refresh_suspended", False)),
            collect_append_info=self._collect_append_info,
            collect_mmd_ik_passthrough_info=self._collect_mmd_ik_passthrough_info,
            decompose_append_rotations_for_scene=self._decompose_append_rotations_for_scene,
            decompose_append_translations_for_scene=self._decompose_append_translations_for_scene,
            key_mmd_ik_passthrough_rotation=self._key_mmd_ik_passthrough_rotation,
            batch_create_and_key_curve_arrays=self._batch_create_and_key_curve_arrays,
            bake_morph_weight_cache_from_runtime=self._bake_morph_weight_cache_from_runtime,
        )

    def _camera_animation_context(self) -> VmdCameraAnimationContext:
        """Return camera-animation state for split VMD helper modules."""
        return VmdCameraAnimationContext(
            motion_scale=self.motion_scale,
            anim_layer=self.anim_layer,
            use_animation_layers=self.use_animation_layers,
            get_or_create_camera=self._get_or_create_camera,
            vmd_frame_to_maya_time=self.vmd_frame_to_maya_time,
            maya_time_to_vmd_frame=self.maya_time_to_vmd_frame,
            add_attrs_to_anim_layer=self._add_attrs_to_anim_layer,
            samples_as_anim_layer_deltas=self._samples_as_anim_layer_deltas,
            batch_key_scalar_channels=self._batch_key_scalar_channels,
            apply_vmd_bezier_tangents=self._apply_vmd_bezier_tangents,
            get_frame_number=self._get_frame_number,
        )

    def _timeline_context(self) -> VmdTimelineContext:
        """Return timeline state for split VMD helper modules."""
        return VmdTimelineContext(
            logger=self.logger,
            fps=self.fps,
            vmd_frame_to_maya_time=self.vmd_frame_to_maya_time,
        )

    def _ik_enabled_animation_context(self) -> VmdIkEnabledAnimationContext:
        """Return IK enabled-state keying state for split VMD helper modules."""
        return VmdIkEnabledAnimationContext(
            logger=self.logger,
            collect_ik_nodes_by_bone_name=self._collect_ik_nodes_by_bone_name,
            get_animation_frame_range=self._get_animation_frame_range,
            vmd_frame_to_maya_time=self.vmd_frame_to_maya_time,
        )

    def _name_mapping_context(self) -> VmdNameMappingContext:
        """Return mutable scene name-mapping state for split VMD helper modules."""
        if not hasattr(self, "bone_name_to_index"):
            self.bone_name_to_index = {}
        if not hasattr(self, "bone_index_to_joint"):
            self.bone_index_to_joint = {}
        return VmdNameMappingContext(
            logger=self.logger,
            bone_name_mapping=self.bone_name_mapping,
            bone_name_to_index=self.bone_name_to_index,
            bone_index_to_joint=self.bone_index_to_joint,
            build_morph_mappings=self._build_morph_mappings,
        )

    def _import_state_context(self) -> VmdImportStateContext:
        """Return import cleanup state for split VMD helper modules."""
        return VmdImportStateContext(
            logger=self.logger,
            bone_name_mapping=self.bone_name_mapping,
            bone_bind_poses=self._bone_bind_poses,
            morph_name_mapping=self.morph_name_mapping,
            collect_append_info=self._collect_append_info,
            iter_morph_mappings=self._iter_morph_mappings,
            set_refresh_suspended=self._set_vmd_import_refresh_suspended,
        )

    def _set_vmd_import_refresh_suspended(self, value: bool) -> None:
        self._vmd_import_refresh_suspended = bool(value)

    def _light_animation_context(self) -> VmdLightAnimationContext:
        """Return light-animation state for split VMD helper modules."""
        return VmdLightAnimationContext(
            logger=self.logger,
            anim_layer=self.anim_layer,
            use_animation_layers=self.use_animation_layers,
            get_or_create_light=self._get_or_create_light,
            vmd_frame_to_maya_time=self.vmd_frame_to_maya_time,
            maya_time_to_vmd_frame=self.maya_time_to_vmd_frame,
            add_attrs_to_anim_layer=self._add_attrs_to_anim_layer,
            samples_as_anim_layer_deltas=self._samples_as_anim_layer_deltas,
            batch_key_scalar_channels=self._batch_key_scalar_channels,
        )

    def _morph_animation_context(self) -> VmdMorphAnimationContext:
        """Return morph-animation state for split VMD helper modules."""
        return VmdMorphAnimationContext(
            logger=self.logger,
            morph_name_mapping=self.morph_name_mapping,
            anim_layer=self.anim_layer,
            use_animation_layers=self.use_animation_layers,
            iter_morph_mappings=self._iter_morph_mappings,
            vmd_frame_to_maya_time=self.vmd_frame_to_maya_time,
            samples_as_anim_layer_deltas=self._samples_as_anim_layer_deltas,
            batch_key_scalar_channels=self._batch_key_scalar_channels,
        )

    def convert(
        self,
        vmd_data: VmdData,
        target_namespace: str = None,
        layer_name: str = "VMD_Motion",
        bake_mode: bool = False,
        clear_existing_motion: bool = False,
        vmd_bytes: bytes = None,
        pmx_bytes: bytes = None,
        pmx_path: str = None,
        profile: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[Callable[[int], None]] = None,
        use_native_physics_bake: bool = False,
    ) -> bool:
        """VMDデータをMayaアニメーションに変換

        mmd-anim runtime が利用可能で、vmd_bytes + pmx_bytes (または pmx_path) が
        提供されている場合、高精度ベイクパス（mmd-anim による Bezier / 付与 / IK 解決済みポーズ）
        を使用します。

        Args:
            vmd_data: パース済みのVMDデータ
            target_namespace: 対象となるネームスペース（省略可）
            layer_name: アニメーションレイヤー名
            bake_mode: True の場合は live rig ではなく runtime final-pose bake を優先する
            clear_existing_motion: True の場合は既存の VMD motion keys/layer を削除してから読み込む
            vmd_bytes: 生の VMD バイナリ（runtime bake で使用）
            pmx_bytes: 生の PMX バイナリ（runtime bake で使用）
            pmx_path: PMX ファイルパス（pmx_bytes がない場合に読み込みに使用）
            profile: import action へ返す診断を書き込む mutable dict
            progress_callback: フェーズ進捗通知コールバック
            use_native_physics_bake: True かつ bake_mode のとき native physics bake を試行する
                （デフォルト OFF。feature 不足や失敗時は既存 runtime batch へ fallback）

        Returns:
            変換が成功した場合True、失敗した場合False
        """
        import_start_time = None
        anim_layer_selection = None
        undo_was_enabled = True
        refresh_suspended = False
        import_context = self._import_context(
            vmd_data=vmd_data,
            target_namespace=target_namespace,
            layer_name=layer_name,
            bake_mode=bake_mode,
            clear_existing_motion=clear_existing_motion,
            vmd_bytes=vmd_bytes,
            pmx_bytes=pmx_bytes,
            pmx_path=pmx_path,
            profile=profile,
            progress_callback=progress_callback,
            use_native_physics_bake=use_native_physics_bake,
        )

        def _emit_progress(value: int) -> None:
            if import_context.progress_callback is not None:
                try:
                    import_context.progress_callback(value)
                except Exception:
                    self.logger.debug("Progress callback failed", exc_info=True)

        try:
            self.logger.info("Starting VMD animation conversion")
            undo_was_enabled, refresh_suspended = self._suspend_import_scene_updates()
            try:
                import_start_time = cmds.currentTime(query=True)
                cmds.play(state=False)
            except Exception:
                import_start_time = None
            if self.use_animation_layers:
                anim_layer_selection = self._capture_anim_layer_selection()

            # 名前マッピングの構築（ボーン名 → Maya joint）
            with vmd_profile.scope("name_mapping_build"):
                self._build_name_mappings(import_context.target_namespace)
            _emit_progress(40)
            motion_kind = self._detect_vmd_motion_kind(import_context.vmd_data)
            if import_context.clear_existing_motion and motion_kind in {"model", "mixed"}:
                self._clear_existing_motion(import_context.layer_name, import_context.target_namespace)

            # ボーンの初期位置を記録
            self._record_bind_poses()
            self.logger.debug(f"Detected VMD motion kind: {motion_kind}")

            # タイムライン設定
            with vmd_profile.scope("timeline_setup"):
                self._setup_timeline(import_context.vmd_data)
            _emit_progress(48)

            # Runtime final-pose bake writes absolute joint channels.  Additive
            # animation layers can turn held rotation samples back into base
            # values after the last VMD key, so bake mode keys the base attrs.
            use_animation_layers_for_import = self.use_animation_layers and not import_context.bake_mode

            # アニメーションレイヤーの作成
            if use_animation_layers_for_import:
                with vmd_profile.scope("anim_layer_create"):
                    self.anim_layer = cmds.animLayer(import_context.layer_name, override=False, weight=1.0)
            else:
                self.anim_layer = None

            live_rig_target = self._has_live_mmd_rig_for_runtime_target()
            self._current_import_live_rig_target = live_rig_target
            if live_rig_target:
                self._build_bone_hierarchy_and_order_maps()
                self._build_runtime_bind_world_maps()
            vmd_bytes, pmx_bytes, pmx_path = self._resolve_runtime_bake_sources(
                import_context.vmd_data,
                import_context.vmd_bytes,
                import_context.pmx_bytes,
                import_context.pmx_path,
                import_context.target_namespace,
            )
            _emit_progress(55)

            runtime_success = False
            if self._should_use_mmd_runtime_bake(
                vmd_bytes,
                pmx_bytes,
                pmx_path,
                live_rig_target,
                import_context.bake_mode,
            ):
                self.logger.info("Converting with mmd-anim runtime high-precision bake path")
                # Native physics bake is opt-in and only active when both bake_mode
                # and use_native_physics_bake are True; otherwise existing path.
                runtime_success = self._convert_using_mmd_runtime(
                    vmd_data=import_context.vmd_data,
                    vmd_bytes=vmd_bytes,
                    pmx_bytes=pmx_bytes,
                    pmx_path=pmx_path,
                    use_native_physics_bake=bool(
                        import_context.bake_mode and import_context.use_native_physics_bake
                    ),
                    profile=import_context.profile,
                )
                if runtime_success:
                    self.logger.info("mmd-anim runtime high-precision bake completed")
                    _emit_progress(82)
                else:
                    self.logger.warning("Runtime bake failed; falling back to legacy path")
                    self._record_profile_warning(
                        import_context.profile,
                        {
                            "source": "vmd_converter",
                            "code": "runtime_bake_failed_fallback",
                            "severity": "warning",
                            "message": "mmd-anim runtime bake failed; falling back to legacy VMD conversion",
                            "fallback": "legacy",
                            "bake_mode": bool(import_context.bake_mode),
                            "live_rig_target": bool(live_rig_target),
                            "has_vmd_bytes": bool(vmd_bytes),
                            "has_pmx_bytes": bool(pmx_bytes),
                            "pmx_path": pmx_path or "",
                        },
                    )

            if not runtime_success:
                # --- レガシーパス（従来の変換） ---
                self._apply_ik_enabled_animation(import_context.vmd_data, import_context.target_namespace)
                _emit_progress(60)

                if hasattr(import_context.vmd_data, "bone_frames") and import_context.vmd_data.bone_frames:
                    self.logger.info(
                        f"Starting bone animation conversion (legacy): {len(import_context.vmd_data.bone_frames)} frames"
                    )
                    with vmd_profile.scope("bone_animation_convert"):
                        bone_success = self._convert_bone_animation(import_context.vmd_data.bone_frames)
                    if not bone_success:
                        self.logger.warning("Some errors occurred during bone animation conversion")
                _emit_progress(72)

                # モーフアニメーション（レガシー）
                if hasattr(import_context.vmd_data, "morph_frames") and import_context.vmd_data.morph_frames:
                    self.logger.debug("Converting morph animation (legacy)")
                    self._convert_morph_animation(import_context.vmd_data.morph_frames)
                _emit_progress(82)

            # カメラアニメーション（レガシー）
            if (
                import_context.import_camera_animation
                and hasattr(import_context.vmd_data, "camera_frames")
                and import_context.vmd_data.camera_frames
            ):
                self.logger.info(f"Converting camera animation: {len(import_context.vmd_data.camera_frames)} frames")
                self._clear_existing_camera_motion()
                camera_sample_bytes = vmd_bytes if import_context.bake_mode else None
                self._convert_camera_animation(import_context.vmd_data.camera_frames, vmd_bytes=camera_sample_bytes)
            _emit_progress(88)

            # ライトアニメーション（レガシー）
            if (
                import_context.import_light_animation
                and hasattr(import_context.vmd_data, "light_frames")
                and import_context.vmd_data.light_frames
            ):
                self.logger.info(f"Converting light animation: {len(import_context.vmd_data.light_frames)} frames")
                self._clear_existing_light_motion()
                light_sample_bytes = vmd_bytes if import_context.bake_mode else None
                self._convert_light_animation(import_context.vmd_data.light_frames, vmd_bytes=light_sample_bytes)
            _emit_progress(94)

            self.logger.info("VMD animation conversion completed")
            self._restore_import_timeline_state(import_start_time)
            return True

        except Exception as e:
            self._restore_import_timeline_state(import_start_time)
            self.logger.error(f"Error occurred during VMD animation conversion: {str(e)}", exc_info=True)
            return False
        finally:
            self._current_import_live_rig_target = False
            self._restore_anim_layer_selection(anim_layer_selection)
            self._restore_import_scene_updates(undo_was_enabled, refresh_suspended)

    def _suspend_import_scene_updates(self) -> Tuple[bool, bool]:
        """Suppress Maya undo recording and viewport refresh during VMD import."""
        return suspend_import_scene_updates(self._import_state_context())

    def _restore_import_scene_updates(self, undo_was_enabled: bool, refresh_suspended: bool) -> None:
        """Restore viewport refresh and undo state after VMD import."""
        restore_import_scene_updates(self._import_state_context(), undo_was_enabled, refresh_suspended)

    @staticmethod
    def _restore_import_timeline_state(current_time: Optional[float]) -> None:
        """Keep VMD import from leaving Maya visibly playing or scrubbed ahead."""
        restore_import_timeline_state(current_time)

    @staticmethod
    def _record_profile_warning(profile: Optional[Dict[str, Any]], warning: Dict[str, Any]) -> None:
        """Append a structured converter warning to the import profile."""
        if not isinstance(profile, dict):
            return
        converter_profile = profile.setdefault("vmd_converter", {})
        converter_profile.setdefault("warnings", []).append(dict(warning))

    def vmd_frame_to_maya_time(self, frame_number: float) -> float:
        """Convert VMD's fixed 30fps frame number to the target Maya time unit."""
        return float(frame_number) * (float(self.fps) / 30.0)

    def maya_time_to_vmd_frame(self, maya_time: float) -> float:
        """Convert target Maya output time back to VMD's fixed 30fps frame number."""
        return float(maya_time) * (30.0 / float(self.fps))

    @staticmethod
    def _capture_anim_layer_selection() -> Dict[str, bool]:
        """VMD import 前の animLayer selected 状態を取得する。"""
        return capture_anim_layer_selection()

    @staticmethod
    def _restore_anim_layer_selection(selection: Optional[Dict[str, bool]]) -> None:
        """VMD import 中に変わった animLayer selected 状態を元に戻す。"""
        restore_anim_layer_selection(selection)

    def _clear_existing_motion(self, layer_name: str, target_namespace: Optional[str] = None) -> None:
        """対象モデルに残っている既存 VMD motion keys/layer を削除する。"""
        clear_existing_motion(self._import_state_context(), layer_name, target_namespace)

    def _clear_existing_camera_motion(self) -> None:
        """既存のMMDカメラアニメーションキーを削除する。"""
        clear_existing_camera_motion(self.logger)

    def _clear_existing_light_motion(self) -> None:
        """既存のMMD照明アニメーションキーを削除する。"""
        clear_existing_light_motion(self.logger)

    def _should_use_mmd_runtime_bake(
        self,
        vmd_bytes: bytes,
        pmx_bytes: bytes,
        pmx_path: str,
        live_rig_target: bool = False,
        bake_mode: bool = False,
    ) -> bool:
        """Return True for Bake mode final-pose import, False for live Rig mode."""
        return should_use_mmd_runtime_bake(
            self,
            vmd_bytes,
            pmx_bytes,
            pmx_path,
            HAS_MMD_RUNTIME,
            is_mmd_runtime_available,
            live_rig_target,
            bake_mode,
        )

    def _resolve_runtime_bake_sources(
        self,
        vmd_data: VmdData,
        vmd_bytes: bytes,
        pmx_bytes: bytes,
        pmx_path: str,
        target_namespace: str = None,
    ) -> Tuple[bytes, bytes, str]:
        """明示指定がない runtime bake 入力を VMD/scene metadata から復元する。"""
        return resolve_runtime_bake_sources(self, vmd_data, vmd_bytes, pmx_bytes, pmx_path, target_namespace)

    def _convert_using_mmd_runtime(
        self,
        vmd_data: VmdData,
        vmd_bytes: bytes,
        pmx_bytes: bytes,
        pmx_path: str,
        use_native_physics_bake: bool = False,
        profile: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        mmd-anim runtime を使って全フレームを評価し、正確なポーズをベイクする。
        付与変形・IK・MMDベジェ補間はすべて runtime 側で解決済み。

        ``use_native_physics_bake`` が True のときは、feature flags / world 作成が
        成功しサンプルが一様な場合に限って sequential physics bake を試行し、
        結果は既存の matrix/morph キャッシュ適用経路へ流す。失敗時は物理なし
        runtime batch へ silent fallback する（例外にしない）。
        """
        resolved_pmx_bytes, pmx_morph_names = resolve_runtime_pmx_bytes_and_morph_names(
            pmx_bytes,
            pmx_path,
            self.logger,
            parse_pmx_native,
        )
        if not resolved_pmx_bytes:
            self.logger.error("Could not get PMX data required for runtime bake")
            return False

        # モデル・クリップ・インスタンス作成
        model = MmdRuntimeModel.from_pmx_bytes(resolved_pmx_bytes)
        if model is None:
            self.logger.error("Failed to create MmdRuntimeModel")
            return False

        clip = MmdRuntimeClip.from_vmd_bytes_for_model(model, vmd_bytes)
        if clip is None:
            self.logger.error("Failed to create MmdRuntimeClip")
            model.free()
            return False

        instance = MmdRuntimeInstance.for_model(model)
        if instance is None:
            self.logger.error("Failed to create MmdRuntimeInstance")
            clip.free()
            model.free()
            return False

        physics_world = None
        try:
            runtime_start = time.perf_counter()
            # フレーム範囲の決定。Python VMD parser が空/失敗でも、
            # bake mode は runtime clip の raw bytes から範囲を復元できる。
            min_frame, max_frame = self._get_animation_frame_range(vmd_data)
            if max_frame <= min_frame:
                clip_frame_range = clip.frame_range() if hasattr(clip, "frame_range") else None
                if clip_frame_range is not None:
                    min_frame, max_frame = clip_frame_range
                    max_time = self.vmd_frame_to_maya_time(max_frame)
                    cmds.playbackOptions(min=0, max=max_time, animationStartTime=0, animationEndTime=max_time)
            bake_samples = self._iter_runtime_bake_frame_samples(min_frame, max_frame)
            self.logger.debug(
                f"Runtime evaluation range: {min_frame} - {max_frame} "
                f"(keys={len(bake_samples)}, fps={self.fps:g})"
            )
            self._disable_mmd_rig_constraints_for_runtime_bake()
            physics_preview_connections = self._capture_physics_preview_rotation_connections()
            self._restore_joints_to_bind_pose_for_runtime_bake()
            if self.bone_index_to_joint:
                self._build_runtime_bind_world_maps()

            physics_routing: Dict[str, Any] = {
                "requested": bool(use_native_physics_bake),
                "used": False,
                "feature_flags": int(get_runtime_feature_flags()) if HAS_MMD_RUNTIME else 0,
                "fps": float(self.fps),
            }
            if use_native_physics_bake:
                if not HAS_MMD_RUNTIME or MmdRuntimePhysicsWorld is None:
                    physics_routing["reason"] = "runtime_unavailable"
                    self.logger.warning(
                        "Native physics bake requested but mmd-anim runtime is unavailable; "
                        "falling back to non-physics runtime batch"
                    )
                elif not is_native_physics_available():
                    physics_routing["reason"] = "feature_flags_unavailable"
                    self.logger.warning(
                        "Native physics bake requested but feature flags missing "
                        "(flags=0x%x); falling back to non-physics runtime batch",
                        physics_routing["feature_flags"],
                    )
                else:
                    physics_world = MmdRuntimePhysicsWorld.from_pmx_bytes(resolved_pmx_bytes)
                    if physics_world is None:
                        physics_routing["reason"] = "physics_world_create_failed"
                        self.logger.warning(
                            "Native physics world could not be created from PMX bytes; "
                            "falling back to non-physics runtime batch"
                        )
                    else:
                        physics_routing["world_created"] = True

            # キャッシュ収集: 評価結果を API 配列へ直接保持（cmds.xform / setKeyframe を内側ループから排除）
            # Native physics batch (when used) reuses the same matrix/morph → channel apply path.
            runtime_cache = collect_runtime_bake_cache(
                self._runtime_cache_collect_context(),
                instance,
                clip,
                bake_samples,
                physics_world=physics_world,
                fps=float(self.fps),
                use_native_physics_bake=bool(use_native_physics_bake and physics_world is not None),
            )
            if getattr(runtime_cache, "physics_bake", None):
                physics_routing.update(runtime_cache.physics_bake)
            if isinstance(profile, dict):
                profile.setdefault("vmd_converter", {})["native_physics_bake"] = dict(physics_routing)
            self.logger.debug(
                f"mmd-anim runtime pose evaluation and cache completed "
                f"(frames={len(runtime_cache.baked_frames)}, elapsed={runtime_cache.eval_elapsed:.3f}s, "
                f"physics_used={physics_routing.get('used', False)})"
            )
            self.logger.debug(
                "runtime bake cache timings: "
                f"mode={'batch' if runtime_cache.batch_mode else 'per-frame'}, "
                f"physics={'yes' if physics_routing.get('used') else 'no'}, "
                f"eval_copy={runtime_cache.eval_copy_elapsed:.3f}s, "
                f"batch_unpack={runtime_cache.batch_unpack_elapsed:.3f}s, "
                f"local_decompose={runtime_cache.local_elapsed:.3f}s, "
                f"append={runtime_cache.append_elapsed:.3f}s"
            )

            # キャッシュから一括でキーフレーム登録（Maya Python API 2.0 優先）
            if runtime_cache.baked_frames:
                apply_start = time.perf_counter()
                apply_runtime_channel_arrays_to_scene_with_undo_disabled(
                    self._runtime_scene_apply_context(),
                    runtime_cache.joint_channel_values,
                    runtime_cache.joint_channel_static,
                    runtime_cache.bake_times,
                    runtime_cache.baked_frames,
                    runtime_cache.morph_cache,
                    pmx_morph_names,
                )
                if not physics_routing.get("used", False):
                    self._restore_physics_preview_rotation_connections(
                        physics_preview_connections
                    )
                apply_elapsed = time.perf_counter() - apply_start
                self.logger.debug(
                    f"Runtime cache key application completed (elapsed={apply_elapsed:.3f}s)"
                )

            runtime_elapsed = time.perf_counter() - runtime_start
            self.logger.debug(f"runtime bake total elapsed={runtime_elapsed:.3f}s")

            return True

        finally:
            # Explicit physics world lifetime: free before instance/clip/model.
            if physics_world is not None:
                try:
                    physics_world.free()
                except Exception:
                    self.logger.debug("physics world free failed", exc_info=True)
            # リソース解放
            instance.free()
            clip.free()
            model.free()

    def _get_animation_frame_range(self, vmd_data: VmdData):
        """VMDデータからアニメーションのフレーム範囲を取得"""
        return get_animation_frame_range(vmd_data)

    def _iter_runtime_bake_frame_samples(self, min_frame: int, max_frame: int) -> List[Tuple[float, float]]:
        """Return (Maya output time, VMD evaluation frame) samples for runtime bake."""
        return iter_runtime_bake_frame_samples(min_frame, max_frame, self.fps)

    def _iter_runtime_bake_frames(self, min_frame: int, max_frame: int) -> List[float]:
        """runtime bakeで評価する VMD フレーム列を返す。"""
        return iter_runtime_bake_frames(min_frame, max_frame, self.fps)

    @staticmethod
    def _runtime_batch_world_matrices_for_frame(batch_result, frame_index: int) -> List[List[float]]:
        """batch 評価の flat buffer から指定フレームの PMX bone world matrices を返す。"""
        return runtime_batch_world_matrices_for_frame(batch_result, frame_index)

    @staticmethod
    def _runtime_batch_morph_weights_for_frame(batch_result, frame_index: int) -> List[float]:
        """batch 評価の flat buffer から指定フレームの PMX morph weights を返す。"""
        return runtime_batch_morph_weights_for_frame(batch_result, frame_index)

    def _build_bone_hierarchy_and_order_maps(self):
        """runtime bake キャッシュ計算用に、ボーン親子関係と rotateOrder を事前収集する。

        Maya ジョイントの DAG 親子とカスタム属性から bone index ベースのマップを構築。
        """
        context = self._runtime_local_decompose_context()
        build_bone_hierarchy_and_order_maps(context)
        self._sync_runtime_local_decompose_context(context)

    def _build_runtime_bind_world_maps(self) -> None:
        """Build bind-space maps used to convert runtime matrices for JO skinning.

        mmd-anim/public no-JO skinning deforms vertices with
        ``inverse(B_noJO) * W_mmd``.  A Maya skeleton with jointOrient uses a
        different bind world matrix, so the joint world matrix must be converted
        to ``B_maya * inverse(B_noJO) * W_mmd`` before local decomposition.
        """
        context = self._runtime_local_decompose_context()
        build_runtime_bind_world_maps(context)
        self._sync_runtime_local_decompose_context(context)

    def _compute_all_bone_locals(
        self, world_matrices: List[List[float]]
    ) -> Dict[int, Tuple[float, float, float, float, float, float]]:
        """runtime から得たワールド行列群から、各ボーンの Maya ローカル姿勢 (translate + rotate deg) を計算。

        親ボーンの変換済みワールド行列の逆行列を掛けてローカル行列を得、ジョイントの
        rotateOrder に適合するオイラー角を抽出する。これにより per-frame の cmds.xform を
        回避しつつ、ベイク結果の等価性を保つ。

        JO 付き skeleton では、runtime world matrix そのものではなく skinning
        matrix が no-JO MMD 評価と一致するよう bind-space 補正を行う。
        """
        context = self._runtime_local_decompose_context()
        try:
            return compute_all_bone_locals(context, world_matrices, compute_maya_local_channels)
        finally:
            self._sync_runtime_local_decompose_context(context)

    def _compute_all_bone_locals_native(
        self,
        world_matrices: List[List[float]],
    ) -> Optional[Dict[int, Tuple[float, float, float, float, float, float]]]:
        """Use mmd-anim FFI to decompose runtime world matrices when available."""
        context = self._runtime_local_decompose_context()
        try:
            return compute_all_bone_locals_native(context, world_matrices, compute_maya_local_channels)
        finally:
            self._sync_runtime_local_decompose_context(context)

    def _compute_native_local_channel_batch(self, batch_result):
        """Compute native local channels for an entire runtime batch when possible."""
        context = self._runtime_local_decompose_context()
        try:
            return compute_native_local_channel_batch(context, batch_result, compute_maya_local_channels_batch)
        finally:
            self._sync_runtime_local_decompose_context(context)

    @staticmethod
    def _native_local_channel_batch_for_frame(
        native_batch,
        frame_index: int,
    ) -> Dict[int, Tuple[float, float, float, float, float, float]]:
        """Extract one frame of local channel tuples from native batch output."""
        return native_local_channel_batch_for_frame(native_batch, frame_index)

    def _get_native_local_decompose_static_inputs(self, ordered_bone_indices: List[int]) -> Optional[Dict[str, list]]:
        """Return cached static inputs for native runtime local decomposition."""
        context = self._runtime_local_decompose_context()
        try:
            return get_native_local_decompose_static_inputs(context, ordered_bone_indices)
        finally:
            self._sync_runtime_local_decompose_context(context)

    def _batch_create_and_key_curves(
        self,
        joint_name: str,
        channel_samples: Dict[str, List[Tuple[float, float]]],
    ) -> bool:
        """Maya Python API 2.0 (MFnAnimCurve + addKeys) でカーブ作成・一括キー挿入を行うヘルパ。

        channel_samples の回転値は rotate animCurve 用の内部角度単位（ラジアン）であること。
        translate は Maya linear unit の値をそのまま渡す。
        API が使えない/失敗したチャンネルは cmds.setKeyframe にフォールバックして等価動作を維持。
        """
        return batch_create_and_key_curves(self._keying_context(), joint_name, channel_samples)

    @staticmethod
    def _runtime_joint_attrs() -> Tuple[str, str, str, str, str, str]:
        """runtime bakeでキー登録するjoint channel一覧を返す。"""
        return runtime_joint_attrs()

    def _create_runtime_joint_channel_arrays(self) -> Dict[str, Dict[str, Optional[om.MDoubleArray]]]:
        """runtime bake用にjoint channelごとの値配列を作成する。"""
        return create_runtime_joint_channel_arrays(self.bone_index_to_joint)

    def _create_runtime_joint_channel_static_state(self) -> Dict[str, Dict[str, dict]]:
        """静的channel判定用の状態を作成する。"""
        return create_runtime_joint_channel_static_state(self.bone_index_to_joint)

    def _append_bone_locals_to_channel_arrays(
        self,
        bone_locals: Dict[int, Tuple[float, float, float, float, float, float]],
        channel_values: Dict[str, Dict[str, Optional[om.MDoubleArray]]],
        static_state: Dict[str, Dict[str, dict]],
    ):
        """frameごとのlocal姿勢をjoint channel配列へ直接追加する。"""
        append_bone_locals_to_channel_arrays(self, bone_locals, channel_values, static_state)

    def _batch_create_and_key_curve_arrays(
        self,
        joint_name: str,
        channel_values: Dict[str, Optional[om.MDoubleArray]],
        static_state: Dict[str, dict],
        times: om.MTimeArray,
        frame_numbers: List[float],
    ) -> Tuple[int, int]:
        """MDoubleArrayへ収集済みのchannel値をMFnAnimCurve.addKeysで一括登録する。"""
        return batch_create_and_key_curve_arrays(
            self._keying_context(),
            joint_name,
            channel_values,
            static_state,
            times,
            frame_numbers,
        )

    def _batch_key_scalar_channels(
        self,
        node_name: str,
        channel_samples: Dict[str, List[Tuple[float, float]]],
        animation_layer: Optional[str] = None,
    ) -> bool:
        """Maya UI 値の scalar channel を MFnAnimCurve.addKeys で一括キーイングする。"""
        return batch_key_scalar_channels(
            self._keying_context(),
            node_name,
            channel_samples,
            animation_layer=animation_layer,
        )

    @staticmethod
    def _samples_as_anim_layer_deltas(node_name: str, channel_samples: Dict[str, List[Tuple[float, float]]]):
        """Convert absolute channel samples to additive animLayer deltas."""
        return samples_as_anim_layer_deltas(node_name, channel_samples)

    @staticmethod
    def _collect_append_info():
        """シーン内の全 mmdAppend ノードから (target_joint, append_node, source_joint, ratio, attr_map) を収集。"""
        return collect_append_info()

    @staticmethod
    def _decompose_append_own_rotation(
        target_rx: om.MDoubleArray,
        target_ry: om.MDoubleArray,
        target_rz: om.MDoubleArray,
        source_rx: om.MDoubleArray,
        source_ry: om.MDoubleArray,
        source_rz: om.MDoubleArray,
        ratio: float,
        target_joint_orient: Optional[om.MQuaternion] = None,
        source_joint_orient: Optional[om.MQuaternion] = None,
        source_rotation_is_mmd: bool = False,
    ):
        """bake の final rotation から grant 寄与を除去し、bone own rotation を計算。"""
        return decompose_append_own_rotation(
            target_rx,
            target_ry,
            target_rz,
            source_rx,
            source_ry,
            source_rz,
            ratio,
            target_joint_orient=target_joint_orient,
            source_joint_orient=source_joint_orient,
            source_rotation_is_mmd=source_rotation_is_mmd,
        )

    def _decompose_append_rotations_for_scene(
        self,
        joint_channel_values: Dict[str, Dict[str, Optional[om.MDoubleArray]]],
        joint_channel_static: Dict[str, Dict[str, dict]],
        append_info: Dict[str, dict],
        n_frames: int,
    ) -> Dict[str, Dict[str, om.MDoubleArray]]:
        """append graph の依存に沿って final rotation を own rotation へ分解する。"""
        return decompose_append_rotations_for_scene(
            self,
            joint_channel_values,
            joint_channel_static,
            append_info,
            n_frames,
        )

    @staticmethod
    def _decompose_append_own_translation(
        target_tx: om.MDoubleArray,
        target_ty: om.MDoubleArray,
        target_tz: om.MDoubleArray,
        source_tx: om.MDoubleArray,
        source_ty: om.MDoubleArray,
        source_tz: om.MDoubleArray,
        ratio: float,
    ):
        """bake の final translation から grant 寄与を除去し、bone own translation を計算。"""
        return decompose_append_own_translation(
            target_tx,
            target_ty,
            target_tz,
            source_tx,
            source_ty,
            source_tz,
            ratio,
        )

    def _decompose_append_translations_for_scene(
        self,
        joint_channel_values: Dict[str, Dict[str, Optional[om.MDoubleArray]]],
        joint_channel_static: Dict[str, Dict[str, dict]],
        append_info: Dict[str, dict],
        n_frames: int,
    ) -> Dict[str, Dict[str, om.MDoubleArray]]:
        """append graph の依存に沿って final translation を own translation へ分解する。"""
        return decompose_append_translations_for_scene(
            self,
            joint_channel_values,
            joint_channel_static,
            append_info,
            n_frames,
        )

    def _apply_runtime_channel_arrays_to_scene(
        self,
        joint_channel_values: Dict[str, Dict[str, Optional[om.MDoubleArray]]],
        joint_channel_static: Dict[str, Dict[str, dict]],
        bake_times: om.MTimeArray,
        baked_frames: List[float],
        morph_cache: List[Tuple[float, list]],
        pmx_morph_names: List[str],
    ) -> None:
        """API配列へ収集済みのruntime bake結果をMaya sceneへ一括適用する。"""
        apply_runtime_channel_arrays_to_scene(
            self._runtime_scene_apply_context(),
            joint_channel_values,
            joint_channel_static,
            bake_times,
            baked_frames,
            morph_cache,
            pmx_morph_names,
        )

    @staticmethod
    def _collect_mmd_ik_passthrough_info() -> Dict[str, Dict[str, Union[str, int]]]:
        """Return joints driven by mmdCcdIk outputRotate and their link indices.

        During runtime-live VMD apply the final pose already includes MMD IK.
        For IK-driven joints, write the final rotation into the IK node input
        and key ``enabled`` off so the existing output connection simply passes
        the keyed rotation through.
        """
        return collect_mmd_ik_passthrough_info()

    def _key_mmd_ik_passthrough_rotation(
        self,
        ik_info: Dict[str, Union[str, int]],
        channels: Dict[str, Optional[om.MDoubleArray]],
        static_state: Dict[str, dict],
        bake_times: om.MTimeArray,
        baked_frames: List[float],
        disable_solver: bool = True,
    ) -> int:
        """Key mmdCcdIk inputRotate/output pass-through for runtime-live apply."""
        return key_mmd_ik_passthrough_rotation(
            self,
            ik_info,
            channels,
            static_state,
            bake_times,
            baked_frames,
            disable_solver,
        )

    def _scale_motion_translate_from_bind(
        self,
        joint: str,
        tx: float,
        ty: float,
        tz: float,
    ) -> Tuple[float, float, float]:
        """Scale a local translate sample as bind pose plus motion delta."""
        return scale_motion_translate_from_bind(self, joint, tx, ty, tz)

    @staticmethod
    def _is_static_channel(samples: List[Tuple[float, float]], tolerance: float = 1e-10) -> bool:
        """全サンプル値が同一なら True を返す。"""
        return is_static_channel(samples, tolerance)

    def _bake_bone_poses_from_world_matrices(
        self, frame: int, world_matrices: list, model_bone_count: int
    ):
        """
        mmd-anim runtime から得たワールド行列 (PMXボーン順) を使って
        Maya ジョイントに正確なポーズをベイクする。

        - bone_index_to_joint から PMX bone index 順 (昇順) で反復し、親ボーン(低index)を子(高index)より先に適用
        - world matrix を Maya 座標系に変換 (Z反転)
        - cmds.xform(ws=True, matrix=...) で目的のワールド姿勢を適用
        - その結果のローカル値をキーフレーム

        index 順にすることで、同一フレーム内の複数ボーン xform(ws) 時に親のワールドが先に確定し、
        子のローカル分解が正しい親基準で行われる。左手捩などツイストボーンの回転再現に重要。
        """
        bake_bone_poses_from_world_matrices(self, frame, world_matrices, model_bone_count)

    @staticmethod
    def _convert_mmd_world_matrix_to_maya(mmd_matrix: list) -> list:
        """
        mmd-anim のワールド行列を Maya の `cmds.xform(..., matrix=...)` 用に変換する。

        mmd-anim の flat matrix は translation を 12, 13, 14 に持つ 16 要素として扱う。
        MMD と Maya の差分は X/Y は同じで Z 方向が反転する座標系変換なので、
        回転 3x3 は S * R * S、translation は t * S を適用する。

        これにより identity は identity のまま保たれ、Z translation だけが反転する。
        """
        return convert_mmd_world_matrix_to_maya(mmd_matrix)

    def _bake_morph_weights_from_runtime(
        self,
        frame: int,
        morph_weights: list,
        pmx_morph_names: List[str] = None,
    ):
        """runtime から得た PMX morph 順のウェイトを Maya blendShape にベイク"""
        bake_morph_weights_from_runtime(self._morph_animation_context(), frame, morph_weights, pmx_morph_names)

    def _bake_morph_weight_cache_from_runtime(
        self,
        morph_cache: List[Tuple[float, list]],
        pmx_morph_names: List[str] = None,
    ) -> None:
        """runtime 評価済み morph weight cache を blendShape/network weight へ一括キーイングする。"""
        bake_morph_weight_cache_from_runtime(self._morph_animation_context(), morph_cache, pmx_morph_names)

    def _disable_mmd_rig_constraints_for_runtime_bake(self):
        """runtime bake と二重評価になる PMX 付与constraint/IK solverを無効化する。"""
        disable_mmd_rig_constraints_for_runtime_bake(self._runtime_rig_context())

    def _has_live_mmd_rig_for_runtime_target(self) -> bool:
        """現在の変換対象にlive MMD rig出力が接続されているかを返す。

        Rig mode は mmdAppend / mmdCcdIk をユーザー操作可能なリグとして残す必要がある。
        runtime bake は final pose を joint に直焼きする Bake mode 用の経路なので、
        対象jointへlive rig出力がある場合は選ばない。
        """
        return has_live_mmd_rig_for_runtime_target(self.logger)

    def _restore_joints_to_bind_pose_for_runtime_bake(self) -> None:
        """live rig出力切断後に残った値を消し、runtime bake用のbind姿勢へ戻す。"""
        restore_joints_to_bind_pose_for_runtime_bake(self._runtime_rig_context())

    def _capture_physics_preview_rotation_connections(self) -> List[Tuple[str, str]]:
        """Remember marked Bullet feedback plugs before runtime bake disconnects them."""
        connections: List[Tuple[str, str]] = []
        for joint in self.bone_index_to_joint.values():
            if not cmds.objExists(joint):
                continue
            for attr in ("rotateX", "rotateY", "rotateZ"):
                destination = f"{joint}.{attr}"
                for source in cmds.listConnections(
                    destination, source=True, destination=False, plugs=True
                ) or []:
                    node = source.split(".", 1)[0]
                    if not cmds.attributeQuery(
                        "mmd_physics_preview_constraint", node=node, exists=True
                    ):
                        continue
                    if cmds.nodeType(node) != "orientConstraint":
                        continue
                    targets = cmds.orientConstraint(node, query=True, targetList=True) or []
                    preserve_mode2 = any(
                        cmds.attributeQuery("mmd_physics_mode", node=target, exists=True)
                        and int(cmds.getAttr(f"{target}.mmd_physics_mode")) == 2
                        for target in targets
                    )
                    if (
                        cmds.getAttr(f"{node}.mmd_physics_preview_constraint")
                        and preserve_mode2
                    ):
                        connections.append((source, destination))
        return connections

    def _restore_physics_preview_rotation_connections(
        self, connections: List[Tuple[str, str]]
    ) -> None:
        """Restore the original bind-offset Bullet feedback after normal runtime bake."""
        for source, destination in connections:
            if not cmds.objExists(source) or not cmds.objExists(destination):
                continue
            try:
                cmds.connectAttr(source, destination, force=True)
            except Exception as exc:
                self.logger.debug(
                    "failed to restore physics preview connection %s -> %s: %s",
                    source,
                    destination,
                    exc,
                )

    def _add_objects_to_layer(self, objects: List[str]):
        """オブジェクトをアニメーションレイヤーに追加

        Args:
            objects: 追加するオブジェクトのリスト
        """
        add_transform_attrs_to_anim_layer(self.anim_layer, objects)

    def _build_name_mappings(self, target_namespace: str = None):
        """ボーン名とモーフ名のマッピングを構築

        Phase 1 拡張: bone_name → joint に加え、
        bone_name → bone_index 、 bone_index → joint も構築する。
        これにより mmd-anim の world_matrices (PMXボーン順) を Maya ジョイントに
        正しく対応づけられる。
        """
        build_name_mappings(self._name_mapping_context(), target_namespace)

    def _record_bind_poses(self):
        """各ボーンの初期位置（バインドポーズ）を記録"""
        record_bind_poses(self._import_state_context())

    def _setup_timeline(self, vmd_data: VmdData):
        """タイムラインの設定

        Args:
            vmd_data: パース済みのVMDデータ
        """
        setup_timeline(self._timeline_context(), vmd_data)

    def _convert_bone_animation(self, bone_frames: List) -> bool:
        """ボーンアニメーションを変換

        Args:
            bone_frames: ボーンフレームデータのリスト

        Returns:
            変換が成功した場合True
        """
        return convert_bone_animation(self._bone_animation_context(), bone_frames)

    @staticmethod
    def _collect_ik_link_joints() -> dict:
        """mmdCcdIk 出力で rotate が駆動される IK link joint を収集する。

        Returns:
            {joint_name: {"solver": solver_node, "slot": bone_slot}} 辞書。
            bone_slot は chainJson 内の links[i].bone_slot で、solver の
            inputRotate にキーイングするときのインデックスとして使う。
        """
        return collect_ik_link_joints()

    @staticmethod
    def _node_namespace(node: str) -> str:
        return node_namespace(node)

    def _collect_ik_nodes_by_bone_name(self, target_namespace: str = None) -> Dict[str, str]:
        """mmdCcdIk ノードを PMX IK ボーン名で引けるように収集する。"""
        return collect_ik_nodes_by_bone_name(target_namespace, self._node_namespace)

    def _apply_ik_enabled_animation(self, vmd_data: VmdData, target_namespace: str = None) -> None:
        """VMD の IK 表示/非表示フレームを mmdCcdIk.enabled に反映する。

        PMX import 直後は REST mesh を守るため mmdCcdIk.enabled=False。
        VMD が適用されるときだけ、VMD property frame に従って有効化する。
        property frame がないモデルモーションでは、従来互換として全 IK を
        評価範囲の先頭で有効にする。
        """
        apply_ik_enabled_animation(self._ik_enabled_animation_context(), vmd_data, target_namespace)

    def _build_legacy_bone_key_routes(self) -> Dict[str, dict]:
        """レガシー VMD キーの出力先を joint / rig node へ振り分ける。"""
        return build_legacy_bone_key_routes(self)

    def _add_attrs_to_anim_layer(self, node: str, attrs: List[str]):
        """指定属性を現在のアニメーションレイヤーへ追加する。"""
        if not (self.use_animation_layers and self.anim_layer):
            return
        add_existing_attrs_to_anim_layer(self.anim_layer, node, attrs)

    @staticmethod
    def _parse_vmd_interpolation(interpolation_bytes):
        """VMD bone interpolation bytes をチャンネル別 Bezier 制御点へ変換する。

        Returns:
            dict: translate_x/y/z と rotation をキーに持つ、正規化済み
                (x1, y1, x2, y2) タプルの辞書。データ不足時は空辞書。
        """
        return parse_vmd_interpolation(interpolation_bytes)

    @staticmethod
    def _vmd_interp_channel_for_attr(attr: str) -> Optional[str]:
        """Maya attribute 名に対応する VMD interpolation channel 名を返す。"""
        return vmd_interp_channel_for_attr(attr)

    @staticmethod
    def _parse_vmd_camera_interpolation(interpolation_bytes):
        """VMD camera interpolation bytes をチャンネル別 Bezier 制御点へ変換する。"""
        return parse_vmd_camera_interpolation(interpolation_bytes)

    @staticmethod
    def _get_frame_number(frame) -> float:
        """VMD frame object / dict から frame_number を取得する。"""
        return get_frame_number(frame)

    def _apply_vmd_bezier_tangents(
        self,
        joint: str,
        frames: List,
        attrs,
        channel_interp_map: Dict[str, str],
        interpolation_parser=None,
    ):
        """VMD Bezier 補間を Maya weighted tangent として適用する。"""
        with vmd_profile.scope("tangent_application", count=max(len(frames) - 1, 0)):
            apply_vmd_bezier_tangents(
                self,
                joint,
                frames,
                attrs,
                channel_interp_map,
                interpolation_parser=interpolation_parser,
            )

    def _set_bone_keyframes(self, joint: str, frames: List, vmd_bone_name: str, key_route: Optional[dict] = None):
        """ボーンのキーフレームを設定

        Args:
            joint: Mayaジョイント名
            frames: フレームデータのリスト
            vmd_bone_name: VMDボーン名
            key_route: append / IK rig 接続に応じたキー出力先情報
        """
        set_bone_keyframes(self._bone_animation_context(), joint, frames, vmd_bone_name, key_route)

    def _get_joint_orient_cache(self, joint_name):
        """joint の jointOrient quaternion と rotateOrder をキャッシュ付きで取得する。"""
        return get_joint_orient_cache(self, joint_name)

    def _convert_vmd_quat_to_joint_rotate(self, joint_name, qx, qy, qz, qw):
        """VMD quaternion を Maya joint.rotate の Euler 角（度）へ変換する。"""
        return convert_vmd_quat_to_joint_rotate(self, joint_name, qx, qy, qz, qw)

    def get_failed_bones(self) -> set:
        """変換に失敗したボーン名のセットを取得

        Returns:
            失敗したボーン名のセット
        """
        return self._failed_bones.copy()

    def set_bone_name_mapping(self, mapping: Dict[str, str]):
        """ボーン名マッピングを設定

        Args:
            mapping: VMDボーン名 -> Mayaジョイント名のマッピング
        """
        self.bone_name_mapping = mapping.copy()

    def _get_or_create_camera(self) -> str:
        """MMDカメラを取得または作成する

        Returns:
            カメラのトランスフォーム名
        """
        return get_or_create_camera()

    def _get_or_create_light(self) -> str:
        """MMD照明を取得または作成する

        Returns:
            照明のトランスフォーム名
        """
        return get_or_create_light()

    def _convert_camera_animation(self, camera_frames: List, vmd_bytes: bytes = None) -> bool:
        """カメラアニメーションを変換

        Args:
            camera_frames: カメラフレームデータのリスト
            vmd_bytes: mmd-anim camera sampling に使用する VMD バイト列

        Returns:
            変換が成功した場合True
        """
        return convert_camera_animation(self._camera_animation_context(), camera_frames, vmd_bytes=vmd_bytes)

    def _detect_vmd_motion_kind(self, vmd_data: VmdData) -> str:
        """VMD内容から大まかなモーション種別を判定する。"""
        return detect_vmd_motion_kind(vmd_data)

    def _convert_light_animation(self, light_frames: List, vmd_bytes: bytes = None) -> bool:
        """照明アニメーションを変換

        VMD light_frames の position を方向ベクトルとして扱い、Maya directionalLight の
        rotateX/Y/Z キーフレームも設定する。位置 (x, y, z) は Maya 方向 (x, y, -z) に変換。
        Maya の directionalLight はローカル -Z 方向に照射するため、指定方向へ -Z を
        向ける Euler 角 (rx, ry) を算出する（rz は常に 0）。

        Args:
            light_frames: 照明フレームデータのリスト
            vmd_bytes: mmd-anim light sampling に使用する VMD バイト列

        Returns:
            変換が成功した場合True
        """
        return convert_light_animation(self._light_animation_context(), light_frames, vmd_bytes=vmd_bytes)

    def _convert_morph_animation(self, morph_frames: List) -> bool:
        """モーフアニメーションを変換

        Args:
            morph_frames: モーフフレームデータのリスト

        Returns:
            変換が成功した場合True
        """
        return convert_morph_animation(self._morph_animation_context(), morph_frames)

    @staticmethod
    def _iter_morph_mappings(mapping_entry):
        return iter_morph_mappings(mapping_entry)

    def _build_morph_mappings(self):
        """シーン内のblendShapeとmetadata networkからモーフ名マッピングを構築"""
        build_morph_mappings(self)
