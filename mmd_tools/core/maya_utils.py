from maya import cmds

from . import maya_attribute_utils as _maya_attribute_utils
from . import maya_animation_utils as _maya_animation_utils
from . import maya_material_utils as _maya_material_utils
from . import maya_mesh_utils as _maya_mesh_utils
from . import maya_name_utils as _maya_name_utils
from . import maya_physics_utils as _maya_physics_utils
from . import maya_rig_utils as _maya_rig_utils
from . import maya_scene_utils as _maya_scene_utils
from . import maya_transform_utils as _maya_transform_utils
from . import maya_viewport_utils as _maya_viewport_utils
from .logger import get_logger

logger = get_logger(__name__)


sanitize_text = _maya_name_utils.sanitize_text
sanitize_bone_name = _maya_name_utils.sanitize_bone_name


set_custom_attributes = _maya_attribute_utils.set_custom_attributes
add_numeric_attribute = _maya_attribute_utils.add_numeric_attribute
add_typed_attribute = _maya_attribute_utils.add_typed_attribute
repair_fbx_mojibake_string = _maya_attribute_utils.repair_fbx_mojibake_string
set_attribute = _maya_attribute_utils.set_attribute
get_attribute = _maya_attribute_utils.get_attribute
attribute_exists = _maya_attribute_utils.attribute_exists
get_attr_safe = _maya_attribute_utils.get_attr_safe
read_json_attr = _maya_attribute_utils.read_json_attr
write_json_attr = _maya_attribute_utils.write_json_attr
find_tagged_nodes = _maya_attribute_utils.find_tagged_nodes
mark_bool_tag = _maya_attribute_utils.mark_bool_tag
disconnect_sources = _maya_attribute_utils.disconnect_sources
connect_if_needed = _maya_attribute_utils.connect_if_needed
get_int_array_attribute = _maya_attribute_utils.get_int_array_attribute


set_viewport_backface_culling = _maya_viewport_utils.set_viewport_backface_culling
setup_mmd_color_management = _maya_viewport_utils.setup_mmd_color_management
TRANSPARENCY_ALGORITHM_DEPTH_PEELING = _maya_viewport_utils.TRANSPARENCY_ALGORITHM_DEPTH_PEELING
setup_mmd_transparency = _maya_viewport_utils.setup_mmd_transparency


create_ik_handle = _maya_rig_utils.create_ik_handle
set_joint_limits = _maya_rig_utils.set_joint_limits
create_pole_vector_constraint = _maya_rig_utils.create_pole_vector_constraint


create_matrix_from_axes = _maya_transform_utils.create_matrix_from_axes
matrix_to_euler = _maya_transform_utils.matrix_to_euler

create_animation_curves = _maya_animation_utils.create_animation_curves
set_keyframes_batch = _maya_animation_utils.set_keyframes_batch


def find_all_mmd_models():
    """
    シーン内のすべてのMMDモデルのルートノードを検索します。

    Returns:
        list: MMDモデルのルートノード名のリスト
    """
    return _scene_model_service().list_mmd_models()


def get_parent_mmd_root(node_name):
    """
    指定されたノードの親階層からMMDモデルのルートノードを検索します。

    Args:
        node_name (str): 検索開始ノード名

    Returns:
        str: MMDモデルのルートノード名。見つからない場合はNone
    """
    return _scene_model_service().get_parent_mmd_root(node_name)


def get_mmd_model_display_name(root_node):
    """
    MMDモデルの表示名を取得します。

    Args:
        root_node (str): MMDモデルのルートノード名

    Returns:
        str: 表示名（日本語名があれば優先、なければノード名）
    """
    return _scene_model_service().get_model_display_name(root_node)


def _scene_model_service():
    """Return a service bound to this module's patchable Maya cmds object."""
    from ..services.scene_model_service import SceneModelService

    return SceneModelService(cmds_module=cmds)


select_objects = _maya_scene_utils.select_objects
object_exists = _maya_scene_utils.object_exists
parent_objects = _maya_scene_utils.parent_objects
list_objects = _maya_scene_utils.list_objects
_list_dg_nodes = _maya_scene_utils._list_dg_nodes


DX11_TEXTURE_SLOTS = _maya_material_utils.DX11_TEXTURE_SLOTS
ATTR_MMD_TEXTURE_SOURCE_KIND = _maya_material_utils.ATTR_MMD_TEXTURE_SOURCE_KIND
ATTR_MMD_SHARED_TOON_ID = _maya_material_utils.ATTR_MMD_SHARED_TOON_ID

sanitize_texture_path = _maya_material_utils.sanitize_texture_path
mark_mmd_texture_file_node = _maya_material_utils.mark_mmd_texture_file_node
get_mmd_original_texture_path = _maya_material_utils.get_mmd_original_texture_path
is_mmd_file_node_unreadable = _maya_material_utils.is_mmd_file_node_unreadable
find_material_texture_file_node = _maya_material_utils.find_material_texture_file_node
classify_mmd_texture_file_node = _maya_material_utils.classify_mmd_texture_file_node
resolve_mmd_texture_file_node = _maya_material_utils.resolve_mmd_texture_file_node
bind_dx11_texture_file_node = _maya_material_utils.bind_dx11_texture_file_node
create_material = _maya_material_utils.create_material
assign_material = _maya_material_utils.assign_material
assign_material_to_faces = _maya_material_utils.assign_material_to_faces

create_mesh_with_uvs = _maya_mesh_utils.create_mesh_with_uvs
split_mesh_by_material = _maya_mesh_utils.split_mesh_by_material
get_materials_from_mesh = _maya_mesh_utils.get_materials_from_mesh
apply_vertex_weights = _maya_mesh_utils.apply_vertex_weights
find_or_create_blendshape_node = _maya_mesh_utils.find_or_create_blendshape_node

find_or_create_nucleus_solver = _maya_physics_utils.find_or_create_nucleus_solver
create_dynamic_curve = _maya_physics_utils.create_dynamic_curve
apply_nhair_to_curve = _maya_physics_utils.apply_nhair_to_curve
apply_ncloth_to_mesh = _maya_physics_utils.apply_ncloth_to_mesh
create_collision_primitive = _maya_physics_utils.create_collision_primitive
apply_nrigid_to_mesh = _maya_physics_utils.apply_nrigid_to_mesh


def resolve_mmd_material_texture(material, workspace_root=None):
    """Resolve the selected material's base texture file node, if present."""
    file_node = find_material_texture_file_node(material)
    if not file_node:
        return None
    return resolve_mmd_texture_file_node(file_node, workspace_root=workspace_root)


def rebind_resolved_mmd_dx11_texture(file_node):
    """Compatibility wrapper for the material texture rebinding helper."""
    return _maya_material_utils.rebind_resolved_mmd_dx11_texture(file_node)


def rebind_resolved_scene_mmd_dx11_textures(results):
    """Compatibility wrapper for scene-level material texture rebinding."""
    return _maya_material_utils.rebind_resolved_scene_mmd_dx11_textures(results)


def resolve_scene_mmd_textures(workspace_root=None):
    """Compatibility wrapper for scene-level material texture resolution."""
    return _maya_material_utils.resolve_scene_mmd_textures(workspace_root=workspace_root)
