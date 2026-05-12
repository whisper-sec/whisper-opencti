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


def test_process_message_uses_default_limit_param(connector, helper, client):
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
    query, params = args
    assert "$value" in query and "$limit" in query
    assert params["value"] == "8.8.8.8"
    assert params["limit"] >= 1


def test_process_message_drops_unmappable_neighbor_but_still_sends_seed(connector, helper, client):
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
                "r": {"type": "LOCATED_IN"},
                "m": {"nodeId": "2", "label": "CITY", "name": "Mountain View, US"},
            }
        ],
        statistics={},
    )

    result = connector._process_message({"entity_id": "ipv4--x"})
    assert "Enriched 8.8.8.8" in result
    bundle = json.loads(helper.send_stix2_bundle.call_args[0][0])
    types = [o["type"] for o in bundle["objects"]]
    assert types == ["ipv4-addr"]


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
    # Should have called execute_cypher with the "value" field's value.
    assert "No Whisper data for example.test" in result
    client.execute_cypher.assert_called_once()
    _query, params = client.execute_cypher.call_args[0]
    assert params["value"] == "example.test"
