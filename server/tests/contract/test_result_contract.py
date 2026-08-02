"""The plugin's emitted JSON must validate against the server's own model.

This is the guard that stops the future S4 runner from drifting away from the
one result contract.
"""

import json

from testforge.schemas.results import ResultBatch

SUITE = """
import pytest

@pytest.mark.case("CHK-1")
def test_passes():
    assert True

@pytest.mark.case("CHK-2")
def test_fails():
    raise AssertionError("boom")

def test_unmarked():
    assert True
"""


def test_plugin_output_validates_against_the_server_model(pytester, tmp_path):
    pytester.makepyfile(test_suite=SUITE)
    out = tmp_path / "results.json"

    pytester.runpytest("--tf-offline", str(out), "--tf-project", "CHK")

    payload = json.loads(out.read_text())
    batch = ResultBatch.model_validate({"results": payload["results"]})

    assert len(batch.results) == 3
    assert {r.outcome for r in batch.results} == {"passed", "failed"}


def test_plugin_emits_no_fields_the_server_rejects(pytester, tmp_path):
    """``ResultIn`` sets ``extra='forbid'``, so an unknown key fails validation here."""
    pytester.makepyfile(test_suite=SUITE)
    out = tmp_path / "results.json"

    pytester.runpytest("--tf-offline", str(out), "--tf-project", "CHK")

    payload = json.loads(out.read_text())
    ResultBatch.model_validate({"results": payload["results"]})
