import pytest
from conftest import fixture

pytestmark = pytest.mark.anyio


async def test_pz_faq_query(client, api):
    api["/faq/"] = fixture("faq.html")
    data = (await client.call_tool("pz_faq", {"query": "لغو", "limit": 1})).structured_content
    assert data["total"] == 2
    assert data["answers"][0]["question"] == "چگونه نوبت دریافتی را لغو کنیم؟"
    assert data["answers"][0]["answer"].startswith("از بخش «نوبت\u200cهای من»")


async def test_pz_faq_list_and_cache(client, api):
    api["/faq/"] = fixture("faq.html")
    data = (await client.call_tool("pz_faq", {})).structured_content
    assert data["total"] == 4 and data["questions"][2].startswith("چگونه در پذیرش۲۴")
    await client.call_tool("pz_faq", {"query": "نوبت"})
    assert len(api.calls) == 1  # the page is cached


async def test_pz_faq_page_changed(client, api):
    api["/faq/"] = "<html><body>no data</body></html>"
    result = await client.call_tool("pz_faq", {})
    assert result.is_error and "FAQ page has changed" in result.content[0].text
