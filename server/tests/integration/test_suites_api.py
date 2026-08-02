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
