"""
Maya MMD Toolsで使用される定数を定義するモジュール。
"""

# シーン階層のグループ名
SCENE_ROOT_SUFFIX = "_root"  # ルートグループのサフィックス（例: "ModelName_root"）
GEOMETRY_GROUP = "Geometry"  # メッシュを格納するグループ名
SKELETON_GROUP = "Skeleton"  # ボーン（ジョイント）階層を格納するグループ名
MORPHS_GROUP = "Morphs"  # モーフ関連ノードを格納するグループ名（現在未使用）
PHYSICS_GROUP = "Physics"  # 物理演算関連ノードを格納するグループ名
RIGID_BODIES_GROUP = "RigidBodies"  # 剛体（nCloth、nHair、nRigid）を格納するグループ名
CONSTRAINTS_GROUP = "Constraints"  # 物理コンストレイントを格納するグループ名

# ファイルタイプ
FILE_TYPE_PMX = "PMX"  # PMXファイル形式（MikuMikuDance 7.30以降の標準形式）
FILE_TYPE_PMD = "PMD"  # PMDファイル形式（MikuMikuDance 7.30以前の形式）
FILE_TYPE_VMD = "VMD"  # VMDファイル形式（モーションデータ）
FILE_TYPE_VPD = "VPD"  # VPDファイル形式（ポーズデータ）

# カスタムアトリビュート名
ATTR_MMD_FILE_TYPE = "mmd_file_type"
ATTR_MMD_FILE_VERSION = "mmd_file_version"
ATTR_MMD_MODEL_NAME = "mmd_model_name"
ATTR_MMD_MODEL_NAME_EN = "mmd_model_name_en"
ATTR_MMD_COMMENT = "mmd_comment"
ATTR_MMD_COMMENT_EN = "mmd_comment_en"
ATTR_MMD_DISPLAY_FRAMES_JSON = "mmd_display_frames_json"

# Animator Toolset viewport visibility state on the imported model root.
ATTR_MMD_SHOW_MESH = "mmd_show_mesh"
ATTR_MMD_SHOW_JOINTS = "mmd_show_joints"
ATTR_MMD_SHOW_PHYSICS_COLLIDERS = "mmd_show_physics_colliders"

# マテリアルカスタムアトリビュート名
ATTR_MMD_MATERIAL = "mmd_material"
ATTR_MMD_MATERIAL_NAME = "mmd_material_name"
ATTR_MMD_MATERIAL_NAME_EN = "mmd_material_name_en"
ATTR_MMD_DIFFUSE_COLOR = "diffuse_color"
ATTR_MMD_SPECULAR_COLOR = "specular_color"
ATTR_MMD_AMBIENT_COLOR = "ambient_color"
ATTR_MMD_SHININESS = "shininess"
ATTR_MMD_SPHERE_PATH = "mmd_sphere_path"
ATTR_MMD_SPHERE_MODE = "mmd_sphere_mode"
ATTR_MMD_MEMO = "mmd_memo"
ATTR_MMD_EDGE_FLAG = "edge_flag"
ATTR_MMD_DRAW_FLAGS = "mmd_draw_flags"
ATTR_MMD_EDGE_COLOR = "mmd_edge_color"
ATTR_MMD_EDGE_SIZE = "mmd_edge_size"
ATTR_MMD_SHADER_OUTLINE_ENABLED = "mmd_shader_outline_enabled"
ATTR_MMD_TEXTURE_INDEX = "mmd_texture_index"
ATTR_MMD_SPHERE_TEXTURE_INDEX = "mmd_sphtex_index"
ATTR_MMD_SPHERE_MODE = "mmd_sphere_mode"
ATTR_MMD_SHARED_TOON_FLAG = "mmd_shared_toon_flag"
ATTR_MMD_TOON_TEXTURE_INDEX = "mmd_toon_texture_index"
ATTR_MMD_MATERIAL_INDEX = "mmd_material_index"
ATTR_MMD_SOURCE_VERTEX_INDICES = "mmd_source_vertex_indices"
ATTR_MMD_ORIGINAL_TEXTURE_PATH = "mmd_original_texture_path"
ATTR_MMD_TEXTURE_UNRESOLVED = "mmd_texture_unresolved"
ATTR_MMD_TEXTURE_CACHE_PATH = "mmd_texture_cache_path"
ATTR_MMD_SOURCE_MODEL_PATH = "mmd_source_model_path"
# blendShape ノードに保存する「weight index → 元モーフ名」対応（JSON 文字列）。
# 頂点モーフは alias を sanitize_text で生成するため lossy だが、これは VMD/PMX が
# 参照する生のモーフ名 (PmxMorph.name) を権威キーとして保持し、正確なマッピングを保証する。
ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON = "mmd_blendshape_morph_names_json"

# ボーンカスタムアトリビュート名（共通）
ATTR_MMD_BONE_NAME = "mmd_bone_name"  # ボーン名（日本語）
ATTR_MMD_BONE_NAME_EN = "mmd_bone_name_en"  # ボーン名（英語）
ATTR_MMD_BONE_FLAGS = "mmd_bone_flags"  # ボーンフラグ
ATTR_MMD_DEFORM_LAYER = "mmd_deform_layer"  # 変形階層
ATTR_MMD_BONE_OFFSET = "mmd_bone_offset"  # ボーンオフセット（接続先）
ATTR_MMD_CONNECTION_BONE = "mmd_connection_bone"  # 接続ボーン名

# IK関連
ATTR_MMD_IK_TARGET = "mmd_ik_target"  # IKターゲットボーン名
ATTR_MMD_IK_LOOP = "mmd_ik_loop"  # IKループ回数
ATTR_MMD_IK_LIMIT_ANGLE = "mmd_ik_limit_angle"  # IK制限角度
ATTR_MMD_IK_LINKS = "mmd_ik_links"  # IKリンク（JSON文字列）

# 付与関連
ATTR_MMD_GRANT_PARENT = "mmd_grant_parent"  # 付与親ボーン名
ATTR_MMD_GRANT_RATE = "mmd_grant_rate"  # 付与率

# 軸制限関連
ATTR_MMD_FIXED_AXIS = "mmd_fixed_axis"  # 固定軸
ATTR_MMD_LOCAL_X_AXIS = "mmd_local_x_axis"  # ローカルX軸
ATTR_MMD_LOCAL_Z_AXIS = "mmd_local_z_axis"  # ローカルZ軸

# 外部親関連
ATTR_MMD_EXTERNAL_PARENT_KEY = "mmd_external_parent_key"  # 外部親キー

# ボーンの詳細アトリビュート名（フォーマット固有の情報を保存）
ATTR_MMD_BONE_INDEX = "mmd_bone_index"  # ボーンインデックス
ATTR_MMD_BONE_PARENT_INDEX = "mmd_bone_parent_index"  # 親ボーンインデックス
ATTR_MMD_CONNECT_TYPE = "mmd_connect_type"  # 接続タイプ
ATTR_MMD_CONNECT_INDEX = "mmd_connect_index"  # 接続先インデックス
ATTR_MMD_CONNECT_BONE_INDEX = "mmd_connect_bone_index"  # 接続先ボーンインデックス
ATTR_MMD_GRANT_PARENT_INDEX = "mmd_grant_parent_index"  # 付与親インデックス
ATTR_MMD_AXIS_DIRECTION = "mmd_axis_direction"  # 軸方向（軸固定時）
ATTR_MMD_X_AXIS_DIRECTION = "mmd_x_axis_direction"  # X軸方向（ローカル軸時）
ATTR_MMD_Z_AXIS_DIRECTION = "mmd_z_axis_direction"  # Z軸方向（ローカル軸時）
ATTR_MMD_IK_TARGET_INDEX = "mmd_ik_target_index"  # IKターゲットインデックス

# PMD固有のアトリビュート名（PMDのみに存在）
ATTR_MMD_BONE_TYPE = "mmd_bone_type"  # ボーンタイプ（PMD固有）
ATTR_MMD_TAIL_POS_INDEX = "mmd_tail_pos_index"  # テール位置ボーンインデックス（PMD固有）

# デフォルト値
DEFAULT_SCALE_FACTOR = 1.0  # インポート時のデフォルトスケール係数
DEFAULT_IMPORT_PHYSICS = True  # 物理演算をインポートするかのデフォルト設定
DEFAULT_IMPORT_MORPHS = True  # モーフをインポートするかのデフォルト設定

# 物理タイプ
PHYSICS_TYPE_HAIR = "hair"  # 髪の毛タイプ（nHairシステムを使用）
PHYSICS_TYPE_CLOTH = "cloth"  # 布タイプ（nClothシステムを使用）
PHYSICS_TYPE_RIGID = "rigid"  # 剛体タイプ（nRigidシステムを使用）
PHYSICS_TYPE_SOFT = "soft"  # ソフトボディタイプ（将来の拡張用）

# カメラ・照明関連
ATTR_MMD_CAMERA = "mmd_camera"  # MMDカメラマーカー
ATTR_MMD_LIGHT = "mmd_light"  # MMD照明マーカー
DEFAULT_CAMERA_NAME = "mmd_camera"  # MMDカメラのデフォルト名
DEFAULT_LIGHT_NAME = "mmd_light"  # MMD照明のデフォルト名
