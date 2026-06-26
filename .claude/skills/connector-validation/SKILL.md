---
name: connector-validation
description: Runbook for validating an OpenCTI connector locally — lint (isort/black/flake8 + STIX-ID pylint), pytest, Docker build, dev-stack end-to-end enrichment, and the qa-handoff TC matrix. Use before opening or updating a PR, or whenever connector behavior changes.
---

# Connector validation runbook

Work top-to-bottom. Cheap checks first; the live e2e and QA matrix last.

## 1. Static checks (fast, always run)

```bash
make format      # isort + black in place
make lint        # isort + black --check + flake8 --ignore=E,W + STIX-ID pylint (10/10)
make test        # pytest (unit, no network)
```

Single test while iterating: `pytest tests/test_converter_to_stix.py::test_name`.

Deps (one venv): `pip install -r tests/test-requirements.txt -r requirements-dev.txt`. Python **3.12** is required, but the `connectors-sdk` install needs `>=3.11,<3.13` — if 3.12 is unavailable locally use a 3.11 venv for the lint/test tools.

Tests build config via the SDK **stub pattern** — `ConnectorSettings` ignores constructor kwargs, so `conftest.build_settings()` subclasses it and overrides `_load_config_dict` to inject a fixed dict (mirrors upstream `domaintools` tests). HTTP is mocked with `responses` in `test_whisper_client.py`.

## 2. Docker build

```bash
docker build -t whisper-opencti:test .
```

Confirms the image builds on `python:3.12-alpine` with the current deps. Note: `connectors-sdk` installs from `git+https://…@master`, so the Dockerfile needs `git` in its build-deps and `pycti` must be pinned to whatever the SDK@master currently requires (a mismatch fails the resolver).

## 3. Live end-to-end (dev stack)

```bash
make dev-up        # full OpenCTI + deps + connector from source (~2-3 min first run)
make dev-restart   # rebuild + restart connector only, after code changes
make dev-logs      # tail
make dev-clean     # stop + WIPE volumes (use for a clean idempotency check)
```

UI: http://localhost:8080 (`admin@whisper.local` / `ChangeMe-dev-only`). The connector retries registration cleanly while OpenCTI's ES initialises (clean one-line warnings, no traceback). Confirm `Connector registered with ID` in the logs before enriching.

Drive an enrichment via pycti and read the authoritative status from the work item:

```python
from pycti import OpenCTIApiClient
c = OpenCTIApiClient("http://localhost:8080", ADMIN_TOKEN, log_level="error")
o = c.stix_cyber_observable.create(observableData={"type":"IPv4-Addr","value":"8.8.8.8"}, update=True)
c.stix_cyber_observable.ask_for_enrichment(id=o["id"], connector_id=WHISPER_CONNECTOR_ID)
# work status: c.work.get_connector_works(WID) → match w["event_source_id"] == o["standard_id"]
#   → w["messages"][-1]["message"]  e.g. "Enriched 8.8.8.8 with N STIX objects"
```

Verify ingested objects carry **pycti-style IDs** (`relationship--<uuidv5>`, `note--…`, `location--…`) and that SCOs use stix2 defaults. For idempotency: enrich twice on a clean stack and confirm the relationship set is unchanged.

## 4. qa-handoff TC matrix

Cover [docs/qa-handoff.md](../../../docs/qa-handoff.md) TC-01…TC-20 per release candidate. Most are API-drivable; a few need env changes/restart (bad key, raised TLP) or are UI/playbook-only.

**Known stale facts (don't re-investigate as bugs — see memory `qa-handoff-stale-facts`):**
- **TC-06** seed `this-should-never-exist-12345.invalid` now exists in WhisperGraph → it enriches instead of returning "No Whisper data". Pick a fresh empty seed.
- **TC-08** the Whisper API returns HTTP 200 for any key (no auth enforcement) → a bad key can't produce `WhisperAuthError` live; that path is only unit-testable.

## Verdict discipline

Report PASS only when you ran the app and saw the change work at its surface (the work-item status / ingested objects), not when tests merely pass. Capture the status string and object counts as evidence. When in doubt, FAIL with the raw output.