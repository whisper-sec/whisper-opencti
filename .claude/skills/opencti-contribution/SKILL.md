---
name: opencti-contribution
description: Process for contributing an OpenCTI connector upstream to OpenCTI-Platform/connectors — template structure, the connectors-sdk + pycti requirements, config-schema generation, conventional + signed commits, and the fork PR workflow. Use when preparing or updating an upstream PR.
---

# Contributing a connector upstream

Mirrors the Filigran [CONTRIBUTING.md](https://github.com/OpenCTI-Platform/connectors/blob/master/CONTRIBUTING.md). This connector lives upstream at `internal-enrichment/whisper/` on the fork `elakkuvan-r/connectors` (branch `feat/whisper-connector`, PR #6708, issue #6707).

## Two repos, two import styles

| | Our repo (`whisper-opencti`) | Upstream fork (`internal-enrichment/whisper/`) |
|---|---|---|
| Imports | `from src.connector.X` | `from connector.X` (no `src.` prefix) |
| Tests path | `src/` + `tests/` have `__init__.py` | `conftest.py` does `sys.path.append(".../src")`, connector imports get `# noqa: E402` |

To port: copy each changed file and translate `src.connector` → `connector`, then re-run isort/black on the fork (shorter import paths change line lengths). Diff against the fork's current file to confirm only intended changes land — never clobber fork-specific content.

Port checklist extras:
- `config.yml.sample` placement (Verified linter VC104): the linter wants it at the connector root — `internal-enrichment/whisper/config.yml.sample` in the fork (`src/` is only a WARNING-grade fallback; missing is an ERROR). In this repo it lives at the repo root, which maps to the connector root when ported.
- Test imports like `from tests.conftest import ...` must be translated to the fork's test layout (`conftest.py` uses `sys.path.append`, no `tests` package).

## Required structure & standards (CONTRIBUTING.md)

- `__metadata__/connector_manifest.json` (name ≤250, short_description ≤250, description, square PNG/JPEG logo ≥96×96) + `connector_config_schema.json` + `CONNECTOR_CONFIG_DOC.md`.
- `src/connector/{__init__,connector,converter_to_stix,settings}.py`, `src/main.py`, `src/requirements.txt`; `tests/`; `Dockerfile` (python:3.12-alpine, non-root, healthcheck, minimal packages, exec-form `ENTRYPOINT ["python", "-m", "src.main"]` — no entrypoint.sh wrapper, the Verified linter forbids it); `config.yml.sample`; `.dockerignore`; `README.md`.
- **Config via connectors-sdk** Pydantic models with `description=` **and** `examples=` (feeds the config schema).
- **Deterministic STIX IDs** via the stix2 library (SCOs) + pycti `generate_id` (SDOs/rels/notes) — see `stix-id-generation`.
- **Lint**: pylint with the vendored STIX plugin, black, isort `--profile black`, flake8 `--ignore=E,W`. Run from repo root.

## requirements.txt pin

```
pycti==<exact version connectors-sdk@master pins>
connectors-sdk @ git+https://github.com/OpenCTI-Platform/connectors.git@master#subdirectory=connectors-sdk
```

`connectors-sdk` is **not on PyPI** — it installs from the monorepo via git. `pycti` must match the SDK@master pin exactly or pip's resolver fails (confirm by attempting a Docker build). The Dockerfile build stage needs `git`.

## Generate the config schema/doc

```bash
make connector_config_schema      # interactive: enter the connector folder name
```

It spins a temp venv, installs the connector + `connectors-sdk`, imports `ConnectorSettings` from `src/main.py`, and writes `__metadata__/connector_config_schema.json` (env-var keys like `WHISPER_API_URL`, carrying your `examples=`) + `CONNECTOR_CONFIG_DOC.md`. Commit both.

## Commits & PR

- **Conventional Commits**, issue number required: `type(scope)!?: description (#issue)` — e.g. `refactor(whisper): use connectors-sdk and pycti ID generation (#6707)`.
- **Signed commits are required.** Configure SSH signing (key registered to the GitHub account):
  ```bash
  git config gpg.format ssh
  git config user.signingkey ~/.ssh/github_signing.pub
  git config commit.gpgsign true
  git commit -S ...
  ```
  Local `git log --show-signature` may say "No signature" without an `allowedSignersFile` — that's local-only; confirm server-side with
  `gh api repos/<fork>/commits/<sha> --jq '.commit.verification.verified'` → `true`.
- Every upstream PR **requires an associated GitHub issue**. Fork PRs gate CI on maintainer approval (`action_required`) on every push.
- Reply to review feedback concisely and human — state what changed (SDK adoption, pycti IDs), not a changelog dump.