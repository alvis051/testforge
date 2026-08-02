# TestForge

Test case and test plan management with automation result ingestion — a system of record that
links the test cases you write to the automated tests that actually run.

Slice 1 (this repo's current state) covers projects, a suite tree, versioned test cases (manual
and automated), discovered automation links, ad-hoc runs, and result ingestion from pytest and
JUnit XML.

## Quickstart

```bash
make install
make seed
make dev
```

Then, in another shell:

```bash
uv run tf case list CHK
```

## Reporting results from your own suite

Install the plugin into the repository under test:

```bash
uv add --dev pytest-testforge
```

Mark a test with the case it covers:

```python
import pytest


@pytest.mark.case("CHK-1")
def test_coupon_applies(): ...
```

Run it, reporting to a local server:

```bash
uv run pytest --tf-url http://localhost:8000 --tf-project CHK
```

Or write the payload to disk and upload it later — useful in air-gapped CI:

```bash
uv run pytest --tf-offline results.json --tf-project CHK
uv run tf run upload results.json
```

Importing an existing suite that has no markers yet is a useful first step: every result lands as
*unresolved*, which enumerates the tests still awaiting a case link.

```bash
uv run tf run import-junit CHK ./junit.xml ci-1234
```

## Design

- Specs: `docs/superpowers/specs/`
- The full product decomposition (S1–S8, including the LLM-evaluation subsystem) is in
  `docs/superpowers/specs/2026-08-02-test-management-core-design.md`.

Key properties:

- **Case keys are stable.** `CHK-42` never changes, so renames and suite moves don't orphan history.
- **Results pin a case version.** Editing a case later never rewrites what a past run executed.
- **Unresolved results are stored, not dropped.** A system of record that silently discards data
  isn't one.
- **One ingestion path.** The pytest plugin, JUnit import, and the future runner all land in
  `IngestionService.ingest`.
