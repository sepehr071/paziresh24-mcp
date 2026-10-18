"""Shared async HTTP client for the Paziresh24 APIs.

Every tool goes through `fetch`, which caps concurrency, presents Chrome's TLS/HTTP2 fingerprint and the headers the
web app sends, retries connection errors, timeouts, 429 and 5xx (some apigw routes hang and answer a 504 page after
20-27 s), caps response bodies at 2 MB, refuses redirects off paziresh24.com, and turns HTTP failures into
`ToolError` messages the model can act on.
"""

from __future__ import annotations

import asyncio
import copy
import os
import random
import re
import time
from typing import Any
from urllib.parse import urlsplit

import httpx
from httpx_curl_cffi import AsyncCurlTransport
from mcp.server.mcpserver.exceptions import ToolError

GW = "https://apigw.paziresh24.com"
DRPROFILE = "https://drprofile.paziresh24.com"
RAVI = "https://ravi-apis.paziresh24.com/ravi/v1"
SAMAN = "https://saman.paziresh24.com"
BIKO = "https://biko.paziresh24.com"
WWW = "https://www.paziresh24.com"
CDN = "https://cdn.paziresh24.com"

HEADERS = {
    # User-Agent, sec-ch-ua and TLS come from the impersonated Chrome profile (see _get_client).
    "Accept": "application/json",
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
    "accept-timezone": "Asia/Tehran",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "priority": "u=1, i",
}

MAX_CONCURRENCY = 2
JITTER = (0.15, 0.6)  # seconds before each call
MAX_RETRIES = 2
MAX_RETRY_WAIT = 10  # cap for Retry-After and backoff, seconds
TIMEOUT = httpx.Timeout(20, connect=10)  # healthy calls take 0.4-3 s (cold search up to 10 s); hung routes 20-27 s
RETRY_DELAY = 0.5
MAX_BODY = 2_000_000
CACHE_TTL = 6 * 3600  # a long-lived stdio process must not serve a stale FAQ or city list forever

_transport: httpx.AsyncBaseTransport | None = None
_client: httpx.AsyncClient | None = None
_limit: asyncio.Semaphore | None = None
_proxy_index = 0
_retired: set[asyncio.Task] = set()
_browser_lock: asyncio.Lock | None = None
_cleared: tuple[httpx.AsyncClient, float] | None = None
_cache: dict[tuple, tuple[float, Any]] = {}


class _XhrTransport(AsyncCurlTransport):
    """The Chrome profile sends navigation-only headers; a None value makes curl drop them (API calls are XHR)."""

    def _create_request_params(self, req: httpx.Request) -> dict[str, Any]:
        p = super()._create_request_params(req)
        p["headers"] = [*p["headers"], (b"upgrade-insecure-requests", None), (b"sec-fetch-user", None)]
        return p


class ApiError(ToolError):
    """A failed upstream call; `status` is the HTTP status (None for network errors)."""

    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Swap the transport (tests use httpx.MockTransport). Drops the current client and the cache."""
    global _transport, _client, _limit, _proxy_index, _browser_lock, _cleared
    _transport, _client, _limit, _proxy_index, _browser_lock, _cleared = transport, None, None, 0, None, None
    _cache.clear()


async def _same_site(request: httpx.Request) -> None:
    """Runs for every hop, so a redirect cannot leave paziresh24.com."""
    host = request.url.host
    if host != "paziresh24.com" and not host.endswith(".paziresh24.com"):
        raise httpx.UnsupportedProtocol(f"redirect to {host} refused")


def _proxies() -> list[str]:
    """PAZIRESH24_MCP_PROXY: one proxy URL, or a comma-separated list used one at a time."""
    return [p.strip() for p in os.environ.get("PAZIRESH24_MCP_PROXY", "").split(",") if p.strip()]


# Plain CDN error pages are not challenges: a bare HTML 403 is an ordinary refusal, and the hung-route 504 is not one.
_CHALLENGE = re.compile(r"just a moment|captcha|challenge|arvan|attention required|enable javascript", re.I)


def _is_challenge(r: httpx.Response) -> bool:
    """A bot-check page: 403/429/503 with an HTML body that carries a known challenge marker."""
    return (
        r.status_code in (403, 429, 503)
        and "html" in r.headers.get("content-type", "") + r.text[:20].lower()
        and bool(_CHALLENGE.search(r.text[:5000]))
    )


def _next_proxy(client: httpx.AsyncClient) -> None:
    """Sticky pool: move to the next proxy after a block or connect error; the next call rebuilds the client.

    Only the client that failed may rotate: a slower request that failed on an old client must not skip a good proxy.
    The old client is closed late because other requests may still be using it.
    """
    global _client, _limit, _proxy_index
    if len(_proxies()) < 2 or client is not _client:
        return
    _client, _limit, _proxy_index = None, None, _proxy_index + 1
    t = asyncio.create_task(_close_later(client))
    _retired.add(t)
    t.add_done_callback(_retired.discard)


async def _close_later(client: httpx.AsyncClient) -> None:
    await asyncio.sleep(30)  # longer than the 20 s request timeout
    await client.aclose()


def _get_client() -> tuple[httpx.AsyncClient, asyncio.Semaphore]:
    global _client, _limit
    if _client is None:
        # Direct calls are fastest; ignore system proxy settings unless the user opts in
        # with PAZIRESH24_MCP_PROXY. curl_cffi presents Chrome's TLS/HTTP2 fingerprint and default headers;
        # cookies and redirects stay with httpx. The proxy goes to the transport (a client-level proxy
        # would be ignored once a transport is given).
        proxies = _proxies()
        _client = httpx.AsyncClient(
            transport=_transport
            or _XhrTransport(
                impersonate="chrome",
                default_headers=True,
                proxy=proxies[_proxy_index % len(proxies)] if proxies else None,
                max_connections=2 * MAX_CONCURRENCY,
            ),
            headers=HEADERS,
            timeout=TIMEOUT,
            limits=httpx.Limits(max_connections=2 * MAX_CONCURRENCY),
            follow_redirects=True,
            event_hooks={"request": [_same_site]},
            trust_env=False,
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
    host = urlsplit(url).hostname
    for attempt in range(MAX_RETRIES + 1):
        try:
            r = await _get(url, params, method, data)
            break
        except ApiError as e:
            # 4xx (403 too) is final; the POSTs here are read-only, so every method may be retried.
            if attempt == MAX_RETRIES or (e.status is not None and e.status < 500 and e.status != 429):
                raise
            wait = e.retry_after if e.retry_after is not None else RETRY_DELAY * 2**attempt * random.uniform(1, 1.5)
            await asyncio.sleep(min(wait, MAX_RETRY_WAIT))
    if text:
        return r.text
    if r.status_code == 204 or not r.content:
        return None
    try:
        return r.json()
    except ValueError as e:
        raise ApiError(f"{host} returned a non-JSON response (HTTP {r.status_code}).", r.status_code) from e


def _page_headers(method: str, url: str) -> dict[str, str]:
    """Origin/Referer/sec-fetch-site as the web app's fetch() sends them: API hosts are same-site, www is same-origin."""
    same_origin = urlsplit(url).hostname == urlsplit(WWW).hostname
    h = {"Referer": f"{WWW}/", "sec-fetch-site": "same-origin" if same_origin else "same-site"}
    if method != "GET" or not same_origin:  # browsers omit Origin on same-origin GETs
        h["Origin"] = WWW
    return h


async def _send(
    client: httpx.AsyncClient,
    limit: asyncio.Semaphore,
    method: str,
    url: str,
    params: dict[str, Any] | None,
    data: dict[str, Any] | None,
) -> httpx.Response:
    """One request with the body read in chunks and refused past MAX_BODY (the hung-route 504 page is small)."""
    host = urlsplit(url).hostname
    try:
        async with limit:
            await asyncio.sleep(random.uniform(*JITTER))
            async with client.stream(method, url, params=params, data=data, headers=_page_headers(method, url)) as r:
                try:
                    declared = int(r.headers.get("content-length") or 0)
                except ValueError:
                    declared = 0  # malformed header: the chunked check below still caps the body
                if declared > MAX_BODY:
                    raise ApiError(f"{host} sent a response over {MAX_BODY // 1_000_000} MB.", r.status_code)
                body = bytearray()
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_BODY:
                        raise ApiError(f"{host} sent a response over {MAX_BODY // 1_000_000} MB.", r.status_code)
    except httpx.TimeoutException as e:
        raise ApiError(f"{host} did not answer in time. Try again in a moment.") from e
    except httpx.RequestError as e:
        if isinstance(e, httpx.ConnectError | httpx.ProxyError):
            _next_proxy(client)
        raise ApiError(
            f"Could not reach {host} ({type(e).__name__}). Check the internet connection, or set PAZIRESH24_MCP_PROXY.",
            400 if isinstance(e, httpx.UnsupportedProtocol) else None,  # a refused off-site redirect is final
        ) from e
    keep = ("content-type", "retry-after")
    return httpx.Response(r.status_code, content=bytes(body), headers={k: r.headers[k] for k in keep if k in r.headers})


async def _browser_cookies(proxy: str | None) -> list[dict[str, Any]]:
    """Open the home page in headless patchright, wait for the challenge to clear, return the cookies."""
    try:
        from patchright.async_api import async_playwright
    except ImportError as e:
        raise ApiError(
            "Paziresh24 served a bot-check page. Install the browser extra (`pip install paziresh24-mcp[browser]`, or "
            "`uvx --with patchright==1.63.0 paziresh24-mcp`) and run `patchright install chromium`.",
            403,
        ) from e
    u = httpx.URL(proxy) if proxy else None
    opts = u and {"server": f"{u.scheme}://{u.host}:{u.port}", "username": u.username, "password": u.password}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, proxy=opts)
        try:
            page = await browser.new_page()
            await page.goto(WWW, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_function(
                "!/just a moment|access denied|attention required|arvancloud/i.test(document.title)", timeout=30_000
            )
            return await browser.contexts[0].cookies()
        finally:
            await browser.close()


async def _clear_challenge(client: httpx.AsyncClient) -> None:
    """Rung 2: let a real browser pass the bot check once and share its cookies with the httpx jar."""
    global _browser_lock, _cleared
    _browser_lock = _browser_lock or asyncio.Lock()
    async with _browser_lock:  # one browser at a time; callers that waited reuse the cookies just fetched
        if _cleared and _cleared[0] is client and time.monotonic() - _cleared[1] < 30:
            return
        proxies = _proxies()
        try:
            cookies = await _browser_cookies(proxies[_proxy_index % len(proxies)] if proxies else None)
        except ApiError:
            raise
        except Exception as e:
            raise ApiError(f"Paziresh24 bot check was not cleared by the browser ({type(e).__name__}).", 403) from e
        for c in cookies:
            client.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
        _cleared = (client, time.monotonic())


async def _get(url: str, params: dict[str, Any] | None, method: str, data: dict[str, Any] | None) -> httpx.Response:
    """One attempt: send, clear a bot check once if needed, and raise ApiError for any HTTP failure."""
    client, limit = _get_client()
    r = await _send(client, limit, method, url, params, data)
    if _is_challenge(r):
        _next_proxy(client)
        client, limit = _get_client()
        await _clear_challenge(client)
        r = await _send(client, limit, method, url, params, data)  # one retry; a second block is final
        if _is_challenge(r):
            _next_proxy(client)
    if r.status_code >= 400:
        raise ApiError(_status_message(r, urlsplit(url).hostname), r.status_code, _retry_after(r))
    return r


def _retry_after(r: httpx.Response) -> float | None:
    """Seconds from a numeric Retry-After on a 429/503, else None (HTTP-date form is ignored)."""
    v = r.headers.get("retry-after", "")
    return float(v) if r.status_code in (429, 503) and v.replace(".", "", 1).isdigit() else None


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
