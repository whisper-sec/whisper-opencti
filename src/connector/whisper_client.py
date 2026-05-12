import logging
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.connector.exceptions import (
    WhisperAuthError,
    WhisperQueryError,
    WhisperTransportError,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF = 0.5
CYPHER_PATH = "/api/query"


class WhisperClient:
    """HTTP client for the Whisper graph API.

    Executes Cypher queries with API-key authentication. Retries 5xx and
    transport errors with exponential backoff; never retries 4xx responses.
    """

    def __init__(
        self,
        api_url: str,
        api_key: str,
        timeout: float = DEFAULT_TIMEOUT,
        verify_ssl: bool = True,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_factor: float = DEFAULT_BACKOFF,
    ) -> None:
        if not api_url:
            raise ValueError("api_url is required")
        if not api_key:
            raise ValueError("api_key is required")
        self.api_url = api_url.rstrip("/")
        self._api_key = api_key
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self._session = self._build_session(max_retries, backoff_factor)

    @staticmethod
    def _build_session(max_retries: int, backoff_factor: float) -> requests.Session:
        retries = Retry(
            total=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=frozenset(["POST"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries)
        session = requests.Session()
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def execute_cypher(
        self,
        query: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a Cypher query and return the result rows.

        Result rows are a list of dicts; the shape of each row matches what
        the Whisper API returns (typically keyed by Cypher RETURN aliases).
        """
        url = f"{self.api_url}{CYPHER_PATH}"
        payload: dict[str, Any] = {"query": query, "params": params or {}}
        logger.debug("whisper request url=%s param_keys=%s", url, list(payload["params"].keys()))

        try:
            response = self._session.post(
                url,
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
        except requests.RequestException as exc:
            raise WhisperTransportError(f"transport error contacting Whisper API: {exc}") from exc

        if response.status_code in (401, 403):
            raise WhisperAuthError(
                f"Whisper API rejected the API key (HTTP {response.status_code})"
            )
        if response.status_code >= 500:
            raise WhisperTransportError(
                f"Whisper API returned HTTP {response.status_code} after retries"
            )
        if response.status_code >= 400:
            body_snippet = response.text[:500]
            raise WhisperQueryError(
                f"Whisper API query error (HTTP {response.status_code}): {body_snippet}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise WhisperQueryError(f"Whisper API returned non-JSON body: {exc}") from exc

        rows = body.get("results", body.get("data", []))
        if not isinstance(rows, list):
            raise WhisperQueryError(
                f"Whisper API returned unexpected result shape: {type(rows).__name__}"
            )
        return rows

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "WhisperClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
