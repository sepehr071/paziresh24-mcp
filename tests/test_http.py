import httpx
import pytest

from paziresh24_mcp import http

pytestmark = pytest.mark.anyio

API_URL = "https://apigw.paziresh24.com/x"
WWW_URL = "https://www.paziresh24.com/faq/"


def test_is_challenge():
    html = {"content-type": "text/html"}
    assert http._is_challenge(httpx.Response(403, text="<html>Just a moment...</html>", headers=html))
    assert http._is_challenge(httpx.Response(503, text="<!doctype html><title>ArvanCloud</title>"))
    assert not http._is_challenge(httpx.Response(504, text="<html>Connection to origin timed out</html>", headers=html))
    assert not http._is_challenge(httpx.Response(403, text="<html>Access Denied</html>", headers=html))
    assert not http._is_challenge(httpx.Response(403, json={"error": "x"}))
    assert not http._is_challenge(httpx.Response(200, text="<html></html>", headers=html))


async def test_frontend_headers_per_host(api):
    api["/x"] = {"ok": 1}
    api["/faq/"] = "<html></html>"
    await http.fetch(API_URL)
    await http.fetch(API_URL, method="POST", data={"a": "1"})
    await http.fetch(WWW_URL, text=True)
    get, post, page = api.calls
    assert get.headers["Origin"] == post.headers["Origin"] == "https://www.paziresh24.com"
    assert get.headers["Referer"] == "https://www.paziresh24.com/"
    assert get.headers["sec-fetch-site"] == "same-site"
    assert page.headers["sec-fetch-site"] == "same-origin" and "Origin" not in page.headers
    assert get.headers["Accept-Language"].startswith("fa-IR")
    assert "User-Agent" not in http.HEADERS


async def test_429_honours_retry_after_then_succeeds(api):
    hits = []

    def limited(request):
        hits.append(1)
        return (
            httpx.Response(429, headers={"Retry-After": "0"}) if len(hits) == 1 else httpx.Response(200, json={"ok": 1})
        )

    api["/x"] = limited
    assert await http.fetch(API_URL) == {"ok": 1} and len(hits) == 2


async def test_403_is_not_retried(api):
    api["/x"] = lambda r: httpx.Response(403, json={"error": "no"})
    with pytest.raises(http.ApiError):
        await http.fetch(API_URL)
    assert len(api.calls) == 1


async def test_network_error_is_retried_twice(api):
    def boom(request):
        raise httpx.ConnectError("refused", request=request)

    api["/x"] = boom
    with pytest.raises(http.ApiError, match="Could not reach"):
        await http.fetch(API_URL)
    assert len(api.calls) == 3


async def test_proxy_list_rotates_on_connect_error(monkeypatch, api):
    monkeypatch.setenv("PAZIRESH24_MCP_PROXY", "http://p1:1, http://p2:2")
    assert http._proxies() == ["http://p1:1", "http://p2:2"]

    def boom(request):
        raise httpx.ConnectError("down", request=request)

    api["/x"] = boom
    http._get_client()
    with pytest.raises(http.ApiError):
        await http._get(API_URL, None, "GET", None)
    assert http._proxy_index == 1 and http._client is None
    monkeypatch.setenv("PAZIRESH24_MCP_PROXY", "http://p1:1")
    http._get_client()
    with pytest.raises(http.ApiError):
        await http._get(API_URL, None, "GET", None)
    assert http._client is not None  # a single proxy never rotates


async def test_stale_client_does_not_rotate(monkeypatch, api):
    monkeypatch.setenv("PAZIRESH24_MCP_PROXY", "http://p1:1, http://p2:2")
    old, _ = http._get_client()
    http._next_proxy(old)
    new, _ = http._get_client()
    assert http._proxy_index == 1 and new is not old
    http._next_proxy(old)  # the retired client failing late must not skip the new proxy
    assert http._proxy_index == 1 and http._client is new
