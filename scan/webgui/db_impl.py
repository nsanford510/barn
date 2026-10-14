#!/usr/bin/env python3
# pyre-ignore-all-errors
"""
Supabase-only database layer for BARN-scan.
Replaces all SQLite usage with Supabase client.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent  # webgui

# geo_utils lives one level up in scan/. BASE_DIR here is webgui/, so add the
# parent explicitly rather than relying on however this module got imported.
_SCAN_DIR = BASE_DIR.parent
if str(_SCAN_DIR) not in sys.path:
    sys.path.insert(0, str(_SCAN_DIR))

from geo_utils import derive_latlng  # noqa: E402

ENV_FILE = BASE_DIR / ".env"
if ENV_FILE.exists():
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                if key not in os.environ:
                    os.environ[key] = value.strip('"').strip("'")

try:
    from supabase import create_client, Client
    _client: Client | None = None
except ImportError:
    create_client = None
    Client = None
    _client = None

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = (
    os.environ.get("SUPABASE_SECRET_KEY")
    or os.environ.get("SUPABASE_SERVICE_KEY")
    or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    or os.environ.get("SUPABASE_PUBLISHABLE_KEY")
    or os.environ.get("SUPABASE_ANON_KEY", "")
)


def get_client() -> Any:
    """Get or create Supabase client. Raises if not configured."""
    global _client
    if not create_client:
        raise RuntimeError("supabase package not installed. Run: pip install supabase")
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_ANON_KEY (or SUPABASE_SERVICE_KEY) must be set in .env")
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_KEY.strip())
    return _client


# ---------------------------------------------------------------------------
# Parcels (Supabase parcels table: lowercase apn PK + row_json jsonb)
# ---------------------------------------------------------------------------

def upsert_parcel(apn: str, row_json: str | dict | None) -> None:
    row = row_json
    if isinstance(row, str):
        try:
            row = json.loads(row) if row else None
        except json.JSONDecodeError:
            row = None
    get_client().table("parcels").upsert({"apn": apn, "row_json": row}).execute()


# ---------------------------------------------------------------------------
# Bills (primary key apn)
# ---------------------------------------------------------------------------

def upsert_bill(
    apn: str,
    pdf_file: str | None = None,
    parcel_number: str | None = None,
    tracer_number: str | None = None,
    location_of_property: str | None = None,
    tax_year: str | None = None,
    last_payment: str | None = None,
    delinquent: int = 0,
    raw_text: str | None = None,
    bill_url: str | None = None,
    power_status: str | None = None,
    has_vpt: int = 0,
    vpt_marker: str | None = None,
    city: str | None = None,
    condition_score: float | None = None,
    condition_notes: str | None = None,
    condition_updated_at: str | None = None,
    streetview_image_path: str | None = None,
    research_status: str | None = None,
    research_report_path: str | None = None,
    research_updated_at: str | None = None,
    zip_code: str | None = None,
    added_at: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "apn": apn,
        "pdf_file": pdf_file or "",
        "parcel_number": parcel_number,
        "tracer_number": tracer_number,
        "location_of_property": location_of_property,
        "tax_year": tax_year,
        "last_payment": last_payment,
        "delinquent": delinquent or 0,
        "raw_text": raw_text,
        "bill_url": bill_url,
        "power_status": power_status,
        "has_vpt": has_vpt or 0,
        "vpt_marker": vpt_marker,
        "city": city,
        "condition_score": condition_score,
        "condition_notes": condition_notes,
        "condition_updated_at": condition_updated_at,
        "streetview_image_path": streetview_image_path,
        "research_status": research_status,
        "research_report_path": research_report_path,
        "research_updated_at": research_updated_at,
        "zip_code": zip_code,
        "added_at": added_at,
    }
    payload = {k: v for k, v in payload.items() if v is not None or k == "apn"}
    if "pdf_file" not in payload or payload.get("pdf_file") is None:
        payload["pdf_file"] = ""
    get_client().table("bills").upsert(payload).execute()


def update_bill_power_status(apn: str, power_status: str) -> None:
    get_client().table("bills").update({"power_status": power_status}).eq("apn", apn).execute()


def update_bill_research(apn: str, research_status: str, research_report_path: str | None = None, research_updated_at: str | None = None) -> None:
    data: dict[str, Any] = {"research_status": research_status}
    if research_report_path is not None:
        data["research_report_path"] = research_report_path
    if research_updated_at is not None:
        data["research_updated_at"] = research_updated_at
    get_client().table("bills").update(data).eq("apn", apn).execute()


def update_bill_condition(apn: str, condition_score: float, condition_notes: str, condition_updated_at: str | None = None, streetview_image_path: str | None = None) -> None:
    data: dict[str, Any] = {"condition_score": condition_score, "condition_notes": condition_notes}
    if condition_updated_at is not None:
        data["condition_updated_at"] = condition_updated_at
    if streetview_image_path is not None:
        data["streetview_image_path"] = streetview_image_path
    get_client().table("bills").update(data).eq("apn", apn).execute()


def update_property_notes(apn: str, important_notes: str) -> None:
    data: dict[str, Any] = {"important_notes": important_notes}
    get_client().table("properties").update(data).eq("apn", apn).execute()


def update_bill_fields(apn: str, fields: dict[str, Any]) -> None:
    """Update arbitrary allowed fields on a bills row."""
    if not fields:
        return
    get_client().table("bills").update(fields).eq("apn", apn).execute()


def update_bill_owner_contact(
    apn: str,
    owner_contact_status: str,
    owner_email: str | None = None,
    owner_phone: str | None = None,
    owner_contact_updated_at: str | None = None,
    tenant_verified: bool | None = None,
    prop_ownership_type: str | None = None,
    prop_last_sale_date: str | None = None,
    prop_occupancy_type: str | None = None,
    owner_mobile_phone: str | None = None,
    owner_details_url: str | None = None,
    property_search_url: str | None = None,
    mailing_search_url: str | None = None,
) -> None:
    data: dict[str, Any] = {"owner_contact_status": owner_contact_status}
    if owner_email is not None:
        data["owner_email"] = owner_email
    if owner_phone is not None:
        data["owner_phone"] = owner_phone
    if owner_contact_updated_at is not None:
        data["owner_contact_updated_at"] = owner_contact_updated_at
    if tenant_verified is not None:
        data["tenant_verified"] = tenant_verified
    if prop_ownership_type is not None:
        data["prop_ownership_type"] = prop_ownership_type
    if prop_last_sale_date is not None:
        data["prop_last_sale_date"] = prop_last_sale_date
    if prop_occupancy_type is not None:
        data["prop_occupancy_type"] = prop_occupancy_type
    if owner_mobile_phone is not None:
        data["owner_mobile_phone"] = owner_mobile_phone
    if owner_details_url is not None:
        data["owner_details_url"] = owner_details_url
    if property_search_url is not None:
        data["property_search_url"] = property_search_url
    if mailing_search_url is not None:
        data["mailing_search_url"] = mailing_search_url
    get_client().table("bills").update(data).eq("apn", apn).execute()


def get_bills_missing_owner_contact() -> list[str]:
    """Return APNs where owner_contact_status is null (not yet scanned)."""
    r = get_client().table("bills").select("apn").is_("owner_contact_status", "null").execute()
    return [row["apn"] for row in (r.data or []) if row.get("apn")]


def get_bill(apn: str) -> dict[str, Any] | None:
    r = get_client().table("bills").select("*").eq("apn", apn).limit(1).execute()
    if r.data and len(r.data) > 0:
        return r.data[0]
    return None


def get_distinct_zips() -> list[str]:
    """Fetch distinct zip codes, the values the /search zip filter matches.

    bills.zip_code is the canonical field (scanner + backfill); situs_zip is
    the legacy parcel-derived column.
    """
    zips: set[str] = set()
    offset = 0
    page_size = 1000
    while True:
        r = (
            get_client()
            .table("map_markers")
            .select("zip_code,situs_zip")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        rows = r.data or []
        for row in rows:
            for key in ("zip_code", "situs_zip"):
                z = str(row.get(key) or "").strip()
                if z:
                    zips.add(z)
        if len(rows) < page_size:
            break
        offset += page_size
    return sorted(zips)


def get_distinct_cities() -> list[str]:
    """Fetch distinct normalized city names from bills."""
    cities: set[str] = set()
    offset = 0
    page_size = 1000
    while True:
        r = (
            get_client()
            .table("bills")
            .select("city")
            .not_.is_("city", "null")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        rows = r.data or []
        for row in rows:
            city = str(row.get("city") or "").strip().lstrip("_ ").upper()
            if city:
                cities.add(city)
        if len(rows) < page_size:
            break
        offset += page_size
    return sorted(cities)


_CONTACT_FILTER_COLUMNS = (
    "prop_occupancy_type",
    "prop_ownership_type",
    "primary_resident_age",
    "deceased_count",
)

_CONTACT_ENRICH_COLUMNS = (
    "prop_occupancy_type",
    "prop_ownership_type",
    "prop_last_sale_date",
    "primary_resident_name",
    "primary_resident_age",
    "owner_mobile_phone",
    "deceased_count",
    "important_notes",
)

_RPC_SORT_COLUMNS = {
    "location_of_property",
    "apn",
    "parcel_number",
    "tracer_number",
    "tax_year",
    "pdf_file",
    "situs_zip",
    "last_payment",
    "delinquent",
    "power_status",
    "city",
    "has_vpt",
    "condition_score",
}


_CONDITION_BUCKETS = {
    # Thresholds carried over verbatim from the retired get_bills_filtered RPC
    # so the UI's "good / fair / poor" keep meaning the same thing.
    "good": [("condition_score", "gte.7")],
    "fair": [("condition_score", "gte.4"), ("condition_score", "lt.7")],
    "poor": [("condition_score", "lt.4")],
    "unscored": [("condition_score", "is.null")],
}

# Some research writers store the short form ("vacant"), so match both spellings.
_AI_VERDICT_ALIASES = {
    "likely_vacant": ["likely_vacant", "vacant"],
    "likely_occupied": ["likely_occupied", "occupied"],
    "unknown": ["unknown"],
}


def _parse_confidence_min(value: Any) -> float | None:
    """Parse a minimum AI vacancy confidence; accepts 0-1 or a 0-100 percent."""
    try:
        n = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if n > 1:
        n /= 100
    return n if 0 < n <= 1 else None


# map_markers column -> the bills column name callers already use.
_VIEW_TO_BILL_KEYS = {
    "location": "location_of_property",
    "lat": "lat",
    "lng": "lng",
}


def _view_row_to_bill_row(row: dict[str, Any]) -> dict[str, Any]:
    """Reshape a map_markers row into the bills-shaped dict callers expect.

    Six Flask routes consume these rows and index them by bills column names,
    so the view's renames are undone here rather than at each call site. The
    synthesized row_json carries only the parcel fields the templates read; the
    full blob is no longer fetched, since coordinates now come from lat/lng.
    """
    out = dict(row)
    for view_key, bill_key in _VIEW_TO_BILL_KEYS.items():
        if view_key in out and view_key != bill_key:
            out[bill_key] = out.pop(view_key)
    out["row_json"] = {
        "MailingAddress": row.get("mailing_address"),
        "SitusAddress": row.get("situs_address"),
        "SitusZip": row.get("situs_zip"),
        "SitusCity": row.get("city"),
    }
    return out


def get_bills_with_parcels_filtered(
    q: str = "",
    zip_filter: str = "",
    power_filter: str = "",
    fav_filter: str = "",
    city_filter: str = "",
    vpt_filter: str = "",
    delinquent_filter: str = "",
    condition_filter: str = "",
    outofstate_filter: str = "",
    research_filter: str = "",
    owner_name_filter: str = "",
    new_filter: str = "",
    added_after: str = "",
    added_before: str = "",
    ai_verdict_filter: str = "",
    ai_confidence_min: str = "",
    site_vacant_filter: str = "",
    sort: str = "location_of_property",
    order: str = "asc",
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[dict], int]:
    """Returns (rows, total), reading the map_markers view.

    Replaces the get_bills_filtered RPC, which took 15 scalar parameters to
    reimplement filtering PostgREST already does, and which the 2026-08-07
    baseline schema dropped.
    """
    query = get_client().table("map_markers").select("*", count="exact")

    term = (q or "").strip()
    if term:
        query = query.or_(f"apn.ilike.*{term}*,location.ilike.*{term}*")
    city = (city_filter or "").strip().upper()
    if city:
        query = query.eq("city", city)
    zips = [z.strip() for z in (zip_filter or "").split(",") if z.strip()]
    if zips:
        zip_values = ",".join(z for z in zips if re.fullmatch(r"\d{5}", z))
        if zip_values:
            query = query.or_(f"zip_code.in.({zip_values}),situs_zip.in.({zip_values})")
    power = (power_filter or "").strip()
    if power:
        query = query.eq("power_status", power)
    research = (research_filter or "").strip()
    if research:
        query = query.eq("research_status", research)
    owner = (owner_name_filter or "").strip()
    if owner:
        query = query.ilike("owner_name", f"*{owner}*")
    if (vpt_filter or "").strip() == "1":
        query = query.eq("has_vpt", 1)
    if (delinquent_filter or "").strip() == "1":
        query = query.eq("delinquent", 1)
    if (fav_filter or "").strip() == "1":
        query = query.is_("is_favorite", True)
    if (outofstate_filter or "").strip() == "1":
        query = query.is_("is_out_of_state", True)
    if (new_filter or "").strip() == "1":
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        query = query.gte("added_at", cutoff)
    # Exact date-added range (UTC ISO timestamps): "added_at >= after" and "added_at < before".
    if (added_after or "").strip():
        query = query.gte("added_at", added_after.strip())
    if (added_before or "").strip():
        query = query.lt("added_at", added_before.strip())
    verdict = (ai_verdict_filter or "").strip().lower()
    if verdict == "none":
        query = query.is_("ai_vacancy_verdict", None)
    elif verdict in _AI_VERDICT_ALIASES:
        query = query.in_("ai_vacancy_verdict", _AI_VERDICT_ALIASES[verdict])
    confidence = _parse_confidence_min(ai_confidence_min)
    if confidence is not None:
        query = query.gte("ai_vacancy_confidence", confidence)
    site_vacant = (site_vacant_filter or "").strip().lower()
    if site_vacant == "yes":
        query = query.is_("site_vacant", True)
    elif site_vacant == "no":
        query = query.is_("site_vacant", False)
    elif site_vacant == "unknown":
        query = query.is_("site_vacant", None)
    for column, expr in _CONDITION_BUCKETS.get((condition_filter or "").strip().lower(), []):
        op, _, value = expr.partition(".")
        if op == "is":
            query = query.is_(column, None)
        elif op == "gte":
            query = query.gte(column, float(value))
        elif op == "lt":
            query = query.lt(column, float(value))

    sort_column = {"location_of_property": "location", "latitude": "lat", "longitude": "lng"}.get(sort, sort)
    query = query.order(sort_column, desc=(order or "asc").lower() == "desc")

    # page_size == 0 means "everything"; PostgREST still needs an explicit range.
    if page_size and page_size > 0:
        offset = max(page - 1, 0) * page_size
        query = query.range(offset, offset + page_size - 1)

    r = query.execute()
    rows = [_view_row_to_bill_row(row) for row in (r.data or [])]
    total = r.count if r.count is not None else len(rows)
    return rows, total
def get_bills_count_where(**kwargs: Any) -> int:
    """Run a simple count on bills with optional .eq filters. kwargs are column=value."""
    q = get_client().table("bills").select("apn", count="exact")
    for key, val in kwargs.items():
        if val is not None:
            q = q.eq(key, val)
    r = q.limit(1).execute()
    return r.count or 0


def get_bills_missing_location() -> list[tuple[str, str]]:
    r = get_client().table("bills").select("apn, bill_url, location_of_property").execute()
    return [(row["apn"], row.get("bill_url") or "") for row in (r.data or []) if not (row.get("location_of_property") or "").strip()]


def count_bills_missing_research() -> int:
    """Count bills where research_status is NULL or 'unchecked'."""
    r = (
        get_client()
        .table("bills")
        .select("apn", count="exact")
        .or_("research_status.is.null,research_status.eq.unchecked")
        .limit(1)
        .execute()
    )
    return r.count or 0


def get_bills_missing_research(limit: int | None = None, offset: int = 0) -> list[str]:
    """
    Get APNs of bills that have not been researched.

    Definition of "missing research":
      - research_status is NULL, or
      - research_status == 'unchecked'

    When limit is set, returns a stable page via order(apn) and range (for batch APIs).
    When limit is None, returns all matching APNs (legacy behavior).
    """
    q = (
        get_client()
        .table("bills")
        .select("apn")
        .or_("research_status.is.null,research_status.eq.unchecked")
        .order("apn")
    )
    if limit is not None:
        if limit <= 0:
            return []
        q = q.range(offset, offset + limit - 1)
    r = q.execute()
    return [row["apn"] for row in (r.data or []) if row.get("apn")]


def update_bill_ai_vacancy(
    apn: str,
    *,
    verdict: str | None = None,
    confidence: float | None = None,
    rationale: str | None = None,
) -> None:
    """Persist structured AI vacancy fields from deep research (additive; does not change has_vpt)."""
    from datetime import datetime as _dt

    now = _dt.now().isoformat()
    data: dict[str, Any] = {"ai_vacancy_updated_at": now}
    if verdict is not None:
        data["ai_vacancy_verdict"] = verdict
    if confidence is not None:
        data["ai_vacancy_confidence"] = confidence
    if rationale is not None:
        data["ai_vacancy_rationale"] = rationale
    get_client().table("bills").update(data).eq("apn", apn).execute()


# ---------------------------------------------------------------------------
# Legacy result helpers. The old `results` table was replaced by `bills`.
# Keep these function names for existing callers, but use the current schema.
# ---------------------------------------------------------------------------

def get_results_apns() -> set[str]:
    r = get_client().table("bills").select("apn").execute()
    return {row["apn"] for row in (r.data or []) if row.get("apn")}


def upsert_result(apn: str, pdf_file: str | None = None) -> None:
    # `bills` is populated by upsert_bill; there is no separate result row in
    # the current Supabase schema. Keep this as a compatibility no-op.
    return None


# ---------------------------------------------------------------------------
# Favorites (Supabase: apn, added_at)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Favorites.
#
# The standalone `favorites` table is gone: it duplicated list_properties for
# a single implicit list. Favorites are now rows in the list named 'Favorites'
# (seeded by supabase/migrations/20260807000003_scouting.sql), so one mechanism
# backs both. The public function names are unchanged — routes, templates and
# callers keep working.
# ---------------------------------------------------------------------------

FAVORITES_LIST_NAME = "Favorites"
_favorites_list_id: int | None = None


def get_favorites_list_id(create: bool = False) -> int | None:
    """Resolve (and cache) the id of the Favorites list.

    The list is seeded by migration, so a miss means something is off. Reads
    pass create=False and tolerate None rather than 500 the whole map page over
    a favorites overlay; writes pass create=True because they need a real id.
    """
    global _favorites_list_id
    if _favorites_list_id is not None:
        return _favorites_list_id
    r = (
        get_client()
        .table("lists")
        .select("id")
        .eq("name", FAVORITES_LIST_NAME)
        .limit(1)
        .execute()
    )
    if r.data:
        _favorites_list_id = int(r.data[0]["id"])
    elif create:
        _favorites_list_id = create_list(FAVORITES_LIST_NAME, "Ad-hoc saved properties")
    return _favorites_list_id


def get_favorites_apns() -> list[str]:
    list_id = get_favorites_list_id()
    if list_id is None:
        return []
    r = (
        get_client()
        .table("list_properties")
        .select("apn")
        .eq("list_id", list_id)
        .execute()
    )
    return [row["apn"] for row in (r.data or []) if row.get("apn")]


def _add_to_favorites(apns: list[str]) -> int:
    """Add APNs to the Favorites list, re-resolving a stale cached list id."""
    global _favorites_list_id
    list_id = get_favorites_list_id(create=True)
    try:
        return add_properties_to_list(list_id, apns)
    except Exception:
        # The cached id goes stale if the list is deleted out from under us.
        _favorites_list_id = None
        return add_properties_to_list(get_favorites_list_id(create=True), apns)


def add_favorite(apn: str) -> None:
    _add_to_favorites([apn])


def remove_favorite(apn: str) -> None:
    list_id = get_favorites_list_id()
    if list_id is not None:
        remove_property_from_list(list_id, apn)


def has_favorite(apn: str) -> bool:
    list_id = get_favorites_list_id()
    if list_id is None:
        return False
    r = (
        get_client()
        .table("list_properties")
        .select("apn")
        .eq("list_id", list_id)
        .eq("apn", apn)
        .limit(1)
        .execute()
    )
    return bool(r.data)


def toggle_favorite(apn: str) -> bool:
    if has_favorite(apn):
        remove_favorite(apn)
        return False
    add_favorite(apn)
    return True


def bulk_add_favorites(apns: list[str]) -> int:
    """Add multiple APNs to favorites. Returns count inserted."""
    if not apns:
        return 0
    return _add_to_favorites(apns)


def bulk_remove_favorites(apns: list[str]) -> int:
    """Remove multiple APNs from favorites. Returns count removed."""
    list_id = get_favorites_list_id()
    if list_id is None:
        return 0
    return bulk_remove_properties_from_list(list_id, apns)


# ---------------------------------------------------------------------------
# Bills + Parcels joined (for property info, coords, etc.)
# ---------------------------------------------------------------------------

def get_bill_with_parcel(apn: str) -> dict[str, Any] | None:
    """Return one bill row with parcel row_json joined (for get_property_info)."""
    bill = get_bill(apn)
    if not bill:
        return None
    # Fetch the raw county parcel row
    r = get_client().table("parcels").select("row_json").eq("apn", apn).limit(1).execute()
    row_json = None
    if r.data and len(r.data) > 0:
        row_json = r.data[0].get("row_json")
    bill["row_json"] = row_json
    return bill


# ---------------------------------------------------------------------------
# Lists (lists + list_properties)
# ---------------------------------------------------------------------------

def ensure_lists_tables() -> None:
    """No-op for Supabase; tables already exist."""
    pass


def get_lists() -> list[dict]:
    r = get_client().table("lists").select("id, name, description, created_at").order("name").execute()
    lists = []
    for row in r.data or []:
        cp = get_client().table("list_properties").select("id", count="exact").eq("list_id", row["id"]).execute()
        lists.append({
            "id": row["id"],
            "name": row["name"],
            "description": row.get("description"),
            "created_at": row.get("created_at"),
            "property_count": cp.count or 0,
        })
    return lists


def create_list(name: str, description: str | None = None) -> int:
    r = get_client().table("lists").insert({"name": name, "description": description or ""}).execute()
    if r.data and len(r.data) > 0:
        return int(r.data[0]["id"])
    raise RuntimeError("Failed to create list")


def get_list(list_id: int) -> dict | None:
    r = get_client().table("lists").select("*").eq("id", list_id).limit(1).execute()
    if r.data and len(r.data) > 0:
        return r.data[0]
    return None


def delete_list(list_id: int) -> bool:
    global _favorites_list_id
    get_client().table("list_properties").delete().eq("list_id", list_id).execute()
    r = get_client().table("lists").delete().eq("id", list_id).execute()
    if r.data and _favorites_list_id == list_id:
        _favorites_list_id = None
    return bool(r.data)


def get_list_properties(list_id: int) -> list[dict]:
    # Join list_properties -> bills -> parcels for full rows
    lp = get_client().table("list_properties").select("apn, sort_order").eq("list_id", list_id).order("sort_order").execute()
    if not lp.data:
        return []
    apns = [row["apn"] for row in lp.data]
    if not apns:
        return []
    bills = get_client().table("bills").select("apn, location_of_property, city, has_vpt, condition_score, lat, lng, streetview_image_path").in_("apn", apns).execute()
    bill_map = {b["apn"]: b for b in (bills.data or [])}
    parcels = get_client().table("parcels").select("apn, row_json").in_("apn", apns).execute()
    parcel_map = {p["apn"]: p.get("row_json") for p in (parcels.data or [])}
    out = []
    for row in lp.data:
        apn = row["apn"]
        b = bill_map.get(apn, {})
        out.append({
            "apn": apn,
            "location_of_property": b.get("location_of_property"),
            "city": b.get("city"),
            "has_vpt": b.get("has_vpt"),
            "condition_score": b.get("condition_score"),
            "streetview_image_path": b.get("streetview_image_path"),
            "lat": b.get("lat"),
            "lng": b.get("lng"),
            "row_json": parcel_map.get(apn),
            "sort_order": row.get("sort_order"),
        })
    return out


def add_properties_to_list(list_id: int, apns: list[str]) -> int:
    """Add a list of APNs to a list. Returns number added (skips already present)."""
    if not apns:
        return 0
    existing = get_client().table("list_properties").select("apn").eq("list_id", list_id).execute()
    existing_apns = {row["apn"] for row in (existing.data or [])}
    max_order_r = get_client().table("list_properties").select("sort_order").eq("list_id", list_id).order("sort_order", desc=True).limit(1).execute()
    next_sort_order = (max_order_r.data[0]["sort_order"] + 1) if max_order_r.data else 0
    added = 0
    for raw_apn in apns:
        apn = str(raw_apn or "").strip()
        if not apn or apn in existing_apns:
            continue
        get_client().table("list_properties").upsert({"list_id": list_id, "apn": apn, "sort_order": next_sort_order}, on_conflict="list_id,apn").execute()
        existing_apns.add(apn)
        added += 1
        next_sort_order += 1
    return added


def reorder_list_properties(list_id: int, apns: list[str]) -> int:
    existing = get_client().table("list_properties").select("apn, sort_order").eq("list_id", list_id).order("sort_order").execute()
    rows = existing.data or []
    if not rows:
        return 0
    existing_apns = {str(row.get("apn")) for row in rows if row.get("apn")}
    requested: list[str] = []
    seen: set[str] = set()
    for raw_apn in apns:
        apn = str(raw_apn or "").strip()
        if apn and apn in existing_apns and apn not in seen:
            requested.append(apn)
            seen.add(apn)
    ordered = requested + [str(row.get("apn")) for row in rows if row.get("apn") and str(row.get("apn")) not in seen]
    for index, apn in enumerate(ordered):
        get_client().table("list_properties").update({"sort_order": index}).eq("list_id", list_id).eq("apn", apn).execute()
    return len(ordered)


def add_properties_to_list_from_filter(
    list_id: int,
    q: str = "",
    zip_filter: str = "",
    power_filter: str = "",
    city_filter: str = "",
    vpt_filter: str = "",
    delinquent_filter: str = "",
    condition_filter: str = "",
    research_filter: str = "",
    occupancy_filter: str = "",
    ownership_filter: str = "",
    primary_resident_age_filter: str = "",
    deceased_count_filter: str = "",
    new_filter: str = "",
    added_after: str = "",
    added_before: str = "",
    ai_verdict_filter: str = "",
    ai_confidence_min: str = "",
    site_vacant_filter: str = "",
    limit: int = 0,
) -> int:
    rows, _ = get_bills_with_parcels_filtered(
        q=q,
        zip_filter=zip_filter,
        power_filter=power_filter,
        city_filter=city_filter,
        vpt_filter=vpt_filter,
        delinquent_filter=delinquent_filter,
        condition_filter=condition_filter,
        research_filter=research_filter,
        new_filter=new_filter,
        added_after=added_after,
        added_before=added_before,
        ai_verdict_filter=ai_verdict_filter,
        ai_confidence_min=ai_confidence_min,
        site_vacant_filter=site_vacant_filter,
        page=1,
        page_size=limit,
    )
    apns = [r["apn"] for r in rows if r.get("apn")]
    if not apns:
        return 0
    existing = get_client().table("list_properties").select("apn").eq("list_id", list_id).execute()
    existing_apns = {row["apn"] for row in (existing.data or [])}
    max_order_r = get_client().table("list_properties").select("sort_order").eq("list_id", list_id).order("sort_order", desc=True).limit(1).execute()
    sort_order = (max_order_r.data[0]["sort_order"] + 1) if max_order_r.data else 0
    added = 0
    for i, apn in enumerate(apns):
        if apn in existing_apns:
            continue
        get_client().table("list_properties").upsert({"list_id": list_id, "apn": apn, "sort_order": sort_order + i}, on_conflict="list_id,apn").execute()
        existing_apns.add(apn)
        added += 1
    return added


def remove_property_from_list(list_id: int, apn: str) -> bool:
    r = get_client().table("list_properties").delete().eq("list_id", list_id).eq("apn", apn).execute()
    return bool(r.data)


def bulk_remove_properties_from_list(list_id: int, apns: list[str]) -> int:
    """Remove multiple APNs from a list. Returns count deleted."""
    cleaned = [str(apn or "").strip() for apn in apns if str(apn or "").strip()]
    if not cleaned:
        return 0
    deleted = 0
    for i in range(0, len(cleaned), 200):
        r = (
            get_client()
            .table("list_properties")
            .delete()
            .eq("list_id", list_id)
            .in_("apn", cleaned[i : i + 200])
            .execute()
        )
        deleted += len(r.data or [])
    return deleted


def get_list_route_preview(list_id: int) -> dict:
    stops = []
    for index, prop in enumerate(get_list_properties(list_id)):
        # bills.lat/lng is authoritative — it feeds the geom column scout_next()
        # orders by. Parcel centroid is only a fallback for pre-existing rows.
        lat, lng = prop.get("lat"), prop.get("lng")
        if lat is None or lng is None:
            latlng = derive_latlng(prop.get("row_json"))
            if latlng is None:
                continue
            lat, lng = latlng
        stops.append({
            "apn": prop.get("apn"),
            "queue_position": index,
            "sort_order": prop.get("sort_order"),
            "address": prop.get("location_of_property") or "",
            "city": prop.get("city") or "",
            "has_vpt": prop.get("has_vpt") in (1, True, "1"),
            "condition_score": prop.get("condition_score"),
            "streetview_image_path": prop.get("streetview_image_path") or "",
            "lat": lat,
            "lng": lng,
        })
    return {"total": len(stops), "stops": stops}


def get_list_route_waypoints(list_id: int) -> list[dict]:
    lp = get_client().table("list_properties").select("apn, sort_order").eq("list_id", list_id).order("sort_order").execute()
    if not lp.data:
        return []
    waypoints = []
    for row in lp.data:
        apn = row["apn"]
        bill = get_client().table("bills").select("apn, location_of_property").eq("apn", apn).limit(1).execute()
        parcel = get_client().table("parcels").select("row_json").eq("apn", apn).limit(1).execute()
        loc = bill.data[0].get("location_of_property") if (bill.data and len(bill.data) > 0) else ""
        row_json = parcel.data[0].get("row_json") if (parcel.data and len(parcel.data) > 0) else None
        latlng = derive_latlng(row_json)
        if latlng is not None:
            lat, lng = latlng
            waypoints.append({"lat": lat, "lng": lng, "address": loc})
    return waypoints


# ---------------------------------------------------------------------------
# Scouting (lists, list_properties, scout_results)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Scouting collections.
#
# `scouting_collections` and `collection_properties` no longer exist: they were
# a second, parallel copy of lists/list_properties, dead on the Kotlin side
# (Repositories.kt already aliases Collection -> List) and dropped in the
# 2026-08-07 baseline schema. These functions keep the /api/scout/collections
# route shape working while delegating to the single lists implementation.
#
# scout_results.collection_id likewise became scout_results.list_id, and
# follow_up / flyered are real booleans now rather than 0/1 integers.
# ---------------------------------------------------------------------------


def get_scout_collections() -> list[dict]:
    out = []
    for row in get_lists():
        scouted = (
            get_client()
            .table("scout_results")
            .select("apn", count="exact")
            .eq("list_id", row["id"])
            .execute()
        )
        out.append({**row, "scouted_count": scouted.count or 0})
    return out


def create_scout_collection(name: str, description: str | None = None, apns: list[str] | None = None) -> int:
    list_id = create_list(name, description)
    if apns:
        add_properties_to_list(list_id, apns)
    return list_id


def delete_scout_collection(collection_id: int) -> bool:
    return delete_list(collection_id)


def get_collection_properties(collection_id: int) -> list[dict]:
    return get_list_properties(collection_id)


def add_properties_to_collection(collection_id: int, apns: list[str]) -> int:
    return add_properties_to_list(collection_id, apns)


def remove_property_from_collection(collection_id: int, apn: str) -> bool:
    return remove_property_from_list(collection_id, apn)


def get_scout_results(collection_id: int | None = None) -> list[dict]:
    q = get_client().table("scout_results").select(
        "id, apn, list_id, follow_up, flyered, notes, scouted_at, latitude, longitude"
    )
    if collection_id is not None:
        q = q.eq("list_id", collection_id)
    r = q.order("scouted_at", desc=True).execute()
    return r.data or []


def upsert_scout_result(
    apn: str,
    collection_id: int | None = None,
    follow_up: int = 0,
    flyered: int = 0,
    notes: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> int:
    payload: dict[str, Any] = {
        "apn": apn,
        # Callers still pass 0/1; the columns are boolean.
        "follow_up": bool(follow_up),
        "flyered": bool(flyered),
        "notes": notes or "",
        "latitude": latitude,
        "longitude": longitude,
    }
    if collection_id is not None:
        payload["list_id"] = collection_id
    r = get_client().table("scout_results").insert(payload).execute()
    if r.data and len(r.data) > 0:
        return int(r.data[0]["id"])
    return 0


def get_scout_stats() -> dict:
    total = get_client().table("scout_results").select("id", count="exact").execute()
    follow_ups = get_client().table("scout_results").select("id", count="exact").eq("follow_up", True).execute()
    flyered = get_client().table("scout_results").select("id", count="exact").eq("flyered", True).execute()
    distinct = get_client().table("scout_results").select("apn").execute()
    return {
        "total": total.count or 0,
        "follow_ups": follow_ups.count or 0,
        "flyered": flyered.count or 0,
        "unique_properties": len({row["apn"] for row in (distinct.data or [])}),
    }


def get_bills_for_export(
    q: str = "",
    zip_filter: str = "",
    power_filter: str = "",
    city_filter: str = "",
    vpt_filter: str = "",
    delinquent_filter: str = "",
    condition_filter: str = "",
    list_id: int | None = None,
    limit: int = 500,
    offset: int = 0,
) -> list[dict]:
    """Fetch bills with optional filters for export/CSV. Uses RPC or table."""
    rows, _ = get_bills_with_parcels_filtered(q=q, zip_filter=zip_filter, power_filter=power_filter, city_filter=city_filter, vpt_filter=vpt_filter, delinquent_filter=delinquent_filter, condition_filter=condition_filter, page=offset // limit + 1, page_size=limit)
    if list_id is not None:
        lp = get_client().table("list_properties").select("apn").eq("list_id", list_id).execute()
        list_apns = {row["apn"] for row in (lp.data or [])}
        rows = [r for r in rows if r.get("apn") in list_apns]
    return rows


def get_bills_for_export_count(
    q: str = "",
    zip_filter: str = "",
    power_filter: str = "",
    city_filter: str = "",
    vpt_filter: str = "",
    delinquent_filter: str = "",
    condition_filter: str = "",
    list_id: int | None = None,
) -> int:
    rows, total = get_bills_with_parcels_filtered(q=q, zip_filter=zip_filter, power_filter=power_filter, city_filter=city_filter, vpt_filter=vpt_filter, delinquent_filter=delinquent_filter, condition_filter=condition_filter, page=1, page_size=10000 if list_id else 1)
    if list_id is not None:
        lp = get_client().table("list_properties").select("apn").eq("list_id", list_id).execute()
        list_apns = {row["apn"] for row in (lp.data or [])}
        return len([r for r in rows if r.get("apn") in list_apns])
    return total


# ---------------------------------------------------------------------------
# Outreach Pipeline
# ---------------------------------------------------------------------------

OUTREACH_STAGES = [
    "identified", "qualified", "outreach_ready", "contacted",
    "responding", "negotiating", "partnered", "declined", "no_response",
]


def calculate_outreach_score(bill: dict) -> float:
    """Calculate outreach readiness score (0-100) for a property."""
    score = 0.0
    has_email = bool(bill.get("owner_email"))

    # Has owner email (25 pts) -- required for email outreach
    if has_email:
        score += 25.0

    # VPT status (20 pts)
    if bill.get("has_vpt"):
        score += 20.0

    # Delinquent (15 pts)
    if bill.get("delinquent"):
        score += 15.0

    # Power off (15 pts)
    if bill.get("power_status") == "off":
        score += 15.0

    # Poor condition (10 pts)
    cond = bill.get("condition_score")
    if cond is not None and cond < 5.0:
        score += 10.0

    # Out-of-state owner (10 pts)
    row_json = bill.get("row_json") or {}
    if isinstance(row_json, str):
        import json
        try:
            row_json = json.loads(row_json)
        except (json.JSONDecodeError, TypeError):
            row_json = {}
    mail_state = (row_json.get("MailState") or "").upper()
    if mail_state and mail_state != "CA":
        score += 10.0

    # Research completed (5 pts)
    if bill.get("research_status") == "completed":
        score += 5.0

    # Cap at 30 if no email
    if not has_email:
        score = min(score, 30.0)

    return round(score, 1)


def calculate_contact_completeness(bill: dict) -> float:
    """Calculate contact data completeness (0-100%)."""
    score = 0.0
    if bill.get("owner_email"):
        score += 40.0
    if bill.get("owner_phone") or bill.get("owner_mobile_phone"):
        score += 20.0
    # Check mailing address from parcel data
    row_json = bill.get("row_json") or {}
    if isinstance(row_json, str):
        import json
        try:
            row_json = json.loads(row_json)
        except (json.JSONDecodeError, TypeError):
            row_json = {}
    if row_json.get("MailAddress"):
        score += 20.0
    if bill.get("primary_resident_name"):
        score += 20.0
    return round(score, 1)


def determine_outreach_stage(bill: dict, outreach: dict | None = None) -> str:
    """Determine the current outreach stage for a property."""
    if outreach:
        # If manually set to terminal stages, respect that
        if outreach.get("stage") in ("partnered", "declined", "no_response"):
            return outreach["stage"]
        if outreach.get("stage") == "negotiating":
            return "negotiating"
        if outreach.get("last_response_at"):
            return "responding"
        if outreach.get("contacted_at"):
            return "contacted"

    score = calculate_outreach_score(bill)
    has_email = bool(bill.get("owner_email"))
    has_research = bill.get("research_status") == "completed"

    if has_email and has_research and score >= 50:
        return "outreach_ready"
    if score >= 50:
        return "qualified"
    return "identified"


def upsert_outreach(apn: str, **kwargs) -> None:
    """Create or update an outreach record for a property."""
    payload = {"apn": apn, "updated_at": "now()"}
    payload.update({k: v for k, v in kwargs.items() if v is not None})
    get_client().table("outreach").upsert(payload, on_conflict="apn").execute()


def get_outreach(apn: str) -> dict | None:
    """Get outreach record for a property."""
    resp = get_client().table("outreach").select("*").eq("apn", apn).execute()
    rows = resp.data or []
    return rows[0] if rows else None


def get_outreach_messages(apn: str, limit: int = 50) -> list[dict]:
    """Get outreach messages for a property, newest first."""
    resp = (
        get_client()
        .table("outreach_messages")
        .select("*")
        .eq("apn", apn)
        .order("sent_at", desc=True)
        .limit(limit)
        .execute()
    )
    return resp.data or []


def insert_outreach_message(
    apn: str,
    direction: str,
    content: str,
    channel: str = "email",
    subject: str | None = None,
    from_address: str | None = None,
    to_address: str | None = None,
    openclaw_message_id: str | None = None,
) -> None:
    """Log an outreach message."""
    payload = {
        "apn": apn,
        "direction": direction,
        "channel": channel,
        "content": content,
    }
    if subject:
        payload["subject"] = subject
    if from_address:
        payload["from_address"] = from_address
    if to_address:
        payload["to_address"] = to_address
    if openclaw_message_id:
        payload["openclaw_message_id"] = openclaw_message_id
    get_client().table("outreach_messages").insert(payload).execute()


def get_outreach_pipeline_counts() -> dict[str, int]:
    """Get count of properties at each pipeline stage."""
    counts = {}
    for stage in OUTREACH_STAGES:
        resp = (
            get_client()
            .table("outreach")
            .select("id", count="exact")
            .eq("stage", stage)
            .execute()
        )
        counts[stage] = resp.count or 0
    return counts


def get_outreach_list(
    stage: str | None = None,
    min_score: float | None = None,
    city: str | None = None,
    sort: str = "outreach_score",
    order: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Get filtered list of outreach records joined with bill data."""
    query = (
        get_client()
        .table("outreach")
        .select("*, bills!inner(location_of_property, city, owner_email, has_vpt, delinquent, power_status, condition_score, primary_resident_name, research_status)", count="exact")
    )
    if stage:
        query = query.eq("stage", stage)
    if min_score is not None:
        query = query.gte("outreach_score", min_score)
    if city:
        query = query.eq("bills.city", city)

    desc = order.lower() == "desc"
    query = query.order(sort, desc=desc).range(offset, offset + limit - 1)

    resp = query.execute()
    return resp.data or [], resp.count or 0


def update_outreach_stage(apn: str, stage: str, **kwargs) -> None:
    """Update the pipeline stage for a property."""
    if stage not in OUTREACH_STAGES:
        raise ValueError(f"Invalid stage: {stage}. Must be one of {OUTREACH_STAGES}")
    payload = {"stage": stage, "updated_at": "now()"}
    payload.update({k: v for k, v in kwargs.items() if v is not None})
    get_client().table("outreach").update(payload).eq("apn", apn).execute()


def recalculate_outreach_scores(apns: list[str] | None = None) -> int:
    """Recalculate outreach scores for given APNs (or all). Returns count updated."""
    if apns:
        resp = get_client().table("bills").select("*").in_("apn", apns).execute()
    else:
        resp = get_client().table("bills").select("*").execute()

    bills = resp.data or []
    updated = 0
    for bill in bills:
        score = calculate_outreach_score(bill)
        completeness = calculate_contact_completeness(bill)
        stage = determine_outreach_stage(bill, get_outreach(bill["apn"]))

        # Update bills table
        get_client().table("bills").update({
            "outreach_score": score,
            "contact_completeness": completeness,
            "outreach_stage": stage,
        }).eq("apn", bill["apn"]).execute()

        # Upsert outreach record
        upsert_outreach(bill["apn"], outreach_score=score, stage=stage)
        updated += 1

    return updated


def get_outreach_setting(key: str, default: str = "") -> str:
    """Get an outreach setting value."""
    resp = get_client().table("outreach_settings").select("value").eq("key", key).execute()
    rows = resp.data or []
    return rows[0]["value"] if rows else default


def set_outreach_setting(key: str, value: str) -> None:
    """Set an outreach setting value."""
    get_client().table("outreach_settings").upsert(
        {"key": key, "value": value, "updated_at": "now()"},
        on_conflict="key",
    ).execute()
