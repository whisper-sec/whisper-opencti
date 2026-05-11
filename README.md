# whisper-opencti

OpenCTI connector that enriches indicators with relationship data from the
Whisper graph.

> Status: **MVP scaffolding** — the connector currently runs as a no-op. STIX
> mapping and the enrichment flow land in follow-up tickets under
> [milestone #1](https://github.com/whisper-sec/whisper-opencti/milestone/1).

## Quickstart

> _Placeholder — full instructions arrive with the local dev stack ticket
> ([#2](https://github.com/whisper-sec/whisper-opencti/issues/2))._

```bash
# 1. Copy the sample config and fill in your Whisper API key + OpenCTI token
cp config.yml.sample config.yml

# 2. Build the connector image
docker build -t whisper-sec/whisper-opencti:dev .

# 3. Run it against a local OpenCTI instance (see ticket #2 for the dev stack)
docker run --rm --env-file .env whisper-sec/whisper-opencti:dev
```

## Configuration

The connector reads configuration from `config.yml` (mounted into the
container) or environment variables. See [config.yml.sample](./config.yml.sample)
for the full set of keys. The minimum required values are:

| Key                    | Env var                  | Description                          |
| ---------------------- | ------------------------ | ------------------------------------ |
| `opencti.url`          | `OPENCTI_URL`            | URL of your OpenCTI platform         |
| `opencti.token`        | `OPENCTI_TOKEN`          | OpenCTI admin or connector token     |
| `whisper.api_url`      | `WHISPER_API_URL`        | Whisper API base URL                 |
| `whisper.api_key`      | `WHISPER_API_KEY`        | Whisper API key for this connector   |

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
ruff check src/
```

## Repository layout

```
.
├── .github/workflows/   # CI (lint + build check)
├── src/                 # Connector source
│   ├── main.py          # Entrypoint
│   └── connector/       # Connector package
├── Dockerfile
├── entrypoint.sh
├── config.yml.sample
├── pyproject.toml
└── requirements.txt
```

## License

Proprietary. See [LICENSE](./LICENSE).
