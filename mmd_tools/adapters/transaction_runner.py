"""Maya-independent orchestration for one authoring transaction.

The runner deliberately does not know how a target is discovered or written.
Callers provide the complete target identity set and the callbacks that own
those operations.  In particular, a native command caller can provide native
``begin``/``rollback`` callbacks without the runner opening a Python undo
chunk of its own.
"""

from __future__ import annotations

from typing import Any, Callable, Generic, Iterable, Optional, Tuple, TypeVar


ResultT = TypeVar("ResultT")


class TransactionFailure(RuntimeError):
    """Describe one failed transaction phase and its optional rollback error."""

    def __init__(
        self,
        operation: str,
        phase: str,
        target_identities: Tuple[Any, ...],
        original_error: Exception,
        rollback_error: Optional[Exception] = None,
    ) -> None:
        self.operation = operation
        self.phase = phase
        self.target_identities = target_identities
        self.original_error = original_error
        self.rollback_error = rollback_error

        message = (
            f"{operation} failed during {phase} for targets {target_identities!r}: "
            f"{original_error}"
        )
        if rollback_error is not None:
            message += f"; rollback failed: {rollback_error}"
        super().__init__(message)


BeginCallback = Callable[[Tuple[Any, ...]], None]
MutateCallback = Callable[[Tuple[Any, ...]], ResultT]
ValidateResultCallback = Callable[[ResultT, Tuple[Any, ...]], None]
VerifyAndCommitCallback = Callable[[ResultT, Tuple[Any, ...]], None]
RollbackCallback = Callable[[Tuple[Any, ...]], None]
ErrorFactory = Callable[[TransactionFailure], Exception]


class TransactionRunner(Generic[ResultT]):
    """Run one callback-owned transaction with exactly-once rollback.

    ``target_identities`` is copied to a tuple before any callback runs.  The
    same immutable tuple is supplied to each callback, so target discovery and
    scene enumeration remain outside this class.  ``mutate`` returns the
    operation result; that result is passed to validation and verification and
    is returned from :meth:`run` after a successful commit.
    """

    __slots__ = (
        "operation",
        "_target_identities",
        "_begin",
        "_mutate",
        "_validate_result",
        "_verify_and_commit",
        "_rollback",
        "_error_factory",
        "_started",
    )

    def __init__(
        self,
        operation: str,
        target_identities: Iterable[Any],
        *,
        begin: BeginCallback,
        mutate: MutateCallback[ResultT],
        verify_and_commit: VerifyAndCommitCallback[ResultT],
        rollback: RollbackCallback,
        validate_result: Optional[ValidateResultCallback[ResultT]] = None,
        error_factory: Optional[ErrorFactory] = None,
    ) -> None:
        self.operation = operation
        self._target_identities = tuple(target_identities)
        self._begin = begin
        self._mutate = mutate
        self._validate_result = validate_result
        self._verify_and_commit = verify_and_commit
        self._rollback = rollback
        self._error_factory = error_factory
        self._started = False

    @property
    def targets(self) -> Tuple[Any, ...]:
        """Return the immutable target identity tuple supplied to callbacks."""

        return self._target_identities

    @property
    def started(self) -> bool:
        """Whether ``begin`` completed successfully for the current run."""

        return self._started

    def run(self) -> ResultT:
        """Execute begin, mutate, validate, and verify/commit in that order."""

        targets = self._target_identities
        self._started = False
        try:
            self._begin(targets)
        except Exception as exc:
            # A failed begin did not open a transaction and must never invoke
            # rollback.  Keep the begin error as the direct cause.
            failure = self._make_failure("begin", exc, targets)
            raise self._customize(failure) from exc

        self._started = True
        try:
            result = self._mutate(targets)
        except Exception as exc:
            self._rollback_then_raise("mutate", exc, targets)

        if self._validate_result is not None:
            try:
                self._validate_result(result, targets)
            except Exception as exc:
                self._rollback_then_raise("validate", exc, targets)

        try:
            self._verify_and_commit(result, targets)
        except Exception as exc:
            self._rollback_then_raise("verify/commit", exc, targets)

        return result

    def _rollback_then_raise(
        self,
        phase: str,
        original_error: Exception,
        targets: Tuple[Any, ...],
    ) -> None:
        """Rollback once and raise an error retaining both failure causes."""

        try:
            self._rollback(targets)
        except Exception as rollback_error:
            failure = self._make_failure(phase, original_error, targets, rollback_error)
            raise self._customize(failure) from rollback_error

        failure = self._make_failure(phase, original_error, targets)
        raise self._customize(failure) from original_error

    def _make_failure(
        self,
        phase: str,
        original_error: Exception,
        targets: Tuple[Any, ...],
        rollback_error: Optional[Exception] = None,
    ) -> TransactionFailure:
        return TransactionFailure(
            self.operation,
            phase,
            targets,
            original_error,
            rollback_error,
        )

    def _customize(self, failure: TransactionFailure) -> Exception:
        if self._error_factory is None:
            return failure
        custom_error = self._error_factory(failure)
        if not isinstance(custom_error, Exception):
            raise TypeError("error_factory must return an Exception")
        return custom_error


__all__ = ["TransactionFailure", "TransactionRunner"]
