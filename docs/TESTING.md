<!--
  Testing reference. Edit freely — updates gap-fill missing or stale sections,
  and human-written content is preserved. Keep the top-level headings stable:
  tooling and reviewers locate policy by these headings.
  Commands listed here must match the Makefile — that is what actually runs;
  this doc explains and expands it.
  Provenance convention: every substantive section carries, directly under its
  heading, an HTML comment of the form
    source: <file/command/PR the claims came from>, verified: <how it was checked>, date: <YYYY-MM-DD>
  Update it whenever you change the section.
-->

# Testing

## Test Pyramid
<!-- source: pyproject.toml [tool.pytest.ini_options], tests/*.py, docs/qa-handoff.md, docs/scenarios/; verified: read config, ran `pytest -q` and `pytest --collect-only -q` in .venv-sdk; date: 2026-07-18 -->

whisper-opencti has exactly one automated layer, unit, plus a manual, human-run acceptance layer. There is no automated integration or e2e suite.

| Layer | Framework | Lives in | What belongs here |
|---|---|---|---|
| unit | pytest | `tests/test_*.py` (7 files, fixtures in [conftest.py](../tests/conftest.py)) | Pure-function tests (`queries.py`, `result_parser.py`, `converter_to_stix.py`), the HTTP boundary mocked with `responses` (`whisper_client.py`), settings validation, and connector orchestration with `helper`/`client` test doubles. No live network calls anywhere in this layer. |
| integration | none (deliberate gap) | - | [docs/architecture.md](architecture.md) §7 says this directly: "no integration test against the live Whisper API in CI." |
| e2e | manual QA pass | [docs/qa-handoff.md](qa-handoff.md) + [docs/scenarios/](scenarios/) | A person runs `make qa-up` (pulls the published GHCR image) against a full OpenCTI stack and works through the TC-01 to TC-20 test-case matrix by hand. Not automated, not a CI gate. |

Running the suite locally (`.venv-sdk/bin/python -m pytest -q`, checked 2026-07-18) collects and passes **200 tests**. [architecture.md](architecture.md) §7 and [ci-cd-guide.md](ci-cd-guide.md) "Job 4: Tests" previously said 197 (a stale count against a live collection run) and have been reconciled to 200 as part of the 2026-07-18 verification pass.

CI runs the suite twice, for different reasons. `.github/workflows/ci.yml`'s `test` job runs plain `pytest` on Python 3.11 as one of five gating jobs. `.github/workflows/ci-tests-connectors.yml` additionally reinstalls `pycti` from `OpenCTI-Platform/opencti@master` before running on Python 3.12, plus a daily cron, specifically to catch `connectors-sdk`-side pin drift before it reaches a push - see that workflow's header comment for the reasoning.

## Commands
<!-- source: Makefile (`test:` target), pyproject.toml [tool.pytest.ini_options], tests/test-requirements.txt; verified: read files, ran commands locally; date: 2026-07-18 -->

`make test` is the canonical entry point. It assumes test dependencies are already installed - it does not install anything itself.

```bash
# One-time setup (per the Makefile's `test:` target comment)
pip install -r tests/test-requirements.txt

# Full suite
make test
# ...which is exactly:
pytest

# Single file
pytest tests/test_connector.py

# Single test
pytest tests/test_connector.py::test_name

# Filter by keyword
pytest -k "domain"

# Verbose output / shorter tracebacks
pytest -vv
pytest --tb=short
```

`pyproject.toml`'s `[tool.pytest.ini_options]` fixes `testpaths = ["tests"]`, `pythonpath = ["."]`, and `addopts = "-ra"` (short summary of everything except passes), so bare `pytest` run from the repo root is equivalent to `pytest tests`. There is no integration or e2e test command; see the Test Pyramid above for what covers that gap instead.

## Conventions & Naming
<!-- source: tests/test_queries.py and other tests/*.py; verified: read implementation; date: 2026-07-18 -->

- File naming: `tests/test_*.py`. Tests sit in a single flat `tests/` directory at the repo root rather than mirroring `src/connector/`'s package layout - one file per source module (`test_queries.py` for `queries.py`, `test_whisper_client.py` for `whisper_client.py`, and so on), plus `test_connector.py` for end-to-end orchestration and `test_main.py` for the entry point.
- Test names read as a sentence describing behavior and expected outcome, not `test_1` or `test_happy_path`: `test_execute_cypher_401_raises_auth_error`, `test_get_query_json_escapes_value_for_safety`. No test classes anywhere - plain module-level functions throughout.
- Style is arrange-act-assert, usually with a short comment explaining why an assertion matters rather than restating the code. Exemplar, from [tests/test_queries.py](../tests/test_queries.py):

  ```python
  def test_get_query_substitutes_value_and_limit_into_literals():
      q = get_query_for_entity_type("IPv4-Addr", value="8.8.8.8", limit=42)
      assert q is not None
      assert '"8.8.8.8"' in q
      assert "LIMIT 42" in q
      # No placeholders should remain - Whisper doesn't accept params.
      assert "$value" not in q
      assert "$limit" not in q
  ```

## Fixtures & Helpers
<!-- source: tests/conftest.py; verified: read implementation; date: 2026-07-18 -->

All shared fixtures live in [conftest.py](../tests/conftest.py):

- `helper` - a `MagicMock` standing in for `OpenCTIConnectorHelper`, with `stix2_create_bundle` wired to actually serialize the objects it's given, so a test can `json.loads` the call args and inspect the real bundle shape.
- `client` - a `MagicMock(spec=WhisperClient)`, the injection seam `WhisperConnector(client=...)` accepts.
- `make_config` - a factory returning real `ConnectorSettings` instances, built through a `StubConnectorSettings` subclass that overrides SDK config loading. Tests get real Pydantic validation without touching env vars or `config.yml`. Call `make_config(max_tlp="TLP:AMBER")` etc. to override the `whisper:` block.
- `config` - convenience alias for `make_config()` with no overrides.
- `connector` - a fully wired `WhisperConnector(helper, config, client)` for end-to-end callback tests.
- `_v7_payload(...)` - a module-level helper (not a fixture) that builds the v7 `_process_message` callback dict, so every test constructs the same shape instead of hand-rolling it.

One thing this suite deliberately never mocks: STIX object construction itself. `test_converter_to_stix.py` asserts against real `stix2` objects and real `pycti.*.generate_id` output rather than a mocked mapper - that's what actually catches ID-generation regressions, the property the whole enrichment loop depends on for idempotency (see [docs/architecture.md](architecture.md) §3.7). Only the Whisper HTTP boundary (`responses` library) and the OpenCTI side (`helper` mock) are test doubles.

## Coverage Targets
<!-- source: .github/workflows/ci-tests-connectors.yml, tests/test-requirements.txt; verified: read workflow file; date: 2026-07-18 -->

No numeric coverage threshold is configured anywhere in this repo. `ci-tests-connectors.yml`'s CI job does generate a coverage report (`pytest --cov --cov-append --cov-report=xml`), but nothing consumes it: there's no `--cov-fail-under` and no Codecov upload (dropped deliberately, per that workflow's header comment - there's no `CODECOV_TOKEN` in this repo).

No coverage tooling enforces a percentage gate, so coverage is judged qualitatively: every behavior in [docs/SPECIFICATIONS.md](SPECIFICATIONS.md) should trace to at least one test, and vice versa (that doc's own header states this as its audit contract). The module-by-module test table and the "Known scope boundaries" list in [docs/architecture.md](architecture.md) §7-8 are the other qualitative references.

## Flakiness Policy
<!-- source: tests/test-requirements.txt, .github/workflows/ci.yml, .github/workflows/ci-tests-connectors.yml; verified: read files - no rerun/retry plugin or quarantine mechanism present; date: 2026-07-18 -->

No flakiness-handling mechanism is configured. `tests/test-requirements.txt` pulls in only `pytest` and `responses` - no `pytest-rerunfailures` or similar - and neither `ci.yml` nor `ci-tests-connectors.yml` retries the test job on failure. That tracks with how the suite is built: every HTTP call goes through `responses` mocks, and nothing depends on wall-clock timing, so there's currently nothing that needs quarantining. If a flaky test does turn up, treat it as a bug - most likely an unmocked timing- or network-dependent path - and fix the root cause rather than adding a retry. See the Never-Weaken Rule below.

## Never-Weaken Rule

Tests are the spec's enforcement arm. To make a suite pass, never delete an assertion, widen a tolerance, mark a test skipped, or loosen a type/fixture — without first proving the *intended behavior* changed and updating SPECIFICATIONS.md in the same change. If a test fails, the choices are: fix the code, or (when behavior legitimately changed) update spec + test together and say so in the PR. Agents and reviewers treat silent assertion-weakening as a blocking finding.
<!-- Project-specific exceptions to this rule (rare) go here, each with a reason. -->

No project-specific exceptions exist today.
