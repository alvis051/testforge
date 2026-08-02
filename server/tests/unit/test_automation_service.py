from datetime import UTC, datetime

import pytest
from testforge.services.automation_service import AutomationService
from testforge.services.case_service import CaseService
from testforge.services.project_service import ProjectService


@pytest.fixture
def case(db_session):
    project = ProjectService(db_session).create(
        key="CHK", name="Checkout", description=None, actor="local"
    )
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


def test_bind_creates_a_link(db_session, case):
    seen = datetime(2026, 8, 1, tzinfo=UTC)
    link = AutomationService(db_session).bind(
        case=case, framework="pytest", test_identifier="tests/t.py::test_a", seen_at=seen
    )
    db_session.commit()

    assert link.test_case_id == case.id
    assert link.first_seen_at == seen
    assert link.active is True


def test_bind_is_idempotent_and_refreshes_last_seen(db_session, case):
    service = AutomationService(db_session)
    first_seen = datetime(2026, 8, 1, tzinfo=UTC)
    later = datetime(2026, 8, 2, tzinfo=UTC)

    first = service.bind(
        case=case, framework="pytest", test_identifier="tests/t.py::test_a", seen_at=first_seen
    )
    db_session.commit()
    second = service.bind(
        case=case, framework="pytest", test_identifier="tests/t.py::test_a", seen_at=later
    )
    db_session.commit()

    assert second.id == first.id
    assert second.first_seen_at == first_seen
    assert second.last_seen_at == later


def test_one_case_can_have_many_automated_tests(db_session, case):
    service = AutomationService(db_session)
    seen = datetime(2026, 8, 1, tzinfo=UTC)
    service.bind(case=case, framework="pytest", test_identifier="t.py::test[web]", seen_at=seen)
    service.bind(case=case, framework="pytest", test_identifier="t.py::test[ios]", seen_at=seen)
    db_session.commit()

    assert len(service.links_for_case(case)) == 2
