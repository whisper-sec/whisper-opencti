"""Tests for the ``whisper.agents`` Cypher builder (agent-activity log source).

Mirrors ``test_queries.py``: the op + args are inlined as a Cypher map literal
(Whisper rejects request-body params), so we assert the produced query string
carries the right literals and no leftover placeholders.
"""

import pytest

from src.connector.agents_client import build_agents_query


def test_builds_logs_op_with_all_args_inlined():
    q = build_agents_query(
        "logs", {"since": 1784002483385, "limit": 1000, "agent": "a98874349306a52c8"}
    )
    assert q.startswith("CALL whisper.agents(")
    assert 'op: "logs"' in q
    assert "since: 1784002483385" in q
    assert "limit: 1000" in q
    # String args are JSON-escaped and double-quoted.
    assert 'agent: "a98874349306a52c8"' in q
    # Nested args map is present.
    assert "args: {" in q


def test_none_valued_args_are_omitted():
    q = build_agents_query("logs", {"since": 100, "limit": 50, "agent": None})
    assert "agent:" not in q
    assert "since: 100" in q
    assert "limit: 50" in q


def test_empty_args_produces_bare_op():
    q = build_agents_query("list")
    assert q == 'CALL whisper.agents({op: "list"})'


def test_args_all_none_drops_args_map():
    q = build_agents_query("list", {"agent": None})
    assert "args" not in q
    assert q == 'CALL whisper.agents({op: "list"})'


def test_string_value_is_json_escaped_for_safety():
    # A quote in the agent id must not break out of the Cypher string literal.
    q = build_agents_query("logs", {"agent": 'evil" }) //'})
    assert '"evil\\" }) //"' in q


def test_int_and_bool_render_as_cypher_literals():
    q = build_agents_query("logs", {"limit": 10, "flag": True, "off": False})
    assert "limit: 10" in q
    assert "flag: true" in q
    assert "off: false" in q


def test_empty_op_rejected():
    with pytest.raises(ValueError):
        build_agents_query("")
