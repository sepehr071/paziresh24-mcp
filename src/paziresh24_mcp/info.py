"""Help content: the official Paziresh24 FAQ (booking, cancelling, online visits, refunds)."""

from __future__ import annotations

import json
import re
from typing import Annotated, Any

from pydantic import Field

from .http import WWW, ApiError, fetch_cached
from .registry import tool
from .search import clip, html_text, norm


@tool("Paziresh24 FAQ")
async def pz_faq(
    query: Annotated[
        str | None,
        Field(
            max_length=100,
            description="Topic words in Persian, e.g. 'لغو نوبت' (cancel), 'استرداد' (refund), 'ویزیت آنلاین' (online visit). Empty = list every question.",
        ),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=50, description="Max answers.")] = 5,
) -> dict[str, Any]:
    """Answers from the official Paziresh24 FAQ: how booking works, cancelling or moving an appointment, online visits (channels, prescriptions), payment and refunds.

    Without a query it lists the questions only. Use for "how do I ..." questions about the site.
    """
    page = await fetch_cached(f"{WWW}/faq/", text=True)
    items = []
    for block in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
        try:
            doc = json.loads(block)
        except ValueError:
            continue
        if isinstance(doc, dict) and doc.get("@type") == "FAQPage":
            items = [
                (q.get("name") or "", html_text((q.get("acceptedAnswer") or {}).get("text")))
                for q in doc.get("mainEntity") or []
            ]
    if not items:
        raise ApiError("The FAQ page has changed: no FAQ data found on paziresh24.com/faq/.")
    if not query or not query.strip():
        return {"total": len(items), "questions": [q for q, _ in items]}
    words = [w for w in norm(query).split() if len(w) > 1]
    scored = []
    for i, (q, a) in enumerate(items):
        hay_q, hay_a = norm(q), norm(a)
        score = sum(2 * (w in hay_q) + (w in hay_a) for w in words)
        if score:
            scored.append((-score, i, q, a))
    scored.sort()
    return {
        "total": len(scored),
        "answers": [{"question": q, "answer": clip(a, 1500)} for _, _, q, a in scored[:limit]],
    }
