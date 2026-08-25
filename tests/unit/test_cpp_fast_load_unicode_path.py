"""Contracts for Unicode PMX paths in the native fast-load command."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "cpp" / "src" / "mmdFastLoad.cpp"


def test_fast_load_decodes_maya_arguments_as_utf8() -> None:
    """Maya command arguments must not pass through the Windows code page."""
    source = SOURCE.read_text(encoding="utf-8")
    parse_args = source[source.index("bool MmdFastLoad::parseArgs(") :]
    parse_args = parse_args[: parse_args.index("// -----------------------------------------------------------------------\n// Helpers")]

    assert 'flagArgumentString("-f", 0).asUTF8()' in parse_args
    assert 'flagArgumentString("-n", 0).asUTF8()' in parse_args
    assert 'flagArgumentString("-f", 0).asChar()' not in parse_args
    assert 'flagArgumentString("-n", 0).asChar()' not in parse_args


def test_fast_load_opens_utf8_path_through_filesystem_conversion() -> None:
    """The binary reader must construct a native filesystem path on Windows."""
    source = SOURCE.read_text(encoding="utf-8")
    reader = source[source.index("std::vector<uint8_t> readBinaryFile(") :]
    reader = reader[: reader.index("// --- Byte-buffer adapters")]

    assert "std::filesystem::u8path(path)" in reader
    assert "std::ifstream ifs(nativePath" in reader
    assert "std::ifstream ifs(path" not in reader


def test_fast_load_reports_unreadable_utf8_path_without_codepage_loss() -> None:
    """Failed reads must preserve the original Unicode path in Maya errors."""
    source = SOURCE.read_text(encoding="utf-8")

    assert "mStringFromUtf8(filePath_)" in source
    assert "filePath_.c_str())" not in source


def test_render_witness_returns_utf8_json_to_maya() -> None:
    """Structured diagnostics must preserve Japanese texture/path values."""
    source = (ROOT / "cpp" / "src" / "MmdRenderShape.cpp").read_text(encoding="utf-8")

    assert "MString mStringFromUtf8(const std::string& value)" in source
    assert "result.setUTF8(value.c_str());" in source
    assert "setResult(mStringFromUtf8(shape->materialBindingDiagnosticsJson()))" in source
    assert "MString(shape->materialBindingDiagnosticsJson().c_str())" not in source
