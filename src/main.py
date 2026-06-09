"""Entry point for the Whisper OpenCTI connector.

Builds the typed ``ConfigConnector`` from environment / config.yml,
instantiates ``OpenCTIConnectorHelper`` with ``playbook_compatible=True``
(required by the v7 internal-enrichment callback contract — see issue
#65), and hands both to ``WhisperConnector.run()``.

Traceback + non-zero exit on any startup failure so Docker reports the
container as ``Exited (1)`` instead of silently looping.
"""

import sys
import traceback

from pycti import OpenCTIConnectorHelper

from src.connector.config import ConfigConnector
from src.connector.connector import WhisperConnector


def main() -> None:
    config = ConfigConnector()
    helper = OpenCTIConnectorHelper(config.load, playbook_compatible=True)
    WhisperConnector(helper=helper, config=config).run()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
