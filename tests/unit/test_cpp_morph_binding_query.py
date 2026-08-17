from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_native_morph_query_is_narrow_read_only_and_registered():
    header = (ROOT / "cpp/src/MmdAuthoringMorphBindingQuery.h").read_text(encoding="utf-8")
    source = (ROOT / "cpp/src/MmdAuthoringMorphBindingQuery.cpp").read_text(encoding="utf-8")
    plugin = (ROOT / "cpp/src/pluginMain.cpp").read_text(encoding="utf-8")
    cmake = (ROOT / "cpp/src/CMakeLists.txt").read_text(encoding="utf-8")
    assert "bool isUndoable() const override { return false; }" in header
    assert "setValue" not in source and "setAttr" not in source
    assert "outputWeight" in source and "mmd_blendshape_morph_names_json" in source
    assert "inputTarget" not in source  # compressed target payload is deliberately excluded
    assert 'registerCommand("mmdAuthoringQueryMorphBindings"' in plugin
    assert "MmdAuthoringMorphBindingQuery.cpp" in cmake


def test_native_morph_query_returns_versioned_canonical_observation_dto():
    source = (ROOT / "cpp/src/MmdAuthoringMorphBindingQuery.cpp").read_text(encoding="utf-8")
    for token in (
        '"version"',
        '"command"',
        '"destinations"',
        '"blendShapes"',
        '"rawNameMappingJson"',
        "canonicalNodeName",
        "logicalIndex",
        "plugsAlias",
    ):
        assert token in source
