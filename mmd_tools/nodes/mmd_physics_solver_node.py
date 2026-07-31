"""mmdPhysicsSolver — Stateful MMD physics solver DG node (Python MPxNode).

Maintains a Bullet physics world via the mmd-anim FFI and steps it in response
to Maya's time evaluation.  Outputs bone world matrices (Maya-space) as a flat
doubleArray plus metadata.

Time state machine:
- same time → idempotent (cached result); Maya-pose inputs are compared first
- forward step → step_runtime(dt)
- bounded forward jump → fixed-step catch-up
- backward / first eval / oversized jump → reset

inputMode attribute:
- 0 (rest-only): solver uses mmd-anim rest pose only, no Maya joint reading
- 1 (maya-pose): solver reads kinematic bone world matrices from Maya joints
  and injects them via apply_physics_world_matrices before each step

This is the Python prototype; a C++ version with the same TypeId will replace
it when the C++ plugin is loaded (mutual-exclusion pattern).
"""

from __future__ import annotations

import json

import maya.api.OpenMaya as om

from mmd_tools.core.logger import get_logger
from mmd_tools.core.native.mmd_anim_runtime import is_native_physics_available
from mmd_tools.core.physics_bind_basis import (
    BIND_BASIS_MISSING,
    BIND_BASIS_SINGULAR,
    BindBasisResolutionError,
    resolve_imported_bind_world_matrix,
)


logger = get_logger(__name__)


def maya_useNewAPI():
    pass


_TIME_EPSILON = 1e-6
_FIXED_STEP_DT = 1.0 / 30.0
_MAX_CATCH_UP_DT = 2.0
_MAX_CATCH_UP_STEPS = 60

INPUT_MODE_REST = 0
INPUT_MODE_MAYA_POSE = 1

_SIMULATED_RB_CACHE: dict[str, om.MMatrix] = {}


class MmdPhysicsSolverNode(om.MPxNode):
    kTypeName = "mmdPhysicsSolver"
    kTypeId = om.MTypeId(0x00128008)
    kClassify = "utility/general"

    aEnable = None
    aInputMode = None
    aInTime = None
    aModelRoot = None

    aInWorldSettings = None
    aInWorldSettingsVersion = None
    aInDescriptorVersion = None
    aInKinematicWorldMatrix = None

    aOutBoneMatrices = None
    aOutBoneCount = None
    aOutStatus = None
    aOutSolved = None

    def __init__(self):
        super().__init__()
        self._world = None
        self._model = None
        self._instance = None
        self._bone_count = 0
        self._bone_joints = []
        self._kinematic_corrections = {}
        self._kinematic_bone_indices: set = set()
        self._rb_shape_paths = {}
        self._rb_shape_mobjects = {}
        self._last_time = None
        self._cached_flat = None
        self._last_kinematic_pose_signature = None
        self._initialized = False
        self._last_reset_generation = -1
        self._last_world_settings_version = None
        self._last_descriptor_version = None
        self._initialization_failure_signatures: set[tuple] = set()
        self._initialization_failure_descriptor_version = None
        self._latched_validation_failure_descriptor_version = None

    def compute(self, plug, data):
        attr = plug.attribute()
        if attr not in (
            self.aOutBoneMatrices,
            self.aOutBoneCount,
            self.aOutStatus,
            self.aOutSolved,
        ):
            return None

        world_settings_version = data.inputValue(
            self.aInWorldSettingsVersion
        ).asInt()
        descriptor_version = data.inputValue(self.aInDescriptorVersion).asInt()

        enable = data.inputValue(self.aEnable).asBool()
        if not enable:
            self._write_outputs(data, solved=False, status="disabled")
            return

        # The world toggle is the production physics OFF control.  Check it
        # before descriptor collection/native world construction so a disabled
        # world remains cheap even when the solver node itself stays enabled.
        # Intentionally do not consume either version here: edits made while
        # OFF must invalidate the next enabled evaluation.
        world_enable, reset_gen = self._read_world_settings()
        if not world_enable:
            self._last_time = None
            self._write_outputs(data, solved=False, status="disabled")
            self._last_kinematic_pose_signature = None
            return

        world_settings_changed = (
            self._last_world_settings_version is not None
            and world_settings_version != self._last_world_settings_version
        )
        self._last_world_settings_version = world_settings_version
        descriptor_changed = (
            self._last_descriptor_version is not None
            and descriptor_version != self._last_descriptor_version
        )
        self._last_descriptor_version = descriptor_version

        input_mode = data.inputValue(self.aInputMode).asShort()
        current_time = data.inputValue(self.aInTime).asTime().asUnits(om.MTime.kSeconds)

        if descriptor_changed:
            self._free_handles()

        if (
            not self._initialized
            and self._latched_validation_failure_descriptor_version != descriptor_version
        ):
            self._try_initialize()

        if self._world is None or self._instance is None:
            self._last_kinematic_pose_signature = None
            self._write_outputs(data, solved=False, status="no physics data")
            return

        # Preserve the original post-initialization check as well: a world
        # toggle can change while initialization is in progress, and the
        # enabled path must retain its existing reset/disable semantics.
        world_enable, reset_gen = self._read_world_settings()
        if not world_enable:
            self._last_time = None
            self._last_kinematic_pose_signature = None
            self._write_outputs(data, solved=False, status="disabled")
            return

        force_reset = world_settings_changed or descriptor_changed
        if reset_gen != self._last_reset_generation:
            self._last_reset_generation = reset_gen
            force_reset = True

        same_time = (
            not force_reset
            and self._last_time is not None
            and abs(current_time - self._last_time) < _TIME_EPSILON
        )

        pose_input = None
        if same_time and input_mode == INPUT_MODE_MAYA_POSE:
            pose_input = self._read_kinematic_pose_inputs(data)
            if (
                pose_input is not None
                and pose_input[2] == self._last_kinematic_pose_signature
            ):
                self._write_outputs(data, solved=True, status="cached")
                return

        if same_time and input_mode != INPUT_MODE_MAYA_POSE:
            self._write_outputs(data, solved=True, status="cached")
            return

        if same_time:
            if not self._reset_world(input_mode, data, pose_input=pose_input):
                self._write_failure(data)
                return
            status = "pose-updated"
        else:
            dt = current_time - self._last_time if self._last_time is not None else None
            if (
                not force_reset
                and dt is not None
                and 0 < dt <= _MAX_CATCH_UP_DT + _TIME_EPSILON
            ):
                if not self._forward_catch_up(dt, input_mode, data):
                    self._write_failure(data)
                    return
                status = "stepped"
            else:
                if not self._reset_world(input_mode, data):
                    self._write_failure(data)
                    return
                status = "reset"

        if not self._update_cached_matrices():
            self._write_failure(data)
            return
        if input_mode != INPUT_MODE_MAYA_POSE:
            self._last_kinematic_pose_signature = None
        elif not self._kinematic_corrections:
            # No kinematic bones means the effective Maya-pose input is the
            # empty tuple; remember it so duplicate pulls remain idempotent.
            self._last_kinematic_pose_signature = ()
        self._last_time = current_time
        self._update_rigid_body_visual_cache()
        self._write_outputs(data, solved=True, status=status)

    def _forward_catch_up(self, dt: float, input_mode: int, data) -> bool:
        """Advance a bounded forward jump without one unstable large step."""
        remaining = min(dt, _MAX_CATCH_UP_DT)
        step_count = 0
        while remaining > _TIME_EPSILON and step_count < _MAX_CATCH_UP_STEPS:
            step_dt = min(_FIXED_STEP_DT, remaining)
            if not self._forward_step(step_dt, input_mode, data):
                return False
            remaining -= step_dt
            step_count += 1
        return remaining <= _TIME_EPSILON

    def _forward_step(self, dt: float, input_mode: int, data) -> bool:
        if not self._instance.evaluate_rest_pose():
            return False
        if input_mode == INPUT_MODE_MAYA_POSE and self._kinematic_corrections:
            if not self._inject_kinematic_poses(data):
                return False
            if not self._instance.evaluate_current_pose_before_physics():
                return False
        if self._world.step_runtime(self._instance, dt) is None:
            return False
        return self._instance.evaluate_current_pose_after_physics()

    def _reset_world(self, input_mode: int, data=None, pose_input=None) -> bool:
        from mmd_tools.core.native.mmd_anim_runtime_types import MMD_RUNTIME_PHYSICS_MODE_LIVE

        if not self._instance.set_physics_mode(MMD_RUNTIME_PHYSICS_MODE_LIVE):
            return False
        if not self._instance.evaluate_rest_pose():
            return False
        if input_mode == INPUT_MODE_MAYA_POSE and self._kinematic_corrections:
            if pose_input is None:
                if not self._inject_kinematic_poses(data):
                    return False
            elif not self._apply_kinematic_pose_inputs(pose_input):
                return False
            if not self._instance.evaluate_current_pose_before_physics():
                return False
        return self._world.reset(self._instance) is not None

    def _update_cached_matrices(self) -> bool:
        raw = self._instance.get_world_matrices()
        if raw is None:
            self._cached_flat = None
            self._last_kinematic_pose_signature = None
            return False

        from mmd_tools.core.coordinate_transform import mmd_matrix_to_maya

        flat = []
        for mat16 in raw:
            flat.extend(mmd_matrix_to_maya(mat16))
        self._cached_flat = flat
        return True

    @staticmethod
    def _validation_error_details(validation_errors) -> list:
        """Return stable, useful fields from descriptor validation errors."""
        details = []
        for error in validation_errors or []:
            fields = {}
            for name in ("index", "kind", "field", "message"):
                try:
                    value = getattr(error, name)
                except Exception:
                    continue
                if value is not None:
                    fields[name] = value if isinstance(value, (bool, int, float, str)) else str(value)
            details.append(fields or str(error))
        return details

    @staticmethod
    def _validation_error_signature(validation_errors) -> tuple:
        """Build a cheap repeat-failure key without serializing full details."""
        fields = ("index", "kind", "field", "message")
        return tuple(
            tuple(str(getattr(error, name, "")) for name in fields)
            for error in validation_errors or []
        )

    def _record_initialization_failure(
        self,
        *,
        model_root,
        descriptor_version,
        stage: str,
        error_type: str,
        reason: str,
        reason_code: str | None = None,
        validation_errors=None,
    ) -> None:
        """Emit one structured diagnostic for each unique initialization failure."""
        if descriptor_version != self._initialization_failure_descriptor_version:
            self._initialization_failure_signatures.clear()
            self._initialization_failure_descriptor_version = descriptor_version
        validation_errors = tuple(validation_errors or ())
        validation_signature = self._validation_error_signature(validation_errors)
        payload = {
            "event": "mmd_physics_solver_initialization_failed",
            "modelRoot": model_root,
            "descriptorVersion": descriptor_version,
            "stage": stage,
            "errorType": error_type,
            "reason": reason,
        }
        if reason_code:
            # Keep ``reason`` backwards compatible for existing diagnostics,
            # while exposing a machine-stable code for new fail-closed paths.
            payload["reasonCode"] = reason_code
        signature = (
            model_root,
            descriptor_version,
            stage,
            error_type,
            reason,
            reason_code,
            validation_signature,
        )
        if signature in self._initialization_failure_signatures:
            return
        self._initialization_failure_signatures.add(signature)
        details = self._validation_error_details(validation_errors)
        if details:
            payload["validationErrors"] = details
        logger.warning(
            "mmdPhysicsSolver initialization failure %s",
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
        )

    def _fail_initialization(
        self,
        *,
        model_root,
        descriptor_version,
        stage: str,
        error_type: str,
        reason: str,
        reason_code: str | None = None,
        validation_errors=None,
    ) -> bool:
        """Record a non-exception failure, release partial handles, and stop."""
        self._record_initialization_failure(
            model_root=model_root,
            descriptor_version=descriptor_version,
            stage=stage,
            error_type=error_type,
            reason=reason,
            reason_code=reason_code,
            validation_errors=validation_errors,
        )
        self._free_handles()
        return False

    def _try_initialize(self, descriptor_version=None) -> bool:
        if descriptor_version is None:
            descriptor_version = self._last_descriptor_version
        model_root = self._get_connected_model_root()
        if not model_root:
            return self._fail_initialization(
                model_root=None,
                descriptor_version=descriptor_version,
                stage="model root connection",
                error_type="MissingModelRoot",
                reason="modelRoot input has no connected node",
            )

        from mmd_tools.core.physics_solver import _collect_bone_joints
        from mmd_tools.core.model_dag_descriptor import build_model_descriptors_from_dag
        from mmd_tools.core.physics_dag_descriptor import build_descriptors_from_dag
        from mmd_tools.core.native.mmd_anim_runtime_handles import (
            MmdRuntimeInstance,
            MmdRuntimeModel,
            MmdRuntimePhysicsWorld,
        )

        stage = "collect bone joints"
        try:
            bone_joints = _collect_bone_joints(model_root)
            self._bone_count = len(bone_joints)
            self._bone_joints = bone_joints

            stage = "build physics descriptors"
            world_descriptors = build_descriptors_from_dag(
                model_root,
                bone_joints=bone_joints,
                bone_count=len(bone_joints),
            )

            stage = "validate physics descriptors"
            if world_descriptors.validation_errors:
                # Descriptor validation is deterministic for a given version.
                # Do not rebuild/emit the same failure on every DG pull; a
                # descriptor edit changes the version and unlocks retry.
                self._latched_validation_failure_descriptor_version = descriptor_version
                return self._fail_initialization(
                    model_root=model_root,
                    descriptor_version=descriptor_version,
                    stage=stage,
                    error_type="ValidationError",
                    reason="physics descriptor validation failed",
                    validation_errors=world_descriptors.validation_errors,
                )

            stage = "build model descriptors"
            model_descriptors = build_model_descriptors_from_dag(model_root)

            stage = "create physics world"
            world = MmdRuntimePhysicsWorld.from_descriptors(
                world_descriptors.rigid_bodies, world_descriptors.joints
            )
            if world is None:
                return self._fail_initialization(
                    model_root=model_root,
                    descriptor_version=descriptor_version,
                    stage=stage,
                    error_type="FactoryReturnedNone",
                    reason="MmdRuntimePhysicsWorld.from_descriptors returned None",
                )
            self._world = world

            stage = "create runtime model"
            model = MmdRuntimeModel.from_descriptors(model_descriptors)
            if model is None:
                return self._fail_initialization(
                    model_root=model_root,
                    descriptor_version=descriptor_version,
                    stage=stage,
                    error_type="FactoryReturnedNone",
                    reason="MmdRuntimeModel.from_descriptors returned None",
                )
            self._model = model

            stage = "create runtime instance"
            instance = MmdRuntimeInstance.for_model(model)
            if instance is None:
                return self._fail_initialization(
                    model_root=model_root,
                    descriptor_version=descriptor_version,
                    stage=stage,
                    error_type="FactoryReturnedNone",
                    reason="MmdRuntimeInstance.for_model returned None",
                )
            self._instance = instance

            stage = "build kinematic pose data"
            self._build_kinematic_pose_data(model_root)

            stage = "build rigid body mapping"
            self._build_rigid_body_shape_mapping(model_root)
        except BindBasisResolutionError as exc:
            self._record_initialization_failure(
                model_root=model_root,
                descriptor_version=descriptor_version,
                stage=stage,
                error_type="BindBasisError",
                reason=str(exc),
                reason_code=exc.reason_code,
            )
            self._free_handles()
            return False
        except Exception as exc:
            self._record_initialization_failure(
                model_root=model_root,
                descriptor_version=descriptor_version,
                stage=stage,
                error_type=type(exc).__name__,
                reason=str(exc) or type(exc).__name__,
            )
            self._free_handles()
            return False

        self._initialized = True
        self._latched_validation_failure_descriptor_version = None
        self._initialization_failure_signatures.clear()
        self._initialization_failure_descriptor_version = descriptor_version
        return True

    def _build_kinematic_pose_data(self, model_root: str) -> None:
        """Identify physics-driven bones and precompute bind corrections.

        Bind correction maps Maya joint world space to the mmd-anim solver's
        internal bone world space.  All bones with rigid body references are
        included (kinematic mode 0 reads joint worldMatrix directly; dynamic
        modes 1/2 read pre-physics inputs connected by VMD recovery).

        correction = mmd_matrix_to_maya(mmd_rest) * maya_bind^(-1)
        At runtime:  mmd_world = maya_matrix_to_mmd(correction * maya_animated)
        """
        from mmd_tools.core.coordinate_transform import mmd_matrix_to_maya

        all_bone_indices, kinematic_bone_indices = self._find_physics_bone_indices(model_root)
        self._kinematic_bone_indices = kinematic_bone_indices
        if not all_bone_indices:
            return

        if not self._instance.evaluate_rest_pose():
            raise BindBasisResolutionError(
                BIND_BASIS_MISSING,
                str(model_root),
                "runtime rest pose is unavailable",
            )
        mmd_rest_matrices = self._instance.get_world_matrices()
        if not mmd_rest_matrices:
            raise BindBasisResolutionError(
                BIND_BASIS_MISSING,
                str(model_root),
                "runtime rest world matrices are unavailable",
            )

        for bone_idx in all_bone_indices:
            if bone_idx >= len(mmd_rest_matrices) or bone_idx >= len(self._bone_joints):
                raise BindBasisResolutionError(
                    BIND_BASIS_MISSING,
                    f"{model_root}[bone:{bone_idx}]",
                    "physics bone has no matching rest/joint entry",
                )
            joint = self._bone_joints[bone_idx]
            if not joint:
                raise BindBasisResolutionError(BIND_BASIS_MISSING, str(joint))
            try:
                mmd_rest_maya = mmd_matrix_to_maya(mmd_rest_matrices[bone_idx])
                mmd_rest_maya_mat = om.MMatrix(mmd_rest_maya)
                # Never read the animated joint world matrix here.  Physics
                # may be enabled after an arbitrary nonzero animation frame;
                # only a validated, saved bind authority is safe at init.
                bind_mat = resolve_imported_bind_world_matrix(joint)
                self._kinematic_corrections[bone_idx] = mmd_rest_maya_mat * bind_mat.inverse()
            except BindBasisResolutionError:
                raise
            except Exception as exc:
                raise BindBasisResolutionError(
                    BIND_BASIS_SINGULAR,
                    str(joint),
                    str(exc) or type(exc).__name__,
                ) from exc

    @staticmethod
    def _find_physics_bone_indices(model_root: str) -> tuple[set, set]:
        """Return (all_physics_bone_indices, kinematic_only_bone_indices).

        All bones with rigid body references get bind corrections.
        The kinematic-only set (physicsMode==0) is used to guard the
        cmds.getAttr fallback — dynamic bones must only be injected
        when they have an explicit DG pre-physics input to avoid cycles.
        """
        from maya import cmds

        all_indices: set = set()
        kinematic_indices: set = set()
        dynamic_indices: set = set()
        try:
            children = cmds.listRelatives(
                model_root, children=True, fullPath=True, type="transform",
            ) or []
            physics_group = None
            for c in children:
                if c.rsplit("|", 1)[-1].rsplit(":", 1)[-1] == "Physics":
                    physics_group = c
                    break
            if not physics_group:
                return all_indices, kinematic_indices

            children = cmds.listRelatives(
                physics_group, children=True, fullPath=True, type="transform",
            ) or []
            rb_group = None
            for c in children:
                if c.rsplit("|", 1)[-1].rsplit(":", 1)[-1] == "RigidBodies":
                    rb_group = c
                    break
            if not rb_group:
                return all_indices, kinematic_indices

            rb_transforms = cmds.listRelatives(
                rb_group, children=True, fullPath=True, type="transform",
            ) or []
            for xform in rb_transforms:
                shapes = cmds.listRelatives(
                    xform, shapes=True, fullPath=True, type="mmdRigidBodyShape",
                ) or []
                for shape in shapes:
                    idx = cmds.getAttr(f"{shape}.relatedBoneIndex")
                    if idx >= 0:
                        all_indices.add(idx)
                        mode = cmds.getAttr(f"{shape}.physicsMode")
                        if mode == 0:
                            kinematic_indices.add(idx)
                        else:
                            dynamic_indices.add(idx)
        except Exception:
            pass
        return all_indices, kinematic_indices - dynamic_indices

    def _read_kinematic_pose_inputs(self, data):
        """Read effective kinematic matrices and return an exact signature.

        The connected matrix array is authoritative when an element exists.
        The joint world-matrix fallback is restricted to physics-kinematic
        bones; dynamic bones with no pre-physics input are intentionally not
        read so this node cannot form a DG feedback cycle.
        """
        from mmd_tools.core.coordinate_transform import maya_matrix_to_mmd

        bone_count = self._bone_count
        if bone_count <= 0:
            return None

        flat = [0.0] * (bone_count * 16)
        mask = [0] * bone_count
        signature = []

        try:
            array_handle = data.inputArrayValue(self.aInKinematicWorldMatrix)
        except AttributeError:
            array_handle = None
        except Exception:
            return None

        for bone_idx in sorted(self._kinematic_corrections):
            correction_inv = self._kinematic_corrections[bone_idx]
            try:
                maya_mat = None
                source = "none"
                if array_handle is not None:
                    try:
                        array_handle.jumpToElement(bone_idx)
                    except Exception:
                        pass
                    else:
                        source = "matrix"
                        try:
                            maya_mat = array_handle.inputValue().asMatrix()
                        except Exception:
                            return None
                        if maya_mat is None:
                            return None
                if maya_mat is None:
                    if bone_idx not in self._kinematic_bone_indices:
                        signature.append((bone_idx, source))
                        continue
                    from maya import cmds
                    joint = self._bone_joints[bone_idx] if bone_idx < len(self._bone_joints) else None
                    if not joint:
                        return None
                    try:
                        maya_world = [float(v) for v in cmds.getAttr(f"{joint}.worldMatrix[0]")]
                        maya_mat = om.MMatrix(maya_world)
                    except Exception:
                        return None
                    source = "joint"

                corrected = correction_inv * maya_mat
                corrected_flat = [
                    corrected.getElement(r, c) for r in range(4) for c in range(4)
                ]
                corrected_mmd = tuple(float(v) for v in maya_matrix_to_mmd(corrected_flat))
                offset = bone_idx * 16
                flat[offset : offset + 16] = corrected_mmd
                mask[bone_idx] = 1
                signature.append((bone_idx, source, corrected_mmd))
            except Exception:
                return None

        return flat, mask, tuple(signature)

    def _apply_kinematic_pose_inputs(self, pose_input) -> bool:
        """Apply a previously read pose snapshot and retain its signature."""
        flat, mask, signature = pose_input

        if any(mask):
            result = self._instance.apply_physics_world_matrices(flat, mask)
            if result is None:
                self._last_kinematic_pose_signature = None
                return False
        self._last_kinematic_pose_signature = signature
        return True

    def _inject_kinematic_poses(self, data) -> bool:
        """Read and inject kinematic matrices from the Maya pose sources.

        ``_read_kinematic_pose_inputs`` performs the ``cmds.getAttr`` fallback
        for ``worldMatrix[0]`` and the ``maya_matrix_to_mmd`` conversion; this
        wrapper applies the result through ``apply_physics_world_matrices``.
        """
        pose_input = self._read_kinematic_pose_inputs(data)
        if pose_input is None:
            self._last_kinematic_pose_signature = None
            return False
        return self._apply_kinematic_pose_inputs(pose_input)

    def _build_rigid_body_shape_mapping(self, model_root: str) -> None:
        """Build dense native state index → shape DAG path mapping."""
        from maya import cmds

        try:
            children = cmds.listRelatives(
                model_root, children=True, fullPath=True, type="transform",
            ) or []
            physics_group = None
            for c in children:
                if c.rsplit("|", 1)[-1].rsplit(":", 1)[-1] == "Physics":
                    physics_group = c
                    break
            if not physics_group:
                return

            children = cmds.listRelatives(
                physics_group, children=True, fullPath=True, type="transform",
            ) or []
            rb_group = None
            for c in children:
                if c.rsplit("|", 1)[-1].rsplit(":", 1)[-1] == "RigidBodies":
                    rb_group = c
                    break
            if not rb_group:
                return

            rb_transforms = cmds.listRelatives(
                rb_group, children=True, fullPath=True, type="transform",
            ) or []
            indexed_shapes = []
            for xform in rb_transforms:
                shapes = cmds.listRelatives(
                    xform, shapes=True, fullPath=True, type="mmdRigidBodyShape",
                ) or []
                for shape in shapes:
                    source_index = cmds.getAttr(f"{shape}.pmxIndex")
                    if source_index >= 0:
                        indexed_shapes.append((source_index, shape))
            indexed_shapes.sort(key=lambda item: item[0])
            for dense_index, (_source_index, shape) in enumerate(indexed_shapes):
                self._rb_shape_paths[dense_index] = shape
                try:
                    sel = om.MSelectionList()
                    sel.add(shape)
                    self._rb_shape_mobjects[dense_index] = sel.getDependNode(0)
                except Exception:
                    pass
        except Exception:
            pass

    def _update_rigid_body_visual_cache(self) -> None:
        """Populate the module-level cache with simulated rigid body world matrices."""
        if not self._rb_shape_paths or self._world is None:
            return
        states = self._world.copy_rigidbody_states()
        if states is None:
            return
        from mmd_tools.core.coordinate_transform import mmd_point_to_maya
        import maya.api.OpenMayaRender as omr

        for dense_index, shape_path in self._rb_shape_paths.items():
            if dense_index >= len(states):
                continue
            pos_mmd, quat_xyzw_mmd = states[dense_index]
            pos_maya = mmd_point_to_maya(pos_mmd)
            qx, qy, qz, qw = quat_xyzw_mmd
            tmat = om.MTransformationMatrix()
            tmat.setTranslation(om.MVector(*pos_maya), om.MSpace.kWorld)
            tmat.setRotation(om.MQuaternion(-qx, -qy, qz, qw))
            _SIMULATED_RB_CACHE[shape_path] = tmat.asMatrix()
            mob = self._rb_shape_mobjects.get(dense_index)
            if mob is not None and not mob.isNull():
                try:
                    omr.MRenderer.setGeometryDrawDirty(mob)
                except Exception:
                    pass

    def _read_world_settings(self):
        """Read enable and resetGeneration from connected world node."""
        try:
            fn = om.MFnDependencyNode(self.thisMObject())
            plug = fn.findPlug("inWorldSettings", False)
            connections = plug.connectedTo(True, False)
            if not connections:
                return False, self._last_reset_generation
            world_fn = om.MFnDependencyNode(connections[0].node())
            enable = world_fn.findPlug("enable", False).asBool()
            reset_gen = world_fn.findPlug("resetGeneration", False).asInt()
            return enable, reset_gen
        except Exception:
            return False, self._last_reset_generation

    def _get_connected_model_root(self):
        try:
            fn = om.MFnDependencyNode(self.thisMObject())
            plug = fn.findPlug("modelRoot", False)
            connections = plug.connectedTo(True, False)
            if connections:
                return om.MFnDependencyNode(connections[0].node()).name()
        except Exception:
            pass
        return None

    def _write_outputs(self, data, solved: bool, status: str) -> None:
        data.outputValue(self.aOutSolved).setBool(solved)
        data.outputValue(self.aOutStatus).setString(status)
        data.outputValue(self.aOutBoneCount).setInt(self._bone_count)

        mat_handle = data.outputValue(self.aOutBoneMatrices)
        if self._cached_flat:
            fn = om.MFnDoubleArrayData()
            arr = om.MDoubleArray(self._cached_flat)
            mat_handle.setMObject(fn.create(arr))
        else:
            fn = om.MFnDoubleArrayData()
            mat_handle.setMObject(fn.create(om.MDoubleArray()))

        data.setClean(self.aOutBoneMatrices)
        data.setClean(self.aOutBoneCount)
        data.setClean(self.aOutStatus)
        data.setClean(self.aOutSolved)

    def _write_failure(self, data, status: str = "physics evaluation failed") -> None:
        """Publish a failed evaluation without exposing a prior successful pose."""
        self._last_time = None
        self._cached_flat = None
        self._last_kinematic_pose_signature = None
        self._write_outputs(data, solved=False, status=status)

    def _free_handles(self) -> None:
        if self._world is not None:
            self._world.free()
            self._world = None
        if self._instance is not None:
            self._instance.free()
            self._instance = None
        if self._model is not None:
            self._model.free()
            self._model = None
        self._initialized = False
        self._bone_joints = []
        self._kinematic_corrections = {}
        self._kinematic_bone_indices = set()
        for path in self._rb_shape_paths.values():
            _SIMULATED_RB_CACHE.pop(path, None)
        self._rb_shape_paths = {}
        self._rb_shape_mobjects = {}
        self._last_time = None
        self._cached_flat = None
        self._last_kinematic_pose_signature = None

    def __del__(self):
        try:
            self._free_handles()
        except Exception:
            pass


def creator():
    return MmdPhysicsSolverNode()


def initialize():
    tAttr = om.MFnTypedAttribute()
    nAttr = om.MFnNumericAttribute()
    uAttr = om.MFnUnitAttribute()
    msgAttr = om.MFnMessageAttribute()

    MmdPhysicsSolverNode.aEnable = nAttr.create(
        "enable", "en", om.MFnNumericData.kBoolean, True
    )
    nAttr.storable = True
    nAttr.keyable = True
    MmdPhysicsSolverNode.addAttribute(MmdPhysicsSolverNode.aEnable)

    eAttr = om.MFnEnumAttribute()
    MmdPhysicsSolverNode.aInputMode = eAttr.create("inputMode", "im", INPUT_MODE_MAYA_POSE)
    eAttr.addField("rest-only", INPUT_MODE_REST)
    eAttr.addField("maya-pose", INPUT_MODE_MAYA_POSE)
    eAttr.storable = True
    eAttr.keyable = False
    MmdPhysicsSolverNode.addAttribute(MmdPhysicsSolverNode.aInputMode)

    MmdPhysicsSolverNode.aInTime = uAttr.create("inTime", "it", om.MFnUnitAttribute.kTime, 0.0)
    uAttr.storable = False
    MmdPhysicsSolverNode.addAttribute(MmdPhysicsSolverNode.aInTime)

    MmdPhysicsSolverNode.aModelRoot = msgAttr.create("modelRoot", "mr")
    MmdPhysicsSolverNode.addAttribute(MmdPhysicsSolverNode.aModelRoot)

    MmdPhysicsSolverNode.aInWorldSettings = msgAttr.create("inWorldSettings", "iws")
    MmdPhysicsSolverNode.addAttribute(MmdPhysicsSolverNode.aInWorldSettings)

    MmdPhysicsSolverNode.aInWorldSettingsVersion = nAttr.create(
        "inWorldSettingsVersion", "iwsv", om.MFnNumericData.kLong, 0
    )
    nAttr.storable = False
    nAttr.keyable = False
    MmdPhysicsSolverNode.addAttribute(MmdPhysicsSolverNode.aInWorldSettingsVersion)

    MmdPhysicsSolverNode.aInDescriptorVersion = nAttr.create(
        "inDescriptorVersion", "idv", om.MFnNumericData.kLong, 0
    )
    nAttr.storable = True
    nAttr.keyable = False
    MmdPhysicsSolverNode.addAttribute(MmdPhysicsSolverNode.aInDescriptorVersion)

    mAttr = om.MFnMatrixAttribute()
    MmdPhysicsSolverNode.aInKinematicWorldMatrix = mAttr.create(
        "inKinematicWorldMatrix", "ikwm", om.MFnMatrixAttribute.kDouble,
    )
    mAttr.storable = False
    mAttr.array = True
    mAttr.usesArrayDataBuilder = True
    mAttr.disconnectBehavior = om.MFnAttribute.kDelete
    MmdPhysicsSolverNode.addAttribute(MmdPhysicsSolverNode.aInKinematicWorldMatrix)

    MmdPhysicsSolverNode.aOutBoneMatrices = tAttr.create(
        "outBoneMatrices", "obm", om.MFnData.kDoubleArray
    )
    tAttr.writable = False
    tAttr.storable = False
    MmdPhysicsSolverNode.addAttribute(MmdPhysicsSolverNode.aOutBoneMatrices)

    MmdPhysicsSolverNode.aOutBoneCount = nAttr.create(
        "outBoneCount", "obc", om.MFnNumericData.kInt, 0
    )
    nAttr.writable = False
    nAttr.storable = False
    MmdPhysicsSolverNode.addAttribute(MmdPhysicsSolverNode.aOutBoneCount)

    MmdPhysicsSolverNode.aOutStatus = tAttr.create(
        "outStatus", "ost", om.MFnData.kString
    )
    tAttr.writable = False
    tAttr.storable = False
    MmdPhysicsSolverNode.addAttribute(MmdPhysicsSolverNode.aOutStatus)

    MmdPhysicsSolverNode.aOutSolved = nAttr.create(
        "outSolved", "osv", om.MFnNumericData.kBoolean, False
    )
    nAttr.writable = False
    nAttr.storable = False
    MmdPhysicsSolverNode.addAttribute(MmdPhysicsSolverNode.aOutSolved)

    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aEnable, MmdPhysicsSolverNode.aOutBoneMatrices
    )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aEnable, MmdPhysicsSolverNode.aOutBoneCount
    )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aEnable, MmdPhysicsSolverNode.aOutStatus
    )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aEnable, MmdPhysicsSolverNode.aOutSolved
    )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aInTime, MmdPhysicsSolverNode.aOutBoneMatrices
    )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aInTime, MmdPhysicsSolverNode.aOutBoneCount
    )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aInTime, MmdPhysicsSolverNode.aOutStatus
    )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aInTime, MmdPhysicsSolverNode.aOutSolved
    )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aInputMode, MmdPhysicsSolverNode.aOutBoneMatrices
    )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aInputMode, MmdPhysicsSolverNode.aOutBoneCount
    )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aInputMode, MmdPhysicsSolverNode.aOutStatus
    )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aInputMode, MmdPhysicsSolverNode.aOutSolved
    )
    for output in (
        MmdPhysicsSolverNode.aOutBoneMatrices,
        MmdPhysicsSolverNode.aOutBoneCount,
        MmdPhysicsSolverNode.aOutStatus,
        MmdPhysicsSolverNode.aOutSolved,
    ):
        MmdPhysicsSolverNode.attributeAffects(
            MmdPhysicsSolverNode.aInWorldSettingsVersion, output
        )
        MmdPhysicsSolverNode.attributeAffects(
            MmdPhysicsSolverNode.aInDescriptorVersion, output
        )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aInKinematicWorldMatrix, MmdPhysicsSolverNode.aOutBoneMatrices
    )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aInKinematicWorldMatrix, MmdPhysicsSolverNode.aOutBoneCount
    )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aInKinematicWorldMatrix, MmdPhysicsSolverNode.aOutStatus
    )
    MmdPhysicsSolverNode.attributeAffects(
        MmdPhysicsSolverNode.aInKinematicWorldMatrix, MmdPhysicsSolverNode.aOutSolved
    )


def register(plugin_fn):
    if not is_native_physics_available():
        om.MGlobal.displayWarning(
            "mmd-anim physics not available — mmdPhysicsSolver not registered"
        )
        return
    plugin_fn.registerNode(
        MmdPhysicsSolverNode.kTypeName,
        MmdPhysicsSolverNode.kTypeId,
        creator,
        initialize,
        om.MPxNode.kDependNode,
        MmdPhysicsSolverNode.kClassify,
    )


def deregister(plugin_fn):
    try:
        plugin_fn.deregisterNode(MmdPhysicsSolverNode.kTypeId)
    except Exception:
        pass
