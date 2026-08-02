import re
from datetime import datetime
from xml.etree import ElementTree

from testforge.errors import AppError
from testforge.schemas.results import ResultIn

CASE_KEY_PATTERN = re.compile(r"\b([A-Z][A-Z0-9]*-\d+)\b")


def parse_junit(
    xml: str, *, framework: str = "junit", default_executed_at: datetime
) -> list[ResultIn]:
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise AppError("invalid_result_payload", f"could not parse JUnit XML: {exc}", 422) from exc

    results: list[ResultIn] = []
    for testcase in root.iter("testcase"):
        classname = testcase.get("classname", "")
        name = testcase.get("name", "")
        identifier = f"{classname}::{name}" if classname else name

        outcome = "passed"
        failure_type = failure_message = stack_trace = None
        for child in testcase:
            if child.tag in {"failure", "error"}:
                outcome = "failed" if child.tag == "failure" else "error"
                failure_type = child.get("type")
                failure_message = child.get("message")
                stack_trace = (child.text or "").strip() or None
            elif child.tag == "skipped":
                outcome = "skipped"

        results.append(
            ResultIn(
                case_key=_case_key(testcase, name),
                test_identifier=identifier,
                framework=framework,
                outcome=outcome,
                duration_ms=_duration_ms(testcase.get("time")),
                failure_type=failure_type,
                failure_message=failure_message,
                stack_trace=stack_trace,
                executed_at=default_executed_at,
            )
        )
    return results


def _case_key(testcase: ElementTree.Element, name: str) -> str | None:
    for prop in testcase.iter("property"):
        if prop.get("name") == "case_key" and prop.get("value"):
            return prop.get("value")
    match = CASE_KEY_PATTERN.search(name)
    return match.group(1) if match else None


def _duration_ms(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(round(float(raw) * 1000))
    except ValueError:
        return None
