from sqlalchemy import select
from sqlalchemy.orm import Session

from testforge.errors import AppError
from testforge.models.project import Project
from testforge.models.suite import Suite


class SuiteService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, project: Project, name: str, parent_id: str | None, actor: str) -> Suite:
        path = "/"
        if parent_id is not None:
            parent = self.get(parent_id)
            path = f"{parent.path}{parent.id}/"
        suite = Suite(
            project_id=project.id, parent_id=parent_id, name=name, path=path, created_by=actor
        )
        self.session.add(suite)
        self.session.flush()
        return suite

    def get(self, suite_id: str) -> Suite:
        suite = self.session.get(Suite, suite_id)
        if suite is None:
            raise AppError("suite_not_found", f"no suite {suite_id}", 404, {"suite_id": suite_id})
        return suite

    def list_for_project(self, project: Project) -> list[Suite]:
        stmt = (
            select(Suite).where(Suite.project_id == project.id).order_by(Suite.path, Suite.position)
        )
        return list(self.session.scalars(stmt))

    def move(self, suite_id: str, new_parent_id: str | None) -> Suite:
        suite = self.get(suite_id)
        old_prefix = f"{suite.path}{suite.id}/"

        if new_parent_id is None:
            new_path = "/"
        else:
            if new_parent_id == suite_id:
                raise AppError(
                    "suite_cycle", "a suite cannot be its own parent", 409, {"suite_id": suite_id}
                )
            parent = self.get(new_parent_id)
            if parent.path.startswith(old_prefix) or parent.path == old_prefix:
                raise AppError(
                    "suite_cycle",
                    "cannot move a suite into its own descendant",
                    409,
                    {"suite_id": suite_id, "new_parent_id": new_parent_id},
                )
            new_path = f"{parent.path}{parent.id}/"

        descendants = list(
            self.session.scalars(select(Suite).where(Suite.path.startswith(old_prefix)))
        )
        suite.parent_id = new_parent_id
        suite.path = new_path
        new_prefix = f"{new_path}{suite.id}/"
        for descendant in descendants:
            descendant.path = new_prefix + descendant.path[len(old_prefix) :]
        self.session.flush()
        return suite
