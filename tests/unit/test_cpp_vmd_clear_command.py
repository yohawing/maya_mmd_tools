from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "cpp/src/MmdVmdClearCurvesCommand.cpp"
HEADER = ROOT / "cpp/src/MmdVmdClearCurvesCommand.h"


def test_native_vmd_clear_has_strict_prepare_and_destructive_remove_contract():
    header = HEADER.read_text(encoding="utf-8")
    source = SOURCE.read_text(encoding="utf-8")

    assert 'constexpr const char* kCommand = "mmdVmdClearCurves"' in source
    assert 'payload.size() != 2U' in source
    assert 'payload.contains("version")' in source
    assert 'payload.contains("plugs")' in source
    assert "MAnimUtil::findAnimation" in source
    assert "getExistingArrayAttributeIndices" in source
    assert "MFnAnimCurve curve" in source
    assert "curve.remove(index);" in source
    assert '"phase", phase' in source or '"phase", "complete"' in source
    assert '"mutated", mutated' in source
    assert '"reason"' in source
    assert 'finishError("mutation"' in source
    assert "MAnimCurveChange" not in header + source
    assert "undoIt" not in header + source
    assert "redoIt" not in header + source


def test_native_vmd_clear_is_registered_and_built():
    cmake = (ROOT / "cpp/src/CMakeLists.txt").read_text(encoding="utf-8")
    plugin = (ROOT / "cpp/src/pluginMain.cpp").read_text(encoding="utf-8")

    assert "MmdVmdClearCurvesCommand.cpp" in cmake
    assert "MmdVmdClearCurvesCommand.h" in cmake
    assert '#include "MmdVmdClearCurvesCommand.h"' in plugin
    assert 'registerCommand("mmdVmdClearCurves"' in plugin
    assert 'deregisterCommand("mmdVmdClearCurves"' in plugin
    assert "isUndoable" in SOURCE.read_text(encoding="utf-8")
