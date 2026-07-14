"""Agent-activity log source - the EXTERNAL_IMPORT sibling connector.

The enrichment connector (``connector.py``) is the *keyless / graph* half of
the two-tier integration: it pushes Whisper's public infrastructure graph
*into* OpenCTI on demand. This module is the *keyed* half: with the tenant's
own API key it periodically pulls that tenant's *own* agent activity - DNS
lookups, egress connections, identity allocations - *out* to OpenCTI as STIX
2.1 timeline intelligence. The enrichment path is left completely untouched.

Built on the connectors-sdk ``ExternalImportConnector`` orchestration:

- ``WhisperLogProcessor`` (a ``BaseDataProcessor``) is the poll body:
  ``collect()`` reads the cursor, joins ``op:list``, and pages ``op:logs``;
  ``transform()`` converts rows → STIX (dedup + cursor advance); ``send()``
  (inherited) ships the bundle inside a managed OpenCTI Work.
- ``WhisperLogSource`` wires the processor into the SDK connector and injects a
  pre-built helper so it inherits ``main.py``'s cold-boot retry behaviour.

Checkpointing uses OpenCTI's **native** connector-state store (no external
dependency): a ``last_ts`` epoch-ms cursor plus a bounded ``seen`` set of
dedup keys covering a small re-poll overlap window.
"""

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

from connectors_sdk.connectors.external_import.base_data_processor import (
    BaseDataProcessor,
)
from connectors_sdk.connectors.external_import.external_import_connector import (
    ExternalImportConnector,
)
from connectors_sdk.connectors.external_import.logger import ConnectorLogger
from connectors_sdk.states.states import ExternalImportConnectorState
from pycti import OpenCTIConnectorHelper

from src.connector.agents_client import AgentsClient
from src.connector.converter_to_stix import WHISPER_AUTHOR
from src.connector.log_converter import convert_log_row
from src.connector.settings import LogConnectorSettings
from src.connector.whisper_client import WhisperClient

# Re-poll a small window behind the stored cursor so an event landing exactly
# on a poll boundary isn't lost; the dedup set below suppresses the re-reads.
OVERLAP_MS = 5000

# Upper bound on the ``seen`` dedup set persisted between polls. Only keys
# within the overlap window need to survive, so this is a generous ceiling.
SEEN_CAP = 5000

_RELATIVE_RE = re.compile(r"^-(\d+)\s*([smhd])$", re.IGNORECASE)
_UNIT_MS = {"s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}


def _now_ms() -> int:
    return int(datetime.now(tz=UTC).timestamp() * 1000)


def _resolve_lookback(spec: str, now_ms: int | None = None) -> int:
    """Resolve an initial-lookback spec to an epoch-ms watermark.

    Accepts a relative window (``-24h`` / ``-90m`` / ``-7d``), a bare
    epoch-millisecond integer, or an RFC3339 timestamp. Falls back to 24h ago
    on anything unparseable (fail-open, never crash the first run).
    """
    now_ms = _now_ms() if now_ms is None else now_ms
    spec = (spec or "").strip()
    match = _RELATIVE_RE.match(spec)
    if match:
        amount = int(match.group(1))
        unit = match.group(2).lower()
        return max(0, now_ms - amount * _UNIT_MS[unit])
    if spec.lstrip("-").isdigit():
        return max(0, int(spec))
    try:
        dt = datetime.fromisoformat(spec.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return max(0, now_ms - _UNIT_MS["h"] * 24)


def dedup_key(row: dict[str, Any]) -> str:
    """Stable per-event key: sha1(agent|ts|kind|qname-or-peer|answer-or-bytes)."""
    agent = row.get("agent")
    ts = row.get("ts")
    kind = row.get("kind")
    locus = row.get("qname") or row.get("peer") or ""
    payload = row.get("answer")
    if payload is None:
        payload = row.get("bytes_down")
    raw = f"{agent}|{ts}|{kind}|{locus}|{payload}"
    return hashlib.sha1(
        raw.encode("utf-8")
    ).hexdigest()  # noqa: S324 (dedup, not security)


class WhisperLogProcessor(BaseDataProcessor):
    """Collect → transform → send one poll of Whisper agent-activity logs."""

    work_name = "Whisper agent activity"

    def __init__(
        self,
        config: LogConnectorSettings,
        agents_client: AgentsClient,
    ) -> None:
        self._config = config
        self.agents_client = agents_client

    # -- helpers -----------------------------------------------------------
    def _batch_limit(self) -> int:
        return max(1, min(int(self._config.whisper.logs_batch_limit), 10000))

    def _log(self, level: str, message: str, meta: dict | None = None) -> None:
        """Best-effort structured log (the SDK logger is injected at runtime)."""
        logger = getattr(self, "logger", None)
        if logger is None:
            return
        getattr(logger, level, logger.info)(message, meta or {})

    # -- pipeline ----------------------------------------------------------
    def collect(self) -> dict[str, Any]:
        """Read cursor, join ``op:list``, and page ``op:logs`` from ``since``."""
        last_ts = getattr(self.state, "last_ts", None)
        seen = set(getattr(self.state, "seen", None) or [])

        if isinstance(last_ts, int):
            since = max(0, last_ts - OVERLAP_MS)
        else:
            since = _resolve_lookback(self._config.whisper.logs_initial_lookback)
            self._log(
                "info",
                "[whisper-logs] first run - using initial lookback",
                {"since_ms": since},
            )

        agent_filter = self._config.whisper.logs_agent
        agents = self.agents_client.list_agents()
        rows = self.agents_client.fetch_logs_paged(
            since=since,
            limit=self._batch_limit(),
            agent=agent_filter,
        )
        self._log(
            "info",
            "[whisper-logs] fetched log rows",
            {"rows": len(rows), "agents": len(agents), "since_ms": since},
        )
        return {"agents": agents, "rows": rows, "seen": seen}

    def transform(self, data: dict[str, Any]) -> list[Any]:
        """Convert rows → STIX, dedup against the overlap window, advance state."""
        agents: dict = data["agents"]
        rows: list = data["rows"]
        seen: set = data["seen"]

        prior_cursor = getattr(self.state, "last_ts", None)
        max_ts = prior_cursor if isinstance(prior_cursor, int) else 0

        ordered = sorted(
            rows, key=lambda r: r.get("ts") if isinstance(r.get("ts"), int) else 0
        )

        by_id: dict[str, Any] = {}
        emitted = 0
        for row in ordered:
            ts = row.get("ts")
            if isinstance(ts, int):
                max_ts = max(max_ts, ts)
            key = dedup_key(row)
            if key in seen:
                continue
            try:
                objs = convert_log_row(row, agents)
            except (
                Exception
            ) as exc:  # noqa: BLE001 - one bad row must not sink the poll
                self._log(
                    "error",
                    "[whisper-logs] failed to convert a log row (skipping)",
                    {"error": str(exc), "kind": row.get("kind")},
                )
                continue
            for obj in objs:
                by_id[obj.id] = obj
            if objs:
                emitted += 1

        # Advance the cursor and rebuild a bounded overlap-window dedup set so
        # the next poll's re-reads are suppressed. Persisted via the SDK's
        # native state store (set on `self.state`; saved by the base connector).
        self.state.last_ts = max_ts
        window_keys = [
            dedup_key(r)
            for r in ordered
            if isinstance(r.get("ts"), int) and r["ts"] >= max_ts - OVERLAP_MS
        ]
        self.state.seen = window_keys[:SEEN_CAP]

        if not by_id:
            self._log("info", "[whisper-logs] no new events this poll", None)
            return []

        objects = list(by_id.values())
        objects.insert(0, WHISPER_AUTHOR)
        self._log(
            "info",
            "[whisper-logs] built STIX bundle",
            {"events": emitted, "objects": len(objects), "cursor_ms": max_ts},
        )
        return objects


class WhisperLogSource(ExternalImportConnector):
    """SDK external-import connector for the Whisper agent-activity log source.

    Injects a pre-built ``OpenCTIConnectorHelper`` (so it inherits the
    cold-boot registration retry from ``main_logs.py``) and drives a single
    ``WhisperLogProcessor`` on the configured ``duration_period``.
    """

    def __init__(
        self,
        helper: OpenCTIConnectorHelper,
        config: LogConnectorSettings,
        agents_client: AgentsClient | None = None,
    ) -> None:
        if agents_client is None:
            agents_client = AgentsClient(
                WhisperClient(
                    api_url=config.whisper.api_url,
                    api_key=config.whisper.api_key.get_secret_value(),
                )
            )
        processor = WhisperLogProcessor(config=config, agents_client=agents_client)
        super().__init__(
            settings=config,
            data_processors=[processor],
            state=ExternalImportConnectorState(),
        )
        self._prebuilt_helper = helper

    def _init_dependencies(self) -> None:
        """Wire up components against the pre-built helper (no re-connect)."""
        self._helper = self._prebuilt_helper
        self.logger = ConnectorLogger(self._helper)
        self.state.inject_dependencies(self._helper)
        for processor in self.data_processors:
            processor.inject_dependencies(
                settings=self.settings,
                helper=self._helper,
                state=self.state,
            )
            processor.post_init()

    def run(self) -> None:
        """Start the scheduled poll loop (blocks)."""
        self.start()


__all__ = [
    "WhisperLogProcessor",
    "WhisperLogSource",
    "dedup_key",
    "OVERLAP_MS",
]
