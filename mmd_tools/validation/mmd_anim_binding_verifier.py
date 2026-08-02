"""Optional in-process verification through the experimental mmd-anim binding.

The CLI adapter remains the file-format gate.  This module is a separate,
opt-in runtime check for PMX bytes plus an optional VMD clip: it evaluates one
frame, reads world matrices and morph weights, and lets the binding own all
native handle cleanup through its context-manager API.
"""

from contextlib import contextmanager
import importlib
import math
from pathlib import Path
import sys
from typing import Any, Callable, Iterator, Mapping, Optional, Sequence

from .export_validator import ExportValidationIssue, ExportValidationReport


MAX_ERROR_LENGTH = 256
RuntimeFactory = Callable[[Optional[str]], Any]


def _issue(code: str, path: str, message: str) -> ExportValidationIssue:
    """Build one blocking binding issue with bounded human-facing text."""
    return ExportValidationIssue(
        code,
        "fatal",
        True,
        path,
        str(message)[:MAX_ERROR_LENGTH],
    )


def _report(issues: Sequence[ExportValidationIssue]) -> ExportValidationReport:
    """Create the common PMX binding report."""
    return ExportValidationReport("pmx", tuple(issues), mode="binding")


def _finite_matrix(values: Any, bone_count: int) -> Optional[str]:
    """Return a deterministic matrix error, or ``None`` when the readback is valid."""
    try:
        length = len(values)
    except TypeError:
        return "world matrix readback is not a sequence"
    expected_length = bone_count * 16
    if length != expected_length:
        return f"world matrix readback length {length} does not match expected {expected_length}"
    for index, value in enumerate(values):
        try:
            finite = math.isfinite(float(value))
        except (TypeError, ValueError, OverflowError):
            finite = False
        if not finite:
            return f"world matrix readback contains a non-finite value at index {index}"
    return None


def _finite_weights(values: Any, morph_count: int) -> Optional[str]:
    """Return a deterministic morph readback error, or ``None``."""
    try:
        length = len(values)
    except TypeError:
        return "morph weight readback is not a sequence"
    if length != morph_count:
        return f"morph weight readback length {length} does not match expected {morph_count}"
    for index, value in enumerate(values):
        try:
            finite = math.isfinite(float(value))
        except (TypeError, ValueError, OverflowError):
            finite = False
        if not finite:
            return f"morph weight readback contains a non-finite value at index {index}"
    return None


@contextmanager
def _binding_runtime_module(binding_root: Optional[str]) -> Iterator[Any]:
    """Temporarily expose the external binding and yield its runtime module."""
    if not binding_root:
        raise ImportError("binding_root is required for the experimental mmd-anim binding")
    resolved_root = str(Path(binding_root).expanduser().resolve())
    if not Path(resolved_root).is_dir():
        raise ImportError(f"binding root does not exist: {resolved_root}")
    original_path = list(sys.path)
    sys.path.insert(0, resolved_root)
    try:
        yield importlib.import_module("mmd_anim._runtime")
    finally:
        sys.path[:] = original_path


def _verify_with_runtime(
    runtime: Any,
    model_path: Path,
    motion_path: Optional[Path],
    *,
    frame: float,
    expected_counts: Mapping[str, int],
) -> ExportValidationReport:
    """Evaluate one model/clip through an already-created binding runtime."""
    issues = []
    model_bytes = model_path.read_bytes()
    model = runtime.create_model_from_pmx_bytes(model_bytes)
    with model:
        bone_count = int(model.bone_count())
        morph_count = int(model.morph_count())
        for name, actual in (("bones", bone_count), ("morphs", morph_count)):
            expected = expected_counts.get(name)
            if expected is not None and int(expected) != actual:
                issues.append(
                    _issue(
                        "MMD_ANIM_BINDING_COUNT_MISMATCH",
                        f"binding.model.{name}_count",
                        f"binding {name} count {actual} does not match expected {expected}",
                    )
                )

        instance = model.create_instance_for_model()
        with instance:
            if motion_path is None:
                instance.evaluate_rest_pose()
            else:
                clip = runtime.create_clip_from_vmd_bytes(model, motion_path.read_bytes())
                with clip:
                    instance.evaluate_clip_frame(clip, frame)

            matrix_error = _finite_matrix(instance.world_matrices_f32(), bone_count)
            if matrix_error:
                issues.append(_issue("MMD_ANIM_BINDING_MATRIX_INVALID", "binding.world_matrices", matrix_error))
            weight_error = _finite_weights(instance.morph_weights_f32(), morph_count)
            if weight_error:
                issues.append(_issue("MMD_ANIM_BINDING_WEIGHT_INVALID", "binding.morph_weights", weight_error))
    return _report(issues)


def verify_mmd_anim_binding_asset(
    model_path: str,
    *,
    motion_path: Optional[str] = None,
    binding_root: Optional[str] = None,
    runtime_library: Optional[str] = None,
    frame: float = 0.0,
    expected_counts: Optional[Mapping[str, int]] = None,
    runtime_factory: Optional[RuntimeFactory] = None,
) -> ExportValidationReport:
    """Verify one PMX asset and optional VMD clip through the Python binding.

    Args:
        model_path: PMX file whose bytes are passed to the binding.
        motion_path: Optional VMD file evaluated at ``frame``.
        binding_root: External ``bindings/python`` directory.
        runtime_library: Optional explicit native FFI library path.
        frame: Representative VMD frame when ``motion_path`` is supplied.
        expected_counts: Optional ``bones``/``morphs`` count contract.
        runtime_factory: Test seam receiving ``runtime_library``.
    """
    model = Path(model_path)
    motion = Path(motion_path) if motion_path else None
    if not model.is_file() or (motion is not None and not motion.is_file()):
        return _report(
            (
                _issue(
                    "MMD_ANIM_BINDING_INPUT_INVALID",
                    "binding.input",
                    "PMX model and optional VMD motion files must exist",
                ),
            )
        )
    try:
        numeric_frame = float(frame)
    except (TypeError, ValueError, OverflowError):
        return _report((_issue("MMD_ANIM_BINDING_INPUT_INVALID", "binding.frame", "frame must be finite"),))
    if not math.isfinite(numeric_frame):
        return _report((_issue("MMD_ANIM_BINDING_INPUT_INVALID", "binding.frame", "frame must be finite"),))

    try:
        if runtime_factory is not None:
            runtime = runtime_factory(runtime_library)
            return _verify_with_runtime(
                runtime,
                model,
                motion,
                frame=numeric_frame,
                expected_counts=expected_counts or {},
            )
        with _binding_runtime_module(binding_root) as runtime_module:
            runtime = runtime_module.RuntimeLibrary(runtime_library)
            return _verify_with_runtime(
                runtime,
                model,
                motion,
                frame=numeric_frame,
                expected_counts=expected_counts or {},
            )
    except (ImportError, ModuleNotFoundError, FileNotFoundError, OSError) as exc:
        return _report(
            (
                _issue(
                    "MMD_ANIM_BINDING_UNAVAILABLE",
                    "binding.runtime",
                    f"mmd-anim Python binding is unavailable: {type(exc).__name__}: {exc}",
                ),
            )
        )
    except Exception as exc:
        return _report(
            (
                _issue(
                    "MMD_ANIM_BINDING_RUNTIME_FAILED",
                    "binding.runtime",
                    f"mmd-anim binding evaluation failed: {type(exc).__name__}: {exc}",
                ),
            )
        )


__all__ = ["verify_mmd_anim_binding_asset"]
