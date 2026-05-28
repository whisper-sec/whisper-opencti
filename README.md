# whisper-opencti

OpenCTI connector that enriches observables - IPv4, IPv6, domain names - with
relationship data from the [Whisper graph](https://whisper.security).

> **MVP** - first cut of the connector implementing the agreed spec under
> [milestone #1](https://github.com/whisper-sec/whisper-opencti/milestone/1).
> Production-ready for the supported entity types below. Threat-property
> enrichment, multi-hop traversals, and Url / file-hash scopes are out of
> scope for this iteration.

## What it does

When you click **Enrich → Whisper** on a supported observable in OpenCTI, the
connector runs a one-hop Cypher query against Whisper anchored on that
observable's value, translates the matching nodes and edges into STIX 2.1
objects + relationships, and pushes the resulting bundle back into OpenCTI.
Re-enrichment is idempotent - STIX SCOs use deterministic IDs derived from
their key properties, so the same indicator always produces the same set of
STIX object IDs.

## Compatibility

| Component | Version |
| --- | --- |
| OpenCTI platform | **6.4.5** (verified). Other 6.4.x releases very likely work; OpenCTI 6.3.x and earlier are not tested. |
| Python (image runtime) | 3.11 |
| `pycti` | 6.4.5 (pinned to match the platform) |
| `stix2` | 3.0.1 |

If you upgrade OpenCTI to a new minor, bump `pycti` in
[requirements.txt](./requirements.txt) and the platform/worker image tags in
[docker-compose.dev.yml](./docker-compose.dev.yml) together - running mismatched
versions causes the connector to fail at registration time.

## Supported entity types

| OpenCTI entity | Whisper anchor label |
| --- | --- |
| `IPv4-Addr` | `IPV4` |
| `IPv6-Addr` | `IPV6` |
| `Domain-Name` | `HOSTNAME` |
| `Autonomous-System` | `ASN` |

`Url`, `StixFile`, and `Email-Addr` are deliberately **not** in scope for the
MVP - Whisper has no direct label for URLs or file hashes, and email enrichment
isn't part of the v1 spec. See [docs/qa-handoff.md](./docs/qa-handoff.md) for
the full known-limitations list.

## Quickstart - local dev stack

A single command brings up a stock OpenCTI instance, its dependencies, and the
connector wired together. Pinned to OpenCTI **6.4.5**.

**Prerequisites:** Docker Desktop (or compatible engine) with at least **6 GB
RAM** available, and `make`.

### 1. Create your `.env` from the template

```bash
cp .env.example .env
$EDITOR .env                          # set WHISPER_API_KEY=<your-real-key>
```

[.env.example](./.env.example) is the **single source of truth** - committed,
with working dev defaults for every variable. `.env` is gitignored. The
Makefile reads `.env` only; without it the make targets exit with a hint.

The placeholder `WHISPER_API_KEY=dev-placeholder-key` in `.env.example` lets the
connector start and register with OpenCTI even before you set a real key - but
every enrichment call fails with `WhisperAuthError` until you replace it with a
real Whisper Security key in your `.env`.

### 2. Bring up the stack

```bash
make dev-up        # build + start everything (~2-3 min on first run)
make dev-status    # check service state
make dev-logs      # tail logs across the stack
make dev-down      # stop containers (keeps data volumes)
make dev-clean     # stop and wipe volumes for a fresh start
```

OpenCTI is at <http://localhost:8080> (login from `.env`: `admin@whisper.local`
/ `ChangeMe-dev-only` per the committed defaults - **dev only, not for
production**).

### Verifying the connector registered

1. Open <http://localhost:8080> and sign in.
2. **Data → Ingestion → Connectors**.
3. The `Whisper` connector should appear with status `Started` and scope
   `IPv4-Addr, IPv6-Addr, Domain-Name`.

If it doesn't appear within ~60s of `dev-up` completing, check
`make dev-logs | grep connector-whisper` - most of the time it's a config issue
flagged at startup.

### Trying enrichment end-to-end

1. **Data → Observations → Observables → Create**: pick `IPv4-Addr` and
   value `8.8.8.8` (or any other supported entity).
2. Open the observable detail page.
3. Click **Enrichment** in the right-hand panel; trigger **Whisper**.
4. Within a few seconds, the **Knowledge → Relationships** tab should populate
   with the related domains / hostnames / IPs returned by Whisper.

See [docs/scenarios/](./docs/scenarios/) for three worked walk-throughs.

## Validating a published image - for QA

This is the path for QA validating a release candidate or stable release. The
QA stack pulls the published image from GHCR (rather than building from source)
and runs it against a full stock OpenCTI for end-to-end testing.

If you've already followed the dev quickstart above, you can re-use the same
`.env` - but each step below is also self-contained, so you can come here
cold.

### Prerequisites

- Docker Desktop (or compatible engine) with at least **6 GB RAM** available, and `make`.
- A GitHub account with access to the `whisper-sec` org (the package is private).
- A real Whisper API key - request from Whisper Security.
- Outbound HTTPS network access from your host to `graph.whisper.security`.

### 1. Create a GitHub PAT with `read:packages`

The image lives in a **private** GHCR package (the connector is licensed for
internal Whisper Security use only - see [LICENSE](./LICENSE)). You need a
personal access token that can read packages from the `whisper-sec` org.

Either form works:

| | URL | Required setting |
|---|---|---|
| **Classic PAT** (simpler) | https://github.com/settings/tokens/new | Scope: `read:packages` only |
| **Fine-grained PAT** (narrower) | https://github.com/settings/personal-access-tokens/new | Resource owner: `whisper-sec` · Repository access: only `whisper-opencti` · Permissions → Packages: Read |

**If `whisper-sec` enforces SSO**, after creating the token go back to the
tokens page, click **Configure SSO** next to your new token, and authorize
it for `whisper-sec`. Without this step `docker login` will fail with
`denied`.

### 2. Authenticate Docker to GHCR

```bash
docker login ghcr.io
# Username: <your-github-username>      (your account name - not whisper-sec)
# Password: <paste your PAT>            (hidden as you paste - NOT your GitHub password)
```

Expected: `Login Succeeded`. The credential is saved to your local
Docker config (Keychain on macOS, `~/.docker/config.json` elsewhere).

### 3. Set up your `.env`

```bash
cp .env.example .env
$EDITOR .env       # set WHISPER_API_KEY=<your-real-whisper-key>
```

The committed [.env.example](./.env.example) is the template - working
defaults for everything except `WHISPER_API_KEY`. The `.env` you create is
gitignored.

The image version is controlled by `WHISPER_CONNECTOR_VERSION` in `.env`
(defaults to the current stable release). To validate a different release,
check the **[releases page](https://github.com/whisper-sec/whisper-opencti/releases)**
for available tags and pick one:

- The entry tagged **Latest** is the current stable release (e.g. `v0.1.0`).
- Entries tagged **Pre-release** are release candidates (e.g. `v0.2.0-rc1`).

Edit `WHISPER_CONNECTOR_VERSION` to that tag, then re-run `make qa-up`.

### 4. Bring up the QA stack

```bash
make qa-up
```

First-time startup takes 2–3 minutes while Elasticsearch initialises. Then
the stack runs at <http://localhost:8080>. Login from `.env`:
`admin@whisper.local` / `ChangeMe-dev-only`.

In another terminal:
```bash
make qa-status     # service health
make qa-logs       # tail logs across the stack
```

The QA stack uses Compose project name `whisper-opencti-qa`, so it can
coexist on disk with the dev stack - but they **cannot run simultaneously**
(both bind `OPENCTI_PORT`). Run `make dev-down` first if the dev stack is up.

### 5. Verify the connector registered

1. Open <http://localhost:8080> and sign in.
2. **Data → Ingestion → Connectors**.
3. `Whisper` should appear with status `Started` and scope
   `IPv4-Addr, IPv6-Addr, Domain-Name`.

If it doesn't appear within ~60 seconds:

```bash
make qa-logs | grep connector-whisper
```

Most often it's a config issue at startup (missing env var, wrong
`OPENCTI_TOKEN`) or `WhisperAuthError` (the placeholder key is still in
`.env`).

### 6. Smoke-test enrichment end-to-end

1. **Data → Observations → Observables → Create**: pick `Domain-Name`, value `dns.google`.
2. Open the observable detail page.
3. Click **Enrichment** in the right-hand panel; trigger **Whisper**.
4. Within a few seconds the **Knowledge → Relationships** tab should populate
   with ~45 related entities (mostly other domains, plus the resolved IP).

If the smoke test passes you're ready for full validation.

### 7. Run the full QA test matrix

The 12-case test matrix lives in **[docs/qa-handoff.md](./docs/qa-handoff.md)**.
That doc contains:

- TC-01 through TC-12 covering green-path, edge-case, and failure scenarios
- The **list of known MVP non-goals** - please don't file bugs against these
- The **bug severity guide** (S1 critical → S4 cosmetic) and what to include in a bug report

Walk through each TC, then file any bugs in this repo's [Issues](https://github.com/whisper-sec/whisper-opencti/issues) tab with the appropriate severity.

### 8. Stop and clean up

```bash
make qa-down       # stop containers, keep data volumes
make qa-clean      # stop and remove volumes (fresh state next time)
```

## Production / external deployment

For an OpenCTI instance you already operate:

### 1. Pull the image

Images are published to GitHub Container Registry (GHCR) on every tagged
release. The package is **private** to the `whisper-sec` org (the connector is
licensed for internal Whisper Security use only - see [LICENSE](./LICENSE)),
so you need a GitHub personal access token with the `read:packages` scope
and `docker login`:

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u <your-github-username> --password-stdin
docker pull ghcr.io/whisper-sec/whisper-opencti:v0.1.0
```

Available tags (see the [releases page](https://github.com/whisper-sec/whisper-opencti/releases) for the full list):

| Tag | Use when |
| --- | --- |
| `vMAJOR.MINOR.PATCH` (e.g. `v0.1.0`) | Production - pin to a specific release. The entry tagged **Latest** on the releases page. |
| `vMAJOR.MINOR.PATCH-rcN` | Pre-release / release candidate. Entries tagged **Pre-release**. |
| `latest` | Whatever was most recently tagged as a stable release. Only if you accept automatic updates on `docker pull`. |

To confirm what's running:

```bash
docker inspect ghcr.io/whisper-sec/whisper-opencti:v0.1.0 \
  | jq -r '.[0].Config.Labels."org.opencontainers.image.version"'
```

If you need to build from source instead (customising the image, debugging),
the Dockerfile accepts a `VERSION` build arg that's baked into the
`org.opencontainers.image.version` label:

```bash
docker build --build-arg VERSION=0.1.0-custom -t whisper-opencti:custom .
```

### 2. Drop the connector service into your existing compose

Paste the [`docker-compose.yml`](./docker-compose.yml) snippet into your
existing OpenCTI compose, update `image:` to the GHCR tag you just pulled, and
set the env vars below. The connector container needs network access to your
OpenCTI platform service (default port 8080) and outbound HTTPS to Whisper.

### 3. Configure

The connector reads its config from environment variables. The same keys are
also accepted in a mounted `config.yml` (see [config.yml.sample](./config.yml.sample)).

#### OpenCTI side

| Env var | Required | Default | Description |
| --- | --- | --- | --- |
| `OPENCTI_URL` | yes | - | URL of your OpenCTI platform reachable from the connector container, e.g. `http://opencti:8080`. |
| `OPENCTI_TOKEN` | yes | - | Token of an OpenCTI user with permission to write observables and relationships. Don't reuse the admin token in production. |
| `CONNECTOR_ID` | yes | - | A unique UUIDv4 for this connector instance. Generate once with `uuidgen` and keep it stable across restarts. |
| `CONNECTOR_NAME` | no | `Whisper` | Display name in the OpenCTI UI. |
| `CONNECTOR_TYPE` | no | `INTERNAL_ENRICHMENT` | Do not change - the connector is an internal-enrichment type only. |
| `CONNECTOR_SCOPE` | no | `IPv4-Addr,IPv6-Addr,Domain-Name,Autonomous-System` | Which entity types this connector responds to. Adding types that the connector doesn't actually support (e.g. `Url`, `StixFile`) just produces "not supported" log lines. |
| `CONNECTOR_AUTO` | no | `false` | If `true`, OpenCTI automatically enriches every new in-scope observable. Leave `false` until you're confident about Whisper API quota. |
| `CONNECTOR_LOG_LEVEL` | no | `info` | One of `debug`, `info`, `warning`, `error`. |

#### Whisper side

| Env var | Required | Default | Description |
| --- | --- | --- | --- |
| `WHISPER_API_URL` | yes | - | Base URL of the Whisper graph API, typically `https://graph.whisper.security`. The connector POSTs Cypher queries to `<api_url>/api/query`. |
| `WHISPER_API_KEY` | yes | - | Your Whisper API key. Sent in the `X-API-Key` header on every request. Never logged. |

## Troubleshooting

### The connector doesn't appear in OpenCTI

- `make dev-logs | grep connector-whisper` (or `docker logs <container>`). If
  the connector container is in a crash-loop, the message-frame is usually a
  bad config error - most often missing `OPENCTI_URL`/`OPENCTI_TOKEN` or an
  invalid `CONNECTOR_ID`.
- If the container is up but OpenCTI doesn't list it: the connector can't
  reach RabbitMQ. Check the platform-side `RABBITMQ__` env vars match and that
  the connector is on the same Docker network.
- Confirm with the OpenCTI GraphQL endpoint:
  ```bash
  curl -fsS -X POST http://localhost:8080/graphql \
    -H "Authorization: Bearer $OPENCTI_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"query":"{ connectors { name active connector_scope } }"}'
  ```

### Enrichment runs but no relationships appear

- Check the work item in OpenCTI: **Data → Connectors → Whisper → click a recent
  work item**. The status string from the connector is shown there - common
  ones:
  - `No Whisper data for <value>`: Whisper has no graph data for that
    observable. Verify with `query` against the live API (e.g. via the
    Whisper MCP).
  - `entity type 'Url' not supported by Whisper enrichment`: the observable is
    out of scope for the MVP.
  - `WhisperAuthError`: `WHISPER_API_KEY` is wrong or empty. Re-set it and
    restart the connector container.

### Whisper rate-limiting or timeouts

The connector retries 5xx responses and connection errors three times with
exponential backoff (configured in [whisper_client.py](./src/connector/whisper_client.py)).
If you see persistent `WhisperTransportError`s in the logs, check Whisper's
status and your account quota. The connector does not back off on 429 today -
follow-up work.

### "Connector started but enrichments don't trigger"

Confirm the observable's entity type is in `CONNECTOR_SCOPE`. The OpenCTI UI
will only offer the connector under **Enrichment** for entities matching the
scope.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

make lint    # ruff check + ruff format --check
make test    # pytest
```

The full suite covers the HTTP client, STIX mapper, result parser, and the
connector callback. CI runs lint + tests + a Docker image build on every PR
to `main` and `develop` - see [.github/workflows/ci.yml](./.github/workflows/ci.yml)
for the live count and current status.

## Repository layout

```
.
├── .github/workflows/      # CI (lint, tests, docker build)
├── docs/
│   ├── architecture.md     # System design + per-module deep dive
│   ├── scenarios/          # Worked enrichment walk-throughs
│   └── qa-handoff.md       # QA test matrix + known limitations
├── src/connector/
│   ├── connector.py        # WhisperConnector class + callback
│   ├── whisper_client.py   # HTTP client with retries
│   ├── queries.py          # Cypher templates per entity type
│   ├── result_parser.py    # Whisper rows → normalized nodes/edges
│   ├── stix_mapper.py      # Normalized → STIX 2.1 bundle
│   └── exceptions.py
├── tests/                  # pytest, 71 cases
├── Dockerfile
├── docker-compose.yml      # Connector-only snippet for existing OpenCTI deployments
├── docker-compose.base.yml # Shared OpenCTI stack (used by dev + qa flavours)
├── docker-compose.dev.yml  # Dev flavour - connector built from source
├── docker-compose.qa.yml   # QA flavour - connector pulled from GHCR
├── Makefile                # dev-up / qa-up / test / lint
├── config.yml.sample
├── .env.example            # Single source of truth; cp to .env (gitignored)
├── pyproject.toml
└── requirements.txt
```

## Further reading

- [docs/architecture.md](./docs/architecture.md) - system design and per-module
  deep dive for engineers onboarding to the codebase.
- [docs/scenarios/](./docs/scenarios/) - three worked enrichment scenarios with
  real Whisper data and the resulting STIX shapes.
- [docs/qa-handoff.md](./docs/qa-handoff.md) - QA test matrix, known
  limitations, severity guide.

## License

Proprietary. See [LICENSE](./LICENSE).
