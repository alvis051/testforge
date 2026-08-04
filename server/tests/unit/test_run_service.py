import pytest
from testforge.errors import AppError
from testforge.services.project_service import ProjectService
from testforge.services.run_service import RunService


@pytest.fixture
def project(db_session):
    project = ProjectService(db_session).create(
        key="CHK", name="Checkout", description=None, actor="local"
    )
    db_session.commit()
    return project


def test_open_creates_a_running_run(db_session, project):
    run, created = RunService(db_session).open(
        project=project,
        external_id="gha-1",
        name="nightly",
        source="ci",
        ci_metadata={"branch": "main"},
        actor="ci-bot",
    )
    db_session.commit()

    assert created is True
    assert run.status == "running"
    assert run.ci_metadata_json == {"branch": "main"}
    assert run.created_by == "ci-bot"


def test_open_is_idempotent_on_external_id(db_session, project):
    service = RunService(db_session)
    first, first_created = service.open(
        project=project, external_id="gha-1", name="a", source="ci", ci_metadata={}, actor="local"
    )
    db_session.commit()

    second, second_created = service.open(
        project=project, external_id="gha-1", name="b", source="ci", ci_metadata={}, actor="local"
    )
    db_session.commit()

    assert second_created is False
    assert second.id == first.id
    assert second.name == "a", "the repeat must not overwrite the original run"


def test_complete_sets_status_and_timestamp(db_session, project):
    service = RunService(db_session)
    run, _ = service.open(
        project=project, external_id="gha-1", name="a", source="ci", ci_metadata={}, actor="local"
    )
    db_session.commit()

    completed = service.complete(run.id)
    db_session.commit()

    assert completed.status == "completed"
    assert completed.completed_at is not None


def test_get_unknown_run_raises(db_session):
    with pytest.raises(AppError) as excinfo:
        RunService(db_session).get("missing")
    assert excinfo.value.code == "run_not_found"


def test_cancel_sets_status_and_timestamp(db_session, project):
    service = RunService(db_session)
    run, _ = service.open(
        project=project, external_id="c-1", name="a", source="ci", ci_metadata={}, actor="local"
    )
    db_session.commit()

    canceled = service.cancel(run.id)
    db_session.commit()

    assert canceled.status == "canceled"
    assert canceled.completed_at is not None


def test_assert_writable_allows_a_running_run(db_session, project):
    service = RunService(db_session)
    run, _ = service.open(
        project=project, external_id="w-1", name="a", source="ci", ci_metadata={}, actor="local"
    )
    db_session.commit()

    service.assert_writable(run)  # must not raise


def test_assert_writable_rejects_a_completed_run(db_session, project):
    service = RunService(db_session)
    run, _ = service.open(
        project=project, external_id="w-2", name="a", source="ci", ci_metadata={}, actor="local"
    )
    service.complete(run.id)
    db_session.commit()

    with pytest.raises(AppError) as excinfo:
        service.assert_writable(run)

    assert excinfo.value.code == "run_already_completed"
    assert excinfo.value.status_code == 409


def test_assert_writable_rejects_a_canceled_run_with_its_own_code(db_session, project):
    service = RunService(db_session)
    run, _ = service.open(
        project=project, external_id="w-3", name="a", source="ci", ci_metadata={}, actor="local"
    )
    service.cancel(run.id)
    db_session.commit()

    with pytest.raises(AppError) as excinfo:
        service.assert_writable(run)

    assert excinfo.value.code == "run_canceled", (
        "a canceled run must be distinguishable from a completed one"
    )
    assert excinfo.value.status_code == 409


def test_open_for_plan_links_the_run_to_the_plan(db_session, project):
    from testforge.services.case_service import CaseService
    from testforge.services.plan_service import PlanService

    CaseService(db_session).create(
        project=project,
        suite_id=None,
        title="A",
        execution_type="manual",
        priority="p2",
        preconditions=None,
        steps=[],
        expected_result=None,
        tags=[],
        actor="local",
    )
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
        plan=plan, name="exec 1", external_id=None, actor="alvis"
    )
    db_session.commit()

    assert run.plan_id == plan.id
    assert run.status == "running"
    assert run.source == "manual"
    assert run.project_id == plan.project_id
    assert run.external_id, "a plan run still needs a unique external_id"


def test_open_for_plan_rejects_an_archived_plan(db_session, project):
    from testforge.services.case_service import CaseService
    from testforge.services.plan_service import PlanService

    CaseService(db_session).create(
        project=project,
        suite_id=None,
        title="A",
        execution_type="manual",
        priority="p2",
        preconditions=None,
        steps=[],
        expected_result=None,
        tags=[],
        actor="local",
    )
    db_session.commit()
    plans = PlanService(db_session)
    plan = plans.create(
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
    plans.archive(plan.id, actor="local")
    db_session.commit()

    with pytest.raises(AppError) as excinfo:
        RunService(db_session).open_for_plan(plan=plan, name=None, external_id=None, actor="local")

    assert excinfo.value.code == "plan_archived"
    assert excinfo.value.status_code == 409
