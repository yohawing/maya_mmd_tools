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
from ..core.pmx_data import PmxData
from ..core.settings import settings
from ..core.vmd_data import VmdData

# mmd-anim runtime (Phase 1+)
try:
    from ..core.native.mmd_anim_runtime import (
        is_mmd_runtime_available,
        MmdRuntimeModel,
        MmdRuntimeClip,
        MmdRuntimeInstance,
    )
    HAS_MMD_RUNTIME = True
except Exception:
    HAS_MMD_RUNTIME = False
    def is_mmd_runtime_available():
        return False
    MmdRuntimeModel = MmdRuntimeClip = MmdRuntimeInstance = None  # type: ignore



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
        self._failed_bones = set()  # 変換に失敗したボーン名を記録
        self._bone_bind_poses: Dict[str, Tuple[float, float, float]] = {}  # ボーンの初期位置
        self.use_quaternion_interpolation = True  # Quaternion補間の使用フラグ
        self.anim_layer = None  # 現在のアニメーションレイヤー名
        self.use_animation_layers = True  # アニメーションレイヤーの使用フラグ

        # runtime bake: 静的チャンネル判定の閾値。ワールド行列→ローカル分解で乗る
        # 浮動小数ジッタを吸収し、これ未満しか動かないチャンネルはキーを打たず
        # setAttr 一回で固定する（不要な全フレームキーを抑制）。
        # 並進は Maya linear 単位、回転は度で指定（内部比較時にラジアン換算）。
        import math as _math
        self._static_eps_translate = float(
            settings.get("import.animation.static_channel_epsilon_translate", 1e-4)
        )
        self._static_eps_rotate = _math.radians(
            float(settings.get("import.animation.static_channel_epsilon_rotate_deg", 0.01))
        )

    def convert(
        self,
        vmd_data: VmdData,
        target_namespace: str = None,
        layer_name: str = "VMD_Motion",
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
            vmd_bytes: 生の VMD バイナリ（runtime bake で使用）
            pmx_bytes: 生の PMX バイナリ（runtime bake で使用）
            pmx_path: PMX ファイルパス（pmx_bytes がない場合に読み込みに使用）

        Returns:
            変換が成功した場合True、失敗した場合False
        """
        try:
            self.logger.info("VMDアニメーション変換を開始します")

            # 名前マッピングの構築（ボーン名 → Maya joint）
            self._build_name_mappings(target_namespace)

            # ボーンの初期位置を記録
            self._record_bind_poses()
            self.logger.info(f"VMD種別判定: {self._detect_vmd_motion_kind(vmd_data)}")

            # タイムライン設定
            self._setup_timeline(vmd_data)

            # アニメーションレイヤーの作成
            if self.use_animation_layers:
                self.anim_layer = cmds.animLayer(layer_name, override=False, weight=1.0)

            # --- Phase 1: mmd-anim runtime を使った高精度ベイク ---
            if self._should_use_mmd_runtime_bake(vmd_bytes, pmx_bytes, pmx_path):
                self.logger.info("mmd-anim runtime を使用した高精度ベイクパスで変換します")
                runtime_success = self._convert_using_mmd_runtime(
                    vmd_data=vmd_data,
                    vmd_bytes=vmd_bytes,
                    pmx_bytes=pmx_bytes,
                    pmx_path=pmx_path,
                )
                if runtime_success:
                    self.logger.info("mmd-anim runtime による高精度ベイクが完了しました")
                    return True
                else:
                    self.logger.warning("runtime ベイクに失敗したため、レガシーパスにフォールバックします")

            # --- レガシーパス（従来の変換） ---
            if hasattr(vmd_data, "bone_frames") and vmd_data.bone_frames:
                self.logger.info(f"ボーンアニメーション変換を開始（レガシー）: {len(vmd_data.bone_frames)}フレーム")
                bone_success = self._convert_bone_animation(vmd_data.bone_frames)
                if not bone_success:
                    self.logger.warning("ボーンアニメーション変換で一部エラーが発生しました")

            # モーフアニメーション（レガシー）
            if hasattr(vmd_data, "morph_frames") and vmd_data.morph_frames:
                self.logger.info("モーフアニメーションを変換します（レガシー）")
                self._convert_morph_animation(vmd_data.morph_frames)

            # カメラアニメーション（レガシー）
            if hasattr(vmd_data, "camera_frames") and vmd_data.camera_frames:
                self.logger.info(f"カメラアニメーションを変換します: {len(vmd_data.camera_frames)}フレーム")
                self._convert_camera_animation(vmd_data.camera_frames)

            # ライトアニメーション（レガシー）
            if hasattr(vmd_data, "light_frames") and vmd_data.light_frames:
                self.logger.info(f"ライトアニメーションを変換します: {len(vmd_data.light_frames)}フレーム")
                self._convert_light_animation(vmd_data.light_frames)

            self.logger.info("VMDアニメーション変換が完了しました")
            return True

        except Exception as e:
            self.logger.error(f"VMDアニメーション変換中にエラーが発生しました: {str(e)}", exc_info=True)
            return False

    def _should_use_mmd_runtime_bake(
        self, vmd_bytes: bytes, pmx_bytes: bytes, pmx_path: str
    ) -> bool:
        """PMX 専用の runtime ベイクを使うか判定（pmx_bytes or .pmx path）。"""
        if not (HAS_MMD_RUNTIME and is_mmd_runtime_available()):
            return False

        # 少なくとも vmd_bytes と (pmx_bytes または pmx_path) が必要
        has_vmd = bool(vmd_bytes)
        if bool(pmx_bytes):
            has_pmx = True
        else:
            has_pmx = bool(pmx_path) and Path(pmx_path).suffix.lower() == ".pmx" and os.path.exists(pmx_path)
        return has_vmd and has_pmx

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
                self.logger.error(f"PMX ファイルの読み込み失敗: {pmx_path} - {e}")
                return False

        if not resolved_pmx_bytes:
            self.logger.error("runtime ベイクに必要な PMX データが取得できませんでした")
            return False

        # モデル・クリップ・インスタンス作成
        pmx_morph_names = []
        if pmx_path and os.path.exists(pmx_path):
            try:
                pmx_data = PmxData().parse_file(pmx_path)
                pmx_morph_names = [morph.name for morph in pmx_data.morphs]
            except Exception as e:
                self.logger.warning(f"runtime morph bake 用 PMX morph 名の取得に失敗: {e}")

        model = MmdRuntimeModel.from_pmx_bytes(resolved_pmx_bytes)
        if model is None:
            self.logger.error("MmdRuntimeModel の作成に失敗しました")
            return False

        clip = MmdRuntimeClip.from_vmd_bytes_for_model(model, vmd_bytes)
        if clip is None:
            self.logger.error("MmdRuntimeClip の作成に失敗しました")
            model.free()
            return False

        instance = MmdRuntimeInstance.for_model(model)
        if instance is None:
            self.logger.error("MmdRuntimeInstance の作成に失敗しました")
            clip.free()
            model.free()
            return False

        try:
            runtime_start = time.perf_counter()
            # フレーム範囲の決定
            min_frame, max_frame = self._get_animation_frame_range(vmd_data)
            bake_frames = self._iter_runtime_bake_frames(min_frame, max_frame)
            self.logger.info(
                f"runtime 評価範囲: {min_frame} - {max_frame} (keys={len(bake_frames)})"
            )
            self._disable_mmd_rig_constraints_for_runtime_bake()

            # runtime bake は最終姿勢を毎フレーム直焼きするため、animation layerを使わない。
            # layer経由だと全ボーン全フレームのblend node作成が重く、未登録attribute警告も出る。
            runtime_anim_layer = self.anim_layer
            self.anim_layer = None
            refresh_suspended = False

            # キャッシュ収集: 評価結果を API 配列へ直接保持（cmds.xform / setKeyframe を内側ループから排除）
            baked_frames: List[int] = []
            bake_times = om.MTimeArray()
            joint_channel_values = self._create_runtime_joint_channel_arrays()
            joint_channel_static = self._create_runtime_joint_channel_static_state()
            morph_cache: List[Tuple[int, list]] = []
            eval_start = time.perf_counter()

            # 各フレームを評価してキャッシュ（Mayaコマンドを呼ばず高速に）
            try:
                try:
                    cmds.refresh(suspend=True)
                    refresh_suspended = True
                except Exception:
                    refresh_suspended = False

                for frame in bake_frames:
                    if not instance.evaluate_clip_frame(clip, float(frame)):
                        continue

                    # ワールド行列・モーフウェイトを取得（ボーン順）
                    world_matrices = instance.get_world_matrices() or []
                    morph_weights = instance.get_morph_weights() or []

                    # ローカルポーズをメモリ内で計算（親子階層を考慮した t/r ）
                    bone_locals: Dict[int, Tuple[float, float, float, float, float, float]] = {}
                    if self.bone_index_to_joint:
                        if not hasattr(self, "_bone_parent_map") or len(getattr(self, "_bone_parent_map", {})) == 0:
                            self._build_bone_hierarchy_and_order_maps()
                        bone_locals = self._compute_all_bone_locals(world_matrices)

                    baked_frames.append(int(frame))
                    bake_times.append(om.MTime(float(frame), om.MTime.uiUnit()))
                    self._append_bone_locals_to_channel_arrays(
                        bone_locals, joint_channel_values, joint_channel_static
                    )
                    morph_cache.append((int(frame), list(morph_weights)))
            finally:
                if refresh_suspended:
                    try:
                        cmds.refresh(suspend=False)
                    except Exception:
                        pass
                self.anim_layer = runtime_anim_layer

            eval_elapsed = time.perf_counter() - eval_start
            self.logger.info(
                f"mmd-anim runtime によるポーズ評価+キャッシュ完了 "
                f"(frames={len(baked_frames)}, elapsed={eval_elapsed:.3f}s)"
            )

            # キャッシュから一括でキーフレーム登録（Maya Python API 2.0 優先）
            if baked_frames:
                apply_start = time.perf_counter()
                self._apply_runtime_channel_arrays_to_scene(
                    joint_channel_values,
                    joint_channel_static,
                    bake_times,
                    baked_frames,
                    morph_cache,
                    pmx_morph_names,
                )
                apply_elapsed = time.perf_counter() - apply_start
                self.logger.info(
                    f"runtime cache キー登録完了 (elapsed={apply_elapsed:.3f}s)"
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

    def _iter_runtime_bake_frames(self, min_frame: int, max_frame: int) -> List[int]:
        """runtime bakeで評価/キー作成する全フレーム列を返す。"""
        min_frame = int(min_frame)
        max_frame = int(max_frame)
        if max_frame < min_frame:
            return []
        return list(range(min_frame, max_frame + 1))

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

    def _compute_all_bone_locals(
        self, world_matrices: List[List[float]]
    ) -> Dict[int, Tuple[float, float, float, float, float, float]]:
        """runtime から得たワールド行列群から、各ボーンの Maya ローカル姿勢 (translate + rotate deg) を計算。

        親ボーンの変換済みワールド行列の逆行列を掛けてローカル行列を得、ジョイントの
        rotateOrder に適合するオイラー角を抽出する。これにより per-frame の cmds.xform を
        回避しつつ、ベイク結果の等価性を保つ。
        """
        if not world_matrices or not self.bone_index_to_joint:
            return {}
        locals_map: Dict[int, Tuple[float, float, float, float, float, float]] = {}
        maya_worlds: Dict[int, om.MMatrix] = {}
        for bidx in self.bone_index_to_joint.keys():
            if bidx < len(world_matrices):
                mmd_m = world_matrices[bidx]
                if isinstance(mmd_m, (list, tuple)) and len(mmd_m) == 16:
                    try:
                        maya_flat = self._convert_mmd_world_matrix_to_maya(list(mmd_m))
                        maya_worlds[bidx] = om.MMatrix(maya_flat)
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
            import math

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
        import math

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
        import math

        for bidx, (tx, ty, tz, rx, ry, rz) in bone_locals.items():
            joint = self.bone_index_to_joint.get(bidx)
            chans = channel_values.get(joint)
            states = static_state.get(joint)
            if not chans or not states:
                continue

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
        frame_numbers: List[int],
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
                            import math

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
                        import math

                        value = math.degrees(value)
                    cmds.setKeyframe(joint_name, attribute=attr, time=frame, value=value)
                except Exception:
                    pass
            keyed += 1

        return keyed, skipped_static

    @staticmethod
    def _collect_append_info():
        """シーン内の全 mmdAppend ノードから (target_joint, append_node, source_joint, ratio, attr_map) を収集。"""
        result = {}
        append_nodes = cmds.ls(type="mmdAppend") or []

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
            src_plugs = cmds.listConnections(f"{node}.sourceRotate", s=True, d=False, p=True) or []
            source_joint = None
            source_append_node = None
            if src_plugs:
                src_node = src_plugs[0].rsplit(".", 1)[0]
                src_attr = src_plugs[0].rsplit(".", 1)[1]
                if src_attr.startswith("appendRotate"):
                    source_append_node = src_node
                    source_joint = node_targets.get(source_append_node)
                else:
                    source_joint = src_node
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
                "local_append": local_append,
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
    def _decompose_append_own_rotation(
        target_rx: om.MDoubleArray,
        target_ry: om.MDoubleArray,
        target_rz: om.MDoubleArray,
        source_rx: om.MDoubleArray,
        source_ry: om.MDoubleArray,
        source_rz: om.MDoubleArray,
        ratio: float,
    ):
        """bake の final rotation から grant 寄与を除去し、bone own rotation を計算。

        final = own * slerp(I, source, ratio)  →  own = final * inv(grant)
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
            if source_resolved:
                source_rotation = (
                    source_resolved["own"]
                    if info.get("local_append")
                    else source_resolved["grant"]
                )

            resolving.remove(joint)
            if source_rotation is None:
                resolved[joint] = None
                return None

            own_rotation, grant_rotation = self._decompose_append_own_rotation(
                final_rotation[0], final_rotation[1], final_rotation[2],
                source_rotation[0], source_rotation[1], source_rotation[2],
                info["ratio"],
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

    def _apply_runtime_channel_arrays_to_scene(
        self,
        joint_channel_values: Dict[str, Dict[str, Optional[om.MDoubleArray]]],
        joint_channel_static: Dict[str, Dict[str, dict]],
        bake_times: om.MTimeArray,
        baked_frames: List[int],
        morph_cache: List[Tuple[int, list]],
        pmx_morph_names: List[str],
    ):
        """API配列へ収集済みのruntime bake結果をMaya sceneへ一括適用する。"""
        keyed_channels = 0
        skipped_static_channels = 0
        total_channels = 0

        append_info = self._collect_append_info()
        decomposed_rotations = self._decompose_append_rotations_for_scene(
            joint_channel_values,
            joint_channel_static,
            append_info,
            len(baked_frames),
        )

        for joint, channels in joint_channel_values.items():
            total_channels += len(channels)
            try:
                info = append_info.get(joint)
                if info and info["attr_map"]:
                    append_node = info["node"]
                    attr_map = dict(info["attr_map"])
                    target_static = joint_channel_static.get(joint, {})
                    decomposed_channels = decomposed_rotations.get(joint, {})

                    if info["affect_rotation"] and not decomposed_channels:
                        attr_map.pop("rotateX", None)
                        attr_map.pop("rotateY", None)
                        attr_map.pop("rotateZ", None)

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
                    joint_channel_static.get(joint, {}),
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

        for frame, morph_weights in morph_cache:
            try:
                self._bake_morph_weights_from_runtime(frame, morph_weights, pmx_morph_names)
            except Exception as e:
                self.logger.debug(f"post-apply morph bake error at frame {frame}: {e}")

        self.logger.info(f"runtime cache 適用完了: {len(baked_frames)} フレームをキー登録")

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
            import math

            for fd in runtime_cache:
                f = fd["frame"]
                for bidx, (tx, ty, tz, rx, ry, rz) in fd.get("bone_locals", {}).items():
                    jname = self.bone_index_to_joint.get(bidx)
                    if not jname:
                        continue
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
                                        import math

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

        # モーフ: 評価ループの外で後処理（cmds ではあるが、キャッシュ適用フェーズ）
        for fd in runtime_cache:
            try:
                self._bake_morph_weights_from_runtime(
                    fd["frame"], fd.get("morph_weights", []), pmx_morph_names
                )
            except Exception as e:
                self.logger.debug(f"post-apply morph bake error at frame {fd['frame']}: {e}")

        self.logger.info(f"runtime cache 適用完了: {len(runtime_cache)} フレームをキー登録")

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

    def _disable_mmd_rig_constraints_for_runtime_bake(self):
        """runtime bake と二重評価になる PMX 付与constraintを無効化する。"""
        constraints = cmds.ls("*.mmd_grant_constraint", objectsOnly=True) or []
        disabled = 0
        for constraint in constraints:
            try:
                if cmds.attributeQuery("nodeState", node=constraint, exists=True):
                    cmds.setAttr(f"{constraint}.nodeState", 2)
                    disabled += 1
                elif cmds.attributeQuery("envelope", node=constraint, exists=True):
                    cmds.setAttr(f"{constraint}.envelope", 0)
                    disabled += 1
            except Exception as e:
                self.logger.debug(f"failed to disable MMD grant constraint {constraint}: {e}")

        if disabled:
            self.logger.info(f"runtime bake 用に {disabled} 個のMMD付与constraintを無効化しました")


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
        self.logger.info("名前マッピングを構築しています")

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

        self.logger.info(f"{len(self.bone_name_mapping)}個のボーンマッピングを構築しました "
                         f"(index対応: {len(self.bone_index_to_joint)}個)")

        # モーフ名マッピングの構築 (for accurate runtime morph bake)
        self._build_morph_mappings()  # 元のメソッドは namespace 引数を取らない

    def _record_bind_poses(self):
        """各ボーンの初期位置（バインドポーズ）を記録"""
        self.logger.info("ボーンの初期位置を記録しています")

        for vmd_bone_name, maya_joint in self.bone_name_mapping.items():
            try:
                # 現在のtranslate値を取得（これがバインドポーズ）
                translate = cmds.getAttr(f"{maya_joint}.translate")[0]
                self._bone_bind_poses[vmd_bone_name] = translate
            except Exception as e:
                self.logger.warning(f"{vmd_bone_name}のバインドポーズ取得エラー: {str(e)}")

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
            cmds.playbackOptions(min=0, max=max_frame, animationStartTime=0, animationEndTime=max_frame)
            self.logger.info(f"タイムライン範囲を設定: 0 - {max_frame}")

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
                    self.logger.error(f"ボーン '{vmd_bone_name}' のアニメーション設定中にエラー: {str(e)}")
                    self._failed_bones.add(vmd_bone_name)
            else:
                if vmd_bone_name not in self._failed_bones:
                    self.logger.info(f"ボーン '{vmd_bone_name}' が見つかりません")
                    self._failed_bones.add(vmd_bone_name)

        # アニメーションレイヤーにジョイントを追加
        # IK link ジョイントは除外 — animLayer が rotate blend node を自動生成し
        # mmdCcdIk solver の outputRotate 接続を上書きするため
        if self.use_animation_layers and self.anim_layer and animated_joints:
            ik_link_joints = self._collect_ik_link_joints()
            layer_joints = [j for j in animated_joints if j not in ik_link_joints]
            self._add_objects_to_layer(layer_joints)

        self.logger.info(f"{success_count}/{total_count}個のボーンアニメーションを変換しました")
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
        for node in cmds.ls(type="mmdCcdIk") or []:
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
                    ik_link_joints[jnt] = {"solver": node, "slot": bone_slot}
        return ik_link_joints

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

        for frame in frames:
            if hasattr(frame, "frame_number"):
                frame_number = frame.frame_number
                vmd_pos = frame.position
                rotation_quat = frame.rotation
            else:
                frame_number = frame.get("frame_number", 0)
                vmd_pos = frame.get("position", [0, 0, 0])
                rotation_quat = frame.get("rotation", [0, 0, 0, 1])

            tx = float(bind_pos[0]) + float(vmd_pos[0])
            ty = float(bind_pos[1]) + float(vmd_pos[1])
            tz = float(bind_pos[2]) - float(vmd_pos[2])

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
                    "time": frame_number,
                    "value": float(value),
                }
                if use_layer:
                    key_args["animLayer"] = self.anim_layer
                cmds.setKeyframe(target_node, **key_args)

        # TODO: maya apiを使うなら、キーフレームを先に打って、カーブを作成した後に、一括で設定するとパフォーマンスが向上する。
        # curves = maya_utils.create_animation_curves(
        #     joint, attrs, animation_layer=self.animation_layer_name
        # )

        # # キーフレームを一括設定
        # maya_utils.set_keyframes_batch(curves, frames, generate_values)

        # IK link bone: VMD 回転を solver.inputRotate にキーイング
        # joint.rotate は solver.outputRotate が駆動するので直接キーできないが、
        # solver の inputRotate に VMD の事前解決済み回転を渡すことで
        # CCD IK の base_rotation として使われ、正しい解に収束する
        ik_info = key_route.get("ik_solver_rotate") if key_route else None
        if ik_info:
            solver_node = ik_info["solver"]
            slot = ik_info["slot"]
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
                rx, ry, rz = self._convert_vmd_quat_to_joint_rotate(joint, *rq)
                # kAngle 複合配列の子属性は setKeyframe で度→ラジアン自動変換が
                # 効かないため、明示的にラジアンに変換してからキーイングする
                for attr, val in zip(ir_attrs, [rx, ry, rz]):
                    cmds.setKeyframe(solver_node, attribute=attr, time=fn, value=math.radians(val))

        # Quaternion補間を適用（rotate が joint 自身に直接キーされている場合のみ）
        rotate_redirected = any(
            attr_targets.get(a, (joint, a))[0] != joint
            for a in ("rotateX", "rotateY", "rotateZ")
        )
        if self.use_quaternion_interpolation and not skip_rotate and not rotate_redirected:
            try:
                cmds.rotationInterpolation(
                    f"{joint}.rotateX",
                    f"{joint}.rotateY",
                    f"{joint}.rotateZ",
                    convert="quaternionSlerp",
                )
            except Exception as e:
                self.logger.warning(f"{joint}へのQuaternion補間適用に失敗: {str(e)}")

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
                f"{joint_name} の rotateAxis が非ゼロです ({rotate_axis})。"
                "レガシーパスでは rotateAxis 未対応のため回転精度が低下します"
            )

        result = (q_jo, rotate_order)
        self._joint_orient_cache[joint_name] = result
        return result

    def _convert_vmd_quat_to_joint_rotate(self, joint_name, qx, qy, qz, qw):
        """VMD quaternion を Maya joint.rotate の Euler 角（度）へ変換する。"""
        q_maya = om.MQuaternion(-float(qx), -float(qy), float(qz), float(qw))

        q_jo, rotate_order = self._get_joint_orient_cache(joint_name)
        if q_jo is not None:
            q_rotate = q_maya * q_jo.inverse()
        else:
            q_rotate = q_maya

        euler = q_rotate.asEulerRotation()
        euler.reorderIt(rotate_order)
        return (math.degrees(euler.x), math.degrees(euler.y), math.degrees(euler.z))

    def get_parent_world_rotation(self, joint):
        """Maya API 2.0を使用して親ワールド変換行列から親の回転を取得"""
        # MSelectionListを使用してMObjectを取得
        selection_list = om.MSelectionList()
        selection_list.add(joint)
        joint_mobject = selection_list.getDagPath(0)

        # MFnTransformを作成
        fn_transform = om.MFnTransform(joint_mobject)

        # 親のワールド変換行列を取得
        parent_path = fn_transform.dagPath().pop()
        if parent_path.length() == 0:
            return om.MQuaternion(0, 0, 0, 1)

        parent_world_matrix = parent_path.inclusiveMatrix()
        parent_transform_matrix = om.MTransformationMatrix(parent_world_matrix)

        return parent_transform_matrix.rotation(asQuaternion=True)

    def apply_rotation(self, joint, world_quat):
        """Deprecated: use _convert_vmd_quat_to_joint_rotate() and key explicit values."""
        rx, ry, rz = self._convert_vmd_quat_to_joint_rotate(
            joint,
            world_quat.x,
            world_quat.y,
            world_quat.z,
            world_quat.w,
        )
        cmds.setAttr(f"{joint}.rotate", rx, ry, rz, type="double3")

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
        if not camera_frames:
            return False

        import math

        camera_transform = self._get_or_create_camera()
        camera_shapes = cmds.listRelatives(camera_transform, shapes=True, type="camera") or []
        camera_shape = camera_shapes[0] if camera_shapes else None

        for attr_name, attr_type, default_value in (
            ("mmd_camera_distance", "double", 0.0),
            ("mmd_camera_viewing_angle", "double", 45.0),
            ("mmd_camera_perspective", "long", 0),
        ):
            if not cmds.attributeQuery(attr_name, node=camera_transform, exists=True):
                cmds.addAttr(camera_transform, longName=attr_name, attributeType=attr_type, keyable=True)
                cmds.setAttr(f"{camera_transform}.{attr_name}", default_value)

        for frame in camera_frames:
            frame_number = frame.frame_number if hasattr(frame, "frame_number") else frame.get("frame_number", 0)
            position = frame.position if hasattr(frame, "position") else frame.get("position", (0, 0, 0))
            rotation = frame.rotation if hasattr(frame, "rotation") else frame.get("rotation", (0, 0, 0))
            distance = frame.distance if hasattr(frame, "distance") else frame.get("distance", 0.0)
            viewing_angle = frame.viewing_angle if hasattr(frame, "viewing_angle") else frame.get("viewing_angle", 45)
            perspective = frame.perspective if hasattr(frame, "perspective") else frame.get("perspective", 0)

            cmds.setKeyframe(camera_transform, attribute="translateX", time=frame_number, value=position[0])
            cmds.setKeyframe(camera_transform, attribute="translateY", time=frame_number, value=position[1])
            cmds.setKeyframe(camera_transform, attribute="translateZ", time=frame_number, value=-position[2])
            cmds.setKeyframe(camera_transform, attribute="rotateX", time=frame_number, value=math.degrees(rotation[0]))
            cmds.setKeyframe(camera_transform, attribute="rotateY", time=frame_number, value=math.degrees(rotation[1]))
            cmds.setKeyframe(camera_transform, attribute="rotateZ", time=frame_number, value=-math.degrees(rotation[2]))
            cmds.setKeyframe(camera_transform, attribute="mmd_camera_distance", time=frame_number, value=distance)
            cmds.setKeyframe(
                camera_transform,
                attribute="mmd_camera_viewing_angle",
                time=frame_number,
                value=float(viewing_angle),
            )
            cmds.setKeyframe(camera_transform, attribute="mmd_camera_perspective", time=frame_number, value=int(perspective))

            if camera_shape:
                focal_length = self._viewing_angle_to_focal_length(camera_shape, float(viewing_angle))
                cmds.setKeyframe(camera_shape, attribute="focalLength", time=frame_number, value=focal_length)
                if cmds.attributeQuery("orthographic", node=camera_shape, exists=True):
                    cmds.setKeyframe(camera_shape, attribute="orthographic", time=frame_number, value=bool(perspective))

        return True

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
        import math

        clamped_angle = max(1.0, min(179.0, float(viewing_angle)))
        aperture_inch = cmds.getAttr(f"{camera_shape}.horizontalFilmAperture")
        aperture_mm = float(aperture_inch) * 25.4
        return aperture_mm / (2.0 * math.tan(math.radians(clamped_angle) / 2.0))

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
        import math

        if not light_frames:
            return False

        light_transform = self._get_or_create_light()
        light_shapes = cmds.listRelatives(light_transform, shapes=True, type="directionalLight") or []
        if not light_shapes:
            return False
        light_shape = light_shapes[0]

        for frame in light_frames:
            frame_number = frame.frame_number if hasattr(frame, "frame_number") else frame.get("frame_number", 0)
            color = frame.color if hasattr(frame, "color") else frame.get("color", (1, 1, 1))
            position = frame.position if hasattr(frame, "position") else frame.get("position", (0.0, -1.0, 0.0))

            # color keyframe（常に設定）
            cmds.setKeyframe(light_shape, attribute="colorR", time=frame_number, value=color[0])
            cmds.setKeyframe(light_shape, attribute="colorG", time=frame_number, value=color[1])
            cmds.setKeyframe(light_shape, attribute="colorB", time=frame_number, value=color[2])

            # 方向ベクトル: VMD (x, y, z) → Maya (x, y, -z)
            dx, dy, dz = float(position[0]), float(position[1]), -float(position[2])
            length = math.sqrt(dx * dx + dy * dy + dz * dz)

            if length < 1e-10:
                self.logger.warning(
                    f"frame {frame_number}: position がゼロベクトルのため rotation key をスキップします"
                )
                continue

            # 正規化
            dx /= length
            dy /= length
            dz /= length

            # Euler 角を算出: Ry * Rx * (0, 0, -1) = (dx, dy, dz)
            # dx = -sin(ry)*cos(rx), dy = sin(rx), dz = -cos(ry)*cos(rx)
            rx = math.asin(dy)  # -pi/2 .. pi/2
            cos_rx = math.cos(rx)
            if abs(cos_rx) > 1e-10:
                ry = math.atan2(-dx / cos_rx, -dz / cos_rx)
            else:
                # cos(rx) ≈ 0 → 真上/真下向き, 任意の ry で可
                ry = 0.0

            cmds.setKeyframe(light_transform, attribute="rotateX", time=frame_number, value=math.degrees(rx))
            cmds.setKeyframe(light_transform, attribute="rotateY", time=frame_number, value=math.degrees(ry))
            cmds.setKeyframe(light_transform, attribute="rotateZ", time=frame_number, value=0.0)

        return True

    def _convert_morph_animation(self, morph_frames: List) -> bool:
        """モーフアニメーションを変換

        Args:
            morph_frames: モーフフレームデータのリスト

        Returns:
            変換が成功した場合True
        """
        if not morph_frames:
            return False

        success_count = 0
        morph_frame_map: Dict[str, List] = {}

        for frame in morph_frames:
            morph_name = frame.morph_name if hasattr(frame, "morph_name") else frame.get("morph_name", "")
            if morph_name not in morph_frame_map:
                morph_frame_map[morph_name] = []
            morph_frame_map[morph_name].append(frame)

        for morph_name, frames in morph_frame_map.items():
            mappings = self._iter_morph_mappings(self.morph_name_mapping.get(morph_name))
            if not mappings:
                continue

            for morph_node, weight_attr, _ in mappings:
                for frame in frames:
                    frame_number = frame.frame_number if hasattr(frame, "frame_number") else frame.get("frame_number", 0)
                    value = frame.value if hasattr(frame, "value") else frame.get("value", 0.0)
                    cmds.setKeyframe(morph_node, attribute=weight_attr, time=frame_number, value=value)

            success_count += 1

        return success_count > 0

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
        """シーン内のblendShapeとbone morph networkからモーフ名マッピングを構築"""
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
            if morph_type not in {"bone", "material"}:
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
            for suffix in ("_boneMorph", "_materialMorph"):
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
            self.logger.info(f"シーンFPSを{fps} ({fps_mapping[fps]})に設定しました")
        else:
            self.logger.warning(f"指定されたFPS {fps} はサポートされていません。デフォルトの30.0 FPSを使用します")
            cmds.currentUnit(time="ntsc")  # デフォルトは30fpsのNTSC
