---
name: connector-ci
description: Diagnose and resolve CI/CD failures, optimize pipeline performance, and manage releases in the whisper-opencti connector
---

# Connector CI Agent

Use this agent to diagnose GitHub Actions CI failures, optimize the CI/CD pipeline, troubleshoot Docker builds, and manage the release process.

## When to Use

- **CI/CD debugging**: "Why is the format job failing?" or "Docker build error — help diagnose"
- **Pipeline optimization**: "How can we speed up the test job?" or "Reduce build time"
- **Dependency issues**: "pycti version mismatch" or "connectors-sdk compatibility problems"
- **Release management**: "Create a release" or "Fix release workflow issue"
- **Local setup**: "I'm getting linting errors locally" or "How do I validate locally?"

## Capabilities

### Diagnose CI Failures
- Interpret GitHub Actions workflow output
- Identify root causes (formatting, linting, test, Docker build issues)
- Suggest specific fixes with commands to run locally

### Version Management
- Verify pycti + connectors-sdk compatibility
- Check version pins across requirements.txt, .env.example, manifest, CLAUDE.md
- Identify dependency conflicts

### Docker Build Issues
- Diagnose Dockerfile failures
- Check base image compatibility
- Verify dependency installation order
- Validate health checks

### Release Process
- Guide through tagging and publishing
- Verify version format (semver, prerelease detection)
- Monitor image push to ghcr.io

### Local Validation
- Help set up local environment (dev vs QA stack)
- Explain pytest, lint, and format commands
- Troubleshoot test failures

## Example Prompts

```
"The Docker build job is failing with a pycti error — help me debug"
→ Agent will check pycti version, connectors-sdk compatibility, and requirements.txt

"CI format check failed, but make format locally doesn't fix it"
→ Agent will investigate isort/black config, check pyproject.toml

"How do I release v1.0.1?"
→ Agent will guide through git tag, push, and release workflow

"Test job is timing out — any optimization ideas?"
→ Agent will review pytest config, test count, and suggest parallelization strategies
```

## Tools Available

- Read/Edit/Write files (diagnose and fix issues)
- Bash (run local checks, verify CI commands)
- GitHub API (inspect workflow logs, PR status)
- Code understanding (debug linting/STIX-ID issues)

## Related Resources

- **Skill**: `ci-cd-pipeline` — How-to guide for CI/CD operations
- **Documentation**: `docs/ci-cd-guide.md` — In-depth pipeline architecture
- **Agent**: `connector-qa` — For end-to-end validation after CI passes
- **Agent**: `connector-developer` — For code changes that trigger CI