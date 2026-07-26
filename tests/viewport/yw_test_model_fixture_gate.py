"""Maya PMX fixture gate for the checked-in ``yw_test_model`` asset.

The gate verifies the manifest and parser census before exercising the
production importer.  It characterizes the imported root through the
production HumanIK frontend, then saves a Maya ASCII scene, reopens it in the
same mayapy process, and checks the representative MMD rig/physics/HumanIK
contracts again.  The script is intentionally deterministic and never
regenerates the fixture or its textures.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

import maya.cmds as cmds
import maya.standalone


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="tests/data/yw_test_model.fixture.json")
    parser.add_argument("--out", default="build/yw-test-model-fixture/maya-report.json")
    return parser.parse_args()


class _ReportableGateError(AssertionError):
    """Assertion carrying the partial report needed to diagnose a gate failure."""

    def __init__(self, message: str, report: Mapping[str, Any]):
        super().__init__(message)
        self.report = dict(report)


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_manifest(path: Path) -> Dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("name") != "yw_test_model":
        raise ValueError(f"unexpected fixture manifest: {path}")
    return manifest


def _load_plugin() -> None:
    plugin_path = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
    plugin_name = plugin_path.stem
    if not cmds.pluginInfo(plugin_name, query=True, loaded=True):
        cmds.loadPlugin(str(plugin_path), quiet=True)
    if not cmds.pluginInfo(plugin_name, query=True, loaded=True):
        raise RuntimeError(f"mmd_tools plugin did not load: {plugin_path}")


def _leaf_name(node: str) -> str:
    return str(node).rsplit("|", 1)[-1].rsplit(":", 1)[-1]


def _nodes_with_attribute(attribute: str) -> Iterable[str]:
    for node in cmds.ls(long=True) or []:
        if cmds.attributeQuery(attribute, node=node, exists=True):
            yield str(node)


def _count_node_type(node_type: str) -> int:
    return len(cmds.ls(type=node_type, long=True) or [])


def _find_model_root() -> str:
    roots = list(_nodes_with_attribute("mmd_model_name"))
    if len(roots) != 1:
        raise RuntimeError(f"expected one imported model root, found {len(roots)}: {roots}")
    return roots[0]


def _census(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    expected = manifest["import"]["node_types"]
    result: Dict[str, Any] = {
        "node_types": {},
        "indexed_bones": len(list(_nodes_with_attribute("mmd_bone_index"))),
        "indexed_materials": len(list(_nodes_with_attribute("mmd_material_index"))),
        "indexed_physics_shapes": len(list(_nodes_with_attribute("pmxIndex"))),
    }
    for node_type in expected:
        if node_type == "mmd_material_nodes":
            result["node_types"][node_type] = len(list(_nodes_with_attribute("mmd_material_index")))
        else:
            result["node_types"][node_type] = _count_node_type(node_type)
    return result


def _assert_census(census: Mapping[str, Any], expected: Mapping[str, Any], label: str) -> None:
    if dict(census["node_types"]) != dict(expected["node_types"]):
        raise AssertionError(f"{label} node census mismatch: {census['node_types']} != {expected['node_types']}")
    for key in ("indexed_bones", "indexed_materials", "indexed_physics_shapes"):
        expected_value = expected.get("contract", {}).get(key)
        if expected_value is not None and census[key] != expected_value:
            raise AssertionError(f"{label} {key} mismatch: {census[key]} != {expected_value}")


def _assert_root_contract(root: str, manifest: Mapping[str, Any]) -> None:
    expected_names = set(manifest["import"]["required_root_children"])
    actual_names = {
        _leaf_name(child) for child in (cmds.listRelatives(root, children=True, fullPath=True) or [])
    }
    if actual_names != expected_names:
        raise AssertionError(f"root children mismatch: {sorted(actual_names)} != {sorted(expected_names)}")
    header = manifest["pmx"]["header"]
    for attribute, expected_value in (
        ("mmd_model_name", header["model_name"]),
        ("mmd_model_name_en", header["model_name_english"]),
        ("mmd_comment", header["comment"]),
        ("mmd_comment_en", header["comment_english"]),
    ):
        if not cmds.attributeQuery(attribute, node=root, exists=True):
            raise AssertionError(f"missing root metadata attribute: {attribute}")
        if cmds.getAttr(f"{root}.{attribute}") != expected_value:
            raise AssertionError(f"root metadata mismatch for {attribute}")


def _assert_connections() -> Dict[str, int]:
    descriptor_types = (
        "mmdCcdIk",
        "mmdAppend",
        "mmdRigidBodyShape",
        "mmdPhysicsJointShape",
        "mmdPhysicsSolver",
    )
    zero_connection_counts = {}
    for node_type in descriptor_types:
        nodes = cmds.ls(type=node_type, long=True) or []
        zero_connection_counts[node_type] = sum(
            not (cmds.listConnections(node, plugs=True, connections=True) or []) for node in nodes
        )
        if zero_connection_counts[node_type]:
            raise AssertionError(f"{node_type} has disconnected descriptor nodes")
    return zero_connection_counts


def _assert_texture_resolution(files: Mapping[str, str], manifest: Mapping[str, Any]) -> Dict[str, Any]:
    expected_texture = files[manifest["import"]["texture_resolution"]["referenced"][0]]
    expected_path = Path(expected_texture).resolve()
    actual_paths = []
    for node in cmds.ls(type="file", long=True) or []:
        value = cmds.getAttr(f"{node}.fileTextureName")
        if value:
            actual_paths.append(str(Path(value).resolve()))
    if str(expected_path) not in actual_paths:
        raise AssertionError(f"referenced texture did not resolve: {expected_path}; actual={actual_paths}")
    return {"referenced": [str(expected_path)], "non_ascii_reference": False}


def _assert_humanik_assignment_contract(
    assignments: Iterable[Any],
    expected: Mapping[str, Any],
    *,
    missing_mmd_bones: Iterable[str] = (),
    duplicate_assignment_count: int = 0,
) -> Dict[str, Any]:
    """Validate and summarize stable HumanIK assignment coverage facts."""
    from mmd_tools.core.humanik_frontend import is_humanik_finger_assignment

    resolved_assignments = tuple(assignments)
    resolved_slots = sorted(str(assignment.hik_bone) for assignment in resolved_assignments)
    required_slots = sorted(str(slot) for slot in expected["required_slots"])
    missing_required_slots = sorted(set(required_slots) - set(resolved_slots))
    finger_count = sum(is_humanik_finger_assignment(assignment) for assignment in resolved_assignments)
    body_count = len(resolved_assignments) - finger_count
    missing_mmd = tuple(str(name) for name in missing_mmd_bones)
    duplicate_count = int(duplicate_assignment_count)
    if len(resolved_assignments) != int(expected["assignment_count"]):
        raise AssertionError(
            "HumanIK assignment count mismatch: "
            f"{len(resolved_assignments)} != {expected['assignment_count']}"
        )
    if body_count != int(expected["body_assignment_count"]):
        raise AssertionError(
            "HumanIK body assignment count mismatch: "
            f"{body_count} != {expected['body_assignment_count']}"
        )
    if finger_count != int(expected["finger_assignment_count"]):
        raise AssertionError(
            "HumanIK finger assignment count mismatch: "
            f"{finger_count} != {expected['finger_assignment_count']}"
        )
    if missing_required_slots:
        raise AssertionError(f"HumanIK required slots are unresolved: {missing_required_slots}")
    if missing_mmd or duplicate_count:
        raise AssertionError(
            "HumanIK assignment resolver reported gaps: "
            f"missing={missing_mmd}, duplicates={duplicate_count}"
        )
    return {
        "assignmentCount": len(resolved_assignments),
        "bodyAssignmentCount": body_count,
        "fingerAssignmentCount": finger_count,
        "requiredSlots": required_slots,
        "resolvedSlots": resolved_slots,
        "missingRequiredSlots": missing_required_slots,
        "missingMmdBones": list(missing_mmd),
        "duplicateAssignmentCount": duplicate_count,
    }


def _maya_node_matches_assignment(actual_node: Any, expected_joint: str) -> bool:
    """Match Maya's short/long ``hikGetSkNode`` spelling to an expected joint."""
    if actual_node is None or not str(actual_node).strip():
        return False
    actual = str(actual_node)
    expected = str(expected_joint)
    return actual == expected or _leaf_name(actual) == _leaf_name(expected)


def _humanik_slot_connections(character: str, assignments: Iterable[Any]) -> Dict[str, Any]:
    """Compare every expected HIK slot with Maya's persisted skeleton readback."""
    from mmd_tools.core.humanik_utils import maya_mel, mel_string

    mel = maya_mel()
    mismatches = []
    matched = 0
    resolved_assignments = tuple(assignments)
    for assignment in resolved_assignments:
        expected_joint = str(assignment.joint)
        actual_node = None
        error = None
        try:
            actual_node = mel.eval(
                f"hikGetSkNode({mel_string(character)}, {int(assignment.hik_index)});"
            )
        except Exception as exc:  # pragma: no cover - Maya MEL error path
            error = f"{type(exc).__name__}: {exc}"
        if error is None and _maya_node_matches_assignment(actual_node, expected_joint):
            matched += 1
            continue
        mismatch = {
            "hikSlot": str(assignment.hik_bone),
            "hikIndex": int(assignment.hik_index),
            "expectedJoint": _leaf_name(expected_joint),
            "actualJoint": _leaf_name(actual_node) if actual_node else "",
            "reason": "readback_error" if error else "wrong_or_missing_connection",
        }
        if error:
            mismatch["error"] = error
        mismatches.append(mismatch)
    return {
        "compared": len(resolved_assignments),
        "matched": matched,
        "mismatchCount": len(mismatches),
        "mismatches": mismatches,
    }


def _characterize_humanik(root: str, manifest: Mapping[str, Any]) -> Dict[str, Any]:
    """Characterize ``root`` through the production HumanIK frontend.

    The report intentionally contains only stable HIK slot facts.  Maya's
    generated character and joint paths are useful diagnostics, but are not a
    fixture contract and therefore are not persisted in the baseline.
    """
    from mmd_tools.core.humanik_builder import get_humanik_definition_lock_state
    from mmd_tools.core.humanik_frontend import (
        HumanIkFrontendSession,
    )

    expected = manifest["import"]["humanik"]
    session = HumanIkFrontendSession()
    binding = session.setup_and_characterize(root, profile=expected["profile"])
    character = str(binding.character)
    locked = bool(get_humanik_definition_lock_state(character))
    if not locked:
        raise AssertionError(f"HumanIK character did not lock: {character}")
    if character and cmds.nodeType(character) != "HIKCharacterNode":
        raise AssertionError(f"unexpected HumanIK character node type: {character}")
    assignment_contract = _assert_humanik_assignment_contract(
        binding.assignments,
        expected,
        missing_mmd_bones=binding.result.missing_mmd_bones,
        duplicate_assignment_count=len(binding.result.duplicate_assignments),
    )
    return {
        "status": "pass",
        "characterExists": bool(cmds.objExists(character)),
        "characterType": cmds.nodeType(character),
        "locked": locked,
        "profile": str(binding.profile),
        **assignment_contract,
    }


def _humanik_reopen_contract(root: str, manifest: Mapping[str, Any]) -> Dict[str, Any]:
    """Read back the persisted HumanIK character after scene reopen."""
    from mmd_tools.core.humanik_builder import (
        get_humanik_definition_lock_state,
        resolve_scene_humanik_assignments,
    )
    from mmd_tools.core.humanik_retarget import find_humanik_character_for_model

    expected = manifest["import"]["humanik"]
    character = find_humanik_character_for_model(root)
    if not character:
        return {
            "status": "not_persisted",
            "persisted": False,
            "reason": "no HIKCharacterNode remained associated with the reopened model root",
            "slotConnections": {
                "compared": 0,
                "matched": 0,
                "mismatchCount": 1,
                "mismatches": [{"reason": "character_missing"}],
            },
        }
    locked = bool(get_humanik_definition_lock_state(character))
    result = resolve_scene_humanik_assignments(root)
    assignment_contract = _assert_humanik_assignment_contract(
        result.assignments,
        expected,
        missing_mmd_bones=result.missing_mmd_bones,
        duplicate_assignment_count=len(result.duplicate_assignments),
    )
    slot_connections = _humanik_slot_connections(character, result.assignments)
    persisted = (
        locked
        and slot_connections["mismatchCount"] == 0
    )
    return {
        "status": "pass" if persisted else "mismatch",
        "persisted": persisted,
        "characterExists": bool(cmds.objExists(character)),
        "characterType": cmds.nodeType(character),
        "locked": locked,
        **assignment_contract,
        "slotConnections": slot_connections,
    }


def _run_gate(args: argparse.Namespace) -> Dict[str, Any]:
    manifest_path = Path(args.manifest).resolve()
    manifest = _load_manifest(manifest_path)
    data_root = manifest_path.parent
    # Use the same central provider as Python tests.  This rejects absent or
    # changed assets before Maya is asked to import anything.
    from tests.common.test_fixture_provider import TestFixtureProvider

    provider = TestFixtureProvider(str(data_root))
    verified = provider.get_verified_fixture("yw_test_model")
    files = verified["files"]
    pmx_path = Path(provider.get_verified_pmx_file("yw_test_model"))

    from mmd_tools.core.mmd_parser import parse_pmx_file

    parsed = parse_pmx_file(str(pmx_path), use_native_pmx_parse=False)
    parsed_counts = {
        key: len(getattr(parsed, key))
        for key in (
            "vertices",
            "faces",
            "materials",
            "bones",
            "morphs",
            "display_frames",
            "rigid_bodies",
            "joints",
            "textures",
            "toon_textures",
        )
    }
    if parsed_counts != manifest["pmx"]["counts"]:
        raise AssertionError(f"parse census mismatch: {parsed_counts} != {manifest['pmx']['counts']}")
    parsed_header = manifest["pmx"]["header"]
    for field in ("model_name", "model_name_english", "comment", "comment_english"):
        if getattr(parsed.header, field) != parsed_header[field]:
            raise AssertionError(f"PMX header mismatch for {field}")
    license_evidence = manifest["license"]["evidence"]
    if (
        parsed.header.comment != license_evidence["comment"]
        or parsed.header.comment_english != license_evidence["comment_english"]
    ):
        raise AssertionError("PMX embedded license evidence does not match the manifest")
    texture_refs = list(parsed.textures)
    if texture_refs != manifest["pmx"]["texture_refs"]:
        raise AssertionError(f"texture refs mismatch: {texture_refs} != {manifest['pmx']['texture_refs']}")
    ik_bones = [bone for bone in parsed.bones if bone.ik_target_bone_index >= 0]
    ik_links = sum(len(bone.ik_links) for bone in ik_bones)
    grant_bones = [bone for bone in parsed.bones if bone.grant_parent_bone_index >= 0]
    rig_contract = manifest["pmx"]["rig_contract"]
    if (len(ik_bones), ik_links, len(grant_bones)) != (
        rig_contract["ik_bones"],
        rig_contract["ik_links"],
        rig_contract["grant_bones"],
    ):
        raise AssertionError("parse rig contract mismatch")

    _load_plugin()
    cmds.file(new=True, force=True)
    from mmd_tools.io.mmd_importer import import_mmd_file

    root = import_mmd_file(
        str(pmx_path),
        options=manifest["import"]["options"],
    )
    if not root or not cmds.objExists(root):
        raise RuntimeError(f"production importer returned no model root: {root!r}")
    root = str(root)
    import_census = _census(manifest)
    _assert_census(import_census, manifest["import"], "import")
    _assert_root_contract(root, manifest)
    zero_connections = _assert_connections()
    texture_resolution = _assert_texture_resolution(files, manifest)
    humanik = _characterize_humanik(root, manifest)

    out_path = Path(args.out).resolve()
    scene_path = out_path.with_suffix(".ma")
    scene_path.parent.mkdir(parents=True, exist_ok=True)
    cmds.file(rename=str(scene_path))
    cmds.file(save=True, force=True, type="mayaAscii")
    cmds.file(new=True, force=True)
    cmds.file(str(scene_path), open=True, force=True)
    reopened_root = _find_model_root()
    reopen_census = _census(manifest)
    _assert_census(reopen_census, manifest["scene_reopen"], "scene reopen")
    _assert_root_contract(reopened_root, manifest)
    reopen_zero_connections = _assert_connections()
    reopen_texture_resolution = _assert_texture_resolution(files, manifest)
    humanik_reopen = _humanik_reopen_contract(reopened_root, manifest)
    if set(_leaf_name(child) for child in (cmds.listRelatives(reopened_root, children=True, fullPath=True) or [])) != set(
        manifest["scene_reopen"]["required_root_children"]
    ):
        raise AssertionError("scene reopen lost a required root child")
    report = {
        "status": "pass",
        "fixture": manifest["name"],
        "manifest": str(manifest_path),
        "pmx": str(pmx_path),
        "maya_version": cmds.about(version=True),
        "parse": {"counts": parsed_counts, "texture_refs": texture_refs, "rig_contract": rig_contract},
        "import": {
            "root": root,
            "census": import_census,
            "zero_connection_counts": zero_connections,
            "texture_resolution": texture_resolution,
            "humanik": humanik,
        },
        "scene_reopen": {
            "scene": str(scene_path),
            "root": reopened_root,
            "census": reopen_census,
            "zero_connection_counts": reopen_zero_connections,
            "texture_resolution": reopen_texture_resolution,
            "humanik": humanik_reopen,
        },
    }
    if not humanik_reopen["persisted"]:
        report["status"] = "fail"
        raise _ReportableGateError(
            "scene reopen did not preserve the HumanIK characterization contract: "
            f"{humanik_reopen}",
            report,
        )
    return report


def main() -> int:
    args = _parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    report: Dict[str, Any] = {"status": "fail", "fixture": "yw_test_model"}
    maya.standalone.initialize(name="python")
    try:
        report = _run_gate(args)
    except Exception as error:
        if isinstance(error, _ReportableGateError):
            report.update(error.report)
        report["error"] = f"{type(error).__name__}: {error}"
        return_code = 1
    else:
        return_code = 0
    finally:
        _write_report(Path(args.out).resolve(), report)
        maya.standalone.uninitialize()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
