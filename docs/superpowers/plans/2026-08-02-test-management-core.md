# Test Management Core (Slice 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the core test-management domain (projects, suites, versioned test cases, tags) plus a thin result-ingestion path, so that a `@pytest.mark.case("CHK-42")` marker on a real test produces a stored result bound to that case and to the case version current at execution time.

**Architecture:** A monolith FastAPI service layered `routes → services → models`, in a uv workspace with two packages: `testforge` (server + CLI) and `pytest-testforge` (the reporting plugin, installable standalone into client repos). Every result — from the plugin, from JUnit import, and later from the S4 runner — flows through one `IngestionService.ingest()` path. The CLI speaks HTTP, never the database.

**Tech Stack:** Python 3.12+, uv, FastAPI, Pydantic v2, SQLAlchemy 2.x, Alembic, SQLite (dev), httpx, typer, pytest, ruff.

Source spec: `docs/superpowers/specs/2026-08-02-test-management-core-design.md`

## Global Constraints

- Python `>=3.12`. All dependency management via `uv add` / `uv add --dev`; never hand-edit `[project.dependencies]`.
- All commands are run from the **repository root** unless stated otherwise.
- Primary keys are UUID strings generated in Python (`testforge.ids.new_id()`), never database sequences.
- Schema must stay Postgres-compatible: use `sqlalchemy.JSON`, `String(n)` with explicit lengths, and `DateTime(timezone=True)`. No SQLite-only types or pragmas.
- Alembic migrations exist from the first table onward. Every task that adds or changes a model also adds a migration.
- All timestamps are timezone-aware UTC.
- Test fixtures use `starlette.testclient.TestClient` (not `httpx.Client` + `httpx.ASGITransport` directly — `ASGITransport` in the installed `httpx` is async-only and has no `handle_request`). `TestClient` is itself an `httpx.Client` subclass. The `httpx2` package is a required dev dependency so `TestClient` runs without the `StarletteDeprecationWarning` that installed `starlette` emits otherwise. This does not affect the CLI (Task 12) or the pytest plugin (Task 10), both of which use plain `httpx.Client` against real network URLs, never `ASGITransport`.
- Every error response body is exactly `{"code": str, "message": str, "details": object}`.
- Error codes are the fixed set: `project_not_found`, `duplicate_project_key`, `case_key_not_found`, `duplicate_case_key`, `suite_cycle`, `case_version_conflict`, `invalid_result_payload`, `run_already_completed`, `suite_not_found`, `run_not_found`.
- Every write endpoint accepts an `X-Actor` header, defaulting to the string `local`.
- Cases are addressable by readable key (`/api/cases/CHK-42`); `case_key` is globally unique.
- Provisional names: package `testforge`, CLI `tf`, plugin `pytest-testforge`.

## File Structure

```
pyproject.toml                              uv workspace root (virtual)
.github/workflows/ci.yml                    lint + tests
Makefile                                    dev shortcuts
README.md

server/
  pyproject.toml                            package: testforge
  alembic.ini
  src/testforge/
    __init__.py
    config.py                               Settings (database_url)
    ids.py                                  new_id()
    errors.py                               AppError + FastAPI handlers
    main.py                                 create_app()
    db/
      base.py                               Base, TimestampMixin
      session.py                            engine/session factory
      migrations/                           alembic env.py + versions/
    models/
      __init__.py                           imports all models (metadata completeness)
      project.py  suite.py  case.py  automation.py  run.py
    schemas/
      common.py    ErrorBody, Step
      projects.py  suites.py  cases.py  runs.py
      results.py   ResultIn, ResultBatch, IngestSummary   ← shared contract
    services/
      project_service.py  suite_service.py  case_service.py
      automation_service.py  run_service.py  ingestion_service.py
      junit.py                              JUnit XML → list[ResultIn]
    api/
      deps.py                               get_session, get_actor
      routes/
        health.py  projects.py  suites.py  cases.py  runs.py
    cli/
      __init__.py                           typer app
      client.py                             httpx API client
  tests/
    conftest.py
    unit/  integration/  contract/  acceptance/

plugin/
  pyproject.toml                            package: pytest-testforge
  src/pytest_testforge/
    __init__.py
    plugin.py
  tests/test_plugin.py
```

Split rationale: each service owns exactly one table group and is the only writer to it. `ingestion_service.py` is the sole cross-module coordinator, and it calls other services rather than their tables — which is what lets S4's runner reuse it unchanged.

---

### Task 1: Repository scaffold, health endpoint, CI

**Files:**
- Create: `pyproject.toml`, `server/pyproject.toml`, `plugin/pyproject.toml`
- Create: `server/src/testforge/__init__.py`, `server/src/testforge/config.py`, `server/src/testforge/main.py`
- Create: `server/src/testforge/api/__init__.py`, `server/src/testforge/api/routes/__init__.py`, `server/src/testforge/api/routes/health.py`
- Create: `plugin/src/pytest_testforge/__init__.py`
- Create: `.github/workflows/ci.yml`, `Makefile`, `.gitignore`
- Test: `server/tests/conftest.py`, `server/tests/integration/test_health.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `create_app(settings: Settings | None = None) -> FastAPI`; `Settings` with attribute `database_url: str`; `get_settings() -> Settings`.

- [ ] **Step 1: Initialize the repository and workspace root**

```bash
git init
mkdir -p server/src/testforge/api/routes server/tests/integration plugin/src/pytest_testforge plugin/tests .github/workflows
```

Create `pyproject.toml`:

```toml
[project]
name = "testforge-workspace"
version = "0.0.0"
requires-python = ">=3.12"
dependencies = []

[tool.uv]
package = false

[tool.uv.workspace]
members = ["server", "plugin"]

[tool.uv.sources]
testforge = { workspace = true }
pytest-testforge = { workspace = true }

[dependency-groups]
dev = ["testforge", "pytest-testforge"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.pytest.ini_options]
testpaths = ["server/tests", "plugin/tests"]
```

Create `.gitignore`:

```
__pycache__/
*.py[cod]
.venv/
.env
*.db
.pytest_cache/
.ruff_cache/
```

Create the repo-root `conftest.py`. Tasks 10 and 11 need pytest's `pytester` fixture, and pytest 8 only honours `pytest_plugins` in the **rootdir** conftest — declaring it in `server/tests/conftest.py` or `plugin/tests/conftest.py` is an error:

```python
pytest_plugins = ["pytester"]
```

- [ ] **Step 2: Create the two member packages**

Create `server/pyproject.toml`:

```toml
[project]
name = "testforge"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn>=0.32",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "sqlalchemy>=2.0",
    "alembic>=1.14",
    "httpx>=0.28",
    "typer>=0.15",
]

[project.scripts]
tf = "testforge.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/testforge"]
```

Create `plugin/pyproject.toml`:

```toml
[project]
name = "pytest-testforge"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["pytest>=8.0", "httpx>=0.28"]

[project.entry-points.pytest11]
testforge = "pytest_testforge.plugin"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/pytest_testforge"]
```

Create empty `server/src/testforge/__init__.py`, `server/src/testforge/api/__init__.py`, `server/src/testforge/api/routes/__init__.py`, and `plugin/src/pytest_testforge/__init__.py`.

- [ ] **Step 3: Install the toolchain**

```bash
uv sync && uv add --dev pytest ruff httpx2
```

`httpx2` is a dev dependency needed only so `starlette.testclient.TestClient` (used in every test fixture from this task onward) runs without a deprecation warning — see Global Constraints.

Expected: a `.venv/` is created and `uv.lock` is written.

- [ ] **Step 4: Write the failing test**

Create `server/tests/conftest.py`:

```python
import pytest
from starlette.testclient import TestClient

from testforge.main import create_app


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    with TestClient(app) as c:
        yield c
```

Create `server/tests/integration/test_health.py`:

```python
def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "testforge"}
```

- [ ] **Step 5: Run the test to verify it fails**

Run: `uv run pytest server/tests/integration/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'testforge.main'`

- [ ] **Step 6: Write the minimal implementation**

Create `server/src/testforge/config.py`:

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TESTFORGE_", env_file=".env")

    database_url: str = "sqlite:///./testforge.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

Create `server/src/testforge/api/routes/health.py`:

```python
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "testforge"}
```

Create `server/src/testforge/main.py`:

```python
from fastapi import FastAPI

from testforge.api.routes import health
from testforge.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="TestForge", version="0.1.0")
    app.state.settings = settings or get_settings()
    app.include_router(health.router)
    return app
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `uv run pytest server/tests/integration/test_health.py -v`
Expected: PASS — 1 passed

- [ ] **Step 8: Add CI and the Makefile**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      - run: uv sync --all-extras --dev
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run pytest -v
```

Create `Makefile`:

```makefile
.PHONY: install dev test lint fmt

install:
	uv sync

dev:
	uv run uvicorn testforge.main:create_app --factory --reload

test:
	uv run pytest -v

lint:
	uv run ruff check .

fmt:
	uv run ruff format .
```

- [ ] **Step 9: Verify lint and the full suite pass**

Run: `uv run ruff format . && uv run ruff check . && uv run pytest -v`
Expected: ruff reports no errors; 1 test passes.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "feat: scaffold uv workspace, health endpoint, and CI"
```

---

### Task 2: Database foundation and Alembic

**Files:**
- Create: `server/src/testforge/ids.py`, `server/src/testforge/db/__init__.py`, `server/src/testforge/db/base.py`, `server/src/testforge/db/session.py`
- Create: `server/alembic.ini`, `server/src/testforge/db/migrations/env.py`, `server/src/testforge/db/migrations/script.py.mako`
- Create: `server/src/testforge/models/__init__.py`
- Test: `server/tests/unit/test_ids.py`, `server/tests/integration/test_migrations.py`

**Interfaces:**
- Consumes: `Settings.database_url` (Task 1).
- Produces: `new_id() -> str`; `Base` (DeclarativeBase); `TimestampMixin` with columns `created_at`/`updated_at`; `create_session_factory(database_url: str) -> sessionmaker[Session]`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/unit/test_ids.py`:

```python
from uuid import UUID

from testforge.ids import new_id


def test_new_id_is_a_unique_uuid_string():
    first, second = new_id(), new_id()
    assert first != second
    assert str(UUID(first)) == first
```

Create `server/tests/integration/test_migrations.py`:

```python
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

from testforge.db.base import Base
from testforge import models  # noqa: F401  ensures all tables register on Base.metadata

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def test_migrations_produce_the_schema_the_models_declare(tmp_path):
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)

    command.upgrade(config, "head")

    engine = create_engine(url)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        diff = compare_metadata(context, Base.metadata)

    assert diff == [], f"models and migrations disagree: {diff}"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest server/tests/unit/test_ids.py server/tests/integration/test_migrations.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'testforge.ids'`

- [ ] **Step 3: Write the database foundation**

Create `server/src/testforge/ids.py`:

```python
from uuid import uuid4


def new_id() -> str:
    return str(uuid4())
```

Create empty `server/src/testforge/db/__init__.py`.

Create `server/src/testforge/db/base.py`:

```python
from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
```

Create `server/src/testforge/db/session.py`:

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def create_session_factory(database_url: str) -> sessionmaker[Session]:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args, future=True)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)
```

Create `server/src/testforge/models/__init__.py`:

```python
"""Importing this module registers every table on ``Base.metadata``."""
```

- [ ] **Step 4: Initialize Alembic**

```bash
uv run alembic init server/src/testforge/db/migrations
mv alembic.ini server/alembic.ini
```

Edit `server/alembic.ini` so the script location is repo-root relative and the URL is a placeholder overridden at runtime:

```ini
script_location = server/src/testforge/db/migrations
sqlalchemy.url = sqlite:///./testforge.db
```

Replace the generated `server/src/testforge/db/migrations/env.py` with:

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from testforge import models  # noqa: F401  registers all tables
from testforge.config import get_settings
from testforge.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

if not config.get_main_option("sqlalchemy.url", "").strip():
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

`render_as_batch=True` matters: SQLite cannot `ALTER TABLE` in place, and batch mode makes later column changes portable.

- [ ] **Step 5: Create the empty baseline migration**

```bash
uv run alembic -c server/alembic.ini revision -m "baseline"
```

Expected: a new file under `server/src/testforge/db/migrations/versions/` with empty `upgrade()`/`downgrade()`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest server/tests/unit/test_ids.py server/tests/integration/test_migrations.py -v`
Expected: PASS — 2 passed. (With no models yet, `compare_metadata` returns an empty diff.)

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: add SQLAlchemy base, session factory, and Alembic with a schema-parity test"
```

---

### Task 3: Error contract and actor header

**Files:**
- Create: `server/src/testforge/errors.py`, `server/src/testforge/api/deps.py`, `server/src/testforge/schemas/__init__.py`, `server/src/testforge/schemas/common.py`
- Modify: `server/src/testforge/main.py`
- Test: `server/tests/unit/test_errors.py`, `server/tests/integration/test_error_contract.py`

**Interfaces:**
- Consumes: `create_app()` (Task 1), `create_session_factory()` (Task 2).
- Produces: `AppError(code: str, message: str, status_code: int = 400, details: dict | None = None)`; `register_error_handlers(app: FastAPI) -> None`; `get_session() -> Iterator[Session]` FastAPI dependency; `get_actor(...) -> str`; Pydantic models `ErrorBody`, `Step`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/unit/test_errors.py`:

```python
from testforge.errors import AppError


def test_app_error_carries_code_status_and_details():
    error = AppError("case_key_not_found", "no such case", 404, {"case_key": "CHK-9"})
    assert error.code == "case_key_not_found"
    assert error.status_code == 404
    assert error.details == {"case_key": "CHK-9"}
    assert str(error) == "no such case"


def test_app_error_defaults():
    error = AppError("duplicate_case_key", "already exists")
    assert error.status_code == 400
    assert error.details == {}
```

Create `server/tests/integration/test_error_contract.py`:

```python
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
```

Add the `client_factory` fixture to `server/tests/conftest.py` (replacing the file written in Task 1):

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest server/tests/unit/test_errors.py server/tests/integration/test_error_contract.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'testforge.errors'`

- [ ] **Step 3: Write the error contract**

Create `server/src/testforge/errors.py`:

```python
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    """A domain failure that maps onto the platform's stable error contract."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    def handle_app_error(_: Request, error: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"code": error.code, "message": error.message, "details": error.details},
        )

    @app.exception_handler(RequestValidationError)
    def handle_validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "invalid_result_payload",
                "message": "request payload failed validation",
                "details": {"errors": jsonable(error.errors())},
            },
        )


def jsonable(errors: list[dict]) -> list[dict]:
    """Validation errors can carry exception objects in ``ctx``; make them serialisable."""
    cleaned = []
    for item in errors:
        entry = {k: v for k, v in item.items() if k != "ctx"}
        entry["loc"] = [str(part) for part in item.get("loc", ())]
        cleaned.append(entry)
    return cleaned
```

Create `server/src/testforge/schemas/__init__.py` (empty) and `server/src/testforge/schemas/common.py`:

```python
from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)


class Step(BaseModel):
    action: str
    expected: str = ""
```

Create `server/src/testforge/api/deps.py`:

```python
from collections.abc import Iterator

from fastapi import Header, Request
from sqlalchemy.orm import Session


def get_session(request: Request) -> Iterator[Session]:
    factory = request.app.state.session_factory
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_actor(x_actor: str | None = Header(default=None)) -> str:
    return x_actor or "local"
```

- [ ] **Step 4: Wire it into the app factory**

Replace `server/src/testforge/main.py`:

```python
from fastapi import FastAPI

from testforge.api.routes import health
from testforge.config import Settings, get_settings
from testforge.db.session import create_session_factory
from testforge.errors import register_error_handlers


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    app = FastAPI(title="TestForge", version="0.1.0")
    app.state.settings = resolved
    app.state.session_factory = create_session_factory(resolved.database_url)
    register_error_handlers(app)
    app.include_router(health.router)
    return app
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest server/tests -v`
Expected: PASS — 7 passed (health, 2 ids/migrations, 2 error unit, 3 error contract).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: add stable error contract, session dependency, and actor header"
```

---

### Task 4: Projects and case-key allocation

**Files:**
- Create: `server/src/testforge/models/project.py`, `server/src/testforge/services/__init__.py`, `server/src/testforge/services/project_service.py`, `server/src/testforge/schemas/projects.py`, `server/src/testforge/api/routes/projects.py`
- Modify: `server/src/testforge/models/__init__.py`, `server/src/testforge/main.py`
- Test: `server/tests/unit/test_project_service.py`, `server/tests/integration/test_projects_api.py`

**Interfaces:**
- Consumes: `Base`, `TimestampMixin`, `new_id()`, `AppError`, `get_session`, `get_actor`.
- Produces: model `Project` (`id`, `key`, `name`, `description`, `case_seq`); `ProjectService(session)` with `create(*, key, name, description, actor) -> Project`, `get_by_key(key) -> Project`, `list() -> list[Project]`, `allocate_case_key(project) -> str`; `db_session` fixture.

- [ ] **Step 1: Write the failing tests**

Add a `db_session` fixture to `server/tests/conftest.py`:

```python
from sqlalchemy.orm import Session

from testforge import models  # noqa: F401
from testforge.db.base import Base
from testforge.db.session import create_session_factory


@pytest.fixture
def db_session(settings) -> Iterator[Session]:
    factory = create_session_factory(settings.database_url)
    Base.metadata.create_all(factory.kw["bind"])
    session = factory()
    try:
        yield session
    finally:
        session.close()
```

Create `server/tests/unit/test_project_service.py`:

```python
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
```

Create `server/tests/integration/test_projects_api.py`:

```python
def test_create_and_read_a_project(client):
    created = client.post(
        "/api/projects",
        json={"key": "CHK", "name": "Checkout", "description": "storefront checkout"},
        headers={"X-Actor": "alvis"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["key"] == "CHK"
    assert body["created_by"] == "alvis"

    fetched = client.get("/api/projects/CHK")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Checkout"


def test_unknown_project_returns_the_error_contract(client):
    response = client.get("/api/projects/NOPE")
    assert response.status_code == 404
    assert response.json()["code"] == "project_not_found"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest server/tests/unit/test_project_service.py server/tests/integration/test_projects_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'testforge.services.project_service'`

- [ ] **Step 3: Write the model**

Create `server/src/testforge/models/project.py`:

```python
from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from testforge.db.base import Base, TimestampMixin
from testforge.ids import new_id


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    case_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False, default="local")
```

Update `server/src/testforge/models/__init__.py`:

```python
"""Importing this module registers every table on ``Base.metadata``."""

from testforge.models.project import Project

__all__ = ["Project"]
```

- [ ] **Step 4: Write the service**

Create empty `server/src/testforge/services/__init__.py`.

Create `server/src/testforge/services/project_service.py`:

```python
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from testforge.errors import AppError
from testforge.models.project import Project


class ProjectService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, key: str, name: str, description: str | None, actor: str) -> Project:
        existing = self.session.scalar(select(Project).where(Project.key == key))
        if existing is not None:
            raise AppError(
                "duplicate_project_key", f"project key {key} already exists", 409, {"key": key}
            )
        project = Project(key=key, name=name, description=description, created_by=actor)
        self.session.add(project)
        self.session.flush()
        return project

    def get_by_key(self, key: str) -> Project:
        project = self.session.scalar(select(Project).where(Project.key == key))
        if project is None:
            raise AppError("project_not_found", f"no project with key {key}", 404, {"key": key})
        return project

    def list(self) -> list[Project]:
        return list(self.session.scalars(select(Project).order_by(Project.key)))

    def allocate_case_key(self, project: Project) -> str:
        """Atomically reserve the next case number for this project."""
        next_seq = self.session.scalar(
            update(Project)
            .where(Project.id == project.id)
            .values(case_seq=Project.case_seq + 1)
            .returning(Project.case_seq)
        )
        self.session.refresh(project)
        return f"{project.key}-{next_seq}"
```

The `UPDATE ... RETURNING` is what makes allocation atomic — two concurrent creates cannot receive the same number, on SQLite 3.35+ and on Postgres alike.

- [ ] **Step 5: Write the schemas and routes**

Create `server/src/testforge/schemas/projects.py`:

```python
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    key: str = Field(min_length=1, max_length=16, pattern=r"^[A-Z][A-Z0-9]*$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    key: str
    name: str
    description: str | None
    case_seq: int
    created_by: str
    created_at: datetime
```

Create `server/src/testforge/api/routes/projects.py`:

```python
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from testforge.api.deps import get_actor, get_session
from testforge.schemas.projects import ProjectCreate, ProjectOut
from testforge.services.project_service import ProjectService

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(
    payload: ProjectCreate,
    session: Session = Depends(get_session),
    actor: str = Depends(get_actor),
) -> ProjectOut:
    project = ProjectService(session).create(
        key=payload.key, name=payload.name, description=payload.description, actor=actor
    )
    return ProjectOut.model_validate(project)


@router.get("", response_model=list[ProjectOut])
def list_projects(session: Session = Depends(get_session)) -> list[ProjectOut]:
    return [ProjectOut.model_validate(p) for p in ProjectService(session).list()]


@router.get("/{project_key}", response_model=ProjectOut)
def get_project(project_key: str, session: Session = Depends(get_session)) -> ProjectOut:
    return ProjectOut.model_validate(ProjectService(session).get_by_key(project_key))
```

Register it in `server/src/testforge/main.py` by adding `projects` to the import and one include line:

```python
from testforge.api.routes import health, projects
...
    app.include_router(health.router)
    app.include_router(projects.router)
```

- [ ] **Step 6: Generate the migration**

```bash
uv run alembic -c server/alembic.ini revision --autogenerate -m "add projects"
```

Expected: a new version file creating the `projects` table.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest server/tests -v`
Expected: PASS — all tests pass, including `test_migrations_produce_the_schema_the_models_declare`.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: add projects with atomic case-key allocation"
```

---

### Task 5: Suite tree

**Files:**
- Create: `server/src/testforge/models/suite.py`, `server/src/testforge/services/suite_service.py`, `server/src/testforge/schemas/suites.py`, `server/src/testforge/api/routes/suites.py`
- Modify: `server/src/testforge/models/__init__.py`, `server/src/testforge/main.py`
- Test: `server/tests/unit/test_suite_service.py`, `server/tests/integration/test_suites_api.py`

**Interfaces:**
- Consumes: `Project`, `ProjectService`, `AppError`.
- Produces: model `Suite` (`id`, `project_id`, `parent_id`, `name`, `path`, `position`); `SuiteService(session)` with `create(*, project, name, parent_id, actor) -> Suite`, `get(suite_id) -> Suite`, `move(suite_id, new_parent_id) -> Suite`, `list_for_project(project) -> list[Suite]`.

`path` is a materialized ancestor chain of the form `/` for a root suite and `/<parent_id>/<grandparent-inclusive chain>/` for nested suites — specifically, `child.path = parent.path + parent.id + "/"`. Subtree queries become a `LIKE` prefix match, and cycle detection is a substring test.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/unit/test_suite_service.py`:

```python
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
```

Create `server/tests/integration/test_suites_api.py`:

```python
import pytest


@pytest.fixture
def project_key(client):
    client.post("/api/projects", json={"key": "CHK", "name": "Checkout"})
    return "CHK"


def test_create_and_list_suites(client, project_key):
    created = client.post(
        f"/api/projects/{project_key}/suites", json={"name": "Web", "parent_id": None}
    )
    assert created.status_code == 201
    parent_id = created.json()["id"]

    client.post(
        f"/api/projects/{project_key}/suites", json={"name": "Coupons", "parent_id": parent_id}
    )

    listed = client.get(f"/api/projects/{project_key}/suites")
    assert listed.status_code == 200
    names = sorted(item["name"] for item in listed.json())
    assert names == ["Coupons", "Web"]


def test_cyclic_move_is_rejected(client, project_key):
    web = client.post(f"/api/projects/{project_key}/suites", json={"name": "Web"}).json()
    coupons = client.post(
        f"/api/projects/{project_key}/suites", json={"name": "Coupons", "parent_id": web["id"]}
    ).json()

    response = client.patch(
        f"/api/suites/{web['id']}", json={"parent_id": coupons["id"], "move": True}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "suite_cycle"


def test_rename_without_move_keeps_the_parent(client, project_key):
    web = client.post(f"/api/projects/{project_key}/suites", json={"name": "Web"}).json()
    coupons = client.post(
        f"/api/projects/{project_key}/suites", json={"name": "Coupons", "parent_id": web["id"]}
    ).json()

    response = client.patch(f"/api/suites/{coupons['id']}", json={"name": "Discounts"})

    assert response.status_code == 200
    assert response.json()["name"] == "Discounts"
    assert response.json()["parent_id"] == web["id"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest server/tests/unit/test_suite_service.py server/tests/integration/test_suites_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'testforge.services.suite_service'`

- [ ] **Step 3: Write the model**

Create `server/src/testforge/models/suite.py`:

```python
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from testforge.db.base import Base, TimestampMixin
from testforge.ids import new_id


class Suite(Base, TimestampMixin):
    __tablename__ = "suites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    parent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("suites.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    path: Mapped[str] = mapped_column(String(2000), nullable=False, default="/", index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False, default="local")
```

Update `server/src/testforge/models/__init__.py`:

```python
"""Importing this module registers every table on ``Base.metadata``."""

from testforge.models.project import Project
from testforge.models.suite import Suite

__all__ = ["Project", "Suite"]
```

- [ ] **Step 4: Write the service**

Create `server/src/testforge/services/suite_service.py`:

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from testforge.errors import AppError
from testforge.models.project import Project
from testforge.models.suite import Suite


class SuiteService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, project: Project, name: str, parent_id: str | None, actor: str) -> Suite:
        path = "/"
        if parent_id is not None:
            parent = self.get(parent_id)
            path = f"{parent.path}{parent.id}/"
        suite = Suite(
            project_id=project.id, parent_id=parent_id, name=name, path=path, created_by=actor
        )
        self.session.add(suite)
        self.session.flush()
        return suite

    def get(self, suite_id: str) -> Suite:
        suite = self.session.get(Suite, suite_id)
        if suite is None:
            raise AppError("suite_not_found", f"no suite {suite_id}", 404, {"suite_id": suite_id})
        return suite

    def list_for_project(self, project: Project) -> list[Suite]:
        stmt = (
            select(Suite).where(Suite.project_id == project.id).order_by(Suite.path, Suite.position)
        )
        return list(self.session.scalars(stmt))

    def move(self, suite_id: str, new_parent_id: str | None) -> Suite:
        suite = self.get(suite_id)
        old_prefix = f"{suite.path}{suite.id}/"

        if new_parent_id is None:
            new_path = "/"
        else:
            if new_parent_id == suite_id:
                raise AppError(
                    "suite_cycle", "a suite cannot be its own parent", 409, {"suite_id": suite_id}
                )
            parent = self.get(new_parent_id)
            if parent.path.startswith(old_prefix) or parent.path == old_prefix:
                raise AppError(
                    "suite_cycle",
                    "cannot move a suite into its own descendant",
                    409,
                    {"suite_id": suite_id, "new_parent_id": new_parent_id},
                )
            new_path = f"{parent.path}{parent.id}/"

        descendants = list(
            self.session.scalars(select(Suite).where(Suite.path.startswith(old_prefix)))
        )
        suite.parent_id = new_parent_id
        suite.path = new_path
        new_prefix = f"{new_path}{suite.id}/"
        for descendant in descendants:
            descendant.path = new_prefix + descendant.path[len(old_prefix) :]
        self.session.flush()
        return suite
```

Cycle detection happens before any mutation, so a rejected move leaves the tree byte-identical.

- [ ] **Step 5: Write the schemas and routes**

Create `server/src/testforge/schemas/suites.py`:

```python
from pydantic import BaseModel, ConfigDict, Field


class SuiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: str | None = None
    description: str | None = None


class SuiteUpdate(BaseModel):
    name: str | None = None
    parent_id: str | None = None
    # ``parent_id=None`` legitimately means "move to root", so a separate flag is needed to
    # distinguish that from "rename only". Defaults to False so a rename never moves a suite.
    move: bool = False


class SuiteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    parent_id: str | None
    name: str
    path: str
    position: int
```

Create `server/src/testforge/api/routes/suites.py`:

```python
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from testforge.api.deps import get_actor, get_session
from testforge.schemas.suites import SuiteCreate, SuiteOut, SuiteUpdate
from testforge.services.project_service import ProjectService
from testforge.services.suite_service import SuiteService

router = APIRouter(tags=["suites"])


@router.post("/api/projects/{project_key}/suites", response_model=SuiteOut, status_code=201)
def create_suite(
    project_key: str,
    payload: SuiteCreate,
    session: Session = Depends(get_session),
    actor: str = Depends(get_actor),
) -> SuiteOut:
    project = ProjectService(session).get_by_key(project_key)
    suite = SuiteService(session).create(
        project=project, name=payload.name, parent_id=payload.parent_id, actor=actor
    )
    return SuiteOut.model_validate(suite)


@router.get("/api/projects/{project_key}/suites", response_model=list[SuiteOut])
def list_suites(project_key: str, session: Session = Depends(get_session)) -> list[SuiteOut]:
    project = ProjectService(session).get_by_key(project_key)
    return [SuiteOut.model_validate(s) for s in SuiteService(session).list_for_project(project)]


@router.patch("/api/suites/{suite_id}", response_model=SuiteOut)
def update_suite(
    suite_id: str, payload: SuiteUpdate, session: Session = Depends(get_session)
) -> SuiteOut:
    service = SuiteService(session)
    suite = service.get(suite_id)
    if payload.name is not None:
        suite.name = payload.name
    if payload.move:
        suite = service.move(suite_id, payload.parent_id)
    return SuiteOut.model_validate(suite)
```

Add `suites` to the route imports and `app.include_router(suites.router)` in `server/src/testforge/main.py`.

- [ ] **Step 6: Generate the migration**

```bash
uv run alembic -c server/alembic.ini revision --autogenerate -m "add suites"
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest server/tests -v`
Expected: PASS — all tests pass, including the schema-parity test.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: add suite tree with materialized paths and cycle-checked moves"
```

---

### Task 6: Test cases with version history

This is the heart of the slice. The invariant under test: **every update appends exactly one immutable snapshot and bumps `current_version_no`, in one transaction.**

**Files:**
- Create: `server/src/testforge/models/case.py`, `server/src/testforge/services/case_service.py`, `server/src/testforge/schemas/cases.py`, `server/src/testforge/api/routes/cases.py`
- Modify: `server/src/testforge/models/__init__.py`, `server/src/testforge/main.py`
- Test: `server/tests/unit/test_case_service.py`, `server/tests/integration/test_cases_api.py`

**Interfaces:**
- Consumes: `Project`, `ProjectService.allocate_case_key`, `SuiteService`, `AppError`, `Step`.
- Produces: models `TestCase`, `TestCaseVersion`, `Tag`, `test_case_tags`; `CaseService(session)` with `create(...) -> TestCase`, `get_by_key(case_key) -> TestCase`, `update(*, case_key, expected_version_no, actor, **fields) -> TestCase`, `list_for_project(project, *, suite_id, tag, status, execution_type, q) -> list[TestCase]`, `archive(case_key, actor) -> TestCase`, `versions(case_key) -> list[TestCaseVersion]`.

- [ ] **Step 1: Write the failing unit tests**

Create `server/tests/unit/test_case_service.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest server/tests/unit/test_case_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'testforge.services.case_service'`

- [ ] **Step 3: Write the models**

Create `server/src/testforge/models/case.py`:

```python
from datetime import datetime

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from testforge.db.base import Base, TimestampMixin
from testforge.ids import new_id

test_case_tags = Table(
    "test_case_tags",
    Base.metadata,
    Column("case_id", String(36), ForeignKey("test_cases.id"), primary_key=True),
    Column("tag_id", String(36), ForeignKey("tags.id"), primary_key=True),
)


class Tag(Base, TimestampMixin):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_tags_project_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)


class TestCase(Base, TimestampMixin):
    __tablename__ = "test_cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    suite_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("suites.id"), nullable=True, index=True
    )
    case_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    execution_type: Mapped[str] = mapped_column(String(20), nullable=False, default="automated")
    priority: Mapped[str] = mapped_column(String(4), nullable=False, default="p2")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    preconditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    expected_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False, default="local")
    updated_by: Mapped[str] = mapped_column(String(200), nullable=False, default="local")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tags: Mapped[list[Tag]] = relationship(secondary=test_case_tags, lazy="selectin")


class TestCaseVersion(Base):
    __tablename__ = "test_case_versions"
    __table_args__ = (UniqueConstraint("test_case_id", "version_no", name="uq_case_version_no"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    test_case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("test_cases.id"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    execution_type: Mapped[str] = mapped_column(String(20), nullable=False)
    priority: Mapped[str] = mapped_column(String(4), nullable=False)
    preconditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    expected_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False, default="local")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

Update `server/src/testforge/models/__init__.py`:

```python
"""Importing this module registers every table on ``Base.metadata``."""

from testforge.models.case import Tag, TestCase, TestCaseVersion, test_case_tags
from testforge.models.project import Project
from testforge.models.suite import Suite

__all__ = ["Project", "Suite", "Tag", "TestCase", "TestCaseVersion", "test_case_tags"]
```

- [ ] **Step 4: Write the service**

Create `server/src/testforge/services/case_service.py`:

```python
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from testforge.db.base import utcnow
from testforge.errors import AppError
from testforge.models.case import Tag, TestCase, TestCaseVersion
from testforge.models.project import Project
from testforge.services.project_service import ProjectService

VERSIONED_FIELDS = (
    "title",
    "execution_type",
    "priority",
    "preconditions",
    "steps_json",
    "expected_result",
)


class CaseService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        project: Project,
        suite_id: str | None,
        title: str,
        execution_type: str,
        priority: str,
        preconditions: str | None,
        steps: list[dict[str, Any]],
        expected_result: str | None,
        tags: list[str],
        actor: str,
    ) -> TestCase:
        case_key = ProjectService(self.session).allocate_case_key(project)
        case = TestCase(
            project_id=project.id,
            suite_id=suite_id,
            case_key=case_key,
            title=title,
            execution_type=execution_type,
            priority=priority,
            preconditions=preconditions,
            steps_json=steps,
            expected_result=expected_result,
            current_version_no=1,
            created_by=actor,
            updated_by=actor,
        )
        case.tags = [self._tag(project.id, name) for name in tags]
        self.session.add(case)
        self.session.flush()
        self._snapshot(case, change_note="initial version", actor=actor)
        return case

    def get_by_key(self, case_key: str) -> TestCase:
        case = self.session.scalar(select(TestCase).where(TestCase.case_key == case_key))
        if case is None:
            raise AppError("case_key_not_found", f"no case {case_key}", 404, {"case_key": case_key})
        return case

    def find_by_key(self, case_key: str) -> TestCase | None:
        """Lookup that returns ``None`` instead of raising — used by ingestion."""
        return self.session.scalar(select(TestCase).where(TestCase.case_key == case_key))

    def update(
        self,
        *,
        case_key: str,
        expected_version_no: int,
        actor: str,
        change_note: str | None = None,
        **fields: Any,
    ) -> TestCase:
        case = self.get_by_key(case_key)
        if case.current_version_no != expected_version_no:
            raise AppError(
                "case_version_conflict",
                f"case {case_key} has moved on to version {case.current_version_no}",
                409,
                {"case_key": case_key, "current_version_no": case.current_version_no},
            )
        for name, value in fields.items():
            if name not in VERSIONED_FIELDS and name not in {"suite_id", "status", "owner"}:
                raise AppError("invalid_result_payload", f"unknown case field {name}", 422)
            if value is not None:
                setattr(case, name, value)
        case.current_version_no += 1
        case.updated_by = actor
        self.session.flush()
        self._snapshot(case, change_note=change_note, actor=actor)
        return case

    def set_tags(self, case_key: str, tag_names: list[str]) -> TestCase:
        """Tags are organisational metadata, not test content: no version bump."""
        case = self.get_by_key(case_key)
        case.tags = [self._tag(case.project_id, name) for name in tag_names]
        self.session.flush()
        return case

    def archive(self, case_key: str, actor: str) -> TestCase:
        case = self.get_by_key(case_key)
        case.archived_at = utcnow()
        case.status = "deprecated"
        case.updated_by = actor
        self.session.flush()
        return case

    def versions(self, case_key: str) -> list[TestCaseVersion]:
        case = self.get_by_key(case_key)
        return list(
            self.session.scalars(
                select(TestCaseVersion)
                .where(TestCaseVersion.test_case_id == case.id)
                .order_by(TestCaseVersion.version_no)
            )
        )

    def current_version_count(self, case_key: str) -> int:
        case = self.get_by_key(case_key)
        return self.session.scalar(
            select(func.count())
            .select_from(TestCaseVersion)
            .where(TestCaseVersion.test_case_id == case.id)
        )

    def list_for_project(
        self,
        project: Project,
        *,
        suite_id: str | None = None,
        tag: str | None = None,
        status: str | None = None,
        execution_type: str | None = None,
        q: str | None = None,
    ) -> list[TestCase]:
        stmt = select(TestCase).where(TestCase.project_id == project.id)
        if suite_id is not None:
            stmt = stmt.where(TestCase.suite_id == suite_id)
        if status is not None:
            stmt = stmt.where(TestCase.status == status)
        if execution_type is not None:
            stmt = stmt.where(TestCase.execution_type == execution_type)
        if q is not None:
            stmt = stmt.where(func.lower(TestCase.title).contains(q.lower()))
        if tag is not None:
            stmt = stmt.where(TestCase.tags.any(Tag.name == tag))
        return list(self.session.scalars(stmt.order_by(TestCase.case_key)))

    def _tag(self, project_id: str, name: str) -> Tag:
        tag = self.session.scalar(select(Tag).where(Tag.project_id == project_id, Tag.name == name))
        if tag is None:
            tag = Tag(project_id=project_id, name=name)
            self.session.add(tag)
            self.session.flush()
        return tag

    def _snapshot(self, case: TestCase, *, change_note: str | None, actor: str) -> None:
        self.session.add(
            TestCaseVersion(
                test_case_id=case.id,
                version_no=case.current_version_no,
                title=case.title,
                execution_type=case.execution_type,
                priority=case.priority,
                preconditions=case.preconditions,
                steps_json=case.steps_json,
                expected_result=case.expected_result,
                change_note=change_note,
                created_by=actor,
                created_at=utcnow(),
            )
        )
        self.session.flush()
```

- [ ] **Step 5: Run the unit tests to verify they pass**

Run: `uv run pytest server/tests/unit/test_case_service.py -v`
Expected: PASS — 8 passed

- [ ] **Step 6: Write the failing API tests**

Create `server/tests/integration/test_cases_api.py`:

```python
import pytest


@pytest.fixture
def project_key(client):
    client.post("/api/projects", json={"key": "CHK", "name": "Checkout"})
    return "CHK"


def test_create_case_returns_a_readable_key(client, project_key):
    response = client.post(
        f"/api/projects/{project_key}/cases",
        json={
            "title": "Coupon applies at checkout",
            "execution_type": "automated",
            "priority": "p1",
            "expected_result": "Discount applied",
            "tags": ["smoke"],
        },
        headers={"X-Actor": "alvis"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["case_key"] == "CHK-1"
    assert body["current_version_no"] == 1
    assert body["created_by"] == "alvis"
    assert body["tags"] == ["smoke"]


def test_case_is_addressable_by_readable_key(client, project_key):
    client.post(f"/api/projects/{project_key}/cases", json={"title": "Coupon"})

    response = client.get("/api/cases/CHK-1")

    assert response.status_code == 200
    assert response.json()["title"] == "Coupon"


def test_patch_bumps_version_and_records_history(client, project_key):
    client.post(f"/api/projects/{project_key}/cases", json={"title": "Coupon"})

    patched = client.patch(
        "/api/cases/CHK-1",
        json={"expected_version_no": 1, "title": "Coupon SAVE10", "change_note": "be specific"},
    )
    assert patched.status_code == 200
    assert patched.json()["current_version_no"] == 2

    versions = client.get("/api/cases/CHK-1/versions").json()
    assert [v["version_no"] for v in versions] == [1, 2]
    assert versions[0]["title"] == "Coupon"


def test_stale_patch_returns_409(client, project_key):
    client.post(f"/api/projects/{project_key}/cases", json={"title": "Coupon"})
    client.patch("/api/cases/CHK-1", json={"expected_version_no": 1, "title": "First"})

    response = client.patch("/api/cases/CHK-1", json={"expected_version_no": 1, "title": "Stale"})

    assert response.status_code == 409
    assert response.json()["code"] == "case_version_conflict"


def test_list_cases_filters_by_tag(client, project_key):
    client.post(f"/api/projects/{project_key}/cases", json={"title": "A", "tags": ["smoke"]})
    client.post(f"/api/projects/{project_key}/cases", json={"title": "B", "tags": ["slow"]})

    response = client.get(f"/api/projects/{project_key}/cases", params={"tag": "smoke"})

    assert [c["title"] for c in response.json()] == ["A"]


def test_archive_marks_the_case(client, project_key):
    client.post(f"/api/projects/{project_key}/cases", json={"title": "Coupon"})

    response = client.post("/api/cases/CHK-1/archive")

    assert response.status_code == 200
    assert response.json()["archived_at"] is not None
```

- [ ] **Step 7: Run the API tests to verify they fail**

Run: `uv run pytest server/tests/integration/test_cases_api.py -v`
Expected: FAIL — 404 responses, because the routes do not exist yet.

- [ ] **Step 8: Write the schemas and routes**

Create `server/src/testforge/schemas/cases.py`:

```python
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from testforge.schemas.common import Step

ExecutionType = Literal["manual", "automated"]
Priority = Literal["p0", "p1", "p2", "p3"]
Status = Literal["draft", "active", "deprecated"]


class CaseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    suite_id: str | None = None
    execution_type: ExecutionType = "automated"
    priority: Priority = "p2"
    preconditions: str | None = None
    steps: list[Step] = Field(default_factory=list)
    expected_result: str | None = None
    tags: list[str] = Field(default_factory=list)


class CaseUpdate(BaseModel):
    expected_version_no: int
    change_note: str | None = None
    title: str | None = None
    suite_id: str | None = None
    execution_type: ExecutionType | None = None
    priority: Priority | None = None
    status: Status | None = None
    owner: str | None = None
    preconditions: str | None = None
    steps: list[Step] | None = None
    expected_result: str | None = None


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_key: str
    project_id: str
    suite_id: str | None
    title: str
    execution_type: str
    priority: str
    status: str
    owner: str | None
    preconditions: str | None
    steps: list[Step] = Field(validation_alias="steps_json")
    expected_result: str | None
    current_version_no: int
    tags: list[str]
    created_by: str
    updated_by: str
    archived_at: datetime | None

    @field_validator("tags", mode="before")
    @classmethod
    def flatten_tags(cls, value: object) -> list[str]:
        if isinstance(value, list) and value and not isinstance(value[0], str):
            return [tag.name for tag in value]
        return value or []


class CaseVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version_no: int
    title: str
    execution_type: str
    priority: str
    preconditions: str | None
    steps: list[Step] = Field(validation_alias="steps_json")
    expected_result: str | None
    change_note: str | None
    created_by: str
    created_at: datetime


class TagsSet(BaseModel):
    tags: list[str]
```

Create `server/src/testforge/api/routes/cases.py`:

```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from testforge.api.deps import get_actor, get_session
from testforge.schemas.cases import CaseCreate, CaseOut, CaseUpdate, CaseVersionOut, TagsSet
from testforge.services.case_service import CaseService
from testforge.services.project_service import ProjectService

router = APIRouter(tags=["cases"])


@router.post("/api/projects/{project_key}/cases", response_model=CaseOut, status_code=201)
def create_case(
    project_key: str,
    payload: CaseCreate,
    session: Session = Depends(get_session),
    actor: str = Depends(get_actor),
) -> CaseOut:
    project = ProjectService(session).get_by_key(project_key)
    case = CaseService(session).create(
        project=project,
        suite_id=payload.suite_id,
        title=payload.title,
        execution_type=payload.execution_type,
        priority=payload.priority,
        preconditions=payload.preconditions,
        steps=[step.model_dump() for step in payload.steps],
        expected_result=payload.expected_result,
        tags=payload.tags,
        actor=actor,
    )
    return CaseOut.model_validate(case)


@router.get("/api/projects/{project_key}/cases", response_model=list[CaseOut])
def list_cases(
    project_key: str,
    suite_id: str | None = None,
    tag: str | None = None,
    status: str | None = None,
    execution_type: str | None = None,
    q: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[CaseOut]:
    project = ProjectService(session).get_by_key(project_key)
    cases = CaseService(session).list_for_project(
        project, suite_id=suite_id, tag=tag, status=status, execution_type=execution_type, q=q
    )
    return [CaseOut.model_validate(case) for case in cases]


@router.get("/api/cases/{case_key}", response_model=CaseOut)
def get_case(case_key: str, session: Session = Depends(get_session)) -> CaseOut:
    return CaseOut.model_validate(CaseService(session).get_by_key(case_key))


@router.patch("/api/cases/{case_key}", response_model=CaseOut)
def update_case(
    case_key: str,
    payload: CaseUpdate,
    session: Session = Depends(get_session),
    actor: str = Depends(get_actor),
) -> CaseOut:
    fields = payload.model_dump(exclude_none=True, exclude={"expected_version_no", "change_note"})
    if "steps" in fields:
        fields["steps_json"] = [dict(step) for step in fields.pop("steps")]
    case = CaseService(session).update(
        case_key=case_key,
        expected_version_no=payload.expected_version_no,
        actor=actor,
        change_note=payload.change_note,
        **fields,
    )
    return CaseOut.model_validate(case)


@router.get("/api/cases/{case_key}/versions", response_model=list[CaseVersionOut])
def list_versions(case_key: str, session: Session = Depends(get_session)) -> list[CaseVersionOut]:
    return [CaseVersionOut.model_validate(v) for v in CaseService(session).versions(case_key)]


@router.put("/api/cases/{case_key}/tags", response_model=CaseOut)
def set_tags(case_key: str, payload: TagsSet, session: Session = Depends(get_session)) -> CaseOut:
    return CaseOut.model_validate(CaseService(session).set_tags(case_key, payload.tags))


@router.post("/api/cases/{case_key}/archive", response_model=CaseOut)
def archive_case(
    case_key: str, session: Session = Depends(get_session), actor: str = Depends(get_actor)
) -> CaseOut:
    return CaseOut.model_validate(CaseService(session).archive(case_key, actor))
```

Add `cases` to the route imports and `app.include_router(cases.router)` in `server/src/testforge/main.py`.

- [ ] **Step 9: Generate the migration**

```bash
uv run alembic -c server/alembic.ini revision --autogenerate -m "add test cases, versions, and tags"
```

- [ ] **Step 10: Run the tests to verify they pass**

Run: `uv run pytest server/tests -v`
Expected: PASS — all tests pass.

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "feat: add versioned test cases with tags and optimistic concurrency"
```

---

### Task 7: Runs and automation links

**Files:**
- Create: `server/src/testforge/models/run.py`, `server/src/testforge/models/automation.py`, `server/src/testforge/services/run_service.py`, `server/src/testforge/services/automation_service.py`
- Modify: `server/src/testforge/models/__init__.py`
- Test: `server/tests/unit/test_run_service.py`, `server/tests/unit/test_automation_service.py`

**Interfaces:**
- Consumes: `Project`, `TestCase`, `AppError`.
- Produces: models `Run`, `Result`, `AutomationLink`; `RunService(session)` with `open(*, project, external_id, name, source, ci_metadata, actor) -> tuple[Run, bool]`, `get(run_id) -> Run`, `complete(run_id) -> Run`; `AutomationService(session)` with `bind(*, case, framework, test_identifier, seen_at) -> AutomationLink`, `links_for_case(case) -> list[AutomationLink]`.

`open()` returns `(run, created)` — `created=False` means an existing run was returned for a repeated `external_id`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/unit/test_run_service.py`:

```python
import pytest

from testforge.errors import AppError
from testforge.services.project_service import ProjectService
from testforge.services.run_service import RunService


@pytest.fixture
def project(db_session):
    project = ProjectService(db_session).create(
        key="CHK", name="Checkout", description=None, actor="local"
    )
    db_session.commit()
    return project


def test_open_creates_a_running_run(db_session, project):
    run, created = RunService(db_session).open(
        project=project,
        external_id="gha-1",
        name="nightly",
        source="ci",
        ci_metadata={"branch": "main"},
        actor="ci-bot",
    )
    db_session.commit()

    assert created is True
    assert run.status == "running"
    assert run.ci_metadata_json == {"branch": "main"}
    assert run.created_by == "ci-bot"


def test_open_is_idempotent_on_external_id(db_session, project):
    service = RunService(db_session)
    first, first_created = service.open(
        project=project, external_id="gha-1", name="a", source="ci", ci_metadata={}, actor="local"
    )
    db_session.commit()

    second, second_created = service.open(
        project=project, external_id="gha-1", name="b", source="ci", ci_metadata={}, actor="local"
    )
    db_session.commit()

    assert second_created is False
    assert second.id == first.id
    assert second.name == "a", "the repeat must not overwrite the original run"


def test_complete_sets_status_and_timestamp(db_session, project):
    service = RunService(db_session)
    run, _ = service.open(
        project=project, external_id="gha-1", name="a", source="ci", ci_metadata={}, actor="local"
    )
    db_session.commit()

    completed = service.complete(run.id)
    db_session.commit()

    assert completed.status == "completed"
    assert completed.completed_at is not None


def test_get_unknown_run_raises(db_session):
    with pytest.raises(AppError) as excinfo:
        RunService(db_session).get("missing")
    assert excinfo.value.code == "run_not_found"
```

Create `server/tests/unit/test_automation_service.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest server/tests/unit/test_run_service.py server/tests/unit/test_automation_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'testforge.services.run_service'`

- [ ] **Step 3: Write the models**

Create `server/src/testforge/models/run.py`:

```python
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from testforge.db.base import Base, TimestampMixin, utcnow
from testforge.ids import new_id


class Run(Base, TimestampMixin):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint("project_id", "external_id", name="uq_runs_project_external_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="local")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    plan_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    ci_metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False, default="local")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Result(Base):
    __tablename__ = "results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id"), nullable=False, index=True
    )
    test_case_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("test_cases.id"), nullable=True, index=True
    )
    test_case_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("test_case_versions.id"), nullable=True
    )
    automation_link_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("automation_links.id"), nullable=True
    )
    test_identifier: Mapped[str] = mapped_column(String(1000), nullable=False)
    unresolved_case_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failure_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    stack_trace: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachments_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

Create `server/src/testforge/models/automation.py`:

```python
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from testforge.db.base import Base
from testforge.ids import new_id


class AutomationLink(Base):
    __tablename__ = "automation_links"
    __table_args__ = (
        UniqueConstraint("framework", "test_identifier", name="uq_automation_identifier"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    test_case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("test_cases.id"), nullable=False, index=True
    )
    framework: Mapped[str] = mapped_column(String(50), nullable=False)
    test_identifier: Mapped[str] = mapped_column(String(1000), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
```

Update `server/src/testforge/models/__init__.py`:

```python
"""Importing this module registers every table on ``Base.metadata``."""

from testforge.models.automation import AutomationLink
from testforge.models.case import Tag, TestCase, TestCaseVersion, test_case_tags
from testforge.models.project import Project
from testforge.models.run import Result, Run
from testforge.models.suite import Suite

__all__ = [
    "AutomationLink",
    "Project",
    "Result",
    "Run",
    "Suite",
    "Tag",
    "TestCase",
    "TestCaseVersion",
    "test_case_tags",
]
```

- [ ] **Step 4: Write the services**

Create `server/src/testforge/services/run_service.py`:

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from testforge.db.base import utcnow
from testforge.errors import AppError
from testforge.models.project import Project
from testforge.models.run import Run


class RunService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def open(
        self,
        *,
        project: Project,
        external_id: str,
        name: str | None,
        source: str,
        ci_metadata: dict,
        actor: str,
    ) -> tuple[Run, bool]:
        existing = self.session.scalar(
            select(Run).where(Run.project_id == project.id, Run.external_id == external_id)
        )
        if existing is not None:
            return existing, False
        run = Run(
            project_id=project.id,
            external_id=external_id,
            name=name,
            source=source,
            ci_metadata_json=ci_metadata,
            created_by=actor,
        )
        self.session.add(run)
        self.session.flush()
        return run, True

    def get(self, run_id: str) -> Run:
        run = self.session.get(Run, run_id)
        if run is None:
            raise AppError("run_not_found", f"no run {run_id}", 404, {"run_id": run_id})
        return run

    def complete(self, run_id: str) -> Run:
        run = self.get(run_id)
        run.status = "completed"
        run.completed_at = utcnow()
        self.session.flush()
        return run
```

Create `server/src/testforge/services/automation_service.py`:

```python
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from testforge.models.automation import AutomationLink
from testforge.models.case import TestCase


class AutomationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def bind(
        self, *, case: TestCase, framework: str, test_identifier: str, seen_at: datetime
    ) -> AutomationLink:
        link = self.session.scalar(
            select(AutomationLink).where(
                AutomationLink.framework == framework,
                AutomationLink.test_identifier == test_identifier,
            )
        )
        if link is None:
            link = AutomationLink(
                test_case_id=case.id,
                framework=framework,
                test_identifier=test_identifier,
                first_seen_at=seen_at,
                last_seen_at=seen_at,
            )
            self.session.add(link)
        else:
            link.test_case_id = case.id
            link.last_seen_at = seen_at
            link.active = True
        self.session.flush()
        return link

    def links_for_case(self, case: TestCase) -> list[AutomationLink]:
        return list(
            self.session.scalars(
                select(AutomationLink)
                .where(AutomationLink.test_case_id == case.id)
                .order_by(AutomationLink.test_identifier)
            )
        )
```

- [ ] **Step 5: Generate the migration**

```bash
uv run alembic -c server/alembic.ini revision --autogenerate -m "add runs, results, and automation links"
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest server/tests -v`
Expected: PASS — all tests pass.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: add runs with idempotent creation and discovered automation links"
```

---

### Task 8: Ingestion path and run APIs

**Files:**
- Create: `server/src/testforge/schemas/results.py`, `server/src/testforge/schemas/runs.py`, `server/src/testforge/services/ingestion_service.py`, `server/src/testforge/api/routes/runs.py`
- Modify: `server/src/testforge/main.py`
- Test: `server/tests/unit/test_ingestion_service.py`, `server/tests/integration/test_runs_api.py`

**Interfaces:**
- Consumes: `RunService`, `CaseService.find_by_key`, `AutomationService.bind`, `AppError`.
- Produces: Pydantic `ResultIn`, `ResultBatch`, `IngestSummary` (the shared contract); `IngestionService(session)` with `ingest(*, run: Run, results: list[ResultIn]) -> IngestSummary`.

- [ ] **Step 1: Write the failing unit tests**

Create `server/tests/unit/test_ingestion_service.py`:

```python
from datetime import UTC, datetime

import pytest

from testforge.errors import AppError
from testforge.schemas.results import ResultIn
from testforge.services.automation_service import AutomationService
from testforge.services.case_service import CaseService
from testforge.services.ingestion_service import IngestionService
from testforge.services.project_service import ProjectService
from testforge.services.run_service import RunService

EXECUTED_AT = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


@pytest.fixture
def project(db_session):
    project = ProjectService(db_session).create(
        key="CHK", name="Checkout", description=None, actor="local"
    )
    db_session.commit()
    return project


@pytest.fixture
def case(db_session, project):
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


@pytest.fixture
def run(db_session, project):
    run, _ = RunService(db_session).open(
        project=project, external_id="gha-1", name="n", source="ci", ci_metadata={}, actor="local"
    )
    db_session.commit()
    return run


def result(**overrides) -> ResultIn:
    payload = {
        "case_key": "CHK-1",
        "test_identifier": "tests/test_checkout.py::test_coupon[web]",
        "framework": "pytest",
        "outcome": "passed",
        "duration_ms": 12,
        "executed_at": EXECUTED_AT,
    }
    payload.update(overrides)
    return ResultIn(**payload)


def test_resolved_result_pins_the_current_case_version(db_session, run, case):
    summary = IngestionService(db_session).ingest(run=run, results=[result()])
    db_session.commit()

    assert summary.recorded == 1
    assert summary.resolved == 1
    assert summary.unresolved == 0

    stored = IngestionService(db_session).results_for_run(run)
    assert stored[0].test_case_id == case.id
    assert stored[0].test_case_version_id is not None
    assert stored[0].unresolved_case_key is None


def test_version_pin_is_the_version_current_at_execution_time(db_session, run, case):
    service = IngestionService(db_session)
    service.ingest(run=run, results=[result()])
    db_session.commit()
    pinned_at_v1 = service.results_for_run(run)[0].test_case_version_id

    CaseService(db_session).update(
        case_key="CHK-1", expected_version_no=1, actor="editor", title="Coupon SAVE10"
    )
    db_session.commit()

    versions = CaseService(db_session).versions("CHK-1")
    assert versions[0].id == pinned_at_v1
    assert versions[1].id != pinned_at_v1, "the old result must still point at v1"


def test_ingest_upserts_the_automation_link(db_session, run, case):
    IngestionService(db_session).ingest(run=run, results=[result()])
    db_session.commit()

    links = AutomationService(db_session).links_for_case(case)
    assert [link.test_identifier for link in links] == ["tests/test_checkout.py::test_coupon[web]"]
    assert links[0].last_seen_at == EXECUTED_AT


def test_unknown_case_key_is_stored_as_unresolved_not_rejected(db_session, run):
    summary = IngestionService(db_session).ingest(run=run, results=[result(case_key="CHK-999")])
    db_session.commit()

    assert summary.recorded == 1
    assert summary.unresolved == 1
    stored = IngestionService(db_session).results_for_run(run)[0]
    assert stored.test_case_id is None
    assert stored.unresolved_case_key == "CHK-999"


def test_result_without_a_case_key_is_stored_as_unresolved(db_session, run):
    summary = IngestionService(db_session).ingest(run=run, results=[result(case_key=None)])
    db_session.commit()

    assert summary.unresolved == 1
    assert IngestionService(db_session).results_for_run(run)[0].unresolved_case_key is None


def test_archived_case_still_accepts_results_and_is_counted(db_session, run, case):
    CaseService(db_session).archive("CHK-1", actor="local")
    db_session.commit()

    summary = IngestionService(db_session).ingest(run=run, results=[result()])
    db_session.commit()

    assert summary.resolved == 1
    assert summary.archived_case_results == 1
    assert IngestionService(db_session).results_for_run(run)[0].test_case_id == case.id


def test_outcome_counts_are_summarised(db_session, run, case):
    summary = IngestionService(db_session).ingest(
        run=run,
        results=[
            result(outcome="passed"),
            result(outcome="failed", test_identifier="t.py::b"),
            result(outcome="skipped", test_identifier="t.py::c"),
        ],
    )
    db_session.commit()

    assert summary.by_outcome == {"passed": 1, "failed": 1, "skipped": 1}


def test_ingesting_into_a_completed_run_is_rejected(db_session, run, case):
    RunService(db_session).complete(run.id)
    db_session.commit()

    with pytest.raises(AppError) as excinfo:
        IngestionService(db_session).ingest(run=run, results=[result()])

    assert excinfo.value.code == "run_already_completed"
    assert excinfo.value.status_code == 409
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest server/tests/unit/test_ingestion_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'testforge.schemas.results'`

- [ ] **Step 3: Write the shared result contract**

Create `server/src/testforge/schemas/results.py`:

```python
"""The result contract.

Every producer of results — the pytest plugin, JUnit import, and the future runner —
emits exactly this shape, and every one of them lands in ``IngestionService.ingest``.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Outcome = Literal["passed", "failed", "skipped", "error", "blocked"]


class ResultIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_key: str | None = None
    test_identifier: str = Field(min_length=1, max_length=1000)
    framework: str = Field(default="pytest", max_length=50)
    outcome: Outcome
    duration_ms: int | None = Field(default=None, ge=0)
    failure_type: str | None = None
    failure_message: str | None = None
    stack_trace: str | None = None
    attachments: list[str] = Field(default_factory=list)
    executed_at: datetime


class ResultBatch(BaseModel):
    results: list[ResultIn]


class IngestSummary(BaseModel):
    run_id: str
    recorded: int
    resolved: int
    unresolved: int
    archived_case_results: int
    by_outcome: dict[str, int]
```

- [ ] **Step 4: Write the ingestion service**

Create `server/src/testforge/services/ingestion_service.py`:

```python
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from testforge.errors import AppError
from testforge.models.case import TestCaseVersion
from testforge.models.run import Result, Run
from testforge.schemas.results import IngestSummary, ResultIn
from testforge.services.automation_service import AutomationService
from testforge.services.case_service import CaseService


class IngestionService:
    """The single path from a raw result to a stored, case-bound row."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.cases = CaseService(session)
        self.automation = AutomationService(session)

    def ingest(self, *, run: Run, results: list[ResultIn]) -> IngestSummary:
        if run.status != "running":
            raise AppError(
                "run_already_completed",
                f"run {run.id} is {run.status} and no longer accepts results",
                409,
                {"run_id": run.id, "status": run.status},
            )

        resolved = unresolved = archived = 0
        outcomes: Counter[str] = Counter()

        for incoming in results:
            case = self.cases.find_by_key(incoming.case_key) if incoming.case_key else None
            version_id = None
            link_id = None

            if case is None:
                unresolved += 1
            else:
                resolved += 1
                if case.archived_at is not None:
                    archived += 1
                version_id = self._current_version_id(case.id, case.current_version_no)
                link = self.automation.bind(
                    case=case,
                    framework=incoming.framework,
                    test_identifier=incoming.test_identifier,
                    seen_at=incoming.executed_at,
                )
                link_id = link.id

            self.session.add(
                Result(
                    run_id=run.id,
                    test_case_id=case.id if case else None,
                    test_case_version_id=version_id,
                    automation_link_id=link_id,
                    test_identifier=incoming.test_identifier,
                    unresolved_case_key=incoming.case_key if case is None else None,
                    outcome=incoming.outcome,
                    duration_ms=incoming.duration_ms,
                    failure_type=incoming.failure_type,
                    failure_message=incoming.failure_message,
                    stack_trace=incoming.stack_trace,
                    attachments_json=incoming.attachments,
                    executed_at=incoming.executed_at,
                )
            )
            outcomes[incoming.outcome] += 1

        self.session.flush()
        return IngestSummary(
            run_id=run.id,
            recorded=len(results),
            resolved=resolved,
            unresolved=unresolved,
            archived_case_results=archived,
            by_outcome=dict(outcomes),
        )

    def results_for_run(self, run: Run) -> list[Result]:
        return list(
            self.session.scalars(
                select(Result).where(Result.run_id == run.id).order_by(Result.test_identifier)
            )
        )

    def _current_version_id(self, case_id: str, version_no: int) -> str | None:
        return self.session.scalar(
            select(TestCaseVersion.id).where(
                TestCaseVersion.test_case_id == case_id,
                TestCaseVersion.version_no == version_no,
            )
        )
```

- [ ] **Step 5: Run the unit tests to verify they pass**

Run: `uv run pytest server/tests/unit/test_ingestion_service.py -v`
Expected: PASS — 8 passed

- [ ] **Step 6: Write the failing API tests**

Create `server/tests/integration/test_runs_api.py`:

```python
import pytest

EXECUTED_AT = "2026-08-01T10:00:00Z"


@pytest.fixture
def setup(client):
    client.post("/api/projects", json={"key": "CHK", "name": "Checkout"})
    client.post("/api/projects/CHK/cases", json={"title": "Coupon"})
    return "CHK"


def open_run(client, external_id="gha-1"):
    return client.post(
        "/api/projects/CHK/runs",
        json={"external_id": external_id, "name": "nightly", "source": "ci"},
    )


def test_open_run_then_ingest_then_complete(client, setup):
    created = open_run(client)
    assert created.status_code == 201
    run_id = created.json()["id"]

    ingested = client.post(
        f"/api/runs/{run_id}/results",
        json={
            "results": [
                {
                    "case_key": "CHK-1",
                    "test_identifier": "tests/test_checkout.py::test_coupon",
                    "framework": "pytest",
                    "outcome": "passed",
                    "duration_ms": 30,
                    "executed_at": EXECUTED_AT,
                }
            ]
        },
    )
    assert ingested.status_code == 200
    assert ingested.json()["resolved"] == 1

    completed = client.post(f"/api/runs/{run_id}/complete")
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"


def test_repeat_external_id_returns_the_same_run_with_200(client, setup):
    first = open_run(client)
    second = open_run(client)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


def test_ingesting_into_a_completed_run_returns_409(client, setup):
    run_id = open_run(client).json()["id"]
    client.post(f"/api/runs/{run_id}/complete")

    response = client.post(
        f"/api/runs/{run_id}/results",
        json={
            "results": [
                {
                    "case_key": "CHK-1",
                    "test_identifier": "t.py::a",
                    "outcome": "passed",
                    "executed_at": EXECUTED_AT,
                }
            ]
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "run_already_completed"


def test_malformed_result_rejects_the_whole_batch(client, setup):
    run_id = open_run(client).json()["id"]

    response = client.post(
        f"/api/runs/{run_id}/results",
        json={
            "results": [
                {
                    "case_key": "CHK-1",
                    "test_identifier": "t.py::a",
                    "outcome": "passed",
                    "executed_at": EXECUTED_AT,
                },
                {"case_key": "CHK-1", "test_identifier": "t.py::b", "outcome": "exploded"},
            ]
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_result_payload"
    assert client.get(f"/api/runs/{run_id}/results").json() == []


def test_unresolved_results_are_stored_and_filterable(client, setup):
    run_id = open_run(client).json()["id"]
    client.post(
        f"/api/runs/{run_id}/results",
        json={
            "results": [
                {
                    "case_key": "CHK-999",
                    "test_identifier": "t.py::orphan",
                    "outcome": "failed",
                    "executed_at": EXECUTED_AT,
                }
            ]
        },
    )

    summary = client.get(f"/api/runs/{run_id}").json()
    assert summary["unresolved_count"] == 1

    unresolved = client.get(f"/api/runs/{run_id}/results", params={"unresolved": True}).json()
    assert unresolved[0]["unresolved_case_key"] == "CHK-999"


def test_automation_links_appear_on_the_case(client, setup):
    run_id = open_run(client).json()["id"]
    client.post(
        f"/api/runs/{run_id}/results",
        json={
            "results": [
                {
                    "case_key": "CHK-1",
                    "test_identifier": "tests/test_checkout.py::test_coupon[web]",
                    "outcome": "passed",
                    "executed_at": EXECUTED_AT,
                }
            ]
        },
    )

    links = client.get("/api/cases/CHK-1/automation-links").json()
    assert [link["test_identifier"] for link in links] == [
        "tests/test_checkout.py::test_coupon[web]"
    ]
```

- [ ] **Step 7: Run the API tests to verify they fail**

Run: `uv run pytest server/tests/integration/test_runs_api.py -v`
Expected: FAIL — 404 responses, because the run routes do not exist yet.

- [ ] **Step 8: Write the run schemas and routes**

Create `server/src/testforge/schemas/runs.py`:

```python
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RunCreate(BaseModel):
    external_id: str = Field(min_length=1, max_length=200)
    name: str | None = None
    source: Literal["ci", "local", "manual", "runner"] = "local"
    ci_metadata: dict = Field(default_factory=dict)


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    external_id: str
    name: str | None
    source: str
    status: str
    created_by: str
    started_at: datetime
    completed_at: datetime | None


class RunSummaryOut(RunOut):
    total_results: int
    unresolved_count: int
    by_outcome: dict[str, int]


class ResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    test_case_id: str | None
    test_case_version_id: str | None
    test_identifier: str
    unresolved_case_key: str | None
    outcome: str
    duration_ms: int | None
    failure_type: str | None
    failure_message: str | None
    executed_at: datetime


class AutomationLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    framework: str
    test_identifier: str
    first_seen_at: datetime
    last_seen_at: datetime
    active: bool
```

Create `server/src/testforge/api/routes/runs.py`:

```python
from collections import Counter

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from testforge.api.deps import get_actor, get_session
from testforge.schemas.results import IngestSummary, ResultBatch
from testforge.schemas.runs import AutomationLinkOut, ResultOut, RunCreate, RunOut, RunSummaryOut
from testforge.services.automation_service import AutomationService
from testforge.services.case_service import CaseService
from testforge.services.ingestion_service import IngestionService
from testforge.services.project_service import ProjectService
from testforge.services.run_service import RunService

router = APIRouter(tags=["runs"])


@router.post("/api/projects/{project_key}/runs", response_model=RunOut)
def open_run(
    project_key: str,
    payload: RunCreate,
    response: Response,
    session: Session = Depends(get_session),
    actor: str = Depends(get_actor),
) -> RunOut:
    project = ProjectService(session).get_by_key(project_key)
    run, created = RunService(session).open(
        project=project,
        external_id=payload.external_id,
        name=payload.name,
        source=payload.source,
        ci_metadata=payload.ci_metadata,
        actor=actor,
    )
    response.status_code = 201 if created else 200
    return RunOut.model_validate(run)


@router.post("/api/runs/{run_id}/results", response_model=IngestSummary)
def ingest_results(
    run_id: str, payload: ResultBatch, session: Session = Depends(get_session)
) -> IngestSummary:
    run = RunService(session).get(run_id)
    return IngestionService(session).ingest(run=run, results=payload.results)


@router.post("/api/runs/{run_id}/complete", response_model=RunOut)
def complete_run(run_id: str, session: Session = Depends(get_session)) -> RunOut:
    return RunOut.model_validate(RunService(session).complete(run_id))


@router.get("/api/runs/{run_id}", response_model=RunSummaryOut)
def get_run(run_id: str, session: Session = Depends(get_session)) -> RunSummaryOut:
    run = RunService(session).get(run_id)
    results = IngestionService(session).results_for_run(run)
    outcomes = Counter(result.outcome for result in results)
    return RunSummaryOut(
        **RunOut.model_validate(run).model_dump(),
        total_results=len(results),
        unresolved_count=sum(1 for r in results if r.test_case_id is None),
        by_outcome=dict(outcomes),
    )


@router.get("/api/runs/{run_id}/results", response_model=list[ResultOut])
def list_results(
    run_id: str,
    outcome: str | None = None,
    case_key: str | None = None,
    unresolved: bool | None = None,
    session: Session = Depends(get_session),
) -> list[ResultOut]:
    run = RunService(session).get(run_id)
    results = IngestionService(session).results_for_run(run)
    if outcome is not None:
        results = [r for r in results if r.outcome == outcome]
    if unresolved is not None:
        results = [r for r in results if (r.test_case_id is None) is unresolved]
    if case_key is not None:
        case = CaseService(session).get_by_key(case_key)
        results = [r for r in results if r.test_case_id == case.id]
    return [ResultOut.model_validate(r) for r in results]


@router.get("/api/cases/{case_key}/automation-links", response_model=list[AutomationLinkOut])
def list_automation_links(
    case_key: str, session: Session = Depends(get_session)
) -> list[AutomationLinkOut]:
    case = CaseService(session).get_by_key(case_key)
    links = AutomationService(session).links_for_case(case)
    return [AutomationLinkOut.model_validate(link) for link in links]
```

Add `runs` to the route imports and `app.include_router(runs.router)` in `server/src/testforge/main.py`.

- [ ] **Step 9: Run the full suite to verify it passes**

Run: `uv run pytest server/tests -v`
Expected: PASS — all tests pass.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "feat: add the ingestion path with version pinning and unresolved-result handling"
```

---

### Task 9: JUnit XML import

**Files:**
- Create: `server/src/testforge/services/junit.py`
- Modify: `server/src/testforge/api/routes/runs.py`
- Test: `server/tests/unit/test_junit.py`, `server/tests/integration/test_junit_import_api.py`

**Interfaces:**
- Consumes: `ResultIn`, `IngestionService`, `RunService`.
- Produces: `parse_junit(xml: str, *, framework: str = "junit", default_executed_at: datetime) -> list[ResultIn]`.

Case keys come from a `<property name="case_key">` element first, falling back to a `PROJ-123` pattern match on the test name.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/unit/test_junit.py`:

```python
from datetime import UTC, datetime

from testforge.services.junit import parse_junit

NOW = datetime(2026, 8, 1, tzinfo=UTC)

XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="4">
    <testcase classname="tests.test_checkout" name="test_coupon" time="0.125">
      <properties><property name="case_key" value="CHK-1"/></properties>
    </testcase>
    <testcase classname="tests.test_login" name="test_login CHK-2" time="0.5">
      <failure type="AssertionError" message="expected 200, got 500">trace here</failure>
    </testcase>
    <testcase classname="tests.test_misc" name="test_orphan" time="0.01"/>
    <testcase classname="tests.test_misc" name="test_skipped" time="0">
      <skipped message="not on this platform"/>
    </testcase>
  </testsuite>
</testsuites>
"""


def test_case_key_comes_from_the_property_first():
    results = parse_junit(XML, default_executed_at=NOW)
    coupon = next(r for r in results if r.test_identifier.endswith("test_coupon"))
    assert coupon.case_key == "CHK-1"
    assert coupon.outcome == "passed"
    assert coupon.duration_ms == 125


def test_case_key_falls_back_to_a_pattern_match_on_the_name():
    results = parse_junit(XML, default_executed_at=NOW)
    login = next(r for r in results if "test_login" in r.test_identifier)
    assert login.case_key == "CHK-2"
    assert login.outcome == "failed"
    assert login.failure_type == "AssertionError"
    assert login.failure_message == "expected 200, got 500"


def test_unannotated_test_yields_no_case_key():
    results = parse_junit(XML, default_executed_at=NOW)
    orphan = next(r for r in results if r.test_identifier.endswith("test_orphan"))
    assert orphan.case_key is None


def test_skipped_outcome_is_mapped():
    results = parse_junit(XML, default_executed_at=NOW)
    skipped = next(r for r in results if r.test_identifier.endswith("test_skipped"))
    assert skipped.outcome == "skipped"


def test_test_identifier_combines_classname_and_name():
    results = parse_junit(XML, default_executed_at=NOW)
    assert "tests.test_checkout::test_coupon" in {r.test_identifier for r in results}
```

Create `server/tests/integration/test_junit_import_api.py`:

```python
XML = """<?xml version="1.0"?>
<testsuites><testsuite name="pytest">
  <testcase classname="tests.test_checkout" name="test_coupon" time="0.1">
    <properties><property name="case_key" value="CHK-1"/></properties>
  </testcase>
  <testcase classname="tests.test_misc" name="test_orphan" time="0.2"/>
</testsuite></testsuites>
"""


def test_import_junit_creates_a_run_and_ingests(client):
    client.post("/api/projects", json={"key": "CHK", "name": "Checkout"})
    client.post("/api/projects/CHK/cases", json={"title": "Coupon"})

    response = client.post(
        "/api/projects/CHK/runs/import-junit",
        json={"external_id": "junit-1", "name": "imported", "xml": XML},
    )

    assert response.status_code == 200
    summary = response.json()
    assert summary["recorded"] == 2
    assert summary["resolved"] == 1
    assert summary["unresolved"] == 1

    run = client.get(f"/api/runs/{summary['run_id']}").json()
    assert run["status"] == "completed"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest server/tests/unit/test_junit.py server/tests/integration/test_junit_import_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'testforge.services.junit'`

- [ ] **Step 3: Write the parser**

Create `server/src/testforge/services/junit.py`:

```python
import re
from datetime import datetime
from xml.etree import ElementTree

from testforge.errors import AppError
from testforge.schemas.results import ResultIn

CASE_KEY_PATTERN = re.compile(r"\b([A-Z][A-Z0-9]*-\d+)\b")


def parse_junit(
    xml: str, *, framework: str = "junit", default_executed_at: datetime
) -> list[ResultIn]:
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise AppError("invalid_result_payload", f"could not parse JUnit XML: {exc}", 422) from exc

    results: list[ResultIn] = []
    for testcase in root.iter("testcase"):
        classname = testcase.get("classname", "")
        name = testcase.get("name", "")
        identifier = f"{classname}::{name}" if classname else name

        outcome = "passed"
        failure_type = failure_message = stack_trace = None
        for child in testcase:
            if child.tag in {"failure", "error"}:
                outcome = "failed" if child.tag == "failure" else "error"
                failure_type = child.get("type")
                failure_message = child.get("message")
                stack_trace = (child.text or "").strip() or None
            elif child.tag == "skipped":
                outcome = "skipped"

        results.append(
            ResultIn(
                case_key=_case_key(testcase, name),
                test_identifier=identifier,
                framework=framework,
                outcome=outcome,
                duration_ms=_duration_ms(testcase.get("time")),
                failure_type=failure_type,
                failure_message=failure_message,
                stack_trace=stack_trace,
                executed_at=default_executed_at,
            )
        )
    return results


def _case_key(testcase: ElementTree.Element, name: str) -> str | None:
    for prop in testcase.iter("property"):
        if prop.get("name") == "case_key" and prop.get("value"):
            return prop.get("value")
    match = CASE_KEY_PATTERN.search(name)
    return match.group(1) if match else None


def _duration_ms(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(round(float(raw) * 1000))
    except ValueError:
        return None
```

- [ ] **Step 4: Add the import route**

Append to `server/src/testforge/api/routes/runs.py` — add these imports at the top of the file:

```python
from pydantic import BaseModel, Field

from testforge.db.base import utcnow
from testforge.services.junit import parse_junit
```

and add the endpoint at the end of the file:

```python
class JUnitImport(BaseModel):
    external_id: str = Field(min_length=1, max_length=200)
    name: str | None = None
    xml: str


@router.post("/api/projects/{project_key}/runs/import-junit", response_model=IngestSummary)
def import_junit(
    project_key: str,
    payload: JUnitImport,
    session: Session = Depends(get_session),
    actor: str = Depends(get_actor),
) -> IngestSummary:
    project = ProjectService(session).get_by_key(project_key)
    executed_at = utcnow()
    results = parse_junit(payload.xml, default_executed_at=executed_at)

    run_service = RunService(session)
    run, _ = run_service.open(
        project=project,
        external_id=payload.external_id,
        name=payload.name,
        source="ci",
        ci_metadata={"import": "junit"},
        actor=actor,
    )
    summary = IngestionService(session).ingest(run=run, results=results)
    run_service.complete(run.id)
    return summary
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest server/tests -v`
Expected: PASS — all tests pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: import JUnit XML through the shared ingestion path"
```

---

### Task 10: pytest plugin

**Files:**
- Create: `plugin/src/pytest_testforge/plugin.py`
- Test: `plugin/tests/test_plugin.py` (the `pytester` fixture comes from the root `conftest.py` written in Task 1)

**Interfaces:**
- Consumes: nothing from the server package — the plugin stays dependency-free of `testforge` so client repos can install it alone.
- Produces: pytest options `--tf-url`, `--tf-project`, `--tf-external-id`, `--tf-run-name`, `--tf-offline`; marker `case`; module function `build_payload(reports, external_id, name) -> dict`.

- [ ] **Step 1: Write the failing tests**

Create `plugin/tests/test_plugin.py`:

```python
import json


SUITE = """
import pytest

@pytest.mark.case("CHK-1")
def test_passes():
    assert True

@pytest.mark.case("CHK-2")
def test_fails():
    assert 1 == 2

@pytest.mark.case("CHK-3")
@pytest.mark.case("CHK-4")
def test_covers_two_cases():
    assert True

def test_unmarked():
    assert True
"""


def test_offline_mode_writes_the_result_payload(pytester, tmp_path):
    pytester.makepyfile(test_suite=SUITE)
    out = tmp_path / "results.json"

    result = pytester.runpytest(
        "--tf-offline", str(out), "--tf-project", "CHK", "--tf-external-id", "local-1"
    )

    result.assert_outcomes(passed=3, failed=1)
    payload = json.loads(out.read_text())

    assert payload["project_key"] == "CHK"
    assert payload["external_id"] == "local-1"
    assert payload["source"] == "local"

    by_case = {r["case_key"]: r for r in payload["results"] if r["case_key"]}
    assert by_case["CHK-1"]["outcome"] == "passed"
    assert by_case["CHK-2"]["outcome"] == "failed"
    assert by_case["CHK-2"]["failure_message"]
    assert "CHK-3" in by_case and "CHK-4" in by_case, "a repeated marker emits one result per case"


def test_unmarked_tests_are_reported_without_a_case_key(pytester, tmp_path):
    pytester.makepyfile(test_suite=SUITE)
    out = tmp_path / "results.json"

    pytester.runpytest("--tf-offline", str(out), "--tf-project", "CHK")

    payload = json.loads(out.read_text())
    unmarked = [r for r in payload["results"] if r["case_key"] is None]
    assert [r["test_identifier"] for r in unmarked] == ["test_suite.py::test_unmarked"]


def test_plugin_is_inert_without_options(pytester):
    pytester.makepyfile(test_suite=SUITE)

    result = pytester.runpytest()

    result.assert_outcomes(passed=3, failed=1)


def test_unreachable_server_warns_but_does_not_fail_the_run(pytester):
    pytester.makepyfile(test_suite="def test_ok():\n    assert True\n")

    result = pytester.runpytest(
        "--tf-url", "http://127.0.0.1:9", "--tf-project", "CHK", "-p", "no:cacheprovider"
    )

    assert result.ret == 0
    result.stdout.fnmatch_lines(["*testforge: could not report results*"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest plugin/tests -v`
Expected: FAIL — the `--tf-offline` option is unrecognised.

- [ ] **Step 3: Write the plugin**

Create `plugin/src/pytest_testforge/plugin.py`:

```python
"""Report pytest results to a TestForge server.

Design rule: reporting must never change the outcome of a test run. Every failure in
this plugin degrades to a warning.
"""

import json
import os
import uuid
from datetime import UTC, datetime

import httpx
import pytest

_RESULTS_KEY = pytest.StashKey[list]()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("testforge")
    group.addoption("--tf-url", default=os.environ.get("TESTFORGE_URL"), help="TestForge base URL")
    group.addoption("--tf-project", default=os.environ.get("TESTFORGE_PROJECT"), help="Project key")
    group.addoption("--tf-external-id", default=None, help="Stable id for this run")
    group.addoption("--tf-run-name", default=None, help="Human-readable run name")
    group.addoption("--tf-offline", default=None, help="Write the payload to this path instead")
    group.addoption("--tf-actor", default=os.environ.get("TESTFORGE_ACTOR", "local"))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "case(key): link this test to a TestForge case key")
    config.stash[_RESULTS_KEY] = []


def _case_keys(item: pytest.Item) -> list[str]:
    return [mark.args[0] for mark in item.iter_markers(name="case") if mark.args]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" and not (report.when == "setup" and report.outcome == "skipped"):
        return

    if report.outcome == "passed":
        result_outcome = "passed"
    elif report.outcome == "skipped":
        result_outcome = "skipped"
    else:
        result_outcome = "failed"

    failure_message = None
    stack_trace = None
    failure_type = None
    if report.longrepr is not None and result_outcome == "failed":
        stack_trace = str(report.longrepr)
        failure_message = stack_trace.strip().splitlines()[-1] if stack_trace else None
        failure_type = getattr(getattr(call, "excinfo", None), "typename", None)

    base = {
        "test_identifier": item.nodeid,
        "framework": "pytest",
        "outcome": result_outcome,
        "duration_ms": int(round(report.duration * 1000)),
        "failure_type": failure_type,
        "failure_message": failure_message,
        "stack_trace": stack_trace,
        "attachments": [],
        "executed_at": datetime.now(UTC).isoformat(),
    }

    keys = _case_keys(item)
    collected = item.config.stash[_RESULTS_KEY]
    if keys:
        collected.extend({**base, "case_key": key} for key in keys)
    else:
        collected.append({**base, "case_key": None})


def build_payload(config: pytest.Config) -> dict:
    external_id = config.getoption("--tf-external-id") or os.environ.get(
        "GITHUB_RUN_ID", str(uuid.uuid4())
    )
    return {
        "project_key": config.getoption("--tf-project"),
        "external_id": external_id,
        "name": config.getoption("--tf-run-name"),
        "source": "ci" if os.environ.get("CI") else "local",
        "results": config.stash[_RESULTS_KEY],
    }


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    config = session.config
    offline_path = config.getoption("--tf-offline")
    url = config.getoption("--tf-url")
    if not offline_path and not url:
        return

    payload = build_payload(config)
    reporter = config.pluginmanager.get_plugin("terminalreporter")

    if offline_path:
        with open(offline_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        if reporter:
            count = len(payload["results"])
            reporter.write_line(f"testforge: wrote {count} results to {offline_path}")
        return

    try:
        _post(url, payload, actor=config.getoption("--tf-actor"))
    except Exception as exc:  # noqa: BLE001 — reporting must never break a test run
        if reporter:
            reporter.write_line(f"testforge: could not report results ({exc})", yellow=True)
        return

    if reporter:
        reporter.write_line(f"testforge: reported {len(payload['results'])} results")


def _post(url: str, payload: dict, *, actor: str) -> None:
    headers = {"X-Actor": actor}
    with httpx.Client(base_url=url.rstrip("/"), timeout=15.0, headers=headers) as client:
        run = client.post(
            f"/api/projects/{payload['project_key']}/runs",
            json={
                "external_id": payload["external_id"],
                "name": payload["name"],
                "source": payload["source"],
            },
        )
        run.raise_for_status()
        run_id = run.json()["id"]

        ingest = client.post(f"/api/runs/{run_id}/results", json={"results": payload["results"]})
        ingest.raise_for_status()

        client.post(f"/api/runs/{run_id}/complete").raise_for_status()
```

- [ ] **Step 4: Run the plugin tests to verify they pass**

Run: `uv run pytest plugin/tests -v`
Expected: PASS — 4 passed

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add pytest-testforge plugin with offline mode and non-fatal reporting"
```

---

### Task 11: Contract and acceptance tests

This task proves the plugin and server agree, and that the slice's success criterion holds end to end.

**Files:**
- Create: `server/tests/contract/test_result_contract.py`, `server/tests/acceptance/test_marker_to_stored_result.py`
- Test: both files above

**Interfaces:**
- Consumes: `build_payload` (Task 10), `ResultIn`/`ResultBatch` (Task 8), the full HTTP API.
- Produces: nothing new.

- [ ] **Step 1: Write the contract test**

Create `server/tests/contract/test_result_contract.py`:

```python
"""The plugin's emitted JSON must validate against the server's own model.

This is the guard that stops the future S4 runner from drifting away from the
one result contract.
"""

import json

from testforge.schemas.results import ResultBatch

SUITE = """
import pytest

@pytest.mark.case("CHK-1")
def test_passes():
    assert True

@pytest.mark.case("CHK-2")
def test_fails():
    raise AssertionError("boom")

def test_unmarked():
    assert True
"""


def test_plugin_output_validates_against_the_server_model(pytester, tmp_path):
    pytester.makepyfile(test_suite=SUITE)
    out = tmp_path / "results.json"

    pytester.runpytest("--tf-offline", str(out), "--tf-project", "CHK")

    payload = json.loads(out.read_text())
    batch = ResultBatch.model_validate({"results": payload["results"]})

    assert len(batch.results) == 3
    assert {r.outcome for r in batch.results} == {"passed", "failed"}


def test_plugin_emits_no_fields_the_server_rejects(pytester, tmp_path):
    """``ResultIn`` sets ``extra='forbid'``, so an unknown key fails validation here."""
    pytester.makepyfile(test_suite=SUITE)
    out = tmp_path / "results.json"

    pytester.runpytest("--tf-offline", str(out), "--tf-project", "CHK")

    payload = json.loads(out.read_text())
    ResultBatch.model_validate({"results": payload["results"]})
```

- [ ] **Step 2: Run the contract test to verify it fails or passes honestly**

Run: `uv run pytest server/tests/contract -v`
Expected: PASS if the plugin and server already agree. If it FAILS with a Pydantic validation error, that is a real contract bug — fix the plugin's payload to match `ResultIn`, not the other way round.

- [ ] **Step 3: Write the acceptance test**

Create `server/tests/acceptance/test_marker_to_stored_result.py`:

```python
"""The Slice 1 success criterion, end to end.

A marked pytest test runs, its result reaches the platform, and it is bound to the
correct case AND to the case version that was current at execution time.
"""

import json

SUITE = """
import pytest

@pytest.mark.case("CHK-1")
def test_coupon_applies():
    assert True
"""


def test_marked_test_result_is_bound_to_the_case_version_current_at_execution(
    client, pytester, tmp_path
):
    client.post("/api/projects", json={"key": "CHK", "name": "Checkout"})
    client.post("/api/projects/CHK/cases", json={"title": "Coupon applies at checkout"})

    pytester.makepyfile(test_suite=SUITE)
    out = tmp_path / "results.json"
    pytester.runpytest("--tf-offline", str(out), "--tf-project", "CHK", "--tf-external-id", "run-1")
    payload = json.loads(out.read_text())

    run_id = client.post(
        "/api/projects/CHK/runs",
        json={"external_id": payload["external_id"], "source": "local"},
    ).json()["id"]
    summary = client.post(f"/api/runs/{run_id}/results", json={"results": payload["results"]})
    client.post(f"/api/runs/{run_id}/complete")

    assert summary.status_code == 200
    assert summary.json()["resolved"] == 1

    versions = client.get("/api/cases/CHK-1/versions").json()
    assert len(versions) == 1

    stored = client.get(f"/api/runs/{run_id}/results").json()[0]
    assert stored["outcome"] == "passed"
    assert stored["test_identifier"] == "test_suite.py::test_coupon_applies"
    assert stored["test_case_id"] is not None
    version_at_execution = stored["test_case_version_id"]

    client.patch(
        "/api/cases/CHK-1", json={"expected_version_no": 1, "title": "Coupon SAVE10 applies"}
    )

    unchanged = client.get(f"/api/runs/{run_id}/results").json()[0]
    assert unchanged["test_case_version_id"] == version_at_execution, (
        "editing the case must not rewrite history"
    )

    links = client.get("/api/cases/CHK-1/automation-links").json()
    assert links[0]["test_identifier"] == "test_suite.py::test_coupon_applies"
```

- [ ] **Step 4: Run the acceptance test to verify it passes**

Run: `uv run pytest server/tests/acceptance -v`
Expected: PASS — 1 passed

- [ ] **Step 5: Run everything**

Run: `uv run ruff format . && uv run ruff check . && uv run pytest -v`
Expected: ruff clean; all tests pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "test: add plugin/server contract test and the end-to-end acceptance test"
```

---

### Task 12: CLI

**Files:**
- Create: `server/src/testforge/cli/__init__.py`, `server/src/testforge/cli/client.py`
- Test: `server/tests/integration/test_cli.py`

**Interfaces:**
- Consumes: the HTTP API only.
- Produces: `app` (typer application, entry point `tf`); `ApiClient(base_url: str, actor: str)` with `get`, `post`, `patch`.

- [ ] **Step 1: Write the failing test**

Create `server/tests/integration/test_cli.py`:

```python
import httpx
import pytest
from typer.testing import CliRunner

from testforge.cli import app
from testforge.cli import client as client_module


@pytest.fixture
def cli(monkeypatch, client):
    """Point the CLI's HTTP client at the in-process test app."""

    def fake_client(self, *_args, **_kwargs):
        return client

    monkeypatch.setattr(client_module.ApiClient, "_http", fake_client, raising=False)
    return CliRunner()


def test_project_create_and_case_list(cli, client):
    result = cli.invoke(app, ["project", "create", "CHK", "Checkout"])
    assert result.exit_code == 0, result.output
    assert "CHK" in result.output

    cli.invoke(app, ["case", "new", "CHK", "Coupon applies"])
    listed = cli.invoke(app, ["case", "list", "CHK"])

    assert listed.exit_code == 0
    assert "CHK-1" in listed.output
    assert "Coupon applies" in listed.output


def test_run_show_reports_counts(cli, client):
    cli.invoke(app, ["project", "create", "CHK", "Checkout"])
    cli.invoke(app, ["case", "new", "CHK", "Coupon"])
    run_id = client.post(
        "/api/projects/CHK/runs", json={"external_id": "cli-1", "source": "local"}
    ).json()["id"]
    client.post(
        f"/api/runs/{run_id}/results",
        json={
            "results": [
                {
                    "case_key": "CHK-1",
                    "test_identifier": "t.py::a",
                    "outcome": "passed",
                    "executed_at": "2026-08-01T10:00:00Z",
                }
            ]
        },
    )

    result = cli.invoke(app, ["run", "show", run_id])

    assert result.exit_code == 0
    assert "passed" in result.output
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest server/tests/integration/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'testforge.cli'`

- [ ] **Step 3: Write the API client**

Create `server/src/testforge/cli/client.py`:

```python
import os

import httpx
import typer


class ApiClient:
    """Thin HTTP wrapper. The CLI never touches the database directly."""

    def __init__(self, base_url: str | None = None, actor: str | None = None) -> None:
        default_url = os.environ.get("TESTFORGE_URL", "http://localhost:8000")
        self.base_url = (base_url or default_url).rstrip("/")
        self.actor = actor or os.environ.get("TESTFORGE_ACTOR", "local")

    def _http(self) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, headers={"X-Actor": self.actor}, timeout=30.0)

    def request(self, method: str, path: str, **kwargs) -> dict | list:
        client = self._http()
        response = client.request(method, path, **kwargs)
        if response.status_code >= 400:
            body = response.json()
            typer.secho(
                f"{body.get('code', 'error')}: {body.get('message', response.text)}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1)
        return response.json()

    def get(self, path: str, **kwargs) -> dict | list:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> dict | list:
        return self.request("POST", path, **kwargs)

    def patch(self, path: str, **kwargs) -> dict | list:
        return self.request("PATCH", path, **kwargs)
```

- [ ] **Step 4: Write the CLI**

Create `server/src/testforge/cli/__init__.py`:

```python
import json
from pathlib import Path

import typer

from testforge.cli.client import ApiClient

app = typer.Typer(help="TestForge CLI")
project_app = typer.Typer(help="Manage projects")
case_app = typer.Typer(help="Manage test cases")
run_app = typer.Typer(help="Inspect and import runs")
app.add_typer(project_app, name="project")
app.add_typer(case_app, name="case")
app.add_typer(run_app, name="run")


@project_app.command("create")
def project_create(key: str, name: str, description: str | None = None) -> None:
    project = ApiClient().post(
        "/api/projects", json={"key": key, "name": name, "description": description}
    )
    typer.echo(f"{project['key']}  {project['name']}")


@project_app.command("list")
def project_list() -> None:
    for project in ApiClient().get("/api/projects"):
        typer.echo(f"{project['key']:<8} {project['name']}")


@case_app.command("new")
def case_new(
    project_key: str,
    title: str,
    execution_type: str = "automated",
    priority: str = "p2",
    tag: list[str] = typer.Option([], "--tag", help="Attach a tag (repeatable)"),
) -> None:
    case = ApiClient().post(
        f"/api/projects/{project_key}/cases",
        json={
            "title": title,
            "execution_type": execution_type,
            "priority": priority,
            "tags": list(tag),
        },
    )
    typer.echo(f"{case['case_key']}  {case['title']}")


@case_app.command("list")
def case_list(
    project_key: str,
    suite_id: str | None = None,
    tag: str | None = None,
    execution_type: str | None = None,
) -> None:
    params = {
        k: v
        for k, v in {"suite_id": suite_id, "tag": tag, "execution_type": execution_type}.items()
        if v is not None
    }
    for case in ApiClient().get(f"/api/projects/{project_key}/cases", params=params):
        typer.echo(f"{case['case_key']:<12} {case['priority']:<4} {case['title']}")


@run_app.command("show")
def run_show(run_id: str) -> None:
    run = ApiClient().get(f"/api/runs/{run_id}")
    typer.echo(f"run {run['id']}  status={run['status']}  results={run['total_results']}")
    for outcome, count in sorted(run["by_outcome"].items()):
        typer.echo(f"  {outcome:<10} {count}")
    if run["unresolved_count"]:
        typer.echo(f"  unresolved {run['unresolved_count']}")


@run_app.command("import-junit")
def run_import_junit(
    project_key: str, xml_path: Path, external_id: str, name: str | None = None
) -> None:
    summary = ApiClient().post(
        f"/api/projects/{project_key}/runs/import-junit",
        json={"external_id": external_id, "name": name, "xml": xml_path.read_text()},
    )
    typer.echo(json.dumps(summary, indent=2))


@run_app.command("upload")
def run_upload(payload_path: Path) -> None:
    """Upload an offline payload written by ``pytest --tf-offline``."""
    payload = json.loads(payload_path.read_text())
    api = ApiClient()
    run = api.post(
        f"/api/projects/{payload['project_key']}/runs",
        json={
            "external_id": payload["external_id"],
            "name": payload.get("name"),
            "source": payload.get("source", "local"),
        },
    )
    summary = api.post(f"/api/runs/{run['id']}/results", json={"results": payload["results"]})
    api.post(f"/api/runs/{run['id']}/complete")
    typer.echo(json.dumps(summary, indent=2))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest server/tests/integration/test_cli.py -v`
Expected: PASS — 2 passed

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: add the tf CLI over the HTTP API"
```

---

### Task 13: Demo seed, README, and CI eval of the whole loop

**Files:**
- Create: `server/src/testforge/seed.py`, `README.md`
- Modify: `Makefile`, `.github/workflows/ci.yml`
- Test: `server/tests/integration/test_seed.py`

**Interfaces:**
- Consumes: all services.
- Produces: `seed_demo(session) -> dict[str, int]` returning counts; `make seed`, `make demo`.

- [ ] **Step 1: Write the failing test**

Create `server/tests/integration/test_seed.py`:

```python
from testforge.seed import seed_demo
from testforge.services.case_service import CaseService
from testforge.services.project_service import ProjectService


def test_seed_creates_a_browsable_demo_project(db_session):
    counts = seed_demo(db_session)
    db_session.commit()

    assert counts["projects"] == 1
    assert counts["cases"] >= 8

    project = ProjectService(db_session).get_by_key("CHK")
    cases = CaseService(db_session).list_for_project(project)
    assert any(case.execution_type == "manual" for case in cases)
    assert any(case.execution_type == "automated" for case in cases)


def test_seed_is_idempotent(db_session):
    seed_demo(db_session)
    db_session.commit()
    counts = seed_demo(db_session)
    db_session.commit()

    assert counts["projects"] == 0, "re-seeding must not duplicate the demo project"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest server/tests/integration/test_seed.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'testforge.seed'`

- [ ] **Step 3: Write the seed**

Create `server/src/testforge/seed.py`:

```python
"""Seed a small, realistic demo project so a reviewer sees something on first run."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from testforge.models.project import Project
from testforge.services.case_service import CaseService
from testforge.services.project_service import ProjectService
from testforge.services.suite_service import SuiteService

DEMO_CASES = [
    ("Coupon SAVE10 applies at checkout", "automated", "p1", ["smoke", "checkout"]),
    ("Checkout rejects an expired coupon", "automated", "p2", ["checkout"]),
    ("Payment failure shows a retry prompt", "automated", "p1", ["checkout", "payments"]),
    ("Order history loads on mobile", "automated", "p2", ["mobile"]),
    ("Session expires after 30 minutes idle", "automated", "p2", ["auth"]),
    ("Search returns results for partial SKUs", "automated", "p3", ["search"]),
    ("Refund is issued to the original payment method", "manual", "p1", ["payments"]),
    ("Accessibility audit of the checkout form", "manual", "p2", ["a11y", "checkout"]),
]


def seed_demo(session: Session) -> dict[str, int]:
    existing = session.scalar(select(Project).where(Project.key == "CHK"))
    if existing is not None:
        return {"projects": 0, "suites": 0, "cases": 0}

    projects = ProjectService(session)
    project = projects.create(
        key="CHK", name="Checkout", description="Demo storefront project", actor="seed"
    )

    suites = SuiteService(session)
    web = suites.create(project=project, name="Web", parent_id=None, actor="seed")
    checkout = suites.create(project=project, name="Checkout", parent_id=web.id, actor="seed")

    cases = CaseService(session)
    for title, execution_type, priority, tags in DEMO_CASES:
        steps = (
            [{"action": "Open the cart", "expected": "Cart page renders"}]
            if execution_type == "manual"
            else []
        )
        cases.create(
            project=project,
            suite_id=checkout.id,
            title=title,
            execution_type=execution_type,
            priority=priority,
            preconditions=None,
            steps=steps,
            expected_result=None,
            tags=tags,
            actor="seed",
        )

    return {"projects": 1, "suites": 2, "cases": len(DEMO_CASES)}
```

Add a CLI command to `server/src/testforge/cli/__init__.py` — append these imports and command:

```python
from testforge.config import get_settings
from testforge.db.session import create_session_factory
from testforge.seed import seed_demo


@app.command("seed")
def seed() -> None:
    """Create the demo project directly in the database."""
    factory = create_session_factory(get_settings().database_url)
    session = factory()
    try:
        counts = seed_demo(session)
        session.commit()
    finally:
        session.close()
    typer.echo(json.dumps(counts, indent=2))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest server/tests/integration/test_seed.py -v`
Expected: PASS — 2 passed

- [ ] **Step 5: Add the demo target and CI smoke step**

Replace `Makefile`:

```makefile
.PHONY: install dev test lint fmt migrate seed demo

install:
	uv sync

dev:
	uv run uvicorn testforge.main:create_app --factory --reload

test:
	uv run pytest -v

lint:
	uv run ruff check .

fmt:
	uv run ruff format .

migrate:
	uv run alembic -c server/alembic.ini upgrade head

seed: migrate
	uv run tf seed

demo: seed
	@echo "Start the API with 'make dev', then: uv run tf case list CHK"
```

Add a smoke job step to `.github/workflows/ci.yml`, after the `uv run pytest -v` step:

```yaml
      - name: Smoke — migrate and seed
        run: |
          uv run alembic -c server/alembic.ini upgrade head
          uv run tf seed
        env:
          TESTFORGE_DATABASE_URL: sqlite:///./ci.db
```

- [ ] **Step 6: Write the README**

Create `README.md`:

````markdown
# TestForge

Test case and test plan management with automation result ingestion — a system of record that
links the test cases you write to the automated tests that actually run.

Slice 1 (this repo's current state) covers projects, a suite tree, versioned test cases (manual
and automated), discovered automation links, ad-hoc runs, and result ingestion from pytest and
JUnit XML.

## Quickstart

```bash
make install
make seed
make dev
```

Then, in another shell:

```bash
uv run tf case list CHK
```

## Reporting results from your own suite

Install the plugin into the repository under test:

```bash
uv add --dev pytest-testforge
```

Mark a test with the case it covers:

```python
import pytest

@pytest.mark.case("CHK-1")
def test_coupon_applies():
    ...
```

Run it, reporting to a local server:

```bash
uv run pytest --tf-url http://localhost:8000 --tf-project CHK
```

Or write the payload to disk and upload it later — useful in air-gapped CI:

```bash
uv run pytest --tf-offline results.json --tf-project CHK
uv run tf run upload results.json
```

Importing an existing suite that has no markers yet is a useful first step: every result lands as
*unresolved*, which enumerates the tests still awaiting a case link.

```bash
uv run tf run import-junit CHK ./junit.xml ci-1234
```

## Design

- Specs: `docs/superpowers/specs/`
- The full product decomposition (S1–S8, including the LLM-evaluation subsystem) is in
  `docs/superpowers/specs/2026-08-02-test-management-core-design.md`.

Key properties:

- **Case keys are stable.** `CHK-42` never changes, so renames and suite moves don't orphan history.
- **Results pin a case version.** Editing a case later never rewrites what a past run executed.
- **Unresolved results are stored, not dropped.** A system of record that silently discards data
  isn't one.
- **One ingestion path.** The pytest plugin, JUnit import, and the future runner all land in
  `IngestionService.ingest`.
````

- [ ] **Step 7: Run everything**

Run: `uv run ruff format . && uv run ruff check . && uv run pytest -v`
Expected: ruff clean; every test passes.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: add demo seed, README, and CI smoke check"
```

---

## Verification

After Task 13, confirm the slice's success criterion by hand:

```bash
make seed && make dev
```

In a second shell, inside any repository with a pytest suite:

```bash
uv run pytest --tf-url http://localhost:8000 --tf-project CHK --tf-external-id manual-1
```

Then check that results landed and are version-pinned:

```bash
uv run tf run show <run_id>
```
