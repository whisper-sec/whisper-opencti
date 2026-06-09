"""Config loader for the Whisper connector.

Thin wrapper around ``pycti.get_config_variable`` that pulls every supported
env var / config.yml key into a single ``ConfigConnector`` object. Pattern
borrowed from the upstream ``shodan-internetdb`` connector — see
https://github.com/OpenCTI-Platform/connectors/blob/master/internal-enrichment/shodan-internetdb/src/shodan_internetdb/config.py

This is a deliberate shim for the v7 callback-shape migration (issue #65).
A follow-up PR replaces it with a Pydantic ``BaseSettings`` model (#65's
PR description, "out of scope"), matching what upstream's ``virustotal``
/ ``dnstwist`` connectors use today.
"""

from pathlib import Path
from typing import Any

import yaml
from pycti import get_config_variable

__all__ = ["ConfigConnector"]

# TLP markings accepted by ``OpenCTIConnectorHelper.check_max_tlp``. The
# connector refuses to enrich any observable whose marking exceeds this.
_TLP_MARKING_OPTIONS = [
    "TLP:WHITE",
    "TLP:CLEAR",
    "TLP:GREEN",
    "TLP:AMBER",
    "TLP:AMBER+STRICT",
    "TLP:RED",
]

# Default TLP ceiling — strict enough that customers must opt in to enrich
# AMBER+STRICT or RED observables.
_DEFAULT_MAX_TLP = "TLP:AMBER+STRICT"


class ConfigConnector:
    """Pull connector-side config from environment / config.yml.

    Attributes are populated via ``pycti.get_config_variable`` so the same
    set of values is honoured whether the operator supplies them via env
    vars (production) or via a mounted ``config.yml`` (local dev).
    """

    def __init__(self) -> None:
        self.load = self._load_config()
        self._initialize_configurations()
        self._validate()

    @staticmethod
    def _load_config() -> dict[str, Any]:
        config_file_path = Path(__file__).resolve().parent.parent.parent / "config.yml"
        if config_file_path.is_file():
            with open(config_file_path) as fh:
                return yaml.safe_load(fh) or {}
        return {}

    def _initialize_configurations(self) -> None:
        self.whisper_api_url: str = get_config_variable(
            "WHISPER_API_URL",
            ["whisper", "api_url"],
            self.load,
        )
        self.whisper_api_key: str = get_config_variable(
            "WHISPER_API_KEY",
            ["whisper", "api_key"],
            self.load,
        )
        # TLP ceiling for enrichment. The connector raises ``WhisperTlpError``
        # on any observable whose marking is stricter than this. Default
        # AMBER+STRICT keeps customer intel out of the Whisper API by default;
        # raising to TLP:RED is an explicit opt-in.
        self.whisper_max_tlp: str = (
            get_config_variable(
                "WHISPER_MAX_TLP",
                ["whisper", "max_tlp"],
                self.load,
                default=_DEFAULT_MAX_TLP,
            )
            or _DEFAULT_MAX_TLP
        )

    def _validate(self) -> None:
        if not self.whisper_api_url or not self.whisper_api_key:
            raise ValueError("WHISPER_API_URL and WHISPER_API_KEY must be configured")
        if self.whisper_max_tlp not in _TLP_MARKING_OPTIONS:
            raise ValueError(
                "WHISPER_MAX_TLP must be one of "
                f"{', '.join(_TLP_MARKING_OPTIONS)}; got {self.whisper_max_tlp!r}"
            )
