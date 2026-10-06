import json

import pytest
from conftest import fixture

pytestmark = pytest.mark.anyio

JAB = "دکتر-هدیه-جباری-0"
RAJ = "دکتر-زهرا-رجبلی"
PROFILE = f"/api/full-profile/{JAB}/"
RATE = "/ravi/v1/rate/doctor/"
FEEDBACKS = "/ravi/v1/feedbacks/doctors/"
WAITING = "/ravi/v1/avg-waiting-time/doctors/"


def profile_routes(api):
    api[PROFILE] = fixture("full_profile.json")
    api[RATE + JAB] = fixture("rate.json")
    api[f"/api/doctors/{JAB}"] = fixture("lookup.json")
    api["/v1/doctors/4775150/insurance"] = fixture("insurance.json")


async def test_pz_doctor(client, api):
    profile_routes(api)
    data = (await client.call_tool("pz_doctor", {"slug": f"https://www.paziresh24.com/dr/{JAB}/"})).structured_content
    assert data["name"] == "هدیه جباری" and data["url"] == f"https://www.paziresh24.com/dr/{JAB}/"
    assert data["gender"] == "female" and data["medical_code"] == "163761"
    assert data["specialties"] == ["متخصص بیماریهای قلب و عروق"] and data["specialty_slugs"] == ["cardiovascular"]
    assert data["about"].startswith("دارای بورد تخصصی") and "<" not in data["about"]
    assert data["rating"] == {
        "behaviour": 4.97,
        "explanation": 4.96,
        "treatment": 4.97,
        "ratings": 679,
        "comments": 638,
    }
    assert data["insurances"] == {
        "basic": ["تامین اجتماعی", "سلامت ایرانیان", "خدمات درمانی", "نیروهای مسلح"],
        "supplementary": [],
    }
    online = data["online_visit"]
    assert online["channels"] == ["whatsapp", "igap_number", "secure_call"]
    assert (
        online["first_free"] == "2026-10-06 15:00"
        and online["user_center_id"] == "972812db-4af0-49d5-865f-516ad2df8474"
    )
    assert online["services"][0]["price_toman"] == 500000 and online["services"][0]["duration_min"] == 10
    office = data["centers"][0]
    assert office["center_id"] == "291c1bfe-e8f3-414a-a82d-d6fe081683fb" and office["type"] == "office"
    assert office["first_free"] == "2026-10-06 11:00" and office["map"]["lat"] == 35.778615445525
    svc = office["services"][0]
    assert svc["service_id"] == "1a4a8ec2-fdf9-436e-a32b-8fc4485f609c" and svc["price_toman"] == 0
    assert svc["duration_min"] == 20 and svc["bookable"] and not svc["request_only"]
    assert svc["hours"][0] == "saturday 15:00-19:40" and svc["hours"][-1] == "wednesday 11:00-15:20"
    assert len(json.dumps(data, ensure_ascii=False).encode()) < 15_000


async def test_pz_doctor_inactive_center_and_booking_opens(client, api):
    # Real profile (2026-10-06): the site lists the inactive Paveh clinic ("booking switched off") and says the
    # hospital's booking period has ended, new slots from 21 Mehr 00:00 (2026-10-13).
    slug = "دکتر-فاطمه-نجفی-12"
    api[f"/api/full-profile/{slug}/"] = fixture("full_profile_centers.json")
    data = (await client.call_tool("pz_doctor", {"slug": slug})).structured_content
    office, hospital, paveh = data["centers"]
    assert office["booking_off"] is None and office["booking_opens"] is None
    assert hospital["first_free"] == "2026-10-14 15:30" and hospital["booking_opens"] == "2026-10-13 00:00"
    assert paveh["name"] == "کلینیک تخصصی بیمارستان قدس پاوه" and paveh["booking_off"] is True
    assert paveh["first_free"] is None and not paveh["services"][0]["bookable"]
    assert data["online_visit"]["center_id"] == "5532"


async def test_pz_doctor_secondary_data_missing(client, api):
    api[PROFILE] = fixture("full_profile.json")
    api[RATE + JAB] = (404, {"error": {"code": "RATE_NOT_FOUND", "message": "doctor rate summary not found"}})
    api[f"/api/doctors/{JAB}"] = fixture("lookup.json")
    api["/v1/doctors/4775150/insurance"] = {}  # biko: no data
    data = (await client.call_tool("pz_doctor", {"slug": JAB})).structured_content
    assert data["rating"] is None and data["insurances"] is None and data["centers"]


async def test_pz_doctor_unknown(client, api):
    api["/api/full-profile/دکتر-ناموجود/"] = (404, {"error": "Slug not found"})
    result = await client.call_tool("pz_doctor", {"slug": "دکتر-ناموجود"})
    assert result.is_error and "Unknown doctor slug" in result.content[0].text


async def test_pz_first_available(client, api):
    api[f"/v1/doctors/{JAB}/availability-status"] = fixture("availability.json")
    data = (await client.call_tool("pz_first_available", {"slug": JAB})).structured_content
    assert data["in_person"] == {"bookable": True, "first_free": "2026-10-06 11:00"}
    assert data["online_visit"] == {"bookable": True, "first_free": "2026-10-06 15:00"}
    assert data["centers"][1] == {"center_id": "5532", "bookable": True, "first_free": "2026-10-06 15:00"}


async def test_pz_first_available_unknown(client, api):
    api["/v1/doctors/zz-unknown/availability-status"] = (404, {"message": "Doctor centers not found"})
    result = await client.call_tool("pz_first_available", {"slug": "zz-unknown"})
    assert result.is_error and "No bookable centers" in result.content[0].text


async def test_pz_reviews(client, api):
    api[FEEDBACKS + RAJ] = fixture("feedbacks.json")
    api[RATE + RAJ] = fixture("rate.json")
    api[WAITING + RAJ] = fixture("waiting.json")
    data = (await client.call_tool("pz_reviews", {"slug": RAJ, "sort": "newest", "limit": 3})).structured_content
    call = next(r for r in api.calls if r.url.path == FEEDBACKS + RAJ)
    assert dict(call.url.params) == {"filter": "newest", "limit": "3", "offset": "0"}
    assert data["total"] == 622 and data["has_more"]
    assert data["summary"]["ratings"] == 679
    assert data["summary"]["waiting_minutes"][0] == {"center_id": "5532", "minutes": 15, "answers": 20}
    r = data["reviews"][0]
    assert r["stars"] == 5 and r["reason"] == "کرونا" and r["posted"] == "2026-10-04" and r["center_id"] == "5532"
    assert r["text"].startswith("ویزیت عالی") and r["waiting_minutes"] is None
    assert not any(k in r for k in ("user_id", "user_display_name"))


async def test_pz_reviews_unknown_slug(client, api):
    # ravi answers an empty page (HTTP 200) and RATE_NOT_FOUND for any slug; drprofile tells unknown slugs apart.
    slug = "دکتر-ناموجود"
    api[FEEDBACKS + slug] = {"list": [], "pageInfo": {"totalRows": 0, "isFirstPage": True, "isLastPage": True}}
    api[RATE + slug] = (404, {"error": {"code": "RATE_NOT_FOUND", "message": "doctor rate summary not found"}})
    api[WAITING + slug] = {"list": [], "pageInfo": {"totalRows": 0}}
    api[f"/api/doctors/{slug}"] = (404, {"error": "Slug not found"})
    result = await client.call_tool("pz_reviews", {"slug": slug})
    assert result.is_error and "Unknown doctor slug" in result.content[0].text
    api[f"/api/doctors/{slug}"] = fixture("lookup.json")  # a real doctor without reviews: empty, no error
    data = (await client.call_tool("pz_reviews", {"slug": slug})).structured_content
    assert data["total"] == 0 and data["reviews"] == []


async def test_pz_reviews_next_page_skips_summary(client, api):
    api[FEEDBACKS + RAJ] = fixture("feedbacks.json")
    data = (await client.call_tool("pz_reviews", {"slug": RAJ, "offset": 10})).structured_content
    assert "summary" not in data and len(api.calls) == 1
    assert api.calls[0].url.params["filter"] == "default"
