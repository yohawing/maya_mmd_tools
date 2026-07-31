"""Pure contracts for the real-asset Control Rig bake parity harness."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.viewport import real_asset_bake_rig_parity as parity
from tests.viewport.real_asset_bake_rig_parity import (
    PAIR_COUNT,
    REPORT_KIND,
    _aggregate_bone_coverage,
    _validate_pair_rows,
    load_pair_manifest,
    validate_child_report,
)


def _assets(tmp_path, index: int):
    pmx = tmp_path / f"model{index}.pmx"
    vmd = tmp_path / f"motion{index}.vmd"
    pmx.write_bytes(b"pmx")
    vmd.write_bytes(b"vmd")
    return pmx, vmd


def test_manifest_requires_exactly_five_unique_pairs(tmp_path):
    rows = []
    for index in range(PAIR_COUNT):
        pmx, vmd = _assets(tmp_path, index)
        rows.append({"name": f"pair-{index}", "pmx": str(pmx), "vmd": str(vmd)})
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"pairs": rows}), encoding="utf-8")
    result = load_pair_manifest(manifest)
    assert len(result) == PAIR_COUNT
    assert len({(item["pmx"], item["vmd"]) for item in result}) == PAIR_COUNT


def test_manifest_rejects_duplicate_pair(tmp_path):
    pmx, vmd = _assets(tmp_path, 0)
    rows = [{"name": f"pair-{index}", "pmx": str(pmx), "vmd": str(vmd)} for index in range(PAIR_COUNT)]
    with pytest.raises(ValueError, match="duplicate PMX/VMD pair"):
        _validate_pair_rows(rows)


def test_discovery_requires_manifest_when_exact_stems_are_insufficient(tmp_path):
    pmx = tmp_path / "models" / "sample.pmx"
    vmd = tmp_path / "motions" / "sample.vmd"
    pmx.parent.mkdir()
    vmd.parent.mkdir()
    pmx.write_bytes(b"pmx")
    vmd.write_bytes(b"vmd")

    with pytest.raises(ValueError, match="provide --manifest"):
        parity.discover_asset_pairs(tmp_path, count=2)


def test_child_validation_rejects_changed_asset_provenance(tmp_path):
    pmx, vmd = _assets(tmp_path, 0)
    pair = {"name": "sample", "pmx": str(pmx), "vmd": str(vmd)}
    payload = {
        "kind": REPORT_KIND,
        "status": "pass",
        "mayaVersion": "2024",
        "pmx": pair["pmx"],
        "vmd": pair["vmd"],
        "frames": [0, 1],
        "errors": [],
        "provenance": parity._expected_provenance(pair, "2024"),
        "boneCoverage": {
            "compared": 1,
            "categories": {category: True for category in parity.BONE_CATEGORIES},
            "pass": True,
        },
        **{
            gate: {"pass": True}
            for gate in (
                "preImportedVmd",
                "setupBoundary",
                "controlRigBake",
                "bakeBack",
                "curveIdentity",
                "persistence",
                "exportFreshImport",
            )
        },
    }
    pmx.write_bytes(b"changed-pmx")

    errors = validate_child_report(payload, pair=pair, version="2024")
    assert any("provenance mismatch" in error for error in errors)


def test_child_validation_rejects_changed_python_source_tree_provenance(tmp_path, monkeypatch):
    pmx, vmd = _assets(tmp_path, 0)
    source = tmp_path / "representative.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        parity,
        "_python_source_tree_files",
        lambda _root: [("core/representative.py", source)],
    )
    pair = {"name": "sample", "pmx": str(pmx), "vmd": str(vmd)}
    payload = {
        "kind": REPORT_KIND,
        "status": "pass",
        "mayaVersion": "2024",
        "pmx": pair["pmx"],
        "vmd": pair["vmd"],
        "pairName": pair["name"],
        "frames": [0, 1],
        "errors": [],
        "provenance": parity._expected_provenance(pair, "2024"),
        "boneCoverage": {
            "compared": 1,
            "categories": {category: True for category in parity.BONE_CATEGORIES},
            "pass": True,
        },
        **{
            gate: {"pass": True}
            for gate in (
                "preImportedVmd",
                "setupBoundary",
                "controlRigBake",
                "bakeBack",
                "curveIdentity",
                "persistence",
                "exportFreshImport",
            )
        },
    }
    source.write_text("VALUE = 2\n", encoding="utf-8")

    errors = validate_child_report(payload, pair=pair, version="2024")
    assert any("provenance mismatch" in error for error in errors)


def test_child_validation_is_fail_closed_for_missing_gate():
    pair = {"name": "sample", "pmx": "F:/MMD/sample.pmx", "vmd": "F:/MMD/sample.vmd"}
    payload = {
        "kind": REPORT_KIND,
        "status": "pass",
        "mayaVersion": "2026",
        "pmx": pair["pmx"],
        "vmd": pair["vmd"],
        "frames": [0, 12],
        "boneCoverage": {"compared": 2},
        "preImportedVmd": {"pass": True},
        "setupBoundary": {"pass": True},
        "controlRigBake": {"pass": True},
        "bakeBack": {"pass": True},
        "curveIdentity": {"pass": True},
        "persistence": {"pass": True},
        "exportFreshImport": {"pass": False},
    }
    errors = validate_child_report(payload, pair=pair, version="2026")
    assert any("exportFreshImport" in error for error in errors)


def test_host_passes_unicode_pair_only_via_utf8_config(tmp_path, monkeypatch):
    rows = []
    for index in range(PAIR_COUNT):
        pmx = tmp_path / f"モデル{index}.pmx"
        vmd = tmp_path / f"モーション{index}.vmd"
        pmx.write_bytes(b"pmx")
        vmd.write_bytes(b"vmd")
        rows.append({"name": f"日本語-{index}", "pmx": str(pmx), "vmd": str(vmd)})
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"pairs": rows}, ensure_ascii=False), encoding="utf-8")
    mayapy = tmp_path / "mayapy.exe"
    mayapy.write_bytes(b"")
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        assert all(str(item).isascii() for item in command)
        config = parity._load_child_pair_config(command[command.index("--pair-config") + 1])
        output = command[command.index("--out") + 1]
        assert config["name"].startswith("日本語-")
        assert "モデル" in config["pmx"] and "モーション" in config["vmd"]
        Path(output).write_text(
            json.dumps({
                "kind": REPORT_KIND, "status": "pass", "mayaVersion": command[command.index("--maya") + 1],
                "pmx": config["pmx"], "vmd": config["vmd"], "pairName": config["name"], "frames": [0, 1], "errors": [],
                "provenance": parity._expected_provenance(
                    config,
                    command[command.index("--maya") + 1],
                ),
                "boneCoverage": {
                    "compared": 1,
                    "categories": {
                        category: True
                        for category in parity.BONE_CATEGORIES
                    },
                    "pass": True,
                },
                **{gate: {"pass": True} for gate in ("preImportedVmd", "setupBoundary", "controlRigBake", "bakeBack", "curveIdentity", "persistence", "exportFreshImport")},
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(parity, "_mayapy", lambda _version: mayapy)
    monkeypatch.setattr(parity.subprocess, "run", fake_run)
    result = parity._run_host(Namespace(out=str(tmp_path / "aggregate.json"), versions="2024,2026", manifest=str(manifest), asset_root=""))
    assert result == 0
    assert len(commands) == PAIR_COUNT * 2

    commands.clear()
    result = parity._run_host(
        Namespace(
            out=str(tmp_path / "aggregate.json"),
            versions="2024,2026",
            manifest=str(manifest),
            asset_root="",
            resume=True,
        )
    )
    assert result == 0
    assert commands == []


def test_matrix_coverage_is_union_across_assets_and_versions():
    payloads = []
    categories = list(parity.BONE_CATEGORIES)
    for index in range(PAIR_COUNT * 2):
        payloads.append(
            {
                "boneCoverage": {
                    "compared": 10,
                    "categories": {
                        category: category == categories[index % len(categories)]
                        for category in categories
                    },
                }
            }
        )

    coverage = _aggregate_bone_coverage(payloads)

    assert coverage["pass"] is True
    assert all(coverage["categories"].values())


def test_single_asset_coverage_does_not_require_absent_twist():
    pair = {"name": "sample", "pmx": "F:/MMD/sample.pmx", "vmd": "F:/MMD/sample.vmd"}
    payload = {
        "kind": REPORT_KIND,
        "status": "pass",
        "mayaVersion": "2024",
        "pmx": pair["pmx"],
        "vmd": pair["vmd"],
        "pairName": pair["name"],
        "frames": [0, 1],
        "errors": [],
        "provenance": parity._expected_provenance(pair, "2024"),
        "boneCoverage": {
            "compared": 20,
            "categories": {category: category != "twist" for category in parity.BONE_CATEGORIES},
            "pass": True,
        },
        **{
            gate: {"pass": True}
            for gate in (
                "preImportedVmd",
                "setupBoundary",
                "controlRigBake",
                "bakeBack",
                "curveIdentity",
                "persistence",
                "exportFreshImport",
            )
        },
    }

    assert validate_child_report(payload, pair=pair, version="2024") == []
