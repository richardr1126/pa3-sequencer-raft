"""Shared HTTP base client.

Concrete clients live in:
- clients/sellers/client.py
- clients/buyers/client.py
"""

import time
from typing import Any

import httpx

__all__ = [
    "MarketplaceClient",
]

# Default retry configuration
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF = 0.1  # seconds


class MarketplaceClient:
    """Base HTTP client for communicating with marketplace backend servers."""

    def __init__(self, host: str, port: int, max_retries: int = DEFAULT_MAX_RETRIES):
        self.host = host
        self.port = port
        self.max_retries = max_retries
        self._client: httpx.Client | None = None

    def connect(self) -> None:
        """Initialize persistent HTTP client."""
        if self._client is not None:
            return
        self._client = httpx.Client(
            base_url=f"http://{self.host}:{self.port}",
            timeout=120.0,
        )

    def _reconnect(self) -> None:
        """Force recreation of underlying HTTP client."""
        self.close()
        self.connect()

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def __enter__(self) -> "MarketplaceClient":
        self.connect()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | list[tuple[str, Any]] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Send HTTP request and normalize response envelope.

        Args:
            method: HTTP method
            path: URL path
            payload: Optional JSON body
            params: Optional query parameters
            session_id: Optional session ID for authenticated routes

        Returns:
            The full response dict with 'ok', 'payload', 'error' fields
        """
        headers: dict[str, str] = {}
        if session_id:
            headers["X-Session-Id"] = session_id

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                if self._client is None:
                    self.connect()

                assert self._client is not None
                response = self._client.request(
                    method=method,
                    url=path,
                    json=payload,
                    params=params,
                    headers=headers if headers else None,
                )

                if response.status_code >= 500 and attempt < self.max_retries - 1:
                    time.sleep(DEFAULT_RETRY_BACKOFF * (attempt + 1))
                    continue

                return self._normalize_response(response)

            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                self.close()
                if attempt < self.max_retries - 1:
                    time.sleep(DEFAULT_RETRY_BACKOFF * (attempt + 1))
                    continue
                raise

        if last_error is not None:
            raise last_error
        raise RuntimeError("Request failed")

    @staticmethod
    def _status_code_to_error_code(status_code: int) -> str:
        if status_code == 400:
            return "INVALID_REQUEST"
        if status_code == 401:
            return "AUTH_REQUIRED"
        if status_code == 404:
            return "NOT_FOUND"
        if status_code == 503:
            return "DB_ERROR"
        if status_code >= 500:
            return "INTERNAL_ERROR"
        return "ERROR"

    def _normalize_response(self, response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json() if response.content else {}
        except ValueError as exc:
            raise httpx.DecodingError(f"Invalid JSON response: {exc}") from exc

        if response.is_success:
            return {"ok": True, "payload": body}

        code = self._status_code_to_error_code(response.status_code)
        message = f"HTTP {response.status_code}"

        if isinstance(body, dict):
            code = str(body.get("code") or code)
            message = str(body.get("message") or message)

        return {
            "ok": False,
            "error": {"code": code, "message": message},
        }
