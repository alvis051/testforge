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
_BY_NODEID_KEY = pytest.StashKey[dict]()


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
    config.stash[_BY_NODEID_KEY] = {}


def _case_keys(item: pytest.Item) -> list[str]:
    return [mark.args[0] for mark in item.iter_markers(name="case") if mark.args]


def _failure_details(report, call: pytest.CallInfo) -> tuple[str | None, str | None, str | None]:
    """Return (failure_type, failure_message, stack_trace) for one phase's report/call.

    ``call`` is phase-specific: during a setup or teardown report it carries that phase's
    exception info, not the test body's.
    """
    if report.longrepr is None:
        return None, None, None

    stack_trace = str(report.longrepr)
    excinfo = getattr(call, "excinfo", None)
    if excinfo is not None:
        return excinfo.typename, excinfo.exconly(), stack_trace

    fallback = stack_trace.strip().splitlines()[-1] if stack_trace.strip() else None
    return None, fallback, stack_trace


def _record(item: pytest.Item, report, call: pytest.CallInfo, result_outcome: str) -> None:
    """Append one result per case key (or a single unmarked result) for this test."""
    failure_type, failure_message, stack_trace = (
        _failure_details(report, call)
        if result_outcome in ("failed", "error")
        else (None, None, None)
    )

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
    entries = [{**base, "case_key": key} for key in keys] if keys else [{**base, "case_key": None}]

    # The ordered list drives payload ordering; the per-nodeid index holds the SAME dict
    # objects so a later phase can upgrade a recorded result in place.
    item.config.stash[_RESULTS_KEY].extend(entries)
    item.config.stash[_BY_NODEID_KEY].setdefault(item.nodeid, []).extend(entries)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report = outcome.get_result()

    if report.when == "setup":
        if report.outcome == "skipped":
            _record(item, report, call, "skipped")
        elif report.outcome != "passed":
            # Setup failed: the call phase never runs, so this is the test's only report.
            _record(item, report, call, "error")
        return

    if report.when == "call":
        if report.outcome == "passed":
            result_outcome = "passed"
        elif report.outcome == "skipped":
            result_outcome = "skipped"  # covers xfail
        else:
            result_outcome = "failed"
        _record(item, report, call, result_outcome)
        return

    if report.when == "teardown" and report.outcome not in ("passed", "skipped"):
        # Teardown errored: upgrade whatever this test already recorded to "error" so a
        # passing call phase is not reported as a clean pass.
        recorded = item.config.stash[_BY_NODEID_KEY].get(item.nodeid)
        if not recorded:
            _record(item, report, call, "error")
            return

        failure_type, failure_message, stack_trace = _failure_details(report, call)
        for entry in recorded:
            entry["outcome"] = "error"
            entry["failure_type"] = entry["failure_type"] or failure_type
            entry["failure_message"] = entry["failure_message"] or failure_message
            entry["stack_trace"] = entry["stack_trace"] or stack_trace


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
        try:
            with open(offline_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
        except Exception as exc:  # noqa: BLE001 — reporting must never break a test run
            if reporter:
                reporter.write_line(
                    f"testforge: could not write results to {offline_path} ({exc})", yellow=True
                )
            return
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
