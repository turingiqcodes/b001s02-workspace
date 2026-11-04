"""Turn exceptions into short messages a non-technical user can act on (FR-10)."""

from __future__ import annotations

import logging

import anthropic

from .bridge import BridgeError

log = logging.getLogger(__name__)

_BRIDGE_MESSAGES = {
    "UNAUTHORIZED": "The Google bridge rejected the backend's secret. Make sure BRIDGE_TOKEN in backend/.env "
                    "matches the BRIDGE_TOKEN Script Property in Apps Script.",
    "UNREACHABLE": "Couldn't reach Google right now. Check your internet connection and APPS_SCRIPT_URL.",
    "TIMEOUT": "Google took too long to respond. Please try again.",
    "PERMISSION_DENIED": "Google denied access. Re-authorize the Apps Script project and try again.",
    "QUOTA": "Google's daily usage limit for this account was reached. Try again later.",
}


def friendly_error(exc: BaseException) -> str:
    if isinstance(exc, BridgeError):
        return _BRIDGE_MESSAGES.get(exc.code, exc.message)
    if isinstance(exc, anthropic.AuthenticationError):
        return "The Anthropic API key is missing or invalid. Set ANTHROPIC_API_KEY in backend/.env and restart the backend."
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "This Anthropic API key doesn't have access to the configured model."
    if isinstance(exc, anthropic.RateLimitError):
        return "Claude is receiving too many requests right now. Wait a moment and try again."
    if isinstance(exc, anthropic.BadRequestError):
        log.warning("Anthropic rejected the request: %s", exc)
        return "Claude couldn't process that request. Try rephrasing, or start a new conversation."
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code >= 500:
            return "Claude is temporarily unavailable. Please try again in a minute."
        return f"Claude returned an error (HTTP {exc.status_code}). Please try again."
    if isinstance(exc, anthropic.APITimeoutError):
        return "Claude took too long to respond. Please try again."
    if isinstance(exc, anthropic.APIConnectionError):
        return "Couldn't connect to Claude. Check your internet connection."
    if isinstance(exc, anthropic.AnthropicError):
        log.warning("Anthropic client error: %s", exc)
        return "Claude isn't configured. Set ANTHROPIC_API_KEY in backend/.env and restart the backend."
    if isinstance(exc, TypeError) and "authentication method" in str(exc):
        # The SDK raises TypeError when no API key / auth token / profile can be found.
        return "No Anthropic API key found. Set ANTHROPIC_API_KEY in backend/.env and restart the backend."
    log.exception("Unexpected error", exc_info=exc)
    return "Something went wrong on our side. Please try again."
