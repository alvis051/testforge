from datetime import timedelta

import pytest
from testforge.db.base import utcnow
from testforge.errors import AppError
from testforge.models.run_job import RunJob
from testforge.services.case_service import CaseService
from testforge.services.plan_service import PlanService
from testforge.services.project_service import ProjectService
from testforge.services.run_service import RunService
from testforge.services.runner_service import MAX_ATTEMPTS, RunnerService


@pytest.fixture
def project(db_session):
    project = ProjectService(db_session).create(
        key="CHK", name="Checkout", description=None, actor="local"
    )
    project.repo_url = "https://example.test/repo.git"
    project.default_ref = "main"
    project.test_command = "pytest -q"
    db_session.flush()
    return project


def make_case(db_session, project, title, execution_type):
    return CaseService(db_session).create(
        project=project,
        suite_id=None,
        title=title,
        execution_type=execution_type,
        priority="p2",
        preconditions=None,
        steps=[],
        expected_result=None,
        tags=[],
        actor="local",
    )


@pytest.fixture
def plan(db_session, project):
    make_case(db_session, project, "Automated one", "automated")
    make_case(db_session, project, "Manual one", "manual")
    make_case(db_session, project, "Automated two", "automated")
    db_session.flush()
    return PlanService(db_session).create(
        project=project,
        name="Smoke",
        description=None,
        milestone=None,
        environment=None,
        case_keys=["CHK-1", "CHK-2", "CHK-3"],
        filter_=None,
        actor="local",
    )


def job_for(db_session, run):
    return db_session.query(RunJob).filter(RunJob.run_id == run.id).one()


def test_dispatch_queues_a_run_with_only_the_automated_case_keys(db_session, project, plan):
    run = RunnerService(db_session).dispatch(plan=plan, git_ref=None, name=None, actor="local")

    assert run.status == "queued"
    assert run.source == "runner"
    assert run.plan_id == plan.id
    job = job_for(db_session, run)
    assert job.case_keys_json == ["CHK-1", "CHK-3"], "the manual case must not be dispatched"
    assert job.git_ref == "main", "no override falls back to the project's default_ref"
    assert job.command == "pytest -q"
    assert job.attempts == 0


def test_dispatch_honours_a_git_ref_override(db_session, project, plan):
    run = RunnerService(db_session).dispatch(
        plan=plan, git_ref="feature-x", name=None, actor="local"
    )

    assert job_for(db_session, run).git_ref == "feature-x"


def test_dispatch_refuses_a_project_with_no_runner_config(db_session, project, plan):
    project.test_command = None
    db_session.flush()

    with pytest.raises(AppError) as exc:
        RunnerService(db_session).dispatch(plan=plan, git_ref=None, name=None, actor="local")

    assert exc.value.code == "runner_not_configured_for_project"
    assert exc.value.status_code == 409


def test_dispatch_refuses_a_plan_with_no_automated_cases(db_session, project):
    make_case(db_session, project, "Manual only", "manual")
    db_session.flush()
    manual_plan = PlanService(db_session).create(
        project=project,
        name="Manual",
        description=None,
        milestone=None,
        environment=None,
        case_keys=["CHK-1"],
        filter_=None,
        actor="local",
    )

    with pytest.raises(AppError) as exc:
        RunnerService(db_session).dispatch(plan=manual_plan, git_ref=None, name=None, actor="local")

    assert exc.value.code == "plan_has_no_automated_cases"


def test_claim_returns_none_when_the_queue_is_empty(db_session, project):
    assert RunnerService(db_session).claim("worker-a") is None


def test_claim_takes_the_job_and_starts_its_run(db_session, project, plan):
    service = RunnerService(db_session)
    run = service.dispatch(plan=plan, git_ref=None, name=None, actor="local")

    job = service.claim("worker-a")

    assert job is not None
    assert job.run_id == run.id
    assert job.status == "running"
    assert job.claimed_by == "worker-a"
    assert job.attempts == 1
    assert job.lease_expires_at is not None
    assert RunService(db_session).get(run.id).status == "running"


def test_a_second_claim_finds_no_work_rather_than_duplicating_the_job(db_session, project, plan):
    service = RunnerService(db_session)
    run = service.dispatch(plan=plan, git_ref=None, name=None, actor="local")

    first = service.claim("worker-a")
    second = service.claim("worker-b")

    assert first is not None
    assert second is None, "the status guard on the UPDATE is what prevents double execution"
    assert job_for(db_session, run).attempts == 1


def test_sweep_requeues_a_job_whose_worker_stopped_reporting(db_session, project, plan):
    service = RunnerService(db_session)
    run = service.dispatch(plan=plan, git_ref=None, name=None, actor="local")
    job = service.claim("worker-a")
    job.lease_expires_at = utcnow() - timedelta(seconds=1)
    db_session.flush()

    assert service.sweep_expired() == 1

    assert job_for(db_session, run).status == "queued"
    assert job_for(db_session, run).claimed_by is None
    assert RunService(db_session).get(run.id).status == "queued"


def test_sweep_gives_up_and_errors_the_run_at_the_attempt_limit(db_session, project, plan):
    service = RunnerService(db_session)
    run = service.dispatch(plan=plan, git_ref=None, name=None, actor="local")

    for _ in range(MAX_ATTEMPTS):
        job = service.claim("worker-a")
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
        db_session.flush()
        service.sweep_expired()

    final = job_for(db_session, run)
    assert final.status == "failed"
    assert final.attempts == MAX_ATTEMPTS
    assert "lease expired" in final.error
    assert RunService(db_session).get(run.id).status == "errored"
    assert RunService(db_session).get(run.id).completed_at is not None
