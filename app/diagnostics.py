from __future__ import annotations

import socket
import ssl

import httpx


def classify_request_error(error: BaseException) -> str:
    """Return a safe, stable category without including exception messages."""
    if isinstance(error, httpx.TimeoutException):
        return "timeout"

    causes: list[BaseException] = []
    pending = [error]
    seen: set[int] = set()
    while pending:
        cause = pending.pop()
        if id(cause) in seen:
            continue
        seen.add(id(cause))
        causes.append(cause)
        if cause.__cause__ is not None:
            pending.append(cause.__cause__)
        if cause.__context__ is not None:
            pending.append(cause.__context__)

    if any(isinstance(cause, ssl.SSLError) for cause in causes):
        return "tls_failure"
    if any(isinstance(cause, socket.gaierror) for cause in causes):
        return "dns_failure"
    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        return "rate_limited" if status_code == 429 else "http_error"
    if isinstance(error, httpx.RemoteProtocolError):
        return "malformed_response"
    if isinstance(error, httpx.InvalidURL | httpx.UnsupportedProtocol):
        return "configuration_error"
    if isinstance(error, httpx.ConnectError):
        return "connection_failure"
    if isinstance(error, httpx.TransportError):
        return "network_failure"
    return "request_error"


def request_error_label(error: BaseException) -> str:
    category = classify_request_error(error)
    if isinstance(error, httpx.HTTPStatusError):
        return f"{category}_{error.response.status_code}"
    return category
