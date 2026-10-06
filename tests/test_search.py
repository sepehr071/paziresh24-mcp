import pytest
from conftest import fixture, form

from paziresh24_mcp.search import parse_slug, toman, upcoming, when

pytestmark = pytest.mark.anyio

SEARCH = "/seapi/v1/search/"
SUGGEST = "/v1/searchia-api/v2/qs/index/slim_clinic_query_su"
JAB = "دکتر-هدیه-جباری-0"
TREATA = "بیمارستان-تخصصی-و-فوق-تخصصی-تریتا"


def suggest_routes(api):
    api["/api/getbaseinfo"] = fixture("baseinfo.json")
    api["/api/megaMenu"] = fixture("megamenu.json")
    api[SUGGEST] = fixture("query_suggest.json")


async def test_pz_suggest_specialty(client, api):
    suggest_routes(api)
    data = (await client.call_tool("pz_suggest", {"query": "قلب"})).structured_content
    slugs = [s["slug"] for s in data["specialties"]]
    assert slugs[:3] == ["exp-cardiac-anesthesia", "cardiovascular", "exp-cardiovascular-diseases"]
    assert len(slugs) == len(set(slugs))  # sub-specialties listed under two groups appear once
    assert data["cities"] == []
    assert data["queries"][0] == "قلب و عروق"
    base = next(r for r in api.calls if r.url.path == "/api/getbaseinfo")
    assert form(base) == {"table": '["city","province"]'}


async def test_pz_suggest_city_and_cache(client, api):
    suggest_routes(api)
    data = (await client.call_tool("pz_suggest", {"query": "شيراز", "limit": 3})).structured_content  # Arabic yeh
    assert data["cities"] == [{"name": "شیراز", "slug": "shiraz", "province": "فارس"}]
    data = (await client.call_tool("pz_suggest", {"query": "teh"})).structured_content
    assert data["cities"][0]["slug"] == "tehran"
    paths = [r.url.path for r in api.calls]
    assert paths.count("/api/getbaseinfo") == 1 and paths.count("/api/megaMenu") == 1  # cached
    assert paths.count(SUGGEST) == 2


async def test_pz_search_doctors_online_cheapest(client, api):
    api[SEARCH + "tehran/cardiovascular"] = fixture("search_consult.json")
    args = {
        "city": "tehran",
        "specialty": "cardiovascular",
        "visit_type": "online",
        "sort": "cheapest_online",
        "limit": 2,
    }
    data = (await client.call_tool("pz_search_doctors", args)).structured_content
    assert dict(api.calls[0].url.params) == {
        "page": "1",
        "limit": "2",
        "sortBy": "clinic_doctor_price",
        "turn_type": "consult",
    }
    assert data["total"] == 305 and data["has_more"] and len(data["results"]) == 2
    d = data["results"][0]
    assert d["slug"] == "دکتر-سعيد-نوريان" and d["url"] == "https://www.paziresh24.com/dr/دکتر-سعيد-نوريان/"
    assert d["name"] == "دکتر سعيد نوريان" and d["gender"] == "male" and d["rating"] == 4.05
    assert d["insurances"] == ["تامین اجتماعی"]
    assert d["online_visit"] == {
        "bookable": True,
        "price_toman": 80000,  # free_price 800000 Rial
        "first_free": "2026-10-06 09:00",
        "center_id": "5532",
        "user_center_id": "978dc085-e011-48d0-8756-f18183903bff",
        "service_id": "978dc085-e57c-4252-931c-a952e827daa1",
    }
    assert d["centers"] == [
        {
            "center_id": "31a36dda-3f95-495b-ad8e-4b63f784644d",
            "user_center_id": "c111142f-6759-4210-ac35-5f504951156f",
            "service_id": "a1505193-9a32-4a6d-a044-37791448115f",
            "name": "مطب دکتر سعيد نوريان",
            "type": "office",
            "city": "تهران",
            "address": None,
            "first_free": None,  # no office entry in freeturns_info (online only)
            "booking_opens": None,
        }
    ]
    assert data["results"][1]["in_person"] == {"first_free": "2026-10-06 16:00"}


async def test_pz_search_doctors_per_center_times_and_stale_online(client, api):
    # Real cards (2026-10-06): a Treata doctor whose card-level first free is at another center, and a doctor with
    # online booking off whose consult_freeturn is from July (the site shows no online visit for her).
    api[SEARCH + "ir"] = fixture("search_freeturns.json")
    data = (await client.call_tool("pz_search_doctors", {"center_id": "204"})).structured_content
    doc, off = data["results"]
    assert doc["in_person"] == {"first_free": "2026-10-06 16:00"}  # earliest anywhere
    assert doc["centers"][0]["center_id"] == "204"
    assert doc["centers"][0]["first_free"] == "2026-10-26 08:03"  # the site: 4 Aban 8 AM at Treata
    assert doc["centers"][0]["booking_opens"] == "2026-10-19 00:00"
    assert off["slug"] == "دکتر-ساناز-سالمیان-پور" and "online_visit" not in off
    assert off["centers"][0]["first_free"] == "2026-10-06 09:00" and off["centers"][0]["booking_opens"] is None


async def test_pz_search_doctors_by_name(client, api):
    api[SEARCH + "ir"] = fixture("search_text.json")
    data = (await client.call_tool("pz_search_doctors", {"text": "هدیه جباری", "limit": 2})).structured_content
    assert api.calls[0].url.params["text"] == "هدیه جباری" and "sortBy" not in api.calls[0].url.params
    doc, center = data["results"]
    assert doc["slug"] == JAB and doc["badges"] == ["منتخب پذیرش24"]
    assert doc["centers"][0]["service_id"] is None  # this card has no services[]: pz_doctor has it
    assert center == {
        "kind": "center",
        "name": "مطب دکتر هدیه جباری",
        "slug": "مطب-دکتر-هدیه-جباری",
        "center_id": "291c1bfe-e8f3-414a-a82d-d6fe081683fb",
        "url": "https://www.paziresh24.com/center/مطب-دکتر-هدیه-جباری/",
        "address": "سعادت آباد، چهارراه سرو، سرو غربی، کوچه نامی، طبقه سوم آزمایشگاه ساره",
        "views": 706,
    }


async def test_pz_search_centers_and_filters(client, api):
    api[SEARCH + "tehran/center"] = fixture("search_centers.json")
    args = {"city": "tehran", "specialty": "cardiovascular", "centers_only": True, "gender": "female", "page": 2}
    data = (await client.call_tool("pz_search_doctors", args)).structured_content
    assert api.calls[0].url.path == SEARCH + "tehran/center"
    assert api.calls[0].url.params["gender"] == "female" and api.calls[0].url.params["page"] == "2"
    assert [r["center_id"] for r in data["results"]] == ["191", "204"]
    assert data["results"][1]["slug"] == TREATA


async def test_pz_search_unknown_slug(client, api):
    api[SEARCH + "zzz/cardiovascular"] = lambda r: __import__("httpx").Response(404, text="<html>Not Found</html>")
    result = await client.call_tool("pz_search_doctors", {"city": "zzz", "specialty": "cardiovascular"})
    assert result.is_error and "pz_suggest" in result.content[0].text


async def test_pz_resolve_url(client):
    encoded = "https://www.paziresh24.com/dr/%D8%AF%DA%A9%D8%AA%D8%B1-%D9%87%D8%AF%DB%8C%D9%87-%D8%AC%D8%A8%D8%A7%D8%B1%DB%8C-0/?utm=x"
    data = (await client.call_tool("pz_resolve_url", {"url": encoded})).structured_content
    assert data == {"kind": "doctor", "slug": JAB, "url": f"https://www.paziresh24.com/dr/{JAB}/"}
    data = (await client.call_tool("pz_resolve_url", {"url": f"paziresh24.com/center/{TREATA}"})).structured_content
    assert data["kind"] == "center" and data["slug"] == TREATA
    result = await client.call_tool("pz_resolve_url", {"url": "https://www.paziresh24.com/s/tehran/"})
    assert result.is_error and "/dr/" in result.content[0].text


def test_parse_slug_forms():
    assert parse_slug(f"/factor/v2/{JAB}/972812db-5afb-427a-9dba-d07aa3fed100") == ("doctor", JAB)
    assert parse_slug(f"https://www.paziresh24.com/{JAB}") == ("doctor", JAB)
    assert parse_slug(TREATA, "center") == ("center", TREATA)


async def test_pz_center(client, api):
    api["/api/slugProfile"] = fixture("center.json")
    data = (await client.call_tool("pz_center", {"slug": TREATA, "specialty": "قلب"})).structured_content
    assert form(api.calls[0]) == {"slug": TREATA}
    assert data["center_id"] == "204" and data["type"] == "hospital"
    assert data["phone"] == "021-47241000 داخلی 1" and data["website"] == "www.treatahospital.com"
    assert data["map"] == {"lat": 35.7576605, "lon": 51.2242893}
    assert data["about"].startswith("بیمارستان تخصصی و فوق تخصصی تریتا") and "<" not in data["about"]
    assert data["departments"][0] == {"name": "ارتوپدی", "doctors": 2}
    assert data["doctor_count"] == 4 and data["matching_doctors"] == 2
    assert [d["slug"] for d in data["doctors"]] == ["دکتر-زهرا-اعمرائی-0", "دکتر-الهه-افشین-0"]
    assert data["doctors"][1]["specialty"] == "متخصص بیماریهای قلب و عروق"


async def test_pz_center_unknown_and_doctor(client, api):
    api["/api/slugProfile"] = fixture("center_404.json")
    result = await client.call_tool("pz_center", {"slug": "zz-not-a-slug-qq"})
    assert result.is_error and "Unknown center slug" in result.content[0].text


def test_units():
    assert toman(5000000) == 500000 and toman("0") == 0 and toman(None) is None
    assert when(1791271800) == "2026-10-06 11:00" and when("1791286200") == "2026-10-06 15:00" and when(0) is None
    assert upcoming("1789918500") is None and upcoming(None) is None  # a Treata card's stale 2026-09-20 freeturn
