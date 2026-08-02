from uuid import UUID

from testforge.ids import new_id


def test_new_id_is_a_unique_uuid_string():
    first, second = new_id(), new_id()
    assert first != second
    assert str(UUID(first)) == first
