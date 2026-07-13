---
name: ci-cd-pipeline
description: How to use, troubleshoot, and optimize the whisper-opencti CI/CD pipeline — local checks, GitHub Actions workflows, Docker builds, and releases
---

# CI/CD Pipeline Guide

This skill covers the whisper-opencti CI/CD setup: local development checks, GitHub Actions workflows, Docker builds, and release process.

## Quick Checks Before Pushing

Always run these locally before pushing to avoid CI failures:

```bash
make lint          # Check formatting + all linters (isort, black, flake8, pylint)
make test          # Run 197 unit tests
make format        # Auto-fix formatting issues (isort + black)
```

If all pass locally, your PR will almost certainly pass GitHub Actions CI.

---

## GitHub Actions CI Workflow

**Trigger**: Push to `main`/`develop` or open a PR to `main`/`develop`

**Runs 5 jobs in parallel** (all must pass):

### 1. **format** — Code formatting check
- Tools: isort 7.0.0, black 26.3.1
- Checks: `isort --profile black --check-only .` + `black --check .`
- **Fix**: `make format` then push

### 2. **flake8** — Basic linting
- Tool: flake8 >=7,<8
- Scope: Ignores E, W codes (only reports fatal/convention errors)
- **Fix**: Run `flake8 --ignore=E,W .` locally to see issues

### 3. **stix_id_linter** — STIX ID validation
- Tools: pylint 3.3.1 + custom `linter_stix_id_generator` plugin
- Checks:
  - `no_generated_id_stix` — All SDOs must use pycti.*.generate_id()
  - `no-value-for-parameter` — Functions have required arguments
  - `unused-import` — No dead imports
- **Why**: Ensures STIX objects deduplicate correctly
- **Fix**: See the actual pylint output; typically missing pycti.ID.generate_id() calls

### 4. **test** — Unit tests
- Tool: pytest
- Count: 197 test cases
- **Fix**: `make test` locally to diagnose failures

### 5. **build** — Docker image build
- Tool: Docker buildx (multiplatform)
- Scope: Validates Dockerfile builds successfully (no push)
- **Common failures**:
  - `pycti==7.260626.0` doesn't match connectors-sdk requirement (should be 7.260701.0)
  - Missing `git` in build deps (needed for connectors-sdk git+https install)
  - Runtime dependency mismatch (check requirements.txt)
- **Fix**: Verify pycti version in `src/requirements.txt` matches connectors-sdk@master pin

---

## GitHub Actions Release Workflow

**Trigger**: Create a git tag matching `v*` (e.g., `v1.0.0`)

**What it does**:
1. Extract version from tag (v1.0.0 → 1.0.0)
2. Determine prerelease status (if version has hyphen, it's a prerelease)
3. Build Docker image for multiple platforms (linux/amd64, linux/arm64)
4. Push to ghcr.io with tags:
   - `ghcr.io/whisper-sec/whisper-opencti:v1.0.0` (always)
   - `ghcr.io/whisper-sec/whisper-opencti:latest` (only if non-prerelease)
5. Auto-generate GitHub Release notes from commits

**Release a new version**:

```bash
# 1. Ensure develop is merged to main
git checkout main
git pull

# 2. Create an annotated tag
git tag -a v1.0.1 -m "Release v1.0.1"

# 3. Push the tag (triggers Release workflow)
git push origin v1.0.1

# 4. Monitor in GitHub Actions → Release workflow
# → Image appears at ghcr.io/whisper-sec/whisper-opencti:v1.0.1
```

---

## Local Development Stacks

### Dev Stack — Build from source

```bash
make dev-up        # Start: OpenCTI + deps + connector (built from ./src/)
make dev-restart   # Rebuild + restart just connector container
make dev-logs      # Tail logs
make dev-down      # Stop (keep volumes)
make dev-clean     # Stop + wipe volumes
```

**Use for**: Iterative development, testing code changes, debugging

### QA Stack — Validate published image

```bash
make qa-up         # Start: OpenCTI + published connector image from ghcr.io
make qa-restart    # Pull latest + restart connector
make qa-logs       # Tail logs
make qa-down       # Stop (keep volumes)
make qa-clean      # Stop + wipe volumes
```

**Use for**: Testing a released version, validating prod-like setup

---

## Version Management

### Critical Rule: pycti + connectors-sdk must match

- **connectors-sdk** pins pycti==X in its pyproject.toml
- **src/requirements.txt** must pin the SAME version
- If versions mismatch → Docker build fails

**How to check**:

```bash
# Check what connectors-sdk requires
curl https://raw.githubusercontent.com/OpenCTI-Platform/connectors/master/connectors-sdk/pyproject.toml | grep '"pycti'

# Verify local version
grep pycti src/requirements.txt

# They should match!
```

**If updating pycti**, update these files together:
- `src/requirements.txt` (pycti pin)
- `.env.example` (OPENCTI_VERSION)
- `__metadata__/connector_manifest.json` (support_version)
- `CLAUDE.md` (version reference)

---

## Troubleshooting CI Failures

| Failure | Root Cause | Fix |
|---------|-----------|-----|
| `format` job fails | Code not formatted correctly | `make format` |
| `flake8` job fails | Fatal linting errors | `flake8 --ignore=E,W .` to see issues |
| `stix_id_linter` fails | Missing pycti.*.generate_id() calls | Check converter_to_stix.py for SDO/rel/note ID generation |
| `test` job fails | Unit test failure | `make test` locally to reproduce |
| `build` job fails | Docker build error | Check pycti version, requirements.txt deps, Dockerfile syntax |

---

## Linting Toolchain (local)

Match GitHub Actions exactly:

```bash
# Install tools (if not already in venv)
pip install isort==7.0.0 black==26.3.1 flake8>=7,<8 pylint==3.3.1 astroid==3.3.5

# Run the same checks GitHub Actions runs
isort --profile black --line-length 88 --check-only --diff .
black --check --diff .
flake8 --ignore=E,W .

# STIX-ID plugin (requires pycti + runtime deps)
cd shared/pylint_plugins/check_stix_plugin && \
  PYTHONPATH=. pylint ../../../src ../../../tests \
    --disable=all \
    --enable=no_generated_id_stix,no-value-for-parameter,unused-import \
    --load-plugins linter_stix_id_generator
```

---

## Configuration Files

| File | Controls |
|------|----------|
| `pyproject.toml` | black (line-length: 88), isort (profile: black), pytest paths |
| `.flake8` | flake8 configuration |
| `.github/workflows/ci.yml` | CI pipeline jobs |
| `.github/workflows/release.yml` | Release + Docker publish |
| `Dockerfile` | Container definition (python:3.12-alpine base) |
| `src/requirements.txt` | Python dependencies |

---

## Best Practices

1. **Run `make lint` + `make test` before pushing** — Catches 95% of CI failures
2. **Keep pycti + connectors-sdk in sync** — Version mismatch = Docker build failure
3. **Use `dev-up` for development, `qa-up` for validation** — Different stacks for different purposes
4. **Tag releases with semantic versioning** (v1.0.0, v1.0.1-rc1) — Determines prerelease status
5. **Check GitHub Actions logs if CI fails** — Output is detailed and actionable

---

## Quick Reference Commands

```bash
# Before pushing
make lint && make test

# Local development
make dev-up && make dev-logs

# Fix formatting
make format

# Full pipeline locally (CI equivalent)
make format && make test && make lint

# Release
git tag v1.0.0 && git push origin v1.0.0
```