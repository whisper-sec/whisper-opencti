"""Tests for the agent-activity log → STIX 2.1 converter.

One test per ``kind`` using the REAL redacted live rows, asserting the STIX
shape from the design mapping, deterministic-ID idempotency, the agent-id →
/128 join, author presence, and the ``open``-connection suppression.
"""

import stix2

from src.connector.converter_to_stix import WHISPER_AUTHOR
from src.connector.log_converter import build_agent_anchor, convert_log_row

LOG_COLUMNS = [
    "ts", "kind", "qname", "qtype", "rcode", "decision", "source", "answer",
    "latency_ms", "agent", "peer", "bytes_up", "bytes_down", "duration_ms",
    "reason", "client_src", "packets_up", "packets_down",
]  # fmt: skip

AGENT_ID = "a98874349306a52c8"
ADDRESS = "2a04:2a01:1:2:3:4:5:6"
# Live `op:list` returns fqdns WITH a trailing root dot; the converter must
# strip it before building the domain-name SCO (else the OpenCTI worker rejects
# the observable). Keep both forms so tests lock the normalization.
FQDN = "a98874349306a52c8.agents.whisper.online."
FQDN_NORM = "a98874349306a52c8.agents.whisper.online"
AGENTS = {
    AGENT_ID: {"agent": AGENT_ID, "address": ADDRESS, "fqdn": FQDN, "label": "e2e"}
}


def _row(**overrides):
    base = dict.fromkeys(LOG_COLUMNS)
    base["agent"] = AGENT_ID
    base.update(overrides)
    return base


def _types(objs):
    return [o.type for o in objs]


def _by_type(objs, t):
    return [o for o in objs if o.type == t]


def test_anchor_joins_agent_id_to_128_and_fqdn():
    objs, refs = build_agent_anchor(AGENT_ID, AGENTS[AGENT_ID])
    ipv6 = _by_type(objs, "ipv6-addr")[0]
    domain = _by_type(objs, "domain-name")[0]
    infra = _by_type(objs, "infrastructure")[0]
    assert ipv6.value == ADDRESS
    # The trailing root dot from op:list is stripped before the DomainName SCO.
    assert domain.value == FQDN_NORM
    assert not domain.value.endswith(".")
    assert infra.infrastructure_types == ["hosting"]
    # consists-of rels wire the /128 and fqdn to the Infrastructure.
    rels = _by_type(objs, "relationship")
    assert all(r.relationship_type == "consists-of" for r in rels)
    assert {r.target_ref for r in rels} == {ipv6.id, domain.id}
    assert refs["ipv6"] == ipv6.id and refs["domain"] == domain.id


def test_anchor_tolerates_missing_identity_fields():
    objs, refs = build_agent_anchor("orphan", None)
    assert _by_type(objs, "infrastructure")
    assert not _by_type(objs, "ipv6-addr")
    assert refs["ipv6"] is None and refs["domain"] is None


def test_alloc_emits_observed_data_over_identity_scos():
    row = _row(ts=1784002483385, kind="alloc")
    objs = convert_log_row(row, AGENTS)
    assert "observed-data" in _types(objs)
    obs = _by_type(objs, "observed-data")[0]
    ipv6 = _by_type(objs, "ipv6-addr")[0]
    domain = _by_type(objs, "domain-name")[0]
    assert set(obs.object_refs) == {ipv6.id, domain.id}
    assert obs.number_observed == 1


def test_dns_allow_emits_resolves_to_and_observed_data():
    row = _row(
        ts=1784002495932,
        kind="dns",
        qname="rdap.whisper.online",
        qtype="AAAA",
        rcode="NOERROR",
        decision="allow",
        source="upstream",
        answer="2001:19f0:5000:15f6::1",
    )
    objs = convert_log_row(row, AGENTS)
    types = _types(objs)
    assert "domain-name" in types  # qname (+ agent fqdn)
    assert "ipv6-addr" in types  # resolved answer (+ agent /128)
    resolves = [
        r
        for r in _by_type(objs, "relationship")
        if r.relationship_type == "resolves-to"
    ]
    assert len(resolves) == 1
    # observed-data ties the agent /128 to the looked-up qname.
    obs = _by_type(objs, "observed-data")[0]
    assert len(obs.object_refs) == 2


def test_dns_refused_emits_indicator_and_sighting():
    row = _row(
        ts=1784002495999,
        kind="dns",
        qname="blocked.example",
        qtype="A",
        rcode="REFUSED",
        decision="refused",
        source="graph",
    )
    objs = convert_log_row(row, AGENTS)
    indicators = _by_type(objs, "indicator")
    sightings = _by_type(objs, "sighting")
    assert len(indicators) == 1
    assert indicators[0].pattern == "[domain-name:value = 'blocked.example']"
    assert indicators[0].pattern_type == "stix"
    assert len(sightings) == 1
    assert sightings[0].sighting_of_ref == indicators[0].id
    # where_sighted_refs (the Whisper author Identity) is REQUIRED - without it
    # the OpenCTI worker silently drops the sighting.
    assert sightings[0].where_sighted_refs == [WHISPER_AUTHOR.id]
    # Sighting is also tied to the ObservedData carrying the agent /128 + qname.
    obs = _by_type(objs, "observed-data")[0]
    assert sightings[0].observed_data_refs == [obs.id]


def test_conn_emits_network_traffic_communicates_with_and_sighting():
    row = _row(
        ts=1784002496119,
        kind="conn",
        peer="rdap.whisper.online:443",
        bytes_up=1715,
        bytes_down=4086,
        duration_ms=185,
        reason="closed",
        client_src="145.224.65.0/24",
        packets_up=3,
        packets_down=4,
    )
    objs = convert_log_row(row, AGENTS)
    nt = _by_type(objs, "network-traffic")[0]
    ipv6 = _by_type(objs, "ipv6-addr")[0]
    dst = _by_type(objs, "domain-name")  # peer host is a domain
    assert nt.src_ref == ipv6.id
    assert nt.dst_port == 443
    assert nt.src_byte_count == 1715
    assert nt.dst_byte_count == 4086
    assert nt.src_packets == 3
    assert nt.dst_packets == 4
    assert nt.protocols == ["tcp"]
    # dst domain SCO present (agent fqdn + peer host = two domain-names).
    assert len(dst) == 2
    comm = [
        r
        for r in _by_type(objs, "relationship")
        if r.relationship_type == "communicates-with"
    ]
    assert len(comm) == 1
    # The egress Sighting is of the agent Infrastructure and MUST carry
    # where_sighted_refs (the Whisper author Identity) or the worker drops it.
    sightings = _by_type(objs, "sighting")
    assert len(sightings) == 1
    infra = _by_type(objs, "infrastructure")[0]
    assert sightings[0].sighting_of_ref == infra.id
    assert sightings[0].where_sighted_refs == [WHISPER_AUTHOR.id]


def test_conn_open_is_suppressed():
    row = _row(ts=1784002496100, kind="conn", peer="x.example:443", reason="open")
    objs = convert_log_row(row, AGENTS)
    # Only the anchor - no network-traffic / sighting for the open half.
    assert not _by_type(objs, "network-traffic")
    assert not _by_type(objs, "sighting")


def test_conn_ipv6_peer_parsed():
    row = _row(
        ts=1784002496200,
        kind="conn",
        peer="[2606:4700:4700::1111]:853",
        bytes_up=10,
        bytes_down=20,
        reason="closed",
    )
    objs = convert_log_row(row, AGENTS)
    nt = _by_type(objs, "network-traffic")[0]
    assert nt.dst_port == 853
    # dst is an ipv6-addr (distinct from the agent /128).
    ipv6s = {o.value for o in _by_type(objs, "ipv6-addr")}
    assert "2606:4700:4700::1111" in ipv6s


def test_deterministic_ids_are_idempotent():
    row = _row(
        ts=1784002496119, kind="conn", peer="rdap.whisper.online:443",
        bytes_up=1, bytes_down=2, reason="closed",
    )  # fmt: skip
    ids1 = [o.id for o in convert_log_row(row, AGENTS)]
    ids2 = [o.id for o in convert_log_row(row, AGENTS)]
    assert ids1 == ids2


def test_author_identity_is_referenced_by_events():
    row = _row(ts=1784002483385, kind="alloc")
    objs = convert_log_row(row, AGENTS)
    obs = _by_type(objs, "observed-data")[0]
    assert obs.created_by_ref == WHISPER_AUTHOR.id


def test_unknown_kind_yields_anchor_only():
    row = _row(ts=1784002483385, kind="mystery")
    objs = convert_log_row(row, AGENTS)
    assert _by_type(objs, "infrastructure")
    assert not _by_type(objs, "observed-data")


def test_row_without_timestamp_is_skipped():
    row = _row(ts=None, kind="alloc")
    assert convert_log_row(row, AGENTS) == []


def test_full_batch_builds_valid_bundle():
    rows = [
        _row(ts=1784002483385, kind="alloc"),
        _row(ts=1784002495932, kind="dns", qname="rdap.whisper.online",
             decision="allow", answer="2001:19f0:5000:15f6::1"),
        _row(ts=1784002495999, kind="dns", qname="blocked.example",
             decision="refused"),
        _row(ts=1784002496119, kind="conn", peer="rdap.whisper.online:443",
             bytes_up=1, bytes_down=2, reason="closed"),
    ]  # fmt: skip
    objs = []
    for r in rows:
        objs.extend(convert_log_row(r, AGENTS))
    bundle = stix2.Bundle(objects=[WHISPER_AUTHOR] + objs, allow_custom=True)
    assert len(bundle.objects) > len(rows)
