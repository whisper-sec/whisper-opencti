"""Tests for the agent-activity poll loop (``WhisperLogProcessor``).

Drives ``collect()`` / ``transform()`` directly with a stub ``AgentsClient``
and a simple in-memory state object (the SDK's state store is exercised at
runtime, not here). Covers first-run lookback, cursor advance, overlap dedup,
the ``op:list`` join, and the empty-tenant no-op.
"""

from types import SimpleNamespace

from src.connector.converter_to_stix import WHISPER_AUTHOR
from src.connector.log_source import (
    OVERLAP_MS,
    WhisperLogProcessor,
    _resolve_lookback,
    dedup_key,
)
from tests.conftest import build_log_settings

LOG_COLUMNS = [
    "ts", "kind", "qname", "qtype", "rcode", "decision", "source", "answer",
    "latency_ms", "agent", "peer", "bytes_up", "bytes_down", "duration_ms",
    "reason", "client_src", "packets_up", "packets_down",
]  # fmt: skip

AGENT_ID = "a98874349306a52c8"
AGENTS = {
    AGENT_ID: {
        "agent": AGENT_ID,
        "address": "2a04:2a01:1:2:3:4:5:6",
        "fqdn": f"{AGENT_ID}.agents.whisper.online",
        "label": "e2e",
    }
}


def _row(**overrides):
    base = dict.fromkeys(LOG_COLUMNS)
    base["agent"] = AGENT_ID
    base.update(overrides)
    return base


class _StubAgents:
    """Records the args of the last fetch and returns preset agents/rows."""

    def __init__(self, agents=None, rows=None):
        self._agents = agents if agents is not None else AGENTS
        self._rows = rows if rows is not None else []
        self.last_since = None
        self.last_limit = None
        self.last_agent = "unset"

    def list_agents(self):
        return self._agents

    def fetch_logs_paged(self, since=None, limit=1000, agent=None, cap=10000):
        self.last_since = since
        self.last_limit = limit
        self.last_agent = agent
        return self._rows


def _make_processor(rows, *, agents=None, state=None, **whisper_overrides):
    proc = WhisperLogProcessor(
        config=build_log_settings(**whisper_overrides),
        agents_client=_StubAgents(agents=agents, rows=rows),
    )
    proc.state = state if state is not None else SimpleNamespace()
    proc.logger = None  # _log() is a no-op when the SDK logger isn't injected
    return proc


def _run(proc):
    return proc.transform(proc.collect())


# --- helpers ---------------------------------------------------------------
def test_dedup_key_is_stable_and_field_sensitive():
    r = _row(ts=1, kind="dns", qname="a.com", answer="1.2.3.4")
    assert dedup_key(r) == dedup_key(dict(r))
    assert dedup_key(r) != dedup_key(_row(ts=1, kind="dns", qname="b.com"))


def test_resolve_lookback_relative_and_epoch_and_rfc3339():
    now = 10_000_000_000
    assert _resolve_lookback("-1h", now_ms=now) == now - 3_600_000
    assert _resolve_lookback("-30m", now_ms=now) == now - 1_800_000
    assert _resolve_lookback("1784002483385", now_ms=now) == 1784002483385
    assert _resolve_lookback("2026-07-08T00:00:00Z", now_ms=now) > 0
    # Unparseable falls back to 24h ago (fail-open, no crash).
    assert _resolve_lookback("garbage", now_ms=now) == now - 86_400_000


# --- poll loop -------------------------------------------------------------
def test_first_run_uses_initial_lookback():
    proc = _make_processor(rows=[], logs_initial_lookback="-1h")
    proc.collect()
    # No stored cursor → since derived from the lookback window (not None).
    assert isinstance(proc.agents_client.last_since, int)
    assert proc.agents_client.last_since > 0


def test_subsequent_run_uses_cursor_minus_overlap():
    state = SimpleNamespace(last_ts=1784002500000, seen=[])
    proc = _make_processor(rows=[], state=state)
    proc.collect()
    assert proc.agents_client.last_since == 1784002500000 - OVERLAP_MS


def test_cursor_advances_to_max_ts_and_bundle_has_author():
    rows = [
        _row(ts=1784002483385, kind="alloc"),
        _row(ts=1784002495932, kind="dns", qname="rdap.whisper.online",
             decision="allow", answer="2001:19f0:5000:15f6::1"),
    ]  # fmt: skip
    state = SimpleNamespace()
    proc = _make_processor(rows=rows, state=state)
    objects = _run(proc)
    assert state.last_ts == 1784002495932
    # Author Identity leads the bundle and is present exactly once.
    assert objects[0].id == WHISPER_AUTHOR.id
    assert sum(1 for o in objects if o.id == WHISPER_AUTHOR.id) == 1


def test_op_list_join_populates_the_128():
    rows = [_row(ts=1784002483385, kind="alloc")]
    proc = _make_processor(rows=rows, state=SimpleNamespace())
    objects = _run(proc)
    ipv6 = [o for o in objects if o.type == "ipv6-addr"]
    assert ipv6 and ipv6[0].value == AGENTS[AGENT_ID]["address"]


def test_overlap_repoll_dedups_already_seen_rows():
    seen_row = _row(ts=1784002495932, kind="dns", qname="rdap.whisper.online",
                    decision="allow", answer="2001:19f0:5000:15f6::1")  # fmt: skip
    new_row = _row(ts=1784002496000, kind="alloc")
    state = SimpleNamespace(last_ts=1784002495000, seen=[dedup_key(seen_row)])
    proc = _make_processor(rows=[seen_row, new_row], state=state)
    objects = _run(proc)
    # The already-seen dns row must not produce its domain/observed-data again;
    # only the new alloc's anchor + observed-data should ship.
    assert not any(getattr(o, "value", None) == "rdap.whisper.online" for o in objects)
    assert any(o.type == "observed-data" for o in objects)


def test_seen_set_is_rebuilt_for_the_overlap_window():
    max_ts = 1784002496000
    in_window = _row(ts=max_ts, kind="alloc")
    out_window = _row(ts=max_ts - OVERLAP_MS - 5000, kind="alloc")
    state = SimpleNamespace()
    proc = _make_processor(rows=[in_window, out_window], state=state)
    _run(proc)
    assert state.last_ts == max_ts
    # Only the in-overlap-window key is persisted for the next poll.
    assert dedup_key(in_window) in state.seen
    assert dedup_key(out_window) not in state.seen


def test_empty_tenant_ships_nothing_and_does_not_crash():
    state = SimpleNamespace()
    proc = _make_processor(rows=[], agents={}, state=state)
    objects = _run(proc)
    assert objects == []
    assert state.last_ts == 0


def test_agent_filter_is_passed_through():
    proc = _make_processor(rows=[], logs_agent=AGENT_ID)
    proc.collect()
    assert proc.agents_client.last_agent == AGENT_ID
