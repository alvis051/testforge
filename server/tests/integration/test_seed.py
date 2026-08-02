from testforge.seed import seed_demo
from testforge.services.case_service import CaseService
from testforge.services.project_service import ProjectService


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
