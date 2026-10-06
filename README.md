<!-- mcp-name: io.github.sepehr071/paziresh24-mcp -->

<div align="center">

<img src="https://raw.githubusercontent.com/sepehr071/paziresh24-mcp/main/.github/banner.png" alt="paziresh24-mcp: let your AI agent find a doctor and a free appointment on Paziresh24" width="100%">

# 🩺 paziresh24-mcp

**Let your AI agent find the right doctor on Paziresh24.**<br>
Search doctors by specialty, city, name and online visit, read profiles, prices, insurances and patient reviews,<br>
and see the free appointment times, all from Claude, Cursor or Copilot.

[![PyPI](https://img.shields.io/pypi/v/paziresh24-mcp?color=2563eb)](https://pypi.org/project/paziresh24-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/paziresh24-mcp)](https://pypi.org/project/paziresh24-mcp/)
[![CI](https://github.com/sepehr071/paziresh24-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/sepehr071/paziresh24-mcp/actions/workflows/ci.yml)
[![MCP Registry](https://img.shields.io/badge/MCP_Registry-io.github.sepehr071%2Fpaziresh24--mcp-7c3aed)](https://registry.modelcontextprotocol.io/?q=paziresh24-mcp)
[![License: MIT](https://img.shields.io/badge/license-MIT-16a34a)](https://github.com/sepehr071/paziresh24-mcp/blob/main/LICENSE)

[![Install in Cursor](https://cursor.com/deeplink/mcp-install-dark.svg)](https://cursor.com/en/install-mcp?name=paziresh24&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyJwYXppcmVzaDI0LW1jcCJdfQ==)
[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_paziresh24--mcp-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=paziresh24&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22paziresh24-mcp%22%5D%7D)

[Quick start](#quick-start) · [What it can do](#what-it-can-do) · [Tools](#tools) · [FAQ](#faq) · [فارسی](#فارسی)

</div>

---

## Why

Paziresh24 (paziresh24.com) lists tens of thousands of Iranian doctors, hospitals and clinics, with in-person
booking and online visits by phone or messenger. Picking one means comparing specialties, ratings, insurance,
prices and who has a free slot soon. An agent with `paziresh24-mcp` reads the same data the site shows and does
the comparing for you, then gives you the profile link to book:

> **You:** A female cardiologist in Tehran with an online visit today, not too expensive?
>
> **Agent:** *calls* `pz_search_doctors(city="tehran", specialty="cardiovascular", visit_type="online", gender="female", sort="earliest")` → `pz_doctor(slug="دکتر-هدیه-جباری-0")` → `pz_free_slots(center_id="5532", ...)` → `pz_visit_price(center_id="5532", ...)`
>
> Dr. Hedieh Jabbari (cardiologist, 5.0 from 11 ratings) has online visits over WhatsApp today from 15:00
> (10-minute slots: 15:00, 15:10, 15:20, ...). The visit is **500,000 Toman + 15,000 VAT = 515,000 Toman**.
> Book it here: https://www.paziresh24.com/dr/دکتر-هدیه-جباری-0/

<sub>Real tool output from 2026-10-06; slots and prices change all the time. Prices are in Toman.</sub>

## What it can do

- 🔎 **Search doctors** by city, specialty, name, online visit, gender, degree and work time, sorted by best, nearest free time or cheapest online visit
- 🏷️ **Resolve words**: Persian or English city and specialty names to search slugs, plus the site's type-ahead
- 👩‍⚕️ **Read a profile**: specialties, about text, medical council code, offices and hospitals with address, phone and map, services, weekly hours, insurance contracts, online visit channels
- 📅 **Find a time**: the earliest free appointment, the days with free slots, and the free times of each day (in person and online)
- 💳 **Know the cost**: online visit fee, VAT and the payable total in Toman
- ⭐ **Check reviews**: rating averages, average waiting time and paged patient reviews (no reviewer names)
- 🏥 **Hospitals and clinics**: departments, doctors, phone, website, map
- 🔒 **Read-only by design**: no login, no slot holds, no booking, no payment

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/).

<details open>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add paziresh24 -- uvx paziresh24-mcp
```
</details>

<details>
<summary><b>Claude Desktop</b></summary>

Settings → Developer → Edit Config, then add:

```json
{
  "mcpServers": {
    "paziresh24": { "command": "uvx", "args": ["paziresh24-mcp"] }
  }
}
```
</details>

<details>
<summary><b>Cursor</b></summary>

Click **Install in Cursor** above, or add the Claude Desktop block to `~/.cursor/mcp.json`.
</details>

<details>
<summary><b>VS Code (Copilot agent mode)</b></summary>

Click **Install in VS Code** above, or add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "paziresh24": { "type": "stdio", "command": "uvx", "args": ["paziresh24-mcp"] }
  }
}
```
</details>

<details>
<summary><b>Anything else</b></summary>

It's a standard stdio MCP server: run `uvx paziresh24-mcp`, or `pip install paziresh24-mcp` and run `paziresh24-mcp`.
</details>

Then just ask:

- "A dermatologist in Shiraz who accepts Tamin Ejtemaei and has a free slot this week."
- "Cheapest online pediatrician visit available today?"
- "What do patients say about this doctor? https://www.paziresh24.com/dr/..."
- "Which cardiologists work at Treata hospital, and who is free soonest?"
- <span dir="rtl">چطور نوبت ویزیت آنلاین را لغو کنم و پولم برمی&zwnj;گردد؟</span>

## How it works

```text
  AI agent  (Claude, Cursor, Copilot, ...)
      │
      │  MCP over stdio
      ▼
  paziresh24-mcp  (runs on your machine)
      │
      │  HTTPS (JSON)
      ├──────▶  apigw.paziresh24.com      (search, free days and slots, prices, holidays)
      ├──────▶  drprofile.paziresh24.com  (doctor profiles)
      ├──────▶  ravi-apis.paziresh24.com  (reviews and ratings)
      ├──────▶  saman / biko.paziresh24.com (availability, insurance)
      └──────▶  www.paziresh24.com        (cities, specialties, hospitals, FAQ)
```

`paziresh24-mcp` runs locally and calls the same public endpoints the Paziresh24 website uses.
There's no hosted server in between, no API key, and nothing about you is sent anywhere else.

## Tools

Doctors are identified by their Persian slug, the part after `/dr/` in the profile URL
(`دکتر-هدیه-جباری-0`); slot and price tools take the `center_id`, `user_center_id` and `service_id`
that search results and `pz_doctor` return.

<details open>
<summary><b>🔎 Find</b> (4)</summary>

| Tool | What it does |
|---|---|
| `pz_suggest` | Persian or English words → city slugs, specialty slugs and query completions |
| `pz_search_doctors` | Doctors (or hospitals and clinics) by city, specialty, name, online visit, gender, degree, with sorting and paging |
| `pz_resolve_url` | A paziresh24.com link → doctor or center slug |
| `pz_center` | Hospital or clinic: address, phone, website, departments and doctors |
</details>

<details open>
<summary><b>👩‍⚕️ One doctor</b> (3)</summary>

| Tool | What it does |
|---|---|
| `pz_doctor` | Full profile: specialties, about, rating, insurances, online visit, centers, services, prices, weekly hours |
| `pz_first_available` | Earliest free in-person and online slot, per center |
| `pz_reviews` | Patient reviews (paged) with rating averages and waiting time per center |
</details>

<details open>
<summary><b>📅 Times and prices</b> (4)</summary>

| Tool | What it does |
|---|---|
| `pz_free_days` | Days with free slots over the whole booking window, days off and holidays |
| `pz_free_slots` | Free appointment times per day for a date range |
| `pz_visit_price` | Fee, VAT and payable total in Toman, and the refund-on-cancel setting |
| `pz_holidays` | Official Iranian holidays between two dates |
</details>

<details open>
<summary><b>ℹ️ Help</b> (1)</summary>

| Tool | What it does |
|---|---|
| `pz_faq` | Paziresh24's official FAQ: booking, cancelling, online visits, refunds |
</details>

All 12 tools are annotated `readOnlyHint: true` and return compact structured JSON, so they don't flood the agent's context.

## Good to know

- **Prices are in Toman** (1 Toman = 10 Rial). The APIs answer in Rial; the server divides by 10.
- **In-person office visits usually show price 0**: they are paid at the office and the fee is not published. Online visits have a real fee; `pz_visit_price` adds the VAT (3% in tests).
- **Times are Tehran local** (`HH:MM`), dates Gregorian `YYYY-MM-DD` (1405-07-18 = 2026-10-10).
- **Online visits** live in Paziresh24's virtual center `5532` (phone call or messenger such as WhatsApp; see `channels`).
- **Insurance:** search cards list accepted insurance names, but there is no insurance filter. `insurances: null` in `pz_doctor` means no data, not "no insurance".
- **Centers with booking off** stay in `pz_doctor` with `booking_off: true` (call their phone); `booking_opens` means the booking period has ended and new slots open at that time. Search cards give the first free time per center as well as the doctor's earliest overall.
- **Booking is not possible here.** Booking needs an SMS login, so the agent gives you the doctor's profile URL. Free slots are listed, never held.
- `sort="earliest"` drops doctors without a free slot. The order follows the site's search API; the sponsored cards on the website were not seen in it.
- **Checked against the website:** on 2026-10-06 every tool's output was compared with the live paziresh24.com pages (names, prices, first free times, review order, search totals). Free days and slots were compared only where the site shows them without holding a slot.

## FAQ

<details>
<summary><b>Can it book an appointment for me?</b></summary>

No, and that's deliberate. It never calls login, slot-hold, booking, payment or review endpoints (a test fails if
their paths appear in the code). The agent finds the doctor and the time; you book on the profile page.
</details>

<details>
<summary><b>Do I need an Iranian IP?</b></summary>

No geo block was seen: direct calls and calls through a proxy in Turkey both worked (2026-10-06). If your network
blocks the site, set `PAZIRESH24_MCP_PROXY`.
</details>

<details>
<summary><b>A tool says the server "did not answer in time"</b></summary>

Some Paziresh24 gateway routes sometimes hang for 20-30 seconds. The server retries once; if it still fails, try
again in a minute.
</details>

<details>
<summary><b>Claude Desktop says <code>uvx</code> is not found</b></summary>

Use the full path to `uvx` (`where uvx` on Windows, `which uvx` on macOS/Linux) as `command`.
</details>

<details>
<summary><b>How do I debug what the agent sees?</b></summary>

```bash
npx @modelcontextprotocol/inspector uvx paziresh24-mcp
```
</details>

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PAZIRESH24_MCP_PROXY` | unset | HTTP proxy for every request, e.g. `http://user:pass@host:port` |

## فارسی

<div dir="rtl">

**paziresh24-mcp** به دستیار هوش مصنوعی شما (Claude، Cursor، Copilot و ...) اجازه می&zwnj;دهد در پذیرش۲۴ پزشک
پیدا کند: جستجو بر اساس تخصص، شهر، نام و ویزیت آنلاین، خواندن پروفایل، بیمه&zwnj;ها، قیمت و نظرات بیماران، و دیدن
زمان&zwnj;های خالی نوبت.

- فقط خواندنی است: وارد حساب نمی&zwnj;شود، نوبت نگه نمی&zwnj;دارد، نوبت ثبت نمی&zwnj;کند و پرداخت نمی&zwnj;کند.
- برای گرفتن نوبت، لینک صفحه پزشک را به شما می&zwnj;دهد.
- همه قیمت&zwnj;ها به تومان است.
- روی سیستم خود شما اجرا می&zwnj;شود و به هیچ سرور واسطی داده نمی&zwnj;فرستد.

**نصب در Claude Code:**

</div>

```bash
claude mcp add paziresh24 -- uvx paziresh24-mcp
```

<div dir="rtl">

بعد بپرسید: «یک متخصص پوست خانم در تهران که امروز ویزیت آنلاین دارد و گران نیست؟»

</div>

## Development

```bash
git clone https://github.com/sepehr071/paziresh24-mcp && cd paziresh24-mcp
uv sync
uv run pytest            # offline, against recorded responses
uv run pytest -m live    # real APIs
uv run ruff check .
```

Tools live in `src/paziresh24_mcp/search.py`, `doctor.py`, `slots.py` and `info.py`; each is a typed async
function with a docstring that tells the agent when to use it. Issues and PRs are welcome, especially new tools and
fixes for API changes.

Releases: bump the version in `pyproject.toml` and `server.json`, then push a `v*` tag. GitHub Actions tests,
publishes to PyPI and the [MCP Registry](https://registry.modelcontextprotocol.io), and creates the GitHub Release.

## Disclaimer

Unofficial and not affiliated with or endorsed by Paziresh24. It uses the public endpoints of the paziresh24.com
website, which can change without notice. It is not medical advice. Please keep request rates reasonable.

## License

[MIT](https://github.com/sepehr071/paziresh24-mcp/blob/main/LICENSE)
