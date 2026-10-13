<!--
  Operational reference for the external platforms this project uses.
  One "## <Platform>" section per platform; edit freely — updates gap-fill
  missing or stale sections, and human-written content is preserved.
  SAFETY: this file is committed. Record env var NAMES only — never tokens,
  keys, or other secret values, in any section.
  Provenance convention: every platform section carries, directly under its
  heading, an HTML comment of the form
    source: <file/command/PR the claims came from>, verified: <how it was checked>, date: <YYYY-MM-DD>
  Update it whenever you change the section.
-->

# Platforms

Operational reference for every external platform this project runs on or integrates with. Architecture-level context (why the dependency exists, failure impact) lives in [docs/architecture.md](architecture.md) → External Dependencies; this doc is the "how to operate it" companion.

## GitHub
<!-- source: .github/workflows/release.yml, .github/workflows/ci-connector-verified-linter.yml, docker-compose.qa.yml, __metadata__/connector_manifest.json, ran `git remote -v`; verified: read workflow/compose/manifest files, ran `git remote -v`; date: 2026-07-18 -->

### Purpose

GitHub hosts whisper-opencti at `whisper-sec/whisper-opencti`: source control, code review, and every CI/CD workflow run here. Two more relationships extend beyond this one repo. GitHub Actions publishes the connector's runtime image to the GitHub Container Registry (GHCR) on each tagged release, and this connector is being ported upstream into the OpenCTI connector catalog through a fork-based PR workflow: changes land here first, then are applied under `internal-enrichment/whisper/` in a fork of `OpenCTI-Platform/connectors` and PR'd upstream per its CONTRIBUTING.md.

### Environments & IDs

| Context | URL / ID | Notes |
|---|---|---|
| This repo (origin) | https://github.com/whisper-sec/whisper-opencti | Default and integration branch `develop`; `develop` and `main` are protected. |
| Fork remote | https://github.com/elakkuvan-r/connectors.git | Staging ground for upstream PRs (confirmed via `git remote -v`). |
| Upstream remote | https://github.com/OpenCTI-Platform/connectors.git | Canonical connectors monorepo; this connector's upstream home is `internal-enrichment/whisper/`. |
| GHCR image | `ghcr.io/whisper-sec/whisper-opencti` | Pushed by [.github/workflows/release.yml](../.github/workflows/release.yml) on `v*` tags; pulled by [docker-compose.qa.yml](../docker-compose.qa.yml) via `WHISPER_CONNECTOR_VERSION`. |
| Actions | https://github.com/whisper-sec/whisper-opencti/actions | Run history for the workflows below. |
| Packages | https://github.com/whisper-sec/whisper-opencti/pkgs/container/whisper-opencti | GHCR package page (already referenced in [docs/ci-cd-guide.md](ci-cd-guide.md)). |

### Access

- **CLI**: `git`, with three remotes configured - `origin` (this repo), `fork` (`elakkuvan-r/connectors`), `upstream` (`OpenCTI-Platform/connectors`). `gh` (GitHub CLI) is also available for PR and Actions inspection, authenticated with the operator's own GitHub account.
- **MCP**: not configured - no GitHub MCP server is defined in this repo (no `.mcp.json` found).
- **API**: GitHub REST/GraphQL via `gh api` for anything the CLI subcommands don't cover directly. CI's push to GHCR authenticates with the `GHCR_PUSH_TOKEN` repository secret (name only - see [release.yml](../.github/workflows/release.yml); the value is never committed).

### Common operations

```bash
# Check CI status for the current branch
gh run list --branch develop

# List the workflows configured for this repo
gh workflow list

# Tag and push a release (full walkthrough in docs/ci-cd-guide.md "Release Process")
git tag -a v1.0.0 -m "Release v1.0.0"
git push origin v1.0.0

# Validate a published GHCR image against a full local OpenCTI stack
make qa-up
```

### Escalation & links

- GitHub platform status: https://www.githubstatus.com
- CI failures: start at the failing job under Actions (URL above). [docs/ci-cd-guide.md](ci-cd-guide.md) maps each of the seven configured workflows (`ci`, `ci-connector-verified-linter`, `ci-tests-connectors`, `ci-unused-deps`, `gh-do-not-merge-label`, `gh-pr-check-conventions`, `release`) to fixes.
- Catalog-conformance failures: the "Connector Verified Linter" job checks `__metadata__/connector_manifest.json` against upstream naming rules. Two intentional deviations are recorded with reasons in [.github/vclint-allowlist.txt](../.github/vclint-allowlist.txt) - this repo publishes its own `ghcr.io/whisper-sec/whisper-opencti` image rather than the upstream `opencti/connector-whisper` name, since the connector hasn't merged upstream yet.
- Do not touch: `develop` and `main` are protected branches. Every change lands through a PR.

## Whisper API (WhisperGraph)
<!-- source: .env.example, src/connector/whisper_client.py, src/connector/settings.py, README.md, docs/qa-handoff.md, __metadata__/connector_manifest.json; verified: read implementation + config; date: 2026-07-18 -->

### Purpose

The connector's only outbound network dependency at runtime: every enrichment sends one Cypher query over HTTP to the Whisper graph API and turns the response into a STIX bundle. The full request/response mechanics live in [docs/architecture.md](architecture.md) §3.5; this section is the operational reference for reaching the API directly.

### Environments & IDs

| Environment | URL / ID | Notes |
|---|---|---|
| Default (dev, QA, and production template alike) | `https://graph.whisper.security` | [.env.example](../.env.example) `WHISPER_API_URL`. This repo does not define a separate staging URL - the same value ships as the working default for both compose stacks. |
| Cypher endpoint | `<WHISPER_API_URL>/api/query` | `CYPHER_PATH` in [whisper_client.py](../src/connector/whisper_client.py). |
| Vendor site | https://whisper.security | `subscription_link` in [__metadata__/connector_manifest.json](../__metadata__/connector_manifest.json) - where an API key is requested. |

(unverified) Whether Whisper Security offers a separate non-production endpoint for testing. Nothing in this repo references one; confirm with Whisper Security directly if a sandbox is needed.

### Access

- **API**: REST, a single endpoint - `POST <WHISPER_API_URL>/api/query`, JSON body `{"query": "<cypher>", "params": {...}}`. Auth via the `X-API-Key` header. Env var name: `WHISPER_API_KEY` (required, never logged - see `WhisperClient._headers()`).
- **CLI**: none shipped by this project. Use `curl` directly for manual checks (below).
- **MCP**: not configured in this repo.

### Common operations

```bash
# Manual Cypher query, mirroring what WhisperClient.execute_cypher sends
curl -sS -X POST "$WHISPER_API_URL/api/query" \
  -H "X-API-Key: $WHISPER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "MATCH (n:IPV4 {name: \"8.8.8.8\"})-[r]-(m) RETURN n, r, m LIMIT 50", "params": {}}'
```

### Escalation & links

- No public Whisper status page is referenced anywhere in this repo (unverified - ask Whisper Security if one exists).
- `docs/qa-handoff.md` TC-09 is the documented failure drill: block `graph.whisper.security` at the firewall and confirm the connector surfaces `WhisperTransportError` after retries.
- Auth failures (`WhisperAuthError`, HTTP 401/403) are terminal until `WHISPER_API_KEY` is fixed - the full error taxonomy is in [docs/architecture.md](architecture.md) §3.8.
- Rate limiting (HTTP 429) retries automatically up to 3 times honoring `Retry-After`; persistent 429s surface as `WhisperTransportError`, not a query bug (`whisper_client.py`).
