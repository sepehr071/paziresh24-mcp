"""One doctor: full profile, earliest free time, patient reviews and rating summary."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .http import BIKO, CDN, DRPROFILE, RAVI, SAMAN, ApiError, fetch
from .registry import tool
from .search import (
    CENTER_TYPES,
    ONLINE_CENTER,
    TEHRAN,
    Slug,
    clip,
    doctor_url,
    html_text,
    parse_slug,
    toman,
    upcoming,
    when,
)

WEEKDAYS = {1: "monday", 2: "tuesday", 3: "wednesday", 4: "thursday", 5: "friday", 6: "saturday", 7: "sunday"}
ORDER = {6: 0, 7: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6}  # Iranian week starts on Saturday


@tool("Doctor profile")
async def pz_doctor(slug: Slug) -> dict[str, Any]:
    """Full profile of one doctor: specialties, about text, medical council code, rating averages, insurance contracts, online visit (channels, price in Toman), and every office/hospital/clinic with address, phone, map, first free slot and services (ids, price, duration, weekly hours).

    A center with `booking_off: true` has online booking switched off (call its phone); `booking_opens` means
    the booking period has ended and new slots open at that time. Use after pz_search_doctors or pz_resolve_url.
    The center_id + user_center_id + service_id of a
    service feed pz_first_available, pz_free_days, pz_free_slots and pz_visit_price. Reviews: pz_reviews.
    Booking itself is done by the user on the returned `url` (it needs an SMS login).
    """
    slug = _slug(slug)
    profile, rate, ids = await asyncio.gather(
        _profile(slug),
        optional(fetch(f"{RAVI}/rate/doctor/{quote(slug)}")),
        optional(fetch(f"{DRPROFILE}/api/doctors/{quote(slug)}")),
    )
    user_id = (ids or {}).get("user_id")
    insurance = await optional(fetch(f"{BIKO}/v1/doctors/{user_id}/insurance")) if user_id else None
    online, centers = None, []
    for c in profile.get("centers") or []:
        # Inactive centers stay listed (the site shows their address and phone, with booking switched off).
        inactive = c.get("is_active") is False
        services = [_service(s) for s in c.get("services") or []]
        if str(c.get("id")) == ONLINE_CENTER:
            if inactive:
                continue
            online = {
                "bookable": bool(profile.get("consult_active_booking")),
                "channels": list(dict.fromkeys(profile.get("online_visit_channel_types") or [])),
                "first_free": when(c.get("freeturn")),
                "center_id": ONLINE_CENTER,
                "user_center_id": c.get("user_center_id"),
                "services": services,
            }
            continue
        lat, lon = (c.get("map") or {}).get("lat"), (c.get("map") or {}).get("lon")
        app_only = c.get("is_only_in_app") or {}
        centers.append(
            {
                "center_id": c.get("id"),
                "user_center_id": c.get("user_center_id"),
                "name": c.get("name"),
                "type": CENTER_TYPES.get(c.get("center_type")) or c.get("center_type_name"),
                "city": c.get("city"),
                "address": c.get("address"),
                "phone": c.get("display_number") or c.get("tell") or None,
                "map": {"lat": lat, "lon": lon} if lat and lon else None,
                "first_free": when(c.get("freeturn")),
                "booking_off": True if inactive else None,
                "booking_opens": min(
                    filter(None, (upcoming(f.get("available_time")) for f in c.get("freeturns_info") or [])),
                    default=None,
                ),
                "app_only": app_only.get("message") or True if app_only.get("status") else None,
                "services": services,
            }
        )
    return {
        "slug": profile.get("slug") or slug,
        "name": profile.get("display_name"),
        "url": doctor_url(profile.get("slug") or slug),
        "gender": {1: "male", 2: "female"}.get(profile.get("gender")),
        "medical_code": profile.get("medical_code"),
        "specialties": [e.get("alias_title") for e in profile.get("expertises") or [] if e.get("alias_title")],
        "specialty_slugs": [g.get("en_slug") for g in profile.get("group_expertises") or [] if g.get("en_slug")],
        "about": clip(html_text(profile.get("biography")), 1200),
        "image": f"{CDN}{profile['image']}" if profile.get("image") else None,
        "profile_views": profile.get("number_of_visits"),
        "on_paziresh24_for": profile.get("insert_at_age"),
        "rating": rating_summary(rate),
        "insurances": _insurances(insurance),
        "online_visit": online,
        "centers": centers,
    }


@tool("Earliest free appointment")
async def pz_first_available(slug: Slug) -> dict[str, Any]:
    """Whether a doctor can be booked now and the nearest free slot, for in-person and online visits and per center (Tehran time).

    A quick check before pz_free_days / pz_free_slots. Center names and service ids are in pz_doctor
    (online visit center_id is '5532').
    """
    slug = _slug(slug)
    try:
        data = await fetch(f"{SAMAN}/v1/doctors/{quote(slug)}/availability-status")
    except ApiError as e:
        if e.status == 404:
            raise ToolError(f"No bookable centers found for '{slug}' (unknown slug or no booking).") from e
        raise

    def status(a: Any) -> dict[str, Any]:
        a = a or {}
        return {"bookable": bool(a.get("booking_available")), "first_free": iso_when(a.get("nearest_time_slot"))}

    return {
        "url": doctor_url(slug),
        "in_person": status(data.get("in_person_availability")),
        "online_visit": status(data.get("online_visit_availability")),
        "centers": [
            {"center_id": c.get("id"), **status(c.get("availability_status"))} for c in data.get("centers") or []
        ],
    }


@tool("Doctor reviews")
async def pz_reviews(
    slug: Slug,
    sort: Annotated[
        Literal["relevant", "newest"], Field(description="'relevant' = the site's default order, 'newest' first.")
    ] = "relevant",
    limit: Annotated[int, Field(ge=1, le=50, description="Reviews per page.")] = 10,
    offset: Annotated[int, Field(ge=0, le=100_000, description="Reviews to skip (paging), e.g. 10 for page 2.")] = 0,
) -> dict[str, Any]:
    """Patient reviews of a doctor (stars 1-5 for behaviour, explanation and treatment, text, visit reason, center, verified visit), paged, plus on the first page a summary: rating averages, counts and average waiting time per center.

    Reviewer names and ids are never returned. Online-visit reviews have center_id '5532'.
    """
    slug = _slug(slug)
    s = quote(slug)
    page = fetch(
        f"{RAVI}/feedbacks/doctors/{s}",
        {"filter": "default" if sort == "relevant" else "newest", "limit": limit, "offset": offset},
    )
    if offset:
        data, rate, waits = await page, None, None
    else:
        data, rate, waits = await asyncio.gather(
            page,
            optional(fetch(f"{RAVI}/rate/doctor/{s}")),
            optional(fetch(f"{RAVI}/avg-waiting-time/doctors/{s}", {"page": 1, "pageSize": 25})),
        )
    info = (data or {}).get("pageInfo") or {}
    if not offset and not info.get("totalRows") and rate is None:
        # ravi answers an empty page for any slug: tell an unknown doctor apart from one without reviews.
        try:
            await fetch(f"{DRPROFILE}/api/doctors/{s}")
        except ApiError as e:
            if e.status == 404:
                raise ToolError(f"Unknown doctor slug '{slug}'. Find doctors with pz_search_doctors.") from e
    out: dict[str, Any] = {"url": doctor_url(slug), "total": info.get("totalRows", 0), "offset": offset}
    if not offset:
        summary = rating_summary(rate) or {}
        summary["waiting_minutes"] = [
            {
                "center_id": w.get("center_id"),
                "minutes": w.get("avg_waiting_time"),
                "answers": w.get("feedback_count"),
            }
            for w in (waits or {}).get("list") or []
        ]
        out["summary"] = summary
    out["has_more"] = info.get("isLastPage") is False
    out["reviews"] = [_review(r) for r in (data or {}).get("list") or []]
    return out


def _review(r: dict[str, Any]) -> dict[str, Any]:
    # user_id / user_display_name are the patient's: deliberately not copied.
    rec = r.get("recommended")
    return {
        "id": r.get("Id"),
        "stars": r.get("avg_rate_value"),
        "behaviour": r.get("doctor_encounter"),
        "explanation": r.get("explanation_of_issue"),
        "treatment": r.get("quality_of_treatment"),
        "recommends": None if rec is None else bool(rec),
        "expectation": r.get("expectation_match"),
        "reason": r.get("condition"),
        "verified_visit": r.get("visit_status") == "visited",
        "center": r.get("center_name"),
        "center_id": r.get("center_id"),
        "waiting_minutes": r.get("waiting_time") if r.get("center_id") != ONLINE_CENTER else None,
        "posted": iso_when(r.get("created_at"), date_only=True),
        "likes": r.get("count_like") or 0,
        "text": clip(r.get("description"), 1000),
    }


def _service(s: dict[str, Any]) -> dict[str, Any]:
    dur = s.get("duration") or ""
    hours = sorted(s.get("hours_of_work") or [], key=lambda h: (ORDER.get(h.get("day"), 9), h.get("from") or ""))
    return {
        "service_id": s.get("id"),
        "title": s.get("alias_title"),
        "price_toman": toman(s.get("free_price")),
        "duration_min": int(dur[:2]) * 60 + int(dur[3:5]) if len(dur) >= 5 and dur[:2].isdigit() else None,
        "bookable": bool(s.get("can_booking")),
        "request_only": bool(s.get("can_request")) and not s.get("can_booking"),
        "hours": [
            f"{WEEKDAYS.get(h.get('day'), h.get('day'))} {(h.get('from') or '')[:5]}-{(h.get('to') or '')[:5]}"
            for h in hours
        ],
    }


def _insurances(data: Any) -> dict[str, Any] | None:
    """biko answers {} when it has no data (not the same as "no insurance")."""
    if not isinstance(data, dict) or not (data.get("base") or data.get("supplement")):
        return None
    return {
        "basic": [i.get("name") for i in data.get("base") or []],
        "supplementary": [i.get("name") for i in data.get("supplement") or []],
    }


def rating_summary(rate: Any) -> dict[str, Any] | None:
    """ravi rate averages (1-5) and counts; None when the doctor has no rating record."""
    if not isinstance(rate, dict) or not rate.get("count_rates"):
        return None
    if rate.get("hide_rates"):
        return {"hidden": True}

    def avg(key: str) -> float | None:
        return round(rate[key], 2) if isinstance(rate.get(key), (int, float)) else None

    return {
        "behaviour": avg("doctor_encounter"),
        "explanation": avg("explanation_of_issue"),
        "treatment": avg("quality_of_treatment"),
        "ratings": rate.get("count_rates"),
        "comments": rate.get("comments_count"),
    }


async def _profile(slug: str) -> dict[str, Any]:
    try:
        body = await fetch(f"{DRPROFILE}/api/full-profile/{quote(slug)}/")
    except ApiError as e:
        if e.status == 404:
            raise ToolError(f"Unknown doctor slug '{slug}'. Find doctors with pz_search_doctors.") from e
        raise
    return (body or {}).get("data") or {}


async def optional(call: Awaitable[Any]) -> Any:
    """Secondary data (rating, insurance, waiting time): a failure or 404 leaves it out instead of failing the tool."""
    try:
        return await call
    except ApiError:
        return None


def _slug(value: str) -> str:
    kind, slug = parse_slug(value)
    if kind != "doctor":
        raise ToolError(f"'{value}' is a center link: call pz_center.")
    return slug


def iso_when(s: Any, date_only: bool = False) -> str | None:
    """ISO time with offset (availability '...T11:00:00.000+03:30', reviews '2026-10-04 14:47:24+00:00') -> Tehran 'YYYY-MM-DD HH:MM'."""
    if not s:
        return None
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(TEHRAN)
    except ValueError:
        return None
    return t.strftime("%Y-%m-%d" if date_only else "%Y-%m-%d %H:%M")
