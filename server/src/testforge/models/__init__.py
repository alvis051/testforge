"""Importing this module registers every table on ``Base.metadata``."""

from testforge.models.project import Project
from testforge.models.suite import Suite

__all__ = ["Project", "Suite"]
