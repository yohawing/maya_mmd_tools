"""Small read-only entry points for external Maya plug-ins and scripts.

Importing this module does not initialize Maya. Call these functions inside
Maya; returned node paths identify the current scene, not persistent assets.
"""

from __future__ import annotations

__all__ = ["list_models", "get_model_skeleton"]


def list_models() -> tuple[str, ...]:
    """Return sorted, full DAG paths of the scene's MMD model roots."""
    from .services.scene_model_service import SceneModelService

    return tuple(SceneModelService().list_mmd_models())


def get_model_skeleton(model_root: str) -> dict:
    """Return a JSON-serializable snapshot of one model's imported bones.

    ``model_root`` must resolve to exactly one MMD model root. ``bones`` is
    sorted by PMX ``index``; ``parent_index`` is the PMX parent (-1 for no
    parent), while ``joint`` is the current full Maya DAG path. Names are the
    stored PMX Japanese/English names, independent of Maya namespaces.

    ``pmx_rest_position`` is the absolute rest position in PMX coordinates.
    ``bind_world_matrix`` contains 16 row-major values in Maya world space
    (Maya matrix convention, translation in centimeters). It comes from saved
    bind data or importer rest metadata, never the current animated pose.

    Raises:
        ValueError: The root is missing, ambiguous, or not an MMD model root.
        MayaSceneMetadataError: Stored bone metadata is invalid or ambiguous.
        BindBasisResolutionError: A bone's bind basis cannot be resolved.

    No nodes, selection, time, or connections are changed. This snapshot does
    not authorize writing joint channels or connecting an external rig.
    """
    from .adapters import MayaCmdsAdapter
    from .adapters.maya_bone_metadata_repository import MayaBoneMetadataRepository
    from .adapters.maya_metadata_read_support import MayaMetadataReadSupport
    from .adapters.maya_scene_metadata_backend import MayaSceneMetadataError
    from .services.scene_model_service import SceneModelService

    if not isinstance(model_root, str) or not model_root.strip():
        raise ValueError("model_root must be a non-empty Maya node path")
    adapter = MayaCmdsAdapter()
    roots = adapter.ls(model_root, long=True) or []
    if len(roots) != 1:
        raise ValueError("model_root must resolve to exactly one existing node")
    root = roots[0]
    service = SceneModelService(cmds_adapter=adapter)
    if not service.object_exists(root) or root not in service.list_mmd_models():
        raise ValueError("model_root must identify an existing MMD model root")

    support = MayaMetadataReadSupport(adapter, error_factory=MayaSceneMetadataError)
    repository = MayaBoneMetadataRepository(support, error_factory=MayaSceneMetadataError)
    metadata = sorted(repository.iter_bone_metadata(root), key=lambda bone: bone["index"])

    from .core.physics_bind_basis import resolve_imported_bind_world_matrix

    bones = []
    for bone in metadata:
        joint = bone["binding_identity"]
        matrix = resolve_imported_bind_world_matrix(joint)
        bones.append({
            "index": bone["index"],
            "name": bone["name"],
            "name_english": bone["name_english"],
            "parent_index": bone["parent_index"],
            "joint": joint,
            "pmx_rest_position": list(bone["rest_position"]),
            "bind_world_matrix": [float(value) for value in matrix],
        })
    return {"model_root": root, "bones": bones}
