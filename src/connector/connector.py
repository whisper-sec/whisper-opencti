import logging

logger = logging.getLogger(__name__)


class WhisperConnector:
    """Placeholder connector. Real implementation lands in a follow-up ticket."""

    def start(self) -> None:
        logging.basicConfig(level=logging.INFO)
        logger.info("whisper-opencti scaffolding is up; connector logic not yet implemented.")
