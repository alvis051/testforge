import pytest
from testforge.errors import AppError
from testforge.services.case_service import CaseService
from testforge.services.project_service import ProjectService


@pytest.fixture
def project(db_session):
    project = ProjectService(db_session).create(
        key="CHK", name="Checkout", description=None, actor="local"
    )
    db_session.commit()
    return project


def make_case(session, project, **overrides):
    payload = {
        "project": project,
        "suite_id": None,
        "title": "Coupon applies at checkout",
        "execution_type": "automated",
        "priority": "p1",
        "preconditions": None,
        "steps": [],
        "expected_result": "Discount is applied",
        "tags": [],
        "actor": "alvis",
    }
    payload.update(overrides)
    return CaseService(session).create(**payload)


def test_create_allocates_a_readable_key_and_writes_version_one(db_session, project):
    case = make_case(db_session, project)
    db_session.commit()

    assert case.case_key == "CHK-1"
    assert case.current_version_no == 1

    versions = CaseService(db_session).versions("CHK-1")
    assert len(versions) == 1
    assert versions[0].version_no == 1
    assert versions[0].title == "Coupon applies at checkout"
    assert versions[0].created_by == "alvis"


def test_update_appends_exactly_one_snapshot_and_bumps_the_version(db_session, project):
    make_case(db_session, project)
    db_session.commit()
    service = CaseService(db_session)

    service.update(
        case_key="CHK-1",
        expected_version_no=1,
        actor="reviewer",
        title="Coupon SAVE10 applies at checkout",
        change_note="clarify which coupon",
    )
    db_session.commit()

    case = service.get_by_key("CHK-1")
    versions = service.versions("CHK-1")

    assert case.title == "Coupon SAVE10 applies at checkout"
    assert case.current_version_no == 2
    assert [v.version_no for v in versions] == [1, 2]
    assert versions[0].title == "Coupon applies at checkout", "v1 must stay immutable"
    assert versions[1].change_note == "clarify which coupon"


def test_stale_expected_version_raises_case_version_conflict(db_session, project):
    make_case(db_session, project)
    db_session.commit()
    service = CaseService(db_session)

    service.update(case_key="CHK-1", expected_version_no=1, actor="a", title="First edit")
    db_session.commit()

    with pytest.raises(AppError) as excinfo:
        service.update(case_key="CHK-1", expected_version_no=1, actor="b", title="Stale edit")

    assert excinfo.value.code == "case_version_conflict"
    assert excinfo.value.status_code == 409
    assert excinfo.value.details == {"case_key": "CHK-1", "current_version_no": 2}


def test_retagging_does_not_create_a_version(db_session, project):
    make_case(db_session, project, tags=["smoke"])
    db_session.commit()
    service = CaseService(db_session)

    service.set_tags("CHK-1", ["smoke", "regression"])
    db_session.commit()

    assert service.current_version_count("CHK-1") == 1
    assert sorted(t.name for t in service.get_by_key("CHK-1").tags) == ["regression", "smoke"]


def test_unknown_case_key_raises(db_session):
    with pytest.raises(AppError) as excinfo:
        CaseService(db_session).get_by_key("CHK-404")
    assert excinfo.value.code == "case_key_not_found"


def test_archive_sets_archived_at_without_deleting(db_session, project):
    make_case(db_session, project)
    db_session.commit()
    service = CaseService(db_session)

    service.archive("CHK-1", actor="alvis")
    db_session.commit()

    assert service.get_by_key("CHK-1").archived_at is not None


def test_list_filters_by_execution_type_and_search(db_session, project):
    make_case(db_session, project, title="Coupon applies", execution_type="automated")
    make_case(db_session, project, title="Refund flow", execution_type="manual")
    db_session.commit()
    service = CaseService(db_session)

    automated = service.list_for_project(project, execution_type="automated")
    searched = service.list_for_project(project, q="refund")

    assert [c.title for c in automated] == ["Coupon applies"]
    assert [c.title for c in searched] == ["Refund flow"]
