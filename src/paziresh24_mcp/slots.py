"""Appointment availability and prices: free days, free slots, payable visit price, public holidays.

Only read-only slot endpoints are used. The slot *hold* endpoints (see CLAUDE.md; tests/test_server.py
fails if their paths appear in the source) reserve a real appointment and must never be called.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Annotated, Any

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .http import GW, fetch, legacy
from .registry import tool
from .search import ONLINE_CENTER, at, day_start, today, toman

CenterId = Annotated[
    str,
    Field(
        pattern=r"^[0-9A-Za-z-]{1,40}$",
        description="center_id from pz_doctor or a pz_search_doctors card, e.g. '291c1bfe-e8f3-414a-a82d-d6fe081683fb'; '5532' = online visit.",
    ),
]
UserCenterId = Annotated[
    str,
    Field(
        pattern=r"^[0-9A-Za-z-]{1,40}$",
        description="user_center_id of the same center (the doctor-at-center id), e.g. '09558bad-168d-4df8-b39d-437a260ca6fc'.",
    ),
]
ServiceId = Annotated[
    str,
    Field(
        pattern=r"^[0-9A-Za-z-]{1,40}$",
        description="service_id of a service at that center, e.g. '1a4a8ec2-fdf9-436e-a32b-8fc4485f609c'.",
    ),
]
JS_WEEKDAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]  # JS getDay()
MAX_SLOT_DAYS = 14
NO_FREE_SLOT = 32


@tool("Free appointment days")
async def pz_free_days(center_id: CenterId, user_center_id: UserCenterId, service_id: ServiceId) -> dict[str, Any]:
    """Days with at least one free appointment for one service of a doctor at one center, over the whole booking window (often 6-8 weeks), with the weekly days off and holidays in that window.

    Read-only (nothing is reserved). Get the ids from pz_doctor. Next: pz_free_slots for the times
    of chosen days; the user books on the doctor's profile URL.
    """
    body = await _free_days(center_id, user_center_id, service_id, with_slots=False)
    cal = body.get("calendar") or {}
    days = sorted(d for d in (_day(t) for t in cal.get("turns") or []) if d)
    first, last = (days[0], days[-1]) if days else (None, None)
    off = str(cal.get("non_workdays") or "")
    return {
        "days": [d.isoformat() for d in days],
        "count": len(days),
        "weekdays_off": [JS_WEEKDAYS[int(n)] for n in off.split(",") if n.strip().isdigit() and int(n) < 7],
        "holidays": _holidays_between(cal.get("holidays"), first, last),
        "online_visit": center_id == ONLINE_CENTER,
    }


@tool("Free appointment slots")
async def pz_free_slots(
    center_id: CenterId,
    user_center_id: UserCenterId,
    service_id: ServiceId,
    date_from: Annotated[
        date | None, Field(description="First day, Gregorian YYYY-MM-DD (default today in Iran), e.g. '2026-10-10'.")
    ] = None,
    date_to: Annotated[
        date | None,
        Field(description=f"Last day, inclusive (default date_from); at most {MAX_SLOT_DAYS} days after date_from."),
    ] = None,
) -> dict[str, Any]:
    """Free appointment start times (Tehran HH:MM) per day for one service of a doctor at one center, for a date range.

    Read-only: this only lists slots; it does not hold or book one. Get the ids from pz_doctor (or a
    search card); find days with pz_free_days. If the range has no free slot, `next_free_day` points
    to the next one. To book, send the user to the doctor's profile URL (booking needs an SMS login).
    """
    start = date_from or today()
    end = date_to or start
    if start.year < 1900:
        raise ToolError("Dates must be Gregorian (YYYY-MM-DD), not Jalali: e.g. 1405-07-18 is 2026-10-10.")
    if end < start:
        raise ToolError("date_to must be on or after date_from.")
    if (end - start).days >= MAX_SLOT_DAYS:
        raise ToolError(f"Ask for at most {MAX_SLOT_DAYS} days at a time; use pz_free_days for the whole window.")
    body = await _free_days(center_id, user_center_id, service_id, with_slots=True)
    lo, hi = day_start(start), day_start(end + timedelta(days=1))
    days, total = [], 0
    result = body.get("result") or {}
    for key in sorted(result if isinstance(result, dict) else {}, key=int):
        if not lo <= int(key) < hi:
            continue
        starts, slice_s = set(), None
        for shift in result[key] or []:
            starts.update(int(t) for t in (shift.get("turns") or {}))
            slice_s = slice_s or shift.get("slice")
        if not starts:
            continue
        times = [at(t).strftime("%H:%M") for t in sorted(starts)]  # type: ignore[union-attr]
        d = _day(key)
        assert d is not None
        days.append(
            {
                "date": d.isoformat(),
                "weekday": d.strftime("%A").lower(),
                "slot_minutes": int(slice_s) // 60 if slice_s else None,
                "slots": times,
            }
        )
        total += len(times)
    out: dict[str, Any] = {
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "total_slots": total,
        "days": days,
    }
    if not days:
        later = [d for d in (_day(t) for t in (body.get("calendar") or {}).get("turns") or []) if d and d > end]
        out["next_free_day"] = min(later).isoformat() if later else None
    return out


@tool("Visit price")
async def pz_visit_price(center_id: CenterId, user_center_id: UserCenterId, service_id: ServiceId) -> dict[str, Any]:
    """Exact price of one service in Toman: the doctor's fee, VAT and the payable total (what the online-visit invoice shows), plus Paziresh24's refund-on-cancel setting.

    Most useful for online visits (center_id '5532'). In-person office visits usually show 0: they are
    paid at the office and the fee is not published.
    """
    price, policy = await asyncio.gather(
        fetch(
            f"{GW}/v1/service-price",
            {"center_id": center_id, "service_id": service_id, "user_center_id": user_center_id},
        ),
        fetch(f"{GW}/payment/v1/cancellation-policy"),
    )
    p = (price or {}).get("result") or {}
    fee = p.get("service_price")
    out = {
        "fee_toman": toman(fee),
        "vat_toman": toman(p.get("vat")),
        "payable_toman": toman(p.get("payable_cost")),
        "automatic_refund_on_cancel": (policy or {}).get("refund_active"),
    }
    if not fee:
        out["note"] = "No online price: in-person visits are usually paid at the office."
    return out


@tool("Public holidays")
async def pz_holidays(
    date_from: Annotated[date, Field(description="First day, Gregorian YYYY-MM-DD, e.g. '2026-10-01'.")],
    date_to: Annotated[
        date, Field(description="Last day, Gregorian YYYY-MM-DD, at most a year later, e.g. '2026-12-31'.")
    ],
) -> dict[str, Any]:
    """Official Iranian public holidays and occasions between two dates (Persian occasion name and the Hijri date).

    Fridays (the weekly day off) are not listed. Use before suggesting appointment days.
    """
    if date_from.year < 1900:
        raise ToolError("Dates must be Gregorian (YYYY-MM-DD), not Jalali.")
    if date_to < date_from or (date_to - date_from).days > 366:
        raise ToolError("date_to must be on or after date_from and at most a year later.")
    data = await fetch(f"{GW}/v1/holidays", {"start_date": date_from.isoformat(), "end_date": date_to.isoformat()})
    return {
        "days": [
            {
                "date": d.get("date"),
                "weekday": date.fromisoformat(d["date"]).strftime("%A").lower() if d.get("date") else None,
                "holiday": bool(d.get("is_holiday")),
                "occasions": [
                    " - ".join(x for x in (e.get("description"), e.get("additional_description")) if x)
                    for e in d.get("events") or []
                ],
            }
            for d in data or []
        ]
    }


async def _free_days(center_id: str, user_center_id: str, service_id: str, *, with_slots: bool) -> dict[str, Any]:
    """POST getFreeDays (form body). Without return_free_turns=false it also lists every free slot of the window."""
    form = {
        "center_id": center_id,
        "service_id": service_id,
        "user_center_id": user_center_id,
        "server_id": "1",
        "return_type": "calendar",
    }
    if not with_slots:
        form["return_free_turns"] = "false"
    body = await fetch(f"{GW}/booking/v2/getFreeDays", method="POST", data=form)
    if isinstance(body, dict) and body.get("status") == NO_FREE_SLOT:
        return {"calendar": {}, "result": {}}
    legacy(body)  # raises on other non-1 statuses (700 = online booking off / wrong service)
    return body


def _day(ts: Any) -> date | None:
    t = at(ts)
    return t.date() if t else None


def _holidays_between(holidays: Any, first: date | None, last: date | None) -> list[dict[str, Any]]:
    if not isinstance(holidays, dict) or not first or not last:
        return []
    out = []
    for key, name in holidays.items():
        d = _day(key)
        if d and first <= d <= last:
            out.append({"date": d.isoformat(), "name": name})
    return sorted(out, key=lambda h: h["date"])
