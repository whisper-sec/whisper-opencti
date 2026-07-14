"""Entry point for the Whisper agent-activity log source (EXTERNAL_IMPORT).

Sibling to ``src/main.py``: it shares the same image, ``WhisperClient``, and
STIX conventions, but runs as its own OpenCTI connector with its own
``CONNECTOR_ID`` and ``CONNECTOR_TYPE=EXTERNAL_IMPORT``. The enrichment
entrypoint (``src/main.py``) is untouched.

Constructs:

1. ``LogConnectorSettings`` - the connectors-sdk ``BaseConnectorSettings`` for
   the external-import connector, reusing the same ``whisper:`` block (one
   tenant key, one auth path).
2. ``OpenCTIConnectorHelper`` via ``_build_helper`` - the same cold-boot retry
   loop ``main.py`` uses, so the connector quietly waits while OpenCTI's API
   finishes booting instead of crash-looping.

Then hands both to ``WhisperLogSource(helper, config).run()``. Wrapped in
``try/traceback/sys.exit(1)`` so Docker reports ``Exited (1)`` on any startup
failure rather than silently looping.
"""

import logging
import os
import sys
import time
import traceback

from pycti import OpenCTIConnectorHelper

from src.connector.log_source import WhisperLogSource
from src.connector.settings import LogConnectorSettings

logger = logging.getLogger("whisper.main_logs")

# Startup retry budget for the OpenCTI connection (~10 minutes by default:
# 120 × 5s), generous enough for a cold OpenCTI/Elasticsearch boot. Mirrors
# the enrichment entrypoint's budget; overridable via env.
_STARTUP_MAX_RETRIES = int(os.environ.get("OPENCTI_STARTUP_MAX_RETRIES", "120"))
_STARTUP_RETRY_DELAY = int(os.environ.get("OPENCTI_STARTUP_RETRY_DELAY", "5"))


def _build_helper(
    yaml_config: dict,
    max_retries: int | None = None,
    retry_delay: int | None = None,
) -> OpenCTIConnectorHelper:
    """Construct the helper, retrying while the OpenCTI API is still booting.

    pycti raises ``ValueError("OpenCTI API is not reachable...")`` when the
    platform isn't up yet - expected on stack startup - so we retry with a
    fixed delay (clean one-line warnings, no traceback) until OpenCTI answers
    or the budget is exhausted. Configuration errors carry a different message
    and are re-raised immediately.
    """
    max_retries = _STARTUP_MAX_RETRIES if max_retries is None else max_retries
    retry_delay = _STARTUP_RETRY_DELAY if retry_delay is None else retry_delay

    api_logger = logging.getLogger("api")
    prior_level = api_logger.level
    api_logger.setLevel(logging.CRITICAL)
    try:
        for attempt in range(1, max_retries + 1):
            try:
                return OpenCTIConnectorHelper(yaml_config)
            except ValueError as exc:
                transient = "not reachable" in str(exc).lower()
                if not transient or attempt >= max_retries:
                    raise
                logger.warning(
                    "OpenCTI API not reachable yet (attempt %d/%d) - "
                    "retrying in %ds. Detail: %s",
                    attempt,
                    max_retries,
                    retry_delay,
                    exc,
                )
                time.sleep(retry_delay)
    finally:
        api_logger.setLevel(prior_level)
    raise RuntimeError("exhausted OpenCTI startup retries")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = LogConnectorSettings()
    helper = _build_helper(settings.to_helper_config())
    WhisperLogSource(helper=helper, config=settings).run()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
