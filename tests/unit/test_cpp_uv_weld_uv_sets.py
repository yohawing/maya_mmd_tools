"""Contracts for preserving UV-set identity during native seam welding."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "cpp" / "src" / "MmdWeldUvSeamVertices.cpp"


def test_weld_reuses_default_map1_before_copying_uvs() -> None:
    """A rebuilt mesh must not turn Maya's default map1 into map11."""
    source = SOURCE.read_text(encoding="utf-8")
    copy_block = source[source.index("// Copy all UV sets") :]
    copy_block = copy_block[: copy_block.index("// Restore authored face-corner normals")]

    assert "newMeshFn.getUVSetNames(existingNames)" in copy_block
    assert "existingNames[i] == uvSet.name" in copy_block
    assert "if (uvStatus && !hasUvSet)" in copy_block
    assert "newMeshFn.setUVs(uvSet.u, uvSet.v, &targetName)" in copy_block
    assert "newMeshFn.assignUVs(uvSet.counts, uvSet.ids, &targetName)" in copy_block
    assert "createUVSetWithName(uvSet.name" in copy_block
    assert "MString createdName = newMeshFn.createUVSetWithName" not in copy_block


def test_weld_restores_the_authored_current_uv_set() -> None:
    """TEXCOORD0 must keep using the source mesh's current UV set."""
    source = SOURCE.read_text(encoding="utf-8")

    assert "const MString currentUvSetName = meshFn.currentUVSetName" in source
    assert "newMeshFn.setCurrentUVSetName(currentUvSetName)" in source


def test_weld_advertises_source_to_local_mapping_capability() -> None:
    """Python must be able to reject an older command before topology changes."""
    source = SOURCE.read_text(encoding="utf-8")

    assert '"-qc", "-queryCapabilities", MSyntax::kBoolean' in source
    assert 'kSourceToLocalCapability = "sourceToLocalV1"' in source
    assert "capabilities.append(kSourceToLocalCapability)" in source
