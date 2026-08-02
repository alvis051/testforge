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
