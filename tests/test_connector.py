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
    # Domain-Name seeds now trigger LINKS_TO supplementary queries (issue
    # #48 Phase A), so we don't assert call-count - just that the main
    # template's value substitution happened in the first call.
    first_query = client.execute_cypher.call_args_list[0][0][0]
    assert '"example.test"' in first_query


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


# --- LINKS_TO supplementary enrichment (issue #48 Phase A) -----------------


def _links_to_side_effect(
    main_rows: list,
    outbound_rows: list,
    inbound_rows: list,
    outbound_count: int,
    inbound_count: int,
):
    """Build an execute_cypher side_effect that returns different rows per
    query, dispatched by which Cypher template was passed in.

    The connector issues 5 calls for a Domain-Name seed: 1 main, then
    outbound, inbound, count_outbound, count_inbound (in that order).
    Matching on substrings keeps the test resilient to whitespace changes.
    """

    def _side_effect(query, *_args, **_kwargs):
        if "count(m)" in query and "-[r:LINKS_TO]->" in query:
            return CypherResult(columns=["c"], rows=[{"c": outbound_count}], statistics={})
        if "count(m)" in query and "<-[r:LINKS_TO]-" in query:
            return CypherResult(columns=["c"], rows=[{"c": inbound_count}], statistics={})
        if "-[r:LINKS_TO]->" in query:
            return CypherResult(columns=["n", "r", "m"], rows=outbound_rows, statistics={})
        if "<-[r:LINKS_TO]-" in query:
            return CypherResult(columns=["n", "r", "m"], rows=inbound_rows, statistics={})
        return CypherResult(columns=["n", "r", "m"], rows=main_rows, statistics={})

    return _side_effect


def test_links_to_supplementary_queries_fire_for_domain_name_seed(connector, helper, client):
    # Domain-Name seed triggers (in order): main + LINKS_TO outbound +
    # LINKS_TO inbound + count_outbound + count_inbound. Plus the Phase B
    # threat-context query also fires for Domain-Name. This is the wiring
    # test for the supplementary LINKS_TO pass - match by query shape so
    # the test stays resilient to other supplementary queries being added.
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "domain-name--x",
        "entity_type": "Domain-Name",
        "value": "example.test",
    }
    client.execute_cypher.side_effect = _links_to_side_effect(
        main_rows=[],
        outbound_rows=[],
        inbound_rows=[],
        outbound_count=0,
        inbound_count=0,
    )
    connector._process_message({"entity_id": "domain-name--x"})

    queries = [c.args[0] for c in client.execute_cypher.call_args_list]
    links_to_queries = [q for q in queries if ":LINKS_TO" in q]
    main_queries = [q for q in queries if 'type(r) <> "LINKS_TO"' in q]
    # Four LINKS_TO queries (outbound, inbound, count_outbound, count_inbound)
    # plus exactly one main query.
    assert len(main_queries) == 1
    assert len(links_to_queries) == 4

    outbound_match = [q for q in links_to_queries if "-[r:LINKS_TO]->" in q and "count(m)" not in q]
    inbound_match = [q for q in links_to_queries if "<-[r:LINKS_TO]-" in q and "count(m)" not in q]
    count_outbound = [q for q in links_to_queries if "-[r:LINKS_TO]->" in q and "count(m)" in q]
    count_inbound = [q for q in links_to_queries if "<-[r:LINKS_TO]-" in q and "count(m)" in q]
    assert len(outbound_match) == len(inbound_match) == 1
    assert len(count_outbound) == len(count_inbound) == 1
    # Cap (25) should be inlined into the directed queries - not LIMIT 50.
    assert "LIMIT 25" in outbound_match[0]
    assert "LIMIT 25" in inbound_match[0]


def test_links_to_outbound_edge_tagged_and_oriented_seed_to_neighbour(connector, helper, client):
    # Outbound LINKS_TO: seed → neighbour. Whisper returns the seed in `n`
    # (source) and neighbour in `m` (target) - parser default keeps that
    # orientation. The edge `description` must say "LINKS_TO outbound" so
    # analysts can distinguish direction in OpenCTI.
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "domain-name--x",
        "entity_type": "Domain-Name",
        "value": "example.test",
    }
    client.execute_cypher.side_effect = _links_to_side_effect(
        main_rows=[],
        outbound_rows=[
            {
                "n": {"nodeId": "seed", "label": "HOSTNAME", "name": "example.test"},
                "r": {"type": "LINKS_TO"},
                "m": {"nodeId": "out1", "label": "HOSTNAME", "name": "neighbour.test"},
            }
        ],
        inbound_rows=[],
        outbound_count=1,
        inbound_count=0,
    )
    connector._process_message({"entity_id": "domain-name--x"})

    helper.send_stix2_bundle.assert_called_once()
    bundle = json.loads(helper.send_stix2_bundle.call_args[0][0])
    rels = [o for o in bundle["objects"] if o["type"] == "relationship"]
    assert len(rels) == 1
    # Edge collapses to related-to with the direction tag in description.
    assert rels[0]["relationship_type"] == "related-to"
    assert rels[0]["description"] == "LINKS_TO outbound"


def test_links_to_inbound_edge_source_target_swapped(connector, helper, client):
    # Inbound LINKS_TO: neighbour → seed semantically. The Whisper query
    # uses the seed-anchored MATCH pattern that puts the seed in column `n`
    # regardless of direction, so the parser's column-position default
    # gives us (seed → neighbour). The connector must swap source/target
    # before emitting so the STIX relationship correctly reads
    # neighbour → seed.
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "domain-name--x",
        "entity_type": "Domain-Name",
        "value": "example.test",
    }
    client.execute_cypher.side_effect = _links_to_side_effect(
        main_rows=[],
        outbound_rows=[],
        inbound_rows=[
            {
                "n": {"nodeId": "seed", "label": "HOSTNAME", "name": "example.test"},
                "r": {"type": "LINKS_TO"},
                "m": {"nodeId": "in1", "label": "HOSTNAME", "name": "referrer.test"},
            }
        ],
        outbound_count=0,
        inbound_count=1,
    )
    connector._process_message({"entity_id": "domain-name--x"})

    bundle = json.loads(helper.send_stix2_bundle.call_args[0][0])
    rels = [o for o in bundle["objects"] if o["type"] == "relationship"]
    assert len(rels) == 1
    rel = rels[0]
    assert rel["description"] == "LINKS_TO inbound"
    # Resolve refs back to SCO values to assert direction.
    by_id = {o["id"]: o for o in bundle["objects"]}
    source = by_id[rel["source_ref"]]
    target = by_id[rel["target_ref"]]
    assert source["value"] == "referrer.test"
    assert target["value"] == "example.test"


def test_links_to_cap_overflow_emits_note_attached_to_seed(connector, helper, client):
    # When Whisper has more than LINKS_TO_CAP (25) neighbours in either
    # direction, the connector must emit a STIX Note attached to the seed
    # so the analyst sees "showing first 25" instead of being misled into
    # thinking 25 is the full picture. Both directions overflow here.
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "domain-name--x",
        "entity_type": "Domain-Name",
        "value": "example.test",
    }
    client.execute_cypher.side_effect = _links_to_side_effect(
        main_rows=[
            {
                "n": {"nodeId": "seed", "label": "HOSTNAME", "name": "example.test"},
                "r": {"type": "RESOLVES_TO"},
                "m": {"nodeId": "ip1", "label": "IPV4", "name": "1.2.3.4"},
            }
        ],
        outbound_rows=[],
        inbound_rows=[],
        outbound_count=42,
        inbound_count=12_800_000,
    )
    connector._process_message({"entity_id": "domain-name--x"})

    bundle = json.loads(helper.send_stix2_bundle.call_args[0][0])
    notes = [o for o in bundle["objects"] if o["type"] == "note"]
    assert len(notes) == 1
    note = notes[0]
    assert note["abstract"] == "LINKS_TO neighbour overflow"
    assert "42 outbound" in note["content"]
    assert "12800000 inbound" in note["content"]
    assert "showing first 25" in note["content"]
    # Note must be attached to the seed Domain-Name SCO.
    seed_id = next(
        o["id"]
        for o in bundle["objects"]
        if o["type"] == "domain-name" and o["value"] == "example.test"
    )
    assert note["object_refs"] == [seed_id]


def test_links_to_no_overflow_omits_note(connector, helper, client):
    # Counts at-or-below the cap should NOT generate a Note. Only emit the
    # overflow notice when it's actually informative.
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "domain-name--x",
        "entity_type": "Domain-Name",
        "value": "example.test",
    }
    client.execute_cypher.side_effect = _links_to_side_effect(
        main_rows=[
            {
                "n": {"nodeId": "seed", "label": "HOSTNAME", "name": "example.test"},
                "r": {"type": "RESOLVES_TO"},
                "m": {"nodeId": "ip1", "label": "IPV4", "name": "1.2.3.4"},
            }
        ],
        outbound_rows=[],
        inbound_rows=[],
        outbound_count=3,
        inbound_count=25,  # exactly at cap is fine - not overflowing
    )
    connector._process_message({"entity_id": "domain-name--x"})

    bundle = json.loads(helper.send_stix2_bundle.call_args[0][0])
    assert not any(o["type"] == "note" for o in bundle["objects"])


def test_links_to_supplementary_skipped_for_non_domain_seeds(connector, helper, client):
    # IPv4, IPv6, and Autonomous-System seeds must NOT trigger directed
    # LINKS_TO queries - that edge type only exists between HOSTNAME nodes
    # in Whisper's schema. (Threat-context Phase B does fire for IPv4/IPv6
    # but that's a different supplementary query; we assert "no LINKS_TO
    # directed/count templates" rather than "exactly one query".)
    for entity_id, entity_type, extra in (
        ("ipv4--x", "IPv4-Addr", {"observable_value": "1.2.3.4"}),
        ("ipv6--x", "IPv6-Addr", {"observable_value": "::1"}),
        (
            "autonomous-system--x",
            "Autonomous-System",
            {"observable_value": "Google LLC", "number": 15169},
        ),
    ):
        client.reset_mock()
        helper.api.stix_cyber_observable.read.return_value = {
            "id": entity_id,
            "entity_type": entity_type,
            **extra,
        }
        client.execute_cypher.return_value = CypherResult(
            columns=["n", "r", "m"], rows=[], statistics={}
        )
        connector._process_message({"entity_id": entity_id})
        queries = [c.args[0] for c in client.execute_cypher.call_args_list]
        # Zero directed/count LINKS_TO queries for non-Domain-Name seeds.
        for q in queries:
            assert "-[r:LINKS_TO]" not in q, f"{entity_type}: unexpected LINKS_TO query: {q}"
            assert "<-[r:LINKS_TO]" not in q, f"{entity_type}: unexpected LINKS_TO query: {q}"


def test_links_to_supplementary_failure_does_not_fail_enrichment(connector, helper, client):
    # The LINKS_TO supplementary pass is nice-to-have. If it raises a
    # transport error mid-flight, the main enrichment result still gets
    # delivered - we don't punish the seed because of a flaky follow-up.
    from src.connector.exceptions import WhisperTransportError

    helper.api.stix_cyber_observable.read.return_value = {
        "id": "domain-name--x",
        "entity_type": "Domain-Name",
        "value": "example.test",
    }

    main_result = CypherResult(
        columns=["n", "r", "m"],
        rows=[
            {
                "n": {"nodeId": "seed", "label": "HOSTNAME", "name": "example.test"},
                "r": {"type": "RESOLVES_TO"},
                "m": {"nodeId": "ip1", "label": "IPV4", "name": "1.2.3.4"},
            }
        ],
        statistics={"executionTimeMs": 4},
    )

    def _flaky(query, *_args, **_kwargs):
        if "LINKS_TO" in query and 'type(r) <> "LINKS_TO"' not in query:
            raise WhisperTransportError("connection reset")
        return main_result

    client.execute_cypher.side_effect = _flaky

    result = connector._process_message({"entity_id": "domain-name--x"})
    assert "Enriched example.test" in result
    helper.send_stix2_bundle.assert_called_once()
    helper.connector_logger.error.assert_called()


# --- Threat-context Note (issue #48 Phase B) -------------------------------


def _threat_context_side_effect(main_rows, threat_rows):
    """Dispatch helper: ignore LINKS_TO supplementary queries (return empty),
    return ``threat_rows`` for the threat-context query, ``main_rows`` for
    the main template. Keeps Phase B tests independent of Phase A wiring.
    """

    def _side_effect(query, *_args, **_kwargs):
        if "FEED_SOURCE" in query and "LISTED_IN" in query:
            return CypherResult(
                columns=["threatScore", "threatLevel", "feedName"],
                rows=threat_rows,
                statistics={},
            )
        if ":LINKS_TO" in query:
            cols = ["c"] if "count(m)" in query else ["n", "r", "m"]
            return CypherResult(columns=cols, rows=[], statistics={})
        return CypherResult(columns=["n", "r", "m"], rows=main_rows, statistics={})

    return _side_effect


def _seed_main_row(label="HOSTNAME", name="malware-traffic-analysis.net"):
    return {
        "n": {"nodeId": "seed", "label": label, "name": name},
        "r": {"type": "RESOLVES_TO"},
        "m": {"nodeId": "ip1", "label": "IPV4", "name": "1.2.3.4"},
    }


def test_threat_context_emits_note_with_score_level_flags_and_feeds(connector, helper, client):
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "domain-name--x",
        "entity_type": "Domain-Name",
        "value": "malware-traffic-analysis.net",
    }
    client.execute_cypher.side_effect = _threat_context_side_effect(
        main_rows=[_seed_main_row()],
        threat_rows=[
            {
                "threatScore": 3.169,
                "threatLevel": "MEDIUM",
                "isMalware": True,
                "isC2": False,
                "isPhishing": False,
                "isThreat": True,
                "threatFirstSeen": 1779849886074,
                "threatLastSeen": 1779849886718,
                "feedName": "tranco-top1m",
                "feedFirstSeen": 1779849886074,
                "feedLastSeen": 1779849886074,
                "feedWeight": 1.0,
            },
            {
                "threatScore": 3.169,
                "threatLevel": "MEDIUM",
                "isMalware": True,
                "isThreat": True,
                "threatFirstSeen": 1779849886074,
                "threatLastSeen": 1779849886718,
                "feedName": "cloudflare-radar-top1m",
                "feedFirstSeen": None,
                "feedLastSeen": None,
                "feedWeight": None,
            },
        ],
    )
    connector._process_message({"entity_id": "domain-name--x"})

    bundle = json.loads(helper.send_stix2_bundle.call_args[0][0])
    threat_notes = [
        o
        for o in bundle["objects"]
        if o["type"] == "note" and o.get("abstract") == "Whisper threat intelligence"
    ]
    assert len(threat_notes) == 1
    note = threat_notes[0]
    content = note["content"]
    assert "Threat assessment: MEDIUM (score 3.169)" in content
    # ISO-formatted timestamps for first/last seen - epoch ms 1779849886074
    # is 2026-05-27 UTC. Asserting the prefix exactly catches regressions
    # where the formatter forgets the UTC conversion.
    assert "First seen: 2026-05-27T02:44:46Z" in content
    assert "Last seen: 2026-05-27T02:44:46Z" in content
    # Only true flags should be listed.
    assert "isMalware" in content
    assert "isThreat" in content
    assert "isC2" not in content
    # Feed listings.
    assert "Listed in 2 source(s):" in content
    assert "tranco-top1m" in content
    assert "cloudflare-radar-top1m" in content
    # Note must be attached to the seed Domain-Name SCO.
    seed_id = next(
        o["id"]
        for o in bundle["objects"]
        if o["type"] == "domain-name" and o["value"] == "malware-traffic-analysis.net"
    )
    assert note["object_refs"] == [seed_id]


def test_threat_context_omits_note_when_no_threat_data(connector, helper, client):
    # Whisper has the seed but no threat properties / no feed listings.
    # Emitting a Note that says "no threat data" would be noise - skip it.
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "domain-name--x",
        "entity_type": "Domain-Name",
        "value": "boring.test",
    }
    client.execute_cypher.side_effect = _threat_context_side_effect(
        main_rows=[_seed_main_row(name="boring.test")],
        threat_rows=[
            {
                "threatScore": 0.0,
                "threatLevel": "NONE",
                "isMalware": False,
                "isThreat": False,
                "threatFirstSeen": None,
                "threatLastSeen": None,
                "feedName": None,
            }
        ],
    )
    connector._process_message({"entity_id": "domain-name--x"})

    bundle = json.loads(helper.send_stix2_bundle.call_args[0][0])
    assert not any(
        o.get("abstract") == "Whisper threat intelligence"
        for o in bundle["objects"]
        if o["type"] == "note"
    )


def test_threat_context_note_emitted_with_score_only_no_feeds(connector, helper, client):
    # Seed has a score and level but isn't on any FEED_SOURCE - still
    # produces a Note. The Note is the only analyst-visible breadcrumb that
    # Whisper has any threat opinion on this seed.
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "ipv4--x",
        "entity_type": "IPv4-Addr",
        "observable_value": "203.0.113.42",
    }
    client.execute_cypher.side_effect = _threat_context_side_effect(
        main_rows=[
            {
                "n": {"nodeId": "seed", "label": "IPV4", "name": "203.0.113.42"},
                "r": {"type": "RESOLVES_TO"},
                "m": {"nodeId": "h1", "label": "HOSTNAME", "name": "example.test"},
            }
        ],
        threat_rows=[
            {
                "threatScore": 1.5,
                "threatLevel": "LOW",
                "feedName": None,
            }
        ],
    )
    connector._process_message({"entity_id": "ipv4--x"})

    bundle = json.loads(helper.send_stix2_bundle.call_args[0][0])
    note = next(
        o
        for o in bundle["objects"]
        if o["type"] == "note" and o.get("abstract") == "Whisper threat intelligence"
    )
    assert "Threat assessment: LOW (score 1.5)" in note["content"]
    assert "Listed in" not in note["content"]


def test_threat_context_query_failure_does_not_fail_enrichment(connector, helper, client):
    # Threat-context Phase B is best-effort - a transport error there must
    # still let the main bundle ship. Mirrors the Phase A LINKS_TO failure
    # path.
    from src.connector.exceptions import WhisperTransportError

    helper.api.stix_cyber_observable.read.return_value = {
        "id": "ipv4--x",
        "entity_type": "IPv4-Addr",
        "observable_value": "8.8.8.8",
    }
    main_result = CypherResult(
        columns=["n", "r", "m"],
        rows=[
            {
                "n": {"nodeId": "seed", "label": "IPV4", "name": "8.8.8.8"},
                "r": {"type": "RESOLVES_TO"},
                "m": {"nodeId": "h1", "label": "HOSTNAME", "name": "dns.google"},
            }
        ],
        statistics={"executionTimeMs": 2},
    )

    def _flaky(query, *_args, **_kwargs):
        if "FEED_SOURCE" in query and "LISTED_IN" in query:
            raise WhisperTransportError("threat-context timeout")
        return main_result

    client.execute_cypher.side_effect = _flaky

    result = connector._process_message({"entity_id": "ipv4--x"})
    assert "Enriched 8.8.8.8" in result
    helper.send_stix2_bundle.assert_called_once()
    helper.connector_logger.error.assert_called()
    bundle = json.loads(helper.send_stix2_bundle.call_args[0][0])
    assert not any(
        o.get("abstract") == "Whisper threat intelligence"
        for o in bundle["objects"]
        if o["type"] == "note"
    )


def test_threat_context_skipped_for_autonomous_system_seed(connector, helper, client):
    # ASN nodes don't carry threat properties in Whisper's schema, so no
    # threat-context query should fire for Autonomous-System seeds.
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "autonomous-system--x",
        "entity_type": "Autonomous-System",
        "observable_value": "Google LLC",
        "number": 15169,
    }
    client.execute_cypher.return_value = CypherResult(
        columns=["n", "r", "m"], rows=[], statistics={}
    )
    connector._process_message({"entity_id": "autonomous-system--x"})

    queries = [c.args[0] for c in client.execute_cypher.call_args_list]
    for q in queries:
        assert not (
            "FEED_SOURCE" in q and "LISTED_IN" in q
        ), f"threat-context query unexpectedly fired for ASN: {q}"


def test_threat_context_note_ships_even_when_no_mappable_relationships(connector, helper, client):
    # Seed has threat data but every main-query neighbour is a dropped
    # label (PREFIX). Without Phase B that's "No mappable Whisper
    # relationships" - but the threat Note IS meaningful, so the bundle
    # ships and the status is the regular "Enriched" line.
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "ipv4--x",
        "entity_type": "IPv4-Addr",
        "observable_value": "192.0.2.7",
    }
    client.execute_cypher.side_effect = _threat_context_side_effect(
        main_rows=[
            {
                "n": {"nodeId": "seed", "label": "IPV4", "name": "192.0.2.7"},
                "r": {"type": "BELONGS_TO"},
                "m": {"nodeId": "p1", "label": "PREFIX", "name": "192.0.2.0/24"},
            }
        ],
        threat_rows=[
            {
                "threatScore": 7.2,
                "threatLevel": "HIGH",
                "isMalware": True,
                "feedName": "abuse-ch-feodo",
            }
        ],
    )
    result = connector._process_message({"entity_id": "192.0.2.7"} | {"entity_id": "ipv4--x"})
    assert "Enriched 192.0.2.7" in result
    helper.send_stix2_bundle.assert_called_once()
    bundle = json.loads(helper.send_stix2_bundle.call_args[0][0])
    assert any(
        o.get("abstract") == "Whisper threat intelligence"
        for o in bundle["objects"]
        if o["type"] == "note"
    )


# --- Network-context Phase C (issue #48) -----------------------------------


def _network_context_side_effect(main_rows, network_rows, threat_rows=None):
    """Dispatch helper for Phase C tests.

    - LINKS_TO queries → empty (Phase A is independently tested).
    - threat-context query → ``threat_rows`` or empty.
    - network-context query (matches by "ANNOUNCED_BY" + "ROUTES") → ``network_rows``.
    - everything else → main template result.
    """
    threat_rows = threat_rows or []

    def _side_effect(query, *_args, **_kwargs):
        if "ANNOUNCED_BY" in query and "ROUTES" in query:
            return CypherResult(
                columns=[
                    "seed",
                    "asn",
                    "asnDescription",
                    "announcedPrefix",
                    "apThreatScore",
                    "apThreatLevel",
                    "isAnycast",
                    "isMoas",
                    "isWithdrawn",
                    "prefix",
                ],
                rows=network_rows,
                statistics={},
            )
        if "FEED_SOURCE" in query and "LISTED_IN" in query:
            return CypherResult(
                columns=["threatScore", "threatLevel", "feedName"],
                rows=threat_rows,
                statistics={},
            )
        if ":LINKS_TO" in query:
            cols = ["c"] if "count(m)" in query else ["n", "r", "m"]
            return CypherResult(columns=cols, rows=[], statistics={})
        return CypherResult(columns=["n", "r", "m"], rows=main_rows, statistics={})

    return _side_effect


def _ip_seed_row(value="8.8.8.8"):
    return {
        "n": {"nodeId": "seed", "label": "IPV4", "name": value},
        "r": {"type": "RESOLVES_TO"},
        "m": {"nodeId": "h1", "label": "HOSTNAME", "name": "dns.google"},
    }


def _network_row(
    *,
    seed_id="seed",
    seed_name="8.8.8.8",
    seed_label="IPV4",
    asn_id="asn1",
    asn_name="AS15169",
    asn_desc="GOOGLE - Google LLC",
    prefix_announced="8.8.8.0/24",
    score=1.0,
    level="LOW",
    anycast=True,
    moas=False,
    withdrawn=False,
    static_prefix="8.8.8.8/32",
):
    return {
        "seed": {"nodeId": seed_id, "label": seed_label, "name": seed_name},
        "asn": {"nodeId": asn_id, "label": "ASN", "name": asn_name},
        "asnDescription": asn_desc,
        "announcedPrefix": prefix_announced,
        "apThreatScore": score,
        "apThreatLevel": level,
        "isAnycast": anycast,
        "isMoas": moas,
        "isWithdrawn": withdrawn,
        "prefix": static_prefix,
    }


def test_network_context_emits_as_sco_edge_and_note(connector, helper, client):
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "ipv4--x",
        "entity_type": "IPv4-Addr",
        "observable_value": "8.8.8.8",
    }
    client.execute_cypher.side_effect = _network_context_side_effect(
        main_rows=[_ip_seed_row()],
        network_rows=[_network_row()],
    )
    connector._process_message({"entity_id": "ipv4--x"})

    bundle = json.loads(helper.send_stix2_bundle.call_args[0][0])

    as_sco = next(o for o in bundle["objects"] if o["type"] == "autonomous-system")
    assert as_sco["number"] == 15169
    # ASN_NAME human label should win over the bare "AS15169" string.
    assert as_sco["name"] == "GOOGLE - Google LLC"

    rels = [o for o in bundle["objects"] if o["type"] == "relationship"]
    announced_by = [r for r in rels if r.get("description") == "ANNOUNCED_BY"]
    assert len(announced_by) == 1
    rel = announced_by[0]
    assert rel["relationship_type"] == "related-to"
    assert rel["source_ref"].startswith("ipv4-addr--")
    assert rel["target_ref"] == as_sco["id"]

    notes = [
        o
        for o in bundle["objects"]
        if o["type"] == "note" and o.get("abstract") == "Whisper network context"
    ]
    assert len(notes) == 1
    content = notes[0]["content"]
    assert "Announced by: AS15169 (GOOGLE - Google LLC)" in content
    assert "Announced prefix: 8.8.8.0/24" in content
    assert "BGP flags: anycast" in content
    assert "ANNOUNCED_PREFIX threat: LOW (score 1)" in content
    assert "Static allocation: 8.8.8.8/32" in content


def test_network_context_falls_back_to_as_number_label_without_has_name(connector, helper, client):
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "ipv4--x",
        "entity_type": "IPv4-Addr",
        "observable_value": "203.0.113.5",
    }
    client.execute_cypher.side_effect = _network_context_side_effect(
        main_rows=[_ip_seed_row(value="203.0.113.5")],
        network_rows=[
            _network_row(
                seed_name="203.0.113.5",
                asn_id="asn99",
                asn_name="AS64500",
                asn_desc=None,
                prefix_announced="203.0.113.0/24",
                static_prefix=None,
                level="NONE",
            )
        ],
    )
    connector._process_message({"entity_id": "ipv4--x"})

    bundle = json.loads(helper.send_stix2_bundle.call_args[0][0])
    as_sco = next(o for o in bundle["objects"] if o["type"] == "autonomous-system")
    assert as_sco["number"] == 64500
    # No HAS_NAME → name is omitted, so the SCO has just `number`.
    assert "name" not in as_sco

    note = next(
        o
        for o in bundle["objects"]
        if o["type"] == "note" and o.get("abstract") == "Whisper network context"
    )
    # Without a description the announcer line is just "AS64500".
    assert "Announced by: AS64500\n" in note["content"] or note["content"].endswith(
        "Announced by: AS64500"
    )
    # NONE-level threat line should be omitted.
    assert "ANNOUNCED_PREFIX threat" not in note["content"]


def test_network_context_handles_moas_multiple_announcers(connector, helper, client):
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "ipv4--x",
        "entity_type": "IPv4-Addr",
        "observable_value": "1.2.3.4",
    }
    client.execute_cypher.side_effect = _network_context_side_effect(
        main_rows=[_ip_seed_row(value="1.2.3.4")],
        network_rows=[
            _network_row(
                seed_name="1.2.3.4",
                asn_id="asn-a",
                asn_name="AS100",
                asn_desc="ASN A Inc",
                prefix_announced="1.2.3.0/24",
                moas=True,
            ),
            _network_row(
                seed_name="1.2.3.4",
                asn_id="asn-b",
                asn_name="AS200",
                asn_desc="ASN B Corp",
                prefix_announced="1.2.0.0/16",
                moas=True,
                score=None,
                level=None,
                anycast=False,
            ),
        ],
    )
    connector._process_message({"entity_id": "ipv4--x"})

    bundle = json.loads(helper.send_stix2_bundle.call_args[0][0])
    as_numbers = sorted(o["number"] for o in bundle["objects"] if o["type"] == "autonomous-system")
    assert as_numbers == [100, 200]

    rels = [
        o
        for o in bundle["objects"]
        if o["type"] == "relationship" and o.get("description") == "ANNOUNCED_BY"
    ]
    assert len(rels) == 2

    note = next(
        o
        for o in bundle["objects"]
        if o["type"] == "note" and o.get("abstract") == "Whisper network context"
    )
    assert "Announced by 2 ASN(s) - multi-origin (MOAS):" in note["content"]
    assert "AS100 (ASN A Inc)" in note["content"]
    assert "AS200 (ASN B Corp)" in note["content"]


def test_network_context_dedups_repeated_asn_rows(connector, helper, client):
    # Whisper can emit multiple rows for the same ASN when the OPTIONAL
    # MATCHes cross-join (e.g. the IP has multiple BELONGS_TO PREFIXes).
    # The connector must dedup by ASN nodeId - otherwise we get duplicate
    # AS SCOs and edges.
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "ipv4--x",
        "entity_type": "IPv4-Addr",
        "observable_value": "8.8.8.8",
    }
    client.execute_cypher.side_effect = _network_context_side_effect(
        main_rows=[_ip_seed_row()],
        network_rows=[
            _network_row(static_prefix="8.8.8.0/24"),
            _network_row(static_prefix="8.8.0.0/16"),  # same ASN, different PREFIX
        ],
    )
    connector._process_message({"entity_id": "ipv4--x"})

    bundle = json.loads(helper.send_stix2_bundle.call_args[0][0])
    as_scos = [o for o in bundle["objects"] if o["type"] == "autonomous-system"]
    assert len(as_scos) == 1
    rels = [
        o
        for o in bundle["objects"]
        if o["type"] == "relationship" and o.get("description") == "ANNOUNCED_BY"
    ]
    assert len(rels) == 1


def test_network_context_skipped_for_domain_seed(connector, helper, client):
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "domain-name--x",
        "entity_type": "Domain-Name",
        "value": "example.test",
    }
    client.execute_cypher.return_value = CypherResult(
        columns=["n", "r", "m"], rows=[], statistics={}
    )
    connector._process_message({"entity_id": "domain-name--x"})

    queries = [c.args[0] for c in client.execute_cypher.call_args_list]
    for q in queries:
        assert not (
            "ANNOUNCED_BY" in q and "ROUTES" in q
        ), f"network-context query unexpectedly fired for Domain-Name: {q}"


def test_network_context_skipped_for_asn_seed(connector, helper, client):
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "autonomous-system--x",
        "entity_type": "Autonomous-System",
        "observable_value": "Google LLC",
        "number": 15169,
    }
    client.execute_cypher.return_value = CypherResult(
        columns=["n", "r", "m"], rows=[], statistics={}
    )
    connector._process_message({"entity_id": "autonomous-system--x"})

    queries = [c.args[0] for c in client.execute_cypher.call_args_list]
    for q in queries:
        assert not (
            "ANNOUNCED_BY" in q and "ROUTES" in q
        ), f"network-context query unexpectedly fired for ASN: {q}"


def test_network_context_query_failure_does_not_fail_enrichment(connector, helper, client):
    from src.connector.exceptions import WhisperTransportError

    helper.api.stix_cyber_observable.read.return_value = {
        "id": "ipv4--x",
        "entity_type": "IPv4-Addr",
        "observable_value": "8.8.8.8",
    }
    main_result = CypherResult(
        columns=["n", "r", "m"],
        rows=[_ip_seed_row()],
        statistics={"executionTimeMs": 2},
    )

    def _flaky(query, *_args, **_kwargs):
        if "ANNOUNCED_BY" in query and "ROUTES" in query:
            raise WhisperTransportError("network-context timeout")
        return main_result

    client.execute_cypher.side_effect = _flaky

    result = connector._process_message({"entity_id": "ipv4--x"})
    assert "Enriched 8.8.8.8" in result
    helper.send_stix2_bundle.assert_called_once()
    helper.connector_logger.error.assert_called()
    bundle = json.loads(helper.send_stix2_bundle.call_args[0][0])
    assert not any(o["type"] == "autonomous-system" for o in bundle["objects"])


def test_network_context_omits_static_allocation_when_same_as_announced(connector, helper, client):
    # Issue #48 follow-up sanity: if Whisper returns a static PREFIX that
    # already matches the ANNOUNCED_PREFIX, the Note shouldn't repeat it
    # under a separate "Static allocation" line - that's pure noise.
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "ipv4--x",
        "entity_type": "IPv4-Addr",
        "observable_value": "8.8.8.8",
    }
    client.execute_cypher.side_effect = _network_context_side_effect(
        main_rows=[_ip_seed_row()],
        network_rows=[_network_row(static_prefix="8.8.8.0/24")],  # same as announced
    )
    connector._process_message({"entity_id": "ipv4--x"})

    bundle = json.loads(helper.send_stix2_bundle.call_args[0][0])
    note = next(
        o
        for o in bundle["objects"]
        if o["type"] == "note" and o.get("abstract") == "Whisper network context"
    )
    assert "Static allocation" not in note["content"]


# --- Dropped-HOSTNAME Note (issue #51) -------------------------------------


def test_dropped_hostnames_note_emitted_with_seed_attachment(connector, helper, client):
    # Main query has a NAMESERVER_FOR edge to an SPF-style invalid HOSTNAME.
    # The parser drops it (per #47); the connector now ALSO surfaces it
    # via a Note attached to the seed.
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "domain-name--x",
        "entity_type": "Domain-Name",
        "value": "telus.ca",
    }
    client.execute_cypher.return_value = CypherResult(
        columns=["n", "r", "m"],
        rows=[
            {
                "n": {"nodeId": "seed", "label": "HOSTNAME", "name": "telus.ca"},
                "r": {"type": "NAMESERVER_FOR"},
                "m": {
                    "nodeId": "ns-invalid",
                    "label": "HOSTNAME",
                    "name": "_spf_telus_com.nssi.telus.com",
                },
            },
            {
                "n": {"nodeId": "seed", "label": "HOSTNAME", "name": "telus.ca"},
                "r": {"type": "NAMESERVER_FOR"},
                "m": {
                    "nodeId": "ns-valid",
                    "label": "HOSTNAME",
                    "name": "ns.telus.com",
                },
            },
        ],
        statistics={"executionTimeMs": 2},
    )
    connector._process_message({"entity_id": "domain-name--x"})

    bundle = json.loads(helper.send_stix2_bundle.call_args[0][0])
    notes = [
        o
        for o in bundle["objects"]
        if o["type"] == "note" and o.get("abstract") == "Whisper dropped non-RFC-1035 DNS records"
    ]
    assert len(notes) == 1
    note = notes[0]
    assert "_spf_telus_com.nssi.telus.com" in note["content"]
    assert "Whisper edge: NAMESERVER_FOR" in note["content"]
    # Sanity: the VALID neighbour must not appear in the dropped list.
    assert "ns.telus.com" not in note["content"]
    # Note attached to the seed Domain-Name SCO.
    seed_id = next(
        o["id"]
        for o in bundle["objects"]
        if o["type"] == "domain-name" and o["value"] == "telus.ca"
    )
    assert note["object_refs"] == [seed_id]


def test_dropped_hostnames_note_skipped_when_nothing_dropped(connector, helper, client):
    # Clean enrichment with no underscore-bearing neighbours must not
    # produce a dropped-records Note - bundle shape unchanged from
    # pre-#51 behaviour.
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "domain-name--x",
        "entity_type": "Domain-Name",
        "value": "telus.ca",
    }
    client.execute_cypher.return_value = CypherResult(
        columns=["n", "r", "m"],
        rows=[
            {
                "n": {"nodeId": "seed", "label": "HOSTNAME", "name": "telus.ca"},
                "r": {"type": "RESOLVES_TO"},
                "m": {"nodeId": "ip1", "label": "IPV4", "name": "1.2.3.4"},
            }
        ],
        statistics={},
    )
    connector._process_message({"entity_id": "domain-name--x"})

    bundle = json.loads(helper.send_stix2_bundle.call_args[0][0])
    assert not any(
        o.get("abstract") == "Whisper dropped non-RFC-1035 DNS records"
        for o in bundle["objects"]
        if o["type"] == "note"
    )


def test_dropped_hostnames_note_dedupes_same_name_across_rows(connector, helper, client):
    # The same invalid HOSTNAME may appear in multiple rows (e.g. once
    # via NAMESERVER_FOR, once via MAIL_FOR). The Note must list it
    # exactly once - the first edge type wins so the content is stable.
    helper.api.stix_cyber_observable.read.return_value = {
        "id": "domain-name--x",
        "entity_type": "Domain-Name",
        "value": "example.com",
    }
    client.execute_cypher.return_value = CypherResult(
        columns=["n", "r", "m"],
        rows=[
            {
                "n": {"nodeId": "seed", "label": "HOSTNAME", "name": "example.com"},
                "r": {"type": "NAMESERVER_FOR"},
                "m": {"nodeId": "bad", "label": "HOSTNAME", "name": "_spf.example.com"},
            },
            {
                "n": {"nodeId": "seed", "label": "HOSTNAME", "name": "example.com"},
                "r": {"type": "MAIL_FOR"},
                "m": {"nodeId": "bad", "label": "HOSTNAME", "name": "_spf.example.com"},
            },
        ],
        statistics={},
    )
    connector._process_message({"entity_id": "domain-name--x"})

    bundle = json.loads(helper.send_stix2_bundle.call_args[0][0])
    note = next(
        o
        for o in bundle["objects"]
        if o["type"] == "note" and o.get("abstract") == "Whisper dropped non-RFC-1035 DNS records"
    )
    # The dropped name must appear exactly once in the body.
    assert note["content"].count("_spf.example.com") == 1
