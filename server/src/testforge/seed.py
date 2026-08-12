"""Seed a small, realistic demo project so a reviewer sees something on first run."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from testforge.models.project import Project
from testforge.models.run_job import RunJob
from testforge.schemas.results import ResultIn
from testforge.services.case_service import CaseService
from testforge.services.ingestion_service import IngestionService
from testforge.services.plan_service import PlanService
from testforge.services.project_service import ProjectService
from testforge.services.run_service import RunService
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

DEMO_STACK_TRACE = """Traceback (most recent call last):
  File "tests/test_checkout.py", line 42, in test_payment_retry_prompt
    assert "Retry payment" in page.text
AssertionError: assert 'Retry payment' in 'Payment failed. Contact support.'"""

DEMO_REPO_URL = "https://github.com/alvis051/testforge.git"
DEMO_TEST_COMMAND = "pytest examples/demo_suite -q"
#: A stand-in commit for the seeded job. Nothing resolves it; it exists so the run
#: detail page has a realistic value to render.
DEMO_RESOLVED_SHA = "0f1e2d3c4b5a69788796a5b4c3d2e1f005040302"

DEMO_RUN_COUNT = 6

#: Per-case outcome sequence across the demo runs, oldest first.
#: CHK-1 recovers on its own twice, so it is flaky. CHK-3 breaks once and stays
#: broken, so it is a regression and must NOT be flagged — the demo exists to
#: show the dashboard telling those apart. CHK-4 is always skipped, which the
#: scorer ignores entirely. The final column reproduces the run Slice 5a's
#: Playwright specs already assert against.
DEMO_SEQUENCES = {
    "CHK-1": "PPFPFP",
    "CHK-2": "PPPPPP",
    "CHK-3": "PPPFFF",
    "CHK-4": "SSSSSS",
    "CHK-5": "PPPPPP",
    "CHK-404": "PPPPPP",
}

OUTCOME_BY_LETTER = {"P": "passed", "F": "failed", "S": "skipped"}

DEMO_IDENTIFIERS = {
    "CHK-1": "tests/test_checkout.py::test_coupon_applies",
    "CHK-2": "tests/test_checkout.py::test_expired_coupon",
    "CHK-3": "tests/test_checkout.py::test_payment_retry_prompt",
    "CHK-4": "tests/test_mobile.py::test_order_history",
    "CHK-5": "tests/test_auth.py::test_session_expiry",
    "CHK-404": "tests/test_search.py::test_orphaned_check",
}

#: (case_key, run index) -> (failure_type, failure_message, stack_trace).
#: Chosen so the category breakdown has four distinct buckets, one of which
#: deliberately falls through to "uncategorized".
DEMO_FAILURE_DETAIL = {
    ("CHK-1", 2): ("TimeoutError", "Timed out after 30s waiting for the coupon field", None),
    ("CHK-1", 4): ("ConnectionError", "Connection refused by payments-sandbox:8443", None),
    ("CHK-3", 3): (None, "Cart total drifted by 0.01 between render and submit", None),
    ("CHK-3", 4): (
        "AssertionError",
        "assert 'Retry payment' in 'Payment failed. Contact support.'",
        DEMO_STACK_TRACE,
    ),
    ("CHK-3", 5): (
        "AssertionError",
        "assert 'Retry payment' in 'Payment failed. Contact support.'",
        DEMO_STACK_TRACE,
    ),
}


def seed_demo(session: Session) -> dict[str, int]:
    existing = session.scalar(select(Project).where(Project.key == "CHK"))
    if existing is not None:
        return {
            "projects": 0,
            "suites": 0,
            "cases": 0,
            "plans": 0,
            "runs": 0,
            "results": 0,
            "jobs": 0,
        }

    projects = ProjectService(session)
    project = projects.create(
        key="CHK", name="Checkout", description="Demo storefront project", actor="seed"
    )
    project.repo_url = DEMO_REPO_URL
    project.default_ref = "main"
    project.test_command = DEMO_TEST_COMMAND
    session.flush()

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

    plan = PlanService(session).create(
        project=project,
        name="Release 1.0 regression",
        description="Everything tagged for the 1.0 release.",
        milestone="1.0",
        environment="staging",
        case_keys=[],
        filter_={"tag": "checkout"},
        actor="seed",
    )

    runs = RunService(session)
    ingestion = IngestionService(session)
    now = datetime.now(UTC)
    result_count = 0

    for index in range(DEMO_RUN_COUNT):
        # One run per day, oldest first, so the trend chart has a real x-axis.
        started = now - timedelta(days=DEMO_RUN_COUNT - 1 - index)
        run = runs.open_for_plan(
            plan=plan,
            name=f"nightly regression {started.date().isoformat()}",
            external_id=f"demo-run-{index + 1}",
            actor="seed",
        )
        run.started_at = started
        session.flush()

        batch = []
        for case_key, sequence in DEMO_SEQUENCES.items():
            failure_type, failure_message, stack_trace = DEMO_FAILURE_DETAIL.get(
                (case_key, index), (None, None, None)
            )
            batch.append(
                ResultIn(
                    case_key=case_key,
                    test_identifier=DEMO_IDENTIFIERS[case_key],
                    framework="pytest",
                    outcome=OUTCOME_BY_LETTER[sequence[index]],
                    duration_ms=120,
                    failure_type=failure_type,
                    failure_message=failure_message,
                    stack_trace=stack_trace,
                    executed_at=started,
                )
            )
        ingestion.ingest(run=run, results=batch)
        result_count += len(batch)

        completed = runs.complete(run.id)
        completed.completed_at = started
        if index == DEMO_RUN_COUNT - 1:
            # The newest run is runner-sourced so the job panel has something to show.
            # Converting a run rather than adding a seventh keeps every existing
            # assertion — run counts and trend dot counts alike — unchanged.
            run.source = "runner"
            session.add(
                RunJob(
                    run_id=run.id,
                    status="succeeded",
                    repo_url=DEMO_REPO_URL,
                    git_ref="main",
                    resolved_sha=DEMO_RESOLVED_SHA,
                    command=DEMO_TEST_COMMAND,
                    case_keys_json=["CHK-1", "CHK-2", "CHK-3"],
                    claimed_by="seed-worker:1",
                    claimed_at=started,
                    attempts=1,
                    exit_code=1,
                    output_tail="==== 4 passed, 1 failed, 1 skipped in 3.20s ====",
                    created_at=started,
                    finished_at=started,
                )
            )
        session.flush()

    return {
        "projects": 1,
        "suites": 2,
        "cases": len(DEMO_CASES),
        "plans": 1,
        "runs": DEMO_RUN_COUNT,
        "results": result_count,
        "jobs": 1,
    }
