"""Model-scoped Mirror Pose and Mirror Select primitives.

    The helpers are deliberately Maya-command agnostic at the pairing layer.  A
    presenter supplies UUID-resolved nodes and MMD bone names; scene mutation is
    limited to current transform values and is rolled back atomically on failure.
    The MMD reflection contract is local X-plane reflection: ``tx``, ``ry`` and
    ``rz`` negate while ``ty``, ``tz`` and ``rx`` are unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
import unicodedata
from typing import Iterable, Mapping

from ..core.mmd_control_rig_basis import (
    IDENTITY_QUATERNION,
    bone_to_control,
    control_to_bone,
)


_CHANNELS = (
    "translateX",
    "translateY",
    "translateZ",
    "rotateX",
    "rotateY",
    "rotateZ",
)


class MirrorActionError(RuntimeError):
    """Raised when a mirror pair or scene mutation is not provable safe."""


@dataclass(frozen=True)
class MirrorEntry:
    """One UUID-owned MMD joint or Control Rig node."""

    identity: str
    node: str
    joint: str
    names: tuple[str, ...]
    authoring_basis: tuple[float, float, float, float] = IDENTITY_QUATERNION
    # MMD-owned joints expose the PMX motion basis through jointOrient.
    joint_orient: tuple[float, float, float, float] = IDENTITY_QUATERNION
    bind_world_matrix: tuple[float, ...] | None = None
    bind_space_node: str | None = None


@dataclass(frozen=True)
class MirrorMapping:
    """A source node and its validated opposite-side destination."""

    source: MirrorEntry
    target: MirrorEntry


def _side_key(name: str) -> tuple[str, str] | None:
    """Return ``(side, base)`` for one explicit left/right name.

    PMX Japanese names use ``左``/``右``.  English metadata additionally
    accepts Left/Right words and conventional ``_L``/``_R`` suffixes.  Names
    without an explicit side (including center/root bones) are unpaired.
    """

    normalized = unicodedata.normalize("NFKC", str(name or "")).strip()
    if not normalized:
        return None
    for marker, side in (("左", "L"), ("右", "R")):
        if normalized.count(marker):
            if normalized.count(marker) != 1:
                return None
            base = normalized.replace(marker, "", 1)
            return side, _base_key(base)

    lowered = normalized.casefold()
    word = re.search(r"(?:^|[\s_.-])(left|right)(?=$|[\s_.-])", lowered)
    if word is None:
        word = re.search(
            r"^(left|right)(?=[A-Z0-9_\-. ])", normalized, flags=re.IGNORECASE
        )
    if word is not None:
        side = "L" if word.group(1).casefold() == "left" else "R"
        base = normalized[: word.start(1)] + normalized[word.end(1) :]
        return side, _base_key(base)

    suffix = re.search(r"(?:^|[\s_.-])([lr])$", lowered)
    if suffix is not None:
        return suffix.group(1).upper(), _base_key(normalized[: suffix.start(1)])
    return None


def _base_key(value: str) -> str:
    """Normalize a side-stripped name while preserving non-Latin scripts."""

    return re.sub(r"[^\w\u0080-\uffff]+", "", value.casefold())


def build_mirror_pairs(entries: Iterable[MirrorEntry]) -> Mapping[str, MirrorEntry]:
    """Build an identity-to-opposite map, rejecting ambiguous pairs."""

    unique = {}
    for entry in entries:
        if not entry.identity or entry.identity in unique:
            raise MirrorActionError("duplicate or missing mirror identity")
        if not entry.node or not entry.joint:
            raise MirrorActionError("mirror entry is incomplete")
        unique[entry.identity] = entry

    candidates: dict[tuple[str, str], set[str]] = {}
    for entry in unique.values():
        for name in entry.names:
            key = _side_key(name)
            if key is not None and key[1]:
                candidates.setdefault(key, set()).add(entry.identity)

    result: dict[str, MirrorEntry] = {}
    for (side, base), identities in candidates.items():
        opposite = "R" if side == "L" else "L"
        opposite_ids = candidates.get((opposite, base), set())
        if len(identities) != 1 or len(opposite_ids) != 1:
            continue
        identity = next(iter(identities))
        opposite_id = next(iter(opposite_ids))
        if identity == opposite_id:
            continue
        existing = result.get(identity)
        target = unique[opposite_id]
        if existing is not None and existing.identity != target.identity:
            raise MirrorActionError("ambiguous mirror counterpart")
        result[identity] = target
    return result


def mirrored_transform_values(
    translation: Iterable[float],
    rotation: Iterable[float],
    *,
    source_basis=IDENTITY_QUATERNION,
    target_basis=IDENTITY_QUATERNION,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Mirror one XYZ transform through source and target authoring bases."""

    translation = tuple(float(value) for value in translation)
    if len(translation) != 3 or not all(math.isfinite(value) for value in translation):
        raise MirrorActionError("mirror translation must contain XYZ")
    tx, ty, tz = translation
    rotation = tuple(float(value) for value in rotation)
    if len(rotation) != 3 or not all(math.isfinite(value) for value in rotation):
        raise MirrorActionError("mirror rotation must contain XYZ")
    if source_basis == IDENTITY_QUATERNION and target_basis == IDENTITY_QUATERNION:
        rx, ry, rz = rotation
        return (-tx, ty, tz), (rx, -ry, -rz)
    control_quaternion = _quaternion_from_euler_degrees(rotation)
    source_bone = control_to_bone(control_quaternion, source_basis)
    x, y, z, w = source_bone
    mirrored_bone = (x, -y, -z, w)
    target_quaternion = bone_to_control(mirrored_bone, target_basis)
    return (-tx, ty, tz), _euler_degrees_from_quaternion(target_quaternion)


class MirrorPoseTransaction:
    """Mirror current transform values without changing animation topology."""

    def __init__(
        self,
        adapter,
        *,
        model_root: str,
        model_uuid: str,
        mappings: Iterable[MirrorMapping],
        scope_roots: Iterable[str] | None = None,
    ):
        self.adapter = adapter
        self.model_root = str(model_root)
        self.model_uuid = str(model_uuid)
        self.mappings = tuple(mappings)
        self.scope_roots = tuple(
            dict.fromkeys(str(scope) for scope in (scope_roots or (model_root,)))
        )
        self._snapshots: dict[str, tuple[dict[str, object], dict[str, tuple[str, ...]], dict[str, bool]]] = {}
        self._world_matrices: dict[str, tuple[float, ...]] = {}

    def apply(self) -> int:
        """Apply one atomic current-frame mirror operation."""

        if not self.mappings:
            raise MirrorActionError("no mirror mappings")
        cmds = getattr(self.adapter, "_cmds", None)
        if cmds is None:
            return self._apply_headless()
        self._assert_model_uuid(cmds)
        nodes = []
        for mapping in self.mappings:
            nodes.extend((mapping.source.node, mapping.target.node))
        self._capture(cmds, nodes)
        for mapping in self.mappings:
            if (
                mapping.source.bind_world_matrix is not None
                and mapping.target.bind_world_matrix is not None
            ):
                self._capture_world_matrix(cmds, mapping.source.joint)
                self._capture_world_matrix(cmds, mapping.target.joint)
        desired_worlds = {}
        for mapping in self.mappings:
            if (
                mapping.source.bind_world_matrix is None
                or mapping.target.bind_world_matrix is None
            ):
                continue
            target = mapping.target.joint
            if target in desired_worlds:
                raise MirrorActionError(f"duplicate mirror target: {target}")
            desired_worlds[target] = self._desired_mmd_world(cmds, mapping)
        opened = self._open_undo("Animator Mirror Pose")
        try:
            # Parent-side targets must settle before descendants are solved.
            # Stable depth ordering also covers intervening, unselected joints
            # (for example an arm-twist joint between arm and elbow).
            write_mappings = sorted(
                self.mappings,
                key=lambda mapping: mapping.target.joint.count("|"),
            )
            for mapping in write_mappings:
                source_values = self._snapshots[mapping.source.node][0]
                target = mapping.target.node
                if (
                    mapping.source.bind_world_matrix is not None
                    and mapping.target.bind_world_matrix is not None
                ):
                    if mapping.target.node == mapping.target.joint:
                        translation, rotation = self._mirrored_mmd_joint_values(
                            cmds,
                            mapping,
                            desired_worlds,
                        )
                    else:
                        translation, rotation = self._mirrored_control_values(
                            cmds,
                            mapping,
                            desired_worlds,
                        )
                else:
                    translation, rotation = mirrored_transform_values(
                        (source_values[channel] for channel in _CHANNELS[:3]),
                        (source_values[channel] for channel in _CHANNELS[3:]),
                        source_basis=mapping.source.authoring_basis,
                        target_basis=mapping.target.authoring_basis,
                    )
                mirrored_values = dict(zip(_CHANNELS, (*translation, *rotation)))
                for channel, value in mirrored_values.items():
                    incoming = self._snapshots[target][1][channel]
                    if incoming and not self._is_direct_anim_curve(cmds, incoming):
                        raise MirrorActionError(
                            f"mirror target has an unsupported writer: {target}.{channel}"
                        )
                    if incoming and not opened:
                        raise MirrorActionError(
                            "Mirror Pose requires Maya Undo for keyed targets"
                        )
                    self._write_channel(
                        cmds,
                        f"{target}.{channel}",
                        value,
                        self._snapshots[target][2][channel],
                        writer=incoming[0] if incoming else None,
                    )
            return len(self.mappings)
        except Exception as exc:
            try:
                if opened:
                    self._close_undo(opened)
                    opened = False
                    cmds.undo()
                    self._restore(cmds)
                else:
                    self._restore(cmds)
            except Exception as rollback_error:
                raise MirrorActionError(
                    f"Mirror Pose failed and rollback was incomplete: {rollback_error}"
                ) from exc
            raise MirrorActionError(str(exc)) from exc
        finally:
            self._close_undo(opened)

    def _capture(self, cmds, nodes: Iterable[str]) -> None:
        capture_world = any(
            mapping.source.bind_world_matrix is not None
            and mapping.target.bind_world_matrix is not None
            for mapping in self.mappings
        )
        for node in dict.fromkeys(nodes):
            paths = cmds.ls(node, long=True) or []
            if len(paths) != 1 or str(paths[0]) != str(node):
                raise MirrorActionError(f"ambiguous mirror node: {node}")
            try:
                rotate_order = int(cmds.getAttr(f"{node}.rotateOrder"))
            except Exception:
                # Lightweight doubles and older imported joints may omit the
                # optional plug; the MMD contract defaults to XYZ (0).
                rotate_order = 0
            if rotate_order != 0:
                raise MirrorActionError(
                    f"unsupported non-XYZ rotate order on mirror node: {node}"
                )
            values = {}
            incoming = {}
            locks = {}
            for channel in _CHANNELS:
                plug = f"{node}.{channel}"
                try:
                    values[channel] = float(cmds.getAttr(plug))
                    incoming[channel] = tuple(
                        str(source)
                        for source in (
                            cmds.listConnections(
                                plug,
                                source=True,
                                destination=False,
                                plugs=True,
                            )
                            or []
                        )
                    )
                    locks[channel] = bool(cmds.getAttr(plug, lock=True))
                except Exception as exc:
                    raise MirrorActionError(f"mirror channel unavailable: {plug}") from exc
            self._snapshots[str(node)] = (values, incoming, locks)
            if capture_world:
                self._capture_world_matrix(cmds, str(node))

    def _capture_world_matrix(self, cmds, node: str) -> None:
        if node in self._world_matrices:
            return
        paths = cmds.ls(node, long=True) or []
        if len(paths) != 1 or str(paths[0]) != str(node):
            raise MirrorActionError(f"ambiguous mirror world node: {node}")
        try:
            matrix = tuple(
                float(value)
                for value in cmds.xform(
                    node,
                    query=True,
                    matrix=True,
                    worldSpace=True,
                )
            )
        except Exception as exc:
            raise MirrorActionError(f"mirror world matrix unavailable: {node}") from exc
        if len(matrix) != 16 or not all(math.isfinite(value) for value in matrix):
            raise MirrorActionError(f"mirror world matrix is invalid: {node}")
        self._world_matrices[str(node)] = matrix

    @staticmethod
    def _space_world_matrix(cmds, node: str | None):
        import maya.api.OpenMaya as om

        if node is None:
            return om.MMatrix()
        paths = cmds.ls(node, long=True) or []
        if len(paths) != 1:
            raise MirrorActionError(f"mirror bind space is ambiguous: {node}")
        return om.MMatrix(
            cmds.xform(
                paths[0],
                query=True,
                matrix=True,
                worldSpace=True,
            )
        )

    def _desired_mmd_world(self, cmds, mapping: MirrorMapping):
        """Return the reflected source skin delta in the target live bind space."""

        try:
            import maya.api.OpenMaya as om

            reflection = om.MMatrix(
                (
                    -1.0, 0.0, 0.0, 0.0,
                    0.0, 1.0, 0.0, 0.0,
                    0.0, 0.0, 1.0, 0.0,
                    0.0, 0.0, 0.0, 1.0,
                )
            )
            source_bind = om.MMatrix(mapping.source.bind_world_matrix)
            source_space = self._space_world_matrix(cmds, mapping.source.bind_space_node)
            source_world = (
                om.MMatrix(self._world_matrices[mapping.source.joint])
                * source_space.inverse()
            )
            target_bind = om.MMatrix(mapping.target.bind_world_matrix)
            target_space = self._space_world_matrix(cmds, mapping.target.bind_space_node)
            source_skin = source_bind.inverse() * source_world
            desired_world = (
                target_bind * reflection * source_skin * reflection * target_space
            )
            return tuple(float(value) for value in desired_world)
        except MirrorActionError:
            raise
        except Exception as exc:
            raise MirrorActionError(
                f"MMD mirror desired world solve failed: {mapping.target.node}"
            ) from exc

    def _mirrored_mmd_joint_values(
        self,
        cmds,
        mapping: MirrorMapping,
        desired_worlds: Mapping[str, tuple[float, ...]],
    ):
        """Solve target TR channels from a precomputed desired world matrix."""

        try:
            import maya.api.OpenMaya as om

            local = self._target_local_transform(cmds, mapping, desired_worlds)
            translate = local.translation(om.MSpace.kTransform)
            total_rotation = local.rotation(asQuaternion=True)
            joint_orient = om.MQuaternion(*mapping.target.joint_orient)
            rotate = total_rotation * joint_orient.inverse()
            translation = (float(translate.x), float(translate.y), float(translate.z))
            rotation = self._closest_target_rotation(om, mapping, rotate)
        except MirrorActionError:
            raise
        except Exception as exc:
            raise MirrorActionError(
                f"MMD mirror world-space solve failed: {mapping.target.node}"
            ) from exc
        if not all(math.isfinite(value) for value in (*translation, *rotation)):
            raise MirrorActionError(f"MMD mirror result is invalid: {mapping.target.node}")
        return translation, rotation

    def _mirrored_control_values(
        self,
        cmds,
        mapping: MirrorMapping,
        desired_worlds: Mapping[str, tuple[float, ...]],
    ):
        """Solve a Control Rig target from its driven joint's desired world."""

        try:
            import maya.api.OpenMaya as om

            local = self._target_local_transform(cmds, mapping, desired_worlds)
            total_rotation = local.rotation(asQuaternion=True)
            joint_orient = om.MQuaternion(*mapping.target.joint_orient)
            bone_rotation = total_rotation * joint_orient.inverse()
            control_rotation = bone_to_control(
                tuple(
                    float(getattr(bone_rotation, component))
                    for component in ("x", "y", "z", "w")
                ),
                mapping.target.authoring_basis,
            )
            control_quaternion = om.MQuaternion(*control_rotation)
            source_values = self._snapshots[mapping.source.node][0]
            translation = (
                -float(source_values["translateX"]),
                float(source_values["translateY"]),
                float(source_values["translateZ"]),
            )
            rotation = self._closest_target_rotation(om, mapping, control_quaternion)
        except MirrorActionError:
            raise
        except Exception as exc:
            raise MirrorActionError(
                f"Control Rig mirror world-space solve failed: {mapping.target.node}"
            ) from exc
        if not all(math.isfinite(value) for value in (*translation, *rotation)):
            raise MirrorActionError(f"Control Rig mirror result is invalid: {mapping.target.node}")
        return translation, rotation

    @staticmethod
    def _target_parent_world(cmds, target_joint, desired_worlds, om):
        parents = cmds.listRelatives(target_joint, parent=True, fullPath=True) or []
        if len(parents) > 1:
            raise MirrorActionError(f"mirror target parent is ambiguous: {target_joint}")
        parent = str(parents[0]) if parents else None
        parent_world = desired_worlds.get(parent)
        if parent_world is not None:
            return om.MMatrix(parent_world)
        if parent:
            return om.MMatrix(
                cmds.xform(parent, query=True, matrix=True, worldSpace=True)
            )
        return om.MMatrix()

    def _target_local_transform(self, cmds, mapping, desired_worlds):
        """Resolve and validate the target joint's desired local transform."""

        import maya.api.OpenMaya as om

        desired_world = om.MMatrix(desired_worlds[mapping.target.joint])
        parent_world = self._target_parent_world(
            cmds, mapping.target.joint, desired_worlds, om
        )
        local = om.MTransformationMatrix(desired_world * parent_world.inverse())
        scale = tuple(float(value) for value in local.scale(om.MSpace.kTransform))
        shear = tuple(float(value) for value in local.shear(om.MSpace.kTransform))
        if any(abs(value - 1.0) > 1.0e-6 for value in scale) or any(
            abs(value) > 1.0e-6 for value in shear
        ):
            raise MirrorActionError(
                f"mirror target requires unsupported scale or shear: {mapping.target.joint}"
            )
        return local

    def _closest_target_rotation(self, om, mapping, quaternion):
        euler = quaternion.asEulerRotation()
        euler.reorderIt(om.MEulerRotation.kXYZ)
        current = self._snapshots[mapping.target.node][0]
        reference = om.MEulerRotation(
            *(math.radians(float(current[f"rotate{axis}"])) for axis in "XYZ"),
            om.MEulerRotation.kXYZ,
        )
        euler = euler.closestSolution(reference)
        return tuple(math.degrees(float(value)) for value in (euler.x, euler.y, euler.z))

    def _restore(self, cmds) -> None:
        self._assert_model_uuid(cmds)
        for node, (values, incoming, locks) in self._snapshots.items():
            for channel in _CHANNELS:
                plug = f"{node}.{channel}"
                current = tuple(
                    str(source)
                    for source in (
                        cmds.listConnections(
                            plug,
                            source=True,
                            destination=False,
                            plugs=True,
                        )
                        or []
                    )
                )
                if current != incoming[channel]:
                    raise MirrorActionError(f"mirror topology changed: {plug}")
                if not incoming[channel]:
                    self._set_channel(cmds, plug, values[channel], locks[channel])

    @staticmethod
    def _set_channel(cmds, plug: str, value: float, locked: bool) -> None:
        if locked:
            cmds.setAttr(plug, lock=False)
        try:
            cmds.setAttr(plug, value)
        finally:
            if locked:
                cmds.setAttr(plug, lock=True)

    @classmethod
    def _write_channel(
        cls,
        cmds,
        plug: str,
        value: float,
        locked: bool,
        *,
        writer: str | None,
    ) -> None:
        if not writer:
            cls._set_channel(cmds, plug, value, locked)
            return
        if locked:
            cmds.setAttr(plug, lock=False)
        try:
            current_time = float(cmds.currentTime(query=True))
            node, channel = plug.rsplit(".", 1)
            cmds.setKeyframe(
                node,
                attribute=channel,
                time=(current_time,),
            )
            curve = writer.rsplit(".", 1)[0]
            cmds.keyframe(
                curve,
                edit=True,
                time=(current_time, current_time),
                valueChange=float(value),
                absolute=True,
            )
        finally:
            if locked:
                cmds.setAttr(plug, lock=True)

    @staticmethod
    def _is_direct_anim_curve(cmds, incoming: tuple[str, ...]) -> bool:
        if len(incoming) != 1:
            return False
        try:
            node = incoming[0].rsplit(".", 1)[0]
            return str(cmds.nodeType(node)).startswith("animCurve")
        except Exception:
            return False

    def _apply_headless(self) -> int:
        snapshots = {}
        try:
            for mapping in self.mappings:
                for node in (mapping.source.node, mapping.target.node):
                    if node not in snapshots:
                        snapshots[node] = (
                            list(self.adapter.xform(node, query=True, translation=True) or (0, 0, 0)),
                            list(self.adapter.xform(node, query=True, rotation=True) or (0, 0, 0)),
                        )
            for mapping in self.mappings:
                translation, rotation = snapshots[mapping.source.node]
                target = mapping.target.node
                mirrored_translation, mirrored_rotation = mirrored_transform_values(
                    translation,
                    rotation,
                    source_basis=mapping.source.authoring_basis,
                    target_basis=mapping.target.authoring_basis,
                )
                self.adapter.xform(
                    target,
                    translation=mirrored_translation,
                    rotation=mirrored_rotation,
                )
            return len(self.mappings)
        except Exception as exc:
            for node, (translation, rotation) in snapshots.items():
                try:
                    self.adapter.xform(node, translation=translation, rotation=rotation)
                except Exception:
                    pass
            raise MirrorActionError(str(exc)) from exc

    def _assert_model_uuid(self, cmds) -> None:
        roots = cmds.ls(self.model_root, uuid=True) or []
        if len(roots) != 1 or str(roots[0]) != self.model_uuid:
            raise MirrorActionError("MMD model UUID changed during Mirror Pose")
        root_paths = cmds.ls(self.model_root, long=True) or []
        if len(root_paths) != 1:
            raise MirrorActionError("MMD model root is ambiguous during Mirror Pose")
        scopes = []
        for scope in self.scope_roots:
            paths = cmds.ls(scope, long=True) or []
            if len(paths) != 1:
                raise MirrorActionError(f"Mirror Pose scope is ambiguous: {scope}")
            scopes.append(str(paths[0]))
        root_path = str(root_paths[0])
        if root_path not in scopes:
            scopes.insert(0, root_path)
        for mapping in self.mappings:
            for node in (mapping.source.node, mapping.target.node):
                paths = cmds.ls(node, long=True) or []
                if len(paths) != 1:
                    raise MirrorActionError(f"ambiguous mirror node: {node}")
                path = str(paths[0])
                if not any(path == scope or path.startswith(scope + "|") for scope in scopes):
                    raise MirrorActionError(f"mirror node is outside model scope: {node}")

    def _open_undo(self, name: str) -> bool:
        try:
            self.adapter.undo_info(openChunk=True, chunkName=name)
            return True
        except Exception:
            return False

    def _close_undo(self, opened: bool) -> None:
        if opened:
            try:
                self.adapter.undo_info(closeChunk=True)
            except Exception:
                pass


def _quaternion_from_euler_degrees(values) -> tuple[float, float, float, float]:
    """Convert Maya XYZ Euler degrees to an xyzw quaternion."""

    x, y, z = (math.radians(float(value)) * 0.5 for value in values)
    cx, sx = math.cos(x), math.sin(x)
    cy, sy = math.cos(y), math.sin(y)
    cz, sz = math.cos(z), math.sin(z)
    return (
        sx * cy * cz - cx * sy * sz,
        cx * sy * cz + sx * cy * sz,
        cx * cy * sz - sx * sy * cz,
        cx * cy * cz + sx * sy * sz,
    )


def _euler_degrees_from_quaternion(quaternion) -> tuple[float, float, float]:
    """Convert an xyzw quaternion to Maya XYZ Euler degrees."""

    x, y, z, w = (float(value) for value in quaternion)
    sinr = 2.0 * (w * x + y * z)
    cosr = 1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(sinr, cosr)
    sinp = 2.0 * (w * y - z * x)
    pitch = math.copysign(math.pi / 2.0, sinp) if abs(sinp) >= 1.0 else math.asin(sinp)
    siny = 2.0 * (w * z + x * y)
    cosy = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.atan2(siny, cosy)
    return tuple(math.degrees(value) for value in (roll, pitch, yaw))
