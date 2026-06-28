"""VMDファイルをMayaアニメーションに変換するモジュール

このモジュールは、MikuMikuDance (MMD)のモーションデータファイル（VMD）を
Mayaのアニメーションデータに変換する機能を提供します。

Phase 1 以降:
- mmd-anim runtime を利用した高精度ベイク（Beziér補間、付与変形、IK を runtime で解決）
- レガシーパス（従来の変換）との共存と自動フォールバック
"""

from __future__ import annotations

import json
import math
import os
import time
from typing import Dict, List, Optional, Tuple, Union

from pathlib import Path

import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import maya.cmds as cmds

from ..core import maya_utils
from ..core.constants import (
    ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_CAMERA,
    ATTR_MMD_LIGHT,
    DEFAULT_CAMERA_NAME,
    DEFAULT_LIGHT_NAME,
)
from ..core.logger import get_logger
from ..core.native.native_pmx_parser import parse_pmx_native
from ..core.settings import settings
from ..core.vmd_data import VmdData
from .vmd_camera_animation import convert_camera_animation, parse_vmd_camera_interpolation, viewing_angle_to_focal_length
from .vmd_light_animation import convert_light_animation
from .vmd_morph_animation import convert_morph_animation
from .vmd_runtime_rig_helper import (
    _ls_mmd_append_nodes,
    _ls_mmd_ccd_ik_nodes,
    disable_mmd_rig_constraints_for_runtime_bake,
    disconnect_node_output_connections,
    has_live_mmd_rig_for_runtime_target,
    native_ik_handle_targets_mapped_joint,
    node_has_mapped_destination,
    node_name_in_set,
    restore_joints_to_bind_pose_for_runtime_bake,
    runtime_bake_mapped_joint_names,
)
from .vmd_runtime_sampling import (
    iter_runtime_bake_frame_samples,
    iter_runtime_bake_frames,
    native_local_channel_batch_for_frame,
    runtime_batch_morph_weights_for_frame,
    runtime_batch_world_matrices_for_frame,
)

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
        if current_time is not None:
            try:
                cmds.currentTime(current_time, edit=True)
            except Exception:
                pass
        try:
            cmds.play(state=False)
        except Exception:
            pass

    def vmd_frame_to_maya_time(self, frame_number: float) -> float:
        """Convert VMD's fixed 30fps frame number to the target Maya time unit."""
        return float(frame_number) * (float(self.fps) / 30.0)

    def maya_time_to_vmd_frame(self, maya_time: float) -> float:
        """Convert target Maya output time back to VMD's fixed 30fps frame number."""
        return float(maya_time) * (30.0 / float(self.fps))

    @staticmethod
    def _capture_anim_layer_selection() -> Dict[str, bool]:
        """VMD import 前の animLayer selected 状態を取得する。"""
        try:
            layers = cmds.ls(type="animLayer") or []
        except Exception:
            return {}

        selection = {}
        for layer in layers:
            try:
                selection[layer] = bool(cmds.animLayer(layer, query=True, selected=True))
            except Exception:
                pass
        return selection

    @staticmethod
    def _restore_anim_layer_selection(selection: Optional[Dict[str, bool]]) -> None:
        """VMD import 中に変わった animLayer selected 状態を元に戻す。"""
        if selection is None:
            return
        try:
            layers = cmds.ls(type="animLayer") or []
        except Exception:
            return

        for layer in layers:
            try:
                cmds.animLayer(layer, edit=True, selected=selection.get(layer, False))
            except Exception:
                pass

    def _clear_existing_motion(self, layer_name: str, target_namespace: Optional[str] = None) -> None:
        """対象モデルに残っている既存 VMD motion keys/layer を削除する。"""
        cleared = 0

        for joint in set(self.bone_name_mapping.values()):
            cleared += self._cut_keyable_attrs(
                joint,
                ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"),
            )

        for target_joint, info in self._collect_append_info().items():
            append_node = info.get("node")
            if append_node and (
                self._node_matches_target_namespace(target_joint, target_namespace)
                or self._node_matches_target_namespace(append_node, target_namespace)
            ):
                cleared += self._cut_keyable_attrs(
                    append_node,
                    (
                        "baseTranslateX",
                        "baseTranslateY",
                        "baseTranslateZ",
                        "baseRotateX",
                        "baseRotateY",
                        "baseRotateZ",
                    ),
                )

        for ik_node in _ls_mmd_ccd_ik_nodes():
            if self._node_matches_target_namespace(ik_node, target_namespace):
                cleared += self._cut_keyable_attrs(ik_node, ("enabled", "inputRotate"))

        morph_nodes = set()
        for mapping_entry in self.morph_name_mapping.values():
            for morph_node, weight_attr, _morph_name in self._iter_morph_mappings(mapping_entry):
                if self._node_matches_target_namespace(morph_node, target_namespace):
                    cleared += self._cut_keyable_attrs(morph_node, (weight_attr,))
                    morph_nodes.add(morph_node)

        if cmds.objExists(layer_name):
            try:
                cmds.delete(layer_name)
                cleared += 1
            except Exception as exc:
                self.logger.debug(f"failed to delete existing animLayer {layer_name}: {exc}")

        self.logger.info(
            "Cleared existing VMD motion: keys_or_layers=%d joints=%d morph_nodes=%d",
            cleared,
            len(set(self.bone_name_mapping.values())),
            len(morph_nodes),
        )

    @staticmethod
    def _node_matches_target_namespace(node: str, target_namespace: Optional[str]) -> bool:
        """target_namespace が指定されている場合、その namespace 内の node だけを対象にする。"""
        if not target_namespace:
            return True
        short_name = node.split("|")[-1]
        return short_name.startswith(f"{target_namespace}:")

    @staticmethod
    def _cut_keyable_attrs(node: str, attrs: Tuple[str, ...]) -> int:
        """存在する attr の key を削除し、削除を試みた attr 数を返す。"""
        if not node or not cmds.objExists(node):
            return 0

        cleared = 0
        for attr in attrs:
            attr_name = attr.split("[", 1)[0]
            if not cmds.attributeQuery(attr_name, node=node, exists=True):
                continue
            try:
                cmds.cutKey(node, attribute=attr)
                cleared += 1
            except Exception:
                pass
        return cleared

    def _should_use_mmd_runtime_bake(
        self,
        vmd_bytes: bytes,
        pmx_bytes: bytes,
        pmx_path: str,
        live_rig_target: bool = False,
        bake_mode: bool = False,
    ) -> bool:
        """Return True for Bake mode final-pose import, False for live Rig mode."""
        if not bake_mode:
            return False
        if live_rig_target:
            self.logger.info("Bake mode requested; live MMD rig outputs will be disabled for runtime bake")
        if not (HAS_MMD_RUNTIME and is_mmd_runtime_available()):
            return False

        has_vmd = bool(vmd_bytes)
        if bool(pmx_bytes):
            has_pmx = True
        else:
            has_pmx = bool(pmx_path) and Path(pmx_path).suffix.lower() == ".pmx" and os.path.exists(pmx_path)
        return bool(has_vmd and has_pmx)

    def _resolve_runtime_bake_sources(
        self,
        vmd_data: VmdData,
        vmd_bytes: bytes,
        pmx_bytes: bytes,
        pmx_path: str,
        target_namespace: str = None,
    ) -> Tuple[bytes, bytes, str]:
        """明示指定がない runtime bake 入力を VMD/scene metadata から復元する。"""
        resolved_vmd_bytes = vmd_bytes
        if not resolved_vmd_bytes:
            vmd_source = getattr(vmd_data, "source_file", None)
            if vmd_source and os.path.exists(vmd_source):
                try:
                    with open(vmd_source, "rb") as file:
                        resolved_vmd_bytes = file.read()
                    self.logger.info(f"Restored VMD bytes for runtime bake from VMD source_file: {vmd_source}")
                except Exception as exc:
                    self.logger.debug(f"Failed to read VMD source_file: {vmd_source}: {exc}")

        resolved_pmx_path = pmx_path
        if not pmx_bytes and not resolved_pmx_path:
            resolved_pmx_path = self._resolve_pmx_path_from_scene(target_namespace)

        return resolved_vmd_bytes, pmx_bytes, resolved_pmx_path

    def _resolve_pmx_path_from_scene(self, target_namespace: str = None) -> Optional[str]:
        """シーンの MMD model root に保存された PMX source path を探す。"""
        candidates = []
        for attr in cmds.ls("*.mmd_source_file", objectsOnly=False) or []:
            node = attr.rsplit(".", 1)[0]
            if target_namespace:
                node_namespace = node.rsplit(":", 1)[0] if ":" in node else ""
                if node_namespace != target_namespace:
                    continue
            try:
                stored = cmds.getAttr(attr)
            except Exception:
                continue
            if not stored:
                continue
            if Path(str(stored)).suffix.lower() != ".pmx":
                continue
            if os.path.exists(stored):
                candidates.append(str(stored))

        if len(candidates) == 1:
            self.logger.info(f"Restored PMX source from scene mmd_source_file: {candidates[0]}")
            return candidates[0]
        if len(candidates) > 1:
            self.logger.warning(
                "runtime bake 用 PMX source が複数見つかったため自動復元をスキップします: "
                + ", ".join(candidates)
            )
        return None

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
        # PMX バイトを解決
        resolved_pmx_bytes = pmx_bytes
        if not resolved_pmx_bytes and pmx_path and os.path.exists(pmx_path):
            try:
                with open(pmx_path, "rb") as f:
                    resolved_pmx_bytes = f.read()
            except Exception as e:
                self.logger.error(f"Failed to read PMX file: {pmx_path} - {e}")
                return False

        if not resolved_pmx_bytes:
            self.logger.error("Could not get PMX data required for runtime bake")
            return False

        # モデル・クリップ・インスタンス作成
        pmx_morph_names = []
        if pmx_path and os.path.exists(pmx_path):
            try:
                pmx_data = parse_pmx_native(pmx_path)
                if pmx_data is not None:
                    pmx_morph_names = [morph.name for morph in pmx_data.morphs]
            except Exception as e:
                self.logger.warning(f"Failed to get PMX morph names for runtime morph bake: {e}")

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

            # runtime bake は最終姿勢を毎フレーム直焼きするため、animation layerを使わない。
            # layer経由だと全ボーン全フレームのblend node作成が重く、未登録attribute警告も出る。
            runtime_anim_layer = self.anim_layer
            self.anim_layer = None
            refresh_suspended = False

            # キャッシュ収集: 評価結果を API 配列へ直接保持（cmds.xform / setKeyframe を内側ループから排除）
            baked_frames: List[float] = []
            bake_times = om.MTimeArray()
            joint_channel_values = self._create_runtime_joint_channel_arrays()
            joint_channel_static = self._create_runtime_joint_channel_static_state()
            morph_cache: List[Tuple[float, list]] = []
            eval_start = time.perf_counter()
            batch_mode = False
            eval_copy_elapsed = 0.0
            batch_unpack_elapsed = 0.0
            local_elapsed = 0.0
            append_elapsed = 0.0

            # 各フレームを評価してキャッシュ（Mayaコマンドを呼ばず高速に）
            try:
                try:
                    cmds.refresh(suspend=True)
                    refresh_suspended = True
                except Exception:
                    refresh_suspended = False

                batch_result = None
                if bake_samples:
                    batch_start = time.perf_counter()
                    batch_vmd_frames = [sample[1] for sample in bake_samples]
                    frame_step = (
                        float(batch_vmd_frames[1]) - float(batch_vmd_frames[0])
                        if len(batch_vmd_frames) > 1
                        else 1.0
                    )
                    batch_result = instance.evaluate_clip_frame_batch(
                        clip,
                        float(batch_vmd_frames[0]),
                        frame_step,
                        len(bake_samples),
                        worker_count=0,
                    )
                    eval_copy_elapsed += time.perf_counter() - batch_start

                if batch_result is not None:
                    batch_mode = True
                    self.logger.info(
                        "Using mmd-anim runtime batch evaluation "
                        f"(frames={batch_result.frame_count}, bones={batch_result.bone_count}, "
                        f"morphs={batch_result.morph_count})"
                    )
                    local_start = time.perf_counter()
                    native_local_batch = self._compute_native_local_channel_batch(batch_result)
                    local_elapsed += time.perf_counter() - local_start
                    if native_local_batch is not None:
                        self.logger.info(
                            "Using native batch local decomposition "
                            f"(frames={native_local_batch['frame_count']}, "
                            f"bones={native_local_batch['bone_count']})"
                        )
                    for frame_index, (maya_time, _vmd_frame) in enumerate(bake_samples):
                        unpack_start = time.perf_counter()
                        morph_weights = self._runtime_batch_morph_weights_for_frame(
                            batch_result, frame_index
                        )
                        batch_unpack_elapsed += time.perf_counter() - unpack_start

                        bone_locals: Dict[int, Tuple[float, float, float, float, float, float]] = {}
                        if self.bone_index_to_joint:
                            if not hasattr(self, "_bone_parent_map") or len(getattr(self, "_bone_parent_map", {})) == 0:
                                self._build_bone_hierarchy_and_order_maps()
                            local_start = time.perf_counter()
                            if native_local_batch is not None:
                                bone_locals = self._native_local_channel_batch_for_frame(
                                    native_local_batch,
                                    frame_index,
                                )
                            else:
                                world_matrices = self._runtime_batch_world_matrices_for_frame(
                                    batch_result, frame_index
                                )
                                bone_locals = self._compute_all_bone_locals(world_matrices)
                            local_elapsed += time.perf_counter() - local_start

                        append_start = time.perf_counter()
                        baked_frames.append(float(maya_time))
                        bake_times.append(om.MTime(float(maya_time), om.MTime.uiUnit()))
                        self._append_bone_locals_to_channel_arrays(
                            bone_locals, joint_channel_values, joint_channel_static
                        )
                        morph_cache.append((float(maya_time), morph_weights))
                        append_elapsed += time.perf_counter() - append_start
                else:
                    if bake_samples:
                        self.logger.info(
                            "mmd-anim runtime batch evaluation unavailable; using per-frame ABI"
                        )
                    for maya_time, vmd_frame in bake_samples:
                        eval_copy_start = time.perf_counter()
                        if not instance.evaluate_clip_frame(clip, float(vmd_frame)):
                            eval_copy_elapsed += time.perf_counter() - eval_copy_start
                            continue

                        # ワールド行列・モーフウェイトを取得（ボーン順）
                        world_matrices = instance.get_world_matrices() or []
                        morph_weights = instance.get_morph_weights() or []
                        eval_copy_elapsed += time.perf_counter() - eval_copy_start

                        # ローカルポーズをメモリ内で計算（親子階層を考慮した t/r ）
                        bone_locals: Dict[int, Tuple[float, float, float, float, float, float]] = {}
                        if self.bone_index_to_joint:
                            if not hasattr(self, "_bone_parent_map") or len(getattr(self, "_bone_parent_map", {})) == 0:
                                self._build_bone_hierarchy_and_order_maps()
                            local_start = time.perf_counter()
                            bone_locals = self._compute_all_bone_locals(world_matrices)
                            local_elapsed += time.perf_counter() - local_start

                        append_start = time.perf_counter()
                        baked_frames.append(float(maya_time))
                        bake_times.append(om.MTime(float(maya_time), om.MTime.uiUnit()))
                        self._append_bone_locals_to_channel_arrays(
                            bone_locals, joint_channel_values, joint_channel_static
                        )
                        morph_cache.append((float(maya_time), list(morph_weights)))
                        append_elapsed += time.perf_counter() - append_start
            finally:
                if refresh_suspended:
                    try:
                        cmds.refresh(suspend=False)
                    except Exception:
                        pass
                self.anim_layer = runtime_anim_layer

            eval_elapsed = time.perf_counter() - eval_start
            self.logger.info(
                f"mmd-anim runtime pose evaluation and cache completed "
                f"(frames={len(baked_frames)}, elapsed={eval_elapsed:.3f}s)"
            )
            self.logger.info(
                "runtime bake cache timings: "
                f"mode={'batch' if batch_mode else 'per-frame'}, "
                f"eval_copy={eval_copy_elapsed:.3f}s, "
                f"batch_unpack={batch_unpack_elapsed:.3f}s, "
                f"local_decompose={local_elapsed:.3f}s, "
                f"append={append_elapsed:.3f}s"
            )

            # キャッシュから一括でキーフレーム登録（Maya Python API 2.0 優先）
            if baked_frames:
                apply_start = time.perf_counter()
                undo_was_enabled = True
                try:
                    undo_was_enabled = bool(cmds.undoInfo(q=True, state=True))
                except Exception:
                    undo_was_enabled = True
                try:
                    cmds.undoInfo(stateWithoutFlush=False)
                except Exception:
                    pass
                try:
                    self._apply_runtime_channel_arrays_to_scene(
                        joint_channel_values,
                        joint_channel_static,
                        bake_times,
                        baked_frames,
                        morph_cache,
                        pmx_morph_names,
                    )
                finally:
                    if undo_was_enabled:
                        try:
                            cmds.undoInfo(stateWithoutFlush=True)
                        except Exception:
                            pass
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
        self._bone_parent_map: Dict[int, Optional[int]] = {}
        self._bone_rotate_orders: Dict[int, int] = {}
        for bidx, joint in list(self.bone_index_to_joint.items()):
            self._bone_rotate_orders[bidx] = 0
            try:
                if cmds.attributeQuery("rotateOrder", node=joint, exists=True):
                    ro = cmds.getAttr(f"{joint}.rotateOrder")
                    if ro is not None:
                        self._bone_rotate_orders[bidx] = int(ro)
            except Exception:
                pass
            self._bone_parent_map[bidx] = None
            try:
                parents = cmds.listRelatives(joint, parent=True, type="joint", fullPath=False) or []
                if parents:
                    pjoint = parents[0]
                    for pidx, pj in self.bone_index_to_joint.items():
                        if pj == pjoint:
                            self._bone_parent_map[bidx] = pidx
                            break
            except Exception:
                pass
        self.logger.debug(f"Built hierarchy map for {len(self._bone_parent_map)} bones for runtime cache")

    def _build_runtime_bind_world_maps(self) -> None:
        """Build bind-space maps used to convert runtime matrices for JO skinning.

        mmd-anim/public no-JO skinning deforms vertices with
        ``inverse(B_noJO) * W_mmd``.  A Maya skeleton with jointOrient uses a
        different bind world matrix, so the joint world matrix must be converted
        to ``B_maya * inverse(B_noJO) * W_mmd`` before local decomposition.
        """
        self._runtime_bind_world_matrices: Dict[int, om.MMatrix] = {}
        self._runtime_no_orient_bind_world_matrices: Dict[int, om.MMatrix] = {}
        self._native_local_decompose_inputs = None
        if not hasattr(self, "_bone_parent_map") or len(getattr(self, "_bone_parent_map", {})) == 0:
            self._build_bone_hierarchy_and_order_maps()

        index_to_bone_name = {idx: name for name, idx in self.bone_name_to_index.items()}
        resolved_bind_worlds: Dict[int, om.MMatrix] = {}

        def _bind_translate(bidx: int, joint: str) -> Tuple[float, float, float]:
            bone_name = index_to_bone_name.get(bidx)
            value = self._bone_bind_poses.get(bone_name) if bone_name else None
            if value is None:
                try:
                    value = cmds.getAttr(f"{joint}.translate")[0]
                except Exception:
                    value = (0.0, 0.0, 0.0)
            return float(value[0]), float(value[1]), float(value[2])

        def _resolve_bind_world(bidx: int) -> Optional[om.MMatrix]:
            if bidx in resolved_bind_worlds:
                return resolved_bind_worlds[bidx]
            joint = self.bone_index_to_joint.get(bidx)
            if not joint or not cmds.objExists(joint):
                return None

            tx, ty, tz = _bind_translate(bidx, joint)
            tm = om.MTransformationMatrix()
            tm.setTranslation(om.MVector(tx, ty, tz), om.MSpace.kTransform)
            q_jo, _ro = self._get_joint_orient_cache(joint)
            if q_jo is not None:
                tm.setRotation(q_jo)
            local_bind = tm.asMatrix()

            parent_idx = getattr(self, "_bone_parent_map", {}).get(bidx)
            parent_world = _resolve_bind_world(parent_idx) if parent_idx is not None else None
            bind_world = local_bind * parent_world if parent_world is not None else local_bind
            resolved_bind_worlds[bidx] = bind_world
            return bind_world

        for bidx, joint in self.bone_index_to_joint.items():
            bind_world = _resolve_bind_world(bidx)
            if bind_world is None:
                continue
            self._runtime_bind_world_matrices[bidx] = bind_world
            bind_no_orient = om.MMatrix()
            bind_no_orient[12] = bind_world[12]
            bind_no_orient[13] = bind_world[13]
            bind_no_orient[14] = bind_world[14]
            self._runtime_no_orient_bind_world_matrices[bidx] = bind_no_orient

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
        if not world_matrices or not self.bone_index_to_joint:
            return {}
        if not hasattr(self, "_runtime_bind_world_matrices"):
            self._build_runtime_bind_world_maps()
        native_locals = self._compute_all_bone_locals_native(world_matrices)
        if native_locals is not None:
            return native_locals
        locals_map: Dict[int, Tuple[float, float, float, float, float, float]] = {}
        maya_worlds: Dict[int, om.MMatrix] = {}
        for bidx in self.bone_index_to_joint.keys():
            if bidx < len(world_matrices):
                mmd_m = world_matrices[bidx]
                if isinstance(mmd_m, (list, tuple)) and len(mmd_m) == 16:
                    try:
                        maya_flat = self._convert_mmd_world_matrix_to_maya(list(mmd_m))
                        runtime_world = om.MMatrix(maya_flat)
                        bind_world = getattr(self, "_runtime_bind_world_matrices", {}).get(bidx)
                        bind_no_orient = getattr(self, "_runtime_no_orient_bind_world_matrices", {}).get(bidx)
                        if bind_world is not None and bind_no_orient is not None:
                            maya_worlds[bidx] = bind_world * bind_no_orient.inverse() * runtime_world
                        else:
                            maya_worlds[bidx] = runtime_world
                    except Exception:
                        pass
        for bidx, joint in self.bone_index_to_joint.items():
            if bidx not in maya_worlds:
                continue
            mw = maya_worlds[bidx]
            pidx = getattr(self, "_bone_parent_map", {}).get(bidx)
            pw = maya_worlds.get(pidx) if pidx is not None else None
            try:
                local_m = (mw * pw.inverse()) if pw is not None else mw
                tm = om.MTransformationMatrix(local_m)
                t = tm.translation(om.MSpace.kTransform)
                tx, ty, tz = float(t.x), float(t.y), float(t.z)
                q_total = tm.rotation(asQuaternion=True)
                q_jo, ro = self._get_joint_orient_cache(joint)
                if q_jo is not None:
                    q_rotate = q_total * q_jo.inverse()
                else:
                    q_rotate = q_total
                order_map = {
                    0: om.MEulerRotation.kXYZ, 1: om.MEulerRotation.kYZX,
                    2: om.MEulerRotation.kZXY, 3: om.MEulerRotation.kXZY,
                    4: om.MEulerRotation.kYXZ, 5: om.MEulerRotation.kZYX,
                }
                e = q_rotate.asEulerRotation()
                order = order_map.get(ro, om.MEulerRotation.kXYZ)
                if e.order != order:
                    e.reorderIt(order)
                rx = math.degrees(e.x)
                ry = math.degrees(e.y)
                rz = math.degrees(e.z)
                locals_map[bidx] = (tx, ty, tz, rx, ry, rz)
            except Exception as e:
                self.logger.debug(f"local compute fail for bone_idx={bidx}: {e}")
        return locals_map

    def _compute_all_bone_locals_native(
        self,
        world_matrices: List[List[float]],
    ) -> Optional[Dict[int, Tuple[float, float, float, float, float, float]]]:
        """Use mmd-anim FFI to decompose runtime world matrices when available."""
        if compute_maya_local_channels is None:
            return None

        ordered_bone_indices = [
            bidx
            for bidx in self.bone_index_to_joint.keys()
            if bidx < len(world_matrices)
            and isinstance(world_matrices[bidx], (list, tuple))
            and len(world_matrices[bidx]) == 16
        ]
        if not ordered_bone_indices:
            return None

        static_inputs = self._get_native_local_decompose_static_inputs(ordered_bone_indices)
        if static_inputs is None:
            return None

        world_flat = []
        for bidx in ordered_bone_indices:
            world_flat.extend(float(value) for value in world_matrices[bidx])

        native_values = compute_maya_local_channels(
            world_flat,
            static_inputs["parent_indices"],
            static_inputs["bind_flat"],
            static_inputs["no_orient_flat"],
            static_inputs["joint_orient_flat"],
            static_inputs["rotate_orders"],
        )
        if native_values is None or len(native_values) != len(ordered_bone_indices):
            return None
        return {
            bidx: tuple(native_values[slot])
            for slot, bidx in enumerate(ordered_bone_indices)
        }

    def _compute_native_local_channel_batch(self, batch_result):
        """Compute native local channels for an entire runtime batch when possible."""
        if compute_maya_local_channels_batch is None:
            return None
        bone_count = int(getattr(batch_result, "bone_count", 0))
        ordered_bone_indices = list(range(bone_count))
        if not ordered_bone_indices or any(bidx not in self.bone_index_to_joint for bidx in ordered_bone_indices):
            return None
        static_inputs = self._get_native_local_decompose_static_inputs(ordered_bone_indices)
        if static_inputs is None:
            return None
        native_batch = compute_maya_local_channels_batch(
            batch_result.world_matrices,
            int(batch_result.frame_count),
            int(batch_result.bone_count),
            static_inputs["parent_indices"],
            static_inputs["bind_flat"],
            static_inputs["no_orient_flat"],
            static_inputs["joint_orient_flat"],
            static_inputs["rotate_orders"],
        )
        if native_batch is None:
            return None
        return {
            "ordered_bone_indices": tuple(ordered_bone_indices),
            "frame_count": int(native_batch.frame_count),
            "bone_count": int(native_batch.bone_count),
            "local_channels": native_batch.local_channels,
        }

    @staticmethod
    def _native_local_channel_batch_for_frame(
        native_batch,
        frame_index: int,
    ) -> Dict[int, Tuple[float, float, float, float, float, float]]:
        """Extract one frame of local channel tuples from native batch output."""
        return native_local_channel_batch_for_frame(native_batch, frame_index)

    def _get_native_local_decompose_static_inputs(self, ordered_bone_indices: List[int]) -> Optional[Dict[str, list]]:
        """Return cached static inputs for native runtime local decomposition."""
        cached = getattr(self, "_native_local_decompose_inputs", None)
        if cached and cached.get("ordered_bone_indices") == tuple(ordered_bone_indices):
            return cached

        parent_lookup = {bidx: slot for slot, bidx in enumerate(ordered_bone_indices)}
        parent_indices = []
        bind_flat = []
        no_orient_flat = []
        joint_orient_flat = []
        rotate_orders = []
        for bidx in ordered_bone_indices:
            joint = self.bone_index_to_joint.get(bidx)
            bind_world = getattr(self, "_runtime_bind_world_matrices", {}).get(bidx)
            bind_no_orient = getattr(self, "_runtime_no_orient_bind_world_matrices", {}).get(bidx)
            if not joint or bind_world is None or bind_no_orient is None:
                return None

            parent_bidx = getattr(self, "_bone_parent_map", {}).get(bidx)
            parent_indices.append(parent_lookup.get(parent_bidx, -1))
            bind_flat.extend(float(bind_world[index]) for index in range(16))
            no_orient_flat.extend(float(bind_no_orient[index]) for index in range(16))

            q_jo, ro = self._get_joint_orient_cache(joint)
            if q_jo is None:
                joint_orient_flat.extend((0.0, 0.0, 0.0, 1.0))
            else:
                joint_orient_flat.extend((float(q_jo.x), float(q_jo.y), float(q_jo.z), float(q_jo.w)))
            rotate_orders.append(int(ro))

        if any(order != 0 for order in rotate_orders):
            return None

        cached = {
            "ordered_bone_indices": tuple(ordered_bone_indices),
            "parent_indices": parent_indices,
            "bind_flat": bind_flat,
            "no_orient_flat": no_orient_flat,
            "joint_orient_flat": joint_orient_flat,
            "rotate_orders": rotate_orders,
        }
        self._native_local_decompose_inputs = cached
        return cached

    @staticmethod
    def _extract_euler_from_matrix(m: om.MMatrix, rotate_order: int) -> Tuple[float, float, float]:
        """MMatrix から、指定した Maya rotateOrder (0=xyz ... 5=zyx) に対応するオイラー角(度)を抽出。
        """
        try:
            tm = om.MTransformationMatrix(m)
            q = tm.rotation(asQuaternion=True)
            order_map = {
                0: om.MEulerRotation.kXYZ,
                1: om.MEulerRotation.kYZX,
                2: om.MEulerRotation.kZXY,
                3: om.MEulerRotation.kXZY,
                4: om.MEulerRotation.kYXZ,
                5: om.MEulerRotation.kZYX,
            }
            order = order_map.get(rotate_order, om.MEulerRotation.kXYZ)
            e = q.asEulerRotation()
            if e.order != order:
                e.reorderIt(order)
            return (math.degrees(e.x), math.degrees(e.y), math.degrees(e.z))
        except Exception:
            return (0.0, 0.0, 0.0)

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
        if not cmds.objExists(joint_name) or not channel_samples:
            return False
        attrs = list(channel_samples.keys())
        curves: Dict[str, oma.MFnAnimCurve] = {}
        try:
            curves = maya_utils.create_animation_curves(
                joint_name,
                attrs,
                tangent_type=oma.MFnAnimCurve.kTangentLinear,
                animation_layer=None,
            )
        except Exception as e:
            self.logger.debug(f"create_animation_curves failed for {joint_name}: {e}")
            curves = {}

        tangent = oma.MFnAnimCurve.kTangentLinear
        shared_times = None
        if channel_samples:
            first_samples = next((samples for samples in channel_samples.values() if samples), None)
            if first_samples:
                try:
                    shared_times = om.MTimeArray()
                    for frame, _ in first_samples:
                        shared_times.append(om.MTime(float(frame), om.MTime.uiUnit()))
                except Exception:
                    shared_times = None

        success_any = False
        for attr, samples in channel_samples.items():
            if not samples:
                continue
            used_api = False
            if attr in curves:
                curve = curves[attr]
                try:
                    times = shared_times
                    vals = om.MDoubleArray()
                    for frame, val in samples:
                        vals.append(float(val))
                    if times is None or len(times) != len(vals):
                        times = om.MTimeArray()
                        for frame, _ in samples:
                            times.append(om.MTime(float(frame), om.MTime.uiUnit()))
                    curve.addKeys(times, vals, tangent, tangent, False)
                    used_api = True
                    success_any = True
                    continue
                except Exception as e:
                    self.logger.debug(f"addKeys failed for {joint_name}.{attr}, fallback: {e}")
            # Fallback (cmds) - 値の単位に注意: 回転は度
            for frame, val in samples:
                try:
                    cmd_val = math.degrees(val) if "rotate" in attr else val
                    cmds.setKeyframe(joint_name, attribute=attr, time=frame, value=cmd_val)
                    success_any = True
                except Exception:
                    pass
            if not used_api:
                self.logger.debug(f"Used cmds.setKeyframe fallback for {joint_name}.{attr}")
        return success_any

    @staticmethod
    def _runtime_joint_attrs() -> Tuple[str, str, str, str, str, str]:
        """runtime bakeでキー登録するjoint channel一覧を返す。"""
        return ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")

    def _create_runtime_joint_channel_arrays(self) -> Dict[str, Dict[str, Optional[om.MDoubleArray]]]:
        """runtime bake用にjoint channelごとの値配列を作成する。"""
        values: Dict[str, Dict[str, Optional[om.MDoubleArray]]] = {}
        for joint in self.bone_index_to_joint.values():
            if not cmds.objExists(joint):
                continue
            values[joint] = {attr: None for attr in self._runtime_joint_attrs()}
        return values

    def _create_runtime_joint_channel_static_state(self) -> Dict[str, Dict[str, dict]]:
        """静的channel判定用の状態を作成する。"""
        states: Dict[str, Dict[str, dict]] = {}
        for joint in self.bone_index_to_joint.values():
            if not cmds.objExists(joint):
                continue
            states[joint] = {
                attr: {"first": None, "is_static": True, "count": 0}
                for attr in self._runtime_joint_attrs()
            }
        return states

    def _append_bone_locals_to_channel_arrays(
        self,
        bone_locals: Dict[int, Tuple[float, float, float, float, float, float]],
        channel_values: Dict[str, Dict[str, Optional[om.MDoubleArray]]],
        static_state: Dict[str, Dict[str, dict]],
    ):
        """frameごとのlocal姿勢をjoint channel配列へ直接追加する。"""
        for bidx, (tx, ty, tz, rx, ry, rz) in bone_locals.items():
            joint = self.bone_index_to_joint.get(bidx)
            chans = channel_values.get(joint)
            states = static_state.get(joint)
            if not chans or not states:
                continue

            tx, ty, tz = self._scale_motion_translate_from_bind(joint, tx, ty, tz)
            values = {
                "translateX": float(tx),
                "translateY": float(ty),
                "translateZ": float(tz),
                "rotateX": math.radians(float(rx)),
                "rotateY": math.radians(float(ry)),
                "rotateZ": math.radians(float(rz)),
            }
            for attr, value in values.items():
                state = states[attr]
                first = state["first"]
                if first is None:
                    state["first"] = value
                    state["count"] = 1
                    continue

                eps = (
                    self._static_eps_rotate
                    if attr.startswith("rotate")
                    else self._static_eps_translate
                )
                if state["is_static"]:
                    if abs(float(value) - float(first)) <= eps:
                        state["count"] += 1
                        continue

                    array = om.MDoubleArray()
                    for _ in range(int(state["count"])):
                        array.append(float(first))
                    array.append(float(value))
                    chans[attr] = array
                    state["is_static"] = False
                    state["count"] += 1
                    continue

                array = chans[attr]
                if array is not None:
                    array.append(float(value))
                state["count"] += 1

    def _batch_create_and_key_curve_arrays(
        self,
        joint_name: str,
        channel_values: Dict[str, Optional[om.MDoubleArray]],
        static_state: Dict[str, dict],
        times: om.MTimeArray,
        frame_numbers: List[float],
    ) -> Tuple[int, int]:
        """MDoubleArrayへ収集済みのchannel値をMFnAnimCurve.addKeysで一括登録する。"""
        if not cmds.objExists(joint_name) or not channel_values:
            return 0, 0

        dynamic_attrs = []
        skipped_static = 0
        for attr, values in channel_values.items():
            state = static_state.get(attr, {})
            if state.get("is_static", False):
                skipped_static += 1
                if state.get("first") is not None:
                    try:
                        value = float(state["first"])
                        if "rotate" in attr:
                            value = math.degrees(value)
                        cmds.setAttr(f"{joint_name}.{attr}", value)
                    except Exception:
                        pass
                continue
            if values is None or len(values) != len(times):
                continue
            dynamic_attrs.append(attr)

        if not dynamic_attrs:
            return 0, skipped_static

        try:
            curves = maya_utils.create_animation_curves(
                joint_name,
                dynamic_attrs,
                tangent_type=oma.MFnAnimCurve.kTangentLinear,
                animation_layer=None,
            )
        except Exception as e:
            self.logger.debug(f"create_animation_curves failed for {joint_name}: {e}")
            curves = {}

        tangent = oma.MFnAnimCurve.kTangentLinear
        keyed = 0
        for attr in dynamic_attrs:
            values = channel_values[attr]
            curve = curves.get(attr)
            if curve:
                try:
                    curve.addKeys(times, values, tangent, tangent, False)
                    keyed += 1
                    continue
                except Exception as e:
                    self.logger.debug(f"addKeys failed for {joint_name}.{attr}, fallback: {e}")

            for index, frame in enumerate(frame_numbers):
                try:
                    value = float(values[index])
                    if "rotate" in attr:
                        value = math.degrees(value)
                    cmds.setKeyframe(joint_name, attribute=attr, time=frame, value=value)
                except Exception:
                    pass
            keyed += 1

        return keyed, skipped_static

    def _batch_key_scalar_channels(
        self,
        node_name: str,
        channel_samples: Dict[str, List[Tuple[float, float]]],
        animation_layer: Optional[str] = None,
    ) -> bool:
        """Maya UI 値の scalar channel を MFnAnimCurve.addKeys で一括キーイングする。"""
        if not cmds.objExists(node_name) or not channel_samples:
            return False

        attrs = [attr for attr, samples in channel_samples.items() if samples]
        if not attrs:
            return False

        curves: Dict[str, oma.MFnAnimCurve] = {}
        try:
            curves = maya_utils.create_animation_curves(
                node_name,
                attrs,
                tangent_type=oma.MFnAnimCurve.kTangentLinear,
                animation_layer=animation_layer,
            )
        except Exception as exc:
            self.logger.debug(f"create_animation_curves failed for {node_name}: {exc}")

        tangent = oma.MFnAnimCurve.kTangentLinear
        success_any = False
        for attr in attrs:
            samples = channel_samples[attr]
            curve = curves.get(attr)
            if curve:
                try:
                    times = om.MTimeArray()
                    values = om.MDoubleArray()
                    for frame, value in samples:
                        times.append(om.MTime(float(frame), om.MTime.uiUnit()))
                        api_value = math.radians(float(value)) if "rotate" in attr else float(value)
                        values.append(api_value)
                    curve.addKeys(times, values, tangent, tangent, False)
                    success_any = True
                    continue
                except Exception as exc:
                    self.logger.debug(f"addKeys failed for {node_name}.{attr}, fallback: {exc}")

            for frame, value in samples:
                try:
                    key_args = {
                        "attribute": attr,
                        "time": frame,
                        "value": float(value),
                    }
                    if animation_layer:
                        key_args["animLayer"] = animation_layer
                    cmds.setKeyframe(node_name, **key_args)
                    success_any = True
                except Exception as exc:
                    self.logger.debug(f"setKeyframe fallback failed for {node_name}.{attr} at {frame}: {exc}")

        return success_any

    @staticmethod
    def _samples_as_anim_layer_deltas(node_name: str, channel_samples: Dict[str, List[Tuple[float, float]]]):
        """Convert absolute channel samples to additive animLayer deltas."""
        adjusted = {}
        for attr, samples in channel_samples.items():
            if not samples:
                adjusted[attr] = samples
                continue
            try:
                base_value = cmds.getAttr(f"{node_name}.{attr}")
                if isinstance(base_value, (list, tuple)):
                    base_value = base_value[0]
                if isinstance(base_value, (list, tuple)):
                    base_value = base_value[0]
                base_value = float(base_value)
            except Exception:
                base_value = 0.0
            adjusted[attr] = [(frame, float(value) - base_value) for frame, value in samples]
        return adjusted

    @staticmethod
    def _collect_append_info():
        """シーン内の全 mmdAppend ノードから (target_joint, append_node, source_joint, ratio, attr_map) を収集。"""
        result = {}
        append_nodes = _ls_mmd_append_nodes()

        def _compound_destinations(src_attr, dst_attr):
            plugs = cmds.listConnections(src_attr, s=False, d=True, p=True) or []
            suffix = f".{dst_attr}"
            return [plug.rsplit(".", 1)[0] for plug in plugs if plug.endswith(suffix)]

        node_targets = {}
        for node in append_nodes:
            rotate_dsts = _compound_destinations(f"{node}.outputRotate", "rotate")
            translate_dsts = _compound_destinations(f"{node}.outputTranslate", "translate")
            if not rotate_dsts and not translate_dsts:
                continue
            target_joint = rotate_dsts[0] if rotate_dsts else translate_dsts[0]
            node_targets[node] = target_joint

        for node in append_nodes:
            target_joint = node_targets.get(node)
            if not target_joint:
                continue
            rotate_dsts = _compound_destinations(f"{node}.outputRotate", "rotate")
            translate_dsts = _compound_destinations(f"{node}.outputTranslate", "translate")

            def _source_from_plug(plug: str, append_prefix: str, joint_attr: str):
                src_node, src_attr = plug.rsplit(".", 1)
                if src_attr.startswith(append_prefix):
                    return node_targets.get(src_node), src_node
                if src_attr.startswith(joint_attr):
                    return src_node, None
                if src_attr.startswith("output3D"):
                    upstream = cmds.listConnections(f"{src_node}.input3D[0]", s=True, d=False, p=True) or []
                    if upstream:
                        return _source_from_plug(upstream[0], append_prefix, joint_attr)
                return None, None

            source_joint = None
            source_append_node = None
            rotate_src_plugs = cmds.listConnections(f"{node}.sourceRotate", s=True, d=False, p=True) or []
            if rotate_src_plugs:
                source_joint, source_append_node = _source_from_plug(rotate_src_plugs[0], "appendRotate", "rotate")
            translate_src_plugs = cmds.listConnections(f"{node}.sourceTranslate", s=True, d=False, p=True) or []
            if not source_joint and translate_src_plugs:
                source_joint, source_append_node = _source_from_plug(
                    translate_src_plugs[0],
                    "appendTranslate",
                    "translate",
                )
            ratio = cmds.getAttr(f"{node}.ratio")
            affect_rot = cmds.getAttr(f"{node}.affectRotation")
            local_append = False
            if cmds.attributeQuery("localAppend", node=node, exists=True):
                local_append = bool(cmds.getAttr(f"{node}.localAppend"))
            attr_map = {}
            if affect_rot and target_joint in rotate_dsts:
                attr_map.update({
                    "rotateX": "baseRotateX",
                    "rotateY": "baseRotateY",
                    "rotateZ": "baseRotateZ",
                })
            affect_translate = False
            if cmds.attributeQuery("affectTranslation", node=node, exists=True):
                affect_translate = bool(cmds.getAttr(f"{node}.affectTranslation"))
            if target_joint in translate_dsts:
                attr_map.update({
                    "translateX": "baseTranslateX",
                    "translateY": "baseTranslateY",
                    "translateZ": "baseTranslateZ",
                })
            result[target_joint] = {
                "node": node,
                "source_joint": source_joint,
                "source_append_node": source_append_node,
                "ratio": ratio,
                "affect_rotation": affect_rot,
                "affect_translation": affect_translate,
                "local_append": local_append,
                "source_rotation_is_mmd": bool(source_append_node and not local_append),
                "source_joint_orient": (
                    om.MQuaternion()
                    if source_append_node and not local_append
                    else VmdConverter._joint_orient_quat_from_joint(source_joint)
                ),
                "target_joint_orient": VmdConverter._joint_orient_quat_from_joint(target_joint),
                "attr_map": attr_map,
            }
        return result

    @staticmethod
    def _get_or_expand_runtime_channel(
        ch_dict: Dict[str, Optional[om.MDoubleArray]],
        st_dict: Dict[str, dict],
        attr: str,
        n_frames: int,
    ) -> Optional[om.MDoubleArray]:
        arr = ch_dict.get(attr)
        if arr is not None:
            return arr
        state = st_dict.get(attr, {})
        if state.get("is_static") and state.get("first") is not None:
            return om.MDoubleArray(n_frames, float(state["first"]))
        return None

    @staticmethod
    def _joint_orient_quat_from_joint(joint: str) -> om.MQuaternion:
        """Return jointOrient as a quaternion, or identity when unavailable."""
        try:
            jo = cmds.getAttr(f"{joint}.jointOrient")[0]
        except Exception:
            return om.MQuaternion()
        if not any(abs(v) > 1e-8 for v in jo):
            return om.MQuaternion()
        return om.MEulerRotation(
            math.radians(float(jo[0])),
            math.radians(float(jo[1])),
            math.radians(float(jo[2])),
        ).asQuaternion()

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
        """bake の final rotation から grant 寄与を除去し、bone own rotation を計算。

        mmdAppend composes animation deltas in joint.rotate space. jointOrient is
        the bind/rest axis and must not contribute to a REST grant.
        """
        n = len(target_rx)
        own_rx = om.MDoubleArray(n, 0.0)
        own_ry = om.MDoubleArray(n, 0.0)
        own_rz = om.MDoubleArray(n, 0.0)
        grant_rx = om.MDoubleArray(n, 0.0)
        grant_ry = om.MDoubleArray(n, 0.0)
        grant_rz = om.MDoubleArray(n, 0.0)
        identity = om.MQuaternion()

        for i in range(n):
            src_euler = om.MEulerRotation(source_rx[i], source_ry[i], source_rz[i])
            src_q = src_euler.asQuaternion()
            grant_q = om.MQuaternion.slerp(identity, src_q, ratio)
            grant_inv = grant_q.conjugate()
            grant_euler = grant_q.asEulerRotation()
            grant_rx[i] = grant_euler.x
            grant_ry[i] = grant_euler.y
            grant_rz[i] = grant_euler.z

            final_euler = om.MEulerRotation(target_rx[i], target_ry[i], target_rz[i])
            final_q = final_euler.asQuaternion()

            own_q = final_q * grant_inv
            own_euler = own_q.asEulerRotation()
            own_rx[i] = own_euler.x
            own_ry[i] = own_euler.y
            own_rz[i] = own_euler.z

        return (own_rx, own_ry, own_rz), (grant_rx, grant_ry, grant_rz)

    def _decompose_append_rotations_for_scene(
        self,
        joint_channel_values: Dict[str, Dict[str, Optional[om.MDoubleArray]]],
        joint_channel_static: Dict[str, Dict[str, dict]],
        append_info: Dict[str, dict],
        n_frames: int,
    ) -> Dict[str, Dict[str, om.MDoubleArray]]:
        """append graph の依存に沿って final rotation を own rotation へ分解する。"""
        resolved: Dict[str, Optional[Dict[str, Tuple[om.MDoubleArray, om.MDoubleArray, om.MDoubleArray]]]] = {}
        resolving = set()

        def _final_rotation(joint: str) -> Optional[Tuple[om.MDoubleArray, om.MDoubleArray, om.MDoubleArray]]:
            channels = joint_channel_values.get(joint, {})
            static = joint_channel_static.get(joint, {})
            rx = self._get_or_expand_runtime_channel(channels, static, "rotateX", n_frames)
            ry = self._get_or_expand_runtime_channel(channels, static, "rotateY", n_frames)
            rz = self._get_or_expand_runtime_channel(channels, static, "rotateZ", n_frames)
            if rx is None or ry is None or rz is None:
                return None
            return rx, ry, rz

        def _resolve(joint: str) -> Optional[Dict[str, Tuple[om.MDoubleArray, om.MDoubleArray, om.MDoubleArray]]]:
            if joint in resolved:
                return resolved[joint]
            if joint in resolving:
                self.logger.warning(f"append rotation cycle detected at {joint}; using baked rotation fallback")
                resolved[joint] = None
                return None

            info = append_info.get(joint)
            if not info or not info.get("affect_rotation") or not info.get("source_joint"):
                resolved[joint] = None
                return None

            final_rotation = _final_rotation(joint)
            if final_rotation is None:
                resolved[joint] = None
                return None

            resolving.add(joint)
            source_joint = info["source_joint"]
            source_info = append_info.get(source_joint)
            source_rotation = _final_rotation(source_joint)
            source_resolved = _resolve(source_joint) if source_info else None
            source_rotation_is_mmd = bool(info.get("source_rotation_is_mmd", False))
            if source_resolved:
                source_rotation = (
                    source_resolved["own"]
                    if info.get("local_append")
                    else source_resolved["grant"]
                )
                source_rotation_is_mmd = not info.get("local_append")

            resolving.remove(joint)
            if source_rotation is None:
                resolved[joint] = None
                return None

            own_rotation, grant_rotation = self._decompose_append_own_rotation(
                final_rotation[0], final_rotation[1], final_rotation[2],
                source_rotation[0], source_rotation[1], source_rotation[2],
                info["ratio"],
                target_joint_orient=info.get("target_joint_orient"),
                source_joint_orient=info.get("source_joint_orient"),
                source_rotation_is_mmd=source_rotation_is_mmd,
            )
            resolved[joint] = {"own": own_rotation, "grant": grant_rotation}
            return resolved[joint]

        decomposed = {}
        for joint in append_info:
            state = _resolve(joint)
            if state:
                own_rx, own_ry, own_rz = state["own"]
                decomposed[joint] = {
                    "rotateX": own_rx,
                    "rotateY": own_ry,
                    "rotateZ": own_rz,
                }
        return decomposed

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
        n = len(target_tx)
        own_tx = om.MDoubleArray(n, 0.0)
        own_ty = om.MDoubleArray(n, 0.0)
        own_tz = om.MDoubleArray(n, 0.0)
        grant_tx = om.MDoubleArray(n, 0.0)
        grant_ty = om.MDoubleArray(n, 0.0)
        grant_tz = om.MDoubleArray(n, 0.0)

        for i in range(n):
            gx = source_tx[i] * ratio
            gy = source_ty[i] * ratio
            gz = source_tz[i] * ratio
            grant_tx[i] = gx
            grant_ty[i] = gy
            grant_tz[i] = gz
            own_tx[i] = target_tx[i] - gx
            own_ty[i] = target_ty[i] - gy
            own_tz[i] = target_tz[i] - gz

        return (own_tx, own_ty, own_tz), (grant_tx, grant_ty, grant_tz)

    def _decompose_append_translations_for_scene(
        self,
        joint_channel_values: Dict[str, Dict[str, Optional[om.MDoubleArray]]],
        joint_channel_static: Dict[str, Dict[str, dict]],
        append_info: Dict[str, dict],
        n_frames: int,
    ) -> Dict[str, Dict[str, om.MDoubleArray]]:
        """append graph の依存に沿って final translation を own translation へ分解する。"""
        resolved: Dict[str, Optional[Dict[str, Tuple[om.MDoubleArray, om.MDoubleArray, om.MDoubleArray]]]] = {}
        resolving = set()

        def _final_translation(joint: str) -> Optional[Tuple[om.MDoubleArray, om.MDoubleArray, om.MDoubleArray]]:
            channels = joint_channel_values.get(joint, {})
            static = joint_channel_static.get(joint, {})
            tx = self._get_or_expand_runtime_channel(channels, static, "translateX", n_frames)
            ty = self._get_or_expand_runtime_channel(channels, static, "translateY", n_frames)
            tz = self._get_or_expand_runtime_channel(channels, static, "translateZ", n_frames)
            if tx is None or ty is None or tz is None:
                return None
            return tx, ty, tz

        def _rest_translation(joint: str) -> Tuple[float, float, float]:
            info = append_info.get(joint)
            if info:
                try:
                    return tuple(float(v) for v in cmds.getAttr(f"{info['node']}.baseTranslate")[0])
                except Exception:
                    pass
            try:
                return tuple(float(v) for v in cmds.getAttr(f"{joint}.translate")[0])
            except Exception:
                return (0.0, 0.0, 0.0)

        def _subtract_rest(
            values: Tuple[om.MDoubleArray, om.MDoubleArray, om.MDoubleArray],
            rest: Tuple[float, float, float],
        ) -> Tuple[om.MDoubleArray, om.MDoubleArray, om.MDoubleArray]:
            tx, ty, tz = values
            out_x = om.MDoubleArray(n_frames, 0.0)
            out_y = om.MDoubleArray(n_frames, 0.0)
            out_z = om.MDoubleArray(n_frames, 0.0)
            for i in range(n_frames):
                out_x[i] = tx[i] - rest[0]
                out_y[i] = ty[i] - rest[1]
                out_z[i] = tz[i] - rest[2]
            return out_x, out_y, out_z

        def _resolve(joint: str) -> Optional[Dict[str, Tuple[om.MDoubleArray, om.MDoubleArray, om.MDoubleArray]]]:
            if joint in resolved:
                return resolved[joint]
            if joint in resolving:
                self.logger.warning(f"append translation cycle detected at {joint}; using baked translation fallback")
                resolved[joint] = None
                return None

            info = append_info.get(joint)
            if not info or not info.get("affect_translation") or not info.get("source_joint"):
                resolved[joint] = None
                return None

            final_translation = _final_translation(joint)
            if final_translation is None:
                resolved[joint] = None
                return None

            resolving.add(joint)
            source_joint = info["source_joint"]
            source_info = append_info.get(source_joint)
            source_translation = _final_translation(source_joint)
            source_resolved = _resolve(source_joint) if source_info else None
            if source_resolved:
                if info.get("local_append"):
                    if source_translation is not None:
                        source_translation = _subtract_rest(source_translation, _rest_translation(source_joint))
                else:
                    source_translation = source_resolved["grant"]
            elif source_translation is not None:
                source_translation = _subtract_rest(source_translation, _rest_translation(source_joint))

            resolving.remove(joint)
            if source_translation is None:
                resolved[joint] = None
                return None

            own_translation, grant_translation = self._decompose_append_own_translation(
                final_translation[0], final_translation[1], final_translation[2],
                source_translation[0], source_translation[1], source_translation[2],
                info["ratio"],
            )
            resolved[joint] = {"own": own_translation, "grant": grant_translation}
            return resolved[joint]

        decomposed = {}
        for joint in append_info:
            state = _resolve(joint)
            if state:
                own_tx, own_ty, own_tz = state["own"]
                decomposed[joint] = {
                    "translateX": own_tx,
                    "translateY": own_ty,
                    "translateZ": own_tz,
                }
        return decomposed

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
        keyed_channels = 0
        skipped_static_channels = 0
        total_channels = 0

        append_info = self._collect_append_info()
        ik_passthrough_info = self._collect_mmd_ik_passthrough_info()
        decomposed_rotations = self._decompose_append_rotations_for_scene(
            joint_channel_values,
            joint_channel_static,
            append_info,
            len(baked_frames),
        )
        decomposed_translations = self._decompose_append_translations_for_scene(
            joint_channel_values,
            joint_channel_static,
            append_info,
            len(baked_frames),
        )

        for joint, channels in joint_channel_values.items():
            total_channels += len(channels)
            try:
                ik_info = ik_passthrough_info.get(joint)
                if ik_info:
                    channels = dict(channels)
                    redirected = self._key_mmd_ik_passthrough_rotation(
                        ik_info,
                        channels,
                        joint_channel_static.get(joint, {}),
                        bake_times,
                        baked_frames,
                    )
                    if redirected:
                        keyed_channels += redirected
                        for attr in ("rotateX", "rotateY", "rotateZ"):
                            channels.pop(attr, None)
                    if not channels:
                        continue

                target_static = joint_channel_static.get(joint, {})
                info = append_info.get(joint)
                if info and info["attr_map"]:
                    append_node = info["node"]
                    attr_map = dict(info["attr_map"])
                    decomposed_rotation_channels = decomposed_rotations.get(joint, {})
                    decomposed_translation_channels = decomposed_translations.get(joint, {})
                    decomposed_channels = dict(decomposed_rotation_channels)
                    decomposed_channels.update(decomposed_translation_channels)

                    if info["affect_rotation"] and not decomposed_rotation_channels:
                        attr_map.pop("rotateX", None)
                        attr_map.pop("rotateY", None)
                        attr_map.pop("rotateZ", None)
                    if info["affect_translation"] and not decomposed_translation_channels:
                        attr_map.pop("translateX", None)
                        attr_map.pop("translateY", None)
                        attr_map.pop("translateZ", None)

                    redirected_channels = {}
                    redirected_static = {}
                    passthrough_channels = {}
                    passthrough_static = {}
                    for attr, values in channels.items():
                        new_attr = attr_map.get(attr)
                        if new_attr:
                            redirected_channels[new_attr] = decomposed_channels.get(attr, values)
                            if attr not in decomposed_channels:
                                orig_state = target_static.get(attr)
                                if orig_state:
                                    redirected_static[new_attr] = orig_state
                        else:
                            passthrough_channels[attr] = values
                            orig_state = target_static.get(attr)
                            if orig_state:
                                passthrough_static[attr] = orig_state

                    if redirected_channels:
                        keyed, skipped = self._batch_create_and_key_curve_arrays(
                            append_node,
                            redirected_channels,
                            redirected_static,
                            bake_times,
                            baked_frames,
                        )
                        keyed_channels += keyed
                        skipped_static_channels += skipped

                    if passthrough_channels:
                        keyed, skipped = self._batch_create_and_key_curve_arrays(
                            joint,
                            passthrough_channels,
                            passthrough_static,
                            bake_times,
                            baked_frames,
                        )
                        keyed_channels += keyed
                        skipped_static_channels += skipped

                    if redirected_channels or passthrough_channels:
                        continue

                keyed, skipped = self._batch_create_and_key_curve_arrays(
                    joint,
                    channels,
                    target_static,
                    bake_times,
                    baked_frames,
                )
                keyed_channels += keyed
                skipped_static_channels += skipped
            except Exception as e:
                self.logger.debug(f"batch array keying error for {joint}: {e}")

        self.logger.info(
            "runtime joint channel pruning: "
            f"keyed={keyed_channels}, skipped_static={skipped_static_channels}, "
            f"total={total_channels}"
        )

        self._bake_morph_weight_cache_from_runtime(morph_cache, pmx_morph_names)

        self.logger.info(f"Applied runtime cache: keyed {len(baked_frames)} frames")
        return None

    @staticmethod
    def _collect_mmd_ik_passthrough_info() -> Dict[str, Dict[str, Union[str, int]]]:
        """Return joints driven by mmdCcdIk outputRotate and their link indices.

        During runtime-live VMD apply the final pose already includes MMD IK.
        For IK-driven joints, write the final rotation into the IK node input
        and key ``enabled`` off so the existing output connection simply passes
        the keyed rotation through.
        """
        result: Dict[str, Dict[str, Union[str, int]]] = {}
        for node in _ls_mmd_ccd_ik_nodes():
            link_slots = []
            try:
                cfg = json.loads(cmds.getAttr(f"{node}.chainJson") or "{}")
                link_slots = [int(link.get("bone_slot", -1)) for link in cfg.get("links", [])]
            except Exception:
                link_slots = []
            for dest in cmds.listConnections(f"{node}.outputRotate", s=False, d=True, p=True) or []:
                if not dest.endswith(".rotate"):
                    continue
                joint = dest.rsplit(".", 1)[0]
                source_plugs = cmds.listConnections(dest, s=True, d=False, p=True) or []
                link_index = None
                prefix = f"{node}.outputRotate["
                for source in source_plugs:
                    if source.startswith(prefix):
                        try:
                            link_index = int(source[len(prefix):].split("]", 1)[0])
                        except (TypeError, ValueError):
                            link_index = None
                        break
                if link_index is None:
                    continue
                input_slot = link_slots[link_index] if link_index < len(link_slots) else link_index
                info = {"node": node, "link_index": link_index, "input_slot": input_slot}
                result[joint] = info
                short_name = joint.rsplit("|", 1)[-1]
                result[short_name] = info
                for long_name in cmds.ls(joint, long=True) or []:
                    result[long_name] = info
        return result

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
        node = str(ik_info.get("node", ""))
        input_slot = int(ik_info.get("input_slot", -1))
        if not node or input_slot < 0 or not cmds.objExists(node):
            return 0

        n_frames = len(baked_frames)
        rx = self._get_or_expand_runtime_channel(channels, static_state, "rotateX", n_frames)
        ry = self._get_or_expand_runtime_channel(channels, static_state, "rotateY", n_frames)
        rz = self._get_or_expand_runtime_channel(channels, static_state, "rotateZ", n_frames)
        if rx is None or ry is None or rz is None:
            return 0
        if len(rx) != n_frames or len(ry) != n_frames or len(rz) != n_frames:
            return 0

        axis_attrs = (
            f"inputRotate[{input_slot}].inputRotateElementX",
            f"inputRotate[{input_slot}].inputRotateElementY",
            f"inputRotate[{input_slot}].inputRotateElementZ",
        )
        for axis_attr in axis_attrs:
            plug_path = f"{node}.{axis_attr}"
            for source in cmds.listConnections(plug_path, s=True, d=False, p=True) or []:
                try:
                    cmds.disconnectAttr(source, plug_path)
                except Exception:
                    pass

        tangent = oma.MFnAnimCurve.kTangentLinear
        keyed = 0
        for axis_attr, values in zip(axis_attrs, (rx, ry, rz)):
            plug_path = f"{node}.{axis_attr}"
            try:
                sel = om.MSelectionList()
                sel.add(plug_path)
                plug = sel.getPlug(0)
                curve = oma.MFnAnimCurve()
                curve.create(plug)
                curve.addKeys(bake_times, values, tangent, tangent, False)
                keyed += 1
            except Exception as exc:
                self.logger.debug(f"addKeys failed for {plug_path}, fallback: {exc}")
                for index, frame in enumerate(baked_frames):
                    try:
                        cmds.setKeyframe(plug_path, time=frame, value=math.degrees(float(values[index])))
                    except Exception as exc2:
                        self.logger.debug(f"failed to key {plug_path} at frame {frame}: {exc2}")
                        break
                else:
                    keyed += 1

        if disable_solver:
            try:
                for source in cmds.listConnections(f"{node}.enabled", s=True, d=False, p=True) or []:
                    try:
                        cmds.disconnectAttr(source, f"{node}.enabled")
                    except Exception:
                        pass
                cmds.setAttr(f"{node}.enabled", False)
                try:
                    sel = om.MSelectionList()
                    sel.add(f"{node}.enabled")
                    plug = sel.getPlug(0)
                    curve = oma.MFnAnimCurve()
                    curve.create(plug)
                    en_values = om.MDoubleArray([0.0] * n_frames)
                    curve.addKeys(bake_times, en_values, tangent, tangent, False)
                except Exception:
                    for frame in baked_frames:
                        cmds.setKeyframe(node, attribute="enabled", time=frame, value=0.0)
                keyed += 1
            except Exception as exc:
                self.logger.debug(f"failed to key {node}.enabled off for runtime live apply: {exc}")

        return keyed

    def _apply_runtime_cache_to_scene(
        self, runtime_cache: List[dict], pmx_morph_names: List[str]
    ):
        """キャッシュ済みフレームデータから、ジョイントの translate/rotate を API 2.0 で一括キーイング。

        モーフは既存パスで後処理（評価ループ外）。これにより内側ループの cmds.xform/setKeyframe を排除。
        """
        if not runtime_cache:
            return

        # ジョイント: per-joint でチャンネル別サンプルをまとめ、一括登録
        if self.bone_index_to_joint:
            per_joint_channels: Dict[str, Dict[str, List[Tuple[float, float]]]] = {}

            for fd in runtime_cache:
                f = fd["frame"]
                for bidx, (tx, ty, tz, rx, ry, rz) in fd.get("bone_locals", {}).items():
                    jname = self.bone_index_to_joint.get(bidx)
                    if not jname:
                        continue
                    tx, ty, tz = self._scale_motion_translate_from_bind(jname, tx, ty, tz)
                    if jname not in per_joint_channels:
                        per_joint_channels[jname] = {
                            "translateX": [],
                            "translateY": [],
                            "translateZ": [],
                            "rotateX": [],
                            "rotateY": [],
                            "rotateZ": [],
                        }
                    chans = per_joint_channels[jname]
                    chans["translateX"].append((f, tx))
                    chans["translateY"].append((f, ty))
                    chans["translateZ"].append((f, tz))
                    # 回転サンプルは addKeys のためラジアンで保持
                    chans["rotateX"].append((f, math.radians(rx)))
                    chans["rotateY"].append((f, math.radians(ry)))
                    chans["rotateZ"].append((f, math.radians(rz)))

            total_channels = 0
            keyed_channels = 0
            skipped_static_channels = 0
            for jname, chans in per_joint_channels.items():
                try:
                    dynamic_chans = {}
                    for attr, samples in chans.items():
                        total_channels += 1
                        if self._is_static_channel(samples):
                            skipped_static_channels += 1
                            if samples:
                                try:
                                    value = float(samples[0][1])
                                    if "rotate" in attr:
                                        value = math.degrees(value)
                                    cmds.setAttr(f"{jname}.{attr}", value)
                                except Exception:
                                    pass
                            continue
                        dynamic_chans[attr] = samples

                    if dynamic_chans:
                        keyed_channels += len(dynamic_chans)
                        self._batch_create_and_key_curves(jname, dynamic_chans)
                except Exception as e:
                    self.logger.debug(f"batch keying error for {jname} (will have used fallbacks): {e}")
            self.logger.info(
                "runtime joint channel pruning: "
                f"keyed={keyed_channels}, skipped_static={skipped_static_channels}, "
                f"total={total_channels}"
            )

        morph_cache = [
            (int(fd["frame"]), list(fd.get("morph_weights", [])))
            for fd in runtime_cache
        ]
        self._bake_morph_weight_cache_from_runtime(morph_cache, pmx_morph_names)

        self.logger.info(f"Applied runtime cache: keyed {len(runtime_cache)} frames")

    def _scale_motion_translate_from_bind(
        self,
        joint: str,
        tx: float,
        ty: float,
        tz: float,
    ) -> Tuple[float, float, float]:
        """Scale a local translate sample as bind pose plus motion delta."""
        if self.motion_scale == 1.0:
            return float(tx), float(ty), float(tz)
        bind = self._bone_bind_poses.get(joint, (0.0, 0.0, 0.0))
        bx, by, bz = float(bind[0]), float(bind[1]), float(bind[2])
        return (
            bx + (float(tx) - bx) * self.motion_scale,
            by + (float(ty) - by) * self.motion_scale,
            bz + (float(tz) - bz) * self.motion_scale,
        )

    @staticmethod
    def _is_static_channel(samples: List[Tuple[float, float]], tolerance: float = 1e-10) -> bool:
        """全サンプル値が同一なら True を返す。"""
        if len(samples) <= 1:
            return True
        first = float(samples[0][1])
        return all(abs(float(value) - first) <= tolerance for _, value in samples[1:])

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
        if not world_matrices or not self.bone_index_to_joint:
            # フォールバック: 最低限キーフレームだけ打つ（評価自体は runtime で済んでいる）
            for vmd_bone_name, maya_joint in self.bone_name_mapping.items():
                if cmds.objExists(maya_joint):
                    try:
                        for attr in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"):
                            key_args = {
                                "attribute": attr,
                                "time": frame,
                            }
                            if self.anim_layer:
                                key_args["animLayer"] = self.anim_layer
                            cmds.setKeyframe(maya_joint, **key_args)
                    except Exception:
                        pass
            return

        # PMXボーンindex昇順で適用（親→子）。bone_name_mapping の挿入順(DAG DFS順やその他)に依存せず、
        # 常に低index(親)を先に xform して子の world 解決を正しくする。
        for bone_idx in sorted(self.bone_index_to_joint.keys()):
            maya_joint = self.bone_index_to_joint[bone_idx]
            if not cmds.objExists(maya_joint):
                continue
            if bone_idx >= len(world_matrices):
                continue

            mmd_mat = world_matrices[bone_idx]  # List[float] of 16, column-major from mmd-anim

            # 簡易 Z flip for MMD (Z forward) -> Maya (Z backward)
            try:
                maya_world = self._convert_mmd_world_matrix_to_maya(mmd_mat)
                cmds.xform(maya_joint, worldSpace=True, matrix=maya_world)

                # 適用後のローカル値をキーフレーム
                for attr in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"):
                    key_args = {
                        "attribute": attr,
                        "time": frame,
                    }
                    if self.anim_layer:
                        key_args["animLayer"] = self.anim_layer
                    cmds.setKeyframe(maya_joint, **key_args)
            except Exception as e:
                self.logger.debug(f"world matrix bake error for {maya_joint} at frame {frame}: {e}")

    @staticmethod
    def _convert_mmd_world_matrix_to_maya(mmd_matrix: list) -> list:
        """
        mmd-anim のワールド行列を Maya の `cmds.xform(..., matrix=...)` 用に変換する。

        mmd-anim の flat matrix は translation を 12, 13, 14 に持つ 16 要素として扱う。
        MMD と Maya の差分は X/Y は同じで Z 方向が反転する座標系変換なので、
        回転 3x3 は S * R * S、translation は t * S を適用する。

        これにより identity は identity のまま保たれ、Z translation だけが反転する。
        """
        if len(mmd_matrix) != 16:
            raise ValueError("mmd_matrix must contain 16 values")

        signs = (1.0, 1.0, -1.0)
        maya_matrix = [float(v) for v in mmd_matrix]

        for row in range(3):
            for col in range(3):
                idx = row * 4 + col
                maya_matrix[idx] = float(mmd_matrix[idx]) * signs[row] * signs[col]

        for col in range(3):
            maya_matrix[12 + col] = float(mmd_matrix[12 + col]) * signs[col]

        return maya_matrix

    def _bake_morph_weights_from_runtime(
        self,
        frame: int,
        morph_weights: list,
        pmx_morph_names: List[str] = None,
    ):
        """runtime から得た PMX morph 順のウェイトを Maya blendShape にベイク"""
        if not morph_weights:
            return

        pmx_morph_names = pmx_morph_names or []
        for index, weight in enumerate(morph_weights):
            if index >= len(pmx_morph_names):
                continue
            morph_name = pmx_morph_names[index]
            mappings = self._iter_morph_mappings(self.morph_name_mapping.get(morph_name))
            if not mappings:
                continue

            for morph_node, weight_attr, _ in mappings:
                try:
                    cmds.setKeyframe(
                        morph_node,
                        attribute=weight_attr,
                        time=frame,
                        value=float(weight),
                    )
                except Exception as e:
                    self.logger.debug(
                        f"runtime morph bake error for {morph_name} at frame {frame}: {e}"
                    )

    def _bake_morph_weight_cache_from_runtime(
        self,
        morph_cache: List[Tuple[float, list]],
        pmx_morph_names: List[str] = None,
    ) -> None:
        """runtime 評価済み morph weight cache を blendShape/network weight へ一括キーイングする。"""
        if not morph_cache:
            return

        pmx_morph_names = pmx_morph_names or []
        samples_by_node: Dict[str, Dict[str, List[Tuple[float, float]]]] = {}
        keyed_morphs = set()
        for frame, morph_weights in morph_cache:
            for index, weight in enumerate(morph_weights):
                if index >= len(pmx_morph_names):
                    continue
                morph_name = pmx_morph_names[index]
                mappings = self._iter_morph_mappings(self.morph_name_mapping.get(morph_name))
                if not mappings:
                    continue
                keyed_morphs.add(morph_name)
                for morph_node, weight_attr, _ in mappings:
                    node_samples = samples_by_node.setdefault(morph_node, {})
                    node_samples.setdefault(weight_attr, []).append((float(frame), float(weight)))

        if not samples_by_node:
            return

        keyed_nodes = 0
        for morph_node, channel_samples in samples_by_node.items():
            try:
                if self._batch_key_scalar_channels(morph_node, channel_samples):
                    keyed_nodes += 1
                    continue
            except Exception as exc:
                self.logger.debug(f"runtime morph batch keying failed for {morph_node}, fallback: {exc}")

            for weight_attr, samples in channel_samples.items():
                for frame, weight in samples:
                    try:
                        cmds.setKeyframe(
                            morph_node,
                            attribute=weight_attr,
                            time=frame,
                            value=float(weight),
                        )
                    except Exception as exc:
                        self.logger.debug(
                            f"runtime morph fallback keying failed for {morph_node}.{weight_attr} at {frame}: {exc}"
                        )

        self.logger.info(
            f"runtime morph batch keying: nodes={keyed_nodes}/{len(samples_by_node)}, morphs={len(keyed_morphs)}"
        )

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
        if not self.anim_layer:
            return

        # オブジェクトをレイヤーに追加
        for obj in objects:
            if cmds.objExists(obj):
                # 各属性をレイヤーに追加
                attrs = [
                    "translateX",
                    "translateY",
                    "translateZ",
                    "rotateX",
                    "rotateY",
                    "rotateZ",
                ]
                for attr in attrs:
                    attr_path = f"{obj}.{attr}"
                    if cmds.attributeQuery(attr, node=obj, exists=True):
                        cmds.animLayer(self.anim_layer, edit=True, attribute=attr_path)

    def _build_name_mappings(self, target_namespace: str = None):
        """ボーン名とモーフ名のマッピングを構築

        Phase 1 拡張: bone_name → joint に加え、
        bone_name → bone_index 、 bone_index → joint も構築する。
        これにより mmd-anim の world_matrices (PMXボーン順) を Maya ジョイントに
        正しく対応づけられる。
        """
        self.logger.info("Building name mapping")

        self.bone_name_to_index: Dict[str, int] = {}
        self.bone_index_to_joint: Dict[int, str] = {}

        # シーン内のジョイントを検索
        if target_namespace:
            joints = maya_utils.list_objects(object_filter=f"{target_namespace}:*", type="joint")
        else:
            joints = maya_utils.list_objects(type="joint")

        # カスタム属性から元のボーン名とインデックスを取得
        for joint in joints:
            if cmds.attributeQuery(ATTR_MMD_BONE_NAME, node=joint, exists=True):
                original_name = cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_NAME}")
                if original_name:
                    self.bone_name_mapping[original_name] = joint

                    # bone index も取得（モデルインポート時に設定済み）
                    if cmds.attributeQuery(ATTR_MMD_BONE_INDEX, node=joint, exists=True):
                        try:
                            idx = cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}")
                            if idx is not None:
                                idx = int(idx)
                                self.bone_name_to_index[original_name] = idx
                                self.bone_index_to_joint[idx] = joint
                        except Exception:
                            pass

        self.logger.info(f"Built {len(self.bone_name_mapping)} bone mappings "
                         f"(index mappings: {len(self.bone_index_to_joint)})")

        # モーフ名マッピングの構築 (for accurate runtime morph bake)
        self._build_morph_mappings()  # 元のメソッドは namespace 引数を取らない

    def _record_bind_poses(self):
        """各ボーンの初期位置（バインドポーズ）を記録"""
        self.logger.info("Recording initial bone positions")

        for vmd_bone_name, maya_joint in self.bone_name_mapping.items():
            try:
                # 現在のtranslate値を取得（これがバインドポーズ）
                translate = cmds.getAttr(f"{maya_joint}.translate")[0]
                self._bone_bind_poses[vmd_bone_name] = translate
            except Exception as e:
                self.logger.warning(f"Failed to get bind pose for {vmd_bone_name}: {str(e)}")

    def _setup_timeline(self, vmd_data: VmdData):
        """タイムラインの設定

        Args:
            vmd_data: パース済みのVMDデータ
        """
        # FPSを設定
        self._set_scene_fps(self.fps)

        # 最大フレーム番号を取得
        max_frame = 0

        # ボーンフレームから最大フレーム取得
        if hasattr(vmd_data, "bone_frames"):
            for frame_data in vmd_data.bone_frames:
                # VmdBoneFrameオブジェクトの場合は属性アクセス、辞書の場合はget
                if hasattr(frame_data, "frame_number"):
                    max_frame = max(max_frame, frame_data.frame_number)
                else:
                    max_frame = max(max_frame, frame_data.get("frame_number", 0))

        if max_frame > 0:
            # タイムラインの範囲を設定
            max_time = self.vmd_frame_to_maya_time(max_frame)
            cmds.playbackOptions(min=0, max=max_time, animationStartTime=0, animationEndTime=max_time)
            self.logger.info(f"Set timeline range: 0 - {max_time}")

    def _convert_bone_animation(self, bone_frames: List) -> bool:
        """ボーンアニメーションを変換

        Args:
            bone_frames: ボーンフレームデータのリスト

        Returns:
            変換が成功した場合True
        """
        # ボーンごとにフレームデータをグループ化
        bone_frame_map: Dict[str, List] = {}

        for frame in bone_frames:
            # VmdBoneFrameオブジェクトの場合は属性アクセス、辞書の場合はget
            if hasattr(frame, "bone_name"):
                bone_name = frame.bone_name
            else:
                bone_name = frame.get("bone_name", "")
            if bone_name not in bone_frame_map:
                bone_frame_map[bone_name] = []
            bone_frame_map[bone_name].append(frame)

        success_count = 0
        total_count = len(bone_frame_map)
        animated_joints = []  # アニメーションを適用したジョイントのリスト
        key_routes = self._build_legacy_bone_key_routes()

        # 各ボーンのアニメーションを設定
        for vmd_bone_name, frames in bone_frame_map.items():
            if vmd_bone_name in self.bone_name_mapping:
                maya_joint = self.bone_name_mapping[vmd_bone_name]

                try:
                    # フレームをフレーム番号でソート
                    frames.sort(key=lambda x: x.frame_number if hasattr(x, "frame_number") else x.get("frame_number", 0))

                    # 位置と回転のキーフレームを設定
                    self._set_bone_keyframes(
                        maya_joint,
                        frames,
                        vmd_bone_name,
                        key_routes.get(maya_joint),
                    )
                    animated_joints.append(maya_joint)
                    success_count += 1

                except Exception as e:
                    self.logger.error(f"Error setting animation for bone '{vmd_bone_name}': {str(e)}")
                    self._failed_bones.add(vmd_bone_name)
            else:
                if vmd_bone_name not in self._failed_bones:
                    self.logger.info(f"Bone '{vmd_bone_name}' not found")
                    self._failed_bones.add(vmd_bone_name)

        # アニメーションレイヤーにジョイントを追加
        # IK link ジョイントは除外 — animLayer が rotate blend node を自動生成し
        # mmdCcdIk solver の outputRotate 接続を上書きするため
        if self.use_animation_layers and self.anim_layer and animated_joints:
            ik_link_joints = self._collect_ik_link_joints()
            append_target_joints = {
                joint
                for joint, route in key_routes.items()
                if route.get("attr_targets")
            }
            layer_joints = [
                j for j in animated_joints
                if j not in ik_link_joints and j not in append_target_joints
            ]
            self._add_objects_to_layer(layer_joints)

        self.logger.info(f"Converted {success_count}/{total_count} bone animations")
        return success_count > 0

    @staticmethod
    def _collect_ik_link_joints() -> dict:
        """mmdCcdIk 出力で rotate が駆動される IK link joint を収集する。

        Returns:
            {joint_name: {"solver": solver_node, "slot": bone_slot}} 辞書。
            bone_slot は chainJson 内の links[i].bone_slot で、solver の
            inputRotate にキーイングするときのインデックスとして使う。
        """
        ik_link_joints: dict = {}
        for node in _ls_mmd_ccd_ik_nodes():
            try:
                raw_chain = cmds.getAttr(f"{node}.chainJson")
                cfg = json.loads(raw_chain) if raw_chain else {}
            except Exception:
                continue

            links = cfg.get("links", [])
            for link_index, link in enumerate(links):
                dests = cmds.listConnections(
                    f"{node}.outputRotate[{link_index}]",
                    s=False,
                    d=True,
                    p=True,
                ) or []
                bone_slot = link.get("bone_slot", link_index)
                for dest in dests:
                    jnt = dest.split(".", 1)[0]
                    info = {"solver": node, "slot": bone_slot}
                    ik_link_joints[jnt] = info
                    try:
                        for long_name in cmds.ls(jnt, long=True) or []:
                            ik_link_joints[long_name] = info
                    except Exception:
                        pass
        return ik_link_joints

    @staticmethod
    def _native_ik_handle_link_joints(handle: str) -> List[str]:
        if not cmds.attributeQuery("mmd_ik_link_joints_json", node=handle, exists=True):
            return []
        try:
            raw = cmds.getAttr(f"{handle}.mmd_ik_link_joints_json") or "[]"
            links = json.loads(raw)
        except Exception:
            return []
        return [j for j in links if isinstance(j, str) and cmds.objExists(j)]

    @staticmethod
    def _node_namespace(node: str) -> str:
        leaf = node.split("|")[-1]
        if ":" not in leaf:
            return ""
        return leaf.rsplit(":", 1)[0].lstrip(":")

    def _collect_ik_nodes_by_bone_name(self, target_namespace: str = None) -> Dict[str, str]:
        """mmdCcdIk ノードを PMX IK ボーン名で引けるように収集する。"""
        nodes: Dict[str, str] = {}
        for node in _ls_mmd_ccd_ik_nodes():
            if target_namespace and self._node_namespace(node) != target_namespace:
                continue
            name = ""
            if cmds.attributeQuery("mmd_ik_bone_name", node=node, exists=True):
                try:
                    name = cmds.getAttr(f"{node}.mmd_ik_bone_name") or ""
                except Exception:
                    name = ""
            if name:
                nodes[name] = node
        return nodes

    def _apply_ik_enabled_animation(self, vmd_data: VmdData, target_namespace: str = None) -> None:
        """VMD の IK 表示/非表示フレームを mmdCcdIk.enabled に反映する。

        PMX import 直後は REST mesh を守るため mmdCcdIk.enabled=False。
        VMD が適用されるときだけ、VMD property frame に従って有効化する。
        property frame がないモデルモーションでは、従来互換として全 IK を
        評価範囲の先頭で有効にする。
        """
        ik_nodes = self._collect_ik_nodes_by_bone_name(target_namespace)
        if not ik_nodes:
            return

        property_frames = sorted(
            list(getattr(vmd_data, "ik_show_hide_frames", []) or []),
            key=lambda f: int(getattr(f, "frame_number", 0)),
        )
        default_nodes = set(ik_nodes.values()) if getattr(vmd_data, "bone_frames", None) else set()

        if property_frames or default_nodes:
            min_frame, _max_frame = self._get_animation_frame_range(vmd_data)
            min_time = self.vmd_frame_to_maya_time(min_frame)
            for node in (ik_nodes.values() if property_frames else default_nodes):
                cmds.setAttr(f"{node}.enabled", True)
                cmds.setKeyframe(node, attribute="enabled", time=min_time, value=1)

        if property_frames:
            keyed = 0
            for frame in property_frames:
                frame_number = int(getattr(frame, "frame_number", 0))
                for ik_name, show_flag in getattr(frame, "ik_states", []) or []:
                    node = ik_nodes.get(ik_name)
                    if not node:
                        continue
                    value = bool(show_flag)
                    cmds.setAttr(f"{node}.enabled", value)
                    cmds.setKeyframe(
                        node,
                        attribute="enabled",
                        time=self.vmd_frame_to_maya_time(frame_number),
                        value=int(value),
                    )
                    keyed += 1
            if keyed:
                self.logger.info(f"Applied {keyed} keys of VMD IK state to mmdCcdIk.enabled")
            return

        if default_nodes:
            self.logger.info(f"No VMD IK state found; set active mmdCcdIk.enabled default ON: {len(default_nodes)} nodes")

    def _build_legacy_bone_key_routes(self) -> Dict[str, dict]:
        """レガシー VMD キーの出力先を joint / rig node へ振り分ける。"""
        append_info = self._collect_append_info()
        ik_link_joints = self._collect_ik_link_joints()
        routes: Dict[str, dict] = {}

        for joint in set(self.bone_name_mapping.values()):
            ik_info = ik_link_joints.get(joint)
            route = {
                "attr_targets": {},
                "skip_rotate": joint in ik_link_joints,
                "ik_solver_rotate": ik_info,
            }
            info = append_info.get(joint)
            if info:
                append_node = info.get("node")
                for src_attr, dst_attr in info.get("attr_map", {}).items():
                    if append_node:
                        route["attr_targets"][src_attr] = (append_node, dst_attr)

            if route["attr_targets"] or route["skip_rotate"] or ik_info:
                routes[joint] = route

        return routes

    def _add_attrs_to_anim_layer(self, node: str, attrs: List[str]):
        """指定属性を現在のアニメーションレイヤーへ追加する。"""
        if not (self.use_animation_layers and self.anim_layer):
            return
        if not cmds.objExists(node):
            return

        for attr in attrs:
            if cmds.attributeQuery(attr, node=node, exists=True):
                cmds.animLayer(self.anim_layer, edit=True, attribute=f"{node}.{attr}")

    @staticmethod
    def _parse_vmd_interpolation(interpolation_bytes):
        """VMD bone interpolation bytes をチャンネル別 Bezier 制御点へ変換する。

        Returns:
            dict: translate_x/y/z と rotation をキーに持つ、正規化済み
                (x1, y1, x2, y2) タプルの辞書。データ不足時は空辞書。
        """
        if not interpolation_bytes or len(interpolation_bytes) < 16:
            return {}

        data = bytes(interpolation_bytes[:16])

        def _norm(value):
            return max(0.0, min(127.0, float(value))) / 127.0

        channels = ("translate_x", "translate_y", "translate_z", "rotation")
        parsed = {}
        for index, channel in enumerate(channels):
            parsed[channel] = (
                _norm(data[index]),
                _norm(data[4 + index]),
                _norm(data[8 + index]),
                _norm(data[12 + index]),
            )
        return parsed

    @staticmethod
    def _vmd_interp_channel_for_attr(attr: str) -> Optional[str]:
        """Maya attribute 名に対応する VMD interpolation channel 名を返す。"""
        if attr == "translateX":
            return "translate_x"
        if attr == "translateY":
            return "translate_y"
        if attr == "translateZ":
            return "translate_z"
        if attr.startswith("rotate") or "inputRotateElement" in attr:
            return "rotation"
        return None

    @staticmethod
    def _parse_vmd_camera_interpolation(interpolation_bytes):
        """VMD camera interpolation bytes をチャンネル別 Bezier 制御点へ変換する。"""
        return parse_vmd_camera_interpolation(interpolation_bytes)

    @staticmethod
    def _is_linear_vmd_interp(points: Tuple[float, float, float, float]) -> bool:
        """VMD Bezier 制御点が線形指定かどうかを判定する。"""
        x1, y1, x2, y2 = points
        return abs(x1 - y1) < 1e-9 and abs(x2 - y2) < 1e-9

    @staticmethod
    def _get_frame_number(frame) -> float:
        """VMD frame object / dict から frame_number を取得する。"""
        if hasattr(frame, "frame_number"):
            return float(frame.frame_number)
        return float(frame.get("frame_number", 0))

    @staticmethod
    def _get_frame_interpolation(frame):
        """VMD frame object / dict から interpolation bytes を取得する。"""
        if hasattr(frame, "interpolation"):
            return frame.interpolation
        return frame.get("interpolation", b"")

    def _query_key_value(self, plug: str, frame_number: float) -> Optional[float]:
        """指定 plug/frame のキー値を取得する。取得できない場合は None。"""
        try:
            values = cmds.keyframe(
                plug,
                query=True,
                time=(frame_number, frame_number),
                valueChange=True,
            )
        except Exception as exc:
            self.logger.debug(f"Failed to query key value for {plug} at {frame_number}: {exc}")
            return None
        if not values:
            return None
        return float(values[0])

    def _apply_vmd_bezier_tangents(
        self,
        joint: str,
        frames: List,
        attrs,
        channel_interp_map: Dict[str, str],
        interpolation_parser=None,
    ):
        """VMD Bezier 補間を Maya weighted tangent として適用する。

        Args:
            joint: デフォルトのキー対象ノード。
            frames: フレーム番号でソート済みの VMD bone frames。
            attrs: source attr 名のリスト、または source attr から
                (target_node, target_attr) への辞書。
            channel_interp_map: source attr から VMD interpolation channel 名への対応。
        """
        if len(frames) < 2:
            return

        if isinstance(attrs, dict):
            attr_targets = attrs
            source_attrs = list(attrs.keys())
        else:
            attr_targets = {attr: (joint, attr) for attr in attrs}
            source_attrs = list(attrs)

        for frame_index in range(len(frames) - 1):
            frame = frames[frame_index]
            next_frame = frames[frame_index + 1]
            frame_number = self._get_frame_number(frame)
            next_frame_number = self._get_frame_number(next_frame)
            frame_time = self.vmd_frame_to_maya_time(frame_number)
            next_frame_time = self.vmd_frame_to_maya_time(next_frame_number)
            dt = next_frame_time - frame_time
            if dt <= 0.0:
                continue

            # VMD の補間バイト列は到着キー側に保存されるため、
            # 区間 frame -> next_frame では next_frame.interpolation を使う。
            parse_interpolation = interpolation_parser or self._parse_vmd_interpolation
            interpolation = parse_interpolation(self._get_frame_interpolation(next_frame))
            if not interpolation:
                continue

            for source_attr in source_attrs:
                channel_name = channel_interp_map.get(source_attr)
                if not channel_name:
                    continue
                points = interpolation.get(channel_name)
                if not points or self._is_linear_vmd_interp(points):
                    continue

                target_node, target_attr = attr_targets.get(source_attr, (joint, source_attr))
                plug = f"{target_node}.{target_attr}"
                value = self._query_key_value(plug, frame_time)
                next_value = self._query_key_value(plug, next_frame_time)
                if value is None or next_value is None:
                    continue

                x1, y1, x2, y2 = points
                dv = next_value - value
                out_dx = dt * x1
                out_dy = dv * y1
                in_dx = dt * (1.0 - x2)
                in_dy = dv * (1.0 - y2)
                out_angle = math.degrees(math.atan2(out_dy, out_dx))
                in_angle = math.degrees(math.atan2(in_dy, in_dx))
                out_weight = math.sqrt((out_dx * out_dx) + (out_dy * out_dy)) / (3.0 * dt)
                in_weight = math.sqrt((in_dx * in_dx) + (in_dy * in_dy)) / (3.0 * dt)

                try:
                    cmds.keyTangent(
                        plug,
                        edit=True,
                        time=(frame_time, frame_time),
                        weightedTangents=True,
                    )
                    cmds.keyTangent(
                        plug,
                        edit=True,
                        time=(frame_time, frame_time),
                        ott="fixed",
                    )
                    cmds.keyTangent(
                        plug,
                        edit=True,
                        time=(frame_time, frame_time),
                        oa=out_angle,
                        ow=out_weight,
                    )
                    cmds.keyTangent(
                        plug,
                        edit=True,
                        time=(next_frame_time, next_frame_time),
                        weightedTangents=True,
                    )
                    cmds.keyTangent(
                        plug,
                        edit=True,
                        time=(next_frame_time, next_frame_time),
                        itt="fixed",
                    )
                    cmds.keyTangent(
                        plug,
                        edit=True,
                        time=(next_frame_time, next_frame_time),
                        ia=in_angle,
                        iw=in_weight,
                    )
                except Exception as exc:
                    self.logger.debug(
                        f"Failed to apply VMD Bezier tangent for {plug} "
                        f"{frame_number}->{next_frame_number}: {exc}"
                    )

    def _set_bone_keyframes(self, joint: str, frames: List, vmd_bone_name: str, key_route: Optional[dict] = None):
        """ボーンのキーフレームを設定

        Args:
            joint: Mayaジョイント名
            frames: フレームデータのリスト
            vmd_bone_name: VMDボーン名
            key_route: append / IK rig 接続に応じたキー出力先情報
        """
        key_route = key_route or {}
        attr_targets = key_route.get("attr_targets", {})
        skip_rotate = bool(key_route.get("skip_rotate"))
        attrs = ["translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"]
        channel_interp_map = {
            attr: self._vmd_interp_channel_for_attr(attr)
            for attr in attrs
            if self._vmd_interp_channel_for_attr(attr)
        }

        keyed_attrs_by_node: Dict[str, List[str]] = {}
        for attr in attrs:
            if skip_rotate and attr.startswith("rotate"):
                continue
            target_node, target_attr = attr_targets.get(attr, (joint, attr))
            keyed_attrs_by_node.setdefault(target_node, []).append(target_attr)

        # IK link ボーンは animLayer を使わない — animLayer が rotate blend node を
        # 自動生成し、mmdCcdIk solver の compound rotate 接続を上書きするため
        use_layer = self.use_animation_layers and self.anim_layer is not None and not skip_rotate
        if use_layer:
            cmds.animLayer(self.anim_layer, edit=True, selected=True)
            for target_node, target_attrs in keyed_attrs_by_node.items():
                self._add_attrs_to_anim_layer(target_node, target_attrs)
        elif self.use_animation_layers and self.anim_layer is not None:
            cmds.animLayer(self.anim_layer, edit=True, selected=False)

        bind_pos = self._bone_bind_poses.get(
            vmd_bone_name,
            self._bone_bind_poses.get(joint, (0.0, 0.0, 0.0)),
        )

        batch_simple_bone = (
            not attr_targets
            and not skip_rotate
            and not key_route.get("ik_solver_rotate")
            and not use_layer
        )
        if batch_simple_bone:
            channel_samples = {attr: [] for attr in attrs}
            for frame in frames:
                if hasattr(frame, "frame_number"):
                    frame_number = frame.frame_number
                    vmd_pos = frame.position
                    rotation_quat = frame.rotation
                else:
                    frame_number = frame.get("frame_number", 0)
                    vmd_pos = frame.get("position", [0, 0, 0])
                    rotation_quat = frame.get("rotation", [0, 0, 0, 1])
                maya_time = self.vmd_frame_to_maya_time(frame_number)

                tx = float(bind_pos[0]) + float(vmd_pos[0]) * self.motion_scale
                ty = float(bind_pos[1]) + float(vmd_pos[1]) * self.motion_scale
                tz = float(bind_pos[2]) - float(vmd_pos[2]) * self.motion_scale
                rx, ry, rz = self._convert_vmd_quat_to_joint_rotate(joint, *rotation_quat)
                values = {
                    "translateX": tx,
                    "translateY": ty,
                    "translateZ": tz,
                    "rotateX": rx,
                    "rotateY": ry,
                    "rotateZ": rz,
                }
                for attr, value in values.items():
                    channel_samples[attr].append((maya_time, float(value)))

            if self._batch_key_scalar_channels(joint, channel_samples):
                if self.use_quaternion_interpolation:
                    try:
                        cmds.scriptEditorInfo(suppressWarnings=True)
                        cmds.rotationInterpolation(
                            f"{joint}.rotateX",
                            f"{joint}.rotateY",
                            f"{joint}.rotateZ",
                            convert="quaternionSlerp",
                        )
                    except Exception as e:
                        self.logger.warning(f"Failed to apply quaternion interpolation to {joint}: {str(e)}")
                    finally:
                        cmds.scriptEditorInfo(suppressWarnings=False)
                tangent_attrs = attrs
                if self.use_quaternion_interpolation:
                    tangent_attrs = [a for a in attrs if not a.startswith("rotate")]
                self._apply_vmd_bezier_tangents(joint, frames, tangent_attrs, channel_interp_map)
                return

            self.logger.debug(f"legacy bone batch keying produced no keys for {joint}; using setKeyframe fallback")

        for frame in frames:
            if hasattr(frame, "frame_number"):
                frame_number = frame.frame_number
                vmd_pos = frame.position
                rotation_quat = frame.rotation
            else:
                frame_number = frame.get("frame_number", 0)
                vmd_pos = frame.get("position", [0, 0, 0])
                rotation_quat = frame.get("rotation", [0, 0, 0, 1])
            maya_time = self.vmd_frame_to_maya_time(frame_number)

            tx = float(bind_pos[0]) + float(vmd_pos[0]) * self.motion_scale
            ty = float(bind_pos[1]) + float(vmd_pos[1]) * self.motion_scale
            tz = float(bind_pos[2]) - float(vmd_pos[2]) * self.motion_scale

            values = {
                "translateX": tx,
                "translateY": ty,
                "translateZ": tz,
            }
            if not skip_rotate:
                rx, ry, rz = self._convert_vmd_quat_to_joint_rotate(joint, *rotation_quat)
                values["rotateX"] = rx
                values["rotateY"] = ry
                values["rotateZ"] = rz

            for attr, value in values.items():
                target_node, target_attr = attr_targets.get(attr, (joint, attr))
                key_args = {
                    "attribute": target_attr,
                    "time": maya_time,
                    "value": float(value),
                }
                if use_layer:
                    key_args["animLayer"] = self.anim_layer
                cmds.setKeyframe(target_node, **key_args)

        # IK link bone: VMD 回転を solver.inputRotate にキーイング
        # joint.rotate は solver.outputRotate が駆動するので直接キーできないが、
        # solver の inputRotate に VMD の事前解決済み回転を渡すことで
        # CCD IK の base_rotation として使われ、正しい解に収束する
        ik_info = key_route.get("ik_solver_rotate") if key_route else None
        if ik_info:
            solver_node = ik_info.get("solver")
            slot = ik_info.get("slot")
            if not solver_node or slot is None:
                return
            ir_attrs = [
                f"inputRotate[{slot}].inputRotateElementX",
                f"inputRotate[{slot}].inputRotateElementY",
                f"inputRotate[{slot}].inputRotateElementZ",
            ]
            for frame in frames:
                if hasattr(frame, "frame_number"):
                    fn = frame.frame_number
                    rq = frame.rotation
                else:
                    fn = frame.get("frame_number", 0)
                    rq = frame.get("rotation", [0, 0, 0, 1])
                maya_time = self.vmd_frame_to_maya_time(fn)
                rx, ry, rz = self._convert_vmd_quat_to_joint_rotate(joint, *rq)
                for attr, val in zip(ir_attrs, [rx, ry, rz]):
                    cmds.setKeyframe(f"{solver_node}.{attr}", time=maya_time, value=val)

        # Quaternion補間を適用（rotate が joint 自身に直接キーされている場合のみ）
        rotate_redirected = any(
            attr_targets.get(a, (joint, a))[0] != joint
            for a in ("rotateX", "rotateY", "rotateZ")
        )
        if self.use_quaternion_interpolation and not skip_rotate and not rotate_redirected:
            try:
                cmds.scriptEditorInfo(suppressWarnings=True)
                cmds.rotationInterpolation(
                    f"{joint}.rotateX",
                    f"{joint}.rotateY",
                    f"{joint}.rotateZ",
                    convert="quaternionSlerp",
                )
            except Exception as e:
                self.logger.warning(f"Failed to apply quaternion interpolation to {joint}: {str(e)}")
            finally:
                cmds.scriptEditorInfo(suppressWarnings=False)

        skip_rotate_tangent = skip_rotate or (
            self.use_quaternion_interpolation and not rotate_redirected
        )
        tangent_targets = {
            attr: attr_targets.get(attr, (joint, attr))
            for attr in attrs
            if not (skip_rotate_tangent and attr.startswith("rotate"))
        }
        self._apply_vmd_bezier_tangents(joint, frames, tangent_targets, channel_interp_map)

        if ik_info and solver_node and slot is not None:
            solver_tangent_targets = {
                "rotateX": (solver_node, f"inputRotate[{slot}].inputRotateElementX"),
                "rotateY": (solver_node, f"inputRotate[{slot}].inputRotateElementY"),
                "rotateZ": (solver_node, f"inputRotate[{slot}].inputRotateElementZ"),
            }
            self._apply_vmd_bezier_tangents(joint, frames, solver_tangent_targets, channel_interp_map)

    def _get_joint_orient_cache(self, joint_name):
        """joint の jointOrient quaternion と rotateOrder をキャッシュ付きで取得する。"""
        if not hasattr(self, "_joint_orient_cache"):
            self._joint_orient_cache = {}
        cached = self._joint_orient_cache.get(joint_name)
        if cached is not None:
            return cached

        joint_orient = cmds.getAttr(f"{joint_name}.jointOrient")[0]
        rotate_order = int(cmds.getAttr(f"{joint_name}.rotateOrder"))

        if any(abs(v) > 1e-8 for v in joint_orient):
            q_jo = om.MEulerRotation(
                math.radians(joint_orient[0]),
                math.radians(joint_orient[1]),
                math.radians(joint_orient[2]),
            ).asQuaternion()
        else:
            q_jo = None

        rotate_axis = cmds.getAttr(f"{joint_name}.rotateAxis")[0]
        if any(abs(v) > 1e-8 for v in rotate_axis):
            self.logger.warning(
                f"{joint_name} has non-zero rotateAxis ({rotate_axis})."
                "Legacy path does not support rotateAxis; rotation accuracy may be reduced"
            )

        result = (q_jo, rotate_order)
        self._joint_orient_cache[joint_name] = result
        return result

    def _convert_vmd_quat_to_joint_rotate(self, joint_name, qx, qy, qz, qw):
        """VMD quaternion を Maya joint.rotate の Euler 角（度）へ変換する。"""
        q_maya = om.MQuaternion(-float(qx), -float(qy), float(qz), float(qw))

        q_jo, rotate_order = self._get_joint_orient_cache(joint_name)
        q_rotate = self._convert_vmd_quat_to_bind_space_rotate(joint_name, q_maya, q_jo)

        euler = q_rotate.asEulerRotation()
        euler.reorderIt(rotate_order)
        return (math.degrees(euler.x), math.degrees(euler.y), math.degrees(euler.z))

    def _convert_vmd_quat_to_bind_space_rotate(
        self,
        joint_name: str,
        q_maya: om.MQuaternion,
        q_jo: Optional[om.MQuaternion],
    ) -> om.MQuaternion:
        """Convert a sparse VMD local rotation into this joint's JO-aware rotate space."""
        bone_index = None
        for idx, joint in getattr(self, "bone_index_to_joint", {}).items():
            if joint == joint_name:
                bone_index = idx
                break
        if bone_index is None:
            if q_jo is not None:
                return q_jo * q_maya * q_jo.inverse()
            return q_maya

        if not hasattr(self, "_runtime_bind_world_matrices"):
            try:
                self._build_runtime_bind_world_maps()
            except Exception:
                pass

        bind_world = getattr(self, "_runtime_bind_world_matrices", {}).get(bone_index)
        bind_no_orient = getattr(self, "_runtime_no_orient_bind_world_matrices", {}).get(bone_index)
        if bind_world is None or bind_no_orient is None:
            if q_jo is not None:
                return q_jo * q_maya * q_jo.inverse()
            return q_maya

        parent_index = getattr(self, "_bone_parent_map", {}).get(bone_index)
        parent_bind_world = getattr(self, "_runtime_bind_world_matrices", {}).get(parent_index, om.MMatrix())
        parent_bind_no_orient = getattr(self, "_runtime_no_orient_bind_world_matrices", {}).get(parent_index, om.MMatrix())

        try:
            no_orient_local = bind_no_orient * parent_bind_no_orient.inverse()
            local_translation = om.MTransformationMatrix(no_orient_local).translation(om.MSpace.kTransform)
            local_tfm = om.MTransformationMatrix()
            local_tfm.setTranslation(local_translation, om.MSpace.kTransform)
            local_tfm.setRotation(q_maya)
            local_no_orient = local_tfm.asMatrix()
            local_total = (
                bind_world
                * bind_no_orient.inverse()
                * local_no_orient
                * parent_bind_no_orient
                * parent_bind_world.inverse()
            )
            q_total = om.MTransformationMatrix(local_total).rotation(asQuaternion=True)
            return q_total * q_jo.inverse() if q_jo is not None else q_total
        except Exception:
            if q_jo is not None:
                return q_jo * q_maya * q_jo.inverse()
            return q_maya

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
        existing = cmds.ls(f"*.{ATTR_MMD_CAMERA}", objectsOnly=True)
        if existing:
            return existing[0]

        camera_transform, _ = cmds.camera(name=DEFAULT_CAMERA_NAME)
        cmds.addAttr(camera_transform, longName=ATTR_MMD_CAMERA, attributeType="bool")
        cmds.setAttr(f"{camera_transform}.{ATTR_MMD_CAMERA}", True)
        return camera_transform

    def _get_or_create_light(self) -> str:
        """MMD照明を取得または作成する

        Returns:
            照明のトランスフォーム名
        """
        existing = cmds.ls(f"*.{ATTR_MMD_LIGHT}", objectsOnly=True)
        if existing:
            return existing[0]

        light_shape = cmds.directionalLight(name=DEFAULT_LIGHT_NAME)
        light_transform = cmds.listRelatives(light_shape, parent=True)[0]
        cmds.addAttr(light_transform, longName=ATTR_MMD_LIGHT, attributeType="bool")
        cmds.setAttr(f"{light_transform}.{ATTR_MMD_LIGHT}", True)
        return light_transform

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
        has_model = bool(getattr(vmd_data, "bone_frames", [])) or bool(getattr(vmd_data, "morph_frames", []))
        has_camera = bool(getattr(vmd_data, "camera_frames", []))
        has_light = bool(getattr(vmd_data, "light_frames", []))

        if has_model and (has_camera or has_light):
            return "mixed"
        if has_camera:
            return "camera"
        if has_light:
            return "light"
        if has_model:
            return "model"
        return "empty"

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
        if isinstance(mapping_entry, list):
            mappings = mapping_entry
        elif mapping_entry:
            mappings = [mapping_entry]
        else:
            return []

        normalized_mappings = []
        for entry in mappings:
            if not isinstance(entry, tuple) or len(entry) != 3:
                continue

            morph_node, weight_ref, morph_name = entry
            if isinstance(weight_ref, int):
                weight_ref = f"weight[{weight_ref}]"
            normalized_mappings.append((morph_node, weight_ref, morph_name))

        return normalized_mappings

    def _register_morph_mapping(self, morph_name: str, mapping: Tuple[str, str, str]) -> None:
        existing = self.morph_name_mapping.get(morph_name)
        if existing is None:
            self.morph_name_mapping[morph_name] = [mapping]
            return

        if isinstance(existing, tuple):
            if existing == mapping:
                return
            self.morph_name_mapping[morph_name] = [existing, mapping]
            return

        for existing_mapping in existing:
            if existing_mapping == mapping:
                return
        existing.append(mapping)

    def _read_blendshape_morph_names(self, blend_shape_node: str) -> Dict[int, str]:
        """blendShape に保存された weight index → 生モーフ名 (PmxMorph.name) を読み出す。

        import 時に MorphConverter が保存した権威マップ。lossy な alias 逆引きに頼らず、
        VMD/PMX が参照する生名で正確にマッピングするために使う。
        """
        result: Dict[int, str] = {}
        if not cmds.attributeQuery(ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, node=blend_shape_node, exists=True):
            return result
        try:
            raw = cmds.getAttr(f"{blend_shape_node}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}") or "{}"
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return result
        if not isinstance(parsed, dict):
            return result
        for key, value in parsed.items():
            try:
                result[int(key)] = str(value)
            except (TypeError, ValueError):
                continue
        return result

    def _build_morph_mappings(self):
        """シーン内のblendShapeとmetadata networkからモーフ名マッピングを構築"""
        self.morph_name_mapping = {}

        blend_shapes = cmds.ls(type="blendShape") or []
        for bs_node in blend_shapes:
            stored_names = self._read_blendshape_morph_names(bs_node)
            weight_count = cmds.blendShape(bs_node, query=True, weightCount=True) or 0
            for i in range(weight_count):
                alias = cmds.aliasAttr(f"{bs_node}.weight[{i}]", query=True)
                mapping = (bs_node, f"weight[{i}]", alias or f"weight[{i}]")
                if alias:
                    self._register_morph_mapping(alias, mapping)

                original_name = stored_names.get(i)
                if original_name:
                    # import 時に保存した生のモーフ名（権威キー）。VMD/PMX の参照名と一致する。
                    self._register_morph_mapping(original_name, mapping)
                elif alias:
                    # レガシーシーン（生名未保存）のフォールバック: 辞書逆引き。
                    # 同一 alias に複数モーフが化ける衝突があり lossy なため、
                    # 保存済み生名がある blendShape では使わない。
                    for candidate in self._get_original_morph_name_candidates(alias):
                        self._register_morph_mapping(candidate, mapping)

        for morph_node in cmds.ls(type="network") or []:
            if not cmds.attributeQuery("mmd_morph_type", node=morph_node, exists=True):
                continue
            morph_type = cmds.getAttr(f"{morph_node}.mmd_morph_type")
            if morph_type not in {"bone", "group", "material"}:
                continue
            if not cmds.attributeQuery("weight", node=morph_node, exists=True):
                continue

            original_name = ""
            if cmds.attributeQuery("mmd_morph_name", node=morph_node, exists=True):
                original_name = cmds.getAttr(f"{morph_node}.mmd_morph_name") or ""
            if not original_name:
                continue

            mapping = (morph_node, "weight", original_name)
            self._register_morph_mapping(original_name, mapping)
            safe_name = morph_node
            for suffix in ("_boneMorph", "_groupMorph", "_materialMorph"):
                if safe_name.endswith(suffix):
                    safe_name = safe_name[: -len(suffix)]
                    break
            self._register_morph_mapping(safe_name, mapping)
            if cmds.attributeQuery("mmd_morph_name_en", node=morph_node, exists=True):
                english_name = cmds.getAttr(f"{morph_node}.mmd_morph_name_en") or ""
                if english_name:
                    self._register_morph_mapping(english_name, mapping)

    def _get_original_morph_name_candidates(self, alias: str) -> List[str]:
        """Maya aliasからVMD/PMX側の元モーフ名候補を取得する。

        PMX import では日本語名を Maya 安全なASCII aliasへ変換する一方、
        VMD morph frame は日本語名のまま来る。そのため alias と辞書逆引き名の
        両方を mapping key として登録する。
        """
        candidates = []
        if not alias:
            return candidates

        try:
            from mmd_tools.core.unicode_converter import get_converter

            converter = get_converter()
            for source_map in (converter.unicode_to_ascii, converter.exact_match):
                for original_name, converted_name in source_map.items():
                    if converted_name == alias:
                        candidates.append(original_name)
        except Exception:
            pass

        unique_candidates = []
        for candidate in candidates:
            if candidate and candidate not in unique_candidates:
                unique_candidates.append(candidate)
        return unique_candidates

    def _set_scene_fps(self, fps: float):
        """シーンのFPSを設定

        Args:
            fps: 設定するFPS値
        """
        # FPSとタイムユニットのマッピング
        fps_mapping = {
            15.0: "game",
            24.0: "film",
            25.0: "pal",
            30.0: "ntsc",
            48.0: "show",
            50.0: "palf",
            60.0: "ntscf",
        }

        if fps in fps_mapping:
            # 定義済みのタイムユニットを使用
            cmds.currentUnit(time=fps_mapping[fps])
            self.logger.info(f"Set scene FPS to {fps} ({fps_mapping[fps]})")
        else:
            self.logger.warning(f"Specified FPS {fps} is not supported. Using default 30.0 FPS")
            cmds.currentUnit(time="ntsc")  # デフォルトは30fpsのNTSC
