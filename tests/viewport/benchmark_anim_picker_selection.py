"""Benchmark Animator Picker replacement selection through Maya command/API paths.

The benchmark creates a temporary grid of joints in a standalone Maya scene and
compares direct ``maya.cmds.select`` calls with
``MayaCmdsAdapter.select_fast`` (Maya Python API 2.0).  Each timed call is
followed by an exact active-selection-list check; a mismatch is a correctness
failure rather than a performance result.

Run with Maya's standalone Python (``mayapy``), for example::

    mayapy tests/viewport/benchmark_anim_picker_selection.py

The result is emitted as JSON on stdout.  ``--out`` may be used to save the
same report to a file.  No preferences, autoload options, or workspace files
are read or written.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import maya.cmds as cmds
import maya.standalone
from maya.api import OpenMaya as om


ROOT = Path(__file__).resolve().parents[2]


def _parse_args() -> argparse.Namespace:
    """Parse benchmark options while keeping the default run short and useful."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--iterations",
        type=int,
        default=200,
        help="Measured replacement selections per path and case (default: 200).",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=20,
        help="Unmeasured selections per path and case (default: 20).",
    )
    parser.add_argument(
        "--grid-size",
        type=int,
        default=8,
        help="Rows and columns in the temporary joint grid (default: 8).",
    )
    parser.add_argument(
        "--rect-width",
        type=int,
        default=4,
        help="Width of rectangle-like multi-joint selections (default: 4).",
    )
    parser.add_argument(
        "--rect-height",
        type=int,
        default=3,
        help="Height of rectangle-like multi-joint selections (default: 3).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional path for a copy of the JSON report.",
    )
    return parser.parse_args()


def _active_selection_names() -> List[str]:
    """Return the active selection as canonical long DAG/dependency names."""
    selection = om.MGlobal.getActiveSelectionList()
    names: List[str] = []
    for index in range(selection.length()):
        try:
            dag_path = selection.getDagPath(index)
            if isinstance(dag_path, tuple):
                dag_path = dag_path[0]
            names.append(dag_path.fullPathName())
            continue
        except (RuntimeError, TypeError, AttributeError):
            # Non-DAG nodes are not expected in this benchmark, but keeping a
            # dependency-node fallback makes the assertion diagnostic useful.
            dependency_node = selection.getDependNode(index)
            names.append(om.MFnDependencyNode(dependency_node).name())
    return names


def _assert_active_selection(expected: Sequence[str], path_name: str, iteration: int) -> None:
    """Fail immediately when Maya's active selection differs from the request."""
    actual = _active_selection_names()
    expected_names = list(expected)
    if actual != expected_names:
        raise AssertionError(
            "active selection mismatch for %s at iteration %d: expected=%r actual=%r"
            % (path_name, iteration, expected_names, actual)
        )


def _make_joint_grid(grid_size: int) -> Tuple[str, List[List[str]]]:
    """Create a temporary, named grid of joints and return canonical paths."""
    token = "%x" % time.time_ns()
    root = cmds.createNode("transform", name="__anim_picker_bench_%s__" % token)
    grid: List[List[str]] = []
    for row in range(grid_size):
        row_nodes: List[str] = []
        for column in range(grid_size):
            joint = cmds.createNode(
                "joint",
                name="__anim_picker_joint_%s_%02d_%02d__" % (token, row, column),
                parent=root,
            )
            # Spatial placement is not needed by selection itself, but makes
            # the fixture describe the rectangle-like picker operation clearly
            # and keeps it useful for an optional interactive inspection.
            cmds.setAttr("%s.translateX" % joint, float(column))
            cmds.setAttr("%s.translateY" % joint, float(-row))
            full_name = cmds.ls(joint, long=True) or [joint]
            row_nodes.append(full_name[0])
        grid.append(row_nodes)
    return root, grid


def _rectangle_patterns(
    grid: Sequence[Sequence[str]], width: int, height: int
) -> List[List[str]]:
    """Build several deterministic rectangle-like replacement selections."""
    grid_size = len(grid)
    patterns: List[List[str]] = []
    max_row = grid_size - height
    max_column = grid_size - width
    # Four corners plus the center provide varied list lengths/order without
    # introducing random noise into repeated benchmark runs.
    anchors = [
        (0, 0),
        (0, max_column),
        (max_row, 0),
        (max_row, max_column),
        (max(0, max_row // 2), max(0, max_column // 2)),
    ]
    for anchor_row, anchor_column in anchors:
        selection = [
            grid[row][column]
            for row in range(anchor_row, anchor_row + height)
            for column in range(anchor_column, anchor_column + width)
        ]
        if selection not in patterns:
            patterns.append(selection)
    return patterns


def _distribution(samples_ns: Sequence[int]) -> Dict[str, Any]:
    """Summarize a nanosecond sample distribution without a fixed threshold."""
    ordered = sorted(samples_ns)
    return {
        "count": len(ordered),
        "min_ns": ordered[0],
        "median_ns": int(statistics.median(ordered)),
        "p95_ns": int(statistics.quantiles(ordered, n=100, method="inclusive")[94])
        if len(ordered) > 1
        else ordered[0],
        "p99_ns": int(statistics.quantiles(ordered, n=100, method="inclusive")[98])
        if len(ordered) > 1
        else ordered[0],
        "max_ns": ordered[-1],
        "mean_ns": round(statistics.mean(ordered), 2),
    }


def _measure_path(
    path_name: str,
    operation: Any,
    patterns: Sequence[Sequence[str]],
    warmup: int,
    iterations: int,
) -> Dict[str, Any]:
    """Warm up, time, and correctness-check one replacement-selection path."""
    for index in range(warmup):
        expected = patterns[index % len(patterns)]
        operation(expected)
        _assert_active_selection(expected, path_name, -(warmup - index))

    samples_ns: List[int] = []
    for index in range(iterations):
        expected = patterns[index % len(patterns)]
        start_ns = time.perf_counter_ns()
        operation(expected)
        samples_ns.append(time.perf_counter_ns() - start_ns)
        _assert_active_selection(expected, path_name, index)
    return _distribution(samples_ns)


def _run_benchmark(args: argparse.Namespace) -> Dict[str, Any]:
    """Create the fixture, measure both paths, and return a JSON-ready report."""
    if args.iterations < 1:
        raise ValueError("--iterations must be at least 1")
    if args.warmup < 0:
        raise ValueError("--warmup must not be negative")
    if args.grid_size < 2:
        raise ValueError("--grid-size must be at least 2")
    if args.rect_width < 1 or args.rect_height < 1:
        raise ValueError("rectangle dimensions must be positive")
    if args.rect_width > args.grid_size or args.rect_height > args.grid_size:
        raise ValueError("rectangle dimensions must fit inside the joint grid")

    root, grid = _make_joint_grid(args.grid_size)
    try:
        single_patterns = [[row[0]] for row in grid]
        multi_patterns = _rectangle_patterns(
            grid, args.rect_width, args.rect_height
        )
        adapter = _load_adapter()

        # Keep the operation itself as the timed region.  Active-list checks
        # and any JSON/statistics work happen outside the perf_counter window.
        cases = {
            "single": {
                "selection_size": 1,
                "cmds_select": _measure_path(
                    "cmds_select/single",
                    lambda nodes: cmds.select(nodes, replace=True),
                    single_patterns,
                    args.warmup,
                    args.iterations,
                ),
                "api2_select_fast": _measure_path(
                    "api2_select_fast/single",
                    lambda nodes: adapter.select_fast(nodes, replace=True),
                    single_patterns,
                    args.warmup,
                    args.iterations,
                ),
            },
            "multi_rectangle": {
                "selection_size": args.rect_width * args.rect_height,
                "cmds_select": _measure_path(
                    "cmds_select/multi_rectangle",
                    lambda nodes: cmds.select(nodes, replace=True),
                    multi_patterns,
                    args.warmup,
                    args.iterations,
                ),
                "api2_select_fast": _measure_path(
                    "api2_select_fast/multi_rectangle",
                    lambda nodes: adapter.select_fast(nodes, replace=True),
                    multi_patterns,
                    args.warmup,
                    args.iterations,
                ),
            },
        }
        cmds.select(clear=True)
        _assert_active_selection([], "cleanup", 0)
        return {
            "schema_version": 1,
            "maya_version": cmds.about(version=True),
            "maya_api_version": cmds.about(apiVersion=True),
            "python_version": platform.python_version(),
            "iterations": args.iterations,
            "warmup": args.warmup,
            "grid_size": args.grid_size,
            "grid_joint_count": args.grid_size * args.grid_size,
            "rectangle": {
                "width": args.rect_width,
                "height": args.rect_height,
                "pattern_count": len(multi_patterns),
            },
            "cases": cases,
            "correctness": {
                "active_selection_checked_after_every_call": True,
                "selection_mode": "replacement",
            },
        }
    finally:
        cmds.select(clear=True)
        if cmds.objExists(root):
            cmds.delete(root)


def _load_adapter() -> Any:
    """Import the project adapter after making the repository importable."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from mmd_tools.adapters.maya_cmds_adapter import MayaCmdsAdapter

    return MayaCmdsAdapter(cmds)


def main() -> int:
    """Run the standalone benchmark and print a machine-readable report."""
    args = _parse_args()
    initialized_here = False
    try:
        try:
            maya.standalone.initialize(name="python")
            initialized_here = True
        except RuntimeError:
            # Already initialized is harmless; do not uninitialize a host Maya.
            pass

        if not cmds.about(batch=True):
            raise RuntimeError(
                "This benchmark must run under Maya standalone mayapy; refusing to alter an interactive scene."
            )

        cmds.file(new=True, force=True)
        report = _run_benchmark(args)
        encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        print(encoded)
        if args.out is not None:
            output_path = args.out if args.out.is_absolute() else ROOT / args.out
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(encoded + "\n", encoding="utf-8")
    except Exception as error:
        print("Animator Picker selection benchmark failed: %s" % error, file=sys.stderr)
        return 1
    finally:
        if initialized_here:
            try:
                maya.standalone.uninitialize()
            except RuntimeError:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
