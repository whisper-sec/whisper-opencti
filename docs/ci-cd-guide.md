---
title: CI/CD Pipeline Guide
description: Comprehensive guide to whisper-opencti GitHub Actions CI/CD setup, local development, Docker builds, and releases
---

# CI/CD Pipeline Guide

This guide explains the whisper-opencti CI/CD architecture, how to work with it locally, troubleshoot failures, and release new versions.

## Table of Contents

- [Overview](#overview)
- [CI Workflow](#ci-workflow)
- [Release Workflow](#release-workflow)
- [Local Development](#local-development)
- [Linting & Code Quality](#linting--code-quality)
- [Docker Images](#docker-images)
- [Version Management](#version-management)
- [Troubleshooting](#troubleshooting)
- [Release Process](#release-process)

---

## Overview

The whisper-opencti repository uses **GitHub Actions** for continuous integration and delivery. The setup follows OpenCTI-Platform/connectors upstream standards and ensures code quality through automated testing, linting, and validation.

### Key Components

- **CI Workflow**: Runs on push/PR to `main` or `develop`
  - 5 parallel jobs: format → flake8 → STIX-ID linter → tests → Docker build
  - All jobs must pass before merge

- **Release Workflow**: Runs on git tag `v*`
  - Builds multiplatform Docker images (amd64, arm64)
  - Publishes to GitHub Container Registry (ghcr.io)
  - Auto-generates GitHub Release with notes

- **Local Development**: Makefile commands for testing, linting, and stacks
  - Dev stack: Build from source for iteration
  - QA stack: Test published image in production-like environment

---

## CI Workflow

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
- Fatal errors only (E codes: indentation, syntax)
- Convention issues only (C codes: naming)
- Ignores: E and W codes (style warnings)

**If it fails**:
```bash
flake8 --ignore=E,W .    # See the errors
# Fix the fatal/convention issues manually
```

**Config**: `.flake8`
```
[flake8]
ignore = E,W
```

---

### Job 3: STIX-ID Linter

**Tools**: pylint 3.3.1 + custom vendored plugin

**Location**: `shared/pylint_plugins/check_stix_plugin/`

**What it checks**:

1. **no_generated_id_stix** — All SDOs must use pycti.*.generate_id()
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

2. **no-value-for-parameter** — Function calls have required arguments

3. **unused-import** — No dead imports

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

**Test Count**: 186 cases

**Test Files**:
- `tests/test_connector.py` — End-to-end connector behavior
- `tests/test_converter_to_stix.py` — STIX object mapping
- `tests/test_queries.py` — Cypher query generation
- `tests/test_result_parser.py` — Result parsing
- `tests/test_whisper_client.py` — HTTP client + retries
- `tests/test_settings.py` — Configuration validation
- `tests/test_main.py` — Startup/entrypoint

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

## Release Workflow

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
make test                              # Run all 186 tests
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

### Toolchain

| Tool | Version | Purpose | Config |
|------|---------|---------|--------|
| isort | 7.0.0 | Sort imports | pyproject.toml |
| black | 26.3.1 | Format code | pyproject.toml (line-length: 88) |
| flake8 | >=7,<8 | Basic linting | .flake8 (ignores E, W) |
| pylint | 3.3.1 | Deep linting | Vendored STIX plugin |
| astroid | 3.3.5 | AST parsing | — |
| pytest | latest | Unit tests | pyproject.toml |

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
COPY entrypoint.sh healthcheck.sh ./
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
# Output: "pycti==7.260701.0"

# Check local requirement
grep pycti src/requirements.txt
# Output: pycti==7.260701.0

# ✅ They match!
```

### Updating pycti

If connectors-sdk updates pycti, update these files together:

1. **src/requirements.txt** — Exact pin
   ```
   pycti==7.260701.0
   ```

2. **.env.example** — Platform version
   ```
   OPENCTI_VERSION=7.260701.0
   ```

3. **__metadata__/connector_manifest.json** — Support version
   ```json
   "support_version": ">=7.260701.0"
   ```

4. **the contributor guide** — Documentation reference
   ```
   Currently `7.260701.0`
   ```

All four must be in sync for consistency.

---

## Troubleshooting

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

**Error**: `no_generated_id_stix` — SDO missing pycti ID generation

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

# Update src/requirements.txt, .env.example, manifest, the contributor guide
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

## Release Process

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

| File | Purpose |
|------|---------|
| `.github/workflows/ci.yml` | CI pipeline definition |
| `.github/workflows/release.yml` | Release + Docker publish |
| `Makefile` | Local development commands |
| `Dockerfile` | Container definition |
| `pyproject.toml` | Tool configuration (black, isort, pytest) |
| `.flake8` | flake8 linting rules |
| `src/requirements.txt` | Python dependencies |
| `docker-compose.base.yml` | Shared services (OpenCTI, ES, RabbitMQ) |
| `docker-compose.dev.yml` | Dev stack (build from source) |
| `docker-compose.qa.yml` | QA stack (published image) |
| `shared/pylint_plugins/` | Vendored STIX-ID validation |

---

## Resources

- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [Docker Compose Documentation](https://docs.docker.com/compose/)
- [pytest Documentation](https://docs.pytest.org/)
- [OpenCTI Connector Development](https://docs.opencti.io/latest/development/connectors)
