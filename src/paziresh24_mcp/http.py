"""Shared async HTTP client for the Paziresh24 APIs.

Every tool goes through `fetch`, which caps concurrency, sends a browser User-Agent, retries once on
connection errors, timeouts and gateway 5xx (some apigw routes hang and answer a 504 page after 20-27 s),
caps response bodies at 2 MB, refuses redirects off paziresh24.com, and turns HTTP failures into
`ToolError` messages the model can act on.
"""

from __future__ import annotations

import asyncio
import copy
import os
import time
from typing import Any
from urllib.parse import urlsplit

import httpx
from mcp.server.mcpserver.exceptions import ToolError

GW = "https://apigw.paziresh24.com"
DRPROFILE = "https://drprofile.paziresh24.com"
RAVI = "https://ravi-apis.paziresh24.com/ravi/v1"
SAMAN = "https://saman.paziresh24.com"
BIKO = "https://biko.paziresh24.com"
WWW = "https://www.paziresh24.com"
CDN = "https://cdn.paziresh24.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "accept-timezone": "Asia/Tehran",
}

MAX_CONCURRENCY = 4
TIMEOUT = httpx.Timeout(20, connect=10)  # healthy calls take 0.4-3 s (cold search up to 10 s); hung routes 20-27 s
RETRY_STATUS = {502, 503, 504}
RETRY_DELAY = 0.5
MAX_BODY = 2_000_000
CACHE_TTL = 6 * 3600  # a long-lived stdio process must not serve a stale FAQ or city list forever

_transport: httpx.AsyncBaseTransport | None = None
_client: httpx.AsyncClient | None = None
_limit: asyncio.Semaphore | None = None
_cache: dict[tuple, tuple[float, Any]] = {}


class ApiError(ToolError):
    """A failed upstream call; `status` is the HTTP status (None for network errors)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Swap the transport (tests use httpx.MockTransport). Drops the current client and the cache."""
    global _transport, _client, _limit
    _transport, _client, _limit = transport, None, None
    _cache.clear()


async def _same_site(request: httpx.Request) -> None:
    """Runs for every hop, so a redirect cannot leave paziresh24.com."""
    host = request.url.host
    if host != "paziresh24.com" and not host.endswith(".paziresh24.com"):
        raise httpx.UnsupportedProtocol(f"redirect to {host} refused")


def _get_client() -> tuple[httpx.AsyncClient, asyncio.Semaphore]:
    global _client, _limit
    if _client is None:
        # Direct calls are fastest; ignore system proxy settings unless the user opts in
        # with PAZIRESH24_MCP_PROXY.
        _client = httpx.AsyncClient(
            transport=_transport,
            headers=HEADERS,
            timeout=TIMEOUT,
            follow_redirects=True,
            event_hooks={"request": [_same_site]},
            trust_env=False,
            proxy=os.environ.get("PAZIRESH24_MCP_PROXY") or None,
        )
        _limit = asyncio.Semaphore(MAX_CONCURRENCY)
    assert _limit is not None
    return _client, _limit


async def fetch(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    method: str = "GET",
    data: dict[str, Any] | None = None,
    text: bool = False,
) -> Any:
    """Call an endpoint and return the parsed JSON body, or the text with text=True.

    `data` is sent as an urlencoded form (the slot and legacy endpoints refuse JSON bodies).
    """
    client, limit = _get_client()
    host = urlsplit(url).hostname
    r = None
    # One retry (the POSTs here are read-only): a reset connection, a hung route or a gateway 5xx often works on the second try.
    for attempt in range(2):
        try:
            async with limit:
                r = await _send(client, method, url, params, data)
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            if attempt == 0:
                await asyncio.sleep(RETRY_DELAY)
                continue
            if isinstance(e, httpx.TimeoutException):
                raise ApiError(f"{host} did not answer in time. Try again in a moment.") from e
            raise ApiError(
                f"Could not reach {host} ({type(e).__name__}). Check the internet connection, or set PAZIRESH24_MCP_PROXY."
            ) from e
        except httpx.RequestError as e:
            raise ApiError(
                f"Could not reach {host} ({type(e).__name__}). Check the internet connection, or set PAZIRESH24_MCP_PROXY."
            ) from e
        if r.status_code in RETRY_STATUS and attempt == 0:
            await asyncio.sleep(RETRY_DELAY)
            continue
        break
    assert r is not None

    if r.status_code >= 400:
        raise ApiError(_status_message(r, host), r.status_code)
    if text:
        return r.text
    if r.status_code == 204 or not r.content:
        return None
    try:
        return r.json()
    except ValueError as e:
        raise ApiError(f"{host} returned a non-JSON response (HTTP {r.status_code}).", r.status_code) from e


async def _send(
    client: httpx.AsyncClient, method: str, url: str, params: dict[str, Any] | None, data: dict[str, Any] | None
) -> httpx.Response:
    """One request with the body read in chunks and refused past MAX_BODY (the hung-route 504 page is small)."""
    async with client.stream(method, url, params=params, data=data) as r:
        try:
            declared = int(r.headers.get("content-length") or 0)
        except ValueError:
            declared = 0  # malformed header: the chunked check below still caps the body
        if declared > MAX_BODY:
            raise ApiError(f"{urlsplit(url).hostname} sent a response over {MAX_BODY // 1_000_000} MB.", r.status_code)
        body = bytearray()
        async for chunk in r.aiter_bytes():
            body += chunk
            if len(body) > MAX_BODY:
                raise ApiError(
                    f"{urlsplit(url).hostname} sent a response over {MAX_BODY // 1_000_000} MB.", r.status_code
                )
    return httpx.Response(
        r.status_code, content=bytes(body), headers={"content-type": r.headers.get("content-type", "")}
    )


async def fetch_cached(url: str, **kwargs: Any) -> Any:
    """`fetch` for reference data that rarely changes (cities, specialty menu, FAQ, cancellation policy): kept for
    CACHE_TTL, keyed on every argument; callers get a copy, so they may mutate it."""
    key = (
        url,
        repr(sorted((kwargs.get("params") or {}).items())),
        repr(sorted((kwargs.get("data") or {}).items())),
        kwargs.get("method", "GET"),
        kwargs.get("text", False),
    )
    hit = _cache.get(key)
    if hit is None or hit[0] < time.monotonic():
        hit = _cache[key] = (time.monotonic() + CACHE_TTL, await fetch(url, **kwargs))
    return copy.deepcopy(hit[1])


def legacy(body: Any) -> Any:
    """The PHP endpoints (www /api/*, the apigw slot reads) answer HTTP 200 with {status, message, result};
    status 1 is success, anything else (32 no free slot, 700 online booking off, 3 bad terminal) is an error."""
    if not isinstance(body, dict) or body.get("status") != 1:
        message = body.get("message") if isinstance(body, dict) else None
        status = body.get("status") if isinstance(body, dict) else None
        raise ApiError(f"Paziresh24 refused the request (status {status}): {message or str(body)[:200]}")
    return body.get("result")


def _status_message(r: httpx.Response, host: str | None) -> str:
    code = r.status_code
    if code == 429:
        return f"{host} is rate limiting requests (HTTP 429). Wait a minute before retrying."
    detail = error_text(r)
    if code == 404:
        return f"Not found on {host} (HTTP 404): {detail}. Check the slug or id."
    if code >= 500:
        return f"{host} had a server error (HTTP {code}): {detail}. Try again later."
    return f"{host} rejected the request (HTTP {code}): {detail}"


def error_text(r: httpx.Response) -> str:
    """The most useful message from the error shapes of the Paziresh24 hosts."""
    try:
        body = r.json()
    except ValueError:
        return "HTML error page" if "<html" in r.text[:500].lower() else r.text[:200]
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:300]
        if isinstance(err, str):
            return err[:300]
        if body.get("message"):
            return str(body["message"])[:300]
    return str(body)[:300]
