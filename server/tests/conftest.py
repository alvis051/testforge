from collections.abc import Callable, Iterator

import pytest
from fastapi import APIRouter
from starlette.testclient import TestClient

from testforge.config import Settings
from testforge.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}")


@pytest.fixture
def client_factory(settings) -> Callable[[APIRouter | None], TestClient]:
    clients: list[TestClient] = []

    def build(router: APIRouter | None = None) -> TestClient:
        app = create_app(settings)
        if router is not None:
            app.include_router(router)
        client = TestClient(app)
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.close()


@pytest.fixture
def client(client_factory) -> Iterator[TestClient]:
    yield client_factory()
