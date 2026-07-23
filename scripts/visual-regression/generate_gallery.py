"""Generate a compact local HTML gallery for visual-regression captures.

Each fixture is represented by its newest JSON-backed capture whose actual
PNG still exists.  Reports are intentionally treated as append-only run
artifacts: old attempts are not useful in the default gallery, and comparison
metrics/metadata are kept in JSON rather than repeated in this visual index.
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


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    """Read a JSON object, returning ``None`` for missing/broken artifacts."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _coerce_path(raw: Any, base: Path) -> Optional[Path]:
    """Resolve a report path relative to its run, accepting file URIs."""

    if not isinstance(raw, (str, os.PathLike)):
        return None
    text = os.fspath(raw).strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme.lower() == "file":
        text = unquote(parsed.path)
        if parsed.netloc and parsed.netloc.lower() != "localhost":
            text = f"//{parsed.netloc}{text}"
        if re.match(r"^/[A-Za-z]:", text):
            text = text[1:]
    path = Path(text)
    if not path.is_absolute() and not _WINDOWS_DRIVE.match(text):
        path = base / path
    return path


def _asset_url(source: Path, html_path: Path) -> str:
    """Return a browser-safe relative URL, or a cross-drive file URI."""

    try:
        relative = os.path.relpath(str(source), str(html_path.parent))
    except (ValueError, OSError):
        try:
            return source.resolve().as_uri()
        except (OSError, ValueError):
            return ""
    return quote(relative.replace("\\", "/"), safe="/-._~:@")


def _value(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _result_name(item: Mapping[str, Any]) -> Optional[str]:
    value = _value(item, "name", "fixture", "case", "case_name")
    return str(value) if value not in (None, "") else None


def _path_from_result(item: Mapping[str, Any], keys: Iterable[str], run_dir: Path) -> Optional[Path]:
    for key in keys:
        raw = item.get(key)
        if isinstance(raw, Mapping):
            raw = _value(raw, "path", "file", "png", "image", "filename")
        path = _coerce_path(raw, run_dir)
        if path is not None:
            return path
    return None


def _discover_run_dirs(root: Path) -> list[Path]:
    """Return run directories containing either supported JSON artifact."""

    paths: set[Path] = set()
    for filename in (REPORT_NAME, COMPARISON_NAME):
        paths.update(path.parent for path in root.rglob(filename) if path.is_file())
    return sorted(paths, key=lambda path: path.as_posix().casefold())


def _latest_actuals(root: Path) -> list[dict[str, Any]]:
    """Select the newest existing actual image for every fixture name.

    JSON mtime is the ordering authority.  A malformed JSON, missing result,
    or result whose actual image has been removed is not a valid candidate and
    therefore cannot displace an older valid capture.
    """

    selected: dict[str, tuple[tuple[int, int, int], dict[str, Any]]] = {}
    for run_dir in _discover_run_dirs(root):
        for filename, source_priority, actual_keys in (
            (REPORT_NAME, 0, ("actual_png", "actual", "capture_png", "capture")),
            (COMPARISON_NAME, 1, ("actual", "actual_png", "capture_png", "capture")),
        ):
            path = run_dir / filename
            if not path.is_file():
                continue
            document = _read_json(path)
            results = document.get("results", []) if document else []
            if not isinstance(results, list):
                continue
            try:
                mtime_ns = path.stat().st_mtime_ns
            except OSError:
                continue
            for index, item in enumerate(results):
                if not isinstance(item, Mapping):
                    continue
                name = _result_name(item)
                actual = _path_from_result(item, actual_keys, run_dir)
                if not name or actual is None or not actual.is_file():
                    continue
                key = (mtime_ns, source_priority, index)
                if name not in selected or key > selected[name][0]:
                    selected[name] = (
                        key,
                        {
                            "name": name,
                            "actual": actual,
                            "source": path,
                        },
                    )
    return [selected[name][1] for name in sorted(selected, key=str.casefold)]


def render_gallery(fixtures: list[dict[str, Any]], root: Path | None = None) -> str:
    """Render case names and clickable actual images only."""

    cards = []
    for fixture in fixtures:
        name = html.escape(str(fixture["name"]))
        url = html.escape(str(fixture["url"]), quote=True)
        cards.append(
            '<article class="case">'
            f"<h2>{name}</h2>"
            f'<a href="{url}"><img loading="lazy" src="{url}" alt="{name}"></a>'
            "</article>"
        )
    body = "".join(cards)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Visual Regression Gallery</title>
<style>
:root {{ color-scheme: dark; font-family: system-ui, sans-serif; background:#16181d; color:#e8eaed; }}
body {{ margin: 1.5rem auto; max-width: 1600px; padding: 0 1rem; }}
.cases {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:1rem; }}
.case {{ border:1px solid #414751; border-radius:8px; padding:.7rem; background:#22252b; }}
h2 {{ font-size:1rem; margin:.2rem 0 .6rem; overflow-wrap:anywhere; }}
.case img {{ display:block; width:100%; aspect-ratio:1; object-fit:contain; background:#101216; border:1px solid #3b414b; }}
</style></head><body><div class="cases">{body}</div></body></html>"""


def generate_gallery(root: Path, output: Path) -> tuple[int, int]:
    """Write a gallery and return ``(selected_fixture_count, image_count)``."""

    root = root.expanduser()
    output = output.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    candidates = _latest_actuals(root) if root.is_dir() else []
    for candidate in candidates:
        candidate["url"] = _asset_url(candidate["actual"], output)
    output.write_text(render_gallery(candidates, root), encoding="utf-8")
    return len(candidates), len(candidates)


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="build/visual-regression", help="Directory containing visual-regression run folders.")
    parser.add_argument(
        "--out",
        "--output",
        dest="output",
        default="build/visual-regression/visual-regression-gallery.html",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    root = Path(args.root)
    output = Path(args.output)
    if not root.is_absolute():
        root = Path.cwd() / root
    if not output.is_absolute():
        output = Path.cwd() / output
    count, _ = generate_gallery(root.resolve(), output.resolve())
    print(f"Visual regression gallery: {output.resolve()} ({count} fixtures)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
