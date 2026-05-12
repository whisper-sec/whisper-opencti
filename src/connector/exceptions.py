class WhisperClientError(Exception):
    """Base exception for the Whisper API client."""


class WhisperAuthError(WhisperClientError):
    """Whisper API rejected the API key (HTTP 401/403)."""


class WhisperTransportError(WhisperClientError):
    """Request failed after retries (timeout, 5xx, connection error)."""


class WhisperQueryError(WhisperClientError):
    """Whisper API returned a query-level error (HTTP 4xx other than auth)."""
