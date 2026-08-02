import pytest
from fastapi import APIRouter, Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from testforge.api.deps import get_actor, get_session
from testforge.errors import AppError
from testforge.models.suite import Suite


def test_app_error_renders_the_standard_body(client_factory):
    router = APIRouter()

    @router.get("/boom")
    def boom():
        raise AppError("project_not_found", "no project WAT", 404, {"key": "WAT"})

    client = client_factory(router)
    response = client.get("/boom")

    assert response.status_code == 404
    assert response.json() == {
        "code": "project_not_found",
        "message": "no project WAT",
        "details": {"key": "WAT"},
    }


def test_request_validation_uses_the_same_body_shape(client_factory):
    router = APIRouter()

    @router.get("/needs-int")
    def needs_int(count: int):
        return {"count": count}

    client = client_factory(router)
    response = client.get("/needs-int", params={"count": "abc"})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "invalid_result_payload"
    assert set(body) == {"code", "message", "details"}


def test_actor_defaults_to_local_and_honours_the_header(client_factory):
    router = APIRouter()

    @router.get("/whoami")
    def whoami(actor: str = Depends(get_actor)):
        return {"actor": actor}

    client = client_factory(router)
    assert client.get("/whoami").json() == {"actor": "local"}
    assert client.get("/whoami", headers={"X-Actor": "alvis"}).json() == {"actor": "alvis"}


def test_integrity_error_maps_to_a_clean_409_conflict(client_factory):
    router = APIRouter()

    @router.get("/racy")
    def racy():
        raise IntegrityError("INSERT ...", {}, Exception("UNIQUE constraint failed"))

    client = client_factory(router)
    response = client.get("/racy")

    assert response.status_code == 409
    assert response.json() == {
        "code": "conflict",
        "message": "the request conflicts with existing data",
        "details": {},
    }


def test_dangling_foreign_key_integrity_error_is_not_swallowed_as_a_conflict(client_factory):
    """A NOT NULL/FK violation is a server bug, not a client-fixable conflict.

    Unlike the unique-constraint case above, this must NOT come back as a clean
    409 -- it should propagate as an unhandled exception (an honest 500), since the
    client cannot do anything to make a dangling foreign key resolve itself.
    """
    router = APIRouter()

    @router.get("/dangling-fk")
    def dangling_fk(session: Session = Depends(get_session)):
        # SQLite foreign-key enforcement is on (see db/session.py), so inserting a
        # suite that points at a project which doesn't exist raises a genuine
        # IntegrityError from the DBAPI layer -- not a manufactured one.
        session.add(Suite(project_id="does-not-exist", name="orphan"))
        session.flush()
        return {"ok": True}

    client = client_factory(router)

    with pytest.raises(IntegrityError) as excinfo:
        client.get("/dangling-fk")

    assert "UNIQUE constraint" not in str(getattr(excinfo.value, "orig", excinfo.value))
