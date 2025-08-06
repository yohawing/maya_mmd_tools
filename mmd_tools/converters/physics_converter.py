import maya.cmds as cmds
from typing import List, Dict, Tuple, Optional, Union
import math
from ..core.logger import get_logger
from ..core import maya_utils
from ..core.pmd_data.rigid_body import PmdRigidBody
from ..core.pmx_data.rigid_body import PmxRigidBody
from ..core.constants import (
    PHYSICS_GROUP,
    RIGID_BODIES_GROUP,
    CONSTRAINTS_GROUP,
    PHYSICS_TYPE_HAIR,
    PHYSICS_TYPE_CLOTH,
    PHYSICS_TYPE_RIGID,
    PHYSICS_TYPE_SOFT,
)


class PhysicsConverter:
    """
    MMDの物理演算データをMayaのnClothシステムに変換するクラス。

    主に髪の毛やスカートなどの布物理シミュレーションを実現します。
    """

    # 物理タイプは constants.py からインポートして使用

    def __init__(self, settings: Optional[Dict] = None):
        """
        PhysicsConverterを初期化する。

        Args:
            settings (Optional[Dict]): 物理設定。Noneの場合はデフォルト設定を使用。
        """
        self.logger = get_logger(__class__.__name__)

        # デフォルト設定
        self._default_settings = {
            "enable_hair_physics": True,
            "enable_cloth_physics": True,
            "simulation_quality": "medium",
            "auto_detect_type": True,
            "solver_iterations": 10,
            "substeps": 3,
            "start_frame": 1,
            "time_scale": 1.0,
        }

        # ユーザー設定とマージ
        self.settings = self._default_settings.copy()
        if settings:
            self.settings.update(settings)

        # nClothソルバーの名前
        self.nucleus_solver = None

        # 作成されたノードの追跡
        self.created_ncloth_nodes = []
        self.created_nrigid_nodes = []
        self.created_constraint_nodes = []

        self.logger.info("PhysicsConverterを初期化しました")

    def convert_pmd_physics(self, pmd_data, bone_joints: Dict[str, str], root_group: str) -> Tuple[List[str], List[str]]:
        """
        PMDの物理演算データをMayaのnClothシステムに変換する。

        Args:
            pmd_data: 解析されたPMDデータオブジェクト。
            bone_joints (Dict[str, str]): ボーン名とMayaジョイント名のマッピング辞書。
            root_group (str): ルートグループの名前。

        Returns:
            tuple: (作成されたnClothノードのリスト, 作成されたコンストレインノードのリスト)。
        """
        self.logger.info("PMD物理データの変換を開始します")

        try:
            # 物理グループを作成
            physics_group = cmds.group(empty=True, name=PHYSICS_GROUP, parent=root_group)
            rigid_bodies_group = cmds.group(empty=True, name=RIGID_BODIES_GROUP, parent=physics_group)
            constraints_group = cmds.group(empty=True, name=CONSTRAINTS_GROUP, parent=physics_group)

            # Nucleusソルバーを作成または取得
            self._ensure_nucleus_solver()

            # ボーンインデックスからボーン名へのマッピングを作成
            bone_index_map = self._create_bone_index_mapping(pmd_data.bones, bone_joints)

            # 剛体データを分析してグループ化
            rigid_body_groups = self._analyze_rigid_bodies(pmd_data.rigid_bodies)

            # 各グループに対して適切な物理システムを作成
            for group_type, rigid_bodies in rigid_body_groups.items():
                if group_type == PHYSICS_TYPE_HAIR and self.settings["enable_hair_physics"]:
                    self._create_hair_physics(rigid_bodies, bone_joints, bone_index_map, rigid_bodies_group)
                elif group_type == PHYSICS_TYPE_CLOTH and self.settings["enable_cloth_physics"]:
                    self._create_cloth_physics(rigid_bodies, bone_joints, bone_index_map, rigid_bodies_group)
                elif group_type == PHYSICS_TYPE_RIGID:
                    self._create_rigid_physics(rigid_bodies, bone_joints, bone_index_map, rigid_bodies_group)
                else:
                    self.logger.debug(f"物理タイプ '{group_type}' はスキップされました")

            # ジョイント（コンストレイン）を作成
            if hasattr(pmd_data, "joints") and pmd_data.joints:
                self._create_constraints(pmd_data.joints, pmd_data.rigid_bodies, constraints_group)

            self.logger.info(
                f"PMD物理変換完了: nCloth={len(self.created_ncloth_nodes)}, "
                f"nRigid={len(self.created_nrigid_nodes)}, "
                f"Constraints={len(self.created_constraint_nodes)}"
            )

            return (self.created_ncloth_nodes, self.created_constraint_nodes)

        except Exception as e:
            self.logger.error(f"PMD物理変換中にエラーが発生しました: {str(e)}")
            raise

    def convert_pmx_physics(self, pmx_data, bone_joints: Dict[str, str], root_group: str) -> Tuple[List[str], List[str]]:
        """
        PMXの物理演算データをMayaのnClothシステムに変換する。

        Args:
            pmx_data: 解析されたPMXデータオブジェクト。
            bone_joints (Dict[str, str]): ボーン名とMayaジョイント名のマッピング辞書。
            root_group (str): ルートグループの名前。

        Returns:
            tuple: (作成されたnClothノードのリスト, 作成されたコンストレインノードのリスト)。
        """
        self.logger.info("PMX物理データの変換を開始します")

        try:
            # 物理グループを作成
            physics_group = cmds.group(empty=True, name=PHYSICS_GROUP, parent=root_group)
            rigid_bodies_group = cmds.group(empty=True, name=RIGID_BODIES_GROUP, parent=physics_group)
            constraints_group = cmds.group(empty=True, name=CONSTRAINTS_GROUP, parent=physics_group)

            # Nucleusソルバーを作成または取得
            self._ensure_nucleus_solver()

            # ボーンインデックスからボーン名へのマッピングを作成
            bone_index_map = self._create_bone_index_mapping(pmx_data.bones, bone_joints)

            # 剛体データを分析してグループ化
            rigid_body_groups = self._analyze_rigid_bodies(pmx_data.rigid_bodies)

            # 各グループに対して適切な物理システムを作成
            for group_type, rigid_bodies in rigid_body_groups.items():
                if group_type == PHYSICS_TYPE_HAIR and self.settings["enable_hair_physics"]:
                    self._create_hair_physics(rigid_bodies, bone_joints, bone_index_map, rigid_bodies_group)
                elif group_type == PHYSICS_TYPE_CLOTH and self.settings["enable_cloth_physics"]:
                    self._create_cloth_physics(rigid_bodies, bone_joints, bone_index_map, rigid_bodies_group)
                elif group_type == PHYSICS_TYPE_RIGID:
                    self._create_rigid_physics(rigid_bodies, bone_joints, bone_index_map, rigid_bodies_group)
                else:
                    self.logger.debug(f"物理タイプ '{group_type}' はスキップされました")

            # ジョイント（コンストレイン）を作成
            if hasattr(pmx_data, "joints") and pmx_data.joints:
                self._create_constraints(pmx_data.joints, pmx_data.rigid_bodies, constraints_group)

            self.logger.info(
                f"PMX物理変換完了: nCloth={len(self.created_ncloth_nodes)}, "
                f"nRigid={len(self.created_nrigid_nodes)}, "
                f"Constraints={len(self.created_constraint_nodes)}"
            )

            return (self.created_ncloth_nodes, self.created_constraint_nodes)

        except Exception as e:
            self.logger.error(f"PMX物理変換中にエラーが発生しました: {str(e)}")
            raise

    def _ensure_nucleus_solver(self) -> str:
        """
        Nucleusソルバーを作成または取得する。

        Returns:
            str: Nucleusソルバーノード名。
        """
        if self.nucleus_solver and maya_utils.object_exists(self.nucleus_solver):
            return self.nucleus_solver

        # 既存のNucleusソルバーを検索または作成
        self.nucleus_solver = maya_utils.find_or_create_nucleus_solver("mmd_nucleus")
        self.logger.info(f"Nucleusソルバー: {self.nucleus_solver}")

        # ソルバー設定を適用
        self._configure_nucleus_solver()

        return self.nucleus_solver

    def _configure_nucleus_solver(self):
        """Nucleusソルバーの設定を行う。"""
        if not self.nucleus_solver or not maya_utils.object_exists(self.nucleus_solver):
            return

        # シミュレーション品質に基づいて設定
        quality_settings = {
            "low": {"substeps": 2, "maxCollisionIterations": 4},
            "medium": {"substeps": 3, "maxCollisionIterations": 8},
            "high": {"substeps": 5, "maxCollisionIterations": 12},
        }

        quality = self.settings.get("simulation_quality", "medium")
        settings = quality_settings.get(quality, quality_settings["medium"])

        maya_utils.set_attribute(self.nucleus_solver, "subSteps", settings["substeps"], "long")
        maya_utils.set_attribute(
            self.nucleus_solver,
            "maxCollisionIterations",
            settings["maxCollisionIterations"],
            "long",
        )
        maya_utils.set_attribute(self.nucleus_solver, "startFrame", self.settings.get("start_frame", 1), "double")
        maya_utils.set_attribute(self.nucleus_solver, "timeScale", self.settings.get("time_scale", 1.0), "double")

        # 重力設定（MMDは下向きをY軸負方向とする）
        # gravity: 重力の大きさ（スカラー）
        # gravityDirection: 重力の方向（ベクトル）
        maya_utils.set_attribute(self.nucleus_solver, "gravity", 9.8, "double")
        maya_utils.set_attribute(self.nucleus_solver, "gravityDirection", [0, -1, 0], "double3")  # Y軸負方向（下向き）

        self.logger.debug(f"Nucleusソルバー設定完了: quality={quality}")

    def _analyze_rigid_bodies(self, rigid_bodies: List[Union[PmdRigidBody, PmxRigidBody]]) -> Dict[str, List]:
        """
        剛体データを分析して物理タイプごとにグループ化する。

        Args:
            rigid_bodies: 剛体データのリスト。

        Returns:
            Dict[str, List]: 物理タイプをキーとした剛体のグループ辞書。
        """
        groups = {
            PHYSICS_TYPE_HAIR: [],
            PHYSICS_TYPE_CLOTH: [],
            PHYSICS_TYPE_RIGID: [],
            PHYSICS_TYPE_SOFT: [],
        }

        if not self.settings.get("auto_detect_type", True):
            # 自動検出が無効の場合、すべてを剛体として扱う
            groups[PHYSICS_TYPE_RIGID] = rigid_bodies
            return groups

        for rb in rigid_bodies:
            physics_type = self._analyze_physics_type(rb)
            groups[physics_type].append(rb)

        # グループ情報をログ出力
        for group_type, bodies in groups.items():
            if bodies:
                self.logger.debug(f"{group_type}: {len(bodies)}個の剛体")

        return groups

    def _analyze_physics_type(self, rigid_body: Union[PmdRigidBody, PmxRigidBody]) -> str:
        """
        剛体のタイプを分析（髪、布、剛体など）。

        Args:
            rigid_body: 剛体データ。

        Returns:
            str: 物理タイプ（PHYSICS_TYPE_*定数のいずれか）。
        """
        name_lower = rigid_body.name.lower()

        # 名前ベースの判定
        hair_keywords = ["髪", "hair", "毛", "ke", "kami"]
        cloth_keywords = ["スカート", "skirt", "マント", "cape", "cloth", "服", "fuku"]
        soft_keywords = ["胸", "chest", "breast", "oppai"]

        for keyword in hair_keywords:
            if keyword in name_lower:
                return PHYSICS_TYPE_HAIR

        for keyword in cloth_keywords:
            if keyword in name_lower:
                return PHYSICS_TYPE_CLOTH

        for keyword in soft_keywords:
            if keyword in name_lower:
                return PHYSICS_TYPE_SOFT

        # 形状ベースの判定
        # カプセル形状は髪の可能性が高い
        if rigid_body.shape_type == 1:  # カプセル
            return PHYSICS_TYPE_HAIR
        # 大きな箱形状は布の可能性
        elif rigid_body.shape_type == 0:  # 箱
            # サイズが大きい場合は布と判定
            if (rigid_body.size[0] * rigid_body.size[1] * rigid_body.size[2]) > 10.0:
                return PHYSICS_TYPE_CLOTH

        # デフォルトは剛体
        return PHYSICS_TYPE_RIGID

    def _create_hair_physics(
        self,
        rigid_bodies: List,
        bone_joints: Dict[str, str],
        bone_index_map: Dict[int, str],
        rigid_bodies_group: str,
    ):
        """
        髪用の物理シミュレーションを作成する。

        Args:
            rigid_bodies: 髪として判定された剛体のリスト。
            bone_joints: ボーン名とMayaジョイント名のマッピング。
            bone_index_map: ボーンインデックスからMayaジョイント名へのマッピング。
            rigid_bodies_group: 剛体グループの名前。
        """
        self.logger.debug(f"髪物理の作成を開始: {len(rigid_bodies)}個の剛体")

        for rb in rigid_bodies:
            try:
                # 関連するボーンを取得
                bone_name = self._get_bone_name_from_rigid_body(rb, bone_index_map)
                if bone_name not in bone_joints:
                    self.logger.warning(f"剛体 '{rb.name}' に対応するボーンが見つかりません")
                    continue

                joint_name = bone_joints[bone_name]

                # 髪のカーブを作成（簡易版）
                curve = self._create_dynamic_curve_for_joint(joint_name, rb)
                if curve:
                    # nHairを作成
                    hair_system = self._create_nhair_system(curve, rb)
                    if hair_system:
                        self.created_ncloth_nodes.append(hair_system)
                        # ノードを剛体グループに配置
                        maya_utils.parent_objects(hair_system, rigid_bodies_group)

            except Exception as e:
                self.logger.error(f"髪物理作成中にエラー: {rb.name} - {str(e)}")

    def _create_cloth_physics(
        self,
        rigid_bodies: List,
        bone_joints: Dict[str, str],
        bone_index_map: Dict[int, str],
        rigid_bodies_group: str,
    ):
        """
        布用の物理シミュレーションを作成する。

        Args:
            rigid_bodies: 布として判定された剛体のリスト。
            bone_joints: ボーン名とMayaジョイント名のマッピング。
            bone_index_map: ボーンインデックスからMayaジョイント名へのマッピング。
            rigid_bodies_group: 剛体グループの名前。
        """
        self.logger.debug(f"布物理の作成を開始: {len(rigid_bodies)}個の剛体")

        # Phase 1では基本的な実装のみ
        for rb in rigid_bodies:
            try:
                # 簡易的なnClothプロキシを作成
                proxy_mesh = self._create_cloth_proxy(rb)
                if proxy_mesh:
                    ncloth = self._create_ncloth(proxy_mesh, rb)
                    if ncloth:
                        self.created_ncloth_nodes.append(ncloth)
                        # ノードを剛体グループに配置
                        maya_utils.parent_objects(ncloth, rigid_bodies_group)

            except Exception as e:
                self.logger.error(f"布物理作成中にエラー: {rb.name} - {str(e)}")

    def _create_rigid_physics(
        self,
        rigid_bodies: List,
        bone_joints: Dict[str, str],
        bone_index_map: Dict[int, str],
        rigid_bodies_group: str,
    ):
        """
        剛体物理を作成する。

        Args:
            rigid_bodies: 剛体として判定された剛体のリスト。
            bone_joints: ボーン名とMayaジョイント名のマッピング。
            bone_index_map: ボーンインデックスからMayaジョイント名へのマッピング。
            rigid_bodies_group: 剛体グループの名前。
        """
        self.logger.debug(f"剛体物理の作成を開始: {len(rigid_bodies)}個の剛体")

        for rb in rigid_bodies:
            try:
                # コリジョンオブジェクトとしてnRigidを作成
                collision_obj = self._create_collision_object(rb)
                if collision_obj:
                    nrigid = self._create_nrigid(collision_obj, rb)
                    if nrigid:
                        self.created_nrigid_nodes.append(nrigid)
                        # ノードを剛体グループに配置
                        maya_utils.parent_objects(nrigid, rigid_bodies_group)

            except Exception as e:
                self.logger.error(f"剛体物理作成中にエラー: {rb.name} - {str(e)}")

    def _create_constraints(self, joints: List, rigid_bodies: List, constraints_group: str):
        """
        MMDのジョイントデータからMayaのコンストレインを作成する。

        Args:
            joints: ジョイントデータのリスト。
            rigid_bodies: 剛体データのリスト。
            constraints_group: コンストレイントグループの名前。
        """
        self.logger.debug(f"コンストレインの作成を開始: {len(joints)}個のジョイント")

        # Phase 1では基本的な実装のみ
        # TODO: Phase 2以降で詳細な実装を追加
        pass

    def _map_physics_parameters(self, mmd_params: Dict) -> Dict:
        """
        MMDパラメータをnClothパラメータに変換する。

        Args:
            mmd_params (Dict): MMDの物理パラメータ。

        Returns:
            Dict: nClothの属性辞書。
        """
        ncloth_params = {}

        # 質量 → 厚み
        if "mass" in mmd_params:
            ncloth_params["thickness"] = mmd_params["mass"] * 0.1

        # 速度減衰 → ダンプ
        if "velocity_attenuation" in mmd_params:
            ncloth_params["damp"] = mmd_params["velocity_attenuation"]

        # 回転減衰 → 曲げ抵抗
        if "rotation_attenuation" in mmd_params:
            ncloth_params["bendResistance"] = mmd_params["rotation_attenuation"] * 10.0

        # 摩擦
        if "friction" in mmd_params:
            ncloth_params["friction"] = mmd_params["friction"]

        # 反発係数
        if "elasticity" in mmd_params:
            ncloth_params["bounce"] = mmd_params["elasticity"]

        return ncloth_params

    # ヘルパーメソッド

    def _create_bone_index_mapping(self, bones, bone_joints: Dict[str, str]) -> Dict[int, str]:
        """
        ボーンインデックスからMayaジョイント名へのマッピングを作成する。

        Args:
            bones: ボーンデータのリスト。
            bone_joints: ボーン名とMayaジョイント名のマッピング。

        Returns:
            Dict[int, str]: ボーンインデックスからMayaジョイント名へのマッピング。
        """
        bone_index_map = {}

        for i, bone in enumerate(bones):
            bone_name = maya_utils.sanitize_text(bone.get_name())
            if bone_name in bone_joints:
                bone_index_map[i] = bone_joints[bone_name]

        return bone_index_map

    def _mmd_to_maya_position(self, position: Tuple[float, float, float]) -> List[float]:
        """
        MMDの座標系をMayaの座標系に変換する。
        MMD: 右手座標系 (X右, Y上, Z手前)
        Maya: 右手座標系 (X右, Y上, Z後ろ)

        Args:
            position: MMDの位置座標 (x, y, z)

        Returns:
            List[float]: Mayaの位置座標 [x, y, z]
        """
        return [position[0], position[1], -position[2]]

    def _mmd_to_maya_rotation(self, rotation: Tuple[float, float, float]) -> List[float]:
        """
        MMDの回転角度をMayaの回転角度に変換する。
        両方ともオイラー角（度数法）を使用。

        Args:
            rotation: MMDの回転角度 (x, y, z) ラジアン

        Returns:
            List[float]: Mayaの回転角度 [x, y, z] 度数
        """
        # ラジアンから度数に変換し、Z軸を反転
        deg_x = math.degrees(rotation[0])
        deg_y = math.degrees(rotation[1])
        deg_z = math.degrees(rotation[2])

        return [deg_x, deg_y, -deg_z]

    def _get_bone_name_from_rigid_body(self, rigid_body, bone_index_map: Optional[Dict[int, str]] = None) -> Optional[str]:
        """剛体から関連するボーン名を取得する。"""
        # PMD/PMXの仕様に基づいて実装
        if hasattr(rigid_body, "bone_index"):
            # PMDの場合
            if bone_index_map and rigid_body.bone_index in bone_index_map:
                return maya_utils.sanitize_text(bone_index_map[rigid_body.bone_index])
            return f"bone_{rigid_body.bone_index}"
        elif hasattr(rigid_body, "related_bone_index"):
            # PMXの場合
            if bone_index_map and rigid_body.related_bone_index in bone_index_map:
                return maya_utils.sanitize_text(bone_index_map[rigid_body.related_bone_index])
            return f"bone_{rigid_body.related_bone_index}"
        return None

    def _create_dynamic_curve_for_joint(self, joint_name: str, rigid_body) -> Optional[str]:
        """ジョイント用のダイナミックカーブを作成する。"""
        try:
            # ジョイントの位置を取得
            pos = cmds.xform(joint_name, q=True, ws=True, t=True)

            # 簡易的なカーブを作成（2点のみ）
            end_pos = [pos[0], pos[1] - 5.0, pos[2]]  # 下方向に伸ばす
            curve = maya_utils.create_dynamic_curve([pos, end_pos], name=f"{rigid_body.name}_curve")

            return curve

        except Exception as e:
            self.logger.error(f"カーブ作成エラー: {str(e)}")
            return None

    def _create_nhair_system(self, curve: str, rigid_body) -> Optional[str]:
        """nHairシステムを作成する。"""
        try:
            # nHairシステムを適用
            hair_system = maya_utils.apply_nhair_to_curve(curve)

            if hair_system:
                # パラメータを設定
                mmd_params = {
                    "mass": rigid_body.mass,
                    "velocity_attenuation": rigid_body.velocity_attenuation,
                    "rotation_attenuation": rigid_body.rotation_attenuation,
                    "friction": rigid_body.friction,
                    "elasticity": rigid_body.elasticity,
                }

                ncloth_params = self._map_physics_parameters(mmd_params)

                # hairSystemにパラメータを適用
                for attr, value in ncloth_params.items():
                    if cmds.attributeQuery(attr, node=hair_system, exists=True):
                        # アトリビュートのタイプを取得
                        attr_type = cmds.getAttr(f"{hair_system}.{attr}", type=True)
                        maya_utils.set_attribute(hair_system, attr, value, attr_type)

                return hair_system

        except Exception as e:
            self.logger.error(f"nHairシステム作成エラー: {str(e)}")
            return None

    def _create_cloth_proxy(self, rigid_body) -> Optional[str]:
        """布物理用のプロキシメッシュを作成する。"""
        try:
            # 簡易的な平面を作成
            proxy = cmds.polyPlane(
                name=f"{rigid_body.name}_proxy",
                width=rigid_body.size[0] * 2,
                height=rigid_body.size[2] * 2,
                subdivisionsX=5,
                subdivisionsY=5,
            )[0]

            # 位置を設定（座標変換を適用）
            maya_pos = self._mmd_to_maya_position(rigid_body.position)
            cmds.xform(proxy, ws=True, t=maya_pos)

            return proxy

        except Exception as e:
            self.logger.error(f"布プロキシ作成エラー: {str(e)}")
            return None

    def _create_ncloth(self, mesh: str, rigid_body) -> Optional[str]:
        """メッシュにnClothを適用する。"""
        try:
            # nClothを適用
            ncloth_shape = maya_utils.apply_ncloth_to_mesh(mesh, self.nucleus_solver)

            if ncloth_shape:
                # パラメータを設定
                mmd_params = {
                    "mass": rigid_body.mass,
                    "velocity_attenuation": rigid_body.velocity_attenuation,
                    "rotation_attenuation": rigid_body.rotation_attenuation,
                    "friction": rigid_body.friction,
                    "elasticity": rigid_body.elasticity,
                }

                ncloth_params = self._map_physics_parameters(mmd_params)

                # nClothにパラメータを適用
                for attr, value in ncloth_params.items():
                    if cmds.attributeQuery(attr, node=ncloth_shape, exists=True):
                        # アトリビュートのタイプを取得
                        attr_type = cmds.getAttr(f"{ncloth_shape}.{attr}", type=True)
                        maya_utils.set_attribute(ncloth_shape, attr, value, attr_type)

                return ncloth_shape

        except Exception as e:
            self.logger.error(f"nCloth作成エラー: {str(e)}")
            return None

    def _create_collision_object(self, rigid_body) -> Optional[str]:
        """コリジョンオブジェクトを作成する。"""
        try:
            # 形状タイプに応じてオブジェクトを作成
            obj = maya_utils.create_collision_primitive(
                rigid_body.shape_type,
                rigid_body.size,
                name=f"{rigid_body.name}_collision",
            )

            # 位置と回転を設定（座標変換を適用）
            maya_pos = self._mmd_to_maya_position(rigid_body.position)
            cmds.xform(obj, ws=True, t=maya_pos)

            if hasattr(rigid_body, "rotation"):
                maya_rot = self._mmd_to_maya_rotation(rigid_body.rotation)
                cmds.xform(obj, ws=True, ro=maya_rot)

            return obj

        except Exception as e:
            self.logger.error(f"コリジョンオブジェクト作成エラー: {str(e)}")
            return None

    def _create_nrigid(self, obj: str, rigid_body) -> Optional[str]:
        """オブジェクトにnRigidを適用する。"""
        try:
            # 静的/動的の判定（PMDとPMXで属性名が異なる）
            is_dynamic = True
            if hasattr(rigid_body, "physics_mode"):
                # PMD: 0=静的, 1=動的, 2=動的（ボーン位置合わせ）
                is_dynamic = rigid_body.physics_mode != 0
            elif hasattr(rigid_body, "mode"):
                # 念のため旧実装もサポート
                is_dynamic = rigid_body.mode != 0

            # nRigidを適用
            nrigid = maya_utils.apply_nrigid_to_mesh(obj, is_dynamic)

            if nrigid:
                # その他のパラメータ設定
                maya_utils.set_attribute(nrigid, "friction", rigid_body.friction, "double")
                maya_utils.set_attribute(nrigid, "bounce", rigid_body.elasticity, "double")

                return nrigid

        except Exception as e:
            self.logger.error(f"nRigid作成エラー: {str(e)}")
            return None
