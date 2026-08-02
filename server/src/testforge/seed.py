"""Seed a small, realistic demo project so a reviewer sees something on first run."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from testforge.models.project import Project
from testforge.services.case_service import CaseService
from testforge.services.project_service import ProjectService
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


def seed_demo(session: Session) -> dict[str, int]:
    existing = session.scalar(select(Project).where(Project.key == "CHK"))
    if existing is not None:
        return {"projects": 0, "suites": 0, "cases": 0}

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

    return {"projects": 1, "suites": 2, "cases": len(DEMO_CASES)}
