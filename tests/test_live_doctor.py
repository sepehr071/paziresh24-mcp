import pytest
from conftest import live_call as call

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def online_doctor(client) -> dict:
    """A Tehran cardiologist with a free online-visit slot (looked up live; doctors come and go)."""
    args = {"city": "tehran", "specialty": "cardiovascular", "visit_type": "online", "sort": "earliest", "limit": 10}
    data = await call(client, "pz_search_doctors", args)
    return next(
        d
        for d in data["results"]
        if (d.get("online_visit") or {}).get("service_id") and d["online_visit"]["first_free"]
    )


async def test_pz_doctor(client):
    card = await online_doctor(client)
    data = await call(client, "pz_doctor", {"slug": card["slug"]})
    assert data["name"] and data["url"] == card["url"]
    assert data["online_visit"]["services"] and data["online_visit"]["center_id"] == "5532"


async def test_pz_first_available(client):
    card = await online_doctor(client)
    data = await call(client, "pz_first_available", {"slug": card["slug"]})
    assert data["online_visit"]["bookable"] and data["online_visit"]["first_free"]


async def test_pz_reviews(client):
    data = await call(client, "pz_reviews", {"slug": "دکتر-زهرا-رجبلی", "sort": "newest", "limit": 5})
    assert data["total"] > 100 and len(data["reviews"]) == 5 and data["summary"]["ratings"] > 100
    assert data["reviews"][0]["posted"] >= data["reviews"][-1]["posted"]
