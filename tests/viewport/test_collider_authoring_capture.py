
from tests.viewport.collider_authoring_capture import _resolve_playblast


def test_resolve_playblast_accepts_maya_numbered_output(tmp_path):
    numbered = tmp_path / "capture.0000.png"
    numbered.write_bytes(b"png")
    assert _resolve_playblast(tmp_path / "capture.png") == numbered


def test_resolve_playblast_preserves_exact_output(tmp_path):
    exact = tmp_path / "capture.png"
    exact.write_bytes(b"png")
    assert _resolve_playblast(exact) == exact
