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

# マテリアルカスタムアトリビュート名
ATTR_MMD_MATERIAL_NAME = "mmd_material_name"
ATTR_MMD_MATERIAL_NAME_EN = "mmd_material_name_en"
ATTR_MMD_SPHERE_PATH = "mmd_sphere_path"
ATTR_MMD_SPHERE_MODE = "mmd_sphere_mode"
ATTR_MMD_MEMO = "mmd_memo"
ATTR_MMD_EDGE_FLAG = "mmd_edge_flag"
ATTR_MMD_DRAW_FLAGS = "mmd_draw_flags"
ATTR_MMD_EDGE_COLOR = "mmd_edge_color"
ATTR_MMD_EDGE_SIZE = "mmd_edge_size"
ATTR_MMD_TEXTURE_INDEX = "mmd_texture_index"
ATTR_MMD_SPHERE_TEXTURE_INDEX = "mmd_sphtex_index"
ATTR_MMD_SPHERE_MODE = "mmd_sphere_mode"
ATTR_MMD_SHARED_TOON_FLAG = "mmd_shared_toon_flag"
ATTR_MMD_TOON_TEXTURE_INDEX = "mmd_toon_texture_index"
ATTR_MMD_MATERIAL_INDEX = "mmd_material_index"

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
