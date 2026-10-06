"""MCP server entry point: registers every read-only Paziresh24 tool."""

import logging

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from . import __version__, doctor, info, search, slots  # noqa: F401  (imports register the tools)
from .registry import TOOLS

INSTRUCTIONS = """\
Unofficial, read-only access to Paziresh24 (paziresh24.com), Iran's largest doctor appointment site:
doctors, hospitals and clinics, in-person and online visits (phone or messenger). Nothing here can log
in, hold a slot, book, pay or post a review. Booking needs an SMS login, so the user finishes it on the
doctor's profile `url` that every doctor result carries.

Workflow:
1. Words -> slugs: pz_suggest('قلب') or pz_suggest('shiraz') gives the city slug, specialty slug and
   query completions. A pasted link: pz_resolve_url.
2. Search: pz_search_doctors(city='tehran', specialty='cardiovascular', visit_type='online',
   sort='earliest' | 'cheapest_online', gender, degree, text='<name>', page, limit<=50). city='ir' is
   all of Iran (good for online visits and names). centers_only=True lists hospitals and clinics.
3. One doctor: pz_doctor(slug) -> specialties, about, rating, insurances, online visit, every center
   with its services and the ids center_id + user_center_id + service_id. pz_reviews(slug) for
   patient reviews and the rating / waiting-time summary.
4. When: pz_first_available(slug) for the nearest slot; pz_free_days(ids) for the days with free
   slots; pz_free_slots(ids, date_from, date_to) for the times. Online visits use center_id '5532'.
5. Cost: pz_visit_price(ids) gives fee + VAT = payable in Toman (online visits; office visits are
   usually paid at the office and show 0). Then send the user to the doctor's `url` to book.
Hospitals: pz_center(slug) (departments, doctors) and pz_search_doctors(center_id=...) for its doctors
by nearest free time. Holidays: pz_holidays. Site rules (cancel, refund, online visit): pz_faq.

Conventions: prices are Toman (the API is Rial and the server divides by 10). Dates in and out are
Gregorian YYYY-MM-DD (convert Jalali first: 1405-07-18 = 2026-10-10); times are Tehran local HH:MM.
Doctors are identified by their Persian slug ('دکتر-هدیه-جباری-0'). There is no insurance filter:
check each card's `insurances`; pz_doctor `insurances: null` means no data, not "no insurance".
Ratings are 1-5. Persian queries match best. Search results include sponsored doctors.
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)

mcp = MCPServer(
    "paziresh24-mcp",
    title="Paziresh24",
    instructions=INSTRUCTIONS,
    version=__version__,
    website_url="https://github.com/sepehr071/paziresh24-mcp",
)

for fn, title in TOOLS:
    mcp.add_tool(fn, title=title, annotations=READ_ONLY)


def main() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per request floods client logs
    mcp.run()


if __name__ == "__main__":
    main()
