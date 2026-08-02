from datetime import UTC, datetime

import pytest
from testforge.errors import AppError
from testforge.schemas.results import ResultIn
from testforge.services.automation_service import AutomationService
from testforge.services.case_service import CaseService
from testforge.services.ingestion_service import IngestionService
from testforge.services.project_service import ProjectService
from testforge.services.run_service import RunService

EXECUTED_AT = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


@pytest.fixture
def project(db_session):
    project = ProjectService(db_session).create(
        key="CHK", name="Checkout", description=None, actor="local"
    )
    db_session.commit()
    return project


@pytest.fixture
def case(db_session, project):
    case = CaseService(db_session).create(
        project=project,
        suite_id=None,
        title="Coupon",
        execution_type="automated",
        priority="p2",
        preconditions=None,
        steps=[],
        expected_result=None,
        tags=[],
        actor="local",
    )
    db_session.commit()
    return case


@pytest.fixture
def run(db_session, project):
    run, _ = RunService(db_session).open(
        project=project, external_id="gha-1", name="n", source="ci", ci_metadata={}, actor="local"
    )
    db_session.commit()
    return run


def result(**overrides) -> ResultIn:
    payload = {
        "case_key": "CHK-1",
        "test_identifier": "tests/test_checkout.py::test_coupon[web]",
        "framework": "pytest",
        "outcome": "passed",
        "duration_ms": 12,
        "executed_at": EXECUTED_AT,
    }
    payload.update(overrides)
    return ResultIn(**payload)


def test_resolved_result_pins_the_current_case_version(db_session, run, case):
    summary = IngestionService(db_session).ingest(run=run, results=[result()])
    db_session.commit()

    assert summary.recorded == 1
    assert summary.resolved == 1
    assert summary.unresolved == 0

    stored = IngestionService(db_session).results_for_run(run)
    assert stored[0].test_case_id == case.id
    assert stored[0].test_case_version_id is not None
    assert stored[0].unresolved_case_key is None


def test_version_pin_is_the_version_current_at_execution_time(db_session, run, case):
    service = IngestionService(db_session)
    service.ingest(run=run, results=[result()])
    db_session.commit()
    pinned_at_v1 = service.results_for_run(run)[0].test_case_version_id

    CaseService(db_session).update(
        case_key="CHK-1", expected_version_no=1, actor="editor", title="Coupon SAVE10"
    )
    db_session.commit()

    versions = CaseService(db_session).versions("CHK-1")
    assert versions[0].id == pinned_at_v1
    assert versions[1].id != pinned_at_v1, "the old result must still point at v1"


def test_ingest_upserts_the_automation_link(db_session, run, case):
    IngestionService(db_session).ingest(run=run, results=[result()])
    db_session.commit()

    links = AutomationService(db_session).links_for_case(case)
    assert [link.test_identifier for link in links] == ["tests/test_checkout.py::test_coupon[web]"]
    assert links[0].last_seen_at == EXECUTED_AT


def test_unknown_case_key_is_stored_as_unresolved_not_rejected(db_session, run):
    summary = IngestionService(db_session).ingest(run=run, results=[result(case_key="CHK-999")])
    db_session.commit()

    assert summary.recorded == 1
    assert summary.unresolved == 1
    stored = IngestionService(db_session).results_for_run(run)[0]
    assert stored.test_case_id is None
    assert stored.unresolved_case_key == "CHK-999"


def test_result_without_a_case_key_is_stored_as_unresolved(db_session, run):
    summary = IngestionService(db_session).ingest(run=run, results=[result(case_key=None)])
    db_session.commit()

    assert summary.unresolved == 1
    assert IngestionService(db_session).results_for_run(run)[0].unresolved_case_key is None


def test_archived_case_still_accepts_results_and_is_counted(db_session, run, case):
    CaseService(db_session).archive("CHK-1", actor="local")
    db_session.commit()

    summary = IngestionService(db_session).ingest(run=run, results=[result()])
    db_session.commit()

    assert summary.resolved == 1
    assert summary.archived_case_results == 1
    assert IngestionService(db_session).results_for_run(run)[0].test_case_id == case.id


def test_outcome_counts_are_summarised(db_session, run, case):
    summary = IngestionService(db_session).ingest(
        run=run,
        results=[
            result(outcome="passed"),
            result(outcome="failed", test_identifier="t.py::b"),
            result(outcome="skipped", test_identifier="t.py::c"),
        ],
    )
    db_session.commit()

    assert summary.by_outcome == {"passed": 1, "failed": 1, "skipped": 1}


def test_ingesting_into_a_completed_run_is_rejected(db_session, run, case):
    RunService(db_session).complete(run.id)
    db_session.commit()

    with pytest.raises(AppError) as excinfo:
        IngestionService(db_session).ingest(run=run, results=[result()])

    assert excinfo.value.code == "run_already_completed"
    assert excinfo.value.status_code == 409


def test_a_case_key_from_another_project_lands_unresolved(db_session, project, run):
    other = ProjectService(db_session).create(
        key="OTH", name="Other", description=None, actor="local"
    )
    foreign_case = CaseService(db_session).create(
        project=other,
        suite_id=None,
        title="Foreign",
        execution_type="automated",
        priority="p2",
        preconditions=None,
        steps=[],
        expected_result=None,
        tags=[],
        actor="local",
    )
    db_session.commit()

    service = IngestionService(db_session)
    summary = service.ingest(run=run, results=[result(case_key=foreign_case.case_key)])

    assert summary.resolved == 0
    assert summary.unresolved == 1

    (stored,) = service.results_for_run(run)
    assert stored.test_case_id is None
    assert stored.unresolved_case_key == foreign_case.case_key
