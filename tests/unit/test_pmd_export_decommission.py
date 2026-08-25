"""Regression coverage for the PMD import-only/export fail-closed boundary."""

from pathlib import Path

from mmd_tools.actions.export_model_action import ExportModelAction
from mmd_tools.services.export_workflow_service import (
    ExportWorkflowRequest,
    ExportWorkflowService,
    STATE_BLOCKED,
)
from mmd_tools.validation.export_validator import validate_model_data
from mmd_tools.validation.output_verifier import verify_model_output


class _SpyCollector:
    def __init__(self):
        self.calls = 0

    def __call__(self, options):
        self.calls += 1
        raise AssertionError("PMD export must reject before collection")


class _SpyWriter:
    def __init__(self):
        self.calls = 0

    def export_pmx_model(self, path, payload):
        self.calls += 1
        Path(path).write_bytes(b"unexpected")


def test_pmd_workflow_rejects_before_collector_and_preserves_existing_output(tmp_path):
    """A PMD request must not invoke collector/writer or replace output."""
    collector = _SpyCollector()
    writer = _SpyWriter()
    action = ExportModelAction(pmx_exporter=writer, collector=collector)
    service = ExportWorkflowService(model_action=action)
    output = tmp_path / "model.pmd"
    output.write_bytes(b"existing")

    result = service.execute(
        ExportWorkflowRequest(
            str(output),
            {"export_format": "pmd", "target_model": "model_ROOT"},
        )
    )

    assert result.state == STATE_BLOCKED
    assert not result.succeeded
    assert collector.calls == 0
    assert writer.calls == 0
    assert output.read_bytes() == b"existing"
    assert {issue.code for issue in result.report.issues} == {"EXPORT_OPTIONS_INVALID"}


def test_pmd_payload_and_output_verifiers_are_canonical_unsupported():
    """Lower-level PMD calls remain fail-closed without PMD writer policy codes."""
    report = validate_model_data({}, "pmd")
    assert report.is_blocking
    assert [issue.code for issue in report.issues] == ["EXPORT_OPTIONS_INVALID"]

    output_report = verify_model_output("missing.pmd", "pmd")
    assert output_report.is_blocking
    assert [issue.code for issue in output_report.issues] == ["EXPORT_OPTIONS_INVALID"]
