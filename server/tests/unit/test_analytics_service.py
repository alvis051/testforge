from datetime import UTC, datetime, timedelta

import pytest
from testforge.schemas.results import ResultIn
from testforge.services.analytics_service import AnalyticsService
from testforge.services.case_service import CaseService
from testforge.services.ingestion_service import IngestionService
from testforge.services.project_service import ProjectService
from testforge.services.run_service import RunService

BASE = datetime(2026, 8, 1, tzinfo=UTC)


@pytest.fixture
def project(db_session):
    project = ProjectService(db_session).create(
        key="CHK", name="Checkout", description=None, actor="local"
    )
    db_session.commit()
    return project


def make_case(session, project, title):
    return CaseService(session).create(
        project=project,
        suite_id=None,
        title=title,
        execution_type="automated",
        priority="p2",
        preconditions=None,
        steps=[],
        expected_result=None,
        tags=[],
        actor="local",
    )


def record(session, project, index, outcomes, failures=None):
    """One run at BASE+index days, with {case_key: outcome} results."""
    runs = RunService(session)
    run, _ = runs.open(
        project=project,
        external_id=f"run-{index}",
        name=f"run {index}",
        source="ci",
        ci_metadata={},
        actor="local",
    )
    run.started_at = BASE + timedelta(days=index)
    session.flush()
    IngestionService(session).ingest(
        run=run,
        results=[
            ResultIn(
                case_key=case_key,
                test_identifier=f"tests/t.py::{case_key}",
                framework="pytest",
                outcome=outcome,
                failure_type=(failures or {}).get(case_key, (None, None))[0],
                failure_message=(failures or {}).get(case_key, (None, None))[1],
                executed_at=BASE + timedelta(days=index),
            )
            for case_key, outcome in outcomes.items()
        ],
    )
    runs.complete(run.id)
    session.commit()
    return run


def test_flaky_cases_flags_self_recovery_and_not_a_regression(db_session, project):
    make_case(db_session, project, "Flaky one")
    make_case(db_session, project, "Broken one")
    db_session.commit()
    # CHK-1 recovers on its own; CHK-2 breaks once and stays broken.
    for index, (a, b) in enumerate(
        [
            ("passed", "passed"),
            ("passed", "passed"),
            ("failed", "passed"),
            ("passed", "failed"),
            ("failed", "failed"),
            ("passed", "failed"),
        ]
    ):
        record(db_session, project, index, {"CHK-1": a, "CHK-2": b})

    flaky = AnalyticsService(db_session).flaky_cases(project)

    assert [c.case_key for c in flaky] == ["CHK-1"]
    assert flaky[0].transitions == 4
    assert flaky[0].sample_size == 6
    assert flaky[0].sequence[0] == "passed"


def test_flaky_cases_are_ranked_by_score(db_session, project):
    make_case(db_session, project, "Very flaky")
    make_case(db_session, project, "Mildly flaky")
    db_session.commit()
    for index, (a, b) in enumerate(
        [
            ("passed", "passed"),
            ("failed", "passed"),
            ("passed", "passed"),
            ("failed", "failed"),
            ("passed", "passed"),
            ("failed", "passed"),
        ]
    ):
        record(db_session, project, index, {"CHK-1": a, "CHK-2": b})

    flaky = AnalyticsService(db_session).flaky_cases(project)

    assert [c.case_key for c in flaky] == ["CHK-1", "CHK-2"]
    assert flaky[0].score > flaky[1].score


def test_a_case_covered_by_two_automated_tests_is_stable_when_uniform_per_run(db_session, project):
    """Bug B: one case, two automation links (two ``test_identifier``s under one
    ``case_key``). One always passes, the other always fails, across 5 runs.
    Every run is uniformly "failed" for the case (not every countable result
    passed), so the case is stable-but-broken — never flapping — and must not
    be reported as flaky."""
    make_case(db_session, project, "Dual-covered")
    db_session.commit()
    runs = RunService(db_session)
    for index in range(5):
        run, _ = runs.open(
            project=project,
            external_id=f"run-{index}",
            name=f"run {index}",
            source="ci",
            ci_metadata={},
            actor="local",
        )
        run.started_at = BASE + timedelta(days=index)
        db_session.flush()
        IngestionService(db_session).ingest(
            run=run,
            results=[
                ResultIn(
                    case_key="CHK-1",
                    test_identifier="tests/t.py::test_a",
                    framework="pytest",
                    outcome="passed",
                    executed_at=BASE + timedelta(days=index),
                ),
                ResultIn(
                    case_key="CHK-1",
                    test_identifier="tests/t.py::test_b",
                    framework="pytest",
                    outcome="failed",
                    executed_at=BASE + timedelta(days=index),
                ),
            ],
        )
        runs.complete(run.id)
        db_session.commit()

    flaky = AnalyticsService(db_session).flaky_cases(project)

    assert flaky == []


def test_flaky_cases_never_include_another_projects_history(db_session, project):
    other = ProjectService(db_session).create(
        key="OTH", name="Other", description=None, actor="local"
    )
    db_session.commit()
    make_case(db_session, other, "Foreign flaky")
    db_session.commit()
    for index, outcome in enumerate(["passed", "failed", "passed", "failed", "passed"]):
        record(db_session, other, index, {"OTH-1": outcome})

    assert AnalyticsService(db_session).flaky_cases(project) == []


def test_run_trends_never_include_another_projects_history(db_session, project):
    other = ProjectService(db_session).create(
        key="OTH", name="Other", description=None, actor="local"
    )
    db_session.commit()
    make_case(db_session, other, "Foreign case")
    db_session.commit()
    for index, outcome in enumerate(["passed", "failed", "passed"]):
        record(db_session, other, index, {"OTH-1": outcome})

    assert AnalyticsService(db_session).run_trends(project) == []


def test_run_trends_are_oldest_first_and_skip_does_not_count(db_session, project):
    make_case(db_session, project, "A")
    make_case(db_session, project, "B")
    db_session.commit()
    record(db_session, project, 0, {"CHK-1": "passed", "CHK-2": "skipped"})
    record(db_session, project, 1, {"CHK-1": "passed", "CHK-2": "failed"})

    trends = AnalyticsService(db_session).run_trends(project)

    assert [p.external_id for p in trends] == ["run-0", "run-1"]
    assert trends[0].pass_rate == 1.0, "a skip must not drag the pass rate down"
    assert trends[0].total == 1
    assert trends[1].pass_rate == 0.5


def test_a_run_with_nothing_countable_reports_a_null_pass_rate(db_session, project):
    make_case(db_session, project, "A")
    db_session.commit()
    record(db_session, project, 0, {"CHK-1": "skipped"})

    trends = AnalyticsService(db_session).run_trends(project)

    assert trends[0].pass_rate is None, "a null breaks the line rather than plotting a false zero"


def test_failure_categories_are_counted_and_ranked(db_session, project):
    make_case(db_session, project, "A")
    make_case(db_session, project, "B")
    db_session.commit()
    record(
        db_session,
        project,
        0,
        {"CHK-1": "failed", "CHK-2": "failed"},
        failures={
            "CHK-1": ("AssertionError", "assert 1 == 2"),
            "CHK-2": ("TimeoutError", "timed out"),
        },
    )
    record(
        db_session,
        project,
        1,
        {"CHK-1": "failed", "CHK-2": "passed"},
        failures={"CHK-1": ("AssertionError", "assert 3 == 4")},
    )

    categories = AnalyticsService(db_session).failure_categories(project)

    assert [(c.category, c.count) for c in categories] == [("assertion", 2), ("timeout", 1)]


def test_failure_categories_never_include_another_projects_history(db_session, project):
    other = ProjectService(db_session).create(
        key="OTH", name="Other", description=None, actor="local"
    )
    db_session.commit()
    make_case(db_session, other, "Foreign case")
    db_session.commit()
    record(
        db_session,
        other,
        0,
        {"OTH-1": "failed"},
        failures={"OTH-1": ("AssertionError", "assert 1 == 2")},
    )

    assert AnalyticsService(db_session).failure_categories(project) == []


def test_a_project_with_no_history_returns_empty_not_an_error(db_session, project):
    service = AnalyticsService(db_session)

    assert service.flaky_cases(project) == []
    assert service.run_trends(project) == []
    assert service.failure_categories(project) == []
