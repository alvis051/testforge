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
