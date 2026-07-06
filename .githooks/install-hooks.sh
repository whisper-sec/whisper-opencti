#!/bin/bash
# Install the pre-PR validation workflow hooks

set -e

echo "Installing pre-push hook for branch protection..."

# Configure git to use .githooks directory
git config core.hooksPath .githooks

# Make pre-push hook executable
chmod +x .githooks/pre-push

echo "✅ Pre-push hook installed!"
echo ""
echo "Branch protection workflow is now active:"
echo "  - Direct pushes to develop and main are blocked"
echo "  - All changes must go through feature branches and PRs"
echo "  - Local CI checks (lint, test, docker-build) must pass before PR"
echo ""
echo "See .claude/skills/pre-pr-validation/SKILL.md for the full workflow."
