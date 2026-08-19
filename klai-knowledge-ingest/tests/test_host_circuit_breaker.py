"""Pure unit tests for knowledge_ingest.host_circuit_breaker.evaluate_chunk.

Onderdeel 2 of the 2026-08-19 intermedia.com "20 minutes, zero pages"
fix — see the module docstring in host_circuit_breaker.py for the full
rationale. Defaults used throughout mirror settings.py's:
consecutive_failure_threshold=5, min_attempts_for_ratio=10,
failure_ratio_threshold=0.5, refusal_threshold=3.
"""

from __future__ import annotations

from knowledge_ingest.host_circuit_breaker import (
    BreakerVerdict,
    ChunkObservation,
    HostCircuitBreakerState,
    evaluate_chunk,
)

_DEFAULT_KWARGS = {
    "consecutive_failure_threshold": 5,
    "min_attempts_for_ratio": 10,
    "failure_ratio_threshold": 0.5,
    "refusal_threshold": 3,
}


def _failure(*, attempted: int = 1, refused: int = 0) -> ChunkObservation:
    return ChunkObservation(
        attempted=attempted, failed=attempted, any_success=False, refused=refused
    )


def _success(*, attempted: int = 1) -> ChunkObservation:
    return ChunkObservation(attempted=attempted, failed=0, any_success=True, refused=0)


def _run(observations: list[ChunkObservation]) -> tuple[HostCircuitBreakerState, BreakerVerdict]:
    state = HostCircuitBreakerState()
    verdict = BreakerVerdict.CONTINUE
    for observation in observations:
        state, verdict = evaluate_chunk(state, observation, **_DEFAULT_KWARGS)
    return state, verdict


class TestConsecutiveFailures:
    def test_five_failures_in_a_row_aborts(self) -> None:
        _state, verdict = _run([_failure() for _ in range(5)])
        assert verdict == BreakerVerdict.ABORT_PERSISTENT_FAILURE

    def test_four_failures_then_a_success_then_four_more_does_not_abort(self) -> None:
        observations = (
            [_failure() for _ in range(4)] + [_success()] + [_failure() for _ in range(4)]
        )
        _state, verdict = _run(observations)
        assert verdict == BreakerVerdict.CONTINUE

    def test_a_success_resets_the_streak_to_zero(self) -> None:
        state = HostCircuitBreakerState()
        for _ in range(4):
            state, verdict = evaluate_chunk(state, _failure(), **_DEFAULT_KWARGS)
        assert state.consecutive_failures == 4
        state, verdict = evaluate_chunk(state, _success(), **_DEFAULT_KWARGS)
        assert state.consecutive_failures == 0
        assert verdict == BreakerVerdict.CONTINUE

    def test_a_wholly_failed_20_url_chunk_counts_as_one_observation_for_the_streak(
        self,
    ) -> None:
        """A block of twenty URLs that fails wholesale must only advance the
        consecutive-failure streak by one — not twenty. The ratio trigger is
        disabled here (a very high min_attempts_for_ratio) so this test
        isolates the streak counter from the independent ratio gate, which
        a 20/20-failed observation would otherwise also cross on its own."""
        kwargs = {**_DEFAULT_KWARGS, "min_attempts_for_ratio": 1_000_000}
        state, verdict = evaluate_chunk(HostCircuitBreakerState(), _failure(attempted=20), **kwargs)
        assert state.consecutive_failures == 1
        assert verdict == BreakerVerdict.CONTINUE

        # Five such 20-url blocks in a row (100 failed URLs total) is only
        # five OBSERVATIONS for the streak — enough to trip it exactly
        # because the threshold is 5, not because of the URL count.
        state = HostCircuitBreakerState()
        verdict = BreakerVerdict.CONTINUE
        for _ in range(5):
            state, verdict = evaluate_chunk(state, _failure(attempted=20), **kwargs)
        assert state.consecutive_failures == 5
        assert verdict == BreakerVerdict.ABORT_PERSISTENT_FAILURE


class TestFailureRatio:
    def test_nine_attempts_six_failed_does_not_abort_too_few_observations(self) -> None:
        """6/9 = 0.667 > 0.5, but only 9 attempts — below min_attempts_for_ratio
        (10) — so the ratio gate must not fire yet."""
        observations = [_failure() for _ in range(6)] + [_success() for _ in range(3)]
        state, verdict = _run(observations)
        assert state.attempted == 9
        assert state.failed == 6
        assert verdict == BreakerVerdict.CONTINUE

    def test_twelve_attempts_seven_failed_aborts(self) -> None:
        """7/12 = 0.583 > 0.5 and 12 >= min_attempts_for_ratio (10) — aborts."""
        observations = [_failure() for _ in range(7)] + [_success() for _ in range(5)]
        state, verdict = _run(observations)
        assert state.attempted == 12
        assert state.failed == 7
        assert verdict == BreakerVerdict.ABORT_PERSISTENT_FAILURE

    def test_exactly_half_failed_does_not_abort(self) -> None:
        """'meer dan de helft' (MORE than half) — exactly 50% must not trip."""
        observations = [_failure() for _ in range(5)] + [_success() for _ in range(5)]
        state, verdict = _run(observations)
        assert state.attempted == 10
        assert verdict == BreakerVerdict.CONTINUE


class TestRefusal:
    def test_three_refusals_aborts_immediately(self) -> None:
        observations = [
            ChunkObservation(attempted=1, failed=1, any_success=False, refused=1) for _ in range(3)
        ]
        _state, verdict = _run(observations)
        assert verdict == BreakerVerdict.ABORT_REFUSAL

    def test_three_refusals_with_interleaved_successes_still_aborts(self) -> None:
        """A refusal is never undone by a success — unlike the consecutive
        streak, the refusal counter is monotonic."""
        observations = [
            ChunkObservation(attempted=1, failed=1, any_success=False, refused=1),
            _success(),
            ChunkObservation(attempted=1, failed=1, any_success=False, refused=1),
            _success(),
            ChunkObservation(attempted=1, failed=1, any_success=False, refused=1),
        ]
        state, verdict = _run(observations)
        assert state.refused == 3
        # The interleaved successes DID reset the consecutive-failure streak...
        assert state.consecutive_failures == 1
        # ...but the refusal trigger fires anyway, independent of the streak.
        assert verdict == BreakerVerdict.ABORT_REFUSAL

    def test_two_refusals_does_not_abort(self) -> None:
        observations = [
            ChunkObservation(attempted=1, failed=1, any_success=False, refused=1) for _ in range(2)
        ]
        _state, verdict = _run(observations)
        assert verdict == BreakerVerdict.CONTINUE

    def test_refusal_takes_priority_over_persistent_failure_verdict(self) -> None:
        """When both the consecutive-failure and refusal thresholds are
        crossed by the same observation, the more specific ABORT_REFUSAL
        verdict wins."""
        observations = [
            ChunkObservation(attempted=1, failed=1, any_success=False, refused=1) for _ in range(5)
        ]
        _state, verdict = _run(observations)
        assert verdict == BreakerVerdict.ABORT_REFUSAL


class TestContinue:
    def test_all_successes_never_aborts(self) -> None:
        _state, verdict = _run([_success() for _ in range(50)])
        assert verdict == BreakerVerdict.CONTINUE

    def test_fresh_state_is_all_zero(self) -> None:
        state = HostCircuitBreakerState()
        assert state.consecutive_failures == 0
        assert state.attempted == 0
        assert state.failed == 0
        assert state.refused == 0
