import pytest
from starlette.testclient import TestClient
from testforge.main import create_app


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    with TestClient(app) as c:
        yield c
