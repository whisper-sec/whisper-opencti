# Pre-PR Validation Workflow

Enforce a strict development workflow: no direct pushes to `develop`, all CI checks run locally before opening a PR.

## Quick Start

```bash
# 1. Create a feature branch (never commit to develop directly)
git checkout develop
git pull origin develop
git checkout -b feat/your-feature-name

# 2. Make changes and commit
git add .
git commit -m "message"

# 3. Run local CI checks BEFORE opening PR
make lint
make test
make docker-build

# 4. If all pass, push and open PR
git push origin feat/your-feature-name
gh pr create --base develop --head feat/your-feature-name --title "..." --body "..."
```

---

## Full Workflow

### Step 1: Branch Protection Setup (one-time)

On GitHub repo settings, enable:
- **Require pull request reviews before merging** (at least 1 approval)
- **Require status checks to pass before merging** (enable: Tests, Docker build, Lint checks)
- **Restrict who can push to matching branches** (only admins can push to `develop`)
- **Require branches to be up to date** before merging

Command to verify branch settings:
```bash
gh api repos/whisper-sec/whisper-opencti/branches/develop/protection
```

### Step 2: Pre-Commit Hook (prevent accidental pushes)

Create `.git/hooks/pre-push` to block pushes to `develop`:

```bash
#!/bin/bash
# Prevent direct pushes to develop branch

remote="$1"
while read local_ref local_sha remote_ref remote_sha; do
  if [[ "$remote_ref" == "refs/heads/develop" ]] || [[ "$remote_ref" == "refs/heads/main" ]]; then
    echo "❌ ERROR: Direct push to $remote_ref is blocked!"
    echo "   Use feature branches: git checkout -b feat/your-feature"
    echo "   Then open a PR for review"
    exit 1
  fi
done

exit 0
```

Install hook:
```bash
chmod +x .git/hooks/pre-push
```

### Step 3: Local CI Checks (required before PR)

Run in order. If any fails, fix and re-run.

#### 3a. Format & Lint
```bash
make format      # Auto-fix formatting (isort + black)
make lint        # Check linting (isort + black --check + flake8 + pylint)
```

Expected output:
```
isort --check-only --profile black src/ tests/
black --check src/ tests/
flake8 --ignore=E,W src/
pylint --load-plugins=... src/
→ All pass (exit code 0)
```

#### 3b. Unit Tests
```bash
make test        # Run all 197 tests with pytest
```

Expected output:
```
pytest tests/ -ra
→ 197 passed in 0.24s
```

#### 3c. Docker Build
```bash
make docker-build
```

Expected output:
```
docker build -t whisper-opencti:test .
→ Successfully tagged whisper-opencti:test
```

#### 3d. End-to-End (optional but recommended)
```bash
make dev-clean
make dev-up           # Start full stack (~2-3 min)
# Drive test enrichment via pycti, confirm work status
make dev-logs         # Check for errors
make dev-clean        # Clean up
```

### Step 4: Push & Open PR

```bash
# Push feature branch
git push origin feat/your-feature-name

# Open PR (from develop to develop, or develop to main for release)
gh pr create \
  --base develop \
  --head feat/your-feature-name \
  --title "Brief description" \
  --body "
## Summary
What this change does.

## Validation
- [ ] All 197 tests pass
- [ ] Lint clean (isort, black, flake8, pylint)
- [ ] Docker build succeeds
- [ ] Live enrichment tested (if applicable)

🤖 Generated with Claude Code
"
```

### Step 5: Review & Merge

- GitHub Actions CI runs automatically (Tests, Docker, STIX-ID linter)
- PR requires at least 1 approval
- All checks must pass
- Merge via "Squash and merge" (keeps history clean) or "Create a merge commit"

---

## Makefile Targets

| Target | Purpose |
|--------|---------|
| `make format` | Auto-format code (isort + black) |
| `make lint` | Check formatting + linters (no auto-fix) |
| `make test` | Run unit test suite (197 tests) |
| `make docker-build` | Build Docker image locally |
| `make dev-up` | Start local OpenCTI + connector stack |
| `make dev-logs` | Tail connector logs |
| `make dev-clean` | Stop and wipe dev stack volumes |

---

## Common Issues & Fixes

### "make lint" fails with "flake8: not found"
**Cause:** Missing dev dependencies  
**Fix:** `pip install -r requirements-dev.txt`

### "pytest: not found"
**Cause:** Missing test dependencies  
**Fix:** `pip install -r tests/test-requirements.txt`

### "docker build" fails: "multiple base images"
**Cause:** Dockerfile syntax error  
**Fix:** Check `Dockerfile` for duplicate `FROM` statements

### pre-push hook not firing
**Cause:** Hook not executable  
**Fix:** `chmod +x .git/hooks/pre-push`

### "I committed to develop by accident"
**Fix:**
```bash
git reset --soft HEAD~1        # Undo last commit (keep changes staged)
git checkout -b feat/recovery  # Create feature branch
git push origin feat/recovery
# Then open PR from feat/recovery to develop
```

---

## Enforcement

### Locally (git hooks)
- `pre-push` hook blocks direct pushes to `develop` and `main`
- Developers must use feature branches

### On GitHub (branch protection)
- `develop` branch is protected: requires PR + approval + passing CI
- Cannot force-push to `develop`
- Only admins can bypass protection

### In CI/CD
- Every PR runs: Tests, Docker build, Lint (STIX-ID pylint)
- All checks must pass before merge
- GitHub status page shows results

---

## Best Practices

✅ **DO:**
- Create feature branch for every change: `git checkout -b feat/short-description`
- Run `make lint && make test && make docker-build` before opening PR
- Write clear PR titles and descriptions
- Reference issues/PRs in commit messages: `fix: resolve issue #123`
- Keep commits atomic (one feature per commit)
- Rebase & squash PR commits before merge (for clean history)

❌ **DON'T:**
- Commit directly to `develop` (git hook will block it)
- Open PR before running local checks
- Push large binaries or secrets (git will reject via pre-commit hook)
- Force-push to `develop` (GitHub protection blocks it)
- Merge without approval (GitHub requires it)

---

## Examples

### Adding a new feature
```bash
git checkout develop && git pull
git checkout -b feat/new-hostname-validation

# Make changes to src/connector/result_parser.py
git add src/
git commit -m "feat: add hostname validation filter"

make lint && make test  # ✅ All pass

git push origin feat/new-hostname-validation
gh pr create --base develop --head feat/new-hostname-validation \
  --title "Add hostname validation filter" \
  --body "Filters non-RFC-1035 hostnames before STIX ingestion..."
```

### Fixing a bug
```bash
git checkout develop && git pull
git checkout -b fix/typo-in-manifest

# Edit __metadata__/connector_manifest.json
git add __metadata__/
git commit -m "fix: correct typo in manifest description"

make lint  # ✅ Pass

git push origin fix/typo-in-manifest
gh pr create --base develop --head fix/typo-in-manifest \
  --title "Fix typo in manifest" \
  --body "Corrects 'nameservering' → 'nameserver' in description"
```

---

## Integration with Claude Code

When using Claude Code / Claude agents:

```bash
# Agent applies changes to a feature branch
git checkout -b feat/description

# Agent runs pre-PR checks
make lint && make test && make docker-build

# Agent opens PR (does NOT push to develop directly)
git push origin feat/description
gh pr create --base develop --head feat/description --title "..." --body "..."

# Human reviews PR on GitHub → approve → merge
```

This skill ensures **zero direct pushes to develop** and **all changes validated locally before review**.