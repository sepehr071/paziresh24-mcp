import asyncio
import json
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from mcp import Client

from paziresh24_mcp import http, search, slots
from paziresh24_mcp.server import mcp

FIXTURES = Path(__file__).parent / "fixtures"
RECORDED = date(2026, 10, 6)  # the day the fixtures were recorded (Tehran)


def fixture(name: str):
    path = FIXTURES / name
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix == ".json" else text


def form(request: httpx.Request) -> dict[str, str]:
    """The urlencoded form body of a request (the slot endpoints refuse JSON)."""
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


async def live_call(client, name: str, args: dict) -> dict:
    """Call a tool against the real site, about one request per second, and fail on tool errors."""
    await asyncio.sleep(1)
    result = await client.call_tool(name, args)
    assert not result.is_error, result.content[0].text
    return result.structured_content


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def fresh_http_client():
    """The shared httpx client is bound to one event loop; each test gets a new loop (and an empty cache)."""
    http.set_transport(None)
    yield
    http.set_transport(None)


@pytest.fixture
def api(monkeypatch):
    """Route table for a fake upstream keyed by URL path: api["/path"] = JSON body, (status, JSON body),
    a text body (HTML pages), or a callable(request) -> httpx.Response.

    Unknown paths return 404. Every request is appended to api.calls so tests can assert on what was sent.
    """

    class Routes(dict):
        calls: list[httpx.Request]

    routes = Routes()
    routes.calls = []
    # Fixture dates are judged against the day they were recorded.
    monkeypatch.setattr(search, "today", lambda: RECORDED)
    monkeypatch.setattr(search, "now", lambda: datetime(2026, 10, 6, 2, 0, tzinfo=search.TEHRAN))
    monkeypatch.setattr(slots, "today", lambda: RECORDED)

    def handler(request: httpx.Request) -> httpx.Response:
        routes.calls.append(request)
        target = routes.get(request.url.path)
        if target is None:
            return httpx.Response(404, json={"error_msg": "404 Route Not Found"})
        if callable(target):
            return target(request)
        if isinstance(target, tuple):
            return httpx.Response(target[0], json=target[1])
        if isinstance(target, str):
            return httpx.Response(200, text=target, headers={"content-type": "text/html"})
        return httpx.Response(200, json=target)

    http.set_transport(httpx.MockTransport(handler))
    return routes


@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as c:
        yield c
