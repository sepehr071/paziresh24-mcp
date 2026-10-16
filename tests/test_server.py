import json
import re
import sys
from pathlib import Path

import httpx
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from paziresh24_mcp import http
from paziresh24_mcp.server import INSTRUCTIONS, drop_empty, mcp

pytestmark = pytest.mark.anyio

ROOT = Path(__file__).parent.parent
# Endpoints that hold a real appointment slot, book, log in or send an SMS. None may ever be called.
FORBIDDEN = [
    r"getFreeTurn(?!s)",  # singular: holds the first free slot
    r"\bsuspend\b",
    r"unsuspend",
    r"gozargah",
    r"setTerminal",
    r"bookRequest",
    r"/api/book\b",
    r"/booking/v2/(?!getFreeDays\b|getFreeTurns\b)",
    r"resetpassword",
    r"sendDownloadLinkApp",
]


async def test_tools_are_read_only_and_documented(client):
    tools = (await client.list_tools()).tools
    assert all(t.name.startswith("pz_") and t.annotations.read_only_hint for t in tools)
    assert all(t.title and t.description for t in tools)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert sorted(re.findall(r"^\| `(pz_\w+)` \|", readme, re.M)) == sorted(t.name for t in tools)
    assert f"All {len(tools)} tools" in readme


def test_instructions_cover_workflow_and_units():
    for word in ("pz_search_doctors", "pz_doctor", "pz_free_slots", "Toman", "Tehran", "5532", "SMS"):
        assert word in INSTRUCTIONS


def test_source_never_calls_slot_hold_or_write_endpoints():
    hits = []
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        hits += [f"{path.name}: {p}" for p in FORBIDDEN if re.search(p, text)]
    assert hits == []


def test_no_invisible_zwnj_in_code():
    for path in [*(ROOT / "src").rglob("*.py"), *(ROOT / "tests").glob("*.py")]:
        assert "\u200c" not in path.read_text(encoding="utf-8"), path


async def test_retries_gateway_timeout_once(client, api):
    answers = iter([httpx.Response(504, text="<html>Connection to origin timed out</html>")])
    api["/payment/v1/cancellation-policy"] = lambda r: next(answers, httpx.Response(200, json={"refund_active": False}))
    api["/v1/service-price"] = {"result": {"service_price": 0, "vat": 0, "payable_cost": 0}}
    ids = {"center_id": "5532", "user_center_id": "a-b", "service_id": "c-d"}
    data = (await client.call_tool("pz_visit_price", ids)).structured_content
    assert data["automatic_refund_on_cancel"] is False
    assert [r.url.path for r in api.calls].count("/payment/v1/cancellation-policy") == 2


async def test_second_gateway_timeout_is_an_error(client, api):
    api["/v1/holidays"] = lambda r: httpx.Response(504, text="<html>Connection to origin timed out</html>")
    result = await client.call_tool("pz_holidays", {"date_from": "2026-10-01", "date_to": "2026-10-02"})
    assert result.is_error and "HTTP 504" in result.content[0].text and len(api.calls) == 2


def test_proxy_env_is_the_only_proxy(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    http.set_transport(None)
    client, _ = http._get_client()
    assert client.trust_env is False


async def test_compact_output():
    # The raw client (the conftest `client` re-parses the text into structured_content).
    async with Client(mcp) as c:
        result = await c.call_tool("pz_resolve_url", {"url": "https://www.paziresh24.com/center/تریتا/"})
        text = result.content[0].text
        assert len(result.content) == 1 and result.structured_content is None
        assert ": " not in text and "\n" not in text and "\\u" not in text
        tools = (await c.list_tools()).tools
        assert all(t.output_schema is None for t in tools)
        assert "title" not in json.dumps([t.input_schema for t in tools])


def test_drop_empty_keeps_lists_zero_and_false():
    assert drop_empty({"a": None, "b": "", "c": [], "d": 0, "e": False, "f": [{"g": None, "h": 1}]}) == {
        "c": [],
        "d": 0,
        "e": False,
        "f": [{"h": 1}],
    }


@pytest.mark.parametrize("slug", ["%2E%2E", "...", "/dr/./"])
async def test_dot_slugs_are_rejected(client, slug):
    result = await client.call_tool("pz_doctor", {"slug": slug})
    assert result.is_error and "not a Paziresh24" in result.content[0].text


async def test_fetch_cached_keys_on_params_and_expires(api, monkeypatch):
    api["/x"] = lambda r: httpx.Response(200, json={"q": r.url.params.get("q"), "n": len(api.calls)})
    url = "https://apigw.paziresh24.com/x"
    a = await http.fetch_cached(url, params={"q": "1"})
    a["q"] = "mutated"  # callers get a copy
    assert (await http.fetch_cached(url, params={"q": "1"}))["q"] == "1"
    assert (await http.fetch_cached(url, params={"q": "2"}))["q"] == "2" and len(api.calls) == 2
    monkeypatch.setattr(http, "CACHE_TTL", -1)
    http._cache.clear()
    await http.fetch_cached(url, params={"q": "1"})
    await http.fetch_cached(url, params={"q": "1"})
    assert len(api.calls) == 4


async def test_read_post_is_retried_once(api):
    api["/booking/v2/getFreeDays"] = lambda r: httpx.Response(504, text="<html>timeout</html>")
    with pytest.raises(http.ApiError):
        await http.fetch("https://apigw.paziresh24.com/booking/v2/getFreeDays", method="POST", data={"a": "1"})
    assert len(api.calls) == 2


async def test_malformed_content_length_is_ignored(api):
    api["/x"] = lambda r: httpx.Response(200, json={"ok": 1}, headers={"content-length": "abc"})
    assert await http.fetch("https://apigw.paziresh24.com/x") == {"ok": 1}


async def test_redirect_off_site_is_refused(api):
    api["/r"] = lambda r: httpx.Response(302, headers={"location": "https://evil.example/x"})
    with pytest.raises(http.ApiError, match="Could not reach"):
        await http.fetch("https://apigw.paziresh24.com/r")
    assert [r.url.host for r in api.calls] == ["apigw.paziresh24.com"]


async def test_oversized_body_is_refused(api, monkeypatch):
    monkeypatch.setattr(http, "MAX_BODY", 100)
    api["/big"] = lambda r: httpx.Response(200, content=b"x" * 500)
    with pytest.raises(http.ApiError, match="over"):
        await http.fetch("https://apigw.paziresh24.com/big", text=True)


async def test_stdio_smoke():
    """Start the real server process over stdio and call a tool that needs no network."""
    params = StdioServerParameters(command=sys.executable, args=["-m", "paziresh24_mcp.server"])
    async with Client(params) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        assert "pz_search_doctors" in names
        result = await c.call_tool("pz_resolve_url", {"url": "https://www.paziresh24.com/center/تریتا/"})
        assert not result.is_error and json.loads(result.content[0].text)["kind"] == "center"
