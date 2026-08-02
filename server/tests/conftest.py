from collections.abc import Callable, Iterator

import pytest
from fastapi import APIRouter
from sqlalchemy.orm import Session
from starlette.testclient import TestClient
from testforge import models  # noqa: F401
from testforge.config import Settings
from testforge.db.base import Base
from testforge.db.session import create_session_factory
from testforge.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}")


@pytest.fixture
def client_factory(settings) -> Callable[[APIRouter | None], TestClient]:
    clients: list[TestClient] = []

    def build(router: APIRouter | None = None) -> TestClient:
        app = create_app(settings)
        Base.metadata.create_all(app.state.session_factory.kw["bind"])
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


@pytest.fixture
def db_session(settings) -> Iterator[Session]:
    factory = create_session_factory(settings.database_url)
    Base.metadata.create_all(factory.kw["bind"])
    session = factory()
    try:
        yield session
    finally:
        session.close()
