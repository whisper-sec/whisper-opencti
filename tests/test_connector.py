import json
from unittest.mock import MagicMock

import pytest
from src.connector.connector import WhisperConnector
from src.connector.exceptions import WhisperTransportError
from src.connector.whisper_client import CypherResult, WhisperClient


@pytest.fixture
def helper():
    h = MagicMock()
    h.api.stix_cyber_observable.read.return_value = None
    return h


@pytest.fixture
def client():
    return MagicMock(spec=WhisperClient)


@pytest.fixture
def connector(helper, client):
    return WhisperConnector(helper=helper, client=client)


def test_process_message_no_entity_id_returns_status(connector, helper):
    result = connector._process_message({})
    assert "missing entity_id" in result
    helper.api.stix_cyber_observable.read.assert_not_called()


def test_process_message_observable_not_found(connector, helper):
    helper.api.stix_cyber_observable.read.return_value = None
    result = connector._process_message({"entity_id": "ipv4--abc"})
    assert "not found" in result
    helper.send_stix2_bundle.assert_not_called()


def test_process_message_unsupported_entity_type(connector, helper, client):
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "url--x",
        "entity_type": "Url",
        "value": "https://example.test/",
    }
    result = connector._process_message({"entity_id": "url--x"})
    assert "not supported" in result
    client.execute_cypher.assert_not_called()
    helper.send_stix2_bundle.assert_not_called()


def test_process_message_observable_without_value(connector, helper):
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "ipv4--x",
        "entity_type": "IPv4-Addr",
    }
    result = connector._process_message({"entity_id": "ipv4--x"})
    assert "no value to enrich" in result
    helper.send_stix2_bundle.assert_not_called()


def test_process_message_no_whisper_data(connector, helper, client):
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "ipv4--x",
        "entity_type": "IPv4-Addr",
        "observable_value": "1.2.3.4",
    }
    client.execute_cypher.return_value = CypherResult(
        columns=["n", "r", "m"], rows=[], statistics={}
    )
    result = connector._process_message({"entity_id": "ipv4--x"})
    assert "No Whisper data" in result
    helper.send_stix2_bundle.assert_not_called()


def test_process_message_enriches_ipv4_with_resolves_to_hostname(connector, helper, client):
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "ipv4--x",
        "entity_type": "IPv4-Addr",
        "observable_value": "8.8.8.8",
    }
    client.execute_cypher.return_value = CypherResult(
        columns=["n", "r", "m"],
        rows=[
            {
                "n": {"nodeId": "1", "label": "IPV4", "name": "8.8.8.8"},
                "r": {"type": "RESOLVES_TO"},
                "m": {"nodeId": "2", "label": "HOSTNAME", "name": "dns.google"},
            }
        ],
        statistics={"rowCount": 1, "executionTimeMs": 3},
    )

    result = connector._process_message({"entity_id": "ipv4--x"})
    assert "Enriched 8.8.8.8" in result

    helper.send_stix2_bundle.assert_called_once()
    bundle = json.loads(helper.send_stix2_bundle.call_args[0][0])
    types_by_id = {o["id"]: o["type"] for o in bundle["objects"]}
    assert "ipv4-addr" in types_by_id.values()
    assert "domain-name" in types_by_id.values()
    rels = [o for o in bundle["objects"] if o["type"] == "relationship"]
    assert len(rels) == 1
    rel = rels[0]
    assert rel["relationship_type"] == "resolves-to"
    assert rel["source_ref"].startswith("domain-name--")
    assert rel["target_ref"].startswith("ipv4-addr--")


def test_process_message_inlines_value_and_limit_into_query(connector, helper, client):
    # Whisper rejects parameterised queries entirely - both $value and $limit
    # are substituted client-side as Cypher literals.
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "ipv4--x",
        "entity_type": "IPv4-Addr",
        "observable_value": "8.8.8.8",
    }
    client.execute_cypher.return_value = CypherResult(
        columns=["n", "r", "m"], rows=[], statistics={}
    )
    connector._process_message({"entity_id": "ipv4--x"})
    args, _kwargs = client.execute_cypher.call_args
    query = args[0]
    assert "$value" not in query
    assert "$limit" not in query
    assert '"8.8.8.8"' in query
    assert "LIMIT " in query
    # execute_cypher called with no params dict (single positional arg).
    assert len(args) == 1


def test_process_message_returns_no_mappable_rels_when_only_seed_remains(connector, helper, client):
    # Issue #44: when the parser drops every neighbour (unmappable labels
    # like CITY / PREFIX / COUNTRY / FEED_SOURCE) and leaves only the seed
    # observable plus no edges, the connector must NOT report success.
    # Sending a bundle with just the seed adds no new info to OpenCTI and
    # produces a misleading green status. The correct outcome is a clear
    # "No mappable Whisper relationships for X" status with no bundle sent.
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "ipv4--x",
        "entity_type": "IPv4-Addr",
        "observable_value": "8.8.8.8",
    }
    client.execute_cypher.return_value = CypherResult(
        columns=["n", "r", "m"],
        rows=[
            {
                "n": {"nodeId": "1", "label": "IPV4", "name": "8.8.8.8"},
                "r": {"type": "BELONGS_TO"},
                "m": {"nodeId": "2", "label": "PREFIX", "name": "8.8.8.0/24"},
            }
        ],
        statistics={},
    )

    result = connector._process_message({"entity_id": "ipv4--x"})

    assert result == "No mappable Whisper relationships for 8.8.8.8"
    helper.send_stix2_bundle.assert_not_called()


def test_process_message_whisper_transport_error_propagates_and_logs(connector, helper, client):
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "ipv4--x",
        "entity_type": "IPv4-Addr",
        "observable_value": "1.2.3.4",
    }
    client.execute_cypher.side_effect = WhisperTransportError("connection refused")
    with pytest.raises(WhisperTransportError):
        connector._process_message({"entity_id": "ipv4--x"})
    helper.send_stix2_bundle.assert_not_called()
    helper.connector_logger.error.assert_called()


def test_process_message_accepts_observable_value_or_value_field(connector, helper, client):
    # pycti returns different field names across versions - handle both.
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "domain-name--x",
        "entity_type": "Domain-Name",
        "value": "example.test",  # not observable_value
    }
    client.execute_cypher.return_value = CypherResult(
        columns=["n", "r", "m"], rows=[], statistics={}
    )
    result = connector._process_message({"entity_id": "domain-name--x"})
    # Should have inlined the "value" field's value as a Cypher literal.
    assert "No Whisper data for example.test" in result
    client.execute_cypher.assert_called_once()
    query = client.execute_cypher.call_args[0][0]
    assert '"example.test"' in query


def test_process_message_enriches_autonomous_system_via_asn_anchor(connector, helper, client):
    # Issue #48: Autonomous-System is now an in-scope entity type. The
    # connector must derive the Whisper-anchor value from the observable's
    # `number` field (OpenCTI's `observable_value` for autonomous-system
    # is the AS *name* like "Google LLC", not the canonical "AS<number>"
    # form Whisper uses), then issue an ASN-anchored Cypher query.
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "autonomous-system--x",
        "entity_type": "Autonomous-System",
        "observable_value": "Google LLC",  # human-readable name
        "number": 15169,
        "name": "Google LLC",
    }
    client.execute_cypher.return_value = CypherResult(
        columns=["n", "r", "m"],
        rows=[
            {
                "n": {"nodeId": "1", "label": "ASN", "name": "AS15169"},
                "r": {"type": "BELONGS_TO"},
                "m": {"nodeId": "2", "label": "IPV4", "name": "8.8.8.8"},
            }
        ],
        statistics={"rowCount": 1, "executionTimeMs": 5},
    )

    result = connector._process_message({"entity_id": "autonomous-system--x"})
    assert "Enriched AS15169" in result

    # Cypher template fired with the ASN anchor + AS-number-derived value,
    # not the human-readable AS name.
    query = client.execute_cypher.call_args[0][0]
    assert ":ASN" in query
    assert '"AS15169"' in query
    assert "Google LLC" not in query

    helper.send_stix2_bundle.assert_called_once()
    bundle = json.loads(helper.send_stix2_bundle.call_args[0][0])
    types = [o["type"] for o in bundle["objects"]]
    assert "autonomous-system" in types
    assert "ipv4-addr" in types


def test_process_message_autonomous_system_without_number_falls_back(connector, helper, client):
    # Edge case: if the observable somehow lacks a `number` field (older
    # OpenCTI versions, manual STIX import, etc.), we fall back to whatever
    # observable_value / value carries - even if it likely won't match a
    # Whisper ASN node. Better to issue the query and return "No Whisper
    # data" than crash.
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "autonomous-system--x",
        "entity_type": "Autonomous-System",
        "observable_value": "Some-Network",
    }
    client.execute_cypher.return_value = CypherResult(
        columns=["n", "r", "m"], rows=[], statistics={}
    )
    result = connector._process_message({"entity_id": "autonomous-system--x"})
    assert "No Whisper data for Some-Network" in result
