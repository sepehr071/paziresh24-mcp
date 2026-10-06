import pytest
from conftest import live_call as call

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def test_pz_suggest(client):
    data = await call(client, "pz_suggest", {"query": "قلب"})
    assert "cardiovascular" in [s["slug"] for s in data["specialties"]] and data["queries"]
    data = await call(client, "pz_suggest", {"query": "شیراز"})
    assert data["cities"][0]["slug"] == "shiraz"


async def test_pz_search_doctors(client):
    args = {
        "city": "tehran",
        "specialty": "cardiovascular",
        "visit_type": "online",
        "sort": "cheapest_online",
        "limit": 5,
    }
    data = await call(client, "pz_search_doctors", args)
    assert data["total"] > 50 and len(data["results"]) == 5
    prices = [d["online_visit"]["price_toman"] for d in data["results"] if d.get("online_visit")]
    assert prices == sorted(prices) and all(p >= 10_000 for p in prices)  # Toman, cheapest first
    assert all(d["url"].startswith("https://www.paziresh24.com/dr/") for d in data["results"])


async def test_pz_search_centers(client):
    data = await call(client, "pz_search_doctors", {"city": "tehran", "centers_only": True, "limit": 3})
    assert data["results"] and all(r["kind"] == "center" and r["slug"] for r in data["results"])


async def test_pz_resolve_url(client):
    data = await call(client, "pz_resolve_url", {"url": "https://www.paziresh24.com/dr/دکتر-هدیه-جباری-0/"})
    assert data["kind"] == "doctor"


async def test_pz_center(client):
    centers = await call(client, "pz_search_doctors", {"city": "tehran", "centers_only": True, "limit": 3})
    slug = next(r["slug"] for r in centers["results"] if r["kind"] == "center")
    data = await call(client, "pz_center", {"slug": slug, "doctors_limit": 5})
    assert data["name"] and data["doctor_count"] > 0 and data["doctors"]
