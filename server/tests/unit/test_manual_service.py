import pytest
from sqlalchemy import select

from testforge.errors import AppError
from testforge.models.run import Result
from testforge.services.case_service import CaseService
from testforge.services.manual_service import ManualExecutionService
from testforge.services.plan_service import PlanService
from testforge.services.project_service import ProjectService
from testforge.services.run_service import RunService


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
        execution_type="manual",
        priority="p2",
        preconditions=None,
        steps=[{"action": "Open cart", "expected": "Cart renders"}],
        expected_result=None,
        tags=[],
        actor="local",
    )


@pytest.fixture
def adhoc_run(db_session, project):
    run, _ = RunService(db_session).open(
        project=project, external_id="m-1", name="n", source="manual", ci_metadata={}, actor="local"
    )
    db_session.commit()
    return run


def results_for(session, run):
    return list(session.scalars(select(Result).where(Result.run_id == run.id)))


def test_record_writes_a_result_row(db_session, project, adhoc_run):
    make_case(db_session, project, "Coupon")
    db_session.commit()

    result = ManualExecutionService(db_session).record(
        run=adhoc_run,
        case_key="CHK-1",
        outcome="passed",
        notes="looked fine",
        duration_ms=45000,
        actor="alvis",
    )
    db_session.commit()

    assert result.outcome == "passed"
    assert result.framework == "manual"
    assert result.test_identifier == "CHK-1"
    assert result.failure_message == "looked fine"
    assert result.duration_ms == 45000
    assert result.test_case_id is not None
    assert result.automation_link_id is None


def test_record_pins_the_case_version_at_execution_time(db_session, project, adhoc_run):
    make_case(db_session, project, "Coupon")
    db_session.commit()
    service = ManualExecutionService(db_session)

    result = service.record(
        run=adhoc_run, case_key="CHK-1", outcome="passed", notes=None, duration_ms=None, actor="a"
    )
    db_session.commit()
    pinned = result.test_case_version_id
    assert pinned is not None

    CaseService(db_session).update(
        case_key="CHK-1", expected_version_no=1, actor="editor", title="Coupon SAVE10"
    )
    db_session.commit()

    assert results_for(db_session, adhoc_run)[0].test_case_version_id == pinned


def test_notes_are_kept_on_a_passing_outcome(db_session, project, adhoc_run):
    make_case(db_session, project, "Coupon")
    db_session.commit()

    result = ManualExecutionService(db_session).record(
        run=adhoc_run,
        case_key="CHK-1",
        outcome="passed",
        notes="minor cosmetic wobble, not worth failing",
        duration_ms=None,
        actor="a",
    )
    db_session.commit()

    assert result.failure_message == "minor cosmetic wobble, not worth failing"


def test_re_executing_the_same_case_replaces_the_prior_result(db_session, project, adhoc_run):
    make_case(db_session, project, "Coupon")
    db_session.commit()
    service = ManualExecutionService(db_session)

    service.record(
        run=adhoc_run,
        case_key="CHK-1",
        outcome="failed",
        notes="mis-click",
        duration_ms=None,
        actor="a",
    )
    db_session.commit()
    service.record(
        run=adhoc_run,
        case_key="CHK-1",
        outcome="passed",
        notes="actually fine",
        duration_ms=None,
        actor="a",
    )
    db_session.commit()

    stored = results_for(db_session, adhoc_run)
    assert len(stored) == 1, "a correction must replace, not append"
    assert stored[0].outcome == "passed"
    assert stored[0].failure_message == "actually fine"


def test_unknown_case_key_is_rejected(db_session, adhoc_run):
    with pytest.raises(AppError) as excinfo:
        ManualExecutionService(db_session).record(
            run=adhoc_run,
            case_key="CHK-999",
            outcome="passed",
            notes=None,
            duration_ms=None,
            actor="a",
        )

    assert excinfo.value.code == "case_key_not_found", (
        "unlike ingestion, manual execution does not tolerate unresolved keys"
    )


def test_a_case_outside_the_runs_plan_is_rejected(db_session, project):
    make_case(db_session, project, "In plan")
    make_case(db_session, project, "Out of plan")
    db_session.commit()
    plan = PlanService(db_session).create(
        project=project,
        name="P",
        description=None,
        milestone=None,
        environment=None,
        case_keys=["CHK-1"],
        filter_=None,
        actor="local",
    )
    db_session.commit()
    run = RunService(db_session).open_for_plan(
        plan=plan, name=None, external_id=None, actor="local"
    )
    db_session.commit()
    service = ManualExecutionService(db_session)

    service.record(
        run=run, case_key="CHK-1", outcome="passed", notes=None, duration_ms=None, actor="a"
    )

    with pytest.raises(AppError) as excinfo:
        service.record(
            run=run, case_key="CHK-2", outcome="passed", notes=None, duration_ms=None, actor="a"
        )

    assert excinfo.value.code == "case_not_in_plan"
    assert excinfo.value.status_code == 404


def test_a_terminal_run_rejects_manual_results(db_session, project, adhoc_run):
    make_case(db_session, project, "Coupon")
    db_session.commit()
    RunService(db_session).cancel(adhoc_run.id)
    db_session.commit()

    with pytest.raises(AppError) as excinfo:
        ManualExecutionService(db_session).record(
            run=adhoc_run,
            case_key="CHK-1",
            outcome="passed",
            notes=None,
            duration_ms=None,
            actor="a",
        )

    assert excinfo.value.code == "run_canceled"
