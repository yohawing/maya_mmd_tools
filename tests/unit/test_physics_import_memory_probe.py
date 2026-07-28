"""Pure-Python contract tests for the physics import memory gate."""

from tests.viewport.physics_import_memory_probe import _baseline_gate, _solver_state


def _sample(private_bytes, working_set_bytes, available=True):
    return {
        "available": available,
        "privateBytes": private_bytes,
        "workingSetBytes": working_set_bytes,
        "label": "afterNewScene",
    }


def test_baseline_gate_passes_small_monotonic_noise():
    report = _baseline_gate(
        [
            _sample(1_000_000_000, 1_200_000_000),
            _sample(1_005_000_000, 1_203_000_000),
            _sample(1_011_000_000, 1_207_000_000),
        ]
    )

    assert report["status"] == "pass"
    assert all(metric["passed"] for metric in report["metrics"])
    assert report["metrics"][0]["baselineDeltaBytes"] == 11_000_000


def test_baseline_gate_fails_large_monotonic_private_growth():
    report = _baseline_gate(
        [
            _sample(1_000_000_000, 1_200_000_000),
            _sample(1_080_000_000, 1_205_000_000),
            _sample(1_160_000_000, 1_210_000_000),
        ]
    )

    assert report["status"] == "fail"
    private_metric = report["metrics"][0]
    assert private_metric["largeMonotonicIncrease"] is True
    assert private_metric["baselineDeltaBytes"] == 160_000_000


def test_baseline_gate_fails_large_net_growth_even_when_not_monotonic():
    report = _baseline_gate(
        [
            _sample(1_000_000_000, 1_200_000_000),
            _sample(950_000_000, 1_190_000_000),
            _sample(1_160_000_000, 1_210_000_000),
        ]
    )

    assert report["status"] == "fail"
    private_metric = report["metrics"][0]
    assert private_metric["monotonicNonDecreasing"] is False
    assert private_metric["largeNetIncrease"] is True
    assert private_metric["largeMonotonicIncrease"] is False


def test_baseline_gate_fails_closed_when_memory_is_unavailable():
    report = _baseline_gate(
        [
            _sample(None, None, available=False),
            _sample(None, None, available=False),
            _sample(None, None, available=False),
        ]
    )

    assert report["status"] == "fail"
    assert report["errors"]
    assert all(metric["passed"] is False for metric in report["metrics"])


def test_baseline_gate_requires_three_cycles():
    report = _baseline_gate(
        [
            _sample(1_000_000_000, 1_200_000_000),
            _sample(1_005_000_000, 1_203_000_000),
        ]
    )

    assert report["status"] == "fail"
    assert "at least 3" in report["errors"][0]


class _SolverCmds:
    def __init__(self, *, solvers, solved=True, status="cached", bone_count=1):
        self.solvers = solvers
        self.solved = solved
        self.status = status
        self.bone_count = bone_count

    def listConnections(self, *_args, **_kwargs):
        return self.solvers

    def getAttr(self, plug):
        if plug.endswith(".outSolved"):
            return self.solved
        if plug.endswith(".outStatus"):
            return self.status
        if plug.endswith(".outBoneCount"):
            return self.bone_count
        raise AssertionError(plug)


def test_solver_state_accepts_all_success_statuses_when_solved():
    for status in ("reset", "stepped", "cached", "pose-updated"):
        report = _solver_state(_SolverCmds(solvers=["solver1"], status=status), "root")

        assert report["healthy"] is True
        assert report["solvers"]["solver1"]["outSolved"] is True


def test_solver_state_rejects_unsolved_or_unowned_solver():
    unsolved = _solver_state(_SolverCmds(solvers=["solver1"], solved=False), "root")
    missing = _solver_state(_SolverCmds(solvers=[]), "root")

    assert unsolved["healthy"] is False
    assert missing == {"solverCount": 0, "healthy": False, "solvers": {}}
