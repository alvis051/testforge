from fastapi import APIRouter, Depends

from testforge.api.deps import get_actor
from testforge.errors import AppError


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
