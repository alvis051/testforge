"""Flakiness scoring by counting outcome transitions.

A genuine regression transitions exactly once (P P P F F F). So does a fix
(F F F P P P). Only a test that recovered with nobody fixing it transitions
twice or more (P P F P P), and that unexplained self-recovery is what flakiness
*is*. The >= 2 threshold therefore encodes the definition rather than tuning a
percentage against it — which also means it needs no commit SHA, reading the
shape of the history instead of requiring an external code identity.
"""

from dataclasses import dataclass

WINDOW = 20
MIN_SAMPLE = 5
MIN_TRANSITIONS = 2

#: Outcomes that carry stability information. Skips and blocks are excluded
#: entirely rather than treated as failures — a skipped test says nothing.
COUNTED = ("passed", "failed", "error")


@dataclass(frozen=True)
class FlakinessResult:
    sequence: list[str]
    sample_size: int
    transitions: int
    score: float
    is_flaky: bool


def score_outcomes(outcomes: list[str]) -> FlakinessResult:
    """Score one case's outcome history. ``outcomes`` is oldest-first."""
    sequence = [outcome for outcome in outcomes if outcome in COUNTED][-WINDOW:]
    sample_size = len(sequence)

    transitions = sum(
        1
        for earlier, later in zip(sequence, sequence[1:], strict=False)
        if (earlier == "passed") != (later == "passed")
    )

    # Ranking only. Classification is the >= MIN_TRANSITIONS rule below: a test
    # failing once in twenty runs is genuinely flaky but scores low, and should
    # appear ranked beneath one that flips constantly.
    score = transitions / (sample_size - 1) if sample_size > 1 else 0.0

    return FlakinessResult(
        sequence=sequence,
        sample_size=sample_size,
        transitions=transitions,
        score=score,
        is_flaky=sample_size >= MIN_SAMPLE and transitions >= MIN_TRANSITIONS,
    )
