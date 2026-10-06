from datetime import date, timedelta

import pytest
from conftest import live_call as call
from test_live_doctor import online_doctor

pytestmark = [pytest.mark.anyio, pytest.mark.live]


def ids(card: dict) -> dict:
    o = card["online_visit"]
    return {"center_id": o["center_id"], "user_center_id": o["user_center_id"], "service_id": o["service_id"]}


async def test_pz_free_days_and_slots(client):
    card = await online_doctor(client)
    days = await call(client, "pz_free_days", ids(card))
    assert days["count"] > 0 and days["online_visit"]
    first = date.fromisoformat(days["days"][0])
    data = await call(
        client, "pz_free_slots", {**ids(card), "date_from": str(first), "date_to": str(first + timedelta(days=2))}
    )
    assert data["total_slots"] > 0 and data["days"][0]["date"] == str(first)
    assert all(len(t) == 5 and t[2] == ":" for t in data["days"][0]["slots"])


async def test_pz_visit_price(client):
    card = await online_doctor(client)
    data = await call(client, "pz_visit_price", ids(card))
    assert data["fee_toman"] == card["online_visit"]["price_toman"]
    assert data["payable_toman"] == data["fee_toman"] + data["vat_toman"]


async def test_pz_holidays(client):
    data = await call(client, "pz_holidays", {"date_from": "2026-10-01", "date_to": "2027-03-30"})
    assert any(d["holiday"] for d in data["days"])
