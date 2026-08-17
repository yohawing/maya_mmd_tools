from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_morph_weight_command_has_narrow_atomic_contract():
    source = (ROOT / "cpp/src/MmdAuthoringMorphWeightCommand.cpp").read_text(
        encoding="utf-8"
    )
    assert 'constexpr const char* kCommand = "mmdAuthoringSetMorphWeights"' in source
    assert 'nodeType == "mmdMorphController" && attrName == "inputWeight"' in source
    assert 'nodeType == "blendShape" && attrName == "weight"' in source
    assert "controllerWeight && numericType != MFnNumericData::kDouble" in source
    assert "blendShapeWeight && numericType != MFnNumericData::kFloat" in source
    assert "plug.isLocked()" in source
    assert "plug.isFreeToChange" in source
    assert "std::isfinite" in source
    assert '"duplicate_plug"' in source
    assert '"rollback_failed"' in source
    assert "actual != value" in source


def test_morph_weight_command_is_built_registered_and_unregistered():
    cmake = (ROOT / "cpp/src/CMakeLists.txt").read_text(encoding="utf-8")
    plugin = (ROOT / "cpp/src/pluginMain.cpp").read_text(encoding="utf-8")
    assert "MmdAuthoringMorphWeightCommand.cpp" in cmake
    assert "MmdAuthoringMorphWeightCommand.h" in cmake
    assert 'registerCommand("mmdAuthoringSetMorphWeights"' in plugin
    assert 'deregisterCommand("mmdAuthoringSetMorphWeights"' in plugin
