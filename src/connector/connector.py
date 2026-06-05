import re
from datetime import UTC, datetime
from pathlib import Path

import stix2
import yaml
from pycti import OpenCTIConnectorHelper, get_config_variable

from src.connector.exceptions import StixMappingError, WhisperClientError
from src.connector.queries import (
    DEFAULT_LIMIT,
    LINKS_TO_CAP,
    THREAT_FLAG_FIELDS,
    get_links_to_queries,
    get_network_context_query,
    get_query_for_entity_type,
    get_threat_context_query,
)
from src.connector.result_parser import collect_dropped_hostnames, parse_cypher_result
from src.connector.stix_mapper import build_bundle, build_note
from src.connector.whisper_client import WhisperClient

_ASN_NAME_RE = re.compile(r"^AS(\d+)$", re.IGNORECASE)


def _append_prefix_block(lines: list[str], announcer: dict, indent: str) -> None:
    """Append the per-announcer prefix/BGP/threat lines to ``lines``.

    Extracted so single-AS and MOAS branches share the same formatting.
    """
    if announcer.get("prefix"):
        lines.append(f"{indent}Announced prefix: {announcer['prefix']}")
    flag_parts: list[str] = []
    if announcer.get("anycast"):
        flag_parts.append("anycast")
    if announcer.get("moas"):
        flag_parts.append("MOAS")
    if announcer.get("withdrawn"):
        flag_parts.append("withdrawn")
    if flag_parts:
        lines.append(f"{indent}BGP flags: {', '.join(flag_parts)}")
    score = announcer.get("score")
    level = announcer.get("level")
    if level and level != "NONE":
        if isinstance(score, int | float):
            lines.append(f"{indent}ANNOUNCED_PREFIX threat: {level} (score {score:g})")
        else:
            lines.append(f"{indent}ANNOUNCED_PREFIX threat: {level}")


class WhisperConnector:
    """OpenCTI internal-enrichment connector for the Whisper graph.

    For each enrichment request, resolves the observable, runs the matching
    Cypher template against Whisper, translates the result into a STIX 2.1
    bundle, and sends it to OpenCTI for ingestion.
    """

    def __init__(
        self,
        helper: OpenCTIConnectorHelper | None = None,
        client: WhisperClient | None = None,
    ) -> None:
        # Avoid loading config.yml when both deps are injected (tests).
        config: dict = {} if (helper is not None and client is not None) else self._load_config()
        self.helper = helper if helper is not None else OpenCTIConnectorHelper(config)
        if client is not None:
            self.client = client
        else:
            api_url = get_config_variable("WHISPER_API_URL", ["whisper", "api_url"], config)
            api_key = get_config_variable("WHISPER_API_KEY", ["whisper", "api_key"], config)
            if not api_url or not api_key:
                raise ValueError("WHISPER_API_URL and WHISPER_API_KEY must be configured")
            self.client = WhisperClient(api_url=api_url, api_key=api_key)

    @staticmethod
    def _load_config() -> dict:
        config_file_path = Path(__file__).resolve().parent.parent.parent / "config.yml"
        if config_file_path.is_file():
            with open(config_file_path) as fh:
                return yaml.safe_load(fh) or {}
        return {}

    @staticmethod
    def _seed_stix_id(entity_type: str, entity_value: str, observable: dict) -> str | None:
        """Derive the deterministic STIX SCO id for the seed observable.

        Mirrors what `stix_mapper`'s node mappers produce. Used by `build_note`
        callers when they need to attach a Note to the seed without having
        the SCO object in hand.
        """
        try:
            if entity_type == "IPv4-Addr":
                return stix2.IPv4Address(value=entity_value).id
            if entity_type == "IPv6-Addr":
                return stix2.IPv6Address(value=entity_value).id
            if entity_type == "Domain-Name":
                return stix2.DomainName(value=entity_value).id
            if entity_type == "Autonomous-System":
                number = observable.get("number")
                if number is not None:
                    return stix2.AutonomousSystem(number=int(number)).id
        except Exception:  # noqa: BLE001 — defensive; never fail the enrichment over this
            return None
        return None

    def _process_message(self, data: dict) -> str:
        entity_id = data.get("entity_id")
        if not entity_id:
            return "missing entity_id in enrichment request"

        observable = self.helper.api.stix_cyber_observable.read(id=entity_id)
        if observable is None:
            return f"entity {entity_id!r} not found as observable"

        return self._enrich_observable(observable)

    def _collect_links_to(
        self,
        entity_type: str,
        entity_value: str,
        observable: dict,
    ) -> tuple[list[dict], list[dict], list[stix2.Note]]:
        """For Domain-Name seeds, run the directed `LINKS_TO` queries and
        return (extra_nodes, extra_edges, cap_overflow_notes).

        Outbound edges are tagged ``description="LINKS_TO outbound"``.
        Inbound edges have their source/target swapped (since the parser
        column-position default puts the seed on the source side, but the
        inbound semantic is neighbour→seed) and tagged ``"LINKS_TO inbound"``.

        If Whisper has more `LINKS_TO` than the cap in either direction,
        a STIX Note is emitted attached to the seed reporting the overflow.
        """
        queries = get_links_to_queries(entity_type, entity_value, cap=LINKS_TO_CAP)
        if queries is None:
            return [], [], []

        extra_nodes: list[dict] = []
        extra_edges: list[dict] = []
        notes: list[stix2.Note] = []

        for direction in ("outbound", "inbound"):
            result = self.client.execute_cypher(queries[direction])
            dir_nodes, dir_edges = parse_cypher_result(result)
            for edge in dir_edges:
                if direction == "inbound":
                    # Swap source/target so the relationship correctly reads
                    # neighbour → seed instead of seed → neighbour.
                    edge["source_id"], edge["target_id"] = edge["target_id"], edge["source_id"]
                edge["properties"] = {"description": f"LINKS_TO {direction}"}
            extra_nodes.extend(dir_nodes)
            extra_edges.extend(dir_edges)

        # Count overflow → Note attached to the seed.
        seed_stix_id = self._seed_stix_id(entity_type, entity_value, observable)
        if seed_stix_id:
            overflow_messages: list[str] = []
            for direction in ("outbound", "inbound"):
                count_result = self.client.execute_cypher(queries[f"count_{direction}"])
                count = (
                    count_result.rows[0].get("c")
                    if count_result.rows and isinstance(count_result.rows[0], dict)
                    else 0
                )
                if isinstance(count, int) and count > LINKS_TO_CAP:
                    overflow_messages.append(
                        f"Whisper found {count} {direction} LINKS_TO neighbours; "
                        f"showing first {LINKS_TO_CAP}."
                    )
            if overflow_messages:
                notes.append(
                    build_note(
                        seed_stix_id=seed_stix_id,
                        content="\n".join(overflow_messages),
                        abstract="LINKS_TO neighbour overflow",
                    )
                )

        return extra_nodes, extra_edges, notes

    @staticmethod
    def _epoch_ms_to_iso(value: object) -> str | None:
        """Format a Whisper millisecond-epoch timestamp as ISO 8601 UTC.

        Returns ``None`` if the value isn't a positive integer/float — keeps
        the formatter robust against the LISTED_IN edges that carry null
        firstSeen/lastSeen for some feeds.
        """
        if not isinstance(value, int | float) or value <= 0:
            return None
        try:
            return datetime.fromtimestamp(value / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, OSError, OverflowError):
            return None

    @staticmethod
    def _format_threat_content(first_row: dict, feeds: list[dict]) -> str:
        """Render the threat-intel Note content from one parsed result.

        ``first_row`` carries the seed-level threat fields (threatScore,
        threatLevel, the 13 boolean flags, threatFirstSeen, threatLastSeen).
        ``feeds`` is the de-duplicated list of FEED_SOURCE listings.
        """
        lines: list[str] = []
        score = first_row.get("threatScore")
        level = first_row.get("threatLevel")
        if score is not None or (level and level != "NONE"):
            level_part = level or "UNKNOWN"
            if isinstance(score, int | float):
                lines.append(f"Threat assessment: {level_part} (score {score:g})")
            else:
                lines.append(f"Threat assessment: {level_part}")

        first_seen = WhisperConnector._epoch_ms_to_iso(first_row.get("threatFirstSeen"))
        last_seen = WhisperConnector._epoch_ms_to_iso(first_row.get("threatLastSeen"))
        if first_seen or last_seen:
            lines.append(f"First seen: {first_seen or '?'}   Last seen: {last_seen or '?'}")

        true_flags = [flag for flag in THREAT_FLAG_FIELDS if first_row.get(flag)]
        if true_flags:
            lines.append("Flags: " + ", ".join(true_flags))

        if feeds:
            lines.append(f"Listed in {len(feeds)} source(s):")
            for feed in feeds:
                feed_line = f"  - {feed['name']}"
                seen = []
                fs = WhisperConnector._epoch_ms_to_iso(feed.get("firstSeen"))
                ls = WhisperConnector._epoch_ms_to_iso(feed.get("lastSeen"))
                if fs:
                    seen.append(f"first {fs}")
                if ls:
                    seen.append(f"last {ls}")
                if seen:
                    feed_line += " (" + ", ".join(seen) + ")"
                lines.append(feed_line)

        return "\n".join(lines)

    @staticmethod
    def _format_dropped_hostnames_content(dropped: list[dict]) -> str:
        """Render the Note content listing HOSTNAME records the parser
        dropped for failing the RFC 1035 check.

        Dedupes by name across rows (the helper dedupes per-row but the
        same name can appear in multiple rows via different edges) and
        keeps the first edge type seen. Stable ordering by first
        occurrence so the Note content is deterministic and the UUIDv5
        on `build_note` idempotently dedupes in OpenCTI.
        """
        seen: set[str] = set()
        unique: list[dict] = []
        for entry in dropped:
            name = entry.get("name", "")
            if not name or name in seen:
                continue
            seen.add(name)
            unique.append(entry)
        lines = [
            "Whisper returned the following DNS record names that don't conform "
            "to RFC 1035 and were not included as STIX domain-name observables:",
            "",
        ]
        for entry in unique:
            edge = entry.get("edge_type") or "(unknown edge)"
            lines.append(f"  - {entry['name']}  (Whisper edge: {edge})")
        return "\n".join(lines)

    def _collect_threat_context(
        self,
        entity_type: str,
        entity_value: str,
        observable: dict,
    ) -> list[stix2.Note]:
        """Return a list with one ``stix2.Note`` if Whisper has threat-feed
        evidence for the seed, otherwise an empty list.

        Skips when:
        - the entity type doesn't carry threat properties (ASN today),
        - Whisper has no record of the seed at all,
        - the seed has no score, no notable level, no true flags, and no
          feed listings (i.e. a Note would convey nothing).

        Best-effort: caller wraps in a try/except so a failure here can't
        sink the main enrichment.
        """
        query = get_threat_context_query(entity_type, entity_value)
        if query is None:
            return []

        result = self.client.execute_cypher(query)
        if not result.rows:
            return []

        first_row = result.rows[0]
        # Each row repeats the seed-level fields and adds one feed listing
        # (or a single all-null row when OPTIONAL MATCH found no feeds).
        # Dedup feeds by name so a noisy graph that double-lists the seed
        # in one source doesn't produce duplicate Note lines.
        feeds_by_name: dict[str, dict] = {}
        for row in result.rows:
            name = row.get("feedName")
            if not name or name in feeds_by_name:
                continue
            feeds_by_name[name] = {
                "name": name,
                "firstSeen": row.get("feedFirstSeen"),
                "lastSeen": row.get("feedLastSeen"),
                "weight": row.get("feedWeight"),
            }
        feeds = list(feeds_by_name.values())

        score = first_row.get("threatScore")
        level = first_row.get("threatLevel")
        has_score = isinstance(score, int | float) and score > 0
        has_level = bool(level) and level != "NONE"
        has_flags = any(first_row.get(flag) for flag in THREAT_FLAG_FIELDS)
        if not (has_score or has_level or has_flags or feeds):
            return []

        seed_stix_id = self._seed_stix_id(entity_type, entity_value, observable)
        if seed_stix_id is None:
            return []

        content = self._format_threat_content(first_row, feeds)
        if not content:
            return []

        return [
            build_note(
                seed_stix_id=seed_stix_id,
                content=content,
                abstract="Whisper threat intelligence",
            )
        ]

    @staticmethod
    def _format_network_content(
        announcers: list[dict],
        static_prefixes: set[str],
    ) -> str:
        """Render the network-context Note content from collected announcers."""
        lines: list[str] = []
        if len(announcers) == 1:
            a = announcers[0]
            label = f"AS{a['asn_number']}"
            if a.get("description"):
                label += f" ({a['description']})"
            lines.append(f"Announced by: {label}")
            _append_prefix_block(lines, a, indent="")
        elif announcers:
            lines.append(f"Announced by {len(announcers)} ASN(s) — multi-origin (MOAS):")
            for a in announcers:
                label = f"AS{a['asn_number']}"
                if a.get("description"):
                    label += f" ({a['description']})"
                lines.append(f"  - {label}")
                _append_prefix_block(lines, a, indent="    ")

        unannounced_static = {
            p for p in static_prefixes if not any(p == a.get("prefix") for a in announcers)
        }
        if unannounced_static:
            lines.append("Static allocation: " + ", ".join(sorted(unannounced_static)))

        return "\n".join(lines)

    def _collect_network_context(
        self,
        entity_type: str,
        entity_value: str,
        observable: dict,
    ) -> tuple[list[dict], list[dict], list[stix2.Note]]:
        """For IPv4/IPv6 seeds, derive announcing-ASN context.

        Returns (extra_nodes, extra_edges, notes). The Autonomous-System SCO
        is synthesized from the ASN node returned by the supplementary
        query (Whisper nodeId preserved for idempotent dedup), and an
        IP→AS `related-to` edge with ``description="ANNOUNCED_BY"`` ties
        them together. Prefix-level details (announced prefix, BGP flags,
        ANNOUNCED_PREFIX threat score) collapse into a single Note attached
        to the seed — there's no clean STIX SCO for a CIDR network.

        Best-effort: caller wraps in try/except so a transport failure
        here can't kill the main bundle or the other supplementary Notes.
        """
        query = get_network_context_query(entity_type, entity_value)
        if query is None:
            return [], [], []

        result = self.client.execute_cypher(query)
        if not result.rows:
            return [], [], []

        seed_stix_id = self._seed_stix_id(entity_type, entity_value, observable)
        # Aggregate by ASN Whisper nodeId so MOAS rows for the same ASN
        # collapse into a single announcer entry — keeps the Note clean
        # when Whisper has multiple ANNOUNCED_PREFIXes that share an AS.
        announcers_by_id: dict[str, dict] = {}
        static_prefixes: set[str] = set()
        seed_whisper_id: str | None = None
        seed_whisper_name: str | None = None
        seed_whisper_label: str | None = None

        for row in result.rows:
            seed_cell = row.get("seed")
            asn_cell = row.get("asn")
            if isinstance(seed_cell, dict) and seed_whisper_id is None:
                seed_whisper_id = seed_cell.get("nodeId")
                seed_whisper_name = seed_cell.get("name")
                seed_whisper_label = seed_cell.get("label")
            if not isinstance(asn_cell, dict):
                continue
            asn_id = asn_cell.get("nodeId")
            asn_name_str = asn_cell.get("name")  # "AS15169"
            if not asn_id or not asn_name_str:
                continue
            match = _ASN_NAME_RE.match(str(asn_name_str))
            if not match:
                continue
            asn_number = int(match.group(1))
            announcer = announcers_by_id.setdefault(
                asn_id,
                {
                    "asn_id": asn_id,
                    "asn_name": asn_name_str,
                    "asn_number": asn_number,
                    "description": row.get("asnDescription"),
                    "prefix": row.get("announcedPrefix"),
                    "score": row.get("apThreatScore"),
                    "level": row.get("apThreatLevel"),
                    "anycast": row.get("isAnycast"),
                    "moas": row.get("isMoas"),
                    "withdrawn": row.get("isWithdrawn"),
                },
            )
            if not announcer.get("description") and row.get("asnDescription"):
                announcer["description"] = row.get("asnDescription")
            if row.get("prefix"):
                static_prefixes.add(row.get("prefix"))

        if not announcers_by_id:
            return [], [], []

        # Synthesize the seed IP node so any edge we emit has a matching
        # entry in `nodes` — covers the case where the main query returned
        # no rows for this IP (e.g. an IP with only ANNOUNCED_PREFIX/PREFIX
        # neighbours, all of which the parser drops).
        extra_nodes: list[dict] = []
        if seed_whisper_id and seed_whisper_name:
            stix_type = "ipv4-addr" if seed_whisper_label == "IPV4" else "ipv6-addr"
            extra_nodes.append(
                {
                    "id": seed_whisper_id,
                    "type": stix_type,
                    "properties": {"value": seed_whisper_name},
                }
            )

        extra_edges: list[dict] = []
        for announcer in announcers_by_id.values():
            # The AS SCO's `name` is the human-readable label (ASN_NAME via
            # HAS_NAME) when Whisper has one, otherwise the AS<number> form.
            asn_props: dict = {"number": announcer["asn_number"]}
            if announcer.get("description"):
                asn_props["name"] = announcer["description"]
            extra_nodes.append(
                {
                    "id": announcer["asn_id"],
                    "type": "autonomous-system",
                    "properties": asn_props,
                }
            )
            if seed_whisper_id:
                extra_edges.append(
                    {
                        "source_id": seed_whisper_id,
                        "target_id": announcer["asn_id"],
                        "type": "related-to",
                        "properties": {"description": "ANNOUNCED_BY"},
                    }
                )

        notes: list[stix2.Note] = []
        content = self._format_network_content(list(announcers_by_id.values()), static_prefixes)
        if seed_stix_id and content:
            notes.append(
                build_note(
                    seed_stix_id=seed_stix_id,
                    content=content,
                    abstract="Whisper network context",
                )
            )

        return extra_nodes, extra_edges, notes

    def _enrich_observable(self, observable: dict) -> str:
        entity_type = observable.get("entity_type")
        entity_value = observable.get("observable_value") or observable.get("value")

        # Autonomous-System: OpenCTI exposes the human-readable AS name as
        # `observable_value` (e.g. "Google LLC") and the AS number as a
        # separate `number` field. Whisper's ASN nodes are keyed by the
        # canonical `AS<number>` string, so we have to convert here. Issue #48.
        if entity_type == "Autonomous-System":
            asn_number = observable.get("number")
            if asn_number is not None:
                entity_value = f"AS{asn_number}"

        if not entity_value:
            return f"observable {observable.get('id')!r} has no value to enrich"

        query = get_query_for_entity_type(entity_type, value=entity_value, limit=DEFAULT_LIMIT)
        if query is None:
            return f"entity type {entity_type!r} not supported by Whisper enrichment"

        self.helper.connector_logger.info(
            "Enriching via Whisper",
            {
                "entity_id": observable.get("id"),
                "entity_type": entity_type,
                "value": entity_value,
            },
        )

        try:
            result = self.client.execute_cypher(query)
        except WhisperClientError as exc:
            self.helper.connector_logger.error(
                "Whisper query failed",
                {"entity_id": observable.get("id"), "error": str(exc)},
            )
            raise

        nodes, edges = parse_cypher_result(result)

        # Capture HOSTNAME records the parser silently dropped for failing
        # the RFC 1035 check (issue #51, builds on #47). Surfaced as a Note
        # attached to the seed so the analyst sees what Whisper had even
        # though we can't ship it as a domain-name SCO.
        dropped_hostnames = collect_dropped_hostnames(result)

        # Supplementary LINKS_TO enrichment for Domain-Name seeds.
        try:
            extra_nodes, extra_edges, extra_notes = self._collect_links_to(
                entity_type, entity_value, observable
            )
        except WhisperClientError as exc:
            # LINKS_TO is a nice-to-have — don't fail the whole enrichment
            # if just the supplementary queries fall over.
            self.helper.connector_logger.error(
                "Whisper LINKS_TO supplementary query failed (continuing)",
                {"entity_id": observable.get("id"), "error": str(exc)},
            )
            extra_nodes, extra_edges, extra_notes = [], [], []

        # Supplementary threat-context Note for HOSTNAME/IPV4/IPV6 seeds.
        # Independently best-effort: a failure here must not block the main
        # bundle or the LINKS_TO Notes from shipping.
        try:
            threat_notes = self._collect_threat_context(entity_type, entity_value, observable)
        except WhisperClientError as exc:
            self.helper.connector_logger.error(
                "Whisper threat-context query failed (continuing)",
                {"entity_id": observable.get("id"), "error": str(exc)},
            )
            threat_notes = []
        extra_notes.extend(threat_notes)

        # Dropped-HOSTNAME Note (issue #51). Independent of any
        # supplementary query: built from the main result we already have.
        if dropped_hostnames:
            seed_stix_id = self._seed_stix_id(entity_type, entity_value, observable)
            if seed_stix_id:
                content = self._format_dropped_hostnames_content(dropped_hostnames)
                extra_notes.append(
                    build_note(
                        seed_stix_id=seed_stix_id,
                        content=content,
                        abstract="Whisper dropped non-RFC-1035 DNS records",
                    )
                )

        # Supplementary network context for IPv4/IPv6 seeds — emits the
        # announcing-ASN as a real Autonomous-System SCO + related-to edge
        # plus a Note for the prefix/BGP/ANNOUNCED_PREFIX threat detail.
        # Same best-effort posture as the other supplementary passes.
        try:
            net_nodes, net_edges, net_notes = self._collect_network_context(
                entity_type, entity_value, observable
            )
        except WhisperClientError as exc:
            self.helper.connector_logger.error(
                "Whisper network-context query failed (continuing)",
                {"entity_id": observable.get("id"), "error": str(exc)},
            )
            net_nodes, net_edges, net_notes = [], [], []
        extra_nodes.extend(net_nodes)
        extra_edges.extend(net_edges)
        extra_notes.extend(net_notes)

        if extra_nodes or extra_edges:
            seen_node_ids = {n["id"] for n in nodes}
            for new_node in extra_nodes:
                if new_node["id"] not in seen_node_ids:
                    nodes.append(new_node)
                    seen_node_ids.add(new_node["id"])
            edges.extend(extra_edges)

        if not nodes and not extra_notes:
            self.helper.connector_logger.info(
                "No Whisper data for entity",
                {"entity_id": observable.get("id"), "value": entity_value},
            )
            return f"No Whisper data for {entity_value}"

        # If every neighbour was dropped by the parser (unmappable labels like
        # PREFIX, CITY, COUNTRY) we end up with just the seed and no edges.
        # Sending a bundle that only re-asserts the seed observable adds no
        # new information to OpenCTI and produces a misleading "Enriched"
        # status — UNLESS we also have supplementary Notes (LINKS_TO overflow
        # or Whisper threat intelligence) to attach, in which case the
        # bundle carries genuinely new analyst-visible context.
        if not edges and not extra_notes:
            self.helper.connector_logger.info(
                "No mappable Whisper relationships for entity",
                {
                    "entity_id": observable.get("id"),
                    "value": entity_value,
                    "nodes_returned": len(nodes),
                },
            )
            return f"No mappable Whisper relationships for {entity_value}"

        try:
            bundle = build_bundle(nodes, edges, extra_objects=extra_notes)
        except StixMappingError as exc:
            self.helper.connector_logger.error(
                "STIX mapping failed",
                {"entity_id": observable.get("id"), "error": str(exc)},
            )
            raise

        objects = getattr(bundle, "objects", None) or []
        if not objects:
            return f"No mappable Whisper data for {entity_value}"

        self.helper.send_stix2_bundle(bundle.serialize())
        elapsed = result.statistics.get("executionTimeMs", "?")
        self.helper.connector_logger.info(
            "Sent STIX bundle",
            {
                "entity_id": observable.get("id"),
                "object_count": len(objects),
                "execution_time_ms": elapsed,
            },
        )
        return f"Enriched {entity_value} with {len(objects)} STIX objects (query: {elapsed}ms)"

    def start(self) -> None:
        self.helper.listen(message_callback=self._process_message)
