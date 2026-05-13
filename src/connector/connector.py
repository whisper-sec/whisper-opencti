from pathlib import Path

import yaml
from pycti import OpenCTIConnectorHelper, get_config_variable

from src.connector.exceptions import StixMappingError, WhisperClientError
from src.connector.queries import DEFAULT_LIMIT, get_query_for_entity_type
from src.connector.result_parser import parse_cypher_result
from src.connector.stix_mapper import build_bundle
from src.connector.whisper_client import WhisperClient


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

    def _process_message(self, data: dict) -> str:
        entity_id = data.get("entity_id")
        if not entity_id:
            return "missing entity_id in enrichment request"

        observable = self.helper.api.stix_cyber_observable.read(id=entity_id)
        if observable is None:
            return f"entity {entity_id!r} not found as observable"

        return self._enrich_observable(observable)

    def _enrich_observable(self, observable: dict) -> str:
        entity_type = observable.get("entity_type")
        entity_value = observable.get("observable_value") or observable.get("value")
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
        if not nodes:
            self.helper.connector_logger.info(
                "No Whisper data for entity",
                {"entity_id": observable.get("id"), "value": entity_value},
            )
            return f"No Whisper data for {entity_value}"

        try:
            bundle = build_bundle(nodes, edges)
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
