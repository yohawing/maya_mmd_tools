"""Central setting-key constants for optionVar-backed plugin settings."""

IMPORT_GENERAL_SCALE_FACTOR = "import.general.scale_factor"
IMPORT_GENERAL_USE_NAMESPACE = "import.general.use_namespace"

IMPORT_MODEL_IMPORT_MODELS = "import.model.import_models"
IMPORT_MODEL_CREATE_MMD_SHADERS = "import.model.create_mmd_shaders"
IMPORT_MODEL_SEPARATE_MESHES_BY_MATERIAL = "import.model.separate_meshes_by_material"
IMPORT_MODEL_AUTO_RESOLVE_TEXTURES = "import.model.auto_resolve_textures"
IMPORT_MODEL_DISABLE_BACKFACE_CULLING = "import.model.disable_backface_culling"
IMPORT_MODEL_UV_SET_NAME = "import.model.uv_set_name"
IMPORT_MODEL_TEXTURE_SEARCH_PATH = "import.model.texture_search_path"
IMPORT_MODEL_SHOW_TEXTURE_ISSUE_DIALOG = "import.model.show_texture_issue_dialog"
IMPORT_MODEL_MMD_SHADER_BACKEND = "import.model.mmd_shader_backend"
IMPORT_MODEL_CREATE_MMD_CONTROL_RIG = "import.model.create_mmd_control_rig"

IMPORT_PHYSICS_IMPORT_PHYSICS = "import.physics.import_physics"

IMPORT_MORPH_IMPORT_MORPHS = "import.morph.import_morphs"
IMPORT_MORPH = "import.morph"

IMPORT_RIG_ADD_SEMI_STANDARD_BONES = "import.rig.add_semi_standard_bones"
IMPORT_RIG_BAKE_MODE = "import.rig.bake_mode"

IMPORT_NAMING_TRANSLATE_NAMES = "import.naming.translate_names"

IMPORT_NATIVE_USE_CPP_FAST_LOAD = "import.native.use_cpp_fast_load"
IMPORT_NATIVE_CPP_FAST_LOAD_MESH_ONLY = "import.native.cpp_fast_load_mesh_only"
IMPORT_NATIVE_USE_CPP_VP2_OWNERSHIP = "import.native.use_cpp_vp2_ownership"
IMPORT_NATIVE_REQUIRE_NATIVE_PMX_PARSE = "import.native.require_native_pmx_parse"
IMPORT_NATIVE_USE_CPP_RIG_NODES = "import.native.use_cpp_rig_nodes"

IMPORT_ANIMATION_START_FRAME = "import.animation.animation_start_frame"
IMPORT_ANIMATION_VMD_FPS = "import.animation.vmd_fps"
IMPORT_ANIMATION_IMPORT_ANIMATIONS = "import.animation.import_animations"
IMPORT_ANIMATION_IMPORT_MORPH_ANIMATION = "import.animation.import_morph_animation"
IMPORT_ANIMATION_IMPORT_CAMERA_ANIMATION = "import.animation.import_camera_animation"
IMPORT_ANIMATION_IMPORT_LIGHT_ANIMATION = "import.animation.import_light_animation"
IMPORT_ANIMATION_USE_NATIVE_PHYSICS_BAKE = "import.animation.use_native_physics_bake"
IMPORT_ANIMATION_REDUCE_BAKE_KEYS = "import.animation.reduce_bake_keys"
IMPORT_ANIMATION_REDUCE_QUALITY = "import.animation.reduce_quality"
IMPORT_ANIMATION_MOTION_SCALE = "import.animation.motion_scale"
IMPORT_ANIMATION_CLEAR_EXISTING_MOTION = "import.animation.clear_existing_motion"
IMPORT_ANIMATION_VMD_ROTATION_TIME_CURVE = "import.animation.vmd_rotation_time_curve"
# Kept as a compatibility alias for callers that used the former animation
# setting name.  The persisted setting is model-scoped and has one authority.
IMPORT_ANIMATION_CREATE_MMD_CONTROL_RIG = IMPORT_MODEL_CREATE_MMD_CONTROL_RIG
IMPORT_ANIMATION_RESAMPLE_CURVES = "import.animation.resample_curves"
IMPORT_ANIMATION_STATIC_CHANNEL_EPSILON_TRANSLATE = "import.animation.static_channel_epsilon_translate"
IMPORT_ANIMATION_STATIC_CHANNEL_EPSILON_ROTATE_DEG = "import.animation.static_channel_epsilon_rotate_deg"

IMPORT_LIGHT_CREATE_CONTROLLER = "import.light.create_controller"
IMPORT_VIEW_SETUP_COLOR_MANAGEMENT = "import.view.setup_color_management"
IMPORT_VIEW_SETUP_TRANSPARENCY = "import.view.setup_transparency"

EXPORT_GENERAL_APPLY_SCALE = "export.general.apply_scale"
EXPORT_MOTION_STRATEGY = "export.motion.strategy"
EXPORT_MOTION_USE_FRAME_RANGE = "export.motion.use_frame_range"
EXPORT_MOTION_START_FRAME = "export.motion.start_frame"
EXPORT_MOTION_END_FRAME = "export.motion.end_frame"
EXPORT_MOTION_RANGE_INITIALIZED = "export.motion.range_initialized"
EXPORT_CAMERA_USE_FRAME_RANGE = "export.camera.use_frame_range"
EXPORT_CAMERA_START_FRAME = "export.camera.start_frame"
EXPORT_CAMERA_END_FRAME = "export.camera.end_frame"
EXPORT_CAMERA_RANGE_INITIALIZED = "export.camera.range_initialized"

UI_GENERAL_DEVELOPMENT_MODE = "ui.general.development_mode"
UI_GENERAL_LANGUAGE = "ui.general.language"
UI_GENERAL_FILE_HISTORY_LIMIT = "ui.general.file_history_limit"
UI_DEV_COMMAND_PORT = "ui.dev.command_port"

LOGGING_ENABLED = "logging.enabled"
LOGGING_LEVEL = "logging.level"
LOGGING_LOG_FILE_PATH = "logging.log_file_path"

# Option-dict keys that must be mirrored into global settings while importing.
MODEL_OPTION_TO_SETTINGS_KEY = {
    "separate_meshes_by_material": IMPORT_MODEL_SEPARATE_MESHES_BY_MATERIAL,
    "auto_resolve_textures": IMPORT_MODEL_AUTO_RESOLVE_TEXTURES,
    "disable_backface_culling": IMPORT_MODEL_DISABLE_BACKFACE_CULLING,
    "uv_set_name": IMPORT_MODEL_UV_SET_NAME,
    "texture_search_path": IMPORT_MODEL_TEXTURE_SEARCH_PATH,
    "add_semi_standard_bones": IMPORT_RIG_ADD_SEMI_STANDARD_BONES,
    "import_morphs": IMPORT_MORPH_IMPORT_MORPHS,
    "translate_names": IMPORT_NAMING_TRANSLATE_NAMES,
}
