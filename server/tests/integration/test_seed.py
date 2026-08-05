from testforge.seed import seed_demo
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


def test_seed_creates_a_completed_run_with_mixed_results(db_session):
    counts = seed_demo(db_session)
    db_session.commit()

    assert counts["plans"] == 1
    assert counts["runs"] == 1
    assert counts["results"] >= 5

    project = ProjectService(db_session).get_by_key("CHK")
    runs = RunService(db_session).list_for_project(project)
    assert len(runs) == 1
    assert runs[0].status == "completed"
    assert runs[0].plan_id is not None


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
    }
