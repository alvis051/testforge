"""Rule-based failure categorisation.

Two ordered passes: every rule is tried against ``failure_type`` first, and only
if none match is ``failure_message`` tried. A precise exception class therefore
beats a loose phrase — ``AssertionError`` whose message happens to mention a
refused connection is an assertion failure, not a connection failure.
"""

import re

UNCATEGORIZED = "uncategorized"

# (category, exception-class pattern, message pattern). Either pattern may be None.
RULES: list[tuple[str, str | None, str | None]] = [
    ("timeout", r"Timeout|TimedOut", r"timed?\s?out"),
    (
        "connection",
        r"Connection|Network|Socket",
        r"connection (refused|reset|aborted)|network is unreachable",
    ),
    (
        "permission",
        r"Permission|Forbidden|Unauthori[sz]ed",
        r"permission denied|forbidden|unauthori[sz]ed",
    ),
    ("not_found", r"NotFound|NoSuchElement", r"not found|no such (file|element|table|column)"),
    ("assertion", r"AssertionError", r"^\s*assert\b"),
]


def classify_failure(failure_type: str | None, failure_message: str | None) -> str:
    """Categorise one failure. Returns ``uncategorized`` when nothing matches.

    An ``uncategorized`` bucket that grows large is useful signal that the rules
    need extending for a given codebase — it is deliberately not a catch-all
    that silently absorbs everything.
    """
    if failure_type:
        for category, type_pattern, _ in RULES:
            if type_pattern and re.search(type_pattern, failure_type, re.IGNORECASE):
                return category
    if failure_message:
        for category, _, message_pattern in RULES:
            if message_pattern and re.search(message_pattern, failure_message, re.IGNORECASE):
                return category
    return UNCATEGORIZED
