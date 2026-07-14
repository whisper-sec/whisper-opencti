"""Thin ``whisper.agents`` control-plane wrapper on top of ``WhisperClient``.

The agent-activity log source reads a tenant's own agent logs through the
Whisper control plane, which is exposed as a single Cypher procedure::

    CALL whisper.agents({op: 'logs', args: {since: ..., limit: ..., agent: ...}})

POSTed to the *same* ``<api_url>/api/query`` endpoint, with the *same*
``X-API-Key`` header, that the enrichment connector already uses - so this
module reuses :class:`WhisperClient` verbatim for HTTP, auth, and retry, and
adds only the two things the enrichment path doesn't need:

1. **A Cypher builder** for the ``whisper.agents`` procedure. Whisper's Cypher
   engine rejects request-body parameters (see ``queries.py``), so the ``op``
   and ``args`` are inlined as a Cypher map literal, string values JSON-escaped.
2. **An op-envelope unwrap.** ``whisper.agents`` answers with a *double-nested*
   envelope: the outer ``/api/query`` body (already parsed by
   ``WhisperClient.execute_cypher`` into ``CypherResult.rows``) carries a single
   row ``{op, ok, status, result, error, retry_after}`` whose inner ``result``
   is itself a columnar ``{columns, rows}`` block holding the real events. This
   module unwraps that, raises :class:`WhisperQueryError` on ``ok=false`` /
   ``status >= 400`` / a non-null ``retry_after``, and zips the inner
   ``columns`` against each row-array into a plain dict-per-row.
"""

import json
from typing import Any

from src.connector.exceptions import WhisperQueryError
from src.connector.whisper_client import WhisperClient


def _cypher_literal(value: Any) -> str:
    """Render a Python value as a Cypher literal (no request-body params).

    Strings are JSON-escaped and double-quoted (Cypher accepts double-quoted
    strings); ints/floats/bools/None map to their Cypher spellings; nested
    dicts recurse into map literals. Mirrors the inline-only discipline the
    enrichment ``queries.py`` uses.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, dict):
        return _cypher_map(value)
    return json.dumps(str(value))


def _cypher_map(mapping: dict[str, Any]) -> str:
    """Render a dict as a Cypher map literal ``{key: value, ...}``.

    Keys are emitted as bare Cypher identifiers (the ``whisper.agents`` op
    schema only ever uses ``[a-z_]`` keys), values via ``_cypher_literal``.
    ``None`` values are omitted so an unset optional (e.g. ``agent``) simply
    doesn't appear in the map.
    """
    parts = [
        f"{key}: {_cypher_literal(val)}"
        for key, val in mapping.items()
        if val is not None
    ]
    return "{" + ", ".join(parts) + "}"


def build_agents_query(op: str, args: dict[str, Any] | None = None) -> str:
    """Build the ``CALL whisper.agents({...})`` Cypher for an op + args.

    ``op`` is required; ``args`` is optional and any ``None``-valued arg is
    dropped (so ``fetch_logs`` without an agent filter emits no ``agent`` key).
    """
    if not op:
        raise ValueError("op is required")
    call_map: dict[str, Any] = {"op": op}
    if args:
        pruned = {k: v for k, v in args.items() if v is not None}
        if pruned:
            call_map["args"] = pruned
    return f"CALL whisper.agents({_cypher_map(call_map)})"


def _rows_to_dicts(inner: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize an inner ``{columns, rows}`` result into a list of dicts.

    Liberal in what it accepts (Postel): a row may already be a dict, or a
    positional array aligned to ``columns`` - either shape is handled.
    """
    columns = inner.get("columns") or []
    rows = inner.get("rows") or []
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(row)
        elif isinstance(row, (list, tuple)) and columns:
            out.append(dict(zip(columns, row)))
    return out


class AgentsClient:
    """``whisper.agents`` op wrapper over an existing :class:`WhisperClient`.

    Owns no auth of its own - it borrows the injected client's key, endpoint,
    and retry policy. Construct with a live ``WhisperClient`` (production) or a
    stub/mock (tests).
    """

    def __init__(self, client: WhisperClient) -> None:
        self.client = client

    def call_op(self, op: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run one ``whisper.agents`` op and return its unwrapped inner result.

        Returns the inner ``{columns, rows, ...}`` dict. Raises
        :class:`WhisperQueryError` when the op reports failure - ``ok=false``,
        an HTTP-ish ``status >= 400``, or a non-null ``retry_after`` - so a
        throttled or rejected op is a clear error, never a silent empty pull.
        """
        query = build_agents_query(op, args)
        result = self.client.execute_cypher(query)
        rows = result.rows
        if not rows:
            raise WhisperQueryError(
                f"whisper.agents(op={op!r}) returned an empty envelope"
            )
        envelope = rows[0]
        if not isinstance(envelope, dict):
            raise WhisperQueryError(
                f"whisper.agents(op={op!r}) returned an unexpected envelope: "
                f"{type(envelope).__name__}"
            )

        retry_after = envelope.get("retry_after")
        if retry_after is not None:
            raise WhisperQueryError(
                f"whisper.agents(op={op!r}) is rate-limited "
                f"(retry_after={retry_after})"
            )
        ok = envelope.get("ok")
        status = envelope.get("status")
        if ok is False or (isinstance(status, int) and status >= 400):
            raise WhisperQueryError(
                f"whisper.agents(op={op!r}) failed "
                f"(ok={ok}, status={status}, error={envelope.get('error')!r})"
            )

        inner = envelope.get("result")
        if inner is None:
            return {"columns": [], "rows": []}
        if not isinstance(inner, dict):
            raise WhisperQueryError(
                f"whisper.agents(op={op!r}) inner result is not a map: "
                f"{type(inner).__name__}"
            )
        return inner

    def list_agents(self) -> dict[str, dict[str, Any]]:
        """Return ``{agent_id: {address, fqdn, label, state, created}}``.

        The log rows carry only the bare agent id (no ``/128`` or fqdn), so the
        source joins each event against this map to recover the routable
        identity. Agent ids are normalized to the bare form (no ``agent-``
        prefix) so they match what the log rows use.

        ``op:list`` requires a ``kind`` arg and answers columnar as
        ``['kind', 'item']`` with each agent's fields nested under ``item``
        (``{agent, address, fqdn, label, state, created}``). Unwrap ``item``
        before reading the fields - but stay liberal (Postel) and accept a flat
        row too, in case the backend ever inlines the fields.
        """
        inner = self.call_op("list", {"kind": "agents"})
        agents: dict[str, dict[str, Any]] = {}
        for row in _rows_to_dicts(inner):
            item = row.get("item")
            if isinstance(item, dict):
                row = item
            agent_id = row.get("agent")
            if not agent_id:
                continue
            bare = str(agent_id)
            if bare.startswith("agent-"):
                bare = bare[len("agent-") :]
            agents[bare] = {
                "agent": bare,
                "address": row.get("address"),
                "fqdn": row.get("fqdn"),
                "label": row.get("label"),
                "state": row.get("state"),
                "created": row.get("created"),
            }
        return agents

    def fetch_logs(
        self,
        since: int | str | None = None,
        limit: int = 1000,
        agent: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch one page of agent-activity log rows as dict-per-row.

        ``since`` is the watermark (epoch-ms integer preferred; the op also
        accepts RFC3339 / relative strings). ``agent`` optionally restricts to
        one agent id. Returns rows with the inner ``result.columns`` zipped
        onto each row-array (``ts``, ``kind``, ``qname`` … ``packets_down``).
        """
        args: dict[str, Any] = {"limit": int(limit)}
        if since is not None:
            args["since"] = since
        if agent:
            args["agent"] = agent
        inner = self.call_op("logs", args)
        return _rows_to_dicts(inner)

    def fetch_logs_paged(
        self,
        since: int | str | None = None,
        limit: int = 1000,
        agent: str | None = None,
        cap: int = 10000,
    ) -> list[dict[str, Any]]:
        """Page through log rows, advancing ``since`` past the newest ``ts``.

        Stops when a short page (< ``limit``) comes back, when ``cap`` total
        rows are collected, or when the cursor can't advance (guards against a
        stuck page). Only meaningful with an integer (epoch-ms) ``since``; a
        non-integer initial ``since`` fetches a single page.
        """
        limit = max(1, min(int(limit), 10000))
        collected: list[dict[str, Any]] = []
        cursor: int | str | None = since
        while True:
            page = self.fetch_logs(since=cursor, limit=limit, agent=agent)
            if not page:
                break
            collected.extend(page)
            if len(page) < limit or len(collected) >= cap:
                break
            timestamps = [r.get("ts") for r in page if isinstance(r.get("ts"), int)]
            if not timestamps:
                # Can't advance a non-timestamped page safely - avoid looping.
                break
            next_cursor = max(timestamps) + 1
            if isinstance(cursor, int) and next_cursor <= cursor:
                break
            cursor = next_cursor
        return collected[:cap]
