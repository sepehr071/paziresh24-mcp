"""Finding doctors and centers: suggestions, doctor search, profile URL resolver, hospital/clinic profile.

Also holds the helpers the other tool modules share (Toman, Tehran times, slugs, URLs, HTML text).
"""

from __future__ import annotations

import asyncio
import html
import re
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any, Literal
from urllib.parse import unquote, urlsplit

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .http import CDN, GW, WWW, ApiError, fetch, fetch_cached, legacy
from .registry import tool

TEHRAN = timezone(timedelta(hours=3, minutes=30))  # Iran has no daylight saving time since 2022
ONLINE_CENTER = "5532"  # every doctor's online visit lives in this virtual center
CENTER_TYPES = {1: "office", 2: "hospital", 3: "clinic"}
SORTS = {
    "best": "clinic",
    "earliest": "clinic_first_freeturn",
    "cheapest_online": "clinic_doctor_price",
    "most_viewed": "clinic_doctor_visits",
    "least_waiting": "clinic_less_waiting_time",
    "popular": "clinic_doctor_popular",
}

Slug = Annotated[
    str,
    Field(
        min_length=3,
        max_length=400,
        description="Doctor slug from pz_search_doctors (Persian, e.g. 'دکتر-هدیه-جباری-0') or the doctor's paziresh24.com profile URL.",
    ),
]


@tool("Suggest cities, specialties and queries")
async def pz_suggest(
    query: Annotated[
        str,
        Field(
            min_length=1,
            max_length=100,
            description="Persian or English word: a city ('تهران', 'shiraz'), a specialty or symptom ('قلب', 'پوست'), or the start of a doctor's name.",
        ),
    ],
    limit: Annotated[int, Field(ge=1, le=30, description="Max items in each list.")] = 10,
) -> dict[str, Any]:
    """Turn words into pz_search_doctors arguments: matching cities (city slug), specialties (specialty slug) and the site's type-ahead query completions (use one as `text`).

    Call this first when you do not know the English city slug or the specialty slug. Then call
    pz_search_doctors with city=<slug>, specialty=<slug> or text=<completion>.
    """
    q = norm(query.strip())
    base, menu, qs = await asyncio.gather(
        fetch_cached(f"{WWW}/api/getbaseinfo", method="POST", data={"table": '["city","province"]'}),
        fetch_cached(f"{WWW}/api/megaMenu"),
        fetch(
            f"{GW}/v1/searchia-api/v2/qs/index/slim_clinic_query_su",
            {"query": q, "inContent": "true", "spellCheckEnabled": "true"},
        ),
    )
    base = legacy(base) or {}
    provinces = {p.get("id"): p.get("name") for p in base.get("province") or []}
    low = q.lower()
    cities = [
        {"name": c.get("name"), "slug": c.get("en_slug"), "province": provinces.get(c.get("province_id"))}
        for c in base.get("city") or []
        if q in norm(c.get("name") or "") or (c.get("en_slug") or "").startswith(low)
    ]
    cities.sort(key=lambda c: (norm(c["name"] or "") != q, (c["slug"] or "") != low, len(c["name"] or "")))
    specialties: dict[str, str] = {}  # slug -> title; sub-specialties repeat under several groups
    for group in legacy(menu) or []:
        for item in [group, *(group.get("sub_menu") or [])]:
            title, slug = item.get("title") or "", _menu_slug(item.get("link"))
            if slug and (q in norm(title) or low in slug):
                specialties.setdefault(slug, title)
    return {
        "cities": cities[:limit],
        "specialties": [{"title": t, "slug": s} for s, t in specialties.items()][:limit],
        "queries": ((qs or {}).get("entity") or {}).get("topQuerySuggestions", [])[:limit],
    }


@tool("Search doctors")
async def pz_search_doctors(
    city: Annotated[
        str,
        Field(
            pattern=r"^[a-z0-9-]+$",
            max_length=60,
            description="English city slug from pz_suggest, e.g. 'tehran', 'isfahan', 'mashhad'; 'ir' = all of Iran (best for online visits and name searches).",
        ),
    ] = "ir",
    specialty: Annotated[
        str | None,
        Field(
            pattern=r"^[a-z0-9-]+$",
            max_length=80,
            description="Specialty slug from pz_suggest: a group ('cardiovascular', 'dermatology', 'general-practitioner') or an expertise ('exp-cardiovascular-diseases').",
        ),
    ] = None,
    text: Annotated[
        str | None,
        Field(
            min_length=2,
            max_length=100,
            description="Free text in Persian: a doctor's name ('هدیه جباری'), a disease or a center, e.g. 'آزمایشگاه'.",
        ),
    ] = None,
    visit_type: Annotated[
        Literal["any", "online", "in_person"],
        Field(
            description="'online' = doctors with an online visit (phone or messenger), 'in_person' = office booking."
        ),
    ] = "any",
    sort: Annotated[
        Literal["best", "earliest", "cheapest_online", "most_viewed", "least_waiting", "popular"],
        Field(
            description="Order. 'earliest' = nearest free appointment first (drops doctors without free slots); 'cheapest_online' = cheapest online visit first."
        ),
    ] = "best",
    gender: Annotated[Literal["male", "female"] | None, Field(description="Doctor's gender.")] = None,
    degree: Annotated[
        Literal["فلوشیپ", "فوق تخصص", "دکترای تخصصی", "متخصص", "دکترای", "کارشناس ارشد", "کارشناس"] | None,
        Field(
            description="Academic degree, e.g. 'فوق تخصص' (subspecialist), 'متخصص' (specialist), 'فلوشیپ' (fellowship)."
        ),
    ] = None,
    work_time: Annotated[
        Literal["morning", "afternoon", "night"] | None, Field(description="Doctor works in that part of the day.")
    ] = None,
    center_id: Annotated[
        str | None,
        Field(
            pattern=r"^[0-9a-fA-F-]{1,40}$",
            description="Only doctors of one hospital or clinic: its center_id from pz_center or a center result, e.g. '204'.",
        ),
    ] = None,
    centers_only: Annotated[
        bool, Field(description="List hospitals and clinics of the city instead of doctors (ignores specialty).")
    ] = False,
    page: Annotated[int, Field(ge=1, le=100, description="1-based page number.")] = 1,
    limit: Annotated[int, Field(ge=1, le=50, description="Results per page (the site caps it at 50).")] = 10,
) -> dict[str, Any]:
    """Search Paziresh24 doctors (or hospitals and clinics) by city, specialty, name, online visit, gender, degree, with sorting and paging.

    Each doctor card has the slug, profile URL, rating, accepted insurance names, first free in-person
    (earliest at any center, plus `first_free` per center) and online slots, the online visit price in Toman
    (`online_visit` only when the doctor offers one), and the center/service ids needed for slot tools
    (a null service_id comes from pz_doctor). There is no insurance filter: check `insurances` yourself. Next: pz_doctor for the full profile,
    pz_first_available, or pz_free_days / pz_free_slots with the ids of a card's center.
    """
    route = f"{city}/center" if centers_only else "/".join(p for p in (city, specialty) if p)
    params: dict[str, Any] = {"page": page, "limit": limit}
    if sort != "best":
        params["sortBy"] = SORTS[sort]
    if visit_type != "any":
        params["turn_type"] = "consult" if visit_type == "online" else "non-consult"
    for key, value in (
        ("text", text),
        ("gender", gender),
        ("degree", degree),
        ("work_time_frames", work_time),
        ("center", center_id),
    ):
        if value:
            params[key] = value
    try:
        data = await fetch(f"{GW}/seapi/v1/search/{route}", params)
    except ApiError as e:
        if e.status == 404:
            raise ToolError(
                f"Unknown city or specialty slug in '{route}'. Look the slugs up with pz_suggest (e.g. city='tehran', specialty='cardiovascular')."
            ) from e
        raise
    s = (data or {}).get("search") or {}
    total = s.get("total") or 0
    results = [_card(r) for r in s.get("result") or [] if r.get("type") in ("doctor", "center")]
    return {"total": total, "page": page, "has_more": bool(results) and page * limit < total, "results": results}


@tool("Resolve a Paziresh24 URL")
async def pz_resolve_url(
    url: Annotated[
        str,
        Field(
            min_length=3,
            max_length=600,
            description="A paziresh24.com link (doctor profile /dr/..., center /center/..., online visit /factor/v2/...), percent-encoded or not, e.g. 'https://www.paziresh24.com/dr/دکتر-هدیه-جباری-0/'.",
        ),
    ],
) -> dict[str, Any]:
    """Turn a paziresh24.com link into its kind ('doctor' or 'center') and slug, plus the clean URL.

    Use when the user pastes a link. Next: pz_doctor(slug) for a doctor, pz_center(slug) for a hospital or clinic.
    """
    kind, slug = parse_slug(url)
    return {"kind": kind, "slug": slug, "url": doctor_url(slug) if kind == "doctor" else center_url(slug)}


@tool("Hospital or clinic profile")
async def pz_center(
    slug: Annotated[
        str,
        Field(
            min_length=3,
            max_length=400,
            description="Center slug from a pz_search_doctors center result (or its /center/... URL), e.g. 'بیمارستان-تخصصی-و-فوق-تخصصی-تریتا'.",
        ),
    ],
    specialty: Annotated[
        str | None,
        Field(
            max_length=60,
            description="Only doctors whose department or specialty contains this Persian text, e.g. 'قلب' or 'زنان'.",
        ),
    ] = None,
    doctors_limit: Annotated[int, Field(ge=0, le=200, description="Max doctors to list (0 = none).")] = 30,
) -> dict[str, Any]:
    """Profile of a hospital or clinic: type, address, phone, website, map, about text, departments, and its doctors (slug, department, specialty, basic insurance flags).

    For a paged doctor list of the center sorted by nearest free time, call
    pz_search_doctors(center_id=<center_id>, sort='earliest'). For one doctor, pz_doctor(slug).
    """
    _, slug = parse_slug(slug, "center")
    res = legacy(await fetch(f"{WWW}/api/slugProfile", method="POST", data={"slug": slug})) or {}
    if (res.get("redirect") or {}).get("statusCode") == 404 or not res.get("data"):
        raise ToolError(f"Unknown center slug '{slug}'. Find centers with pz_search_doctors(centers_only=True).")
    if res.get("type") != "center":
        raise ToolError(f"'{slug}' is a doctor, not a center: call pz_doctor('{slug}').")
    d = res["data"]
    want = norm(specialty) if specialty else None
    doctors = []
    for doc in d.get("doctors") or []:
        exps = [e.get("alias_title") for e in doc.get("expertises") or [] if e.get("alias_title")]
        dept = (doc.get("uc_desk") or "").strip() or None
        if want and not any(want in norm(t) for t in [dept or "", *exps]):
            continue
        ins = doc.get("insurance_list") or {}
        doctors.append(
            {
                "name": (doc.get("display_name") or "").strip(),
                "slug": doc.get("slug"),
                "department": dept,
                "specialty": exps[0] if exps else None,
                "insurances": [n for k, n in (("tamin", "تامین اجتماعی"), ("salamat", "سلامت")) if ins.get(k)],
            }
        )
    lat, lon = (d.get("map") or {}).get("lat"), (d.get("map") or {}).get("lon")
    return {
        "center_id": d.get("id"),
        "name": d.get("name"),
        "type": CENTER_TYPES.get(int(d.get("center_type") or 0)) or d.get("center_type_name"),
        "url": center_url(d.get("slug") or slug),
        "city": d.get("city"),
        "province": d.get("province"),
        "address": (d.get("address") or "").strip() or None,
        "phone": re.sub(r"\s+", " ", d.get("display_number") or d.get("tell") or "").strip() or None,
        "website": d.get("website") or None,
        "map": {"lat": float(lat), "lon": float(lon)} if lat and lon else None,
        "image": f"{CDN}{d['image']}" if d.get("image") else None,
        "about": clip(html_text(d.get("biography")), 1500),
        "departments": [
            {"name": s.get("alias_title"), "doctors": len(s.get("doctors") or [])} for s in d.get("services") or []
        ],
        "doctor_count": len(d.get("doctors") or []),
        "matching_doctors": len(doctors),
        "doctors": doctors[:doctors_limit],
    }


def _card(r: dict[str, Any]) -> dict[str, Any]:
    """Compact search card: everything an agent needs to pick a doctor and call the slot tools."""
    if r.get("type") == "center":
        _, slug = parse_slug(r.get("url") or "", "center")
        return {
            "kind": "center",
            "name": r.get("title"),
            "slug": slug,
            "center_id": r.get("id"),
            "url": center_url(slug),
            "address": (r.get("display_address") or "").strip("|, ") or None,
            "views": r.get("view"),
        }
    slug = r.get("slug")
    rate = r.get("rate_info") or {}
    service_of = {s.get("center_id"): s.get("id") for s in r.get("services") or []}
    # Per-center first free time and booking start; the card-level presence_freeturn is the doctor's earliest anywhere.
    free_at = {str(f.get("center_id")): f for f in r.get("freeturns_info") or []}
    consult = (r.get("consult_services") or [{}])[0]
    centers, online_uc = [], None
    for c in r.get("centers") or []:
        if str(c.get("id")) == ONLINE_CENTER:
            online_uc = c.get("user_center_id")
            continue
        free = free_at.get(str(c.get("id"))) or {}
        centers.append(
            {
                "center_id": c.get("id"),
                "user_center_id": c.get("user_center_id"),
                "service_id": service_of.get(c.get("id")),
                "name": c.get("name"),
                "type": CENTER_TYPES.get(c.get("center_type")),
                "city": c.get("city_name"),
                "address": c.get("address"),
                "first_free": upcoming(free.get("freeturn")),
                "booking_opens": upcoming(free.get("available_time")),
            }
        )
    card: dict[str, Any] = {
        "kind": "doctor",
        "slug": slug,
        "name": " ".join(p for p in (r.get("prefix"), r.get("display_name") or r.get("title")) if p),
        "url": doctor_url(slug) if slug else None,
        "specialty": r.get("display_expertise"),
        "gender": {1: "male", 2: "female"}.get(r.get("gender")),
        "rating": round(rate["rate"], 2) if rate.get("rate") else None,
        "ratings_count": rate.get("rates_count") or r.get("rates_count") or 0,
        "satisfaction_pct": r.get("satisfaction"),
        "badges": [b.get("title") for b in r.get("badges") or [] if b.get("title")],
        "insurances": r.get("insurances") or [],
        "waiting_time": r.get("waiting_time"),
        # presence_active_booking was false for doctors with free office slots: only the time is trusted.
        "in_person": {"first_free": when(r.get("presence_freeturn"))},
        "centers": centers,
    }
    # Doctors with online booking switched off keep the 5532 center and a stale consult_freeturn (months or years
    # old); the site shows no online visit for them, so neither do we.
    online_bookable = bool(r.get("consult_active_booking"))
    if online_bookable or consult:
        card["online_visit"] = {
            "bookable": online_bookable,
            "price_toman": toman(consult.get("free_price")),
            "first_free": when(r.get("consult_freeturn")) if online_bookable else None,
            "center_id": ONLINE_CENTER,
            "user_center_id": online_uc,
            "service_id": consult.get("id") or service_of.get(ONLINE_CENTER),
        }
    return card


def _menu_slug(link: Any) -> str | None:
    """Mega-menu links look like /s/ir/<slug>/."""
    m = re.match(r"^/s/ir/([^/]+)/?$", link or "")
    return m[1] if m else None


def parse_slug(value: str, default_kind: str = "doctor") -> tuple[str, str]:
    """A slug or a paziresh24.com URL (encoded or not) -> (kind, slug)."""
    s = unquote(value.strip())
    path = urlsplit(s).path if "://" in s else s.split("?")[0].split("#")[0]
    parts = [p for p in path.split("/") if p]
    if parts and "paziresh24.com" in parts[0]:  # a link pasted without https://
        parts = parts[1:]
    if not parts:
        raise ToolError(f"'{value}' is not a Paziresh24 slug or URL.")
    if parts[0] == "dr" and len(parts) > 1:
        return "doctor", parts[1]
    if parts[0] == "center" and len(parts) > 1:
        return "center", parts[1]
    if parts[0] == "factor" and len(parts) > 2:  # /factor/v2/<slug>/<service id>
        return "doctor", parts[2] if parts[1].startswith("v") else parts[1]
    if parts[0] in ("s", "dr", "center", "factor") or len(parts) > 1:
        raise ToolError(f"'{value}' is not a doctor or center link. Doctor links look like /dr/<slug>/.")
    if parts[0].startswith("دکتر-"):
        return "doctor", parts[0]
    return default_kind, parts[0]


def doctor_url(slug: str) -> str:
    return f"{WWW}/dr/{slug}/"


def center_url(slug: str) -> str:
    return f"{WWW}/center/{slug}/"


def toman(rial: Any) -> int | None:
    """Every Paziresh24 API price is Rial; the site shows Toman (Rial / 10). 0 means not priced online."""
    try:
        return round(float(rial) / 10) if rial is not None and rial != "" else None
    except (TypeError, ValueError):
        return None


def now() -> datetime:
    return datetime.now(TEHRAN)


def today() -> date:
    return now().date()


def at(ts: Any) -> datetime | None:
    """Unix seconds (int or numeric string) -> Tehran datetime; None or 0 -> None."""
    try:
        n = int(ts)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(n, TEHRAN) if n > 0 else None


def when(ts: Any) -> str | None:
    """Unix seconds -> 'YYYY-MM-DD HH:MM' Tehran time."""
    t = at(ts)
    return t.strftime("%Y-%m-%d %H:%M") if t else None


def upcoming(ts: Any) -> str | None:
    """`when`, but only for a time still ahead: booking start times (`available_time`; the site then says the
    booking period has ended) and per-center free times, which can be stale for centers booked elsewhere."""
    t = at(ts)
    return t.strftime("%Y-%m-%d %H:%M") if t and t > now() else None


def day_start(d: date) -> int:
    """Unix second of 00:00 Tehran of a day (the slot APIs' day key)."""
    return int(datetime(d.year, d.month, d.day, tzinfo=TEHRAN).timestamp())


def norm(s: str) -> str:
    """Arabic yeh/kaf -> Persian and zero-width non-joiner -> space, so typed and stored names match."""
    return s.replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ")


def html_text(s: Any) -> str:
    """Plain text from HTML: tags dropped, entities decoded, blank lines collapsed."""
    text = re.sub(r"<(br|/p|/div|/li|/h\d)\s*/?>", "\n", s or "", flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t\r\xa0]+", " ", text)).strip()


def clip(s: Any, n: int) -> str | None:
    s = (s or "").strip()
    return (s[: n - 1] + "…" if len(s) > n else s) or None
