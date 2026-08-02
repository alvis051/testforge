import pytest
from testforge.errors import AppError
from testforge.services.project_service import ProjectService


def test_create_and_fetch_a_project(db_session):
    service = ProjectService(db_session)
    service.create(key="CHK", name="Checkout", description=None, actor="alvis")
    db_session.commit()

    found = service.get_by_key("CHK")
    assert found.name == "Checkout"
    assert found.case_seq == 0
    assert found.created_by == "alvis"


def test_get_by_key_raises_project_not_found(db_session):
    with pytest.raises(AppError) as excinfo:
        ProjectService(db_session).get_by_key("NOPE")
    assert excinfo.value.code == "project_not_found"
    assert excinfo.value.status_code == 404


def test_allocate_case_key_increments_monotonically(db_session):
    service = ProjectService(db_session)
    project = service.create(key="CHK", name="Checkout", description=None, actor="local")
    db_session.commit()

    keys = [service.allocate_case_key(project) for _ in range(3)]

    assert keys == ["CHK-1", "CHK-2", "CHK-3"]
    db_session.commit()
    assert service.get_by_key("CHK").case_seq == 3
