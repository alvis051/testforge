"""Importing this module registers every table on ``Base.metadata``."""

from testforge.models.automation import AutomationLink
from testforge.models.case import Tag, TestCase, TestCaseVersion, test_case_tags
from testforge.models.plan import TestPlan, TestPlanCase
from testforge.models.project import Project
from testforge.models.run import Result, Run
from testforge.models.run_job import RunJob
from testforge.models.suite import Suite

__all__ = [
    "AutomationLink",
    "Project",
    "Result",
    "Run",
    "RunJob",
    "Suite",
    "Tag",
    "TestCase",
    "TestCaseVersion",
    "TestPlan",
    "TestPlanCase",
    "test_case_tags",
]
