from testforge.seed import seed_demo
from testforge.services.analytics_service import AnalyticsService
from testforge.services.case_service import CaseService
from testforge.services.ingestion_service import IngestionService
from testforge.services.project_service import ProjectService
from testforge.services.run_service import RunService


def test_seed_creates_a_browsable_demo_project(db_session):
    counts = seed_demo(db_session)
    db_session.commit()

    assert counts["projects"] == 1
    assert counts["cases"] >= 8

    project = ProjectService(db_session).get_by_key("CHK")
    cases = CaseService(db_session).list_for_project(project)
    assert any(case.execution_type == "manual" for case in cases)
    assert any(case.execution_type == "automated" for case in cases)


def test_seed_is_idempotent(db_session):
    seed_demo(db_session)
    db_session.commit()
    counts = seed_demo(db_session)
    db_session.commit()

    assert counts["projects"] == 0, "re-seeding must not duplicate the demo project"


def test_seed_creates_a_history_of_completed_runs(db_session):
    counts = seed_demo(db_session)
    db_session.commit()

    assert counts["plans"] == 1
    assert counts["runs"] == 6
    assert counts["results"] == 36

    project = ProjectService(db_session).get_by_key("CHK")
    runs = RunService(db_session).list_for_project(project)
    assert len(runs) == 6
    assert all(run.status == "completed" for run in runs)
    assert all(run.plan_id is not None for run in runs)
    assert runs[0].started_at > runs[-1].started_at, "list_for_project is newest-first"


def test_seeded_run_has_a_failure_a_skip_and_an_unresolved_result(db_session):
    seed_demo(db_session)
    db_session.commit()

    project = ProjectService(db_session).get_by_key("CHK")
    run = RunService(db_session).list_for_project(project)[0]
    results = IngestionService(db_session).results_for_run(run)

    outcomes = {r.outcome for r in results}
    assert "passed" in outcomes
    assert "failed" in outcomes
    assert "skipped" in outcomes

    failed = next(r for r in results if r.outcome == "failed")
    assert failed.failure_message, "a demo failure with no message shows an empty dashboard panel"
    assert failed.stack_trace

    unresolved = [r for r in results if r.test_case_id is None]
    assert len(unresolved) == 1
    assert unresolved[0].unresolved_case_key is not None


def test_seed_is_still_idempotent_including_the_run(db_session):
    seed_demo(db_session)
    db_session.commit()
    counts = seed_demo(db_session)
    db_session.commit()

    assert counts == {
        "projects": 0,
        "suites": 0,
        "cases": 0,
        "plans": 0,
        "runs": 0,
        "results": 0,
        "jobs": 0,
    }


def test_the_seeded_history_distinguishes_a_flaky_case_from_a_broken_one(db_session):
    seed_demo(db_session)
    db_session.commit()
    project = ProjectService(db_session).get_by_key("CHK")

    flaky = AnalyticsService(db_session).flaky_cases(project)

    keys = [case.case_key for case in flaky]
    assert "CHK-1" in keys, "CHK-1 recovers on its own and must be flagged"
    assert "CHK-3" not in keys, (
        "CHK-3 breaks once and stays broken — a regression, and the demo exists "
        "to show that the dashboard tells the two apart"
    )


def test_the_seeded_history_exercises_several_failure_categories(db_session):
    seed_demo(db_session)
    db_session.commit()
    project = ProjectService(db_session).get_by_key("CHK")

    categories = {c.category for c in AnalyticsService(db_session).failure_categories(project)}

    assert {"assertion", "timeout", "connection"} <= categories
    assert "uncategorized" in categories, (
        "one failure deliberately falls through, so the bucket is visible in the demo"
    )


def test_the_seed_configures_the_runner_and_leaves_a_finished_job(db_session):
    from testforge.models.run_job import RunJob
    from testforge.seed import DEMO_REPO_URL, DEMO_TEST_COMMAND

    counts = seed_demo(db_session)
    db_session.commit()

    assert counts["jobs"] == 1
    project = ProjectService(db_session).get_by_key("CHK")
    assert project.repo_url == DEMO_REPO_URL
    assert project.test_command == DEMO_TEST_COMMAND

    newest = RunService(db_session).list_for_project(project)[0]
    assert newest.source == "runner", "the newest seeded run demonstrates the runner"

    job = db_session.query(RunJob).filter(RunJob.run_id == newest.id).one()
    assert job.status == "succeeded"
    assert job.resolved_sha is not None
    assert job.output_tail
