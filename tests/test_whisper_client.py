import logging

import pytest
import requests
import responses
from src.connector.exceptions import (
    WhisperAuthError,
    WhisperQueryError,
    WhisperTransportError,
)
from src.connector.whisper_client import WhisperClient

URL = "https://api.whisper.test/api/query"


@pytest.fixture
def client():
    # Disable retry backoff in tests so they run fast.
    return WhisperClient(
        api_url="https://api.whisper.test",
        api_key="test-key",
        max_retries=2,
        backoff_factor=0,
    )


@responses.activate
def test_execute_cypher_success(client):
    responses.add(
        responses.POST,
        URL,
        json={"results": [{"n": {"id": "ip-1"}}, {"n": {"id": "ip-2"}}]},
        status=200,
    )
    rows = client.execute_cypher("MATCH (n) RETURN n")
    assert rows == [{"n": {"id": "ip-1"}}, {"n": {"id": "ip-2"}}]


@responses.activate
def test_execute_cypher_accepts_data_key(client):
    responses.add(responses.POST, URL, json={"data": [{"x": 1}]}, status=200)
    assert client.execute_cypher("MATCH (n) RETURN n") == [{"x": 1}]


@responses.activate
def test_execute_cypher_sends_api_key_header(client):
    responses.add(responses.POST, URL, json={"results": []}, status=200)
    client.execute_cypher("MATCH (n) RETURN n")
    assert responses.calls[0].request.headers["X-API-Key"] == "test-key"


@responses.activate
def test_execute_cypher_sends_query_and_params(client):
    responses.add(responses.POST, URL, json={"results": []}, status=200)
    client.execute_cypher("MATCH (n {id: $id}) RETURN n", {"id": "ip-1"})
    body = responses.calls[0].request.body
    assert b'"query":' in body
    assert b'"params": {"id": "ip-1"}' in body or b'"params":{"id":"ip-1"}' in body


@responses.activate
def test_execute_cypher_401_raises_auth_error(client):
    responses.add(responses.POST, URL, json={"error": "bad key"}, status=401)
    with pytest.raises(WhisperAuthError):
        client.execute_cypher("MATCH (n) RETURN n")


@responses.activate
def test_execute_cypher_403_raises_auth_error(client):
    responses.add(responses.POST, URL, json={"error": "forbidden"}, status=403)
    with pytest.raises(WhisperAuthError):
        client.execute_cypher("MATCH (n) RETURN n")


@responses.activate
def test_execute_cypher_400_raises_query_error(client):
    responses.add(responses.POST, URL, json={"error": "bad cypher"}, status=400)
    with pytest.raises(WhisperQueryError):
        client.execute_cypher("BAD")


@responses.activate
def test_execute_cypher_5xx_retried_then_raises_transport_error(client):
    # max_retries=2 → 1 initial + 2 retries = 3 attempts total
    for _ in range(3):
        responses.add(responses.POST, URL, json={"error": "internal"}, status=503)
    with pytest.raises(WhisperTransportError):
        client.execute_cypher("MATCH (n) RETURN n")
    assert len(responses.calls) == 3


@responses.activate
def test_execute_cypher_recovers_after_5xx_then_200(client):
    responses.add(responses.POST, URL, status=503)
    responses.add(responses.POST, URL, json={"results": [{"ok": True}]}, status=200)
    rows = client.execute_cypher("MATCH (n) RETURN n")
    assert rows == [{"ok": True}]
    assert len(responses.calls) == 2


@responses.activate
def test_execute_cypher_connection_error_raises_transport_error(client):
    for _ in range(3):
        responses.add(responses.POST, URL, body=requests.ConnectionError("network down"))
    with pytest.raises(WhisperTransportError):
        client.execute_cypher("MATCH (n) RETURN n")


@responses.activate
def test_execute_cypher_non_json_body_raises_query_error(client):
    responses.add(responses.POST, URL, body="not json", status=200)
    with pytest.raises(WhisperQueryError):
        client.execute_cypher("MATCH (n) RETURN n")


@responses.activate
def test_execute_cypher_unexpected_result_shape_raises_query_error(client):
    responses.add(responses.POST, URL, json={"results": {"not": "a list"}}, status=200)
    with pytest.raises(WhisperQueryError):
        client.execute_cypher("MATCH (n) RETURN n")


@responses.activate
def test_api_key_never_logged(client, caplog):
    responses.add(responses.POST, URL, json={"results": []}, status=200)
    with caplog.at_level(logging.DEBUG, logger="src.connector.whisper_client"):
        client.execute_cypher("MATCH (n) RETURN n")
    for record in caplog.records:
        assert "test-key" not in record.getMessage()


def test_init_rejects_empty_url():
    with pytest.raises(ValueError):
        WhisperClient(api_url="", api_key="x")


def test_init_rejects_empty_key():
    with pytest.raises(ValueError):
        WhisperClient(api_url="https://x", api_key="")


def test_init_strips_trailing_slash():
    c = WhisperClient(api_url="https://api.whisper.test/", api_key="k")
    assert c.api_url == "https://api.whisper.test"


@responses.activate
def test_context_manager_closes_session(client):
    responses.add(responses.POST, URL, json={"results": []}, status=200)
    with client as c:
        c.execute_cypher("MATCH (n) RETURN n")
    # Session is closed; another request would re-open connections but the
    # session object itself should still be usable post-close. Just verify
    # no exception leaked from __exit__.
