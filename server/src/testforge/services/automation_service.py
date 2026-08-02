from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from testforge.models.automation import AutomationLink
from testforge.models.case import TestCase


class AutomationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def bind(
        self, *, case: TestCase, framework: str, test_identifier: str, seen_at: datetime
    ) -> AutomationLink:
        link = self.session.scalar(
            select(AutomationLink).where(
                AutomationLink.framework == framework,
                AutomationLink.test_identifier == test_identifier,
            )
        )
        if link is None:
            link = AutomationLink(
                test_case_id=case.id,
                framework=framework,
                test_identifier=test_identifier,
                first_seen_at=seen_at,
                last_seen_at=seen_at,
            )
            self.session.add(link)
        else:
            link.test_case_id = case.id
            link.last_seen_at = seen_at
            link.active = True
        self.session.flush()
        return link

    def links_for_case(self, case: TestCase) -> list[AutomationLink]:
        return list(
            self.session.scalars(
                select(AutomationLink)
                .where(AutomationLink.test_case_id == case.id)
                .order_by(AutomationLink.test_identifier)
            )
        )
