---
title: Development Guide
description: Local setup, environment variables, and day-to-day workflows for whisper-opencti
---

<!--
  Development guide for whisper-opencti.
  Provenance convention: every substantive section carries an HTML comment
  directly under its heading of the form
    source: <file(s)>, verified: <how it was checked>, date: <YYYY-MM-DD>
  Update it whenever you change the section.
  SAFETY: this file is committed. Env var NAMES and where to obtain values only
  — never the values themselves (the .env.example defaults below are working
  dev-only placeholders, safe to reference verbatim; see .env.example itself
  for the authoritative source).
-->

# Development

This is the day-to-day guide for developing whisper-opencti locally: what to
install, how to bring the stack up, every environment variable the project
reads, and how to run and debug it. For the CI pipeline and release process,
see [docs/ci-cd-guide.md](./ci-cd-guide.md) (this repo's CI/CD doc stays at
that filename — checkouts commonly sit on case-insensitive filesystems, so
never create a `docs/CICD.md` twin).

## Prerequisites
<!-- source: pyproject.toml, Dockerfile, Makefile, README.md (Installation → Requirements), verified: read all four files, date: 2026-07-18 -->

| Tool | Version | Check with |
|---|---|---|
| Python | `>=3.11` (`pyproject.toml` `requires-python`); the published image runs 3.12-alpine (`Dockerfile`) | `python3 --version` |
| pip | any recent version | `pip --version` |
| Docker Desktop (or compatible engine) | recent; **≥6 GB RAM** available to the engine (README) | `docker --version` |
| Docker Compose | v2 (Compose CLI plugin — the Makefile invokes `docker compose`, not the standalone `docker-compose` binary) | `docker compose version` |
| make | any (GNU make) | `make --version` |

No `.nvmrc`/`.tool-versions`/language-version-manager file is committed for Python (`.python-version` is gitignored, not tracked) — the only enforced pin is `requires-python = ">=3.11"` in `pyproject.toml`. **Gotcha observed while writing this doc**: running the test suite under a system Python older than 3.11 (e.g. 3.9) fails at collection with `ImportError: cannot import name 'UTC' from 'datetime'` (`src/connector/connector.py` uses `datetime.UTC`, added in 3.11) — make sure `python3 --version` reports `>=3.11` before creating a local venv.

macOS note: this checkout sits on a **case-insensitive filesystem** — `docs/architecture.md` and `docs/ci-cd-guide.md` are the canonical doc names; never create `docs/ARCHITECTURE.md` / `docs/CICD.md` alongside them.

## Setup
<!-- source: Makefile, .env.example, README.md (Installation, Local Dev Stack), verified: read all three files, date: 2026-07-18 -->

1. Clone and enter the repo:
   ```bash
   git clone https://github.com/whisper-sec/whisper-opencti.git
   cd whisper-opencti
   ```
   Expect: a normal working tree with `Makefile`, `src/`, `tests/`, `docs/` at the root.

2. Create your `.env` from the committed template — **required**, every `make` target that touches Docker Compose gates on this file existing (`Makefile`'s `_check-env` target):
   ```bash
   cp .env.example .env
   ```
   Expect: nothing prints; `.env` now exists (gitignored) alongside `.env.example`.

3. Edit `.env` and set a real `WHISPER_API_KEY` (obtained from Whisper Security). Every other variable in `.env.example` has a working dev default and does not need to change for local dev:
   ```bash
   $EDITOR .env
   ```
   Expect: the placeholder `WHISPER_API_KEY=dev-placeholder-key` lets the connector start and register with OpenCTI even before this step, but every enrichment call fails with `WhisperAuthError` until replaced.

4. (Optional — only needed to lint/test outside Docker, e.g. in an editor or before pushing) create a venv and install the lint + test toolchains:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r tests/test-requirements.txt -r requirements-dev.txt
   ```
   Expect: `tests/test-requirements.txt` pulls in `src/requirements.txt` itself (via `-r ../src/requirements.txt`, resolved relative to that file), so this one command installs runtime + test + lint/format dependencies together. This mirrors the command already established in `docs/ci-cd-guide.md`'s Local Development section. Note: `README.md`'s own "Local Setup" snippet (`pip install -r src/requirements.txt -r requirements-dev.txt`) omits `tests/test-requirements.txt` and so does not install `pytest`/`responses` — flagged as an open question below.

5. Bring up the dev stack (builds the connector from `./src/` via `Dockerfile`):
   ```bash
   make dev-up
   ```
   Expect: `docker compose ... up -d --build` runs, then the Makefile prints the OpenCTI URL and admin login read back from `.env`. First-time startup takes **2-3 minutes** while Elasticsearch initializes.

6. Log in to OpenCTI at the printed URL (`http://localhost:8080` with the committed defaults) using `OPENCTI_ADMIN_EMAIL` / `OPENCTI_ADMIN_PASSWORD` from `.env` (`admin@whisper.local` / `ChangeMe-dev-only` — dev-only, never reuse in production).

**Open question**: `README.md`'s "Local Setup" section documents `make docker-build` as a step, but no `docker-build` target exists in the `Makefile` (confirmed: `.PHONY` list and target definitions both omit it) — likely stale documentation in README, not a Makefile gap. To build the image directly instead, use `docker build -t whisper-opencti:test .` (see `docs/ci-cd-guide.md` → CI Workflow → Job 5).

## Env Vars
<!-- source: .env.example, docker-compose.base.yml, docker-compose.dev.yml, docker-compose.qa.yml, README.md (Configuration), verified: read all five files, cross-checked every ${VAR} reference against .env.example, date: 2026-07-18 -->

Single source of truth: **`.env.example`** (committed, working dev defaults for every key) → copy to `.env` (gitignored) per [Setup](#setup) above. The Makefile's Compose invocations (`--env-file .env`) read only `.env` — there is no fallback. The connector itself also accepts the same keys via a mounted `config.yml` (see `config.yml.sample`) as an alternative to environment variables, for deployments that don't use the dev/QA compose stacks.

| Name | Purpose | Required | Value from |
|---|---|---|---|
| `WHISPER_API_KEY` | Whisper Security API key; connector auth (`X-API-Key` header) | Yes (placeholder lets the connector start; enrichment fails with `WhisperAuthError` until replaced) | Whisper Security |
| `OPENCTI_VERSION` | OpenCTI platform + worker image tag pulled by both compose stacks | Yes (has a working default) | `.env.example`; keep in lockstep with the `pycti` pin in `src/requirements.txt` (see `docs/ci-cd-guide.md` → Version Management) |
| `OPENCTI_PORT` | Host port OpenCTI is exposed on | Yes (default `8080`) | `.env.example` — **both the dev and QA stacks bind this port and cannot run simultaneously** (see [Common Failures](#common-failures)) |
| `OPENCTI_BASE_URL` | Public base URL OpenCTI advertises to itself | Yes (default `http://localhost:8080`) | `.env.example` |
| `OPENCTI_ADMIN_EMAIL` | Bootstrap admin login | Yes (dev default) | `.env.example` |
| `OPENCTI_ADMIN_PASSWORD` | Bootstrap admin login | Yes (dev default — **dev only, never for production**) | `.env.example` |
| `OPENCTI_ADMIN_TOKEN` | API token; also passed to the connector and worker containers as `OPENCTI_TOKEN` | Yes (committed dev default) | `.env.example` |
| `OPENCTI_ENCRYPTION_KEY` | OpenCTI v7's required 32-byte base64 encryption key | Yes (committed dev/QA-only placeholder) | `.env.example`; generate a fresh one for anything beyond local dev/QA with `openssl rand -base64 32` |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | MinIO (S3-compatible) credentials for OpenCTI object storage | Yes (dev defaults) | `.env.example` |
| `RABBITMQ_DEFAULT_USER` / `RABBITMQ_DEFAULT_PASS` | RabbitMQ credentials | Yes (dev defaults: `guest`/`guest`) | `.env.example`; replace with strong credentials for production |
| `ELASTIC_MEMORY_SIZE` | Elasticsearch heap size (`-Xms`/`-Xmx`) | Yes (default `2G`) | `.env.example` |
| `CONNECTOR_ID` | UUIDv4 identifying this connector instance to OpenCTI | Yes (dev default provided) | `.env.example`; generate a fresh one per real instance with `uuidgen` and keep it stable across restarts |
| `WHISPER_CONNECTOR_VERSION` | GHCR image tag pulled by the **QA stack only** (`make qa-up`) | Yes, for the QA stack (default `v0.1.0`) | `.env.example`; set to a tag from the [releases page](https://github.com/whisper-sec/whisper-opencti/releases) to validate a specific release/RC |
| `WHISPER_API_URL` | Base URL of the Whisper graph API | Yes (default `https://graph.whisper.security`) | `.env.example` |

Additional connector-only variables exist (`CONNECTOR_TYPE`, `CONNECTOR_NAME`, `CONNECTOR_SCOPE`, `CONNECTOR_AUTO`, `CONNECTOR_LOG_LEVEL`, `WHISPER_MAX_TLP`) but are **hardcoded in `docker-compose.dev.yml`/`docker-compose.qa.yml`** rather than sourced from `.env` — see those files or `README.md`'s Configuration tables if you need to override one for a manual (non-compose) deployment.

No CI workflow reads any of the above — they are strictly local/QA-stack concerns (see `docs/ci-cd-guide.md` → Secrets & Env). Never commit `.env` or `config.yml` (both gitignored) — only `.env.example` / `config.yml.sample`, which carry placeholder values only.

## Run & Debug
<!-- source: Makefile, README.md (Deployment, Debugging), verified: read both files, date: 2026-07-18 -->

```bash
# Dev stack — connector built from ./src/ via Dockerfile
make dev-up          # build + start OpenCTI + deps + connector (~2-3 min first run, ES init)
make dev-status       # service status
make dev-logs         # tail logs across the whole stack
make dev-restart      # rebuild + restart just the connector container
make dev-down         # stop (preserve volumes)
make dev-clean        # stop + wipe volumes (fresh state)

# QA stack — pulls the published ghcr.io image at $WHISPER_CONNECTOR_VERSION
# NOTE: cannot run at the same time as the dev stack (both bind OPENCTI_PORT) —
# `make dev-down` first if the dev stack is up.
make qa-up
make qa-status
make qa-logs
make qa-restart       # pull latest image + restart just the connector
make qa-down
make qa-clean

# Tests / lint (see docs/ci-cd-guide.md for the full CI-parity breakdown)
make test             # pytest
make lint             # format check + flake8 + STIX-ID pylint
make format           # auto-fix isort + black
```

OpenCTI UI: the URL printed by `make dev-up`/`make qa-up` (`OPENCTI_BASE_URL` in `.env`, `http://localhost:8080` by default). Log in with `OPENCTI_ADMIN_EMAIL`/`OPENCTI_ADMIN_PASSWORD` from `.env`.

**Debugging a connector that doesn't appear in OpenCTI** (from `README.md`):
```bash
make dev-logs | grep connector-whisper    # or: docker logs <container>
```
If the container crash-loops, the log usually shows a config error (missing `OPENCTI_URL`/`OPENCTI_TOKEN`, invalid `CONNECTOR_ID`). If it's up but not listed in OpenCTI, it likely can't reach RabbitMQ — confirm the platform-side `RABBITMQ__` env vars and Docker network match. Confirm registration directly via GraphQL:
```bash
curl -fsS -X POST http://localhost:8080/graphql \
  -H "Authorization: Bearer $OPENCTI_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"{ connectors { name active connector_scope } }"}'
```

There is no documented way to point the dev/QA stacks at non-local (staging/shared) backend services — both `docker-compose.dev.yml` and `docker-compose.qa.yml` wire the connector to the `opencti` service defined in the same compose project (`OPENCTI_URL=http://opencti:8080`); this is an open question if remote-backend development is ever needed.

## Common Failures
<!-- source: Makefile (_check-env), README.md (Debugging), and a failure reproduced directly while writing this doc, date: 2026-07-18 -->

### `Error: .env not found`
- **Cause**: fresh clone — `.env` (gitignored) hasn't been created yet. Every Compose-touching `make` target gates on this via the `_check-env` target.
- **Fix**: `cp .env.example .env`, then edit `WHISPER_API_KEY`.

### Dev and QA stacks won't come up together / port 8080 already in use
- **Cause**: both `docker-compose.dev.yml` and `docker-compose.qa.yml` are overlaid on the same `docker-compose.base.yml`, which binds `"${OPENCTI_PORT}:8080"` — by default both stacks want host port `8080`. They **cannot run simultaneously**, even though they use different Compose project names (`whisper-opencti-dev` vs `whisper-opencti-qa`).
- **Fix**: `make dev-down` before `make qa-up` (or vice versa). `make qa-up`'s own output prints this reminder.

### `WhisperAuthError` on every enrichment
- **Cause**: `WHISPER_API_KEY` in `.env` is still the `.env.example` placeholder (`dev-placeholder-key`) or is otherwise invalid.
- **Fix**: set a real key in `.env`, then `make dev-restart` (or `make qa-restart`) to pick it up.

### Connector container crash-loops or never appears in OpenCTI
- **Cause**: usually a bad config (missing `OPENCTI_URL`/`OPENCTI_TOKEN`, invalid `CONNECTOR_ID`) if crash-looping; if the container is up but absent from the OpenCTI connector list, it usually can't reach RabbitMQ.
- **Fix**: `make dev-logs | grep connector-whisper`; verify the platform-side `RABBITMQ__` env vars and Docker network match; see [Run & Debug](#run--debug) for the GraphQL registration check.

### `pytest` fails at collection with `ImportError: cannot import name 'UTC' from 'datetime'`
- **Cause**: running under a Python interpreter older than 3.11 (e.g. the OS-provided Python 3.9) — `src/connector/connector.py` imports `datetime.UTC`, added in Python 3.11, and `pyproject.toml` pins `requires-python = ">=3.11"` accordingly.
- **Fix**: create the local venv with a 3.11+ interpreter (`python3 --version` to check first; use `python3.11`/`python3.12` explicitly if the default `python3` resolves to something older).

### isort reorders imports inside `.venv-sdk` / breaks pylint's plugin loading
- **Cause**: isort's default skip list only covers `.venv`/`venv`, not `.venv-sdk`; without the `extend_skip = [".venv-sdk"]` entry in `pyproject.toml`, isort recurses into that vendored SDK venv and rewrites import order inside installed site-packages (breaking order-dependent imports, e.g. `dill`, which pylint loads).
- **Fix**: keep `extend_skip = [".venv-sdk"]` in `pyproject.toml`'s `[tool.isort]` table — don't remove it if touching isort config (the rationale is documented in `pyproject.toml` itself).
