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
from typing import Dict, List, Optional, Tuple, Union


import maya.api.OpenMaya as om
import maya.cmds as cmds

from ..core.logger import get_logger
from ..core.native.native_pmx_parser import parse_pmx_native
from ..core.settings import settings
from ..core.vmd_data import VmdData
from .vmd_append_decomposition import (
    collect_append_info,
    decompose_append_own_rotation,
    decompose_append_own_translation,
    decompose_append_rotations_for_scene,
    decompose_append_translations_for_scene,
    get_or_expand_runtime_channel,
    joint_orient_quat_from_joint,
)
from .vmd_anim_layer import add_existing_attrs_to_anim_layer, add_transform_attrs_to_anim_layer
from .vmd_bezier_tangent import apply_vmd_bezier_tangents, query_key_value
from .vmd_bone_animation import convert_bone_animation, set_bone_keyframes
from .vmd_bone_interpolation import (
    get_frame_interpolation,
    get_frame_number,
    is_linear_vmd_interp,
    parse_vmd_interpolation,
    vmd_interp_channel_for_attr,
)
from .vmd_camera_animation import (
    convert_camera_animation,
    get_or_create_camera,
    parse_vmd_camera_interpolation,
    viewing_angle_to_focal_length,
)
from .vmd_import_state import (
    capture_anim_layer_selection,
    clear_existing_motion,
    cut_keyable_attrs,
    node_matches_target_namespace,
    restore_anim_layer_selection,
    restore_import_timeline_state,
    store_bind_translate,
)
from .vmd_ik_enabled_animation import apply_ik_enabled_animation, collect_ik_nodes_by_bone_name, node_namespace
from .vmd_ik_passthrough import collect_mmd_ik_passthrough_info, key_mmd_ik_passthrough_rotation
from .vmd_joint_rotation import (
    convert_vmd_quat_to_bind_space_rotate,
    convert_vmd_quat_to_joint_rotate,
    extract_euler_from_matrix,
    get_joint_orient_cache,
)
from .vmd_legacy_bone_routes import (
    build_legacy_bone_key_routes,
    collect_ik_link_joints,
    native_ik_handle_link_joints,
)
from .vmd_light_animation import convert_light_animation, get_or_create_light
from .vmd_motion_kind import detect_vmd_motion_kind
from .vmd_morph_animation import convert_morph_animation
from .vmd_morph_mapping import (
    build_morph_mappings,
    get_original_morph_name_candidates,
    iter_morph_mappings,
    read_blendshape_morph_names,
    register_morph_mapping,
)
from .vmd_name_mapping import build_name_mappings
from .vmd_runtime_rig_helper import (
    disable_mmd_rig_constraints_for_runtime_bake,
    disconnect_node_output_connections,
    has_live_mmd_rig_for_runtime_target,
    native_ik_handle_targets_mapped_joint,
    node_has_mapped_destination,
    node_name_in_set,
    restore_joints_to_bind_pose_for_runtime_bake,
    runtime_bake_mapped_joint_names,
)
from .vmd_runtime_channels import (
    append_bone_locals_to_channel_arrays,
    create_runtime_joint_channel_arrays,
    create_runtime_joint_channel_static_state,
    runtime_joint_attrs,
)
from .vmd_runtime_cache_apply import apply_runtime_cache_to_scene, is_static_channel, scale_motion_translate_from_bind
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
    resolve_pmx_path_from_scene,
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
from .vmd_timeline import set_scene_fps, setup_timeline

# mmd-anim runtime (Phase 1+)
try:
    from ..core.native.mmd_anim_runtime import (
        is_mmd_runtime_available,
        MmdRuntimeModel,
        MmdRuntimeClip,
        MmdRuntimeInstance,
        compute_maya_local_channels,
        compute_maya_local_channels_batch,
    )
    HAS_MMD_RUNTIME = True
except Exception:
    HAS_MMD_RUNTIME = False
    def is_mmd_runtime_available():
        return False
    MmdRuntimeModel = MmdRuntimeClip = MmdRuntimeInstance = None  # type: ignore
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
        self.motion_scale = float(settings.get("import.animation.motion_scale", 1.0))
        self._failed_bones = set()  # 変換に失敗したボーン名を記録
        self._bone_bind_poses: Dict[str, Tuple[float, float, float]] = {}  # ボーンの初期位置
        self.use_quaternion_interpolation = True  # Quaternion補間の使用フラグ
        self.anim_layer = None  # 現在のアニメーションレイヤー名
        self.use_animation_layers = True  # アニメーションレイヤーの使用フラグ
        self.import_camera_animation = True
        self.import_light_animation = True

        # runtime bake: 静的チャンネル判定の閾値。ワールド行列→ローカル分解で乗る
        # 浮動小数ジッタを吸収し、これ未満しか動かないチャンネルはキーを打たず
        # setAttr 一回で固定する（不要な全フレームキーを抑制）。
        # 並進は Maya linear 単位、回転は度で指定（内部比較時にラジアン換算）。
        self._static_eps_translate = float(
            settings.get("import.animation.static_channel_epsilon_translate", 1e-4)
        )
        self._static_eps_rotate = math.radians(
            float(settings.get("import.animation.static_channel_epsilon_rotate_deg", 0.01))
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

        Returns:
            変換が成功した場合True、失敗した場合False
        """
        import_start_time = None
        anim_layer_selection = None
        try:
            self.logger.info("Starting VMD animation conversion")
            try:
                import_start_time = cmds.currentTime(query=True)
                cmds.play(state=False)
            except Exception:
                import_start_time = None
            if self.use_animation_layers:
                anim_layer_selection = self._capture_anim_layer_selection()

            # 名前マッピングの構築（ボーン名 → Maya joint）
            self._build_name_mappings(target_namespace)
            if clear_existing_motion:
                self._clear_existing_motion(layer_name, target_namespace)

            # ボーンの初期位置を記録
            self._record_bind_poses()
            self.logger.info(f"Detected VMD motion kind: {self._detect_vmd_motion_kind(vmd_data)}")

            # タイムライン設定
            self._setup_timeline(vmd_data)

            # アニメーションレイヤーの作成
            if self.use_animation_layers:
                self.anim_layer = cmds.animLayer(layer_name, override=False, weight=1.0)

            live_rig_target = self._has_live_mmd_rig_for_runtime_target()
            if live_rig_target:
                self._build_bone_hierarchy_and_order_maps()
                self._build_runtime_bind_world_maps()
            vmd_bytes, pmx_bytes, pmx_path = self._resolve_runtime_bake_sources(
                vmd_data,
                vmd_bytes,
                pmx_bytes,
                pmx_path,
                target_namespace,
            )

            runtime_success = False
            if self._should_use_mmd_runtime_bake(vmd_bytes, pmx_bytes, pmx_path, live_rig_target, bake_mode):
                self.logger.info("Converting with mmd-anim runtime high-precision bake path")
                runtime_success = self._convert_using_mmd_runtime(
                    vmd_data=vmd_data,
                    vmd_bytes=vmd_bytes,
                    pmx_bytes=pmx_bytes,
                    pmx_path=pmx_path,
                )
                if runtime_success:
                    self.logger.info("mmd-anim runtime high-precision bake completed")
                else:
                    self.logger.warning("Runtime bake failed; falling back to legacy path")

            if not runtime_success:
                # --- レガシーパス（従来の変換） ---
                self._apply_ik_enabled_animation(vmd_data, target_namespace)

                if hasattr(vmd_data, "bone_frames") and vmd_data.bone_frames:
                    self.logger.info(f"Starting bone animation conversion (legacy): {len(vmd_data.bone_frames)} frames")
                    bone_success = self._convert_bone_animation(vmd_data.bone_frames)
                    if not bone_success:
                        self.logger.warning("Some errors occurred during bone animation conversion")

                # モーフアニメーション（レガシー）
                if hasattr(vmd_data, "morph_frames") and vmd_data.morph_frames:
                    self.logger.info("Converting morph animation (legacy)")
                    self._convert_morph_animation(vmd_data.morph_frames)

            # カメラアニメーション（レガシー）
            if self.import_camera_animation and hasattr(vmd_data, "camera_frames") and vmd_data.camera_frames:
                self.logger.info(f"Converting camera animation: {len(vmd_data.camera_frames)} frames")
                self._convert_camera_animation(vmd_data.camera_frames)

            # ライトアニメーション（レガシー）
            if self.import_light_animation and hasattr(vmd_data, "light_frames") and vmd_data.light_frames:
                self.logger.info(f"Converting light animation: {len(vmd_data.light_frames)} frames")
                self._convert_light_animation(vmd_data.light_frames)

            self.logger.info("VMD animation conversion completed")
            self._restore_import_timeline_state(import_start_time)
            return True

        except Exception as e:
            self._restore_import_timeline_state(import_start_time)
            self.logger.error(f"Error occurred during VMD animation conversion: {str(e)}", exc_info=True)
            return False
        finally:
            self._restore_anim_layer_selection(anim_layer_selection)

    @staticmethod
    def _restore_import_timeline_state(current_time: Optional[float]) -> None:
        """Keep VMD import from leaving Maya visibly playing or scrubbed ahead."""
        restore_import_timeline_state(current_time)

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
        clear_existing_motion(self, layer_name, target_namespace)

    @staticmethod
    def _node_matches_target_namespace(node: str, target_namespace: Optional[str]) -> bool:
        """target_namespace が指定されている場合、その namespace 内の node だけを対象にする。"""
        return node_matches_target_namespace(node, target_namespace)

    @staticmethod
    def _cut_keyable_attrs(node: str, attrs: Tuple[str, ...]) -> int:
        """存在する attr の key を削除し、削除を試みた attr 数を返す。"""
        return cut_keyable_attrs(node, attrs)

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

    def _resolve_pmx_path_from_scene(self, target_namespace: str = None) -> Optional[str]:
        """シーンの MMD model root に保存された PMX source path を探す。"""
        return resolve_pmx_path_from_scene(self, target_namespace)

    def _convert_using_mmd_runtime(
        self,
        vmd_data: VmdData,
        vmd_bytes: bytes,
        pmx_bytes: bytes,
        pmx_path: str,
    ) -> bool:
        """
        mmd-anim runtime を使って全フレームを評価し、正確なポーズをベイクする。
        付与変形・IK・MMDベジェ補間はすべて runtime 側で解決済み。
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
            self.logger.info(
                f"Runtime evaluation range: {min_frame} - {max_frame} "
                f"(keys={len(bake_samples)}, fps={self.fps:g})"
            )
            self._disable_mmd_rig_constraints_for_runtime_bake()
            self._restore_joints_to_bind_pose_for_runtime_bake()
            if self.bone_index_to_joint:
                self._build_runtime_bind_world_maps()

            # キャッシュ収集: 評価結果を API 配列へ直接保持（cmds.xform / setKeyframe を内側ループから排除）
            runtime_cache = collect_runtime_bake_cache(self, instance, clip, bake_samples)
            self.logger.info(
                f"mmd-anim runtime pose evaluation and cache completed "
                f"(frames={len(runtime_cache.baked_frames)}, elapsed={runtime_cache.eval_elapsed:.3f}s)"
            )
            self.logger.info(
                "runtime bake cache timings: "
                f"mode={'batch' if runtime_cache.batch_mode else 'per-frame'}, "
                f"eval_copy={runtime_cache.eval_copy_elapsed:.3f}s, "
                f"batch_unpack={runtime_cache.batch_unpack_elapsed:.3f}s, "
                f"local_decompose={runtime_cache.local_elapsed:.3f}s, "
                f"append={runtime_cache.append_elapsed:.3f}s"
            )

            # キャッシュから一括でキーフレーム登録（Maya Python API 2.0 優先）
            if runtime_cache.baked_frames:
                apply_start = time.perf_counter()
                apply_runtime_channel_arrays_to_scene_with_undo_disabled(
                    self,
                    runtime_cache.joint_channel_values,
                    runtime_cache.joint_channel_static,
                    runtime_cache.bake_times,
                    runtime_cache.baked_frames,
                    runtime_cache.morph_cache,
                    pmx_morph_names,
                )
                apply_elapsed = time.perf_counter() - apply_start
                self.logger.info(
                    f"Runtime cache key application completed (elapsed={apply_elapsed:.3f}s)"
                )

            runtime_elapsed = time.perf_counter() - runtime_start
            self.logger.info(f"runtime bake total elapsed={runtime_elapsed:.3f}s")

            return True

        finally:
            # リソース解放
            instance.free()
            clip.free()
            model.free()

    def _get_animation_frame_range(self, vmd_data: VmdData):
        """VMDデータからアニメーションのフレーム範囲を取得"""
        min_f = 0
        max_f = 0
        for frame_list in [
            getattr(vmd_data, "bone_frames", []),
            getattr(vmd_data, "morph_frames", []),
            getattr(vmd_data, "camera_frames", []),
            getattr(vmd_data, "light_frames", []),
        ]:
            for f in frame_list:
                if hasattr(f, "frame_number"):
                    fn = f.frame_number
                elif isinstance(f, dict):
                    fn = f.get("frame_number", 0)
                else:
                    fn = 0
                max_f = max(max_f, fn)
        return int(min_f), int(max_f)

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
        build_bone_hierarchy_and_order_maps(self)

    def _build_runtime_bind_world_maps(self) -> None:
        """Build bind-space maps used to convert runtime matrices for JO skinning.

        mmd-anim/public no-JO skinning deforms vertices with
        ``inverse(B_noJO) * W_mmd``.  A Maya skeleton with jointOrient uses a
        different bind world matrix, so the joint world matrix must be converted
        to ``B_maya * inverse(B_noJO) * W_mmd`` before local decomposition.
        """
        build_runtime_bind_world_maps(self)

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
        return compute_all_bone_locals(self, world_matrices)

    def _compute_all_bone_locals_native(
        self,
        world_matrices: List[List[float]],
    ) -> Optional[Dict[int, Tuple[float, float, float, float, float, float]]]:
        """Use mmd-anim FFI to decompose runtime world matrices when available."""
        return compute_all_bone_locals_native(self, world_matrices, compute_maya_local_channels)

    def _compute_native_local_channel_batch(self, batch_result):
        """Compute native local channels for an entire runtime batch when possible."""
        return compute_native_local_channel_batch(self, batch_result, compute_maya_local_channels_batch)

    @staticmethod
    def _native_local_channel_batch_for_frame(
        native_batch,
        frame_index: int,
    ) -> Dict[int, Tuple[float, float, float, float, float, float]]:
        """Extract one frame of local channel tuples from native batch output."""
        return native_local_channel_batch_for_frame(native_batch, frame_index)

    def _get_native_local_decompose_static_inputs(self, ordered_bone_indices: List[int]) -> Optional[Dict[str, list]]:
        """Return cached static inputs for native runtime local decomposition."""
        return get_native_local_decompose_static_inputs(self, ordered_bone_indices)

    @staticmethod
    def _extract_euler_from_matrix(m: om.MMatrix, rotate_order: int) -> Tuple[float, float, float]:
        """MMatrix から、指定した Maya rotateOrder (0=xyz ... 5=zyx) に対応するオイラー角(度)を抽出。
        """
        return extract_euler_from_matrix(m, rotate_order)

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
        return batch_create_and_key_curves(self, joint_name, channel_samples)

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
            self,
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
        return batch_key_scalar_channels(self, node_name, channel_samples, animation_layer=animation_layer)

    @staticmethod
    def _samples_as_anim_layer_deltas(node_name: str, channel_samples: Dict[str, List[Tuple[float, float]]]):
        """Convert absolute channel samples to additive animLayer deltas."""
        return samples_as_anim_layer_deltas(node_name, channel_samples)

    @staticmethod
    def _collect_append_info():
        """シーン内の全 mmdAppend ノードから (target_joint, append_node, source_joint, ratio, attr_map) を収集。"""
        return collect_append_info()

    @staticmethod
    def _get_or_expand_runtime_channel(
        ch_dict: Dict[str, Optional[om.MDoubleArray]],
        st_dict: Dict[str, dict],
        attr: str,
        n_frames: int,
    ) -> Optional[om.MDoubleArray]:
        return get_or_expand_runtime_channel(ch_dict, st_dict, attr, n_frames)

    @staticmethod
    def _joint_orient_quat_from_joint(joint: str) -> om.MQuaternion:
        """Return jointOrient as a quaternion, or identity when unavailable."""
        return joint_orient_quat_from_joint(joint)

    @staticmethod
    def _decompose_append_own_rotation(
        target_rx: om.MDoubleArray,
        target_ry: om.MDoubleArray,
        target_rz: om.MDoubleArray,
        source_rx: om.MDoubleArray,
        source_ry: om.MDoubleArray,
        source_rz: om.MDoubleArray,
        ratio: float,
        target_joint_orient: om.MQuaternion | None = None,
        source_joint_orient: om.MQuaternion | None = None,
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
            self,
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

    def _apply_runtime_cache_to_scene(
        self, runtime_cache: List[dict], pmx_morph_names: List[str]
    ):
        """キャッシュ済みフレームデータから、ジョイントの translate/rotate を API 2.0 で一括キーイング。

        モーフは既存パスで後処理（評価ループ外）。これにより内側ループの cmds.xform/setKeyframe を排除。
        """
        apply_runtime_cache_to_scene(self, runtime_cache, pmx_morph_names)

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
        bake_morph_weights_from_runtime(self, frame, morph_weights, pmx_morph_names)

    def _bake_morph_weight_cache_from_runtime(
        self,
        morph_cache: List[Tuple[float, list]],
        pmx_morph_names: List[str] = None,
    ) -> None:
        """runtime 評価済み morph weight cache を blendShape/network weight へ一括キーイングする。"""
        bake_morph_weight_cache_from_runtime(self, morph_cache, pmx_morph_names)

    def _disable_mmd_rig_constraints_for_runtime_bake(self):
        """runtime bake と二重評価になる PMX 付与constraint/IK solverを無効化する。"""
        disable_mmd_rig_constraints_for_runtime_bake(self)

    def _has_live_mmd_rig_for_runtime_target(self) -> bool:
        """現在の変換対象にlive MMD rig出力が接続されているかを返す。

        Rig mode は mmdAppend / mmdCcdIk をユーザー操作可能なリグとして残す必要がある。
        runtime bake は final pose を joint に直焼きする Bake mode 用の経路なので、
        対象jointへlive rig出力がある場合は選ばない。
        """
        return has_live_mmd_rig_for_runtime_target(self.logger)

    @classmethod
    def _native_ik_handle_targets_mapped_joint(cls, handle: str, mapped_joints: set[str]) -> bool:
        return native_ik_handle_targets_mapped_joint(handle, mapped_joints, cls._native_ik_handle_link_joints)

    def _restore_joints_to_bind_pose_for_runtime_bake(self) -> None:
        """live rig出力切断後に残った値を消し、runtime bake用のbind姿勢へ戻す。"""
        restore_joints_to_bind_pose_for_runtime_bake(self)

    def _runtime_bake_mapped_joint_names(self) -> set[str]:
        return runtime_bake_mapped_joint_names(self.bone_name_mapping)

    @classmethod
    def _node_has_mapped_destination(
        cls,
        node: str,
        attrs: Tuple[str, ...] | None,
        mapped_joints: set[str],
    ) -> bool:
        return node_has_mapped_destination(node, attrs, mapped_joints)

    @staticmethod
    def _node_name_in_set(node: str, names: set[str]) -> bool:
        return node_name_in_set(node, names)

    @staticmethod
    def _disconnect_node_output_connections(node: str, attrs: Tuple[str, ...]) -> int:
        return disconnect_node_output_connections(node, attrs)


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
        build_name_mappings(self, target_namespace)

    def _record_bind_poses(self):
        """各ボーンの初期位置（バインドポーズ）を記録"""
        self.logger.info("Recording initial bone positions")

        for vmd_bone_name, maya_joint in self.bone_name_mapping.items():
            try:
                # 現在のtranslate値を取得（これがバインドポーズ）
                translate = cmds.getAttr(f"{maya_joint}.translate")[0]
                self._bone_bind_poses[vmd_bone_name] = translate
                store_bind_translate(maya_joint, translate)
            except Exception as e:
                self.logger.warning(f"Failed to get bind pose for {vmd_bone_name}: {str(e)}")

    def _setup_timeline(self, vmd_data: VmdData):
        """タイムラインの設定

        Args:
            vmd_data: パース済みのVMDデータ
        """
        setup_timeline(self, vmd_data)

    def _convert_bone_animation(self, bone_frames: List) -> bool:
        """ボーンアニメーションを変換

        Args:
            bone_frames: ボーンフレームデータのリスト

        Returns:
            変換が成功した場合True
        """
        return convert_bone_animation(self, bone_frames)

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
    def _native_ik_handle_link_joints(handle: str) -> List[str]:
        return native_ik_handle_link_joints(handle)

    @staticmethod
    def _node_namespace(node: str) -> str:
        return node_namespace(node)

    def _collect_ik_nodes_by_bone_name(self, target_namespace: str = None) -> Dict[str, str]:
        """mmdCcdIk ノードを PMX IK ボーン名で引けるように収集する。"""
        return collect_ik_nodes_by_bone_name(self, target_namespace)

    def _apply_ik_enabled_animation(self, vmd_data: VmdData, target_namespace: str = None) -> None:
        """VMD の IK 表示/非表示フレームを mmdCcdIk.enabled に反映する。

        PMX import 直後は REST mesh を守るため mmdCcdIk.enabled=False。
        VMD が適用されるときだけ、VMD property frame に従って有効化する。
        property frame がないモデルモーションでは、従来互換として全 IK を
        評価範囲の先頭で有効にする。
        """
        apply_ik_enabled_animation(self, vmd_data, target_namespace)

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
    def _is_linear_vmd_interp(points: Tuple[float, float, float, float]) -> bool:
        """VMD Bezier 制御点が線形指定かどうかを判定する。"""
        return is_linear_vmd_interp(points)

    @staticmethod
    def _get_frame_number(frame) -> float:
        """VMD frame object / dict から frame_number を取得する。"""
        return get_frame_number(frame)

    @staticmethod
    def _get_frame_interpolation(frame):
        """VMD frame object / dict から interpolation bytes を取得する。"""
        return get_frame_interpolation(frame)

    def _query_key_value(self, plug: str, frame_number: float) -> Optional[float]:
        """指定 plug/frame のキー値を取得する。取得できない場合は None。"""
        return query_key_value(self.logger, plug, frame_number)

    def _apply_vmd_bezier_tangents(
        self,
        joint: str,
        frames: List,
        attrs,
        channel_interp_map: Dict[str, str],
        interpolation_parser=None,
    ):
        """VMD Bezier 補間を Maya weighted tangent として適用する。"""
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
        set_bone_keyframes(self, joint, frames, vmd_bone_name, key_route)

    def _get_joint_orient_cache(self, joint_name):
        """joint の jointOrient quaternion と rotateOrder をキャッシュ付きで取得する。"""
        return get_joint_orient_cache(self, joint_name)

    def _convert_vmd_quat_to_joint_rotate(self, joint_name, qx, qy, qz, qw):
        """VMD quaternion を Maya joint.rotate の Euler 角（度）へ変換する。"""
        return convert_vmd_quat_to_joint_rotate(self, joint_name, qx, qy, qz, qw)

    def _convert_vmd_quat_to_bind_space_rotate(
        self,
        joint_name: str,
        q_maya: om.MQuaternion,
        q_jo: Optional[om.MQuaternion],
    ) -> om.MQuaternion:
        """Convert a sparse VMD local rotation into this joint's JO-aware rotate space."""
        return convert_vmd_quat_to_bind_space_rotate(self, joint_name, q_maya, q_jo)

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

    def _convert_camera_animation(self, camera_frames: List) -> bool:
        """カメラアニメーションを変換

        Args:
            camera_frames: カメラフレームデータのリスト

        Returns:
            変換が成功した場合True
        """
        return convert_camera_animation(self, camera_frames)

    def _detect_vmd_motion_kind(self, vmd_data: VmdData) -> str:
        """VMD内容から大まかなモーション種別を判定する。"""
        return detect_vmd_motion_kind(vmd_data)

    def _viewing_angle_to_focal_length(self, camera_shape: str, viewing_angle: float) -> float:
        """VMD viewing_angle(deg) を Maya camera focalLength(mm) に変換する。"""
        return viewing_angle_to_focal_length(camera_shape, viewing_angle)

    def _convert_light_animation(self, light_frames: List) -> bool:
        """照明アニメーションを変換

        VMD light_frames の position を方向ベクトルとして扱い、Maya directionalLight の
        rotateX/Y/Z キーフレームも設定する。位置 (x, y, z) は Maya 方向 (x, y, -z) に変換。
        Maya の directionalLight はローカル -Z 方向に照射するため、指定方向へ -Z を
        向ける Euler 角 (rx, ry) を算出する（rz は常に 0）。

        Args:
            light_frames: 照明フレームデータのリスト

        Returns:
            変換が成功した場合True
        """
        return convert_light_animation(self, light_frames)

    def _convert_morph_animation(self, morph_frames: List) -> bool:
        """モーフアニメーションを変換

        Args:
            morph_frames: モーフフレームデータのリスト

        Returns:
            変換が成功した場合True
        """
        return convert_morph_animation(self, morph_frames)

    @staticmethod
    def _iter_morph_mappings(mapping_entry):
        return iter_morph_mappings(mapping_entry)

    def _register_morph_mapping(self, morph_name: str, mapping: Tuple[str, str, str]) -> None:
        register_morph_mapping(self, morph_name, mapping)

    def _read_blendshape_morph_names(self, blend_shape_node: str) -> Dict[int, str]:
        """blendShape に保存された weight index → 生モーフ名 (PmxMorph.name) を読み出す。

        import 時に MorphConverter が保存した権威マップ。lossy な alias 逆引きに頼らず、
        VMD/PMX が参照する生名で正確にマッピングするために使う。
        """
        return read_blendshape_morph_names(blend_shape_node)

    def _build_morph_mappings(self):
        """シーン内のblendShapeとmetadata networkからモーフ名マッピングを構築"""
        build_morph_mappings(self)

    def _get_original_morph_name_candidates(self, alias: str) -> List[str]:
        """Maya aliasからVMD/PMX側の元モーフ名候補を取得する。

        PMX import では日本語名を Maya 安全なASCII aliasへ変換する一方、
        VMD morph frame は日本語名のまま来る。そのため alias と辞書逆引き名の
        両方を mapping key として登録する。
        """
        return get_original_morph_name_candidates(alias)

    def _set_scene_fps(self, fps: float):
        """シーンのFPSを設定

        Args:
            fps: 設定するFPS値
        """
        set_scene_fps(fps, self.logger)
