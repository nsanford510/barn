#!/usr/bin/env python3
"""One-off backfill of structured vacancy start dates from existing finding text.

Before `vacant_since` existed, vacancy research agents recorded vacancy length only
as free text (report_md, rationale, evidence). The web GUI derives "Vacant For"
from `bills.ai_vacant_since`, so likely_vacant properties showed nothing. This
script reads the latest likely_vacant finding per APN and, only where the text
states a start or a minimum duration explicitly, fills in:

    vacancy_findings.vacant_since   (the specific finding the value came from)
    bills.ai_vacant_since           (the same value, for that APN)

Accepted formats match the submit_vacancy_finding RPC: YYYY, YYYY-MM, YYYY-MM-DD.

Extraction is deliberately conservative. It only accepts:
  * labelled starts          "Vacancy start: January 29, 2015", "Vacancy start date: during calendar year 2024"
  * "vacant since [at least] <date>", "vacancy began <date>", "became vacant in <date>"
  * "vacant by <date> (at least/about ...)"   earliest dated evidence, with a stated duration
  * stated durations         "vacant-lot period of at least 3 years 11 months",
                             converted to YYYY-MM using the finding's created_at
Everything else (listing dates, purchase/sale dates, tax delinquency, "unknown",
conditional "if ..." sentences, durations measured from some other date) is left
blank and listed as "no length found". Rows that already have a value are never
overwritten.

Usage (from the repo root):
    scan/.venv/bin/python scripts/backfill_vacant_since.py            # dry run
    scan/.venv/bin/python scripts/backfill_vacant_since.py --apply    # write
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = REPO_ROOT / "scan" / ".env"

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_RE = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|"
    r"Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
# Alternatives are ordered most specific first; the first one that matches wins.
_DATE_RE = (
    r"(?:"
    r"(?P<iso>\d{4}-\d{2}(?:-\d{2})?)"
    r"|(?P<mdy>\d{1,2}/\d{1,2}/\d{4})"
    r"|(?P<my>\d{1,2}/\d{4})"
    rf"|(?P<mon>{_MONTH_RE})\.?\s+(?:(?P<day>\d{{1,2}})(?:st|nd|rd|th)?,?\s+)?(?P<monyear>\d{{4}})"
    r"|(?:(?:early|late|mid-?)\s+)?(?:spring|summer|fall|autumn|winter)\s+(?P<seasonyear>\d{4})"
    r"|(?:calendar\s+year\s+)?(?P<year>(?:19|20)\d{2})"
    r")"
)
_LEAD_RE = r"(?:(?:during|in|on|about|~)\s*)*"
_MODS_RE = r"(?:(?:at\s+least|about|approximately|roughly|over|more\s+than|nearly|~)\s*)*"
_DUR_RE = (
    r"(?:(?P<y>\d{1,3})\s*(?:years?|yrs?|y)\b(?:[\s,]*(?:and\s*)?(?P<m>\d{1,2})\s*(?:months?|mos?)\b)?"
    r"|(?P<monly>\d{1,3})\s*(?:months?|mos?)\b)"
)
_HEDGE_RE = re.compile(
    r"\b(?:if|may have|might|could have|possibly|perhaps|unverified|alleged|suggests?)\b", re.IGNORECASE
)
# A quote or a complaint/hearing/neighbour before the phrase means the duration is somebody
# else's statement, measured from their own date rather than from the finding's created_at.
_THIRD_PARTY_RE = re.compile(r"['\"“‘]|complain|hearing|neighbou?r|\btold\b|flyer|broker|slogan|brochure|\blabels\b", re.IGNORECASE)
# A date followed by a range or a second year ("1962–1969") is not a single start date.
_RANGE_AFTER_RE = re.compile(r"^\s*(?:[–-]|to\b|or\b|/)\s*\d")
_AFTER_DUR_BAD = re.compile(
    r"^\s*(?:[-–]?\s*)?(?:apart|ago|earlier|before|after|between|old|of\s+(?:dated|listing))", re.IGNORECASE
)


@dataclass(frozen=True)
class Extraction:
    value: str
    kind: str      # start | since | by | duration
    snippet: str


def _valid(value: str, today: date) -> bool:
    """True when `value` is YYYY / YYYY-MM / YYYY-MM-DD, a real date in 1900..today."""
    if not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
        return False
    padded = {4: value + "-01-01", 7: value + "-01"}.get(len(value), value)
    try:
        d = datetime.strptime(padded, "%Y-%m-%d").date()
    except ValueError:
        return False
    return date(1900, 1, 1) <= d <= today


def _date_from_match(m: re.Match[str]) -> str | None:
    """Normalise a _DATE_RE match to YYYY / YYYY-MM / YYYY-MM-DD."""
    if m.group("iso"):
        return m.group("iso")
    if m.group("mdy"):
        mo, dd, yy = m.group("mdy").split("/")
        return f"{int(yy):04d}-{int(mo):02d}-{int(dd):02d}"
    if m.group("my"):
        mo, yy = m.group("my").split("/")
        return f"{int(yy):04d}-{int(mo):02d}"
    if m.group("mon"):
        month = MONTHS[m.group("mon")[:3].lower()]
        year = int(m.group("monyear"))
        if m.group("day"):
            return f"{year:04d}-{month:02d}-{int(m.group('day')):02d}"
        return f"{year:04d}-{month:02d}"
    if m.group("seasonyear"):
        return m.group("seasonyear")
    if m.group("year"):
        return m.group("year")
    return None


def duration_to_start(years: int, months: int, ref: date) -> str:
    """YYYY-MM of `ref` minus the duration (year-month precision)."""
    total = ref.year * 12 + (ref.month - 1) - (years * 12 + months)
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _clean(text: str) -> str:
    """Drop markdown emphasis/links so labels like '**Vacancy start:**' match."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    return re.sub(r"[ \t]+", " ", text)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.;])\s+(?=[A-Z(\[*\"“-])|\n+", _clean(text))
    return [p.strip() for p in parts if p.strip()]


def _snippet(sentence: str, start: int, end: int) -> str:
    lo, hi = max(0, start - 40), min(len(sentence), end + 60)
    return ("…" if lo else "") + sentence[lo:hi].strip() + ("…" if hi < len(sentence) else "")


def extract_vacant_since(text: str, ref: date) -> Extraction | None:
    """Best explicit vacancy start in `text`, or None when it is not clearly stated.

    `ref` is the date relative durations are measured from (the finding's created_at).
    Explicit dates beat durations; within a kind, the first sentence wins.
    """
    def dated(prefix: str, suffix: str = "") -> re.Pattern[str]:
        return re.compile(prefix + rf"(?P<date>{_DATE_RE})" + suffix, re.IGNORECASE)

    patterns: list[tuple[str, re.Pattern[str]]] = [
        ("start", dated(rf"\b(?:likely\s+|estimated\s+)?vacancy\s+start(?:\s+date)?\s*:\s*{_LEAD_RE}")),
        ("since", dated(rf"\bvacan(?:t|cy)\s+since\s+{_MODS_RE}{_LEAD_RE}")),
        ("since", dated(rf"\bvacancy\s+(?:began|started)\s+{_LEAD_RE}")),
        ("since", dated(rf"\b(?:became|was\s+declared)\s+vacant\s+{_LEAD_RE}")),
        ("by", dated(rf"\bvacan(?:t|cy)\s+by\s+{_LEAD_RE}",
                     r"(?=[^.;\n]{0,40}?\((?:at\s+least|about|~))")),
    ]
    dur_patterns = [
        re.compile(rf"\bvacan(?:t|cy)(?:[- ]lot)?\s+(?:period\s+)?(?:of|for)\s+{_MODS_RE}{_DUR_RE}", re.IGNORECASE),
        re.compile(rf"\bat\s+least\s+{_DUR_RE}\s+vacant\b", re.IGNORECASE),
        re.compile(rf"\bvacancy\s+duration\s*:\s*{_MODS_RE}{_DUR_RE}", re.IGNORECASE),
    ]
    sentences = _sentences(text)
    for kind, pat in patterns:
        for s in sentences:
            for m in pat.finditer(s):
                before = s[max(0, m.start() - 80): m.start()]
                if _HEDGE_RE.search(before) or _THIRD_PARTY_RE.search(before) or _RANGE_AFTER_RE.match(s[m.end():]):
                    continue
                dm = re.fullmatch(_DATE_RE, m.group("date"), re.IGNORECASE)
                value = _date_from_match(dm) if dm else None
                if value and _valid(value, ref):
                    return Extraction(value, kind, _snippet(s, m.start(), m.end()))
    for pat in dur_patterns:
        for s in sentences:
            if _HEDGE_RE.search(s):
                continue
            for m in pat.finditer(s):
                if _AFTER_DUR_BAD.match(s[m.end():]) or _THIRD_PARTY_RE.search(s[max(0, m.start() - 120): m.start()]):
                    continue
                years = int(m.group("y") or 0)
                months = int(m.group("m") or m.group("monly") or 0)
                if years == 0 and months == 0:
                    continue
                value = duration_to_start(years, months, ref)
                if _valid(value, ref):
                    return Extraction(value, "duration", _snippet(s, m.start(), m.end()))
    return None


def finding_text(finding: dict[str, Any]) -> str:
    """report_md first, then rationale, then evidence details, as one searchable blob."""
    parts = [finding.get("report_md") or "", finding.get("rationale") or ""]
    for ev in finding.get("evidence") or []:
        if isinstance(ev, dict):
            parts.append(f"{ev.get('signal') or ''}: {ev.get('detail') or ''}")
    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Supabase / PostgREST (service key from scan/.env; never printed)
# ---------------------------------------------------------------------------

def _load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


class Rest:
    def __init__(self) -> None:
        import requests

        env = _load_env(ENV_FILE)
        key = (env.get("SUPABASE_SECRET_KEY") or env.get("SUPABASE_SERVICE_KEY")
               or env.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
        if not env.get("SUPABASE_URL") or not key:
            raise SystemExit("scan/.env needs SUPABASE_URL and a service key (SUPABASE_SECRET_KEY)")
        self.base = env["SUPABASE_URL"].rstrip("/") + "/rest/v1/"
        self.http = requests.Session()
        self.http.headers.update({"apikey": key, "Authorization": f"Bearer {key}"})

    def select_all(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            r = self.http.get(self.base + table, params=params,
                              headers={"Range-Unit": "items", "Range": f"{offset}-{offset + 999}"}, timeout=60)
            r.raise_for_status()
            page = r.json()
            rows.extend(page)
            if len(page) < 1000:
                return rows
            offset += 1000

    def patch_if_blank(self, table: str, key_col: str, key: str, col: str, value: str,
                       extra: dict[str, str] | None = None) -> int:
        """PATCH `col` only where it is NULL/empty; returns rows changed (0 or 1)."""
        params = {key_col: f"eq.{key}", "or": f"({col}.is.null,{col}.eq.)"}
        params.update(extra or {})
        r = self.http.patch(self.base + table, params=params, json={col: value},
                            headers={"Prefer": "return=representation"}, timeout=60)
        r.raise_for_status()
        return len(r.json())


def load_candidates(rest: Rest) -> list[dict[str, Any]]:
    """likely_vacant bills with no ai_vacant_since, each with its latest likely_vacant finding."""
    bills = rest.select_all("bills", {
        "select": "apn,location_of_property,city,ai_vacancy_verdict,ai_vacant_since",
        "ai_vacancy_verdict": "eq.likely_vacant",
        "order": "apn.asc",
    })
    findings = rest.select_all("vacancy_findings", {
        "select": "id,apn,agent,verdict,rationale,evidence,report_md,vacant_since,created_at",
        "verdict": "eq.likely_vacant",
        "order": "created_at.desc",
    })
    latest: dict[str, dict[str, Any]] = {}
    for f in findings:
        latest.setdefault(f["apn"], f)
    out = []
    for b in bills:
        b["finding"] = latest.get(b["apn"])
        out.append(b)
    return out


def run(apply: bool) -> int:
    rest = Rest()
    rows = load_candidates(rest)
    total = len(rows)
    already = [r for r in rows if (r.get("ai_vacant_since") or "").strip()]
    todo = [r for r in rows if not (r.get("ai_vacant_since") or "").strip()]
    print(f"likely_vacant bills: {total}; already have ai_vacant_since: {len(already)}; to examine: {len(todo)}")
    print(f"mode: {'APPLY' if apply else 'DRY RUN (no writes)'}\n")

    filled: list[tuple[dict[str, Any], Extraction]] = []
    blank: list[str] = []
    for r in todo:
        f = r["finding"]
        ext = None
        if f:
            ref = datetime.fromisoformat(f["created_at"].replace("Z", "+00:00")).date()
            ext = extract_vacant_since(finding_text(f), ref)
        if ext:
            filled.append((r, ext))
            print(f"[FILL] {r['apn']} | {(r.get('location_of_property') or '')[:40]} | "
                  f"{ext.value} ({ext.kind}) | “{ext.snippet}”")
        else:
            blank.append(r["apn"] + ("" if f else " (no likely_vacant finding)"))

    written = 0
    if apply:
        for r, ext in filled:
            f = r["finding"]
            rest.patch_if_blank("vacancy_findings", "id", f["id"], "vacant_since", ext.value)
            n = rest.patch_if_blank("bills", "apn", r["apn"], "ai_vacant_since", ext.value,
                                    {"ai_vacancy_verdict": "eq.likely_vacant"})
            written += n
            print(f"  wrote {r['apn']} -> {ext.value} (bills rows changed: {n})")

    print(f"\nno length found ({len(blank)}): " + ", ".join(blank))
    print(f"\nSUMMARY total={total} already_set={len(already)} extracted={len(filled)} "
          f"no_length_found={len(blank)} written={written}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print extractions, write nothing (default)")
    mode.add_argument("--apply", action="store_true", help="write extracted values")
    args = ap.parse_args()
    return run(apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
