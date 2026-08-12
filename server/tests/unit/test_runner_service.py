from datetime import timedelta

import pytest
from sqlalchemy import event
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


def test_claim_update_re_checks_status_on_the_row_it_targets(db_session, project, plan):
    """Proves the outer ``AND status == "queued"`` guard on claim()'s UPDATE is
    load-bearing -- not merely that the inner subquery finds no more queued rows.

    Why not race two real workers instead? We tried, extensively: sequential
    calls on one session, real OS threads racing separate connections with a
    ``threading.Barrier``, and a low-level ``sqlite3.Connection.set_progress_handler``
    injection to force a second connection's write into the middle of the
    first's UPDATE. All of it converges on the same result -- confirmed here
    with the progress-handler probe -- that SQLite acquires the write-intent
    lock for this whole single-statement UPDATE (subquery included) up front:
    a second connection's own write attempt fails with "database is locked"
    from that statement's very first instruction. The subquery's read and the
    outer guard's re-check are therefore always evaluated against one atomic,
    unchanging snapshot in this backend -- there is no sequence of calls, on
    however many sessions, that can make them disagree. The guard's payoff is
    real on an MVCC backend like Postgres (a second worker's statement can
    resume against a row that changed underneath it after a lock wait), but
    SQLite's coarser locking makes that exact window unreachable by any
    behavioral test here.

    So this test verifies the guard the only way that actually distinguishes
    "guard present" from "guard absent" in this suite: it captures the literal
    SQL claim() sends to the database during a real call and asserts the outer
    UPDATE carries its own ``status = 'queued'`` condition, separate from and
    in addition to the one inside the id-selecting subquery. Delete the outer
    condition -- ``.where(RunJob.id == oldest, RunJob.status == "queued")``
    down to ``.where(RunJob.id == oldest)`` -- and this assertion fails
    immediately, because the text of the outer WHERE clause changes.
    """
    service = RunnerService(db_session)
    service.dispatch(plan=plan, git_ref=None, name=None, actor="local")

    captured: list[str] = []

    def record_update(conn, cursor, statement, parameters, context, executemany):
        if "UPDATE run_jobs" in statement:
            captured.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record_update)
    try:
        job = service.claim("worker-a")
    finally:
        event.remove(engine, "before_cursor_execute", record_update)

    assert job is not None
    assert len(captured) == 1, "expected exactly one UPDATE run_jobs statement from claim()"

    # Isolate the outer WHERE clause from the id-selecting subquery's own WHERE:
    # the first "WHERE" starts the outer clause (which contains the whole
    # subquery), and the subquery's own closing paren marks where the outer
    # condition, if any, picks back up.
    where_clause = captured[0].split("WHERE", 1)[1]
    subquery_part, _, outer_part = where_clause.partition(")")

    assert "status" in subquery_part, (
        "sanity check: the subquery that picks the oldest queued row must filter "
        "by status -- if this fails, the statement shape changed and the split "
        "below is no longer isolating the right clause"
    )
    assert "status" in outer_part, (
        "the outer UPDATE has no status condition of its own -- without one, the "
        "UPDATE matches its target purely by id, regardless of whether that row's "
        "status changed between the subquery picking it and the write landing"
    )


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
