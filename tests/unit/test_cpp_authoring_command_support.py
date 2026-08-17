from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_native_authoring_support_is_narrow_undoable_and_preflights():
    header = (ROOT / "cpp/src/MmdAuthoringCommandSupport.h").read_text(encoding="utf-8")
    source = (ROOT / "cpp/src/MmdAuthoringCommandSupport.cpp").read_text(encoding="utf-8")
    assert "redoIt() override" in header
    assert "undoIt() override" in header
    assert "MObjectHandle" in header
    assert "mmdAuthoringWitnessBool" in source
    assert "mmdAuthoringWitnessInt" in source
    assert "mmdAuthoringWitnessDouble" in source
    assert "mmdAuthoringWitnessString" in source
    assert "plug_not_allowed" in source
    assert "ambiguous_or_missing_node" in source
    assert "duplicate_plug" in source
    assert "valueMatches" in source
    assert "numeric_limits<int>" in source
    assert "duplicate_json_key" in source
    assert "initialExecution_" in header
    assert "return MS::kFailure" in source
    assert 'prepared_ = false' in source
    assert "write set was restored" in source


def test_native_authoring_support_is_registered_and_built():
    plugin = (ROOT / "cpp/src/pluginMain.cpp").read_text(encoding="utf-8")
    cmake = (ROOT / "cpp/src/CMakeLists.txt").read_text(encoding="utf-8")
    assert 'registerCommand("mmdAuthoringSetAttrs"' in plugin
    assert 'deregisterCommand("mmdAuthoringSetAttrs"' in plugin
    assert 'plugin.deregisterCommand("mmdWeldUvSeamVertices")' in plugin
    assert "renderer->deregisterOverride" in plugin
    assert "if (!cleanupStatus)" in plugin
    assert "keeping its pointer and registration state" in plugin
    assert "registration remains tracked" in plugin
    override_cleanup = plugin.split(
        "if (sCppRegisteredMmdNativeCasterOverride && renderer)", 1
    )[1].split("if (sCppRegisteredMmdNativeCasterWitnessCommand)", 1)[0]
    assert override_cleanup.index("if (!cleanupStatus)") < override_cleanup.index(
        "delete sMmdNativeCasterOverride"
    )
    assert "MmdAuthoringCommandSupport.cpp" in cmake
