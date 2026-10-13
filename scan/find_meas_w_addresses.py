#!/usr/bin/env python3
"""
Keep the bills table in sync with parcels paying a Vacant Property Tax.

Process:
1) Re-check parcels already in the DB and remove the ones whose latest bill
   no longer carries a VPT charge (curated properties are unflagged instead).
2) Read the parcel CSV and check every parcel not yet looked up for the
   current bill year; add the ones with a VPT charge.

Lookups are cached in measw_cache.jsonl so a run only hits the tax site for
parcels that are unchecked or stale.
"""
import csv
import sys
import html
import json
import queue
import re
import threading
import time
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
PdfReader = None

BASE_DIR = Path(__file__).resolve().parent
INPUT_CSV = BASE_DIR / "parcels.csv"
LEGACY_INPUT_CSV = BASE_DIR / "Parcels_5567367248157875843.csv"
CACHE_JSONL = BASE_DIR / "measw_cache.jsonl"
# Audit trail of every property removed/unflagged by the scan (deletes are irreversible).
REMOVED_JSONL = BASE_DIR / "vpt_removed.jsonl"

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from geo_utils import derive_latlng  # noqa: E402

BASE_URL = "https://propertytax.alamedacountyca.gov"
ACCOUNT_SUMMARY = BASE_URL + "/account-summary?apn="

# Tax markers by city (substring match on bill HTML)
MEAS_W_MARKER = "MEAS-W OAKLAND VPT"  # Oakland Vacant Property Tax
# Berkeley Measure M - county may use various formats
MEAS_M_MARKERS = [
    "MEAS-M BERKELEY",
    "MEAS-M",
    "MEAS M BERKELEY",
    "MEAS M",
    "BERKELEY VPT",
    "Measure M",
    "MEASURE M",
    "VACANT PROPERTY TAX",
    "VACANT PARCEL TAX",
    "VPT BERKELEY",
]
VPT_MARKERS = [MEAS_W_MARKER] + MEAS_M_MARKERS


# Browser workers. Each needs its own browser context because the tax site
# keeps the "current parcel" in session cookies. More workers do not make a scan
# faster: the county WAF rate-limits per IP (measured 2026-10), so throughput
# is set by LOOKUP_INTERVAL_SEC, not by parallelism.
MAX_WORKERS = int(os.getenv("VPT_MAX_WORKERS", "1"))
# Minimum seconds between lookup starts, across all workers. 3s is rejected
# within 16 lookups; 5s and 8s were rejected within ~130; ~10s (6/min) ran
# 200+ lookups clean.
LOOKUP_INTERVAL_SEC = float(os.getenv("VPT_LOOKUP_INTERVAL_SEC", "10"))
LOOKUP_INTERVAL_MAX_SEC = 60.0
# A cached lookup whose bill is older than the current roll year is re-checked
# after this many days (the new bill may not have been posted yet).
RECHECK_DAYS = int(os.getenv("VPT_RECHECK_DAYS", "30"))
# The county WAF answers "Request Rejected" once the limit is exceeded. Pause,
# slow down for the rest of the run, and give up after repeated rejections.
REJECT_BACKOFF_SEC = 60.0
REJECT_BACKOFF_MAX_SEC = 480.0
MAX_CONSECUTIVE_REJECTIONS = 5
REJECTED_MARKER = "Request Rejected"
BLOCKED_RESOURCE_TYPES = frozenset({"image", "font", "stylesheet", "media"})
# A browser that has run for thousands of lookups degrades (observed on EC2 after ~2,200
# lookups: CPU pegged, traffic collapsed, every lookup timing out). So start a fresh browser
# every RECYCLE_EVERY lookups, and straight away after RECYCLE_AFTER_ERRORS failures in a row.
# Set either to 0 to disable it.
RECYCLE_EVERY = int(os.getenv("VPT_RECYCLE_EVERY", "150"))
RECYCLE_AFTER_ERRORS = int(os.getenv("VPT_RECYCLE_AFTER_ERRORS", "3"))

CHROME_PATH = "/usr/bin/google-chrome"
OUTPUT_DIR = BASE_DIR / "bills"
CDP_URL = os.environ.get("CDP_URL", "").strip()
_stop_requested = False
# Live progress of the current scan, read by the web UI via run_all.get_scan_state()
# and reported by fleet nodes. "scanned" counts parcels that are up to date
# (already cached or looked up in this run) out of "total" in scope; "per_min" is
# this run's lookup speed; "paused_until" is the epoch second the pacer resumes
# after a WAF rejection (0 when not paused).
def _blank_progress() -> dict:
    return {
        "scanned": 0, "total": 0, "per_min": 0.0,
        "added": 0, "removed": 0, "unflagged": 0, "errors": 0,
        "rejections": 0, "paused_until": 0.0, "interval": 0.0, "current_apn": None,
    }


scan_progress = _blank_progress()


def request_stop() -> None:
    """Request the running scanner to stop gracefully."""
    global _stop_requested
    _stop_requested = True


def is_stop_requested() -> bool:
    """Check if stop has been requested."""
    return _stop_requested


def get_browser(playwright_instance, cdp_url: str | None = None):
    """
    Get browser for scanning. Defaults to an isolated headless Chrome/Chromium
    process so it runs quietly in the background without stealing OS focus or
    mouse cursor from the user.
    If CDP_URL is explicitly configured, it connects via CDP.
    Returns (browser, is_cdp).
    """
    url = cdp_url if cdp_url is not None else CDP_URL
    if url:
        try:
            browser = playwright_instance.chromium.connect_over_cdp(url)
            print(f"Connected to Chrome CDP session at {url}")
            return browser, True
        except Exception as e:
            print(f"Could not connect to Chrome CDP at {url} ({e}); using headless browser")

    executable = CHROME_PATH if os.path.exists(CHROME_PATH) else None
    browser = playwright_instance.chromium.launch(
        headless=True,
        executable_path=executable,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    return browser, False


def get_input_csv_path(city: str | None = None) -> Path:
    if city:
        city_slug = re.sub(r"[^a-zA-Z0-9_-]", "", city.strip().lower())
        city_csv = BASE_DIR / f"{city_slug}.csv"
        if city_csv.exists():
            return city_csv

    configured_path = os.environ.get("PARCELS_CSV_PATH", "").strip()
    if configured_path:
        path = Path(configured_path).expanduser()
        if not path.is_absolute():
            path = BASE_DIR / path
        if path.exists():
            return path
    if INPUT_CSV.exists():
        return INPUT_CSV
    oakland_csv = BASE_DIR / "oakland.csv"
    if oakland_csv.exists():
        return oakland_csv
    return LEGACY_INPUT_CSV


def _safe_url(url: str) -> str:
    import urllib.parse
    parts = urllib.parse.urlsplit(url.strip())
    path = urllib.parse.quote(parts.path)
    query = urllib.parse.quote(parts.query, safe="=&?+")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def _is_bill_delinquent(html_text: str, debug_apn: str | None = None) -> bool:
    """
    Detect if bill HTML indicates actual tax delinquency (not boilerplate).
    Uses multiple patterns to match Alameda County bill wording. Set
    VPT_DEBUG_DELINQUENCY=1 to log near-misses (page has keywords but no pattern matched).
    """
    patterns = [
        r'TOTAL\s+REDEMPTION',
        r'REDEMPTION\s+AMOUNT',
        r'REDEMPTION\s+AMOUNT\s+DUE',
        r'REDEMPTION\s+DUE',
        r'PRIOR\s+YEAR\s+TAXES?\s+\$[\d,]+\.\d{2}',
        r'PRIOR\s+YEAR\s+\$[\d,]+\.\d{2}',
        r'PRIOR\s+YEAR.*?\$[\d,]+\.\d{2}',
        r'DELINQUENT\s+\$[\d,]+',
        r'DELINQUENT\s+AMOUNT\s*:\s*\$[\d,]+',
        r'STATUS\s*:\s*DELINQUENT',
        r'TAX\s+DEFAULTED\s+(?:ON\s+)?\d{1,2}/\d{1,2}/\d{2,4}',
        r'DEFAULTED\s+(?:ON\s+)?\d{1,2}/\d{1,2}/\d{2,4}',
        r'PROPERTY\s+(?:HAS\s+)?DEFAULTED',
        r'AMOUNT\s+DUE\s+FOR\s+REDEMPTION',
        r'TAX\s+DEFAULT\s+REDEMPTION',
    ]
    for pat in patterns:
        if re.search(pat, html_text, re.IGNORECASE | re.DOTALL):
            return True
    if debug_apn and os.getenv("VPT_DEBUG_DELINQUENCY", "").strip().lower() in ("1", "true", "yes"):
        text = re.sub(r'\s+', ' ', html_text)[:600]
        low = text.lower()
        if any(kw in low for kw in ("redemption amount", "prior year", "delinquent", "tax defaulted", "defaulted on")):
            print(f"[delinquency debug] APN {debug_apn}: keywords present but no pattern matched. Snippet: {text!r}...")
    return False


def _marker_with_amount(marker: str, bill_html: str) -> str:
    """Append the charge on the marker's row, e.g. 'MEAS-W OAKLAND VPT $6,000.00'."""
    m = re.search(
        re.escape(marker) + r'(?:(?!</tr>).)*?\$\s*([\d,]+\.\d{2})',
        bill_html,
        re.DOTALL | re.IGNORECASE,
    )
    return f"{marker} ${m.group(1)}" if m else marker


def normalize_apn(value: str | None) -> str:
    """
    Canonical APN for comparing the parcel CSV form ('041 413301202') with the
    form printed on bills ('41-4133-12-2'): book/page/parcel/sub zero-padded to
    3/4/3/2 with separators dropped.
    """
    text = (value or "").strip().upper()
    if "-" not in text:
        return re.sub(r"[^A-Z0-9]", "", text)
    parts = [part.strip() for part in text.split("-")]
    if len(parts) > 4:
        return re.sub(r"[^A-Z0-9]", "", text)
    parts += ["0"] * (4 - len(parts))
    out = ""
    for part, width in zip(parts, (3, 4, 3, 2)):
        m = re.fullmatch(r"(\d*)([A-Z]*)", part)
        out += (m.group(1).zfill(width) + m.group(2)) if m else part
    return out


def _bill_parcel_number(bill_html: str) -> str | None:
    m = re.search(r'Parcel Number:</strong>\s*<span[^>]*>\s*([^<]+)', bill_html)
    return m.group(1).strip() if m else None


def _tax_result(status: str = "error", **fields) -> dict:
    """
    Result of one tax lookup. status is 'ok' (latest bill fetched and verified
    to belong to the APN), 'no_bill' (parcel has no bills), 'rejected' (WAF
    block) or 'error'. Only 'ok' results may add or remove a property.
    """
    result = {
        "status": status,
        "has_vpt": False,
        "is_delinquent": False,
        "bill_url": None,
        "roll_year": None,
        "vpt_marker": None,
        "bill_html": "",
    }
    result.update(fields)
    return result


def check_bill_vpt(bill_html: str, city: str | None = None) -> tuple[bool, str | None]:
    """
    Check bill HTML for Oakland Measure W or Berkeley Measure M VPT charges.
    Eliminates false positives from large assessments (e.g. $756,000.00).
    """
    if not bill_html:
        return False, None

    city_upper = (city or "").strip().upper()

    # 1. Direct explicit text markers
    explicit_markers = [
        "MEAS-W OAKLAND VPT",
        "MEAS-W",
        "MEAS-M BERKELEY",
        "MEAS-M",
        "MEAS M BERKELEY",
        "MEAS M",
        "BERKELEY VPT",
        "VACANT PROPERTY TAX",
        "VACANT PARCEL TAX",
        "VPT BERKELEY",
    ]
    for marker in explicit_markers:
        if marker in bill_html:
            return True, _marker_with_amount(marker, bill_html)

    # 2. Check table.fixed-charges specifically for Berkeley Measure M charges
    fixed_charges_match = re.search(
        r'<table[^>]*class=["\'][^"\']*fixed-charges[^"\']*["\'][^>]*>(.*?)</table>',
        bill_html,
        re.DOTALL | re.IGNORECASE,
    )
    if fixed_charges_match:
        table_html = fixed_charges_match.group(1)
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL | re.IGNORECASE)
        for r in rows:
            for marker in explicit_markers:
                if marker in r:
                    return True, _marker_with_amount(marker, r)
            if city_upper == "BERKELEY" or not city_upper:
                if re.search(r'(?<![\d,])\$\s*(?:3,000|6,000)\.00(?!\d)', r):
                    desc_match = re.search(r'<td[^>]*>(.*?)</td>', r, re.DOTALL | re.IGNORECASE)
                    desc = re.sub(r'<[^>]+>', '', desc_match.group(1)).strip() if desc_match else ""
                    if "PARAMEDIC" not in desc.upper():
                        return True, f"BERKELEY VPT {desc} $6,000.00".strip()

    return False, None


def check_property_taxes_with_page(apn: str, page, city: str | None = None) -> dict:
    """
    Check property tax status using an existing Playwright/CDP page.
    """
    clean_apn = re.sub(r"\s+", " ", (apn or "")).strip()
    if not clean_apn:
        return _tax_result()

    try:
        # Navigate to search page if not already there
        if not page.url.startswith("https://propertytax.alamedacountyca.gov/search"):
            page.goto("https://propertytax.alamedacountyca.gov/search", timeout=15000)

        # Switch to Parcel Number search, populate the APN inputs and submit via
        # DOM events (zero mouse/pointer hijacking). Submitting first fetches a
        # CSRF token; that fetch is where the WAF rejects us when it rate-limits.
        with page.expect_response("**/api/csrf-token", timeout=5000) as token_response:
            page.evaluate("""(cleanApn) => {
                const toggle = document.querySelector('#toggleParcel');
                if (toggle) toggle.click();
                const disp = document.querySelector('#displayApn');
                if (disp) {
                    disp.value = cleanApn;
                    disp.dispatchEvent(new Event('input', { bubbles: true }));
                    disp.dispatchEvent(new Event('change', { bubbles: true }));
                }
                const hidden = document.querySelector('#apn');
                if (hidden) {
                    hidden.value = cleanApn;
                }
                const search = document.querySelector('#searchButton');
                if (search) search.click();
            }""", clean_apn)
        if "json" not in (token_response.value.headers.get("content-type") or "").lower():
            return _tax_result("rejected")

        # Wait for account summary or results
        reached_summary = True
        try:
            page.wait_for_url("**/account-summary*", timeout=10000)
        except Exception:
            reached_summary = False

        if reached_summary:
            try:
                page.wait_for_selector('form[id^="view-bill-form"]', state="attached", timeout=6000)
            except Exception:
                pass

        forms = page.query_selector_all('form[id^="view-bill-form"]')
        if not forms:
            summary_html = page.content()
            if REJECTED_MARKER in summary_html:
                return _tax_result("rejected")
            # A summary page with no bills is a real answer; a search that never
            # got there (or is stuck on the bot check) is not.
            if reached_summary and "bobcmn" not in summary_html:
                return _tax_result("no_bill")
            return _tax_result()

        # Select highest rollYear SEC bill (or highest rollYear)
        best_form = forms[0]
        best_year = -1
        for f in forms:
            ry = f.query_selector('input[name="rollYear"]')
            tt = f.query_selector('input[name="taxType"]')
            val = ry.get_attribute("value") if ry else None
            tax_type = tt.get_attribute("value") if tt else ""
            y = int(val) if val and val.isdigit() else 0
            if tax_type == "SEC":
                y += 10000
            if y > best_year:
                best_year = y
                best_form = f

        ry = best_form.query_selector('input[name="rollYear"]')
        val = ry.get_attribute("value") if ry else None
        roll_year = int(val) if val and val.isdigit() else None

        with page.expect_navigation(timeout=10000):
            best_form.evaluate("f => f.submit()")

        bill_url = page.url
        bill_html = page.content()
        if REJECTED_MARKER in bill_html:
            return _tax_result("rejected")

        # The site tracks the parcel in session cookies, so make sure this bill
        # really is the one we asked for before anyone acts on it.
        bill_parcel = _bill_parcel_number(bill_html)
        if normalize_apn(bill_parcel) != normalize_apn(clean_apn):
            print(f"APN {clean_apn}: bill is for parcel {bill_parcel!r}; ignoring result")
            return _tax_result()

        has_vpt, vpt_marker = check_bill_vpt(bill_html, city)
        is_delinquent = _is_bill_delinquent(bill_html, debug_apn=clean_apn)

        return _tax_result(
            "ok",
            has_vpt=has_vpt,
            is_delinquent=is_delinquent,
            bill_url=bill_url,
            roll_year=roll_year,
            vpt_marker=vpt_marker,
            bill_html=bill_html,
        )
    except Exception as exc:
        print(f"Error checking APN {clean_apn}: {exc}")
        return _tax_result()


def check_property_taxes(apn: str, page=None, city: str | None = None) -> dict:
    """
    Check property tax status for an APN.
    Returns dict with: status, has_vpt, is_delinquent, bill_url, roll_year, vpt_marker, bill_html.
    """
    if page is not None:
        return check_property_taxes_with_page(apn, page, city=city)

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser, is_cdp = get_browser(p)
            try:
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                p_page = context.new_page()
                try:
                    return check_property_taxes_with_page(apn, p_page, city=city)
                finally:
                    p_page.close()
            finally:
                browser.close()
    except Exception as exc:
        print(f"Browser check failed for {apn}: {exc}")
        return _tax_result()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def current_roll_year(now: datetime | None = None) -> int:
    """Roll year of the newest secured bills (posted each October). VPT_ROLL_YEAR overrides."""
    override = os.getenv("VPT_ROLL_YEAR", "").strip()
    if override.isdigit():
        return int(override)
    now = now or _now()
    return now.year if now.month >= 10 else now.year - 1


def is_cache_fresh(entry: dict | None, now: datetime | None = None) -> bool:
    """
    True if a cached lookup still answers "what does the latest bill say?".
    Fresh means we saw the current roll year's bill, or we looked recently and
    the newest bill on file was older.
    """
    if not entry:
        return False
    status = entry.get("status")
    if status is None:
        # Legacy entries cached failed lookups as negatives with no bill_url.
        if not entry.get("bill_url"):
            return False
    elif status not in ("ok", "no_bill"):
        return False
    roll_year = entry.get("roll_year")
    if isinstance(roll_year, int) and roll_year >= current_roll_year(now):
        return True
    try:
        checked_at = datetime.fromisoformat(entry.get("checked_at") or "")
    except ValueError:
        return False
    return (now or _now()) - checked_at < timedelta(days=RECHECK_DAYS)


def load_cache() -> dict[str, dict]:
    cache: dict[str, dict] = {}
    if CACHE_JSONL.exists():
        with CACHE_JSONL.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                apn = obj.get("apn")
                if apn:
                    # Support both old and new cache formats
                    cache[apn] = {
                        "has_meas_w": obj.get("has_meas_w", obj.get("has_vpt", False)),
                        "has_vpt": obj.get("has_vpt", obj.get("has_meas_w", False)),
                        "is_delinquent": obj.get("is_delinquent", False),
                        "bill_url": obj.get("bill_url"),
                        "roll_year": obj.get("roll_year"),
                        "vpt_marker": obj.get("vpt_marker"),
                        "status": obj.get("status"),
                        "checked_at": obj.get("checked_at"),
                    }
    return cache


def append_cache(
    apn: str,
    has_vpt: bool,
    is_delinquent: bool,
    bill_url: str | None,
    roll_year: int | None,
    vpt_marker: str | None = None,
    status: str = "ok",
) -> None:
    with CACHE_JSONL.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "apn": apn,
                    "has_meas_w": has_vpt,  # Keep for backward compatibility
                    "has_vpt": has_vpt,
                    "is_delinquent": is_delinquent,
                    "bill_url": bill_url,
                    "roll_year": roll_year,
                    "vpt_marker": vpt_marker,
                    "status": status,
                    "checked_at": _now().isoformat(),
                }
            )
            + "\n"
        )

def pdf_path_for(apn: str, roll_year: int | None) -> Path:
    year = roll_year or "unknown"
    safe_apn = re.sub(r"[^A-Za-z0-9_-]+", "_", apn)
    return OUTPUT_DIR / f"bill_{safe_apn}_{year}.pdf"


def html_to_text(html_text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html_text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_bill_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}

    def grab(prefix: str) -> str | None:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith(prefix):
                return line.replace(prefix, "", 1).strip()
        return None

    parcel = grab("Parcel Number:")
    if parcel:
        fields["parcel_number"] = parcel

    tracer = grab("Tracer Number:")
    if tracer:
        fields["tracer_number"] = tracer

    location = grab("Location of Property:")
    if location:
        fields["location_of_property"] = location

    tax_year = grab("Tax Year:")
    if tax_year:
        fields["tax_year"] = tax_year

    # Last payment date (e.g. "PAID DEC 10, 2025")
    last_payment = None
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^PAID\s+([A-Z]{3}\s+\d{1,2},\s+\d{4})", line)
        if m:
            last_payment = m.group(1)
    if last_payment:
        fields["last_payment"] = last_payment

    # Delinquent indicator: look for explicit "DELINQUENT" (not "delinquency")
    delinquent = False
    for line in text.splitlines():
        upper = line.upper()
        if "DELINQUENT" in upper and "DELINQUENCY" not in upper:
            delinquent = True
            break
    fields["delinquent"] = "1" if delinquent else "0"

    return fields


def extract_bill_fields_from_html(html_text: str, city: str | None = None) -> dict[str, str]:
    fields: dict[str, str] = {}

    # Parcel Number (inside <span class="no-link">)
    m = re.search(r'Parcel Number:</strong>\s*<span[^>]*>\s*([^<]+)', html_text)
    if m:
        fields["parcel_number"] = m.group(1).strip()

    # Tracer Number
    m = re.search(r'Tracer Number:</strong>\s*<span[^>]*>\s*([^<]+)', html_text)
    if m:
        fields["tracer_number"] = m.group(1).strip()

    # Location of Property
    m = re.search(r'Location of Property:</strong>\s*([^<\n]+)', html_text)
    if m:
        fields["location_of_property"] = m.group(1).strip()

    # Tax Year
    m = re.search(r'Tax Year:\s*([0-9]{4}-[0-9]{4})', html_text)
    if m:
        fields["tax_year"] = m.group(1).strip()

    # Last payment date (e.g. "PAID DEC 10, 2025")
    m = re.search(r'PAID\s+([A-Z]{3}\s+\d{1,2},\s+\d{4})', html_text)
    if m:
        fields["last_payment"] = m.group(1)

    # Check for VPT markers (Oakland Measure W & Berkeley Measure M)
    has_vpt, vpt_marker = check_bill_vpt(html_text, city=city)
    fields["has_vpt"] = "1" if has_vpt else "0"
    fields["vpt_marker"] = vpt_marker or ""

    fields["delinquent"] = "1" if _is_bill_delinquent(html_text) else "0"

    return fields


def init_db(conn=None) -> None:
    """No-op: Supabase schema is managed in cloud."""
    pass


def _zip_from_row_json(row_json: str | None) -> str | None:
    """ZIP from a county parcel row; current files use ZIPCODE, older ones SitusZip."""
    if not row_json:
        return None
    try:
        row_data = json.loads(row_json)
    except (TypeError, json.JSONDecodeError):
        return None
    zip_code = str(row_data.get("ZIPCODE") or row_data.get("SitusZip") or "").strip()
    return zip_code or None


def upsert_db(
    apn: str,
    bill_url: str,
    bill_html: str,
    row_json: str | None,
    power_status: str | None = None,
    added_at: str | None = None,
) -> None:
    import db
    city = None
    lat = lng = None
    if row_json:
        try:
            row_data = json.loads(row_json)
            city = row_data.get("CITY", row_data.get("SitusCity", "")).strip().upper()
        except json.JSONDecodeError:
            pass
        # Derive coordinates here rather than in a separate backfill pass: the
        # parcel centroid is already in hand, and bills.lat/lng feed the
        # bills_set_geom trigger that scout_next()'s KNN ordering depends on.
        latlng = derive_latlng(row_json)
        if latlng:
            lat, lng = latlng
        db.upsert_parcel(apn, row_json)

    fields = extract_bill_fields_from_html(bill_html, city=city)
    raw_text = html_to_text(bill_html)
    db.upsert_bill(
        apn=apn,
        pdf_file=None,
        parcel_number=fields.get("parcel_number"),
        tracer_number=fields.get("tracer_number"),
        location_of_property=fields.get("location_of_property"),
        tax_year=fields.get("tax_year"),
        last_payment=fields.get("last_payment"),
        delinquent=int(fields.get("delinquent") or 0),
        raw_text=raw_text,
        bill_url=bill_url,
        power_status=power_status,
        has_vpt=int(fields.get("has_vpt") or 0),
        vpt_marker=fields.get("vpt_marker"),
        city=city,
        lat=lat,
        lng=lng,
        zip_code=_zip_from_row_json(row_json),
        added_at=added_at,
    )
    db.upsert_result(apn, None)


def find_address_for_apn(apn: str) -> str | None:
    with get_input_csv_path().open(newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("APN", "").strip() == apn:
                address = row.get("ADDRESS", row.get("MailingAddress", "")).strip()
                return address or None
    return None


def _load_parcel_rows(csv_path: Path, city: str | None = None) -> tuple[list[str], dict[str, str]]:
    """Unique APNs in file order plus their CSV row as JSON, optionally for one city."""
    apn_order: list[str] = []
    apn_to_rowjson: dict[str, str] = {}
    if not csv_path.exists():
        return apn_order, apn_to_rowjson
    with csv_path.open(newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row_city = row.get("CITY", row.get("SitusCity", "")).strip().upper()
            # Filter by target city if specified and city field is present
            if city and row_city and row_city != city:
                continue
            apn = row.get("APN", "").strip()
            if not apn or apn in apn_to_rowjson:
                continue
            apn_order.append(apn)
            apn_to_rowjson[apn] = json.dumps(row, ensure_ascii=True)
    return apn_order, apn_to_rowjson


def _log_removal(apn: str, action: str, result: dict, db_row: dict) -> None:
    with REMOVED_JSONL.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "apn": apn,
                    "action": action,
                    "location_of_property": db_row.get("location_of_property"),
                    "city": db_row.get("city"),
                    "bill_roll_year": result.get("roll_year"),
                    "was_vpt": bool(db_row.get("has_vpt")),
                    "was_delinquent": bool(db_row.get("delinquent")),
                    "is_delinquent": bool(result.get("is_delinquent")),
                    "at": _now().isoformat(),
                }
            )
            + "\n"
        )


def apply_scan_result(
    apn: str,
    result: dict,
    row_json: str | None,
    db_row: dict | None,
    curated: bool,
    dry_run: bool = False,
    label: str = "ALL",
) -> str:
    """
    Bring the bills table in line with one verified tax lookup.

    db_row is the property's scanner-owned bills row (see
    db.get_bills_for_recheck) or None. Returns what happened: 'added',
    'updated', 'removed', 'unflagged', 'unchanged', 'no_bill' or 'error'.
    """
    status = result.get("status")
    if status != "ok":
        # Unverified lookups never add or remove anything.
        return "no_bill" if status == "no_bill" else "error"

    prefix = f"[{label}]{' (dry run)' if dry_run else ''}"
    if result.get("has_vpt"):
        action = "updated" if db_row else "added"
        if not dry_run:
            upsert_db(apn, result.get("bill_url") or "", result.get("bill_html") or "", row_json)
        if action == "added":
            print(f"{prefix} ADDED {apn}: {result.get('vpt_marker')}")
        return action

    if not db_row:
        return "unchanged"

    where = db_row.get("location_of_property") or "unknown address"
    reason = f"no VPT on {result.get('roll_year') or 'latest'} bill"
    import db
    if curated:
        if not db_row.get("has_vpt"):
            return "unchanged"
        # Someone has worked on this property; deleting would cascade to their
        # lists, scout results and outreach history.
        if not dry_run:
            db.update_bill_fields(
                apn,
                {"has_vpt": 0, "vpt_marker": None, "delinquent": int(bool(result.get("is_delinquent")))},
            )
            _log_removal(apn, "unflagged", result, db_row)
        print(f"{prefix} UNFLAGGED {apn} ({where}): {reason}; kept because it is curated")
        return "unflagged"

    if not dry_run:
        db.bulk_delete_bills([apn])
        _log_removal(apn, "removed", result, db_row)
    print(f"{prefix} REMOVED {apn} ({where}): {reason}")
    return "removed"


class _Pacer:
    """
    Spaces lookups across all workers to stay under the county WAF's limit,
    and pauses everyone (then slows down) when it rejects a request anyway.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._interval = LOOKUP_INTERVAL_SEC
        self._backoff = REJECT_BACKOFF_SEC
        self._next_slot = 0.0
        self._paused_until = 0.0
        self._consecutive = 0
        self.aborted = False

    def rejected(self) -> None:
        with self._lock:
            now = time.time()
            if now < self._paused_until:
                return  # another worker already reported this rejection
            self._consecutive += 1
            if self._consecutive >= MAX_CONSECUTIVE_REJECTIONS:
                scan_progress["rejections"] += 1
                self.aborted = True
                return
            self._interval = min(max(self._interval, 1.0) * 1.25, LOOKUP_INTERVAL_MAX_SEC)
            scan_progress["rejections"] += 1
            scan_progress["interval"] = round(self._interval, 1)
            scan_progress["paused_until"] = now + self._backoff
            print(
                f"Tax site rejected a request; pausing {self._backoff:.0f}s, "
                f"then one lookup every {self._interval:.1f}s"
            )
            self._paused_until = now + self._backoff
            self._next_slot = max(self._next_slot, self._paused_until)
            self._backoff = min(self._backoff * 2, REJECT_BACKOFF_MAX_SEC)

    def ok(self) -> None:
        with self._lock:
            self._consecutive = 0
            self._backoff = REJECT_BACKOFF_SEC

    def wait(self) -> None:
        """Block until this worker's turn to start a lookup."""
        with self._lock:
            slot = max(time.time(), self._next_slot)
            self._next_slot = slot + self._interval
        while not self.aborted and not _stop_requested:
            remaining = slot - time.time()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 1.0))


def _new_scan_context(browser):
    """Isolated context (own session cookies) that skips assets the scan never reads."""
    context = browser.new_context()
    context.route(
        "**/*",
        lambda route: route.abort()
        if route.request.resource_type in BLOCKED_RESOURCE_TYPES
        else route.continue_(),
    )
    return context


_WORKER_DONE = object()


def _needs_new_browser(lookups: int, consecutive_errors: int) -> bool:
    """True once the current browser has run long enough, or is failing lookup after lookup."""
    return (RECYCLE_EVERY > 0 and lookups >= RECYCLE_EVERY) or (
        RECYCLE_AFTER_ERRORS > 0 and consecutive_errors >= RECYCLE_AFTER_ERRORS
    )


def _close_quietly(browser) -> None:
    try:
        browser.close()
    except Exception:
        pass  # a wedged browser may already be gone; the replacement starts regardless


def _scan_worker(tasks: queue.Queue, results: queue.Queue, city: str | None, pacer: _Pacer) -> None:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = context = page = None
            lookups = consecutive_errors = 0
            try:
                while not _stop_requested and not pacer.aborted:
                    if browser is not None and _needs_new_browser(lookups, consecutive_errors):
                        print(
                            f"Restarting the browser after {lookups} lookups "
                            f"({consecutive_errors} failures in a row)",
                            flush=True,
                        )
                        _close_quietly(browser)
                        browser = context = page = None
                        lookups = consecutive_errors = 0
                    try:
                        apn = tasks.get_nowait()
                    except queue.Empty:
                        break
                    pacer.wait()
                    if browser is None:
                        browser, _is_cdp = get_browser(p)
                    if context is None:
                        context = _new_scan_context(browser)
                    if page is None or page.is_closed():
                        page = context.new_page()
                    result = check_property_taxes_with_page(apn, page, city=city)
                    lookups += 1
                    if result["status"] == "rejected":
                        # Retry this parcel after the pause, on a fresh session.
                        pacer.rejected()
                        tasks.put(apn)
                        context.close()
                        context = page = None
                        continue
                    pacer.ok()
                    consecutive_errors = consecutive_errors + 1 if result["status"] == "error" else 0
                    results.put((apn, result))
            finally:
                if browser is not None:
                    _close_quietly(browser)
    except Exception as exc:
        print(f"Scan worker stopped: {exc}")
    finally:
        results.put(_WORKER_DONE)


def scan_apns(apns: list[str], city: str | None, on_result, max_workers: int | None = None) -> bool:
    """
    Look up each APN with paced browser workers. on_result(apn, result) runs
    on the calling thread, so it may write the cache and DB without locks.
    Returns False if the scan gave up because the site kept rejecting requests.
    """
    if not apns:
        return True
    tasks: queue.Queue = queue.Queue()
    for apn in apns:
        tasks.put(apn)
    results: queue.Queue = queue.Queue()
    pacer = _Pacer()
    running = max(1, min(max_workers or MAX_WORKERS, len(apns)))
    for _ in range(running):
        threading.Thread(target=_scan_worker, args=(tasks, results, city, pacer), daemon=True).start()
    while running:
        item = results.get()
        if item is _WORKER_DONE:
            running -= 1
            continue
        on_result(*item)
    return not pacer.aborted


def _scan_and_apply(
    apns: list[str],
    city: str | None,
    row_json_by_apn: dict[str, str],
    db_rows: dict[str, dict],
    curated: set[str],
    dry_run: bool = False,
    scope_total: int | None = None,
) -> Counter:
    """
    Scan APNs and apply each result to the cache and DB. Returns action counts.
    scope_total is the number of parcels the run covers including ones that
    needed no lookup, so progress reads as "parcels up to date / all parcels".
    """
    label = city or "ALL"
    stats: Counter = Counter()
    total = len(apns)
    started = time.time()
    scope_total = max(scope_total or total, total)
    up_to_date = scope_total - total
    scan_progress.update(_blank_progress())
    scan_progress.update(scanned=up_to_date, total=scope_total, interval=LOOKUP_INTERVAL_SEC)

    def on_result(apn: str, result: dict) -> None:
        stats["scanned"] += 1
        if result["status"] in ("ok", "no_bill"):
            stats["done"] += 1     # verified against the county site; failed lookups are retried later
        try:
            action = apply_scan_result(
                apn, result, row_json_by_apn.get(apn), db_rows.get(apn), apn in curated,
                dry_run=dry_run, label=label,
            )
            # Cached only once the DB agrees, so a failed write is retried next run.
            if result["status"] in ("ok", "no_bill"):
                append_cache(
                    apn, result["has_vpt"], result["is_delinquent"], result["bill_url"],
                    result["roll_year"], result["vpt_marker"], status=result["status"],
                )
        except Exception as exc:
            print(f"[{label}] Error applying result for {apn}: {exc}")
            action = "error"
        stats[action] += 1
        per_min = stats["scanned"] * 60 / max(time.time() - started, 0.001)
        scan_progress.update(
            scanned=up_to_date + stats["done"], per_min=round(per_min, 1), current_apn=apn,
            added=stats["added"], removed=stats["removed"], unflagged=stats["unflagged"],
            errors=stats["error"],
        )
        if stats["scanned"] % 25 == 0 or stats["scanned"] == total:
            print(
                f"[{label}] {stats['scanned']}/{total} ({per_min:.1f}/min) "
                f"added {stats['added']}, removed {stats['removed']}, "
                f"unflagged {stats['unflagged']}, errors {stats['error']}"
            )

    if not scan_apns(apns, city, on_result):
        stats["aborted"] = 1
        print(f"[{label}] Stopping: the tax site kept rejecting requests. Try again later or raise VPT_LOOKUP_INTERVAL_SEC.")
    return stats


def recheck_apns(apns: list[str], city: str | None = None, dry_run: bool = False) -> dict[str, int]:
    """Look up specific APNs now (ignoring the cache) and add/remove them as their latest bill says."""
    apns = list(dict.fromkeys(a for a in apns if a))
    if not apns:
        return {}
    import db
    _, apn_to_rowjson = _load_parcel_rows(get_input_csv_path(city=city))
    return dict(_scan_and_apply(apns, city, apn_to_rowjson, db.get_bills_for_recheck(), db.get_curated_apns(), dry_run))


def recheck_cached_positives(dry_run: bool = False) -> dict[str, int]:
    """Re-check cached VPT parcels that are missing from the DB."""
    import db
    in_db = db.get_results_apns()
    apns = [apn for apn, entry in load_cache().items() if entry.get("has_vpt") and apn not in in_db]
    return recheck_apns(apns, dry_run=dry_run)


def run_single_apn(apn: str, dry_run: bool = False) -> None:
    stats = recheck_apns([apn], dry_run=dry_run)
    print(f"APN {apn}: {stats}")


TARGET_CITY: str | None = None  # Set via --city argument


def main(
    city: str | None = None,
    recheck_only: bool = False,
    dry_run: bool = False,
    force: bool = False,
    restrict_to_csv: bool | None = None,
) -> dict[str, int]:
    """
    Sync one city (or every parcel in the CSV) with the tax site. Returns
    action counts (scanned, added, updated, removed, unflagged, error, ...;
    aborted=1 if the site kept rejecting requests).

    restrict_to_csv limits the DB re-check to parcels present in the CSV. Fleet
    nodes set it (or VPT_RESTRICT_DB_TO_CSV=1) because their CSV is only their
    segment of the city and other nodes own the rest.
    """
    global TARGET_CITY, _stop_requested
    _stop_requested = False
    if city:
        TARGET_CITY = city.strip().upper()
    label = TARGET_CITY or "ALL"
    stats: Counter = Counter()

    csv_path = get_input_csv_path(city=TARGET_CITY)
    if not csv_path.exists():
        print(f"Parcel CSV not found at {csv_path}")
        return dict(stats)

    apn_order, apn_to_rowjson = _load_parcel_rows(csv_path, TARGET_CITY)
    if not apn_order:
        print(f"No parcels found for city: {label}")
        return dict(stats)

    cache = load_cache()

    import db
    db_rows = db.get_bills_for_recheck(TARGET_CITY)
    if restrict_to_csv is None:
        restrict_to_csv = os.getenv("VPT_RESTRICT_DB_TO_CSV", "").strip().lower() in ("1", "true", "yes")
    if restrict_to_csv:
        csv_apns = {normalize_apn(apn) for apn in apn_order}
        db_rows = {apn: row for apn, row in db_rows.items() if normalize_apn(apn) in csv_apns}
    elif not TARGET_CITY:
        # Without a city filter the DB also holds other counties' parcels.
        db_rows = {apn: row for apn, row in db_rows.items() if apn in apn_to_rowjson}
    curated = db.get_curated_apns() if db_rows else set()
    # Older DB rows are keyed by the bill's dashed APN form; compare normalised
    # so discovery does not add the same parcel again under the CSV form.
    known_apns = {normalize_apn(apn) for apn in db_rows}

    # 1) Properties already in the DB go first, so lapsed ones are removed early.
    recheck: list[str] = []
    # VPT rows before delinquent-only ones: those are the removals that matter most.
    for apn, row in sorted(db_rows.items(), key=lambda item: not item[1].get("has_vpt")):
        entry = cache.get(apn)
        if not force and is_cache_fresh(entry):
            if entry.get("has_vpt") and row.get("has_vpt"):
                continue
            if not entry.get("has_vpt") and entry.get("status") == "ok":
                # Already verified against the latest bill: no need to ask again.
                stats[apply_scan_result(apn, {**entry, "bill_html": ""}, None, row, apn in curated, dry_run=dry_run, label=label)] += 1
                continue
        recheck.append(apn)

    # 2) Then every parcel not yet looked up for the current bill year.
    discover: list[str] = []
    if not recheck_only:
        discover = [
            apn for apn in apn_order
            if normalize_apn(apn) not in known_apns and (force or not is_cache_fresh(cache.get(apn)))
        ]

    print(f"[{label}] {len(apn_order)} parcels, {len(known_apns)} in DB")
    print(f"  - re-checking {len(recheck)} DB properties; scanning {len(discover)} unchecked/stale parcels")

    # Everything this run covers: the city's parcels plus DB rows keyed differently.
    if recheck_only:
        scope_total = len(known_apns)
    else:
        scope_total = len(known_apns | {normalize_apn(apn) for apn in apn_order})
    stats.update(
        _scan_and_apply(recheck + discover, TARGET_CITY, apn_to_rowjson, db_rows, curated, dry_run, scope_total=scope_total)
    )
    if _stop_requested:
        print(f"[{label}] Stop requested. Ending scan.")

    print(
        f"[{label}] Scan complete{' (dry run)' if dry_run else ''}. Scanned {stats['scanned']}: "
        f"added {stats['added']}, removed {stats['removed']}, unflagged {stats['unflagged']}, "
        f"no bill {stats['no_bill']}, errors {stats['error']}"
    )
    return dict(stats)


def fix_missing_fields() -> None:
    """Re-check scanner-owned DB entries with missing location_of_property."""
    import db
    owned = db.get_bills_for_recheck()
    apns = [apn for apn, _ in db.get_bills_missing_location() if apn in owned]

    if not apns:
        print("No entries with missing fields.")
        return

    print(f"Found {len(apns)} entries with missing fields. Re-checking...")
    stats = recheck_apns(apns)
    print(f"Fixed {stats.get('updated', 0)} entries; removed {stats.get('removed', 0)} without VPT.")


if __name__ == "__main__":
    cli_city = None
    single_apn = None
    mode = None
    flags = {"recheck_only": False, "dry_run": False, "force": False, "restrict_to_csv": False}
    args = [a.strip() for a in sys.argv[1:]]
    while args:
        arg = args.pop(0)
        if arg in ("--backfill-only", "--retry-missing", "--fix-missing"):
            mode = arg
        elif arg in ("--recheck-only", "--dry-run", "--force", "--restrict-to-csv"):
            flags[arg[2:].replace("-", "_")] = True
        elif arg == "--city" and args:
            cli_city = args.pop(0)
        elif arg.startswith("--city="):
            cli_city = arg.split("=", 1)[1].strip()
        else:
            single_apn = arg

    if mode == "--fix-missing":
        fix_missing_fields()
    elif mode:
        print(recheck_cached_positives(dry_run=flags["dry_run"]))
    elif single_apn:
        run_single_apn(single_apn, dry_run=flags["dry_run"])
    else:
        # Unset flag -> None, so VPT_RESTRICT_DB_TO_CSV still applies.
        flags["restrict_to_csv"] = flags["restrict_to_csv"] or None
        main(city=cli_city, **flags)
