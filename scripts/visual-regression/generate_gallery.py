"""Generate a local HTML gallery for Maya visual-regression runs.

The capture and comparison scripts deliberately keep their JSON and PNG files
inside ``build/visual-regression``.  This report-only helper discovers those
files without requiring a manifest, joins capture/comparison results by
fixture name, and writes an HTML file whose image links are relative to the
HTML file.  Missing or malformed files are represented in the page instead of
making gallery generation fail.

Typical usage::

    python scripts/visual-regression/generate_gallery.py
    python scripts/visual-regression/generate_gallery.py --root build/visual-regression \
        --out build/visual-regression/gallery.html
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional
from urllib.parse import quote, unquote, urlparse


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
REPORT_NAME = "visual-regression-report.json"
COMPARISON_NAME = "visual-regression-comparison.json"
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_MAYA_VERSION = re.compile(r"(?:maya|Maya)[-_ ]?(20(?:2[0-9]|3[0-9]))")
_BACKEND = re.compile(r"(?:maya[-_])?(dx11|glsl|opengl|glcore)", re.IGNORECASE)


def _read_json(path: Path) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Read a JSON object, returning a human-readable error on failure."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if not isinstance(value, dict):
        return None, "JSON root is not an object"
    return value, None


def _coerce_path(raw: Any, base: Path) -> Optional[Path]:
    """Resolve a JSON path relative to ``base`` while accepting file URIs."""

    if not isinstance(raw, (str, os.PathLike)):
        return None
    text = os.fspath(raw).strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme.lower() == "file":
        # ``Path.as_uri`` emits file:///C:/... on Windows.  urlparse leaves
        # the drive in ``path`` and unquote restores spaces and CJK names.
        text = unquote(parsed.path)
        if parsed.netloc and parsed.netloc.lower() != "localhost":
            text = f"//{parsed.netloc}{text}"
        if re.match(r"^/[A-Za-z]:", text):
            text = text[1:]
    path = Path(text)
    if not path.is_absolute() and not _WINDOWS_DRIVE.match(text):
        path = base / path
    return path


def _asset_url(source: Optional[Path], html_path: Path) -> str:
    """Return a browser-safe relative URL (or a cross-drive file URI)."""

    if source is None:
        return ""
    try:
        relative = os.path.relpath(str(source), str(html_path.parent))
    except (ValueError, OSError):
        # ``os.path.relpath`` raises for different Windows drives.  A file URI
        # remains usable from a local file:// page in that case.
        try:
            return source.resolve().as_uri()
        except (OSError, ValueError):
            return ""
    # relpath uses '\\' on Windows; browsers require '/'.  quote protects
    # spaces, '#', '?' and non-ASCII characters without escaping path slashes.
    return quote(relative.replace("\\", "/"), safe="/-._~:@")


def _existing_file(path: Optional[Path]) -> Optional[Path]:
    """Return ``path`` only when it points to an existing regular file."""

    return path if path is not None and path.is_file() else None


def _value(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _result_name(item: Mapping[str, Any]) -> Any:
    """Return the common fixture-name spellings used by report revisions."""

    return _value(item, "name", "fixture", "case", "case_name")


def _backend(report: Mapping[str, Any], run_name: str) -> str:
    raw = _value(report, "shader_backend", "shaderBackend", "backend", "shader-backend")
    if raw:
        return str(raw)
    for source in (str(report.get("kind", "")), run_name):
        match = _BACKEND.search(source)
        if match:
            return match.group(1).lower()
    return "unknown"


def _maya_version(report: Mapping[str, Any], run_name: str) -> str:
    raw = _value(report, "maya_version", "mayaVersion", "maya", "version")
    if raw is not None and str(raw) not in {"", "0"}:
        return str(raw)
    # Keep this conservative: schemaVersion and arbitrary frame versions are
    # not Maya versions, while the launcher convention is maya-2024/2026.
    for source in (str(report.get("kind", "")), run_name, str(report.get("output_dir", ""))):
        match = _MAYA_VERSION.search(source)
        if match:
            return match.group(1)
    return "unknown"


def _status(value: Any) -> str:
    if isinstance(value, bool):
        return "pass" if value else "fail"
    text = str(value or "").lower()
    if text in {"pass", "passed", "ok", "success", "true"}:
        return "pass"
    if text in {"fail", "failed", "error", "false"}:
        return "fail"
    return "unknown"


def _path_from_result(item: Mapping[str, Any], keys: Iterable[str], report_dir: Path) -> Optional[Path]:
    for key in keys:
        raw = item.get(key)
        if isinstance(raw, Mapping):
            raw = _value(raw, "path", "file", "png", "image", "filename")
        path = _coerce_path(raw, report_dir)
        if path is not None:
            return path
    return None


def _image_candidates(run_dir: Path) -> list[Path]:
    """Find fixture images, excluding shader and obvious log directories."""

    images: list[Path] = []
    for path in run_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        if any(part.lower() in {"shaders", "logs", "__pycache__"} for part in path.relative_to(run_dir).parts):
            continue
        images.append(path)
    return sorted(images, key=lambda path: path.as_posix().lower())


def _fixture_from_path(path: Path, run_dir: Path) -> Optional[str]:
    try:
        relative = path.relative_to(run_dir)
    except ValueError:
        return None
    if len(relative.parts) < 2:
        return None
    return relative.parts[0]


def _discover_run_dirs(root: Path) -> list[Path]:
    paths: set[Path] = set()
    for filename in (REPORT_NAME, COMPARISON_NAME):
        paths.update(path.parent for path in root.rglob(filename) if path.is_file())
    return sorted(paths, key=lambda path: path.as_posix().lower())


def _load_run(run_dir: Path, root: Path, html_path: Path) -> dict[str, Any]:
    report_path = run_dir / REPORT_NAME
    comparison_path = run_dir / COMPARISON_NAME
    report, report_error = _read_json(report_path) if report_path.is_file() else (None, "missing report")
    comparison, comparison_error = (
        _read_json(comparison_path) if comparison_path.is_file() else (None, "missing comparison")
    )
    report = report or {}
    comparison = comparison or {}
    run_name = run_dir.name
    report_results = report.get("results", [])
    comparison_results = comparison.get("results", [])
    if not isinstance(report_results, list):
        report_results = []
    if not isinstance(comparison_results, list):
        comparison_results = []

    captures = {
        str(_result_name(item)): item
        for item in report_results
        if isinstance(item, Mapping) and _result_name(item)
    }
    comparisons = {
        str(_result_name(item)): item
        for item in comparison_results
        if isinstance(item, Mapping) and _result_name(item)
    }
    fixture_names = set(captures) | set(comparisons)
    image_paths = _image_candidates(run_dir)
    # A report may contain a result with a name but no image; image-only fixture
    # folders are added as well so partially captured runs remain useful.
    for path in image_paths:
        image_fixture = _fixture_from_path(path, run_dir)
        if image_fixture:
            fixture_names.add(image_fixture)

    fixtures: list[dict[str, Any]] = []
    for name in sorted(fixture_names, key=str.casefold):
        capture = captures.get(name, {})
        compare = comparisons.get(name, {})
        fixture_dir = run_dir / name
        fixture_images = []
        if fixture_dir.is_dir():
            fixture_images = [
                path
                for path in fixture_dir.rglob("*")
                if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
            ]

        def image_with_tokens(tokens: tuple[str, ...]) -> Optional[Path]:
            matches = [path for path in fixture_images if any(token in path.stem.lower() for token in tokens)]
            return sorted(matches)[0] if matches else None

        reference = _path_from_result(compare, ("reference", "oracle", "oracle_png", "reference_png"), run_dir)
        if reference is None:
            reference = _path_from_result(capture, ("oracle_png", "reference", "reference_png"), run_dir)
        if reference is None:
            reference = image_with_tokens(("reference", "oracle", "ref"))
        actual = _path_from_result(compare, ("actual", "actual_png", "capture", "capture_png"), run_dir)
        if actual is None:
            actual = _path_from_result(capture, ("actual_png", "actual", "capture_png"), run_dir)
        if actual is None:
            actual = image_with_tokens(("actual", "capture", "got"))
        diff = _path_from_result(compare, ("diff_png", "diff_image", "difference_png", "diff", "difference"), run_dir)
        if diff is None:
            diff = _path_from_result(capture, ("diff_png", "diff_image", "difference_png", "diff", "difference"), run_dir)
        if diff is None:
            diff = image_with_tokens(("diff", "difference"))
        reference = _existing_file(reference)
        actual = _existing_file(actual)
        diff = _existing_file(diff)
        metrics = compare.get("metrics", {}) if isinstance(compare.get("metrics"), Mapping) else {}
        nmae = _value(metrics, "normalized_mean_absolute_error", "normalizedMeanAbsoluteError", "nmae", "mae")
        if nmae is None:
            nmae = _value(compare, "normalized_mean_absolute_error", "normalizedMeanAbsoluteError", "nmae")
        chroma = _value(metrics, "chroma_ratio", "chromaRatio", "actual_chroma_ratio")
        if chroma is None:
            chroma = _value(compare, "chroma_ratio", "chromaRatio")
        status = _status(_value(compare, "status", "result"))
        if status == "unknown":
            status = _status(_value(capture, "status", "ok"))
        if status == "unknown" and isinstance(compare.get("failures"), list):
            status = "fail" if compare["failures"] else "pass"
        fixtures.append(
            {
                "name": name,
                "status": status,
                "nmae": nmae,
                "chroma_ratio": chroma,
                "reference": _asset_url(reference, html_path),
                "actual": _asset_url(actual, html_path),
                "diff": _asset_url(diff, html_path),
                "missing": [label for label, path in (("reference", reference), ("actual", actual)) if path is None],
                "failures": compare.get("failures", []) if isinstance(compare.get("failures"), list) else [],
            }
        )

    run_status = _status(_value(comparison, "status", "result"))
    if run_status == "unknown":
        run_status = _status(_value(report, "status", "result", "ok"))
    if run_status == "unknown":
        errors = report.get("errors", [])
        run_status = "fail" if errors or any(item["status"] == "fail" for item in fixtures) else ("pass" if fixtures and all(item["status"] == "pass" for item in fixtures) else "unknown")
    if report_error not in (None, "missing report") and run_status == "pass":
        run_status = "unknown"
    if comparison_error not in (None, "missing comparison") and run_status == "pass":
        run_status = "unknown"
    try:
        run_path = run_dir.relative_to(root).as_posix()
    except ValueError:
        run_path = run_dir.name
    return {
        "name": run_name,
        "path": run_path,
        "status": run_status,
        "backend": _backend(report or comparison, run_name),
        "maya_version": _maya_version(report or comparison, run_name),
        "fixtures": fixtures,
        "errors": [error for error in (report_error, comparison_error) if error not in (None, "missing report", "missing comparison")],
        "missing_reports": [label for label, path in ((REPORT_NAME, report_path), (COMPARISON_NAME, comparison_path)) if not path.is_file()],
    }


def _metric(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.6f}"
    return html.escape(str(value))


def _image_figure(label: str, url: str, fixture_name: str) -> str:
    if not url:
        return f'<figure class="missing"><div class="placeholder">missing</div><figcaption>{html.escape(label)}</figcaption></figure>'
    return (
        f'<figure><a href="{html.escape(url, quote=True)}"><img loading="lazy" src="{html.escape(url, quote=True)}" '
        f'alt="{html.escape(fixture_name + " " + label, quote=True)}"></a><figcaption>{html.escape(label)}</figcaption></figure>'
    )


def render_gallery(runs: list[dict[str, Any]], root: Path) -> str:
    """Render gallery HTML.  Kept separate from I/O for focused unit tests."""

    cards: list[str] = []
    for run in runs:
        fixture_cards = []
        for fixture in run["fixtures"]:
            failures = " ".join(str(item) for item in fixture["failures"])
            fixture_cards.append(
                '<article class="fixture">'
                f'<h3>{html.escape(fixture["name"])}</h3>'
                f'<span class="status {html.escape(fixture["status"])}">{html.escape(fixture["status"])}</span>'
                '<table><tbody>'
                f'<tr><th>NMAE</th><td>{_metric(fixture["nmae"])}</td><th>Chroma ratio</th><td>{_metric(fixture["chroma_ratio"])}</td></tr>'
                f'<tr><th>Failures</th><td colspan="3">{html.escape(failures) if failures else "—"}</td></tr>'
                '</tbody></table><div class="images">'
                + _image_figure("reference", fixture["reference"], fixture["name"])
                + _image_figure("actual", fixture["actual"], fixture["name"])
                + _image_figure("diff", fixture["diff"], fixture["name"])
                + '</div></article>'
            )
        if not fixture_cards:
            fixture_cards.append('<p class="muted">No fixture results or images found.</p>')
        errors = " ".join(run["errors"])
        missing = ", ".join(run["missing_reports"])
        notes = "".join(
            part
            for part in (
                f'<p class="warning">{html.escape(errors)}</p>' if errors else "",
                f'<p class="warning">Missing: {html.escape(missing)}</p>' if missing else "",
            )
        )
        cards.append(
            '<section class="run">'
            f'<header><h2>{html.escape(run["name"])}</h2><span class="status {html.escape(run["status"])}">{html.escape(run["status"])}</span>'
            f'<p class="meta">path: {html.escape(run["path"])} · backend: {html.escape(run["backend"])} · Maya: {html.escape(run["maya_version"])}</p></header>'
            f'{notes}<div class="fixtures">{"".join(fixture_cards)}</div></section>'
        )
    body = "".join(cards) or '<p class="muted">No visual-regression reports found.</p>'
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Visual Regression Gallery</title>
<style>
:root {{ color-scheme: dark; font-family: system-ui, sans-serif; background:#16181d; color:#e8eaed; }}
body {{ margin: 2rem auto; max-width: 1600px; padding: 0 1rem; }}
h1 {{ margin-bottom:.25rem; }} .muted {{ color:#9aa0a6; }}
.run {{ background:#22252b; border:1px solid #3b414b; border-radius:10px; padding:1rem; margin:1rem 0 1.5rem; }}
.run header {{ display:flex; flex-wrap:wrap; align-items:baseline; gap:.75rem; }} h2,h3 {{ margin:.2rem 0 .6rem; }}
.meta {{ flex-basis:100%; color:#aeb4bd; margin:.1rem 0 .6rem; font-size:.9rem; }}
.fixtures {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(360px,1fr)); gap:1rem; }}
.fixture {{ border:1px solid #414751; border-radius:8px; padding:.7rem; position:relative; }}
.status {{ border-radius:999px; padding:.15rem .55rem; font-size:.78rem; text-transform:uppercase; letter-spacing:.04em; }}
.pass {{ background:#164c2c; color:#b5f5c8; }} .fail {{ background:#662323; color:#ffd1d1; }} .unknown {{ background:#514114; color:#ffe9a3; }}
table {{ border-collapse:collapse; width:100%; font-size:.86rem; margin:.45rem 0 .7rem; }} th,td {{ border:1px solid #3b414b; padding:.25rem .4rem; text-align:left; }} th {{ color:#aeb4bd; }}
.images {{ display:grid; grid-template-columns:repeat(3,1fr); gap:.45rem; }} figure {{ margin:0; }} figure img, .placeholder {{ display:block; width:100%; aspect-ratio:1; object-fit:contain; background:#101216; border:1px solid #3b414b; }}
.placeholder {{ display:grid; place-items:center; color:#747b86; font-size:.8rem; }} figcaption {{ color:#aeb4bd; font-size:.75rem; padding-top:.2rem; text-align:center; }}
.warning {{ color:#ffd18a; font-size:.85rem; white-space:pre-wrap; }}
</style></head><body><h1>Visual Regression Gallery</h1>
<p class="muted">Root: {html.escape(root.as_posix())} · generated from local JSON reports</p>{body}
</body></html>'''


def generate_gallery(root: Path, output: Path) -> tuple[int, int]:
    """Generate ``output`` and return ``(run_count, fixture_count)``."""

    root = root.expanduser()
    output = output.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    runs = [_load_run(path, root, output) for path in _discover_run_dirs(root)] if root.is_dir() else []
    output.write_text(render_gallery(runs, root), encoding="utf-8")
    return len(runs), sum(len(run["fixtures"]) for run in runs)


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="build/visual-regression", help="Directory containing visual-regression run folders.")
    parser.add_argument("--out", "--output", dest="output", default="build/visual-regression/visual-regression-gallery.html")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    root = Path(args.root)
    output = Path(args.output)
    if not root.is_absolute():
        root = Path.cwd() / root
    if not output.is_absolute():
        output = Path.cwd() / output
    run_count, fixture_count = generate_gallery(root.resolve(), output.resolve())
    print(f"Visual regression gallery: {output.resolve()} ({run_count} runs, {fixture_count} fixtures)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
