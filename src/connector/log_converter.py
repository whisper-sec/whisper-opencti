"""Map Whisper agent-activity log rows to STIX 2.1 objects.

One normalized log row (a dict-per-row from ``AgentsClient.fetch_logs``, joined
against ``AgentsClient.list_agents`` for the agent's routable ``/128`` + fqdn)
becomes a small set of STIX objects. The conventions match
``converter_to_stix.py`` exactly so both connectors dedup against each other and
across re-runs:

- **SCOs** (``ipv6-addr``, ``domain-name``, ``ipv4-addr``, ``network-traffic``)
  use the stix2 library's built-in value-deterministic IDs - never an explicit
  ``id=`` - and carry authorship via the ``x_opencti_created_by_ref`` custom
  property.
- **SDOs / relationships / sightings** (``Infrastructure``, ``Indicator``,
  ``observed-data``, ``consists-of`` / ``resolves-to`` / ``communicates-with``
  relationships, ``Sighting``) use ``pycti.*.generate_id`` at the literal ``id=``
  kwarg position, and carry ``created_by_ref``.

The author is the *same* ``WHISPER_AUTHOR`` Identity the enrichment path emits,
imported directly so there is exactly one Whisper author in OpenCTI.

Per-record mapping:

- **alloc** → agent anchor (Infrastructure + ipv6-addr + domain-name +
  ``consists-of`` rels) + an ``observed-data`` for the identity coming into
  existence.
- **dns / allow** → the agent anchor + the looked-up ``domain-name`` + the
  resolved ip SCO + a ``resolves-to`` rel + an ``observed-data`` recording the
  agent's lookup.
- **dns / refused** → an ``Indicator`` (``[domain-name:value = '<qname>']``) +
  an ``observed-data`` + a ``Sighting`` of that Indicator at the agent
  Infrastructure - "agent X tried a policy-blocked domain".
- **conn** (egress, ``closed``) → a ``network-traffic`` (agent ipv6 → dst) +
  a ``communicates-with`` rel + a ``Sighting`` of the agent's egress. The
  paired ``open`` event is skipped (the ``closed`` event carries the totals).
"""

import logging
from datetime import UTC, datetime
from typing import Any

import pycti
import stix2
import validators

# Reuse the *exact* author Identity + id the enrichment converter emits, so
# OpenCTI has a single "Whisper" organization authoring both connectors' data.
from src.connector.converter_to_stix import WHISPER_AUTHOR

logger = logging.getLogger(__name__)

_AUTHOR_ID = WHISPER_AUTHOR.id
_SCO_AUTHOR = {"x_opencti_created_by_ref": _AUTHOR_ID}


def _normalize_fqdn(value: Any) -> str:
    """Normalize a DNS name from ``op:list`` / ``op:logs`` for a ``DomainName``.

    ``op:list`` fqdns (and any fully-qualified name from ``op:logs``) arrive
    with a trailing root dot (``a1b2c3.botboss.app.``). stix2 builds the
    ``DomainName`` verbatim, but the OpenCTI worker rejects the dotted value
    (``Observable is not correctly formatted``), cascading a missing-reference
    error on the ``consists-of`` rel. Strip the single trailing dot so the SCO
    is well-formed. Returns ``""`` for empty/None input.
    """
    text = str(value or "").strip()
    if text.endswith("."):
        text = text[:-1]
    return text


def _ts_to_datetime(value: Any) -> datetime | None:
    """Convert an epoch-millisecond ``ts`` to an aware UTC datetime."""
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (ValueError, OSError, OverflowError):
        return None


def _dst_sco(host: str) -> Any | None:
    """Build the destination SCO for a ``conn`` peer host.

    ``host`` may be a domain name, an IPv4, or an IPv6 (Postel: accept all).
    Returns the matching stix2 SCO, or ``None`` if the host is empty/unusable.
    """
    host = _normalize_fqdn(host)
    if not host:
        return None
    if validators.ipv4(host):
        return stix2.IPv4Address(value=host, custom_properties=_SCO_AUTHOR)
    if validators.ipv6(host):
        return stix2.IPv6Address(value=host, custom_properties=_SCO_AUTHOR)
    if validators.domain(host):
        return stix2.DomainName(value=host, custom_properties=_SCO_AUTHOR)
    return None


def _resolved_sco(answer: str) -> Any | None:
    """Build the resolved-address SCO for a ``dns`` answer (v4 or v6)."""
    answer = (answer or "").strip()
    if not answer:
        return None
    if validators.ipv4(answer):
        return stix2.IPv4Address(value=answer, custom_properties=_SCO_AUTHOR)
    if validators.ipv6(answer):
        return stix2.IPv6Address(value=answer, custom_properties=_SCO_AUTHOR)
    return None


def _split_peer(peer: str) -> tuple[str, int | None]:
    """Split a ``conn`` peer ``host:port`` into ``(host, port)``.

    Handles bracketed IPv6 (``[2001:db8::1]:443``) and bare ``host:port``.
    Returns ``(host, None)`` when no parseable port is present.
    """
    peer = (peer or "").strip()
    if not peer:
        return "", None
    if peer.startswith("["):
        # [ipv6]:port
        close = peer.find("]")
        if close != -1:
            host = peer[1:close]
            rest = peer[close + 1 :]
            port = None
            if rest.startswith(":") and rest[1:].isdigit():
                port = int(rest[1:])
            return host, port
        return peer, None
    host, sep, tail = peer.rpartition(":")
    if sep and tail.isdigit() and host and ":" not in host:
        return host, int(tail)
    # No port, or a bare IPv6 with colons and no port.
    return peer, None


def build_agent_anchor(
    agent_id: str, meta: dict[str, Any] | None
) -> tuple[list[Any], dict[str, str | None]]:
    """Build the durable per-agent anchor objects + a ref map.

    Returns ``(objects, refs)`` where ``refs`` carries the STIX ids downstream
    events reference: ``infra`` (always), ``ipv6`` and ``domain`` (when the
    ``op:list`` join supplied a ``/128`` / fqdn). Missing identity fields are
    tolerated - a revoked/absent agent still yields an Infrastructure keyed on
    its id.
    """
    meta = meta or {}
    address = meta.get("address")
    fqdn = _normalize_fqdn(meta.get("fqdn"))
    label = meta.get("label")
    name = label or fqdn or f"agent-{agent_id}"

    objects: list[Any] = []
    refs: dict[str, str | None] = {"infra": None, "ipv6": None, "domain": None}

    infra = stix2.Infrastructure(
        id=pycti.Infrastructure.generate_id(name=name),
        name=name,
        infrastructure_types=["hosting"],
        created_by_ref=_AUTHOR_ID,
    )
    objects.append(infra)
    refs["infra"] = infra.id

    ipv6 = None
    if address:
        ipv6 = stix2.IPv6Address(value=address, custom_properties=_SCO_AUTHOR)
        objects.append(ipv6)
        refs["ipv6"] = ipv6.id
        objects.append(
            stix2.Relationship(
                id=pycti.StixCoreRelationship.generate_id(
                    "consists-of", infra.id, ipv6.id
                ),
                relationship_type="consists-of",
                source_ref=infra.id,
                target_ref=ipv6.id,
                created_by_ref=_AUTHOR_ID,
            )
        )

    if fqdn:
        domain = stix2.DomainName(value=fqdn, custom_properties=_SCO_AUTHOR)
        objects.append(domain)
        refs["domain"] = domain.id
        objects.append(
            stix2.Relationship(
                id=pycti.StixCoreRelationship.generate_id(
                    "consists-of", infra.id, domain.id
                ),
                relationship_type="consists-of",
                source_ref=infra.id,
                target_ref=domain.id,
                created_by_ref=_AUTHOR_ID,
            )
        )

    return objects, refs


def _convert_alloc(row: dict, refs: dict, when: datetime) -> list[Any]:
    """alloc → observed-data over the agent's ipv6 (+ domain) SCOs."""
    object_refs = [r for r in (refs.get("ipv6"), refs.get("domain")) if r]
    if not object_refs:
        return []
    return [
        stix2.ObservedData(
            id=pycti.ObservedData.generate_id(object_refs),
            first_observed=when,
            last_observed=when,
            number_observed=1,
            object_refs=object_refs,
            created_by_ref=_AUTHOR_ID,
        )
    ]


def _convert_dns(row: dict, refs: dict, when: datetime) -> list[Any]:
    """dns → resolves-to + observed-data (allow) or Indicator + Sighting (refused)."""
    qname = _normalize_fqdn(row.get("qname"))
    if not qname or not validators.domain(qname):
        return []
    decision = (row.get("decision") or "").strip().lower()
    objects: list[Any] = []

    qname_sco = stix2.DomainName(value=qname, custom_properties=_SCO_AUTHOR)
    objects.append(qname_sco)

    if decision == "refused":
        pattern = f"[domain-name:value = '{qname}']"
        indicator = stix2.Indicator(
            id=pycti.Indicator.generate_id(pattern),
            name=f"Whisper policy-refused DNS: {qname}",
            description=(
                "A Whisper-governed agent attempted a DNS lookup that policy "
                "refused. Registration/observation is not itself a verdict - "
                "review before acting."
            ),
            pattern=pattern,
            pattern_type="stix",
            valid_from=when,
            created_by_ref=_AUTHOR_ID,
        )
        objects.append(indicator)
        # The Sighting MUST carry ``where_sighted_refs`` (an Identity/Location):
        # the OpenCTI worker silently drops any sighting whose where-sighted set
        # is empty, so the Whisper author Identity anchors it. We additionally
        # tie it to a concrete ObservedData carrying the agent's /128 + the
        # refused qname - the canonical link from a Sighting to the observation
        # that triggered it.
        obs_id: str | None = None
        obs_refs = [r for r in (refs.get("ipv6"), qname_sco.id) if r]
        if obs_refs:
            obs_id = pycti.ObservedData.generate_id(obs_refs)
            objects.append(
                stix2.ObservedData(
                    id=obs_id,
                    first_observed=when,
                    last_observed=when,
                    number_observed=1,
                    object_refs=obs_refs,
                    created_by_ref=_AUTHOR_ID,
                )
            )
        sighting_kwargs: dict[str, Any] = {
            "id": pycti.StixSightingRelationship.generate_id(
                indicator.id, [_AUTHOR_ID], when, when
            ),
            "sighting_of_ref": indicator.id,
            "where_sighted_refs": [_AUTHOR_ID],
            "first_seen": when,
            "last_seen": when,
            "count": 1,
            "created_by_ref": _AUTHOR_ID,
        }
        if obs_id:
            sighting_kwargs["observed_data_refs"] = [obs_id]
        objects.append(stix2.Sighting(**sighting_kwargs))
        return objects

    # decision == allow (or unspecified): record the resolution + lookup.
    resolved = _resolved_sco(row.get("answer") or "")
    if resolved is not None:
        objects.append(resolved)
        objects.append(
            stix2.Relationship(
                id=pycti.StixCoreRelationship.generate_id(
                    "resolves-to", qname_sco.id, resolved.id
                ),
                relationship_type="resolves-to",
                source_ref=qname_sco.id,
                target_ref=resolved.id,
                created_by_ref=_AUTHOR_ID,
            )
        )
    obs_refs = [r for r in (refs.get("ipv6"), qname_sco.id) if r]
    if obs_refs:
        objects.append(
            stix2.ObservedData(
                id=pycti.ObservedData.generate_id(obs_refs),
                first_observed=when,
                last_observed=when,
                number_observed=1,
                object_refs=obs_refs,
                created_by_ref=_AUTHOR_ID,
            )
        )
    return objects


def _convert_conn(row: dict, refs: dict, when: datetime) -> list[Any]:
    """conn (egress, closed) → network-traffic + communicates-with + Sighting."""
    # Only the lifecycle-terminal "closed" event carries the byte/packet
    # totals; skip the paired "open" to avoid double-counting.
    if (row.get("reason") or "").strip().lower() == "open":
        return []
    if not refs.get("ipv6"):
        return []
    host, port = _split_peer(row.get("peer") or "")
    dst = _dst_sco(host)
    if dst is None:
        return []

    objects: list[Any] = [dst]
    nt_kwargs: dict[str, Any] = {
        "protocols": ["tcp"],
        "src_ref": refs["ipv6"],
        "dst_ref": dst.id,
        "start": when,
        "custom_properties": _SCO_AUTHOR,
    }
    if port is not None:
        nt_kwargs["dst_port"] = port
    for src_field, row_field in (
        ("src_byte_count", "bytes_up"),
        ("dst_byte_count", "bytes_down"),
        ("src_packets", "packets_up"),
        ("dst_packets", "packets_down"),
    ):
        val = row.get(row_field)
        if isinstance(val, int) and val >= 0:
            nt_kwargs[src_field] = val
    objects.append(stix2.NetworkTraffic(**nt_kwargs))

    if refs.get("infra"):
        objects.append(
            stix2.Relationship(
                id=pycti.StixCoreRelationship.generate_id(
                    "communicates-with", refs["infra"], dst.id
                ),
                relationship_type="communicates-with",
                source_ref=refs["infra"],
                target_ref=dst.id,
                created_by_ref=_AUTHOR_ID,
            )
        )
        # where_sighted_refs (the Whisper author Identity) is required - the
        # OpenCTI worker drops any sighting whose where-sighted set is empty.
        objects.append(
            stix2.Sighting(
                id=pycti.StixSightingRelationship.generate_id(
                    refs["infra"], [_AUTHOR_ID], when, when
                ),
                sighting_of_ref=refs["infra"],
                where_sighted_refs=[_AUTHOR_ID],
                first_seen=when,
                last_seen=when,
                count=1,
                created_by_ref=_AUTHOR_ID,
            )
        )
    return objects


def convert_log_row(
    row: dict[str, Any], agents: dict[str, dict[str, Any]] | None = None
) -> list[Any]:
    """Convert one normalized log row into STIX objects (anchor + event).

    ``agents`` is the ``op:list`` join map (``{agent_id: {address, fqdn, ...}}``).
    The agent anchor is emitted for every row - deterministic IDs coalesce the
    duplicates in OpenCTI. Unknown ``kind`` values yield just the anchor (still
    useful) rather than an error, staying liberal in what we accept.
    """
    agent_id = row.get("agent")
    if not agent_id:
        return []
    when = _ts_to_datetime(row.get("ts"))
    if when is None:
        return []

    meta = (agents or {}).get(str(agent_id))
    objects, refs = build_agent_anchor(str(agent_id), meta)

    kind = (row.get("kind") or "").strip().lower()
    if kind == "alloc":
        objects.extend(_convert_alloc(row, refs, when))
    elif kind == "dns":
        objects.extend(_convert_dns(row, refs, when))
    elif kind == "conn":
        objects.extend(_convert_conn(row, refs, when))
    # else: unknown kind → anchor only.

    return objects
