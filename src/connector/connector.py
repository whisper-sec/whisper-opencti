from pathlib import Path

import yaml
from pycti import OpenCTIConnectorHelper, get_config_variable


class WhisperConnector:
    """OpenCTI internal-enrichment connector.

    Registers with the OpenCTI platform and listens for enrichment requests.
    The enrichment logic itself is a placeholder until follow-up tickets land.
    """

    def __init__(self) -> None:
        config_file_path = Path(__file__).resolve().parent.parent.parent / "config.yml"
        config: dict = {}
        if config_file_path.is_file():
            with open(config_file_path) as fh:
                config = yaml.safe_load(fh) or {}

        self.helper = OpenCTIConnectorHelper(config)
        self.whisper_api_url = get_config_variable(
            "WHISPER_API_URL", ["whisper", "api_url"], config
        )
        self.whisper_api_key = get_config_variable(
            "WHISPER_API_KEY", ["whisper", "api_key"], config
        )

    def _process_message(self, data: dict) -> list[str]:
        entity_id = data.get("entity_id")
        self.helper.connector_logger.info(
            "Received enrichment request (logic not yet implemented)",
            {"entity_id": entity_id},
        )
        return [
            f"Whisper connector received enrichment request for {entity_id}; "
            "enrichment logic ships in a follow-up ticket."
        ]

    def start(self) -> None:
        self.helper.listen(message_callback=self._process_message)
