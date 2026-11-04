"""Client for the Google Apps Script bridge.

Every request carries the shared secret (NFR-02) and names one allow-listed
operation (NFR-03). The allow-list is enforced here *and* in the bridge.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

import httpx

from app.errors import BridgeError

log = logging.getLogger(__name__)

# Must match ALLOWED_OPERATIONS in apps_script/Code.gs.
ALLOWED_OPERATIONS = frozenset(
    {
        "get_profile",
        # Gmail
        "get_latest_emails",
        "search_emails",
        "count_emails",
        "read_email",
        "read_thread",
        "list_drafts",
        "get_draft",
        "list_gmail_labels",
        "create_draft",
        "update_draft",
        "delete_draft",
        "send_draft",
        "send_email",
        "update_email_labels",
        "trash_email",
        "restore_email",
        # Calendar
        "list_calendar_events",
        "get_calendar_event",
        "check_availability",
        "create_calendar_event",
        "update_calendar_event",
        "delete_calendar_event",
        # Drive
        "list_drive_files",
        "get_drive_file",
        "save_attachments_to_drive",
        "trash_drive_file",
    }
)


class BridgeClient(Protocol):
    async def call(self, operation: str, args: dict[str, Any] | None = None) -> Any: ...


class HttpBridgeClient:
    def __init__(
        self,
        url: str,
        secret: str,
        timeout_seconds: float = 90,
        transport: httpx.AsyncBaseTransport | None = None,  # injectable for tests
    ):
        self._transport = transport
        self._url = url
        self._secret = secret
        self._timeout = httpx.Timeout(timeout_seconds, connect=15)

    async def call(self, operation: str, args: dict[str, Any] | None = None) -> Any:
        if operation not in ALLOWED_OPERATIONS:
            raise BridgeError(f"Operation '{operation}' is not allowed.", code="unknown_operation")
        payload = {"secret": self._secret, "operation": operation, "args": args or {}}
        log.info("bridge call: %s", operation)  # never log payload (contains secret)
        try:
            # Apps Script answers POST with a 302 to googleusercontent.com; follow it.
            async with httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=True, transport=self._transport
            ) as client:
                resp = await client.post(self._url, json=payload)
        except httpx.TimeoutException as exc:
            raise BridgeError(
                "Google didn't respond in time. The Apps Script bridge may be starting up; try again.",
                code="bridge_timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise BridgeError(
                "Couldn't reach the Google Apps Script bridge. Check APPS_SCRIPT_URL and your connection.",
                code="bridge_unreachable",
            ) from exc
        return _parse_response(resp)


def _parse_response(resp: httpx.Response) -> Any:
    if resp.status_code >= 400:
        raise BridgeError(
            f"The Apps Script bridge returned HTTP {resp.status_code}. Check the deployment URL and access settings.",
            code="bridge_http_error",
        )
    try:
        data = resp.json()
    except ValueError as exc:
        # Usually an HTML Google sign-in page: the web app isn't deployed with "Anyone" access.
        raise BridgeError(
            "The Apps Script bridge didn't return JSON. Make sure it's deployed as a web app "
            "that executes as you and is accessible to 'Anyone'.",
            code="bridge_bad_response",
        ) from exc
    if not isinstance(data, dict) or "ok" not in data:
        raise BridgeError("The Apps Script bridge returned an unexpected response.", code="bridge_bad_response")
    if not data["ok"]:
        err = data.get("error") or {}
        raise BridgeError(
            str(err.get("message") or "The Google operation failed."),
            code=str(err.get("code") or "google_error"),
        )
    return data.get("result")


class UnconfiguredBridge:
    """Used when APPS_SCRIPT_URL / APPS_SCRIPT_SECRET are missing."""

    async def call(self, operation: str, args: dict[str, Any] | None = None) -> Any:
        raise BridgeError(
            "The Google bridge isn't configured. Set APPS_SCRIPT_URL and APPS_SCRIPT_SECRET in backend/.env.",
            code="not_configured",
        )
