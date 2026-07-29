"""Saved Maya bind-world resolution for the stateful MMD physics solver.

The resolver is intentionally Maya-command based and does not read the live
joint world matrix as a bind fallback.  A saved ``dagPose.worldMatrix`` is the
primary authority; an inverse ``skinCluster.bindPreMatrix`` is accepted only
after finite, invertibility, and cross-candidate agreement checks.
"""

from __future__ import annotations

import math
import re

import maya.api.OpenMaya as om


BIND_MATRIX_TOLERANCE = 1e-5
BIND_SINGULAR_TOLERANCE = 1e-12
BIND_BASIS_MISSING = "bind_basis_missing"
BIND_BASIS_NONFINITE = "bind_basis_nonfinite"
BIND_BASIS_SINGULAR = "bind_basis_singular"
BIND_BASIS_MISMATCH = "bind_basis_mismatch"
BIND_BASIS_AMBIGUOUS = "bind_basis_ambiguous"


class BindBasisResolutionError(RuntimeError):
    """Fail-closed error raised when a saved Maya bind basis is unusable."""

    def __init__(self, reason_code: str, joint: str, detail: str = ""):
        self.reason_code = reason_code
        self.joint = joint
        message = f"{reason_code} for {joint}"
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)


def _matrix_values(value):
    """Return a finite-checkable 16-value matrix representation."""
    if value is None:
        return None
    try:
        values = [float(item) for item in value]
    except Exception:
        values = None
    if values is not None and len(values) == 16:
        return values
    try:
        return [
            float(value.getElement(row, column))
            for row in range(4)
            for column in range(4)
        ]
    except Exception:
        pass
    try:
        return [
            float(value[row][column])
            for row in range(4)
            for column in range(4)
        ]
    except Exception:
        return None


def _validate_matrix(value, *, joint: str, label: str):
    """Validate finite, invertible matrix and return an ``MMatrix``."""
    values = _matrix_values(value)
    if values is None or len(values) != 16 or not all(math.isfinite(v) for v in values):
        raise BindBasisResolutionError(
            BIND_BASIS_NONFINITE,
            joint,
            f"{label} is not a finite 4x4 matrix",
        )
    try:
        matrix = om.MMatrix(values)
        # Maya's MMatrix.inverse() historically returns an identity-like value
        # for some singular inputs instead of raising.  Check determinant first.
        try:
            determinant = float(matrix.det4x4())
        except AttributeError:
            determinant = None
        if determinant is not None and not math.isfinite(determinant):
            raise BindBasisResolutionError(
                BIND_BASIS_NONFINITE,
                joint,
                f"{label} determinant is non-finite ({determinant})",
            )
        if determinant is not None and abs(determinant) <= BIND_SINGULAR_TOLERANCE:
            raise BindBasisResolutionError(
                BIND_BASIS_SINGULAR,
                joint,
                f"{label} determinant is singular ({determinant})",
            )
        inverse = matrix.inverse()
        inverse_values = _matrix_values(inverse)
        if inverse_values is None or not all(math.isfinite(v) for v in inverse_values):
            raise ValueError("inverse is non-finite")
    except BindBasisResolutionError:
        raise
    except Exception as exc:
        raise BindBasisResolutionError(
            BIND_BASIS_SINGULAR,
            joint,
            f"{label} is singular ({exc or type(exc).__name__})",
        ) from exc
    return matrix


def _matrices_agree(left, right, tolerance: float = BIND_MATRIX_TOLERANCE) -> bool:
    left_values = _matrix_values(left)
    right_values = _matrix_values(right)
    if left_values is None or right_values is None or len(left_values) != len(right_values):
        return False
    return max(abs(a - b) for a, b in zip(left_values, right_values)) <= tolerance


def _same_node(left, right, cmds) -> bool:
    """Compare Maya node paths without collapsing namespace-qualified names."""
    if not left or not right:
        return False
    left_text, right_text = str(left), str(right)
    if left_text == right_text or left_text.lstrip("|") == right_text.lstrip("|"):
        return True
    try:
        left_long = cmds.ls(left_text, long=True) or []
        right_long = cmds.ls(right_text, long=True) or []
        return bool(set(left_long).intersection(right_long))
    except Exception:
        return False


def _dag_pose_bind_candidates(joint, cmds):
    """Read saved ``dagPose.worldMatrix`` candidates for ``joint``."""
    try:
        poses = cmds.dagPose(joint, query=True, bindPose=True) or []
    except Exception:
        poses = []
    candidates = []
    for pose in poses:
        try:
            members = cmds.dagPose(pose, query=True, members=True) or []
        except Exception:
            members = []

        # ``members`` is a multi attribute and can be sparse.  Resolve the
        # actual logical index through each message connection rather than
        # assuming query order equals worldMatrix logical index.
        logical_indices = []
        logical_indices_query_succeeded = False
        try:
            logical_indices = cmds.getAttr(f"{pose}.members", multiIndices=True) or []
            logical_indices_query_succeeded = True
        except Exception:
            pass
        indexed_members = []
        for logical_index in logical_indices:
            try:
                connected = cmds.listConnections(
                    f"{pose}.members[{logical_index}]",
                    source=True,
                    destination=False,
                ) or []
            except Exception:
                connected = []
            indexed_members.append((logical_index, connected[0] if connected else None))
        if logical_indices and any(member is None for _, member in indexed_members):
            raise BindBasisResolutionError(
                BIND_BASIS_AMBIGUOUS,
                str(joint),
                f"{pose}.members logical index has no member connection",
            )
        if not indexed_members:
            if len(members) > 1 and not logical_indices_query_succeeded:
                raise BindBasisResolutionError(
                    BIND_BASIS_AMBIGUOUS,
                    str(joint),
                    f"{pose}.members logical indices are unavailable",
                )
            indexed_members = list(enumerate(members))

        for logical_index, member in indexed_members:
            if not _same_node(member, joint, cmds):
                continue
            try:
                raw = cmds.getAttr(f"{pose}.worldMatrix[{logical_index}]")
            except Exception:
                raw = None
            candidates.append((raw, f"{pose}.worldMatrix[{logical_index}]"))
            break
    return candidates


def _skin_cluster_nodes(joint, cmds):
    """Find skinClusters whose influence list contains ``joint``."""
    # Query connection topology only; do not read the live matrix value.
    for plug in (f"{joint}.message", f"{joint}.worldMatrix[0]"):
        for kwargs in (
            {"source": False, "destination": True, "type": "skinCluster"},
            {"type": "skinCluster"},
        ):
            try:
                result = cmds.listConnections(plug, **kwargs) or []
            except Exception:
                continue
            if result:
                return list(dict.fromkeys(result))
    return []


def _skin_bind_candidates(joint, cmds):
    """Read validated fallback candidates from ``bindPreMatrix``."""
    candidates = []
    for skin in _skin_cluster_nodes(joint, cmds):
        matrix_indices = []
        try:
            matrix_size = cmds.getAttr(f"{skin}.matrix", size=True) or 0
            matrix_indices.extend(range(int(matrix_size)))
        except Exception:
            pass

        # ``connections=True`` exposes source/destination pairs including the
        # logical destination index where array size is unavailable.
        bulk_matching_sources = []
        if not matrix_indices:
            try:
                pairs = cmds.listConnections(
                    f"{skin}.matrix", connections=True, plugs=True
                ) or []
            except Exception:
                pairs = []
            for pair_index in range(0, len(pairs) - 1, 2):
                source, destination = str(pairs[pair_index]), str(pairs[pair_index + 1])
                if not _same_node(source.split(".", 1)[0], joint, cmds):
                    source, destination = destination, source
                if not _same_node(source.split(".", 1)[0], joint, cmds):
                    continue
                match = re.search(r"\.matrix\[(\d+)\]$", destination)
                if match:
                    matrix_indices.append(int(match.group(1)))
            matrix_indices = list(dict.fromkeys(matrix_indices))

        if not matrix_indices:
            # Last-resort single-influence mapping for lightweight Maya
            # command wrappers; exactly one matching source maps to index 0.
            try:
                sources = cmds.listConnections(
                    f"{skin}.matrix", source=True, destination=False, plugs=True
                ) or []
            except Exception:
                sources = []
            matching_sources = [
                source
                for source in sources
                if _same_node(str(source).split(".", 1)[0], joint, cmds)
            ]
            if len(matching_sources) == 1:
                matrix_indices.append(0)
                bulk_matching_sources = matching_sources

        # A test double or older command implementation may not expose array
        # size; query influence order as a second route.
        if not matrix_indices:
            try:
                influences = cmds.skinCluster(skin, query=True, influence=True) or []
            except Exception:
                influences = []
            for index, influence in enumerate(influences):
                if _same_node(influence, joint, cmds):
                    matrix_indices.append(index)

        for index in matrix_indices:
            source_plugs = []
            try:
                source_plugs = cmds.listConnections(
                    f"{skin}.matrix[{index}]",
                    source=True,
                    destination=False,
                    plugs=True,
                ) or []
            except Exception:
                pass
            if not source_plugs and index == 0 and bulk_matching_sources:
                source_plugs = bulk_matching_sources
            if source_plugs and not any(
                _same_node(str(plug).split(".", 1)[0], joint, cmds)
                for plug in source_plugs
            ):
                continue
            if not source_plugs:
                try:
                    influences = cmds.skinCluster(skin, query=True, influence=True) or []
                except Exception:
                    influences = []
                if index >= len(influences) or not _same_node(influences[index], joint, cmds):
                    continue
            try:
                raw = cmds.getAttr(f"{skin}.bindPreMatrix[{index}]")
            except Exception:
                raw = None
            candidates.append((raw, f"{skin}.bindPreMatrix[{index}]"))
    return candidates


def resolve_saved_bind_world_matrix(joint):
    """Resolve a saved Maya bind world matrix, never using live pose."""
    from maya import cmds

    joint_name = str(joint)
    dag_candidates = _dag_pose_bind_candidates(joint, cmds)
    skin_candidates = _skin_bind_candidates(joint, cmds)

    dag_matrices = [
        _validate_matrix(raw, joint=joint_name, label=source)
        for raw, source in dag_candidates
    ]
    skin_matrices = []
    for raw, source in skin_candidates:
        pre_matrix = _validate_matrix(raw, joint=joint_name, label=source)
        try:
            bind_matrix = pre_matrix.inverse()
            if not all(math.isfinite(v) for v in _matrix_values(bind_matrix)):
                raise ValueError("inverse is non-finite")
        except Exception as exc:
            raise BindBasisResolutionError(
                BIND_BASIS_SINGULAR,
                joint_name,
                f"{source} inverse failed ({exc or type(exc).__name__})",
            ) from exc
        skin_matrices.append(bind_matrix)

    def collapse(candidates, source_kind):
        if not candidates:
            return None
        first = candidates[0]
        if any(not _matrices_agree(first, candidate) for candidate in candidates[1:]):
            raise BindBasisResolutionError(
                BIND_BASIS_AMBIGUOUS,
                joint_name,
                f"multiple {source_kind} bind candidates disagree",
            )
        return first

    dag_matrix = collapse(dag_matrices, "dagPose")
    skin_matrix = collapse(skin_matrices, "skinCluster")
    if dag_matrix is not None and skin_matrix is not None:
        if not _matrices_agree(dag_matrix, skin_matrix):
            raise BindBasisResolutionError(
                BIND_BASIS_MISMATCH,
                joint_name,
                "dagPose.worldMatrix and inverse(bindPreMatrix) disagree",
            )
        return dag_matrix
    if dag_matrix is not None:
        return dag_matrix
    if skin_matrix is not None:
        return skin_matrix
    raise BindBasisResolutionError(BIND_BASIS_MISSING, joint_name)
