"""HTTP client for the Finnvnoi API usage endpoints."""

from __future__ import annotations

import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from . import DEFAULT_BASE_URL, __version__

USER_AGENT = f"finnvnoi-api-check/{__version__}"


class ApiError(Exception):
    """An error from the server or from the network layer."""

    def __init__(self, message: str, status: Optional[int] = None, code: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code

    @property
    def is_auth(self) -> bool:
        return self.status in (401, 403)

    @property
    def is_rate_limited(self) -> bool:
        return self.status == 429

    def __str__(self) -> str:
        if self.status:
            return f"HTTP {self.status}: {self.message}"
        return self.message


class Client:
    def __init__(
        self,
        api_key: str = "",
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 15.0,
        verify_tls: bool = True,
    ):
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        context = ssl.create_default_context()
        if not verify_tls:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        self._opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))
        self.last_latency_ms: Optional[float] = None

    # -- transport -----------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        api_key: Optional[str] = None,
    ) -> Any:
        url = self.base_url + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)

        data = None
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        key = api_key if api_key is not None else self.api_key
        if key:
            headers["Authorization"] = f"Bearer {key}"
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        started = time.monotonic()
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            self.last_latency_ms = (time.monotonic() - started) * 1000
            raise _http_error(exc) from None
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, socket.timeout):
                raise ApiError(f"Timed out after {self.timeout:.0f}s", code="timeout") from None
            raise ApiError(f"Cannot reach server: {reason}", code="network") from None
        except socket.timeout:
            raise ApiError(f"Timed out after {self.timeout:.0f}s", code="timeout") from None

        self.last_latency_ms = (time.monotonic() - started) * 1000
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError("Response was not valid JSON", code="bad_json") from None

    # -- endpoints -----------------------------------------------------
    def usage(self, api_key: Optional[str] = None) -> Dict[str, Any]:
        """Usage for the key sent in the Authorization header."""
        return self._request("GET", "/v1/usage", api_key=api_key)

    def activity(
        self,
        window: str = "lt",
        include_logs: bool = True,
        page: int = 1,
        after_id: Optional[int] = None,
        api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Totals plus the request log. The log is lifetime-only; window filters only the totals."""
        return self._request(
            "GET",
            "/v1/usage/activity",
            params={
                "window": window,
                "include_logs": "true" if include_logs else "false",
                "page": page,
                "after_id": after_id,
            },
            api_key=api_key,
        )

    def bulk(self, keys: List[str], api_key: Optional[str] = None) -> Dict[str, Any]:
        """Usage for several keys in one call."""
        return self._request("POST", "/v1/usage/bulk", body={"keys": list(keys)}, api_key=api_key)

    def ping(self) -> bool:
        try:
            self._request("GET", "/health", api_key="")
            return True
        except ApiError:
            return False


def _http_error(exc: urllib.error.HTTPError) -> ApiError:
    status = exc.code
    message = exc.reason or "Unknown error"
    code = None
    try:
        payload = json.loads(exc.read().decode("utf-8"))
        detail = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(detail, dict):
            message = detail.get("message") or message
            code = detail.get("code") or detail.get("type")
        elif isinstance(payload, dict) and payload.get("detail"):
            message = str(payload["detail"])
    except Exception:
        pass
    if status == 401:
        message = message or "Invalid API key"
    elif status == 429:
        message = message or "Rate limited"
    return ApiError(str(message), status=status, code=code)
