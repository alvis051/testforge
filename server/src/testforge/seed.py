"""Seed a small, realistic demo project so a reviewer sees something on first run."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from testforge.models.project import Project
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

DEMO_RESULTS = [
    ("CHK-1", "tests/test_checkout.py::test_coupon_applies", "passed", None, None),
    ("CHK-2", "tests/test_checkout.py::test_expired_coupon", "passed", None, None),
    (
        "CHK-3",
        "tests/test_checkout.py::test_payment_retry_prompt",
        "failed",
        "assert 'Retry payment' in 'Payment failed. Contact support.'",
        DEMO_STACK_TRACE,
    ),
    ("CHK-4", "tests/test_mobile.py::test_order_history", "skipped", None, None),
    ("CHK-5", "tests/test_auth.py::test_session_expiry", "passed", None, None),
    ("CHK-404", "tests/test_search.py::test_orphaned_check", "passed", None, None),
]


def seed_demo(session: Session) -> dict[str, int]:
    existing = session.scalar(select(Project).where(Project.key == "CHK"))
    if existing is not None:
        return {"projects": 0, "suites": 0, "cases": 0, "plans": 0, "runs": 0, "results": 0}

    projects = ProjectService(session)
    project = projects.create(
        key="CHK", name="Checkout", description="Demo storefront project", actor="seed"
    )

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
    run = runs.open_for_plan(
        plan=plan, name="nightly regression", external_id="demo-run-1", actor="seed"
    )
    executed_at = datetime.now(UTC)
    IngestionService(session).ingest(
        run=run,
        results=[
            ResultIn(
                case_key=case_key,
                test_identifier=identifier,
                framework="pytest",
                outcome=outcome,
                duration_ms=120,
                failure_message=message,
                stack_trace=trace,
                executed_at=executed_at,
            )
            for case_key, identifier, outcome, message, trace in DEMO_RESULTS
        ],
    )
    runs.complete(run.id)

    return {
        "projects": 1,
        "suites": 2,
        "cases": len(DEMO_CASES),
        "plans": 1,
        "runs": 1,
        "results": len(DEMO_RESULTS),
    }
