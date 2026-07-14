"""Tests for the ``whisper.agents`` op-envelope unwrap (``AgentsClient``).

The control-plane answers with a double-nested envelope. The outer body is
already parsed by ``WhisperClient.execute_cypher`` into ``CypherResult.rows``;
``AgentsClient`` unwraps the single ``{op, ok, status, result, error,
retry_after}`` row and zips the inner columnar ``result`` into dict-per-row.

Fixtures use the REAL redacted live samples captured from ``whisper logs
--json`` (values sanitized - no real key, no real /128).
"""

from unittest.mock import MagicMock

import pytest
import responses

from src.connector.agents_client import AgentsClient
from src.connector.exceptions import WhisperQueryError
from src.connector.whisper_client import CypherResult, WhisperClient

# Inner columnar result columns, exactly as the live `op:logs` returns them.
LOG_COLUMNS = [
    "ts", "kind", "qname", "qtype", "rcode", "decision", "source", "answer",
    "latency_ms", "agent", "peer", "bytes_up", "bytes_down", "duration_ms",
    "reason", "client_src", "packets_up", "packets_down",
]  # fmt: skip

# Redacted live rows (dns / conn / alloc).
DNS_ROW = [
    1784002495932, "dns", "rdap.whisper.online", "AAAA", "NOERROR", "allow",
    "upstream", "2001:19f0:5000:15f6::1", 3, "a98874349306a52c8",
    None, None, None, None, None, None, None, None,
]  # fmt: skip
CONN_ROW = [
    1784002496119, "conn", None, None, None, None, None, None, None,
    "a98874349306a52c8", "rdap.whisper.online:443", 1715, 4086, 185,
    "closed", "145.224.65.0/24", 3, 4,
]  # fmt: skip
ALLOC_ROW = [
    1784002483385, "alloc", None, None, None, None, None, None, None,
    "a98874349306a52c8", None, None, None, None, None, None, None, None,
]  # fmt: skip


def _envelope(op, *, ok=True, status=200, result=None, error=None, retry_after=None):
    """Build the outer op-envelope row (what execute_cypher returns as rows[0])."""
    return {
        "op": op,
        "ok": ok,
        "status": status,
        "result": result,
        "error": error,
        "retry_after": retry_after,
    }


def _mock_client(envelope):
    client = MagicMock(spec=WhisperClient)
    client.execute_cypher.return_value = CypherResult(
        columns=["op", "ok", "status", "result", "error", "retry_after"],
        rows=[envelope],
        statistics={},
    )
    return client


def test_fetch_logs_zips_columns_onto_row_arrays():
    inner = {"columns": LOG_COLUMNS, "rows": [DNS_ROW, CONN_ROW, ALLOC_ROW]}
    client = _mock_client(_envelope("logs", result=inner))
    ac = AgentsClient(client)

    rows = ac.fetch_logs(since=1784002480000, limit=1000)

    assert len(rows) == 3
    assert rows[0]["kind"] == "dns"
    assert rows[0]["qname"] == "rdap.whisper.online"
    assert rows[0]["answer"] == "2001:19f0:5000:15f6::1"
    assert rows[0]["agent"] == "a98874349306a52c8"
    assert rows[1]["kind"] == "conn"
    assert rows[1]["peer"] == "rdap.whisper.online:443"
    assert rows[1]["bytes_down"] == 4086
    assert rows[2]["kind"] == "alloc"


def test_fetch_logs_passes_since_limit_agent_into_query():
    client = _mock_client(_envelope("logs", result={"columns": [], "rows": []}))
    ac = AgentsClient(client)

    ac.fetch_logs(since=123, limit=42, agent="a98874349306a52c8")

    query = client.execute_cypher.call_args.args[0]
    assert 'op: "logs"' in query
    assert "since: 123" in query
    assert "limit: 42" in query
    assert 'agent: "a98874349306a52c8"' in query


def test_list_agents_unwraps_item_from_kind_item_columns():
    # Live `op:list` answers columnar as ['kind', 'item'] with each agent's
    # fields nested under `item`, and the fqdns end with a trailing root dot.
    inner = {
        "columns": ["kind", "item"],
        "rows": [
            [
                "agents",
                {
                    "agent": "a98874349306a52c8",
                    "address": "2a04:2a01:1:2:3:4:5:6",
                    "fqdn": "a98874349306a52c8.agents.whisper.online.",
                    "label": "opencti-e2e",
                    "state": "active",
                    "created": 1784002483000,
                },
            ],
            # `agent-` prefix on the list side must be normalized to bare.
            [
                "agents",
                {
                    "agent": "agent-b1234567890abcdef",
                    "address": "2a04:2a01:9:9:9:9:9:9",
                    "fqdn": "b1234567890abcdef.agents.whisper.online.",
                    "label": "second",
                    "state": "active",
                    "created": 1784002484000,
                },
            ],
        ],
    }
    client = _mock_client(_envelope("list", result=inner))
    ac = AgentsClient(client)

    agents = ac.list_agents()

    # Agent fields are read out of the nested `item`, not the flat row.
    assert set(agents) == {"a98874349306a52c8", "b1234567890abcdef"}
    assert agents["a98874349306a52c8"]["address"] == "2a04:2a01:1:2:3:4:5:6"
    # fqdn is preserved verbatim (trailing dot) - normalization is downstream
    # in the converter, not here.
    assert (
        agents["a98874349306a52c8"]["fqdn"]
        == "a98874349306a52c8.agents.whisper.online."
    )
    assert agents["b1234567890abcdef"]["label"] == "second"

    # op:list MUST carry the required `kind: "agents"` arg (400 BAD_ARGS else).
    query = client.execute_cypher.call_args.args[0]
    assert 'op: "list"' in query
    assert 'kind: "agents"' in query


def test_raises_on_ok_false():
    client = _mock_client(_envelope("logs", ok=False, status=200, error="nope"))
    with pytest.raises(WhisperQueryError):
        AgentsClient(client).fetch_logs()


def test_raises_on_status_4xx():
    client = _mock_client(_envelope("logs", ok=True, status=403, error="forbidden"))
    with pytest.raises(WhisperQueryError):
        AgentsClient(client).fetch_logs()


def test_raises_on_retry_after_present():
    client = _mock_client(_envelope("logs", ok=True, status=200, retry_after=30))
    with pytest.raises(WhisperQueryError):
        AgentsClient(client).fetch_logs()


def test_raises_on_empty_envelope():
    client = MagicMock(spec=WhisperClient)
    client.execute_cypher.return_value = CypherResult(
        columns=[], rows=[], statistics={}
    )
    with pytest.raises(WhisperQueryError):
        AgentsClient(client).fetch_logs()


def test_null_result_returns_empty_rows():
    ac = AgentsClient(_mock_client(_envelope("logs", result=None)))
    assert ac.fetch_logs() == []


def test_paging_advances_since_until_short_page():
    """A full page triggers another fetch; a short page stops paging."""
    page1 = {"columns": LOG_COLUMNS, "rows": [DNS_ROW]}
    page2 = {"columns": LOG_COLUMNS, "rows": []}
    client = MagicMock(spec=WhisperClient)
    client.execute_cypher.side_effect = [
        CypherResult(["op"], [_envelope("logs", result=page1)], {}),
        CypherResult(["op"], [_envelope("logs", result=page2)], {}),
    ]
    ac = AgentsClient(client)

    rows = ac.fetch_logs_paged(since=0, limit=1, cap=10000)

    assert len(rows) == 1
    # Second fetch advanced `since` past the newest ts (+1).
    second_query = client.execute_cypher.call_args_list[1].args[0]
    assert f"since: {DNS_ROW[0] + 1}" in second_query


def test_paging_respects_cap():
    page = {"columns": LOG_COLUMNS, "rows": [DNS_ROW, CONN_ROW]}
    client = MagicMock(spec=WhisperClient)
    client.execute_cypher.return_value = CypherResult(
        ["op"], [_envelope("logs", result=page)], {}
    )
    ac = AgentsClient(client)

    rows = ac.fetch_logs_paged(since=0, limit=2, cap=2)

    assert len(rows) == 2


@responses.activate
def test_reuses_whisper_client_auth_and_retry_over_http():
    """End-to-end over the real WhisperClient: X-API-Key sent, 5xx retried."""
    inner = {"columns": LOG_COLUMNS, "rows": [ALLOC_ROW]}
    body = {
        "success": True,
        "columns": ["op", "ok", "status", "result", "error", "retry_after"],
        "rows": [_envelope("logs", result=inner)],
        "statistics": {},
    }
    url = "https://api.whisper.test/api/query"
    # First a 503 (retried), then success - proves WhisperClient's retry is reused.
    responses.add(responses.POST, url, json={"error": "boom"}, status=503)
    responses.add(responses.POST, url, json=body, status=200)

    client = WhisperClient(
        api_url="https://api.whisper.test",
        api_key="whisper_live_redacted",
        backoff_factor=0,
    )
    ac = AgentsClient(client)

    rows = ac.fetch_logs(since=0, limit=10)

    assert len(rows) == 1 and rows[0]["kind"] == "alloc"
    assert len(responses.calls) == 2  # 503 then 200
    assert responses.calls[0].request.headers["X-API-Key"] == "whisper_live_redacted"
