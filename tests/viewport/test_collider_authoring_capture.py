
from tests.viewport.collider_authoring_capture import _resolve_playblast, _semantic_failures


def test_resolve_playblast_accepts_maya_numbered_output(tmp_path):
    numbered = tmp_path / "capture.0000.png"
    numbered.write_bytes(b"png")
    assert _resolve_playblast(tmp_path / "capture.png") == numbered


def test_resolve_playblast_preserves_exact_output(tmp_path):
    exact = tmp_path / "capture.png"
    exact.write_bytes(b"png")
    assert _resolve_playblast(exact) == exact


def _green_report():
    return {
        "checks": {
            "editedCapsuleTotalHeight": 6.0,
            "reopenCapsuleTotalHeight": 6.0,
            "boxHidden": True,
            "pluginInitializeComplete": True,
            "unselectedSelection": [],
            "unselectedDisplayStatus": 2,
            "selectedSelection": ["|ColliderEvidence|capsule"],
            "selectedDisplayStatus": 8,
            "reopenMatrixMaxError": 0.0,
            "realRestMatrixMaxError": 0.0,
            "followOffsetMaxError": 0.0,
            "followBboxCenterMaxError": 0.0,
            "boundFollowConstraint": True,
            "rawPoseUnchanged": True,
            "physicsModeLineStyles": [0, 0, 0],
            "realColliderCount": 2,
            "realCollidersVisible": True,
        }
    }


def test_semantic_gate_accepts_complete_evidence():
    assert _semantic_failures(_green_report()) == []


def test_semantic_gate_rejects_each_false_positive():
    mutations = {
        "editedCapsuleTotalHeight": 5.0,
        "reopenCapsuleTotalHeight": 7.0,
        "boxHidden": False,
        "pluginInitializeComplete": False,
        "unselectedSelection": ["|ColliderEvidence|capsule"],
        "unselectedDisplayStatus": 8,
        "selectedSelection": [],
        "selectedDisplayStatus": 2,
        "reopenMatrixMaxError": 0.01,
        "realRestMatrixMaxError": 0.01,
        "followOffsetMaxError": 0.01,
        "followBboxCenterMaxError": 0.01,
        "boundFollowConstraint": False,
        "rawPoseUnchanged": False,
        "physicsModeLineStyles": [0, 2, 1],
        "realColliderCount": 0,
        "realCollidersVisible": False,
    }
    for key, value in mutations.items():
        report = _green_report()
        report["checks"][key] = value
        assert _semantic_failures(report), key


def test_semantic_gate_rejects_non_finite_numbers():
    for key in (
        "editedCapsuleTotalHeight",
        "reopenCapsuleTotalHeight",
        "reopenMatrixMaxError",
        "realRestMatrixMaxError",
        "followOffsetMaxError",
        "followBboxCenterMaxError",
    ):
        report = _green_report()
        report["checks"][key] = float("nan")
        assert _semantic_failures(report), key
