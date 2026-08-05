from starlette.testclient import TestClient
from testforge.config import Settings
from testforge.main import create_app


def build_dist(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>TestForge</title>")
    assets = dist / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('app');")
    return dist


def test_api_routes_still_win_over_the_static_mount(tmp_path):
    dist = build_dist(tmp_path)
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", frontend_dist=str(dist))
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json() == {"status": "ok", "service": "testforge"}


def test_index_is_served_at_the_root(tmp_path):
    dist = build_dist(tmp_path)
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", frontend_dist=str(dist))
    with TestClient(create_app(settings)) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "TestForge" in response.text


def test_a_client_side_route_falls_back_to_index(tmp_path):
    dist = build_dist(tmp_path)
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", frontend_dist=str(dist))
    with TestClient(create_app(settings)) as client:
        response = client.get("/runs/some-run-id")

    assert response.status_code == 200, "React Router owns this path; a hard refresh must not 404"
    assert "TestForge" in response.text


def test_an_unknown_api_path_still_404s_as_json(tmp_path):
    dist = build_dist(tmp_path)
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", frontend_dist=str(dist))
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/nope")

    assert response.status_code == 404
    assert "TestForge" not in response.text, (
        "an unknown API path must not silently return the SPA shell"
    )


def test_the_app_starts_fine_with_no_frontend_built(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", frontend_dist=None)
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
