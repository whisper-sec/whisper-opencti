"""Connector configuration, built on the OpenCTI ``connectors-sdk``.

Per the upstream PR review (OpenCTI-Platform/connectors#6708), the connector
now uses the SDK's ``BaseConnectorSettings`` rather than a hand-rolled
``pydantic-settings`` model. The ``opencti:`` and ``connector:`` blocks come
from the SDK base classes; the ``whisper:`` block carries the
connector-specific configuration.

The SDK loads values from environment variables and an optional ``config.yml``
(see ``config.yml.sample``); ``to_helper_config()`` produces the dict consumed
by ``OpenCTIConnectorHelper``.
"""

from datetime import timedelta

from connectors_sdk import (
    BaseConfigModel,
    BaseConnectorSettings,
    BaseExternalImportConnectorConfig,
    BaseInternalEnrichmentConnectorConfig,
    ListFromString,
)
from pydantic import Field, SecretStr

__all__ = [
    "ConnectorSettings",
    "LogConnectorSettings",
    "WhisperConfig",
]

_DEFAULT_SCOPE = ["IPv4-Addr", "IPv6-Addr", "Domain-Name", "Autonomous-System"]

# Default scope for the log-source sibling. External-import connectors don't
# enrich a specific observable type, so this is a descriptive marker rather
# than a real dispatch scope.
_DEFAULT_LOG_SCOPE = ["whisper-agent-activity"]


class _WhisperConnectorConfig(BaseInternalEnrichmentConnectorConfig):
    """``connector:`` block - defaults specific to this connector."""

    name: str = Field(default="Whisper", description="Connector display name.")
    scope: ListFromString = Field(
        default=_DEFAULT_SCOPE,
        description="Observable types this connector enriches.",
    )


class WhisperConfig(BaseConfigModel):
    """``whisper:`` block - Whisper graph API settings."""

    api_url: str = Field(
        description=(
            "Base URL of the Whisper graph API, e.g. "
            "'https://graph.whisper.security'. The connector POSTs Cypher "
            "to '<api_url>/api/query'."
        ),
        examples=["https://graph.whisper.security"],
    )
    api_key: SecretStr = Field(
        description="Whisper API key, sent in the X-API-Key header. Never logged.",
        examples=["whisper-0123456789abcdef0123456789abcdef"],
    )
    max_tlp: str = Field(
        default="TLP:AMBER+STRICT",
        description=(
            "Maximum TLP marking the connector will enrich. Observables marked "
            "above this level are skipped. Set 'TLP:RED' to disable the gate."
        ),
        examples=["TLP:AMBER+STRICT", "TLP:RED"],
    )
    # --- Agent-activity log-source settings (EXTERNAL_IMPORT sibling only) ---
    # These are optional and unread by the enrichment connector; they only
    # steer the log source (src/main_logs.py). Kept on the same WhisperConfig
    # block so both connectors share one `whisper.api_url` / `whisper.api_key`
    # - same tenant key, same auth path, no second credential.
    logs_initial_lookback: str = Field(
        default="-24h",
        description=(
            "How far back the log source reads on its very first run (no "
            "stored cursor yet). Accepts a relative window like '-24h' / "
            "'-90m' / '-7d', an epoch-millisecond integer, or an RFC3339 "
            "timestamp. Subsequent runs resume from the persisted cursor."
        ),
        examples=["-24h", "-7d", "1784002483385"],
    )
    logs_agent: str | None = Field(
        default=None,
        description=(
            "Optional agent id to restrict the log pull to a single agent "
            "(e.g. 'a98874349306a52c8'). Leave unset to ingest activity for "
            "every agent the tenant key can see."
        ),
        examples=["a98874349306a52c8"],
    )
    logs_batch_limit: int = Field(
        default=1000,
        ge=1,
        le=10000,
        description=(
            "Rows requested per control-plane page (the op caps at 10000). "
            "The source pages until a short page is returned."
        ),
        examples=[1000, 5000],
    )


class ConnectorSettings(BaseConnectorSettings):
    """Top-level settings: OpenCTI + connector blocks (from the SDK) + whisper."""

    connector: _WhisperConnectorConfig = Field(default_factory=_WhisperConnectorConfig)
    whisper: WhisperConfig = Field(default_factory=WhisperConfig)


class _WhisperLogConnectorConfig(BaseExternalImportConnectorConfig):
    """``connector:`` block for the EXTERNAL_IMPORT log-source sibling.

    Mirrors ``_WhisperConnectorConfig`` but for the periodic pull connector:
    ``type`` is fixed to ``EXTERNAL_IMPORT`` by the SDK base, and
    ``duration_period`` gets a sane 5-minute default so the common case needs
    no configuration.
    """

    name: str = Field(
        default="Whisper Agent Activity",
        description="Connector display name.",
    )
    scope: ListFromString = Field(
        default=_DEFAULT_LOG_SCOPE,
        description="Descriptive scope marker for the log source.",
    )
    duration_period: timedelta = Field(
        default=timedelta(minutes=5),
        description="How long to wait between two polls of the log source.",
    )


class LogConnectorSettings(BaseConnectorSettings):
    """Top-level settings for the agent-activity log source.

    Reuses the exact same ``WhisperConfig`` block (so the log source and the
    enrichment connector share one ``whisper.api_url`` / ``whisper.api_key``),
    paired with the EXTERNAL_IMPORT ``connector:`` block above. The enrichment
    ``ConnectorSettings`` is left untouched.
    """

    connector: _WhisperLogConnectorConfig = Field(
        default_factory=_WhisperLogConnectorConfig
    )
    whisper: WhisperConfig = Field(default_factory=WhisperConfig)
