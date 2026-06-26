---
name: opencti-connector
description: Core conventions for building and modifying OpenCTI internal-enrichment connectors in this repo — the enrichment pipeline, connectors-sdk configuration, scope/TLP/playbook gates, the bundle-send contract, and the project's hard constraints. Use when implementing, reviewing, or debugging connector logic.
---

# OpenCTI internal-enrichment connector

This is an OpenCTI **internal-enrichment** connector. A user clicks **Enrich → Whisper** on a supported observable; OpenCTI pushes the request over RabbitMQ; the connector runs a bounded Cypher query against the Whisper graph API, translates the result into a STIX 2.1 bundle, and ships it back.

## The enrichment pipeline (load-bearing flow)

A single request walks these modules in order — read them in this order when debugging:

1. **[connector.py](../../../src/connector/connector.py)** — `WhisperConnector._process_message` is the pycti v7 callback. The worker hands us `{enrichment_entity, stix_entity, stix_objects, event_type}` directly (no `helper.api.*.read` round-trip). It runs the **TLP gate** (`_extract_and_check_markings`) and **scope gate** (`_is_entity_in_scope`), then delegates to `_enrich_observable`. The returned string becomes the work-item status in the UI.
2. **[settings.py](../../../src/connector/settings.py)** — `ConnectorSettings(BaseConnectorSettings)` + `WhisperConfig(BaseConfigModel)` from `connectors-sdk`. See the `connector-config-sdk` knowledge below.
3. **[queries.py](../../../src/connector/queries.py)** — picks a Cypher template by entity type. See "Cypher constraints".
4. **[whisper_client.py](../../../src/connector/whisper_client.py)** — `WhisperClient.execute_cypher` POSTs to `<api_url>/api/query` with `X-API-Key`; retries 5xx/429/transport 3× with backoff; 401/403 → `WhisperAuthError`, other 4xx → `WhisperQueryError`, post-retry 429 → `WhisperTransportError`.
5. **[result_parser.py](../../../src/connector/result_parser.py)** — walks `CypherResult.rows`, distinguishes node cells (`nodeId`) from edge cells (`type`), infers edge direction by column position (`_nearest_node`), orients direction-sensitive rels (`_orient_edge`), and maps Whisper labels to STIX types. Unmapped labels (FEED_SOURCE, PREFIX, RIR, TLD, PHONE, …) are silently dropped, and edges touching a dropped node drop with them.
6. **[converter_to_stix.py](../../../src/connector/converter_to_stix.py)** — `build_bundle` turns normalized nodes/edges into a `stix2.Bundle`; `build_note` emits Note SDOs. See the `stix-id-generation` skill for the ID rules — they are the easiest thing to get wrong.

## connectors-sdk configuration

Config is built on the SDK, not a hand-rolled model:

```python
from connectors_sdk import (BaseConfigModel, BaseConnectorSettings,
                            BaseInternalEnrichmentConnectorConfig, ListFromString)
from pydantic import Field, SecretStr

class WhisperConfig(BaseConfigModel):
    api_url: str = Field(description="...", examples=["https://graph.whisper.security"])
    api_key: SecretStr = Field(description="...", examples=["whisper-..."])
    max_tlp: str = Field(default="TLP:AMBER+STRICT", examples=["TLP:AMBER+STRICT", "TLP:RED"])

class ConnectorSettings(BaseConnectorSettings):
    connector: _WhisperConnectorConfig = Field(default_factory=_WhisperConnectorConfig)
    whisper: WhisperConfig = Field(default_factory=WhisperConfig)
```

- The SDK loads from env vars + `config.yml`; it **ignores constructor kwargs** (a `model_validator(mode="wrap")` reads env/config). Env names are `<BLOCK>_<FIELD>` flat — `WHISPER_API_URL` → `whisper.api_url`, like upstream `domaintools`.
- `main.py`: `settings = ConnectorSettings()`; `helper = OpenCTIConnectorHelper(settings.to_helper_config(), playbook_compatible=True)`.
- Read config in the connector via `self.config.whisper.api_url` and `self.config.whisper.api_key.get_secret_value()`.
- Always give fields `description=` **and** `examples=` — they feed the generated config schema (see `opencti-contribution`).

## Sending bundles

```python
bundle = self.helper.stix2_create_bundle(objects)          # objects = list, not a Bundle
self.helper.send_stix2_bundle(bundle, cleanup_inconsistent_bundle=True)
```

Use `self.helper.connector_logger.info("msg", {"key": val})` — never a module logger — on the connector send path.

## Hard constraints (do not break — see [CLAUDE.md](../../../CLAUDE.md))

- **Scope.** `IPv4-Addr`, `IPv6-Addr`, `Domain-Name`, `Autonomous-System`. `Url`/`StixFile`/`Email-Addr` are out of scope (mappers exist, no query templates). Unsupported types return a status string and **do not raise**.
- **`playbook_compatible=True` is mandatory.** Out-of-scope entities arriving with no `event_type` (a playbook chain) must ship the original `stix_objects` back via `_send_passthrough_bundle`. Never early-return on unsupported types.
- **Threat properties (`threatScore`, `threatLevel`, the 13 boolean flags, FEED_SOURCE) surface as a Note, not an `indicator` SDO.**
- **pycti and OpenCTI are released in lockstep on the same CalVer.** Bumping pycti requires updating `src/requirements.txt`, `.env.example` (`OPENCTI_VERSION`), and the manifest `support_version` together.
- **Python 3.12** (matches `python:3.12-alpine`).

## Cypher constraints (queries.py)

- **Whisper's engine rejects request-body params** — there is no `params` field. `$value` is JSON-escaped and inlined as a double-quoted literal; `$limit` is inlined as an int. **Do not refactor to bound parameters** — the API rejects them.
- **One hop, `LIMIT 50`** (`DEFAULT_LIMIT`) for the broad IPv4/IPv6/ASN query. Supplementary passes (LINKS_TO directed/count, threat-context, IP network-context) chain a bounded number of edges by design.
- **Domain-Name does NOT use the broad query** (issue #61) — it fans out to targeted directional builders capped at `DOMAIN_FACT_LIMIT` (50) and `DOMAIN_PIVOT_CAP` (25). `SUPPORTED_ENTITY_TYPES`, not the `QUERIES` keyset, is the scope source of truth.
- The main query uses **undirected `-[r]-`** (parser orients). The directed LINKS_TO templates use `->`/`<-` because web-link direction can't be inferred.

## When you change connector behavior

Run the `connector-validation` skill before opening a PR. Mapping/ID changes especially need the STIX-ID pylint and a live re-enrich (idempotency).