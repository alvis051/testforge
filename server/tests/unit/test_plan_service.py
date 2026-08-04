import pytest
from testforge.errors import AppError
from testforge.services.case_service import CaseService
from testforge.services.plan_service import PlanService
from testforge.services.project_service import ProjectService


@pytest.fixture
def project(db_session):
    project = ProjectService(db_session).create(
        key="CHK", name="Checkout", description=None, actor="local"
    )
    db_session.commit()
    return project


def make_case(session, project, title, **overrides):
    payload = {
        "project": project,
        "suite_id": None,
        "title": title,
        "execution_type": "automated",
        "priority": "p2",
        "preconditions": None,
        "steps": [],
        "expected_result": None,
        "tags": [],
        "actor": "local",
    }
    payload.update(overrides)
    return CaseService(session).create(**payload)


def test_create_snapshots_explicit_case_keys(db_session, project):
    make_case(db_session, project, "A")
    make_case(db_session, project, "B")
    db_session.commit()
    service = PlanService(db_session)

    plan = service.create(
        project=project,
        name="Release 1.0",
        description=None,
        milestone="1.0",
        environment="staging",
        case_keys=["CHK-1"],
        filter_=None,
        actor="alvis",
    )
    db_session.commit()

    assert plan.name == "Release 1.0"
    assert plan.status == "active"
    assert plan.created_by == "alvis"
    assert service.case_count(plan.id) == 1


def test_create_snapshots_from_a_filter(db_session, project):
    make_case(db_session, project, "Smoke A", tags=["smoke"])
    make_case(db_session, project, "Slow B", tags=["slow"])
    db_session.commit()
    service = PlanService(db_session)

    plan = service.create(
        project=project,
        name="Smoke only",
        description=None,
        milestone=None,
        environment=None,
        case_keys=[],
        filter_={"tag": "smoke"},
        actor="local",
    )
    db_session.commit()

    members = service.cases(plan.id)
    assert len(members) == 1


def test_explicit_keys_and_filter_are_unioned_and_deduplicated(db_session, project):
    make_case(db_session, project, "Smoke A", tags=["smoke"])
    make_case(db_session, project, "Manual B", execution_type="manual")
    db_session.commit()
    service = PlanService(db_session)

    plan = service.create(
        project=project,
        name="Union",
        description=None,
        milestone=None,
        environment=None,
        case_keys=["CHK-1", "CHK-2"],
        filter_={"tag": "smoke"},
        actor="local",
    )
    db_session.commit()

    assert service.case_count(plan.id) == 2, "CHK-1 matches both selectors, must appear once"


def test_explicit_keys_keep_their_order_then_filter_matches_follow(db_session, project):
    case_a = make_case(db_session, project, "A", tags=["smoke"])
    case_b = make_case(db_session, project, "B")
    case_c = make_case(db_session, project, "C", tags=["smoke"])
    db_session.commit()
    service = PlanService(db_session)

    plan = service.create(
        project=project,
        name="Ordered",
        description=None,
        milestone=None,
        environment=None,
        case_keys=["CHK-2"],
        filter_={"tag": "smoke"},
        actor="local",
    )
    db_session.commit()

    members = service.cases(plan.id)
    assert [m.test_case_id for m in members] == [case_b.id, case_a.id, case_c.id]


def test_snapshot_pins_the_case_version_and_survives_later_edits(db_session, project):
    make_case(db_session, project, "Coupon")
    db_session.commit()
    service = PlanService(db_session)
    plan = service.create(
        project=project,
        name="Pinned",
        description=None,
        milestone=None,
        environment=None,
        case_keys=["CHK-1"],
        filter_=None,
        actor="local",
    )
    db_session.commit()
    pinned = service.cases(plan.id)[0].test_case_version_id
    assert pinned is not None

    CaseService(db_session).update(
        case_key="CHK-1", expected_version_no=1, actor="editor", title="Coupon SAVE10"
    )
    db_session.commit()

    assert service.cases(plan.id)[0].test_case_version_id == pinned
    assert service.case_count(plan.id) == 1, "editing a case must not change plan membership"


def test_adding_a_matching_case_later_does_not_join_an_existing_plan(db_session, project):
    make_case(db_session, project, "A", tags=["smoke"])
    db_session.commit()
    service = PlanService(db_session)
    plan = service.create(
        project=project,
        name="Frozen",
        description=None,
        milestone=None,
        environment=None,
        case_keys=[],
        filter_={"tag": "smoke"},
        actor="local",
    )
    db_session.commit()

    make_case(db_session, project, "B", tags=["smoke"])
    db_session.commit()

    assert service.case_count(plan.id) == 1, "the snapshot is static, not a live filter"


def test_empty_selection_is_rejected(db_session, project):
    service = PlanService(db_session)

    with pytest.raises(AppError) as excinfo:
        service.create(
            project=project,
            name="Empty",
            description=None,
            milestone=None,
            environment=None,
            case_keys=[],
            filter_={"tag": "nonexistent"},
            actor="local",
        )

    assert excinfo.value.code == "empty_plan_selection"
    assert excinfo.value.status_code == 422


def test_unknown_case_key_is_rejected(db_session, project):
    service = PlanService(db_session)

    with pytest.raises(AppError) as excinfo:
        service.create(
            project=project,
            name="Bad key",
            description=None,
            milestone=None,
            environment=None,
            case_keys=["CHK-999"],
            filter_=None,
            actor="local",
        )

    assert excinfo.value.code == "case_key_not_found"


def test_a_case_from_another_project_is_rejected(db_session, project):
    other = ProjectService(db_session).create(
        key="OTH", name="Other", description=None, actor="local"
    )
    db_session.commit()
    make_case(db_session, other, "Foreign")
    db_session.commit()

    with pytest.raises(AppError) as excinfo:
        PlanService(db_session).create(
            project=project,
            name="Cross-project",
            description=None,
            milestone=None,
            environment=None,
            case_keys=["OTH-1"],
            filter_=None,
            actor="local",
        )

    assert excinfo.value.code == "case_key_not_found"


def test_get_unknown_plan_raises(db_session):
    with pytest.raises(AppError) as excinfo:
        PlanService(db_session).get("missing")
    assert excinfo.value.code == "plan_not_found"
    assert excinfo.value.status_code == 404


def test_archive_marks_the_plan(db_session, project):
    make_case(db_session, project, "A")
    db_session.commit()
    service = PlanService(db_session)
    plan = service.create(
        project=project,
        name="Doomed",
        description=None,
        milestone=None,
        environment=None,
        case_keys=["CHK-1"],
        filter_=None,
        actor="local",
    )
    db_session.commit()

    service.archive(plan.id, actor="alvis")
    db_session.commit()

    assert service.get(plan.id).status == "archived"
    assert service.get(plan.id).archived_at is not None


def test_list_filters_by_milestone(db_session, project):
    make_case(db_session, project, "A")
    db_session.commit()
    service = PlanService(db_session)
    for milestone in ("1.0", "2.0"):
        service.create(
            project=project,
            name=f"Release {milestone}",
            description=None,
            milestone=milestone,
            environment=None,
            case_keys=["CHK-1"],
            filter_=None,
            actor="local",
        )
    db_session.commit()

    found = service.list_for_project(project, milestone="2.0")

    assert [p.milestone for p in found] == ["2.0"]


def test_is_case_in_plan(db_session, project):
    case_a = make_case(db_session, project, "A")
    case_b = make_case(db_session, project, "B")
    db_session.commit()
    service = PlanService(db_session)
    plan = service.create(
        project=project,
        name="Only A",
        description=None,
        milestone=None,
        environment=None,
        case_keys=["CHK-1"],
        filter_=None,
        actor="local",
    )
    db_session.commit()

    assert service.is_case_in_plan(plan.id, case_a.id) is True
    assert service.is_case_in_plan(plan.id, case_b.id) is False
