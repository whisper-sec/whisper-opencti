---
title: CI/CD Pipeline Guide
description: Comprehensive guide to whisper-opencti GitHub Actions CI/CD setup, local development, Docker builds, and releases
---

<!--
  This file is the repo's CI/CD reference and stays at docs/ci-cd-guide.md —
  checkouts commonly sit on case-insensitive filesystems (macOS); never create
  docs/CICD.md alongside it.
  Provenance convention: every substantive section carries an HTML comment
  directly under its heading of the form
    source: <file(s)>, verified: <how it was checked>, date: <YYYY-MM-DD>
  Update it whenever you change the section.
-->

# CI/CD Pipeline Guide

This guide explains the whisper-opencti CI/CD architecture, how to work with it locally, troubleshoot failures, and release new versions.

## Table of Contents

- [Overview](#overview)
- [Pipeline Map](#pipeline-map)
- [Branch → Deploy Mapping](#branch--deploy-mapping)
- [CI Workflow](#ci-workflow)
- [Connector Verified Linter](#connector-verified-linter)
- [Tests Connectors (Upstream Parity)](#tests-connectors-upstream-parity)
- [Unused Dependencies Check](#unused-dependencies-check)
- [PR Hygiene Checks](#pr-hygiene-checks)
- [Release Workflow](#release-workflow)
- [Local Development](#local-development)
- [Linting & Code Quality](#linting--code-quality)
- [Docker Images](#docker-images)
- [Version Management](#version-management)
- [Secrets & Env](#secrets--env)
- [Troubleshooting](#troubleshooting)
- [Release Process](#release-process)
- [Best Practices](#best-practices)
- [Key Files](#key-files)
- [Resources](#resources)

---

## Overview
<!-- source: .github/workflows/*.yml (7 files), verified: read all 7 workflow files in full, date: 2026-07-18 -->

The whisper-opencti repository uses **GitHub Actions** for continuous integration and delivery, across **7 workflows**. Several of them deliberately replicate checks from upstream `OpenCTI-Platform/connectors` (tracked under issue #89) — this repo is the standalone development home of a connector that gets ported upstream to `internal-enrichment/whisper/`, so CI here is built to catch anything that would fail upstream's own CI before that port happens. Do not "modernize" or diverge this toolchain from upstream without a deliberate decision. See [Pipeline Map](#pipeline-map) for the full list of workflows.

### Key Components

- **CI** (`ci.yml`): Runs on push/PR to `main` or `develop`
  - 5 parallel jobs: format → flake8 → STIX-ID linter → tests → Docker build
  - All jobs must pass before merge

- **Connector Verified Linter** (`ci-connector-verified-linter.yml`), **Tests Connectors** (`ci-tests-connectors.yml`), **Unused Dependencies** (`ci-unused-deps.yml`)
  - Upstream-parity checks that reproduce specific jobs from `OpenCTI-Platform/connectors`' own CI — see their dedicated sections below

- **PR hygiene** (`gh-do-not-merge-label.yml`, `gh-pr-check-conventions.yml`)
  - Blocks merge on a `do not merge` label, unsigned commits, or a non-conventional PR title

- **Release** (`release.yml`): Runs on git tag `v*`
  - Builds multiplatform Docker images (amd64, arm64)
  - Publishes to GitHub Container Registry (ghcr.io)
  - Auto-generates GitHub Release with notes

- **Local Development**: Makefile commands for testing, linting, and stacks
  - Dev stack: Build from source for iteration
  - QA stack: Test published image in production-like environment

---

## Pipeline Map
<!-- source: .github/workflows/{ci,ci-connector-verified-linter,ci-tests-connectors,ci-unused-deps,gh-do-not-merge-label,gh-pr-check-conventions,release}.yml, verified: read each file in full, date: 2026-07-18 -->

| Workflow | File / location | Trigger | What it does |
|---|---|---|---|
| CI | `.github/workflows/ci.yml` | push to `main`/`develop`; PR to `main`/`develop` | 5 jobs: `format` (isort + black check) → `flake8` (`--ignore=E,W`) → `stix_id_linter` (pylint + vendored `no_generated_id_stix` plugin) → `test` (pytest) → `build` (docker buildx, validation only, no push) |
| Connector Verified Linter | `.github/workflows/ci-connector-verified-linter.yml` | `pull_request` (any); push to `main`/`develop` | Stages the repo into a synthetic `internal-enrichment/whisper/` monorepo layout, runs upstream's `connector-linter` tool (installed via pip from `OpenCTI-Platform/connectors`), fails on any `::error` annotation not covered by `.github/vclint-allowlist.txt` |
| Tests Connectors | `.github/workflows/ci-tests-connectors.yml` | push to `main`/`develop`; `pull_request`; daily cron `0 6 * * *` (UTC); `workflow_dispatch` | Replicates upstream's `run_test.sh`: isolated `uv` venv, installs `tests/test-requirements.txt`, force-installs `pycti` from `OpenCTI-Platform/opencti@master`, runs `uv pip check` (pycti/connectors-sdk drift gate), then pytest with coverage. The daily cron catches upstream pycti-pin drift even without a push here. |
| Unused Dependencies (warning) | `.github/workflows/ci-unused-deps.yml` | push to `main`/`develop`; PR to `main`/`develop` | Runs `deptry` against `src/` vs `src/requirements.txt`; posts findings to the job summary as warnings. **Never fails the build** (warning-only, matches upstream) |
| Do Not Merge label check | `.github/workflows/gh-do-not-merge-label.yml` | PR `opened`/`reopened`/`labeled`/`unlabeled`/`synchronize` | Fails if the PR carries a `do not merge` label |
| PR conventions checks | `.github/workflows/gh-pr-check-conventions.yml` | PR `opened`/`edited`/`reopened`/`ready_for_review`/`synchronize` | 3 jobs: `check-signed-commits` (blocking), `check-pr-issue` (advisory here, `continue-on-error: true`), `check-pr-title-convention` (blocking, skipped for `renovate[bot]`) |
| Release | `.github/workflows/release.yml` | push of tag `v*` | Builds linux/amd64 + linux/arm64 image, pushes to `ghcr.io/whisper-sec/whisper-opencti`, creates a GitHub Release with auto-generated notes |

---

## Branch → Deploy Mapping
<!-- source: .github/workflows/release.yml, README.md (Production / External Deployment), verified: read release.yml + README, date: 2026-07-18 -->

This repository does not auto-deploy to a running environment on branch push — there is no staging/production server this CI pushes to. The only "deploy" is publishing a versioned connector image, triggered by **tag push**, not by branch push.

| Branch / ref | Environment | URL | Deploy trigger |
|---|---|---|---|
| `develop` (integration branch, `git.defaultBranch`) | none — CI checks only | n/a | Push/PR runs the checks in [Pipeline Map](#pipeline-map); nothing is deployed |
| `main` (protected) | none — CI checks only | n/a | Same as above |
| tag `v*` (e.g. `v1.0.0`) | GitHub Container Registry image | `https://github.com/whisper-sec/whisper-opencti/pkgs/container/whisper-opencti` | `release.yml` on tag push. The image is then consumed manually — `make qa-up` pulls it into the local QA stack, or an operator drops the [`docker-compose.yml`](../docker-compose.yml) snippet into their own OpenCTI deployment per README's "Production / External Deployment" section |

**Open question**: there is no automated promotion from a merged PR / tag to a live "production" instance this team operates — consumers (QA, external OpenCTI operators) pull a tagged image themselves. Confirm this is intentional rather than a gap if a managed environment is ever introduced.

---

## CI Workflow
<!-- source: .github/workflows/ci.yml, verified: read file in full, date: 2026-07-18 -->

### Trigger

```
┌─────────────────────────────────────────┐
│ Trigger: Push to main/develop OR PR    │
└─────────────────────────────────────────┘
                    │
                    ↓
         GitHub Actions CI starts
              (5 parallel jobs)
```

### Job 1: Format Check

**Tools**: isort 7.0.0, black 26.3.1

**What it checks**:
- Import ordering consistency (isort)
- Code formatting consistency (black)

**If it fails**:
```bash
make format    # Auto-fixes formatting
git add .
git commit -m "style: auto-format code"
git push
```

**Config**: `pyproject.toml`
```toml
[tool.black]
line-length = 88
target-version = ["py311"]

[tool.isort]
profile = "black"
line_length = 88
```

---

### Job 2: Flake8 Linting

**Tool**: flake8 >=7,<8

**What it checks**:
- E and W codes (pycodestyle style/indentation) are explicitly ignored via the `--ignore=E,W` CLI flag - black and isort own formatting instead
- What's left active is pyflakes (F codes: unused imports, undefined names, other logic errors); no `--max-complexity` is set, so mccabe's C9 complexity check never trips, and no naming-convention plugin (e.g. `pep8-naming`) is installed

**If it fails**:
```bash
flake8 --ignore=E,W .    # See the errors
# Fix the pyflakes issues manually
```

**Config**: `.flake8` only excludes directories - the `E,W` ignore itself is a CLI flag (`ci.yml`, `Makefile`'s `lint` target), not stored here:
```
[flake8]
extend-exclude =
    .venv,
    .venv-sdk,
    .git,
    __pycache__,
    .pytest_cache,
    .ruff_cache,
    build,
    dist,
    *.egg-info
```

---

### Job 3: STIX-ID Linter

**Tools**: pylint 3.3.1 + custom vendored plugin

**Location**: `shared/pylint_plugins/check_stix_plugin/`

**What it checks**:

1. **no_generated_id_stix** - All SDOs must use pycti.*.generate_id()
   - Examples of violations:
     ```python
     # ❌ WRONG: Creating SDO without pycti ID generator
     identity = Identity(name="My Source")  # Missing id=
     
     # ✅ CORRECT: Using pycti.Identity.generate_id()
     identity = Identity(
         id=pycti.Identity.generate_id(name="My Source"),
         name="My Source"
     )
     ```

2. **no-value-for-parameter** - Function calls have required arguments

3. **unused-import** - No dead imports

**Why it matters**: Ensures STIX objects deduplicate correctly across re-enrichments and multiple connector runs.

**If it fails**:
```bash
cd shared/pylint_plugins/check_stix_plugin
PYTHONPATH=. pylint ../../../src ../../../tests \
  --disable=all \
  --enable=no_generated_id_stix,no-value-for-parameter,unused-import \
  --load-plugins linter_stix_id_generator

# Look for: no_generated_id_stix violations
# Fix: Add pycti.X.generate_id() to SDO constructors
```

---

### Job 4: Tests

**Tool**: pytest

**Test Count**: 200 cases (`.venv-sdk/bin/python -m pytest --collect-only -q`, checked 2026-07-18 - see `docs/TESTING.md`'s Test Pyramid)

**Test Files**:
- `tests/test_connector.py` - End-to-end connector behavior
- `tests/test_converter_to_stix.py` - STIX object mapping
- `tests/test_queries.py` - Cypher query generation
- `tests/test_result_parser.py` - Result parsing
- `tests/test_whisper_client.py` - HTTP client + retries
- `tests/test_settings.py` - Configuration validation
- `tests/test_main.py` - Startup/entrypoint

**If it fails**:
```bash
make test              # Run all tests
pytest tests/test_connector.py  # Single file
pytest tests/test_connector.py::test_name  # Single test

# With output
pytest -vv
```

**Config**: `pyproject.toml`
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-ra"
```

---

### Job 5: Docker Build

**Tool**: Docker buildx (multiplatform)

**What it does**:
- Builds Dockerfile (validation only, no push)
- Supports multiplatform setup (linux/amd64 ready)

**Common failures**:

| Failure | Cause | Fix |
|---------|-------|-----|
| `Cannot install ... pycti==7.260626.0` | Version mismatch with connectors-sdk | Update `src/requirements.txt` to match connectors-sdk pin |
| `fatal: Repository not found` (git) | git not in build deps | Check Dockerfile line 30 includes git in .build-deps |
| `ModuleNotFoundError: libmagic` | libmagic1 not installed | Verify Dockerfile line 29 installs libmagic |

**If it fails**:
```bash
docker build -t whisper-opencti:test .    # Build locally to see full error
docker build --no-cache -t whisper-opencti:test .  # Fresh build
```

---

## Connector Verified Linter
<!-- source: .github/workflows/ci-connector-verified-linter.yml, .github/vclint-allowlist.txt, verified: read both files in full, date: 2026-07-18 -->

**Mirrors**: upstream `OpenCTI-Platform/connectors` `.github/workflows/ci-connector-verified-linter.yml` (the `vclint` job), adapted for this single-connector repo (issue #89).

**What it does**:

1. Stages this repo's tracked files into `$RUNNER_TEMP/monorepo/internal-enrichment/whisper/` via `rsync`, excluding everything that exists here for internal/dev purposes but was never part of the upstream port (`.github`, `docs`, `Makefile`, the compose files, `.env.example`, `shared`, `tools`, etc.) — this reproduces the monorepo layout the linter expects.
2. Installs `connector-linter` via pip from `OpenCTI-Platform/connectors` (`shared/tools/connector_linter` subdirectory) — the tool's source lives upstream, not in this repo.
3. Runs `connector-linter check internal-enrichment/whisper` twice: once for github-format `::error`/`::warning` annotations, once for a markdown summary (written to `$GITHUB_STEP_SUMMARY`).
4. Filters the `::error` annotations against `.github/vclint-allowlist.txt` (fixed-string patterns via `grep -F`, one per line, each requiring a `# reason:` comment) — the job fails iff any non-allowlisted `::error` remains.

**Current allowlist entries** (`.github/vclint-allowlist.txt`):

| Code | Pattern | Reason |
|---|---|---|
| `VC202` | `"container_image": "ghcr.io/whisper-sec/whisper-opencti"` vs expected `"opencti/connector-whisper"` | This repo publishes its own image; the upstream image name only applies once the connector is merged and released upstream |
| `VC401` | Image name `whisper-sec/whisper-opencti` vs expected `opencti/connector-whisper` | Same reason — the user-facing `docker-compose.yml` snippet references the image actually published from this repo |

**If it fails**: read the job summary / `::error` annotations for the specific `VCxxx` code and fix the underlying issue (usually in `src/`, `__metadata__/connector_manifest.json`, or `docker-compose.yml`). If it's a *new*, intentional deviation from upstream convention (not one of the two already documented above), add an entry to `.github/vclint-allowlist.txt` with a `# reason:` comment rather than letting the job go red without explanation.

---

## Tests Connectors (Upstream Parity)
<!-- source: .github/workflows/ci-tests-connectors.yml, verified: read file in full, date: 2026-07-18 -->

**Mirrors**: upstream `OpenCTI-Platform/connectors` `.github/workflows/ci-tests-connectors.yml` ("Tests Connectors") together with the `run_test.sh` pipeline it invokes — inlined here as named steps since this repo has exactly one connector (no changed-connector detection matrix).

**What it does** (job `test`, `RELEASE_REF=master`):

1. `uv venv -p 3.12 .temp_venv` — an isolated virtual environment, separate from the plain-pip `test` job in `ci.yml` (which uses Python 3.11).
2. `uv pip install -r tests/test-requirements.txt`.
3. Uninstalls the pinned `pycti` and force-installs the latest from `OpenCTI-Platform/opencti@master` (`client-python` subdirectory) — the upstream-parity point: this job tests against tomorrow's `pycti`, not today's pin.
4. `uv pip check` — fails if the freshly installed `pycti` no longer satisfies `connectors-sdk`'s exact pin. This is the drift gate.
5. `python -m pytest tests --cov --cov-append --cov-report=xml --junitxml=test_outputs/tests/junit.xml -q -rA`.
6. On failure only: a diagnostic step prints the local `src/requirements.txt` pycti pin next to the current `connectors-sdk@master` pin.

**Also runs on a daily cron** (`0 6 * * *` UTC) and `workflow_dispatch`, in addition to push/PR — this repo's own addition, not upstream's: it catches `connectors-sdk@master` bumping its `pycti` pin (which would break `uv pip check` here) even on days nobody pushes to this repo. Codecov upload steps present upstream are dropped here (no `CODECOV_TOKEN` in this repo).

**If it fails** (`uv pip check` step): read the "Diagnose pycti pin drift" step's output for the current `connectors-sdk@master` pin, bump `pycti` in `src/requirements.txt` to match, then update `.env.example` (`OPENCTI_VERSION`) and `__metadata__/connector_manifest.json` (`support_version`) in lockstep — see [Version Management](#version-management).

---

## Unused Dependencies Check
<!-- source: .github/workflows/ci-unused-deps.yml, verified: read file in full, date: 2026-07-18 -->

**Mirrors**: upstream `OpenCTI-Platform/connectors` `.github/workflows/ci-unused-deps.yml`, adapted for this single-connector repo (`deptry` runs from inside `src/` rather than from the repo root, to avoid the root `pyproject.toml`'s PEP 621 `[project]` table being picked up as the dependency source instead of `requirements.txt`).

**What it does**: runs `deptry==0.25.1` against `src/` (checked against `src/requirements.txt`), looking for `DEP002` (package listed in requirements but never imported). Two packages are pre-ignored as runtime-implicit (never imported directly), matching upstream's `.github/deptry-package-map.txt` convention:

- `PyYAML` — parsed by connectors-sdk/pycti at runtime for `config.yml`; pinned only to control the version.
- `pydantic-settings` — backs connectors-sdk's `BaseConnectorSettings`; never imported directly by the connector.

**This check never fails the build** — findings are posted to the job's `$GITHUB_STEP_SUMMARY` as warnings only, matching upstream's convention. PR-comment plumbing that upstream has is dropped here.

**If it flags a package**:
- False positive (imported under a different name than the package name, e.g. `PyYAML` → `import yaml`): add it to `IGNORE_PKGS` in `.github/workflows/ci-unused-deps.yml`.
- Genuinely unused: remove it from `src/requirements.txt`.

---

## PR Hygiene Checks
<!-- source: .github/workflows/gh-do-not-merge-label.yml, .github/workflows/gh-pr-check-conventions.yml, verified: read both files in full, date: 2026-07-18 -->

Two workflows gate PR metadata rather than code:

**`gh-do-not-merge-label.yml`** (mirrors upstream as-is, no adaptation): fails whenever the PR carries a label literally named `do not merge`. Remove the label to unblock.

**`gh-pr-check-conventions.yml`** (mirrors upstream, with deviations noted inline in the file): three jobs, all triggered on PR `opened`/`edited`/`reopened`/`ready_for_review`/`synchronize`:

| Job | Blocking? | What it checks |
|---|---|---|
| `check-signed-commits` | Yes | Every commit in the PR is signed (`1Password/check-signed-commits-action@v1`) |
| `check-pr-issue` | **No** — `continue-on-error: true` (deviation from upstream, which blocks; internal PRs don't always reference an issue) | PR has at least one linked closing issue (`gh pr view --json closingIssuesReferences`) |
| `check-pr-title-convention` | Yes (skipped for `renovate[bot]`) | PR title matches conventional-commit format: types `feat\|fix\|docs\|style\|refactor\|perf\|test\|build\|ci\|chore\|revert`, optional scope, optional `!`, and a trailing issue reference like `(#123)` unless the title contains `(deps)` (`FiligranHQ/filigran-ci-tools/actions/pr-title-check@main`) |

Also skipped entirely vs. upstream: the "check-organization" job (`FiligranHQ/auto-label` — tags PRs from the FiligranHQ org; not applicable here).

---

## Release Workflow
<!-- source: .github/workflows/release.yml, verified: read file in full, date: 2026-07-18 -->

### Trigger

```
┌─────────────────────────────────────────┐
│ git tag v1.0.0 && git push origin v1.0.0│
└─────────────────────────────────────────┘
                    │
                    ↓
     Release workflow starts
     (build + push + release notes)
```

### Steps

1. **Version Extraction**
   - Tag: `v1.0.0` → Version: `1.0.0`
   - Tag: `v1.0.0-rc1` → Prerelease: true

2. **Multiplatform Build**
   - Setup QEMU for arm64 emulation
   - Build for linux/amd64 + linux/arm64
   - Single manifest covers both architectures

3. **Push to ghcr.io**
   - Authenticate with `GHCR_PUSH_TOKEN` secret
   - Tags:
     - `ghcr.io/whisper-sec/whisper-opencti:v1.0.0` (always)
     - `ghcr.io/whisper-sec/whisper-opencti:latest` (only if non-prerelease)

4. **Create GitHub Release**
   - Auto-generate release notes from commits since last tag
   - Mark as prerelease if version contains hyphen (e.g., `v1.0.0-rc1`)
   - Link to published container image

### Example Release

```bash
# Release v1.0.0
git tag -a v1.0.0 -m "Release v1.0.0: isinstance fixes and pycti bump"
git push origin v1.0.0

# Release workflow runs:
# - Builds + pushes image to ghcr.io/whisper-sec/whisper-opencti:v1.0.0
# - Tags with :latest (non-prerelease)
# - Creates GitHub Release

# Pre-release v1.0.0-rc1
git tag -a v1.0.0-rc1 -m "Release candidate 1"
git push origin v1.0.0-rc1

# Release workflow:
# - Pushes image with :v1.0.0-rc1 only (no :latest)
# - Marks GitHub Release as prerelease
```

---

## Local Development
<!-- source: Makefile, .env.example, README.md, verified: read all three files, date: 2026-07-18 -->

### Setup

```bash
# 1. Clone repo
git clone https://github.com/whisper-sec/whisper-opencti.git
cd whisper-opencti

# 2. Copy environment template
cp .env.example .env

# 3. Edit .env to add WHISPER_API_KEY
vi .env

# 4. Install Python deps (optional, for local linting/testing)
python -m venv venv
source venv/bin/activate
pip install -r tests/test-requirements.txt -r requirements-dev.txt
```

### Dev Stack (Build from Source)

```bash
make dev-up          # Start OpenCTI + deps + connector (built from ./src/)
make dev-logs        # Tail logs
make dev-status      # Show service status
make dev-restart     # Rebuild + restart connector container
make dev-down        # Stop (preserve volumes)
make dev-clean       # Stop + wipe volumes (fresh state)
```

**Time to first enrichment**: 2-3 minutes (Elasticsearch initialization)

**Access**:
- OpenCTI: http://localhost:8080
- Login: admin@whisper.local / ChangeMe-dev-only (from .env.example)

### QA Stack (Published Image)

```bash
make qa-up           # Start stack with published ghcr.io image
make qa-logs         # Tail logs
make qa-restart      # Pull latest image + restart connector
make qa-down         # Stop
make qa-clean        # Stop + wipe
```

**Use for**: Testing a released version in production-like environment

### Running Tests

```bash
make test                              # Run all 200 tests
pytest                                 # Same
pytest tests/test_connector.py          # Single file
pytest tests/test_connector.py::test_name  # Single test
pytest -k "domain"                     # Tests matching pattern
pytest -vv                             # Verbose output
pytest --tb=short                      # Short traceback
```

### Linting Locally

```bash
make lint            # Check all: format + flake8 + STIX-ID pylint
make format          # Auto-fix formatting (isort + black)

# Individual checks
isort --profile black --check-only .
black --check .
flake8 --ignore=E,W .
```

---

## Linting & Code Quality
<!-- source: Makefile (lint/format targets), pyproject.toml, .flake8, requirements-dev.txt, verified: read all four files, date: 2026-07-18 -->

### Toolchain

| Tool | CI version (`ci.yml`) | Local version (`requirements-dev.txt`) | Purpose | Config |
|------|---------|---------|---------|--------|
| isort | 7.0.0 | 5.13.2 | Sort imports | pyproject.toml |
| black | 26.3.1 | 26.3.1 | Format code | pyproject.toml (line-length: 88) |
| flake8 | >=7,<8 | >=7,<8 | Basic linting | .flake8 (ignores E, W) |
| pylint | 3.3.1 | 3.3.1 | Deep linting | Vendored STIX plugin |
| astroid | 3.3.5 | 3.3.5 | AST parsing | - |
| pytest | 9.0.3 (`tests/test-requirements.txt`) | 9.0.3 (`tests/test-requirements.txt`) | Unit tests | pyproject.toml |

**isort version note**: CI's `format` job installs isort 7.0.0 in its own venv (no pylint alongside it). Locally, `requirements-dev.txt` pins isort **5.13.2** instead — pylint 3.3.1 declares `isort<6`, so a single local venv running both isort and pylint needs isort 5.x; black-profile output is functionally identical between the two for this codebase's import shapes (per the comment in `requirements-dev.txt`). This is an intentional, documented deviation, not drift — CI still runs the split-venv 7.0.0 check.

### Formatting Standards

- **Line length**: 88 characters
- **Import order**: isort with black profile
- **Indentation**: 4 spaces
- **Quotes**: Double quotes (black standard)

### Matching GitHub Actions Locally

To replicate CI exactly:

```bash
# Install exact versions
pip install isort==7.0.0 black==26.3.1 'flake8>=7,<8' pylint==3.3.1 astroid==3.3.5

# Run the 5-job equivalent
make format && make test && make lint
```

If all pass, GitHub Actions CI will pass.

---

## Docker Images
<!-- source: Dockerfile, verified: read file in full, date: 2026-07-18 -->

### Base Image

```dockerfile
FROM python:3.12-alpine
```

### Why Alpine?

- **Small**: ~40MB base vs ~160MB with debian
- **Final image**: ~150MB vs ~400MB
- **Build fast**: Fewer dependencies to install
- **Runtime small**: Less attack surface

### Build Optimization

**Layer 1**: Runtime libraries (libmagic, libffi)
```dockerfile
RUN apk add --no-cache libmagic libffi
```

**Layer 2**: Build tools (temporary, removed after pip)
```dockerfile
RUN apk add --no-cache --virtual .build-deps gcc musl-dev libffi-dev git && \
    pip install --no-cache-dir -r requirements.txt && \
    apk del .build-deps
```

**Layer 3**: Source code + scripts
```dockerfile
COPY src/ ./src/
COPY healthcheck.sh ./
```

**Layer 4**: Non-root user (UID 10001 = OpenCTI convention)
```dockerfile
RUN addgroup -S connector && \
    adduser -S -G connector -u 10001 connector && \
    chown -R connector:connector /opt/connector
USER connector
```

### Health Check

```dockerfile
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD ./healthcheck.sh
```

- **Check frequency**: Every 60 seconds
- **Timeout**: 5 seconds per check
- **Grace period**: 30 seconds (allow OpenCTI/RabbitMQ connection time)
- **Failure threshold**: 3 consecutive failed checks

---

## Version Management
<!-- source: src/requirements.txt, .env.example, __metadata__/connector_manifest.json, README.md (Installation → Requirements), verified: read all four files, date: 2026-07-18 -->

### The Critical Rule

**pycti + connectors-sdk must use compatible versions**

Why?
- connectors-sdk pins `pycti==X` in its pyproject.toml
- Mismatch → pip resolver conflict → Docker build failure

### How to Check Compatibility

```bash
# Check what connectors-sdk requires
curl https://raw.githubusercontent.com/OpenCTI-Platform/connectors/master/connectors-sdk/pyproject.toml \
  | grep '"pycti'
# Output (checked 2026-07-18): "pycti==7.260715.0"

# Check local requirement
grep pycti src/requirements.txt
# Output: pycti==7.260715.0

# In sync as of 2026-07-18 (bumped after upstream's 7.260715.0 release broke
# resolution repo-wide for three days). Upstream cuts date-versioned releases
# roughly weekly and each one moves the sdk's pycti pin, so this drift WILL
# recur: it is the exact thing `uv pip check` in ci-tests-connectors.yml's
# daily cron watches for. When it fires, bump src/requirements.txt (and
# .env.example / the manifest's support_version in lockstep, below).
```

### Updating pycti

If connectors-sdk updates pycti, update these files together:

1. **src/requirements.txt** - Exact pin
   ```
   pycti==7.260701.0
   ```

2. **.env.example** - Platform version
   ```
   OPENCTI_VERSION=7.260701.0
   ```

3. **__metadata__/connector_manifest.json** - Support version
   ```json
   "support_version": ">=7.260701.0"
   ```

All three must be in sync for consistency.

---

## Secrets & Env
<!-- source: .github/workflows/*.yml (grepped for `secrets\.`), .gitignore, verified: grep across all 7 workflow files, date: 2026-07-18 -->

CI needs exactly two tokens; everything else the pipelines touch is either public or generated at runtime.

| Name | Purpose | Stored in |
|---|---|---|
| `GHCR_PUSH_TOKEN` | Authenticates `docker/login-action` push to `ghcr.io` and the `softprops/action-gh-release` GitHub Release creation, both in `release.yml` | GitHub Actions repository secret |
| `GITHUB_TOKEN` | Default Actions token; used in `gh-pr-check-conventions.yml` for `gh pr view` (linked-issue check) and to create the PR-title check run | Auto-provided by GitHub Actions per run — not a repo-configured secret |

No workflow reads `WHISPER_API_KEY`, an OpenCTI token, or any other value from `.env` / `config.yml` — those are strictly local/QA-stack concerns (`.env` and `config.yml` are both gitignored and never committed; only the placeholder-valued `.env.example` / `config.yml.sample` are committed). See [Local Development → Setup](#setup) here, and the `docs/DEVELOPMENT.md` Env Vars section, for the local/QA-stack variable list.

---

## Troubleshooting
<!-- source: .github/workflows/*.yml, .github/vclint-allowlist.txt, verified: read files + cross-checked against each job's steps, date: 2026-07-18 -->

### Format Job Fails

**Error**: `isort` or `black` check fails

**Fix**:
```bash
make format          # Auto-fixes
git add .
git commit -m "style: auto-format code"
git push
```

---

### Flake8 Job Fails

**Error**: `fatal syntax error` or `indentation error`

**Fix**:
```bash
flake8 --ignore=E,W .   # See which lines
# Fix manually, then re-run
```

---

### STIX-ID Linter Fails

**Error**: `no_generated_id_stix` - SDO missing pycti ID generation

**Fix**:
```python
# ❌ Wrong
identity = Identity(name="My Source")

# ✅ Correct
identity = Identity(
    id=pycti.Identity.generate_id(name="My Source"),
    name="My Source"
)
```

Check: `src/connector/converter_to_stix.py` for SDO/relationship/note construction.

---

### Test Job Fails

**Error**: pytest failure

**Fix**:
```bash
make test                          # Run all tests
pytest tests/test_connector.py -vv # Verbose output
# Look for assertion failures or exceptions
```

Common causes:
- Mock response shape changed
- STIX object construction incorrect
- Result parser logic error

---

### Docker Build Fails

**Error**: `Cannot install -r requirements.txt (line 4) and pycti==7.260626.0 because these package versions have conflicting dependencies`

**Root cause**: pycti version mismatch

**Fix**:
```bash
# Check connectors-sdk requirement
grep pycti src/requirements.txt

# Update to match connectors-sdk@master
curl https://raw.githubusercontent.com/OpenCTI-Platform/connectors/master/connectors-sdk/pyproject.toml | grep '"pycti'

# Update src/requirements.txt, .env.example, manifest
```

---

### Docker Build Fails: "fatal: Repository not found"

**Root cause**: git not available during pip install

**Check Dockerfile**:
```dockerfile
# Line 30 should include git in .build-deps
apk add --no-cache --virtual .build-deps gcc musl-dev libffi-dev git
```

---

### Connector Verified Linter Fails

**Error**: job `vclint` fails with "Non-allowlisted connector-linter errors found"

**Cause**: `connector-linter` found a Verified-Connector convention violation (`VCxxx`) that isn't already covered by `.github/vclint-allowlist.txt`.

**Fix**:
```bash
# Reproduce locally: stage the repo the way the job does, then run the linter
pip install 'git+https://github.com/OpenCTI-Platform/connectors.git@master#subdirectory=shared/tools/connector_linter'
mkdir -p /tmp/monorepo/internal-enrichment/whisper
rsync -a --exclude='.git' --exclude='.github' ./ /tmp/monorepo/internal-enrichment/whisper/
cd /tmp/monorepo && connector-linter check internal-enrichment/whisper --format github
```
Fix the underlying issue, or — if it's an intentional, already-understood deviation from upstream convention (like the two existing image-name entries) — add a new pattern to `.github/vclint-allowlist.txt` with a `# reason:` comment.

---

### Tests Connectors: `uv pip check` Fails

**Error**: `uv pip check` reports a dependency conflict after the job force-installs `pycti` from `OpenCTI-Platform/opencti@master`

**Cause**: `connectors-sdk@master` bumped its exact `pycti` pin, and the `src/requirements.txt` pin is now behind it. Can surface on the daily `0 6 * * *` cron run even without a push.

**Fix**:
```bash
# Same comparison the job's failure-only diagnostic step runs:
grep pycti src/requirements.txt
curl -fsSL https://raw.githubusercontent.com/OpenCTI-Platform/connectors/master/connectors-sdk/pyproject.toml | grep pycti
# Bump src/requirements.txt to match, then update .env.example (OPENCTI_VERSION)
# and __metadata__/connector_manifest.json (support_version) in lockstep.
```

---

### Unused Dependencies Job Flags a Package

**Error**: `$GITHUB_STEP_SUMMARY` lists `⚠ <pkg> is listed in src/requirements.txt but not imported in src` — **this never fails the build**, it's warning-only.

**Fix**: if the package is used under a different import name (e.g. `PyYAML` → `import yaml`), add it to `IGNORE_PKGS` in `.github/workflows/ci-unused-deps.yml`. If truly unused, remove it from `src/requirements.txt`.

---

### PR Blocked by "do not merge" Label

**Cause**: the PR carries a label literally named `do not merge` (`gh-do-not-merge-label.yml`).

**Fix**: remove the label from the PR.

---

### PR Conventions Checks Fail

**Error**: one of the three `gh-pr-check-conventions.yml` jobs is red

**Cause / Fix**:
- `check-signed-commits` (blocking): a commit in the PR isn't signed — sign it per GitHub's [commit-signing docs](https://docs.github.com/en/authentication/managing-commit-signature-verification/signing-commits).
- `check-pr-title-convention` (blocking, skipped for `renovate[bot]`): PR title doesn't match `type(scope)!: subject (#123)` (types: `feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert`; the trailing `(#123)` issue reference is required unless the title contains `(deps)`) — rename the PR.
- `check-pr-issue` (**advisory only** here, `continue-on-error: true`): no linked issue found — link one with `Fixes #123` / `Closes #123` / `Resolves #123` in the PR description if you want the check green; it does not block merge in this repo.

---

## Release Process
<!-- source: .github/workflows/release.yml, pyproject.toml, verified: read release.yml + confirmed version field in pyproject.toml, date: 2026-07-18 -->

### Step-by-Step

```bash
# 1. Ensure all PRs merged to main
git checkout main
git pull origin main

# 2. Verify version in pyproject.toml
grep version pyproject.toml
# "1.0.0"

# 3. Create annotated tag (message auto-used in release notes)
git tag -a v1.0.0 -m "Release v1.0.0: isinstance fixes and pycti 7.260701.0"

# 4. Push tag (triggers Release workflow)
git push origin v1.0.0

# 5. Monitor in GitHub
# - https://github.com/whisper-sec/whisper-opencti/actions
# - Release workflow builds + pushes image to ghcr.io

# 6. Verify image published
# - https://github.com/whisper-sec/whisper-opencti/pkgs/container/whisper-opencti
# - Pull: docker pull ghcr.io/whisper-sec/whisper-opencti:v1.0.0
```

### Prerelease

For release candidates, add a hyphen to version:

```bash
git tag -a v1.0.0-rc1 -m "Release candidate 1"
git push origin v1.0.0-rc1

# Release workflow:
# - Pushes image with :v1.0.0-rc1 only (no :latest tag)
# - Marks GitHub Release as prerelease (pre-release checkbox checked)
```

---

## Best Practices

1. **Always run `make lint && make test` before pushing**
   - Catches 95% of CI failures locally
   - Saves time and reduces noise

2. **Keep pycti + connectors-sdk synchronized**
   - Version mismatch = Docker build failure
   - Check when connectors-sdk updates

3. **Use dev stack for iteration, QA stack for validation**
   - Dev: Fast feedback loop during development
   - QA: Validate released image works in prod-like setup

4. **Tag releases with semantic versioning**
   - v1.0.0 (release)
   - v1.0.0-rc1 (prerelease)
   - Version determines `:latest` tag + prerelease flag

5. **Review GitHub Actions logs**
   - Detailed output from each job
   - Actionable error messages
   - Same commands work locally to reproduce

---

## Key Files
<!-- source: .github/workflows/, repo root listing, verified: read each workflow file + ls repo root, date: 2026-07-18 -->

| File | Purpose |
|------|---------|
| `.github/workflows/ci.yml` | CI pipeline definition (format, flake8, STIX-ID lint, tests, docker build) |
| `.github/workflows/ci-connector-verified-linter.yml` | Upstream Verified-Linter parity check (`connector-linter`) |
| `.github/workflows/ci-tests-connectors.yml` | Upstream Tests-Connectors parity check + daily pycti-drift cron |
| `.github/workflows/ci-unused-deps.yml` | `deptry` unused-dependency check (warning-only) |
| `.github/workflows/gh-do-not-merge-label.yml` | Blocks merge while a "do not merge" label is present |
| `.github/workflows/gh-pr-check-conventions.yml` | Signed-commit, linked-issue (advisory), PR-title convention checks |
| `.github/workflows/release.yml` | Release + Docker publish |
| `.github/vclint-allowlist.txt` | Documented, intentional connector-linter deviations |
| `Makefile` | Local development commands |
| `Dockerfile` | Container definition |
| `pyproject.toml` | Tool configuration (black, isort, pytest) + package version |
| `.flake8` | flake8 linting rules |
| `src/requirements.txt` | Python runtime dependencies (incl. `pycti` pin) |
| `requirements-dev.txt` | Lint/format toolchain (isort, black, flake8, pylint, astroid) |
| `tests/test-requirements.txt` | Test-runtime dependencies (pulls in `src/requirements.txt` + pytest + responses) |
| `docker-compose.base.yml` | Shared services (OpenCTI, ES, RabbitMQ, MinIO, worker) |
| `docker-compose.dev.yml` | Dev stack (build from source) |
| `docker-compose.qa.yml` | QA stack (published image) |
| `docker-compose.yml` | Connector-only snippet for an existing external OpenCTI deployment |
| `shared/pylint_plugins/` | Vendored STIX-ID validation |

---

## Resources

- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [Docker Compose Documentation](https://docs.docker.com/compose/)
- [pytest Documentation](https://docs.pytest.org/)
- [OpenCTI Connector Development](https://docs.opencti.io/latest/development/connectors)
