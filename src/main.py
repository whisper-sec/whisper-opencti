"""Entry point for the Whisper OpenCTI connector.

Loads the optional ``config.yml`` once, then constructs:

1. ``WhisperSettings`` - Pydantic-validated config for the Whisper side
   (api_url / api_key / max_tlp), with env vars overriding the YAML
   ``whisper:`` block.
2. ``OpenCTIConnectorHelper`` with ``playbook_compatible=True`` - required
   by the v7 internal-enrichment callback contract (issue #65). The
   helper reads its own ``OPENCTI__`` / ``CONNECTOR__`` / ``RABBITMQ__``
   keys out of the same YAML dict.

Then hands both to ``WhisperConnector.run()``. Wrapped in
``try/traceback/sys.exit(1)`` so Docker reports the container as
``Exited (1)`` on any startup failure rather than silently looping.
"""

import sys
import traceback

from pycti import OpenCTIConnectorHelper

from src.connector.connector import WhisperConnector
from src.connector.settings import WhisperSettings, load_yaml_config


def main() -> None:
    yaml_config = load_yaml_config()
    settings = WhisperSettings.from_environment(yaml_config)
    helper = OpenCTIConnectorHelper(yaml_config, playbook_compatible=True)
    WhisperConnector(helper=helper, config=settings).run()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
