import pytest


@pytest.fixture
def project_key(client):
    client.post("/api/projects", json={"key": "CHK", "name": "Checkout"})
    client.post(
        "/api/projects/CHK/cases",
        json={"title": "Coupon applies", "execution_type": "manual", "tags": ["smoke"]},
    )
    client.post("/api/projects/CHK/cases", json={"title": "Refund flow", "tags": ["slow"]})
    return "CHK"


def test_create_plan_snapshots_cases(client, project_key):
    response = client.post(
        f"/api/projects/{project_key}/plans",
        json={
            "name": "Release 2.4",
            "milestone": "2.4",
            "environment": "staging",
            "case_keys": ["CHK-1"],
        },
        headers={"X-Actor": "alvis"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Release 2.4"
    assert body["status"] == "active"
    assert body["case_count"] == 1
    assert body["created_by"] == "alvis"


def test_create_plan_from_a_filter(client, project_key):
    response = client.post(
        f"/api/projects/{project_key}/plans",
        json={"name": "Smoke", "filter": {"tag": "smoke"}},
    )

    assert response.status_code == 201
    assert response.json()["case_count"] == 1


def test_empty_selection_is_rejected(client, project_key):
    response = client.post(
        f"/api/projects/{project_key}/plans",
        json={"name": "Nothing", "filter": {"tag": "nonexistent"}},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "empty_plan_selection"


def test_list_and_get_a_plan(client, project_key):
    created = client.post(
        f"/api/projects/{project_key}/plans",
        json={"name": "Release 2.4", "milestone": "2.4", "case_keys": ["CHK-1"]},
    ).json()

    listed = client.get(f"/api/projects/{project_key}/plans", params={"milestone": "2.4"})
    fetched = client.get(f"/api/plans/{created['id']}")

    assert [p["name"] for p in listed.json()] == ["Release 2.4"]
    assert fetched.status_code == 200
    assert fetched.json()["case_count"] == 1


def test_plan_cases_are_returned_in_snapshot_order(client, project_key):
    plan = client.post(
        f"/api/projects/{project_key}/plans",
        json={"name": "Ordered", "case_keys": ["CHK-2", "CHK-1"]},
    ).json()

    cases = client.get(f"/api/plans/{plan['id']}/cases").json()

    assert [c["case_key"] for c in cases] == ["CHK-2", "CHK-1"]
    assert cases[0]["title"] == "Refund flow"
    assert cases[0]["test_case_version_id"] is not None


def test_unknown_plan_returns_the_error_contract(client):
    response = client.get("/api/plans/missing")

    assert response.status_code == 404
    assert response.json()["code"] == "plan_not_found"


def test_execute_a_plan_opens_a_linked_run(client, project_key):
    plan = client.post(
        f"/api/projects/{project_key}/plans",
        json={"name": "Release 2.4", "case_keys": ["CHK-1"]},
    ).json()

    response = client.post(f"/api/plans/{plan['id']}/runs", json={"name": "nightly"})

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "running"
    assert body["name"] == "nightly"


def test_archived_plan_cannot_be_executed(client, project_key):
    plan = client.post(
        f"/api/projects/{project_key}/plans",
        json={"name": "Doomed", "case_keys": ["CHK-1"]},
    ).json()
    client.post(f"/api/plans/{plan['id']}/archive")

    response = client.post(f"/api/plans/{plan['id']}/runs", json={})

    assert response.status_code == 409
    assert response.json()["code"] == "plan_archived"
