import re
import sys
from pathlib import Path

import httpx
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from paziresh24_mcp import http
from paziresh24_mcp.server import INSTRUCTIONS

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


async def test_stdio_smoke():
    """Start the real server process over stdio and call a tool that needs no network."""
    params = StdioServerParameters(command=sys.executable, args=["-m", "paziresh24_mcp.server"])
    async with Client(params) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        assert "pz_search_doctors" in names
        result = await c.call_tool("pz_resolve_url", {"url": "https://www.paziresh24.com/center/تریتا/"})
        assert not result.is_error and result.structured_content["kind"] == "center"
