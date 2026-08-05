from sqlalchemy import select, update
from sqlalchemy.orm import Session

from testforge.errors import AppError
from testforge.models.project import Project


class ProjectService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, key: str, name: str, description: str | None, actor: str) -> Project:
        existing = self.session.scalar(select(Project).where(Project.key == key))
        if existing is not None:
            raise AppError(
                "duplicate_project_key", f"project key {key} already exists", 409, {"key": key}
            )
        project = Project(key=key, name=name, description=description, created_by=actor)
        self.session.add(project)
        self.session.flush()
        return project

    def get(self, project_id: str) -> Project:
        """Lookup by id, for rows (runs, plans) that reference a project by id but need
        its human-facing key in the response."""
        project = self.session.get(Project, project_id)
        if project is None:
            raise AppError(
                "project_not_found",
                f"no project {project_id}",
                404,
                {"project_id": project_id},
            )
        return project

    def get_by_key(self, key: str) -> Project:
        project = self.session.scalar(select(Project).where(Project.key == key))
        if project is None:
            raise AppError("project_not_found", f"no project with key {key}", 404, {"key": key})
        return project

    def list(self) -> list[Project]:
        return list(self.session.scalars(select(Project).order_by(Project.key)))

    def allocate_case_key(self, project: Project) -> str:
        """Atomically reserve the next case number for this project."""
        next_seq = self.session.scalar(
            update(Project)
            .where(Project.id == project.id)
            .values(case_seq=Project.case_seq + 1)
            .returning(Project.case_seq)
        )
        self.session.refresh(project)
        return f"{project.key}-{next_seq}"
