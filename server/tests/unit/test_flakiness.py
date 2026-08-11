from testforge.analytics.flakiness import MIN_SAMPLE, WINDOW, score_outcomes

PASS = "passed"
FAIL = "failed"


def sequence(letters: str) -> list[str]:
    """'PPFPP' -> the outcome strings, for tests that read like the design."""
    return [{"P": "passed", "F": "failed", "E": "error", "S": "skipped"}[c] for c in letters]


def test_a_regression_that_stays_broken_is_not_flaky():
    result = score_outcomes(sequence("PPPFFF"))

    assert result.transitions == 1
    assert result.is_flaky is False, (
        "breaking once and staying broken is a regression, not flakiness"
    )


def test_a_fix_is_not_flaky():
    result = score_outcomes(sequence("FFFPPP"))

    assert result.transitions == 1
    assert result.is_flaky is False


def test_a_test_that_recovers_on_its_own_is_flaky():
    result = score_outcomes(sequence("PPFPP"))

    assert result.transitions == 2
    assert result.is_flaky is True, "recovering with nobody fixing it is exactly what flakiness is"


def test_constant_flip_flopping_scores_one():
    result = score_outcomes(sequence("PFPFPF"))

    assert result.transitions == 5
    assert result.score == 1.0
    assert result.is_flaky is True


def test_a_stable_suite_scores_zero():
    result = score_outcomes(sequence("PPPPPP"))

    assert result.transitions == 0
    assert result.score == 0.0
    assert result.is_flaky is False


def test_errors_count_as_failures():
    result = score_outcomes(sequence("PPEPP"))

    assert result.transitions == 2
    assert result.is_flaky is True


def test_skips_are_excluded_from_the_sequence_entirely():
    result = score_outcomes(sequence("PPSFPP"))

    assert result.sequence == sequence("PPFPP"), "a skip says nothing about stability"
    assert result.sample_size == 5
    assert result.transitions == 2
    assert result.is_flaky is True


def test_a_short_history_is_never_flagged_however_it_flips():
    result = score_outcomes(sequence("PFPF"))

    assert result.transitions == 3
    assert result.sample_size == 4
    assert result.is_flaky is False, (
        f"fewer than {MIN_SAMPLE} results is not evidence, whatever the shape"
    )


def test_only_the_most_recent_window_is_considered():
    result = score_outcomes(sequence("PF" * 20))

    assert result.sample_size == WINDOW


def test_an_empty_history_is_handled():
    result = score_outcomes([])

    assert result.sample_size == 0
    assert result.transitions == 0
    assert result.score == 0.0
    assert result.is_flaky is False


def test_a_history_of_only_skips_is_handled():
    result = score_outcomes(sequence("SSSSSS"))

    assert result.sequence == []
    assert result.is_flaky is False
