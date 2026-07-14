"""Build the Physics DAG hierarchy (rigid bodies + joints) from parsed PMX data."""

from __future__ import annotations

import math
import re
from typing import Optional

from maya import cmds

from mmd_tools.core.constants import (
    CONSTRAINTS_GROUP,
    PHYSICS_GROUP,
    PHYSICS_WORLD_NODE,
    RIGID_BODIES_GROUP,
)
from mmd_tools.core.logger import get_logger

_logger = get_logger(__name__)

_INVALID_NAME_CHARS_RE = re.compile(r"[^0-9A-Za-z_]+")


def _sanitize_node_name(name: str) -> str:
    """Turn an arbitrary PMX name into a Maya-safe node name fragment."""
    sanitized = _INVALID_NAME_CHARS_RE.sub("_", name or "").strip("_")
    if not sanitized:
        return "unnamed"
    if sanitized[0].isdigit():
        sanitized = f"_{sanitized}"
    return sanitized


def _display_name(name_english: str, name_japanese: str) -> str:
    return name_english or name_japanese or "unnamed"


def _set_vector_attr(shape: str, attr: str, values) -> None:
    x, y, z = values
    cmds.setAttr(f"{shape}.{attr}X", x)
    cmds.setAttr(f"{shape}.{attr}Y", y)
    cmds.setAttr(f"{shape}.{attr}Z", z)


def _set_angle_vector_attr(shape: str, attr: str, values) -> None:
    # rotationX/Y/Z-style attributes are MFnUnitAttribute(kAngle); cmds.setAttr
    # expects degrees while PMX stores Euler angles in radians.
    x, y, z = values
    cmds.setAttr(f"{shape}.{attr}X", math.degrees(x))
    cmds.setAttr(f"{shape}.{attr}Y", math.degrees(y))
    cmds.setAttr(f"{shape}.{attr}Z", math.degrees(z))


def _set_position_attr(node: str, attr_prefix: str, position) -> None:
    x, y, z = position
    cmds.setAttr(f"{node}.{attr_prefix}X", x)
    cmds.setAttr(f"{node}.{attr_prefix}Y", y)
    cmds.setAttr(f"{node}.{attr_prefix}Z", z)


def _resolve_rigid_body_transform(rigid_body_transforms: list, index: int) -> Optional[str]:
    if index is None or index < 0 or index >= len(rigid_body_transforms):
        return None
    transform = rigid_body_transforms[index]
    return transform if transform and cmds.objExists(transform) else None


def _build_rigid_body(index: int, rb, maya_joints: list, parent_group: str, logger) -> Optional[str]:
    base_name = _display_name(rb.name_english, rb.name)
    node_name = f"rb_{index}_{_sanitize_node_name(base_name)}"
    transform = None
    try:
        transform = cmds.createNode("transform", name=node_name, parent=parent_group)
        shape = cmds.createNode("mmdRigidBodyShape", name=f"{node_name}Shape", parent=transform)

        cmds.setAttr(f"{shape}.pmxIndex", index)
        cmds.setAttr(f"{shape}.nameJp", rb.name or "", type="string")
        cmds.setAttr(f"{shape}.nameEn", rb.name_english or "", type="string")
        cmds.setAttr(f"{shape}.enable", True)
        cmds.setAttr(f"{shape}.shapeType", rb.shape_type)

        _set_vector_attr(shape, "shapeSize", rb.size)
        _set_vector_attr(shape, "position", rb.position)
        _set_angle_vector_attr(shape, "rotation", rb.rotation)

        cmds.setAttr(f"{shape}.physicsMode", rb.physics_mode)
        cmds.setAttr(f"{shape}.mass", rb.mass)
        cmds.setAttr(f"{shape}.linearDamping", rb.velocity_attenuation)
        cmds.setAttr(f"{shape}.angularDamping", rb.rotation_attenuation)
        cmds.setAttr(f"{shape}.friction", rb.friction)
        cmds.setAttr(f"{shape}.restitution", rb.elasticity)
        cmds.setAttr(f"{shape}.collisionGroup", min(rb.group, 15))
        cmds.setAttr(f"{shape}.collisionMask", rb.collision_mask)
        cmds.setAttr(f"{shape}.relatedBoneIndex", rb.related_bone_index)

        if 0 <= rb.related_bone_index < len(maya_joints):
            maya_joint = maya_joints[rb.related_bone_index]
            if maya_joint and cmds.objExists(maya_joint):
                cmds.connectAttr(f"{maya_joint}.message", f"{shape}.relatedBone")

        _set_position_attr(transform, "translate", rb.position)

        return transform
    except Exception as exc:
        logger.warning(f"event=rigid_body_build_failed index={index} name={base_name!r} error={exc}")
        if transform and cmds.objExists(transform):
            cmds.delete(transform)
        return None


def _build_joint(index: int, jt, rigid_body_transforms: list, parent_group: str, logger) -> Optional[str]:
    base_name = _display_name(jt.name_english, jt.name)
    node_name = f"jt_{index}_{_sanitize_node_name(base_name)}"
    transform = None
    try:
        transform = cmds.createNode("transform", name=node_name, parent=parent_group)
        shape = cmds.createNode("mmdPhysicsJointShape", name=f"{node_name}Shape", parent=transform)

        cmds.setAttr(f"{shape}.pmxIndex", index)
        cmds.setAttr(f"{shape}.nameJp", jt.name or "", type="string")
        cmds.setAttr(f"{shape}.nameEn", jt.name_english or "", type="string")
        cmds.setAttr(f"{shape}.enable", True)
        cmds.setAttr(f"{shape}.jointType", jt.joint_type)

        _set_vector_attr(shape, "position", jt.position)
        _set_angle_vector_attr(shape, "rotation", jt.rotation)
        _set_vector_attr(shape, "translationLimitMin", jt.translation_limit_min)
        _set_vector_attr(shape, "translationLimitMax", jt.translation_limit_max)
        _set_angle_vector_attr(shape, "rotationLimitMin", jt.rotation_limit_min)
        _set_angle_vector_attr(shape, "rotationLimitMax", jt.rotation_limit_max)
        _set_vector_attr(shape, "springTranslation", jt.spring_translation)
        _set_vector_attr(shape, "springRotation", jt.spring_rotation)

        cmds.setAttr(f"{shape}.rigidBodyAIndex", jt.rigid_body_a_index)
        cmds.setAttr(f"{shape}.rigidBodyBIndex", jt.rigid_body_b_index)

        rb_a = _resolve_rigid_body_transform(rigid_body_transforms, jt.rigid_body_a_index)
        if rb_a:
            cmds.connectAttr(f"{rb_a}.message", f"{shape}.rigidBodyA")
        rb_b = _resolve_rigid_body_transform(rigid_body_transforms, jt.rigid_body_b_index)
        if rb_b:
            cmds.connectAttr(f"{rb_b}.message", f"{shape}.rigidBodyB")

        _set_position_attr(transform, "translate", jt.position)

        return transform
    except Exception as exc:
        logger.warning(f"event=joint_build_failed index={index} name={base_name!r} error={exc}")
        if transform and cmds.objExists(transform):
            cmds.delete(transform)
        return None


def build_physics_scene(
    *,
    rigid_bodies,
    joints,
    bones,
    maya_joints,
    root_group: str,
    logger=None,
) -> tuple[list[str], list[str]]:
    """Build physics DAG nodes from PMX data.

    Creates ``root_group/Physics/RigidBodies`` and ``root_group/Physics/Constraints``
    groups, then one transform + ``mmdRigidBodyShape``/``mmdPhysicsJointShape`` pair
    per PMX rigid body / joint, with all PMX fields copied onto the shape attributes.

    Returns (rigid_body_transforms, joint_transforms).
    """
    log = logger or _logger

    physics_group = cmds.group(empty=True, name=PHYSICS_GROUP, parent=root_group)
    rigid_bodies_group = cmds.group(empty=True, name=RIGID_BODIES_GROUP, parent=physics_group)
    constraints_group = cmds.group(empty=True, name=CONSTRAINTS_GROUP, parent=physics_group)

    # Kept positional (index-aligned with the PMX lists, holes as None on
    # failure) because joints resolve rigid_body_a/b_index by list position.
    rigid_body_transforms = [
        _build_rigid_body(index, rb, maya_joints, rigid_bodies_group, log) for index, rb in enumerate(rigid_bodies)
    ]
    joint_transforms = [
        _build_joint(index, jt, rigid_body_transforms, constraints_group, log) for index, jt in enumerate(joints)
    ]

    world_node = _find_or_create_world_node()
    _build_solver(root_group, world_node, maya_joints, log)

    return rigid_body_transforms, joint_transforms


def _find_or_create_world_node() -> str:
    """Return the existing scene world node, or create one at the scene root."""
    existing = cmds.ls(type="mmdPhysicsWorldShape")
    if existing:
        parents = cmds.listRelatives(existing[0], parent=True, fullPath=True) or []
        return parents[0] if parents else existing[0]
    transform = cmds.createNode("transform", name=PHYSICS_WORLD_NODE)
    cmds.createNode("mmdPhysicsWorldShape", name=f"{PHYSICS_WORLD_NODE}Shape", parent=transform)
    return transform


def _build_solver(root_group: str, world_node: str, maya_joints: list, logger) -> Optional[str]:
    """Create one mmdPhysicsSolver per model and connect to world + model root."""
    try:
        solver = cmds.createNode("mmdPhysicsSolver", name="mmdPhysicsSolver")
        cmds.setAttr(f"{solver}.inputMode", 1)
        cmds.connectAttr("time1.outTime", f"{solver}.inTime")
        cmds.connectAttr(f"{root_group}.message", f"{solver}.modelRoot")
        world_shapes = cmds.listRelatives(world_node, shapes=True, type="mmdPhysicsWorldShape") or []
        if world_shapes:
            cmds.connectAttr(f"{world_shapes[0]}.message", f"{solver}.inWorldSettings")
        _build_bone_drivers(solver, maya_joints, logger)
        return solver
    except Exception as exc:
        logger.warning(f"event=solver_build_failed error={exc}")
        return None


def _build_bone_drivers(solver: str, maya_joints: list, logger) -> None:
    """Create one mmdPhysicsBoneDriver per bone and connect to solver outputs."""
    for i, joint in enumerate(maya_joints):
        if not joint or not cmds.objExists(joint):
            continue
        try:
            driver = cmds.createNode("mmdPhysicsBoneDriver", name=f"physDriver_{i}")
            cmds.connectAttr(f"{solver}.outBoneMatrices", f"{driver}.inSolverBoneMatrices")
            cmds.connectAttr(f"{solver}.outBoneCount", f"{driver}.inSolverBoneCount")
            cmds.connectAttr(f"{solver}.outSolved", f"{driver}.inSolved")
            cmds.setAttr(f"{driver}.inBoneIndex", i)

            parent_joints = cmds.listRelatives(joint, parent=True, type="joint") or []
            if parent_joints:
                parent_idx = maya_joints.index(parent_joints[0]) if parent_joints[0] in maya_joints else -1
                cmds.setAttr(f"{driver}.inParentBoneIndex", parent_idx)
                cmds.connectAttr(f"{parent_joints[0]}.worldInverseMatrix[0]", f"{driver}.inParentInverseMatrix")

            jo = cmds.getAttr(f"{joint}.jointOrient")[0]
            cmds.setAttr(f"{driver}.inJointOrientX", jo[0])
            cmds.setAttr(f"{driver}.inJointOrientY", jo[1])
            cmds.setAttr(f"{driver}.inJointOrientZ", jo[2])

            ra = cmds.getAttr(f"{joint}.rotateAxis")[0]
            cmds.setAttr(f"{driver}.inRotateAxisX", ra[0])
            cmds.setAttr(f"{driver}.inRotateAxisY", ra[1])
            cmds.setAttr(f"{driver}.inRotateAxisZ", ra[2])

            ro = cmds.getAttr(f"{joint}.rotateOrder")
            cmds.setAttr(f"{driver}.inRotateOrder", ro)
        except Exception as exc:
            logger.debug(f"event=bone_driver_skip bone={i} error={exc}")


def _node_leaf_name(node: str) -> str:
    """Return the leaf name from a Maya DAG path or plain node name."""
    return str(node or "").rsplit("|", 1)[-1]


def _set_driver_angle(driver: str, attr: str, values) -> None:
    """Copy a Maya angle compound into the driver's angle children."""
    for axis, value in zip(("X", "Y", "Z"), values or (0.0, 0.0, 0.0)):
        cmds.setAttr(f"{driver}.{attr}{axis}", float(value))


def _no_orient_matrix(bind_world) -> list[float]:
    """Return a translation-only matrix matching the existing IK correction."""
    matrix = [0.0] * 16
    matrix[0] = matrix[5] = matrix[10] = matrix[15] = 1.0
    if bind_world and len(bind_world) >= 15:
        matrix[12:15] = [float(bind_world[12]), float(bind_world[13]), float(bind_world[14])]
    return matrix


def _connect_physics_output(source: str, destination: str) -> None:
    """Replace the existing channel input with the live physics output."""
    cmds.connectAttr(source, destination, force=True)


def _capture_input_connections(node: str, attr: str) -> list[tuple[str, str]]:
    """Capture compound and child input connections before replacing them."""
    destinations = [f"{node}.{attr}"] + [f"{node}.{attr}{axis}" for axis in "XYZ"]
    captured: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for destination in destinations:
        for source in cmds.listConnections(
            destination, source=True, destination=False, plugs=True
        ) or []:
            connection = (source, destination)
            if connection not in seen:
                captured.append(connection)
                seen.add(connection)
    return captured


def _restore_input_connections(connections: list[tuple[str, str]], logger) -> None:
    """Restore inputs removed by a failed live-driver connection attempt."""
    for source, destination in connections:
        try:
            cmds.connectAttr(source, destination, force=True)
        except Exception as exc:
            logger.warning(
                "Unable to restore physics target input %s from %s: %s",
                destination,
                source,
                exc,
            )


def _connect_kinematic_joint_inputs(
    *,
    solver: str,
    rigid_bodies,
    maya_joints,
    root_group: str,
    logger=None,
) -> None:
    """Connect kinematic (physicsMode=0) joint worldMatrices to the solver.

    This creates proper DG dependencies so that moving a controller at the
    same frame dirties the solver's outputs and triggers re-evaluation.
    """
    log = logger or _logger
    connected = set()
    for rb in rigid_bodies or []:
        try:
            physics_mode = int(getattr(rb, "physics_mode", 0))
            bone_index = int(getattr(rb, "related_bone_index", -1))
        except (TypeError, ValueError):
            continue
        if physics_mode != 0 or bone_index < 0:
            continue
        if bone_index in connected:
            continue
        if bone_index >= len(maya_joints or []):
            continue
        joint = maya_joints[bone_index]
        if not joint or not cmds.objExists(joint):
            continue
        try:
            cmds.connectAttr(
                f"{joint}.worldMatrix[0]",
                f"{solver}.inKinematicWorldMatrix[{bone_index}]",
                force=True,
            )
            connected.add(bone_index)
        except Exception as exc:
            log.debug(
                "event=kinematic_input_connect_failed bone=%d joint=%s error=%s",
                bone_index, joint, exc,
            )


def build_physics_live_graph(
    *,
    rigid_bodies,
    bones,
    maya_joints,
    root_group: str,
    logger=None,
) -> dict:
    """Create the Maya DG graph that drives dynamic PMX bones from physics.

    This is intentionally a bounded live-preview path.  The solver currently
    evaluates the PMX/rest pose directly; it does not yet consume a separate
    pre-physics controller/IK pose.  Dynamic rigid bodies (physics mode 1/2)
    are therefore wired to their related Maya joints and replace existing
    ``translate``/``rotate`` inputs so that timeline playback is immediately
    visible.

    Returns a summary with ``solver`` and ``drivers``.  Node creation failures
    are reported and returned as an empty graph so importing a model without
    the native physics node remains non-fatal.
    """
    log = logger or _logger
    solver = None
    drivers: list[str] = []

    try:
        root_token = _sanitize_node_name(_node_leaf_name(root_group))
        solver = cmds.createNode("mmdPhysicsSolver", name=f"{root_token}_mmdPhysicsSolver")
        cmds.setAttr(f"{solver}.enable", True)
        cmds.connectAttr(f"{root_group}.message", f"{solver}.modelRoot", force=True)

        time_nodes = cmds.ls(type="time") or []
        if time_nodes:
            cmds.connectAttr(f"{time_nodes[0]}.outTime", f"{solver}.inTime", force=True)
        else:
            log.warning("Physics solver created without a Maya time node: %s", solver)
    except Exception as exc:
        log.warning("event=physics_solver_graph_failed root=%s error=%s", root_group, exc)
        if solver and cmds.objExists(solver):
            cmds.delete(solver)
        return {"solver": None, "drivers": [], "reason": "solver_node_unavailable"}

    _connect_kinematic_joint_inputs(
        solver=solver,
        rigid_bodies=rigid_bodies,
        maya_joints=maya_joints,
        root_group=root_group,
        logger=log,
    )

    driven_bones: set[int] = set()
    for rb_index, rb in enumerate(rigid_bodies or []):
        try:
            physics_mode = int(getattr(rb, "physics_mode", 0))
            bone_index = int(getattr(rb, "related_bone_index", -1))
        except (TypeError, ValueError):
            continue

        if physics_mode not in (1, 2) or bone_index in driven_bones:
            continue
        if bone_index < 0 or bone_index >= len(maya_joints or []):
            log.warning(
                "Skipping physics driver for rigid body %d: invalid related bone index %d",
                rb_index,
                bone_index,
            )
            continue

        joint = maya_joints[bone_index]
        if not joint or not cmds.objExists(joint):
            log.warning(
                "Skipping physics driver for rigid body %d: related Maya joint is missing",
                rb_index,
            )
            continue

        parent_bone_index = -1
        if bone_index < len(bones or []):
            try:
                parent_bone_index = int(getattr(bones[bone_index], "parent_bone_index", -1))
            except (TypeError, ValueError):
                parent_bone_index = -1

        driver = None
        previous_translate_inputs = _capture_input_connections(joint, "translate")
        previous_rotate_inputs = _capture_input_connections(joint, "rotate")
        try:
            driver_name = f"{_sanitize_node_name(_node_leaf_name(joint))}_mmdPhysicsBoneDriver"
            driver = cmds.createNode("mmdPhysicsBoneDriver", name=driver_name)
            cmds.setAttr(f"{driver}.enable", True)
            cmds.setAttr(f"{driver}.inBoneIndex", bone_index)
            cmds.setAttr(f"{driver}.inParentBoneIndex", parent_bone_index)
            cmds.setAttr(f"{driver}.inSolved", False)

            joint_orient = cmds.getAttr(f"{joint}.jointOrient")[0]
            rotate_axis = cmds.getAttr(f"{joint}.rotateAxis")[0]
            _set_driver_angle(driver, "inJointOrient", joint_orient)
            _set_driver_angle(driver, "inRotateAxis", rotate_axis)
            cmds.setAttr(f"{driver}.inRotateOrder", int(cmds.getAttr(f"{joint}.rotateOrder")))

            bind_world = [
                float(value) for value in cmds.getAttr(f"{joint}.worldMatrix[0]")
            ]
            cmds.setAttr(f"{driver}.inBindWorldMatrix", bind_world, type="matrix")
            cmds.setAttr(
                f"{driver}.inNoOrientBindWorldMatrix",
                _no_orient_matrix(bind_world),
                type="matrix",
            )

            parent_bind_world = [1.0, 0.0, 0.0, 0.0,
                                 0.0, 1.0, 0.0, 0.0,
                                 0.0, 0.0, 1.0, 0.0,
                                 0.0, 0.0, 0.0, 1.0]
            if 0 <= parent_bone_index < len(maya_joints or []):
                parent_joint = maya_joints[parent_bone_index]
                if parent_joint and cmds.objExists(parent_joint):
                    parent_bind_world = [
                        float(value) for value in cmds.getAttr(
                            f"{parent_joint}.worldMatrix[0]"
                        )
                    ]
            cmds.setAttr(f"{driver}.inParentBindWorldMatrix", parent_bind_world, type="matrix")
            cmds.setAttr(
                f"{driver}.inParentNoOrientBindWorldMatrix",
                _no_orient_matrix(parent_bind_world),
                type="matrix",
            )

            cmds.connectAttr(
                f"{solver}.outBoneMatrices",
                f"{driver}.inSolverBoneMatrices",
                force=True,
            )
            cmds.connectAttr(f"{solver}.outBoneCount", f"{driver}.inSolverBoneCount", force=True)
            cmds.connectAttr(f"{solver}.outSolved", f"{driver}.inSolved", force=True)

            # Root PMX bones use solver world space directly.  Child bones use
            # the PMX parent index above and do not need this fallback input.
            if not (0 <= parent_bone_index < len(bones or [])):
                parents = cmds.listRelatives(joint, parent=True, fullPath=True) or []
                if parents and cmds.nodeType(parents[0]) == "joint":
                    cmds.connectAttr(
                        f"{parents[0]}.worldInverseMatrix[0]",
                        f"{driver}.inParentInverseMatrix",
                        force=True,
                    )

            if not cmds.attributeQuery("mmd_model_root", node=driver, exists=True):
                cmds.addAttr(driver, longName="mmd_model_root", attributeType="message")
            cmds.connectAttr(f"{root_group}.message", f"{driver}.mmd_model_root", force=True)

            if not cmds.attributeQuery("mmd_target_joint", node=driver, exists=True):
                cmds.addAttr(driver, longName="mmd_target_joint", dataType="string")
            cmds.setAttr(f"{driver}.mmd_target_joint", joint, type="string")

            _connect_physics_output(f"{driver}.outTranslate", f"{joint}.translate")
            _connect_physics_output(f"{driver}.outRotate", f"{joint}.rotate")
            drivers.append(driver)
            driven_bones.add(bone_index)
        except Exception as exc:
            log.warning(
                "event=physics_bone_driver_failed rigid_body=%d bone=%d joint=%s error=%s",
                rb_index,
                bone_index,
                joint,
                exc,
            )
            if driver and cmds.objExists(driver):
                cmds.delete(driver)
            _restore_input_connections(previous_translate_inputs, log)
            _restore_input_connections(previous_rotate_inputs, log)

    return {
        "solver": solver,
        "drivers": drivers,
        "driven_bone_count": len(drivers),
    }


def recover_physics_driver_connections(model_root: str, *, logger=None) -> dict:
    """Re-attach orphaned mmdPhysicsBoneDriver nodes to their target joints.

    After VMD import, the runtime bake path disconnects physics driver outputs
    from joints.  This function finds all drivers that belong to *model_root*,
    checks whether their ``outTranslate``/``outRotate`` outputs are still
    connected to the target joint, and reconnects them if not.

    When animCurves are found driving the joint (from VMD import), they are
    rerouted to the driver's ``inPreTranslate``/``inPreRotate`` inputs and
    the driver's ``outPrePhysicsWorldMatrix`` is connected to the solver's
    ``inKinematicWorldMatrix`` array.  This preserves the VMD animation as
    the pre-physics pose source without creating a DG cycle.

    Returns a summary dict with ``recovered`` and ``skipped`` counts.
    """
    log = logger or _logger
    recovered = 0
    skipped = 0
    pre_physics_routed = 0

    try:
        available_types = set(cmds.allNodeTypes() or [])
    except Exception:
        available_types = set()
    if "mmdPhysicsBoneDriver" not in available_types:
        log.debug("mmdPhysicsBoneDriver not registered, skipping recovery")
        return {"recovered": 0, "skipped": 0, "reason": "node_type_unavailable"}

    solver_node = _find_solver_for_model(model_root)

    drivers = cmds.ls(type="mmdPhysicsBoneDriver") or []
    for driver in drivers:
        if not cmds.objExists(driver):
            continue

        root_connections = cmds.listConnections(
            f"{driver}.mmd_model_root", source=True, destination=False
        ) or []
        if model_root not in root_connections:
            continue

        try:
            target_joint = cmds.getAttr(f"{driver}.mmd_target_joint") or ""
        except Exception:
            target_joint = ""
        if not target_joint or not cmds.objExists(target_joint):
            skipped += 1
            continue

        anim_curves = _capture_anim_curve_connections(target_joint)

        reconnected = False
        for out_attr, joint_attr in [("outTranslate", "translate"), ("outRotate", "rotate")]:
            src_plug = f"{driver}.{out_attr}"
            dst_plug = f"{target_joint}.{joint_attr}"
            existing = cmds.listConnections(src_plug, source=False, destination=True, plugs=True) or []
            if dst_plug in existing:
                continue
            try:
                cmds.connectAttr(src_plug, dst_plug, force=True)
                reconnected = True
            except Exception as exc:
                log.warning(
                    "event=physics_driver_recovery_failed driver=%s joint=%s attr=%s error=%s",
                    driver, target_joint, joint_attr, exc,
                )

        if anim_curves and solver_node:
            routed = _reroute_anim_to_pre_physics(
                driver=driver,
                anim_curves=anim_curves,
                solver=solver_node,
                logger=log,
            )
            if routed:
                pre_physics_routed += 1

        if reconnected:
            recovered += 1
            log.info(
                "event=physics_driver_recovered driver=%s joint=%s",
                driver, target_joint,
            )
        else:
            skipped += 1

    if recovered or pre_physics_routed:
        log.info(
            "Physics driver recovery: %d reconnected, %d pre-physics routed, "
            "%d skipped for model %s",
            recovered, pre_physics_routed, skipped, model_root,
        )
    return {
        "recovered": recovered,
        "skipped": skipped,
        "pre_physics_routed": pre_physics_routed,
    }


def _find_solver_for_model(model_root: str):
    """Find the mmdPhysicsSolver connected to *model_root*."""
    try:
        available = set(cmds.allNodeTypes() or [])
    except Exception:
        available = set()
    if "mmdPhysicsSolver" not in available:
        return None
    solvers = cmds.ls(type="mmdPhysicsSolver") or []
    for solver in solvers:
        conns = cmds.listConnections(
            f"{solver}.modelRoot", source=True, destination=False
        ) or []
        if model_root in conns:
            return solver
    return None


def _capture_anim_curve_connections(joint: str) -> dict:
    """Capture animCurve source connections on joint translate/rotate channels.

    Returns a dict like ``{"translateX": "animCurve1.output", ...}``.
    """
    result = {}
    for compound, channels in [
        ("translate", ("X", "Y", "Z")),
        ("rotate", ("X", "Y", "Z")),
    ]:
        for ch in channels:
            plug = f"{joint}.{compound}{ch}"
            sources = cmds.listConnections(plug, source=True, destination=False,
                                           plugs=True, type="animCurve") or []
            if sources:
                result[f"{compound}{ch}"] = sources[0]
    return result


def _reroute_anim_to_pre_physics(
    *,
    driver: str,
    anim_curves: dict,
    solver: str,
    logger=None,
) -> bool:
    """Connect captured animCurve outputs to driver pre-physics inputs.

    Also wires ``driver.outPrePhysicsWorldMatrix`` →
    ``solver.inKinematicWorldMatrix[boneIndex]``.
    """
    log = logger or _logger
    attr_map = {
        "translateX": "inPreTranslateX",
        "translateY": "inPreTranslateY",
        "translateZ": "inPreTranslateZ",
        "rotateX": "inPreRotateX",
        "rotateY": "inPreRotateY",
        "rotateZ": "inPreRotateZ",
    }

    any_connected = False
    for joint_channel, anim_plug in anim_curves.items():
        driver_attr = attr_map.get(joint_channel)
        if not driver_attr:
            continue
        dst_plug = f"{driver}.{driver_attr}"
        try:
            cmds.connectAttr(anim_plug, dst_plug, force=True)
            any_connected = True
        except Exception as exc:
            log.debug(
                "event=pre_physics_reroute_failed driver=%s attr=%s error=%s",
                driver, driver_attr, exc,
            )

    if not any_connected:
        return False

    try:
        bone_index = cmds.getAttr(f"{driver}.inBoneIndex")
    except Exception:
        bone_index = -1
    if bone_index < 0:
        return False

    src = f"{driver}.outPrePhysicsWorldMatrix"
    dst = f"{solver}.inKinematicWorldMatrix[{bone_index}]"
    try:
        cmds.connectAttr(src, dst, force=True)
    except Exception as exc:
        log.debug(
            "event=pre_physics_solver_connect_failed driver=%s bone=%d error=%s",
            driver, bone_index, exc,
        )
        return False

    log.info(
        "event=pre_physics_input_routed driver=%s bone_index=%d",
        driver, bone_index,
    )
    return True
