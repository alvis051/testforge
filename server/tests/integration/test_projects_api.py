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
