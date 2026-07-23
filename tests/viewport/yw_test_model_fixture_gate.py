"""Maya PMX fixture gate for the checked-in ``yw_test_model`` asset.

The gate verifies the manifest and parser census before exercising the
production importer.  It then saves a Maya ASCII scene, reopens it in the
same mayapy process, and checks the representative MMD rig/physics contracts
again.  The script is intentionally deterministic and never regenerates the
fixture or its textures.
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
    if set(_leaf_name(child) for child in (cmds.listRelatives(reopened_root, children=True, fullPath=True) or [])) != set(
        manifest["scene_reopen"]["required_root_children"]
    ):
        raise AssertionError("scene reopen lost a required root child")
    return {
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
        },
        "scene_reopen": {
            "scene": str(scene_path),
            "root": reopened_root,
            "census": reopen_census,
            "zero_connection_counts": reopen_zero_connections,
            "texture_resolution": reopen_texture_resolution,
        },
    }


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
