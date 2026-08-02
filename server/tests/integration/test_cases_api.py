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
