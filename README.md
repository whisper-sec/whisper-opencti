# whisper-opencti

OpenCTI connector that enriches indicators with relationship data from the
Whisper graph.

> Status: **MVP scaffolding** - the connector registers with OpenCTI but the
> enrichment logic is a placeholder. STIX mapping and the real enrichment flow
> land in follow-up tickets under
> [milestone #1](https://github.com/whisper-sec/whisper-opencti/milestone/1).

## Local dev stack

A single command brings up a stock OpenCTI instance, its dependencies, and the
Whisper connector wired together. Pinned to OpenCTI **6.4.5**.

**Prerequisites:** Docker Desktop (or compatible engine) with at least **6 GB
RAM** available, and `make`.

```bash
make dev-up        # build + start everything (~2-3 min on first run)
make dev-status    # check service state
make dev-logs      # tail logs across the stack
make dev-down      # stop containers (keeps data volumes)
make dev-clean     # stop and wipe volumes for a fresh start
```

The stack uses values from [.env.dev](./.env.dev) - these are committed dev
defaults, **not for production use**. OpenCTI will be at
<http://localhost:8080> (login: `admin@whisper.local` / `ChangeMe-dev-only`).

### Verifying the connector registered

1. Open <http://localhost:8080> and sign in.
2. Navigate to **Data → Ingestion → Connectors**.
3. The `Whisper` connector should appear with status `Started`.

If it doesn't appear within ~60 seconds of `dev-up` completing, check
`make dev-logs | grep connector-whisper`.

## Production / external deployment

For an OpenCTI instance you already operate, paste the
[`docker-compose.yml`](./docker-compose.yml) snippet into your existing
compose file and supply the env vars listed in [.env.sample](./.env.sample).

The minimum required configuration:

| Key                  | Env var             | Description                          |
| -------------------- | ------------------- | ------------------------------------ |
| `opencti.url`        | `OPENCTI_URL`       | URL of your OpenCTI platform         |
| `opencti.token`      | `OPENCTI_TOKEN`     | OpenCTI token for this connector     |
| `connector.id`       | `CONNECTOR_ID`      | A unique UUIDv4 for this connector   |
| `whisper.api_url`    | `WHISPER_API_URL`   | Whisper API base URL                 |
| `whisper.api_key`    | `WHISPER_API_KEY`   | Whisper API key                      |

See [config.yml.sample](./config.yml.sample) for the full set of keys when
configuring via a mounted YAML file instead of env vars.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
ruff check src/
```

## Repository layout

```
.
├── .github/workflows/      # CI (lint + build check)
├── src/                    # Connector source
│   ├── main.py             # Entrypoint
│   └── connector/          # Connector package
├── Dockerfile
├── docker-compose.yml      # Connector-only snippet for existing OpenCTI deployments
├── docker-compose.dev.yml  # Full local stack (OpenCTI + deps + connector)
├── Makefile                # dev-up / dev-down / dev-logs / dev-clean
├── entrypoint.sh
├── config.yml.sample
├── .env.sample             # Env vars for production
├── .env.dev                # Committed dev defaults used by docker-compose.dev.yml
├── pyproject.toml
└── requirements.txt
```

## License

Proprietary. See [LICENSE](./LICENSE).
