from concurrent.futures import ThreadPoolExecutor

import pytest
from testforge.db.base import Base
from testforge.db.session import create_session_factory
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


def test_allocate_case_key_is_atomic_under_concurrent_creates(settings):
    """Two creates racing for the same project must never be handed the same key."""
    factory = create_session_factory(settings.database_url)
    Base.metadata.create_all(factory.kw["bind"])

    with factory() as setup_session:
        ProjectService(setup_session).create(
            key="CHK", name="Checkout", description=None, actor="local"
        )
        setup_session.commit()

    threads, per_thread = 4, 20

    def allocate_a_batch() -> list[str]:
        keys: list[str] = []
        with factory() as session:
            service = ProjectService(session)
            project = service.get_by_key("CHK")
            for _ in range(per_thread):
                keys.append(service.allocate_case_key(project))
                session.commit()
        return keys

    with ThreadPoolExecutor(max_workers=threads) as pool:
        futures = [pool.submit(allocate_a_batch) for _ in range(threads)]
        batches = [future.result() for future in futures]

    allocated = [key for batch in batches for key in batch]
    expected_total = threads * per_thread

    assert len(allocated) == expected_total
    assert len(set(allocated)) == expected_total
    assert set(allocated) == {f"CHK-{n}" for n in range(1, expected_total + 1)}

    with factory() as session:
        assert ProjectService(session).get_by_key("CHK").case_seq == expected_total
