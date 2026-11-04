"""Client for the Google Apps Script Web App that talks to Gmail, Calendar and Drive.

Request:  POST APPS_SCRIPT_URL  {"secret", "action", "params"}
Response: {"ok": true, "data": ...}  or  {"ok": false, "error": {"code", "message"}}
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

import httpx

from ..errors import BridgeError

log = logging.getLogger(__name__)

NOT_JSON = ("The Google bridge did not return JSON. Deploy the Web App with Who has access: Anyone "
            "and use the URL ending in /exec.")

_FRIENDLY = {
    "unauthorized": "Invalid shared secret. APPS_SCRIPT_SHARED_SECRET in .env and the BRIDGE_SHARED_SECRET "
                    "script property must match exactly (no spaces or quotes).",
    "not_configured": "The Apps Script bridge is not configured: set the BRIDGE_SHARED_SECRET script property "
                      "and run checkSetup.",
    "permission_denied": "Google reported an error: permission denied. Re-authorize the Apps Script project "
                         "(run any function in the editor), then deploy a new version.",
    "quota": "Google reported an error: a daily usage limit was reached. Try again later.",
}


class Bridge(Protocol):
    def call(self, action: str, params: dict[str, Any] | None = None) -> Any: ...


class AppsScriptBridge:
    def __init__(self, url: str, secret: str, timeout: float = 30.0,
                 transport: httpx.BaseTransport | None = None):
        self._url = url
        self._secret = secret
        self._timeout = timeout
        # Apps Script answers a POST with a 302 to script.googleusercontent.com; follow it.
        self._client = httpx.Client(timeout=timeout, follow_redirects=True, transport=transport)

    def call(self, action: str, params: dict[str, Any] | None = None) -> Any:
        payload = {"secret": self._secret, "action": action, "params": params or {}}
        log.info("bridge call: %s", action)
        try:
            resp = self._client.post(self._url, json=payload)
        except httpx.TimeoutException as exc:
            raise BridgeError(f"Google did not answer within {self._timeout:g} seconds.", bridge_code="timeout") from exc
        except httpx.HTTPError as exc:
            raise BridgeError(f"Could not reach the Google bridge ({exc.__class__.__name__}). "
                              "Check your internet connection and APPS_SCRIPT_URL.", bridge_code="unreachable") from exc

        if resp.status_code in (401, 403):
            raise BridgeError("Google refused the request (HTTP %d). Deploy the Web App with Who has access: "
                              "Anyone." % resp.status_code, bridge_code="http_error")
        if resp.status_code == 404:
            raise BridgeError("APPS_SCRIPT_URL was not found (HTTP 404). Copy the Web App URL ending in /exec.",
                              bridge_code="http_error")
        if resp.status_code >= 400:
            raise BridgeError(f"The Google bridge returned HTTP {resp.status_code}.", bridge_code="http_error")
        try:
            body = resp.json()
        except ValueError as exc:
            raise BridgeError(NOT_JSON, bridge_code="bad_response") from exc
        if not isinstance(body, dict) or not isinstance(body.get("ok"), bool):
            raise BridgeError("The Google bridge returned an unexpected response.", bridge_code="bad_response")
        if not body["ok"]:
            err = body.get("error") if isinstance(body.get("error"), dict) else {}
            code = str(err.get("code") or "internal")
            message = str(err.get("message") or "Unknown error.")
            log.info("bridge error: %s %s", action, code)
            raise BridgeError(_FRIENDLY.get(code, f"Google reported an error: {message}"), bridge_code=code)
        return body.get("data")
