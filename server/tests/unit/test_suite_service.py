import pytest
from testforge.errors import AppError
from testforge.services.project_service import ProjectService
from testforge.services.suite_service import SuiteService


@pytest.fixture
def project(db_session):
    project = ProjectService(db_session).create(
        key="CHK", name="Checkout", description=None, actor="local"
    )
    db_session.commit()
    return project


def test_root_suite_has_root_path(db_session, project):
    suite = SuiteService(db_session).create(
        project=project, name="Smoke", parent_id=None, actor="local"
    )
    assert suite.path == "/"
    assert suite.parent_id is None


def test_child_path_contains_the_parent_id(db_session, project):
    service = SuiteService(db_session)
    parent = service.create(project=project, name="Web", parent_id=None, actor="local")
    child = service.create(project=project, name="Coupons", parent_id=parent.id, actor="local")

    assert child.path == f"/{parent.id}/"


def test_move_rewrites_descendant_paths(db_session, project):
    service = SuiteService(db_session)
    web = service.create(project=project, name="Web", parent_id=None, actor="local")
    coupons = service.create(project=project, name="Coupons", parent_id=web.id, actor="local")
    edge = service.create(project=project, name="Edge", parent_id=coupons.id, actor="local")
    mobile = service.create(project=project, name="Mobile", parent_id=None, actor="local")
    db_session.commit()

    service.move(coupons.id, mobile.id)
    db_session.commit()

    db_session.refresh(coupons)
    db_session.refresh(edge)
    assert coupons.path == f"/{mobile.id}/"
    assert edge.path == f"/{mobile.id}/{coupons.id}/"


def test_move_into_own_descendant_raises_suite_cycle(db_session, project):
    service = SuiteService(db_session)
    web = service.create(project=project, name="Web", parent_id=None, actor="local")
    coupons = service.create(project=project, name="Coupons", parent_id=web.id, actor="local")
    db_session.commit()

    with pytest.raises(AppError) as excinfo:
        service.move(web.id, coupons.id)

    assert excinfo.value.code == "suite_cycle"
    db_session.rollback()
    db_session.refresh(web)
    assert web.path == "/", "a rejected move must leave the tree untouched"


def test_move_into_itself_raises_suite_cycle(db_session, project):
    service = SuiteService(db_session)
    web = service.create(project=project, name="Web", parent_id=None, actor="local")
    db_session.commit()

    with pytest.raises(AppError) as excinfo:
        service.move(web.id, web.id)
    assert excinfo.value.code == "suite_cycle"
