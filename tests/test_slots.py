import pytest
from conftest import fixture, form

pytestmark = pytest.mark.anyio

FREE_DAYS = "/booking/v2/getFreeDays"
OFFICE = {
    "center_id": "291c1bfe-e8f3-414a-a82d-d6fe081683fb",
    "user_center_id": "09558bad-168d-4df8-b39d-437a260ca6fc",
    "service_id": "1a4a8ec2-fdf9-436e-a32b-8fc4485f609c",
}
ONLINE = {
    "center_id": "5532",
    "user_center_id": "972812db-4af0-49d5-865f-516ad2df8474",
    "service_id": "972812db-5afb-427a-9dba-d07aa3fed100",
}


async def test_pz_free_days(client, api):
    api[FREE_DAYS] = fixture("free_days_calendar.json")
    data = (await client.call_tool("pz_free_days", OFFICE)).structured_content
    assert form(api.calls[0]) == {**OFFICE, "server_id": "1", "return_type": "calendar", "return_free_turns": "false"}
    assert data["count"] == 43 and data["days"][:3] == ["2026-10-06", "2026-10-07", "2026-10-10"]
    assert data["days"][-1] == "2026-12-05"
    assert data["weekdays_off"] == ["thursday", "friday"]  # non_workdays "4,5" in JS numbering
    assert data["holidays"] == [{"date": "2026-11-13", "name": "شهادت حضرت فاطمه زهرا (س)"}]
    assert data["online_visit"] is False


async def test_pz_free_slots_range(client, api):
    api[FREE_DAYS] = fixture("free_days_slots.json")
    args = {**ONLINE, "date_from": "2026-10-06", "date_to": "2026-10-07"}
    data = (await client.call_tool("pz_free_slots", args)).structured_content
    assert "return_free_turns" not in form(api.calls[0])  # all-slot mode
    assert data["total_slots"] == 8 and [d["date"] for d in data["days"]] == ["2026-10-06", "2026-10-07"]
    day = data["days"][0]
    assert day == {
        "date": "2026-10-06",
        "weekday": "tuesday",
        "slot_minutes": 10,
        "slots": ["15:00", "15:10", "15:20", "15:30"],
    }


async def test_pz_free_slots_default_today_and_next_day(client, api):
    api[FREE_DAYS] = fixture("free_days_slots.json")
    data = (await client.call_tool("pz_free_slots", ONLINE)).structured_content
    assert data["date_from"] == data["date_to"] == "2026-10-06" and data["total_slots"] == 4
    data = (
        await client.call_tool("pz_free_slots", {**ONLINE, "date_from": "2026-10-08", "date_to": "2026-10-09"})
    ).structured_content
    assert data["days"] == [] and data["next_free_day"] == "2026-10-10"


async def test_pz_free_slots_no_free_slot_status(client, api):
    api[FREE_DAYS] = {"status": 32, "message": "عدم وجود نوبت خالی (تکمیل ظرفیت)", "result": []}
    data = (await client.call_tool("pz_free_slots", ONLINE)).structured_content
    assert data["days"] == [] and data["next_free_day"] is None


async def test_pz_free_days_booking_off(client, api):
    api[FREE_DAYS] = {"status": 700, "message": "نوبت دهی اینترنتی غیر فعال است", "result": []}
    result = await client.call_tool("pz_free_days", ONLINE)
    assert result.is_error and "status 700" in result.content[0].text


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"date_from": "1405-07-18"}, "Gregorian"),
        ({"date_from": "2026-10-10", "date_to": "2026-10-09"}, "on or after"),
        ({"date_from": "2026-10-06", "date_to": "2026-10-20"}, "at most 14 days"),
    ],
)
async def test_pz_free_slots_bad_dates(client, api, args, message):
    result = await client.call_tool("pz_free_slots", {**ONLINE, **args})
    assert result.is_error and message in result.content[0].text and not api.calls


async def test_pz_visit_price(client, api):
    api["/v1/service-price"] = fixture("service_price.json")
    api["/payment/v1/cancellation-policy"] = fixture("cancellation.json")
    data = (await client.call_tool("pz_visit_price", ONLINE)).structured_content
    call = next(r for r in api.calls if r.url.path == "/v1/service-price")
    assert dict(call.url.params) == ONLINE
    assert data == {
        "fee_toman": 500000,
        "vat_toman": 15000,
        "payable_toman": 515000,
        "automatic_refund_on_cancel": False,
    }


async def test_pz_visit_price_office(client, api):
    api["/v1/service-price"] = {"result": {"service_price": 0, "vat": 0, "payable_cost": 0, "currency": "ريال"}}
    api["/payment/v1/cancellation-policy"] = fixture("cancellation.json")
    data = (await client.call_tool("pz_visit_price", OFFICE)).structured_content
    assert data["fee_toman"] == 0 and "office" in data["note"]


async def test_pz_holidays(client, api):
    api["/v1/holidays"] = fixture("holidays.json")
    data = (
        await client.call_tool("pz_holidays", {"date_from": "2026-10-01", "date_to": "2026-12-30"})
    ).structured_content
    assert dict(api.calls[0].url.params) == {"start_date": "2026-10-01", "end_date": "2026-12-30"}
    assert data["days"][0] == {
        "date": "2026-11-13",
        "weekday": "friday",
        "holiday": True,
        "occasions": ["شهادت حضرت فاطمه زهرا (س) - ۳ جمادی الثانیه"],
    }
