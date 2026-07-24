"""
ctypes signature binding helpers for the mmd-anim runtime FFI.

The runtime loader imports these helpers after loading a CDLL. Keeping the
signature table separate avoids mixing ABI binding details with handle wrapper
classes and Maya scene utilities.
"""

from __future__ import annotations

from ctypes import CDLL, POINTER, c_bool, c_char_p, c_float, c_int32, c_size_t, c_uint8, c_uint32, c_uint64, c_void_p
from typing import Any, List

from mmd_tools.core.logger import get_logger
from mmd_tools.core.native.mmd_anim_runtime_types import (
    MmdRuntimeFfiAppendConfig,
    MmdRuntimeFfiByteBuffer,
    MmdRuntimeFfiGenericCurveDescriptor,
    MmdRuntimeFfiGenericCurveInfo,
    MmdRuntimeFfiGenericCurveKey,
    MmdRuntimeFfiIkSolveStats,
    MmdRuntimeFfiPoseReductionReport,
    MmdRuntimeFfiPhysicsJointDesc,
    MmdRuntimeFfiHostPoseView,
    MmdRuntimeFfiPhysicsRigidbodyBinding,
    MmdRuntimeFfiPhysicsRigidbodyDesc,
    MmdRuntimeFfiPhysicsStepStats,
    MmdRuntimeFfiPhysicsTickConfig,
    MmdRuntimeFfiPhysicsWorldStepReport,
    MmdRuntimeFfiReductionTolerances,
    MmdRuntimeFfiRigBone,
    MmdRuntimeFfiRigBoneLocalAxisV2,
    MmdRuntimeFfiRigIkLink,
    MmdRuntimeModelDescriptor,
)

logger = get_logger(__name__)


def setup_function_signatures(lib: CDLL) -> None:
    """Set ctypes argtypes/restype for the core runtime ABI."""
    lib.mmd_runtime_abi_version.restype = c_uint32
    lib.mmd_runtime_abi_version.argtypes = []
    set_sig(lib, "mmd_runtime_feature_flags", c_uint32, [])

    lib.mmd_runtime_model_free.restype = None
    lib.mmd_runtime_model_free.argtypes = [c_void_p]
    lib.mmd_runtime_clip_free.restype = None
    lib.mmd_runtime_clip_free.argtypes = [c_void_p]
    lib.mmd_runtime_instance_free.restype = None
    lib.mmd_runtime_instance_free.argtypes = [c_void_p]

    lib.mmd_runtime_byte_buffer_free.restype = None
    lib.mmd_runtime_byte_buffer_free.argtypes = [MmdRuntimeFfiByteBuffer]

    lib.mmd_runtime_model_create_from_pmx_bytes.restype = c_void_p
    lib.mmd_runtime_model_create_from_pmx_bytes.argtypes = [POINTER(c_uint8), c_size_t]
    set_sig(
        lib,
        "mmd_runtime_model_create_from_descriptor",
        c_void_p,
        [POINTER(MmdRuntimeModelDescriptor)],
    )
    set_sig(lib, "mmd_runtime_export_vmd_animation_json", MmdRuntimeFfiByteBuffer, [POINTER(c_uint8), c_size_t])
    set_sig(lib, "mmd_runtime_export_pmx_model_json", MmdRuntimeFfiByteBuffer, [POINTER(c_uint8), c_size_t])
    set_sig(lib, "mmd_runtime_export_pmd_model_json", MmdRuntimeFfiByteBuffer, [POINTER(c_uint8), c_size_t])
    set_sig(
        lib,
        "mmd_runtime_export_pmx_from_parts",
        MmdRuntimeFfiByteBuffer,
        [
            POINTER(c_uint8),
            c_size_t,
            POINTER(c_float),
            c_size_t,
            POINTER(c_float),
            POINTER(c_float),
            POINTER(c_uint32),
            c_size_t,
            POINTER(c_uint32),
            POINTER(c_float),
            POINTER(c_float),
        ],
    )

    lib.mmd_runtime_clip_create_from_vmd_bytes_for_model.restype = c_void_p
    lib.mmd_runtime_clip_create_from_vmd_bytes_for_model.argtypes = [c_void_p, POINTER(c_uint8), c_size_t]
    set_sig(lib, "mmd_runtime_clip_frame_range", c_bool, [c_void_p, POINTER(c_uint32), POINTER(c_uint32)])
    set_sig(lib, "mmd_runtime_vmd_camera_track_create_from_vmd_bytes", c_void_p, [POINTER(c_uint8), c_size_t])
    set_sig(lib, "mmd_runtime_vmd_camera_track_sample", c_bool, [c_void_p, c_float, POINTER(c_float), c_size_t])
    set_sig(lib, "mmd_runtime_vmd_camera_track_free", None, [c_void_p])
    set_sig(lib, "mmd_runtime_vmd_sample_camera", c_bool, [POINTER(c_uint8), c_size_t, c_float, POINTER(c_float), c_size_t])
    set_sig(lib, "mmd_runtime_vmd_light_track_create_from_vmd_bytes", c_void_p, [POINTER(c_uint8), c_size_t])
    set_sig(lib, "mmd_runtime_vmd_light_track_frame_count", c_size_t, [c_void_p])
    set_sig(lib, "mmd_runtime_vmd_light_track_sample", c_bool, [c_void_p, c_float, POINTER(c_float), c_size_t])
    set_sig(lib, "mmd_runtime_vmd_light_track_free", None, [c_void_p])
    set_sig(lib, "mmd_runtime_vmd_sample_light", c_bool, [POINTER(c_uint8), c_size_t, c_float, POINTER(c_float), c_size_t])
    set_sig(lib, "mmd_runtime_vmd_self_shadow_track_create_from_vmd_bytes", c_void_p, [POINTER(c_uint8), c_size_t])
    set_sig(lib, "mmd_runtime_vmd_self_shadow_track_frame_count", c_size_t, [c_void_p])
    set_sig(lib, "mmd_runtime_vmd_self_shadow_track_sample", c_bool, [c_void_p, c_float, POINTER(c_float), c_size_t])
    set_sig(lib, "mmd_runtime_vmd_self_shadow_track_free", None, [c_void_p])
    set_sig(lib, "mmd_runtime_vmd_sample_self_shadow", c_bool, [POINTER(c_uint8), c_size_t, c_float, POINTER(c_float), c_size_t])

    lib.mmd_runtime_instance_create_for_model.restype = c_void_p
    lib.mmd_runtime_instance_create_for_model.argtypes = [c_void_p]

    lib.mmd_runtime_instance_evaluate_clip_frame.restype = c_bool
    lib.mmd_runtime_instance_evaluate_clip_frame.argtypes = [c_void_p, c_void_p, c_float]
    try:
        lib.mmd_runtime_instance_evaluate_clip_frame_with_ik_options.restype = c_bool
        lib.mmd_runtime_instance_evaluate_clip_frame_with_ik_options.argtypes = [
            c_void_p,
            c_void_p,
            c_float,
            c_float,
            c_uint32,
        ]
    except AttributeError:
        logger.debug("mmd-anim runtime does not expose evaluate_clip_frame_with_ik_options")
    try:
        lib.mmd_runtime_instance_evaluate_rest_pose.restype = c_bool
        lib.mmd_runtime_instance_evaluate_rest_pose.argtypes = [c_void_p]
    except AttributeError:
        logger.debug("mmd-anim runtime does not expose evaluate_rest_pose")

    lib.mmd_runtime_instance_world_matrix_f32_len.restype = c_size_t
    lib.mmd_runtime_instance_world_matrix_f32_len.argtypes = [c_void_p]
    lib.mmd_runtime_instance_copy_world_matrices.restype = c_bool
    lib.mmd_runtime_instance_copy_world_matrices.argtypes = [c_void_p, POINTER(c_float), c_size_t]

    set_sig(lib, "mmd_runtime_instance_clip_frame_batch_world_matrix_f32_len", c_size_t, [c_void_p, c_size_t])
    set_sig(lib, "mmd_runtime_instance_clip_frame_batch_morph_weight_f32_len", c_size_t, [c_void_p, c_size_t])
    set_sig(
        lib,
        "mmd_runtime_instance_evaluate_clip_frame_batch",
        c_bool,
        [c_void_p, c_void_p, c_float, c_float, c_size_t, c_uint32, POINTER(c_float), c_size_t, POINTER(c_float), c_size_t],
    )
    set_sig(
        lib,
        "mmd_runtime_compute_maya_local_channels",
        c_bool,
        [
            POINTER(c_float),
            c_size_t,
            POINTER(c_int32),
            c_size_t,
            POINTER(c_float),
            c_size_t,
            POINTER(c_float),
            c_size_t,
            POINTER(c_float),
            c_size_t,
            POINTER(c_uint8),
            c_size_t,
            c_size_t,
            POINTER(c_float),
            c_size_t,
        ],
    )
    set_sig(
        lib,
        "mmd_runtime_compute_maya_local_channels_batch",
        c_bool,
        [
            POINTER(c_float),
            c_size_t,
            c_size_t,
            POINTER(c_int32),
            c_size_t,
            POINTER(c_float),
            c_size_t,
            POINTER(c_float),
            c_size_t,
            POINTER(c_float),
            c_size_t,
            POINTER(c_uint8),
            c_size_t,
            c_size_t,
            POINTER(c_float),
            c_size_t,
        ],
    )
    try:
        lib.mmd_runtime_instance_skinning_matrix_f32_len.restype = c_size_t
        lib.mmd_runtime_instance_skinning_matrix_f32_len.argtypes = [c_void_p]
        lib.mmd_runtime_instance_copy_skinning_matrices.restype = c_bool
        lib.mmd_runtime_instance_copy_skinning_matrices.argtypes = [c_void_p, POINTER(c_float), c_size_t]
    except AttributeError:
        logger.debug("mmd-anim runtime does not expose skinning matrix copy ABI")

    lib.mmd_runtime_instance_morph_weight_len.restype = c_size_t
    lib.mmd_runtime_instance_morph_weight_len.argtypes = [c_void_p]
    lib.mmd_runtime_instance_copy_morph_weights.restype = c_bool
    lib.mmd_runtime_instance_copy_morph_weights.argtypes = [c_void_p, POINTER(c_float), c_size_t]

    lib.mmd_runtime_instance_ik_enabled_len.restype = c_size_t
    lib.mmd_runtime_instance_ik_enabled_len.argtypes = [c_void_p]
    lib.mmd_runtime_instance_copy_ik_enabled.restype = c_bool
    lib.mmd_runtime_instance_copy_ik_enabled.argtypes = [c_void_p, POINTER(c_uint8), c_size_t]

    setup_parsed_model_signatures(lib)
    setup_rig_primitive_signatures(lib)
    setup_physics_signatures(lib)
    setup_reduction_signatures(lib)


def setup_reduction_signatures(lib: CDLL) -> None:
    """Safely bind optional dense-pose reduction and generic curve symbols.

    The generic curve ABI was added after the v0.3.1 runtime shipped.  Every
    symbol is therefore optional so loading an older DLL remains successful and
    callers can choose their dense-bake fallback explicitly.
    """

    set_sig(
        lib,
        "mmd_runtime_reduced_pose_create_from_dense",
        c_uint32,
        [
            c_void_p,
            c_uint64,
            POINTER(c_float),
            c_size_t,
            POINTER(c_float),
            c_size_t,
            c_size_t,
            c_float,
            c_float,
            c_uint32,
            MmdRuntimeFfiReductionTolerances,
            POINTER(c_void_p),
        ],
    )
    set_sig(lib, "mmd_runtime_reduced_pose_free", None, [c_void_p])
    set_sig(lib, "mmd_runtime_reduced_pose_bone_count", c_size_t, [c_void_p])
    set_sig(lib, "mmd_runtime_reduced_pose_morph_count", c_size_t, [c_void_p])
    set_sig(
        lib,
        "mmd_runtime_reduced_pose_report",
        c_uint32,
        [c_void_p, POINTER(MmdRuntimeFfiPoseReductionReport)],
    )
    set_sig(
        lib,
        "mmd_runtime_reduced_pose_generic_curve_info",
        c_uint32,
        [c_void_p, POINTER(MmdRuntimeFfiGenericCurveInfo)],
    )
    set_sig(
        lib,
        "mmd_runtime_reduced_pose_generic_curve_count",
        c_uint32,
        [c_void_p, POINTER(c_size_t)],
    )
    set_sig(
        lib,
        "mmd_runtime_reduced_pose_generic_curve_descriptor",
        c_uint32,
        [c_void_p, c_size_t, POINTER(MmdRuntimeFfiGenericCurveDescriptor)],
    )
    set_sig(
        lib,
        "mmd_runtime_reduced_pose_generic_curve_keys",
        c_uint32,
        [
            c_void_p,
            c_size_t,
            POINTER(MmdRuntimeFfiGenericCurveKey),
            c_size_t,
            c_size_t,
            POINTER(c_size_t),
        ],
    )


def setup_physics_signatures(lib: CDLL) -> None:
    """Safely set optional native physics ABI signatures."""
    try:
        set_sig(lib, "mmd_runtime_last_error_message", c_char_p, [])
        set_sig(lib, "mmd_runtime_instance_get_physics_mode", c_uint32, [c_void_p, POINTER(c_uint32)])
        set_sig(lib, "mmd_runtime_instance_set_physics_mode", c_uint32, [c_void_p, c_uint32])
        set_sig(lib, "mmd_runtime_instance_reset_physics_tick", c_uint32, [c_void_p])
        set_sig(lib, "mmd_runtime_instance_evaluate_clip_frame_before_physics", c_uint32, [c_void_p, c_void_p, c_float])
        set_sig(
            lib,
            "mmd_runtime_instance_evaluate_clip_frame_before_physics_with_ik_options",
            c_uint32,
            [c_void_p, c_void_p, c_float, c_float, c_uint32],
        )
        set_sig(lib, "mmd_runtime_instance_evaluate_current_pose_before_physics", c_uint32, [c_void_p])
        set_sig(lib, "mmd_runtime_instance_apply_host_pose", c_uint32, [c_void_p, POINTER(MmdRuntimeFfiHostPoseView)])
        set_sig(
            lib,
            "mmd_runtime_instance_apply_host_pose_and_evaluate_before_physics",
            c_uint32,
            [c_void_p, POINTER(MmdRuntimeFfiHostPoseView)],
        )
        set_sig(lib, "mmd_runtime_instance_evaluate_current_pose_after_physics", c_uint32, [c_void_p])
        set_sig(
            lib,
            "mmd_runtime_instance_evaluate_current_pose_after_physics_with_ik_options",
            c_uint32,
            [c_void_p, c_float, c_uint32],
        )
        set_sig(
            lib,
            "mmd_runtime_instance_advance_physics_tick_clock",
            c_uint32,
            [c_void_p, c_float, POINTER(MmdRuntimeFfiPhysicsStepStats)],
        )
        set_sig(
            lib,
            "mmd_runtime_physics_world_create_from_pmx_bytes",
            c_uint32,
            [POINTER(c_uint8), c_size_t, POINTER(c_void_p)],
        )
        set_sig(lib, "mmd_runtime_physics_world_free", None, [c_void_p])
        set_sig(lib, "mmd_runtime_physics_world_reset", c_uint32, [c_void_p, c_void_p, POINTER(c_size_t)])
        set_sig(
            lib,
            "mmd_runtime_physics_world_step_runtime",
            c_uint32,
            [c_void_p, c_void_p, c_float, POINTER(MmdRuntimeFfiPhysicsWorldStepReport)],
        )
        set_sig(lib, "mmd_runtime_physics_world_rigidbody_count", c_uint32, [c_void_p, POINTER(c_size_t)])
        set_sig(lib, "mmd_runtime_physics_world_get_gravity", c_uint32, [c_void_p, POINTER(c_float)])
        set_sig(lib, "mmd_runtime_physics_world_set_gravity", c_uint32, [c_void_p, POINTER(c_float)])
        set_sig(
            lib,
            "mmd_runtime_physics_world_copy_rigidbody_states",
            c_uint32,
            [c_void_p, POINTER(c_float), c_size_t],
        )
        set_sig(
            lib,
            "mmd_runtime_physics_world_copy_rigidbody_bindings",
            c_uint32,
            [c_void_p, POINTER(MmdRuntimeFfiPhysicsRigidbodyBinding), c_size_t, POINTER(c_size_t)],
        )
        set_sig(
            lib,
            "mmd_runtime_physics_world_physics_driven_bone_mask",
            c_uint32,
            [c_void_p, POINTER(c_uint8), c_size_t],
        )
        set_sig(
            lib,
            "mmd_runtime_evaluate_host_frame",
            c_uint32,
            [
                c_void_p,
                c_void_p,
                POINTER(MmdRuntimeFfiHostPoseView),
                c_uint32,
                c_float,
                c_float,
                c_uint32,
                POINTER(MmdRuntimeFfiPhysicsWorldStepReport),
            ],
        )
        set_sig(
            lib,
            "mmd_runtime_physics_world_create",
            c_uint32,
            [
                POINTER(MmdRuntimeFfiPhysicsRigidbodyDesc),
                c_size_t,
                POINTER(MmdRuntimeFfiPhysicsJointDesc),
                c_size_t,
                POINTER(c_void_p),
            ],
        )
        set_sig(
            lib,
            "mmd_runtime_instance_get_physics_tick_config",
            c_uint32,
            [c_void_p, POINTER(MmdRuntimeFfiPhysicsTickConfig)],
        )
        set_sig(
            lib,
            "mmd_runtime_instance_set_physics_tick_config",
            c_uint32,
            [c_void_p, POINTER(MmdRuntimeFfiPhysicsTickConfig)],
        )
        set_sig(
            lib,
            "mmd_runtime_instance_apply_physics_world_matrices",
            c_uint32,
            [c_void_p, POINTER(c_float), c_size_t, POINTER(c_uint8), c_size_t, POINTER(c_size_t)],
        )
        set_sig(
            lib,
            "mmd_runtime_physics_world_bake_clip_frames",
            c_uint32,
            [
                c_void_p,
                c_void_p,
                c_void_p,
                c_float,
                c_float,
                c_float,
                c_size_t,
                POINTER(c_float),
                c_size_t,
                POINTER(c_float),
                c_size_t,
                POINTER(MmdRuntimeFfiPhysicsWorldStepReport),
            ],
        )
    except Exception as exc:
        logger.debug("Error while setting native physics ABI signatures: %s", exc)


def setup_parsed_model_signatures(lib: CDLL) -> None:
    """Safely set optional parsed-model ABI signatures."""
    try:
        set_sig(lib, "mmd_runtime_parsed_model_create_from_pmx_bytes", c_void_p, [POINTER(c_uint8), c_size_t])
        set_sig(lib, "mmd_runtime_parsed_model_free", None, [c_void_p])
        set_sig(lib, "mmd_runtime_parsed_model_vertex_count", c_size_t, [c_void_p])
        set_sig(lib, "mmd_runtime_parsed_model_index_count", c_size_t, [c_void_p])
        set_sig(lib, "mmd_runtime_parsed_model_material_group_count", c_size_t, [c_void_p])
        set_sig(lib, "mmd_runtime_parsed_model_vertex_morph_count", c_size_t, [c_void_p])
        set_sig(lib, "mmd_runtime_parsed_model_vertex_morph_offset_count", c_size_t, [c_void_p])
        set_sig(lib, "mmd_runtime_parsed_model_positions", c_void_p, [c_void_p])
        set_sig(lib, "mmd_runtime_parsed_model_normals", c_void_p, [c_void_p])
        set_sig(lib, "mmd_runtime_parsed_model_uvs", c_void_p, [c_void_p])
        set_sig(lib, "mmd_runtime_parsed_model_edge_scale", c_void_p, [c_void_p])
        set_sig(lib, "mmd_runtime_parsed_model_indices", c_void_p, [c_void_p])
        set_sig(lib, "mmd_runtime_parsed_model_skin_indices", c_void_p, [c_void_p])
        set_sig(lib, "mmd_runtime_parsed_model_skin_weights", c_void_p, [c_void_p])
        set_sig(lib, "mmd_runtime_parsed_model_material_groups", c_void_p, [c_void_p])
        set_sig(lib, "mmd_runtime_parsed_model_vertex_morph_spans", c_void_p, [c_void_p])
        set_sig(lib, "mmd_runtime_parsed_model_vertex_morph_vertex_indices", c_void_p, [c_void_p])
        set_sig(lib, "mmd_runtime_parsed_model_vertex_morph_position_offsets", c_void_p, [c_void_p])
        set_sig(lib, "mmd_runtime_parsed_model_vertex_morph_name", MmdRuntimeFfiByteBuffer, [c_void_p, c_size_t])
        set_sig(lib, "mmd_runtime_parsed_model_metadata_json", MmdRuntimeFfiByteBuffer, [c_void_p])
    except Exception as exc:
        logger.debug("Error while setting parsed-model ABI signatures: %s", exc)


def set_sig(lib: CDLL, name: str, restype: Any, argtypes: List[Any]) -> None:
    """Set argtypes/restype for an optional symbol when it exists."""
    func = getattr(lib, name, None)
    if func is None:
        logger.debug("parsed-model ABI symbol '%s' does not exist in the DLL", name)
        return
    func.restype = restype
    func.argtypes = argtypes


def setup_rig_primitive_signatures(lib: CDLL) -> None:
    """Safely set optional rig primitive ABI signatures."""
    try:
        set_sig(lib, "mmd_runtime_pmx_rig_spec_create", c_void_p, [POINTER(c_uint8), c_size_t])
        set_sig(lib, "mmd_runtime_pmx_rig_spec_free", None, [c_void_p])
        set_sig(lib, "mmd_runtime_pmx_rig_spec_manifest_json", MmdRuntimeFfiByteBuffer, [c_void_p])
        set_sig(
            lib,
            "mmd_runtime_ik_chain_create",
            c_void_p,
            [POINTER(MmdRuntimeFfiRigBone), c_size_t, c_uint32, POINTER(MmdRuntimeFfiRigIkLink), c_size_t, c_uint32, c_float],
        )
        set_sig(
            lib,
            "mmd_runtime_ik_chain_create_v2",
            c_void_p,
            [
                POINTER(MmdRuntimeFfiRigBone),
                c_size_t,
                POINTER(MmdRuntimeFfiRigBoneLocalAxisV2),
                c_uint32,
                POINTER(MmdRuntimeFfiRigIkLink),
                c_size_t,
                c_uint32,
                c_float,
            ],
        )
        set_sig(lib, "mmd_runtime_ik_chain_free", None, [c_void_p])
        set_sig(
            lib,
            "mmd_runtime_ik_chain_solve",
            c_bool,
            [
                c_void_p,
                POINTER(c_float),
                POINTER(c_float),
                POINTER(c_float),
                POINTER(c_float),
                c_float,
                c_uint32,
                POINTER(c_float),
                c_size_t,
                POINTER(MmdRuntimeFfiIkSolveStats),
            ],
        )
        set_sig(lib, "mmd_runtime_append_solver_create", c_void_p, [POINTER(MmdRuntimeFfiAppendConfig)])
        set_sig(lib, "mmd_runtime_append_solver_free", None, [c_void_p])
        set_sig(
            lib,
            "mmd_runtime_append_solver_solve",
            c_bool,
            [c_void_p, POINTER(c_float), POINTER(c_float), POINTER(c_float), POINTER(c_float)],
        )
    except Exception as exc:
        logger.debug("Error while setting rig primitive ABI signatures: %s", exc)
