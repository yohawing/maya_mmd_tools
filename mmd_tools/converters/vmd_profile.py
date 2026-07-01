"""Opt-in profiling counters for VMD import performance triage."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterator


_PROFILE_ENV = "MMD_TOOLS_VMD_PROFILE_JSONL"
_ALLOW_SETKEYFRAME_FALLBACK_ENV = "MMD_TOOLS_VMD_ALLOW_SETKEYFRAME_FALLBACK"
_times: dict[str, float] = defaultdict(float)
_counts: dict[str, int] = defaultdict(int)
_extras: dict[str, float | int | str] = {}


def enabled() -> bool:
    """Return True when VMD profiling output is requested."""
    return bool(os.environ.get(_PROFILE_ENV))


def allow_setkeyframe_fallback() -> bool:
    """Return True when slow cmds.setKeyframe fallback is explicitly allowed."""
    return os.environ.get(_ALLOW_SETKEYFRAME_FALLBACK_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


@contextmanager
def scope(name: str, *, count: int = 1) -> Iterator[None]:
    """Accumulate elapsed seconds for a named profiling phase."""
    if not enabled():
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        _times[name] += time.perf_counter() - start
        _counts[name] += count


def add_count(name: str, amount: int = 1) -> None:
    """Accumulate a named count."""
    if enabled():
        _counts[name] += int(amount)


def set_extra(name: str, value: float | int | str) -> None:
    """Set a summary metadata value."""
    if enabled():
        _extras[name] = value


def flush(label: str) -> None:
    """Append the current profiling summary and reset counters."""
    output = os.environ.get(_PROFILE_ENV)
    if not output:
        return
    payload = {
        "label": label,
        "type": "vmd_profile_summary",
        "times": {key: round(value, 6) for key, value in sorted(_times.items())},
        "counts": dict(sorted(_counts.items())),
        "extras": dict(sorted(_extras.items())),
    }
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    _times.clear()
    _counts.clear()
    _extras.clear()
