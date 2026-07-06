# Bug Fix Workflow with Multi-Agent Review

Structured workflow for fixing connector bugs with architectural review, QA validation, and documentation updates before opening a PR.

## Workflow Overview

```
🐛 Bug Identified
    ↓
📋 Developer proposes solution steps
    ↓
🏛️ Architect reviews & approves (or requests changes)
    ↓
💻 Developer implements changes
    ↓
✅ QA validates & tests
    ↓
📖 Docs updates (if needed)
    ↓
🔀 PR opened & merged
```

## Step-by-Step Process

### 1. Identify Bug & Gather Context

```
✅ What is the issue?
   - Error message or unexpected behavior
   - Where it occurs (which code path)
   - Impact (does it block enrichment? corrupt data? performance?)
   - Reproducer (how to trigger it)

✅ Root cause hypothesis
   - What's likely causing it?
   - Which file(s) are involved?
   - Is it a logic error, version mismatch, or constraint violation?
```

### 2. Propose Solution (Developer → Architect)

**Developer creates feature branch and proposes approach:**

```bash
git checkout -b fix/bug-description
```

**Proposal format:**

```markdown
## Bug Fix Proposal

### Problem
[Clear description of the bug, with error messages/logs]

### Root Cause
[What's causing it and why]

### Proposed Solution
1. [Step 1: What will change and where]
2. [Step 2: Code pattern/approach]
3. [Step 3: How it will be tested]

### Affected Code
- File: `src/connector/module.py` (lines X-Y)
- Reason: [Why this file needs changes]

### Testing Strategy
- Unit test: [What test will verify the fix]
- Integration: [Any live testing needed?]

### Documentation Impact
- [ ] No docs changes needed
- [ ] README update required
- [ ] Comment updates needed
- [ ] CI/CD guide update required

### Breaking Changes
- [ ] No breaking changes
- [ ] Potential breakage: [describe]
```

### 3. Architect Reviews & Approves

**Architect evaluates:**

```markdown
## Architecture Review

✅ Problem Understanding: [Is root cause correctly identified?]
✅ Solution Design: [Is approach sound and minimal?]
✅ Constraint Compliance: [Does it respect STIX, pycti, RFC 1035?]
✅ Testing Feasibility: [Can it be adequately tested?]
✅ Impact Assessment: [Will it affect other features?]

### Status
- ✅ APPROVE: Proceed with implementation
- 🔄 REQUEST CHANGES: Refinements needed
- ❌ BLOCK: Critical issues to address first

### Feedback
[Specific guidance, concerns, or alternative approaches]

### Next Steps
[What developer should do: implement, revise, explore alternatives]
```

### 4. Developer Implements

Once approved, developer implements the fix:

```bash
# Make changes
git add .
git commit -m "fix: [brief description of fix]"

# Run local CI checks (required before moving to QA)
make format
make lint
make test
make docker-build

# If all pass, ready for QA
```

### 5. QA Validates

**QA runs comprehensive tests:**

```bash
# Unit tests
make test                    # All 186 tests must pass
pytest tests/test_module.py  # Specific test file

# Lint/Type checks
make lint                    # isort, black, flake8, pylint

# Docker image
make docker-build            # Image builds without errors

# Integration testing (if applicable)
make dev-clean
make dev-up                  # Start full stack
# Drive test enrichment
make dev-logs                # Check for errors
make dev-clean
```

**QA Report Format:**

```markdown
## QA Validation Report

### Unit Tests
- ✅ PASS: 186/186 tests pass
- Evidence: [pytest output summary]

### Lint & Type Checks
- ✅ PASS: isort, black, flake8, pylint all clean
- Evidence: [Make lint output]

### Docker Build
- ✅ PASS: Image builds successfully
- Evidence: [docker build logs snippet]

### Integration Tests
- ✅ PASS: Live enrichment (IPv4 8.8.8.8)
- Objects created: N
- No errors in logs
- Evidence: [work item status / connector logs]

### Regression Testing
- ✅ PASS: No breakage of existing features
- Tested: [List features verified]

### Status
- ✅ APPROVED FOR DOCS & PR
- ❌ BLOCKED: [Issues preventing merge]

### Issues Found
- [Issue 1: description and severity]
- [Issue 2: description and severity]
```

### 6. Docs Updates

**If docs need updating:**

```markdown
## Documentation Updates

### Files to Update
1. **README.md** — [Description of change]
   - New section: [if applicable]
   - Version bump: [if applicable]

2. **docs/ci-cd-guide.md** — [Description of change]

3. **Code comments** — [Where and why]

### Changes Made
- [Specific updates with before/after]

### Verification
- Links work: ✅
- Examples are current: ✅
- No broken references: ✅
```

### 7. Open PR

**After all validations pass:**

```bash
git push origin fix/bug-description

gh pr create \
  --base develop \
  --head fix/bug-description \
  --title "fix: [brief description]" \
  --body "
## Summary
[1-3 sentence description]

## Changes
- [Change 1]
- [Change 2]

## Validation
- ✅ Architect approved (link to review)
- ✅ QA passed all tests (link to report)
- ✅ Docs updated (or N/A)
- ✅ Local CI checks pass

## Related
- Fixes: #[issue number]
- Related to: [any other issues/PRs]

🤖 Multi-Agent Workflow
"
```

## Multi-Agent Coordination

### Agents in Workflow

| Agent | Role | When Active |
|-------|------|------------|
| **connector-developer** | Proposes and implements fixes | Steps 2, 4 |
| **connector-architect** | Reviews design and approves | Step 3 |
| **connector-qa** | Validates and tests | Step 5 |
| **connector-docs** | Updates documentation | Step 6 |

### Communication Between Agents

1. **Developer → Architect**: Proposal with steps and approach
2. **Architect → Developer**: Approval or feedback
3. **Developer → QA**: "Ready for validation, code in branch X"
4. **QA → Developer**: Validation report (pass/fail/issues)
5. **Developer → Docs**: "Need docs updates" (if applicable)
6. **Docs → Developer**: PR-ready confirmation
7. **Developer → Human**: PR ready for review

## Checklist for Bug Fixes

### Pre-Proposal
- [ ] Bug clearly described with reproducer
- [ ] Root cause identified
- [ ] Constraint check: STIX, pycti, RFC 1035, etc.
- [ ] Test scenarios identified

### Pre-Implementation
- [ ] Architect approved the approach
- [ ] Feature branch created
- [ ] No direct commits to develop

### Pre-QA
- [ ] Code changes made
- [ ] Local lint/test pass
- [ ] Commit message clear and follows conventions
- [ ] No secrets/passwords in code

### Pre-Docs
- [ ] QA validation passed
- [ ] All 186 tests pass
- [ ] Docker image builds
- [ ] Integration tests pass (if applicable)

### Pre-PR
- [ ] Docs updated (or marked N/A)
- [ ] All validations signed off
- [ ] Commit message follows conventions
- [ ] No breaking changes (or clearly documented)

### Pre-Merge
- [ ] PR reviewed by maintainer
- [ ] GitHub CI passes
- [ ] At least 1 approval
- [ ] Branch protection rules satisfied

## Example Bug Fix Session

### Scenario: RFC 1035 Hostname Validation Bug

**Step 1: Bug Identified**
```
Issue: Unicode hostnames (café.com) accepted as valid
Impact: OpenCTI rejects them at ingestion; orphan relationships created
Root cause: isalnum() accepts non-ASCII characters
```

**Step 2: Developer Proposes**
```markdown
## Proposal
**Problem**: Unicode hostnames violate RFC 1035

**Solution**: Add c.isascii() check to validation
- File: src/connector/result_parser.py:89
- Change: (c.isascii() and c.isalnum())
- Test: pytest tests/test_result_parser.py::test_invalid_unicode_hostname
```

**Step 3: Architect Reviews**
```markdown
✅ APPROVE
- Root cause correctly identified
- Minimal, focused change
- Correct approach per RFC 1035
- No constraint violations
- Test scenario clear
```

**Step 4: Developer Implements**
```bash
git checkout -b fix/rfc-1035-hostname-validation
# Make changes
make lint && make test && make docker-build
# All pass ✅
```

**Step 5: QA Validates**
```markdown
✅ PASS
- 186 tests pass
- Lint clean
- Docker builds
- Unicode hostnames now rejected ✅
```

**Step 6: Docs** (N/A — no doc changes needed)

**Step 7: PR Opens**
```
PR Title: fix: enforce ASCII-only characters in hostname validation
- Architect: ✅ Approved
- QA: ✅ Validated
- Ready for merge
```

## Tips for Success

✅ **DO:**
- Clearly describe the problem with reproducer
- Get architect approval before coding
- Run full local CI before QA handoff
- Document all validation evidence
- Keep PRs focused on single bug

❌ **DON'T:**
- Code first, ask for approval later
- Combine multiple bug fixes in one PR
- Skip any validation step
- Push directly to develop
- Ignore architect feedback

## Tools & Integration

```bash
# Create feature branch (enforce via pre-push hook)
git checkout -b fix/description

# Run local CI (required before QA)
make lint && make test && make docker-build

# Push for QA validation
git push origin fix/description

# Open PR after all approvals
gh pr create --base develop ...
```

## Environment

- Work directory: `/Users/elakkuvan/Documents/Z_Whisper/whisper-opencti`
- Feature branches: `fix/` or `feat/` prefix
- Protected branches: `develop`, `main` (no direct pushes)
- Git hook: `.githooks/pre-push` enforces branch protection