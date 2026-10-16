#!/usr/bin/env python3
"""
Vacancy research client for remote agents. Standard library only; copy this
folder to the agent's machine. See AGENT_BRIEF.md for how to use it.

Environment:
    BARN_SUPABASE_URL       project URL, e.g. https://xxxx.supabase.co
    BARN_SUPABASE_ANON_KEY  the project's publishable (anon) key
    BARN_AGENT_TOKEN        this agent's personal token

Commands (each prints JSON on stdout; errors go to stderr with exit code 1):
    research_client.py claim [--city OAKLAND] [--zip 94601,94607] [--lease-minutes 45]
    research_client.py submit --file finding.json [--token CLAIM_TOKEN]
    research_client.py release --token CLAIM_TOKEN --reason "why"
    research_client.py extend --token CLAIM_TOKEN [--minutes 30]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

TIMEOUT_SEC = 30


class ClientError(RuntimeError):
    pass


def _env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise ClientError(f"{name} is not set")
    return value


def build_request(function: str, params: dict[str, Any]) -> urllib.request.Request:
    key = _env("BARN_SUPABASE_ANON_KEY")
    headers = {"apikey": key, "Content-Type": "application/json", "Accept": "application/json"}
    if key.startswith("eyJ"):  # legacy JWT anon keys also go in Authorization
        headers["Authorization"] = f"Bearer {key}"
    body = {"p_agent_token": _env("BARN_AGENT_TOKEN"), **params}
    return urllib.request.Request(
        f"{_env('BARN_SUPABASE_URL').rstrip('/')}/rest/v1/rpc/{function}",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )


def call(function: str, params: dict[str, Any]) -> Any:
    request = build_request(function, params)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(raw).get("message") or raw
        except (ValueError, AttributeError):
            message = raw
        raise ClientError(f"{function} refused (HTTP {e.code}): {message}") from None
    except urllib.error.URLError as e:
        raise ClientError(f"{function} could not reach the server: {e.reason}") from None
    return json.loads(raw) if raw.strip() else None


def parse_zip_codes(values: list[str] | None) -> list[str]:
    """Flatten repeated and comma-separated --zip values into 5-digit ZIP codes."""
    zips: list[str] = []
    for value in values or []:
        for part in value.replace(",", " ").split():
            zip_code = part.split("-")[0]  # accept ZIP+4
            if len(zip_code) != 5 or not zip_code.isdigit():
                raise ClientError(f"not a 5-digit zip code: {part}")
            if zip_code not in zips:
                zips.append(zip_code)
    return zips


def claim(
    city: str = "",
    lease_minutes: int = 45,
    zip_codes: list[str] | None = None,
    apn: str | None = None,
) -> dict[str, Any] | None:
    """One property to research, or None when nothing matching is left in the queue."""
    params: dict[str, Any] = {"p_lease_minutes": lease_minutes, "p_city": city or ""}
    if zip_codes:
        params["p_zip_codes"] = zip_codes
    if apn:
        params["p_apn"] = apn
    return call("claim_vacancy_research", params)


def submit(finding: dict[str, Any], claim_token: str | None = None) -> dict[str, Any]:
    token = claim_token or finding.get("claim_token")
    if not token:
        raise ClientError("claim_token is required (in the finding file or via --token)")
    return call("submit_vacancy_finding", {
        "p_claim_token": token,
        "p_verdict": finding.get("verdict"),
        "p_confidence": finding.get("confidence"),
        "p_rationale": finding.get("rationale"),
        "p_evidence": finding.get("evidence") or [],
        "p_report_md": finding.get("report_md"),
    })


def release(claim_token: str, reason: str = "") -> dict[str, Any]:
    return call("release_vacancy_research", {"p_claim_token": claim_token, "p_error": reason})


def extend(claim_token: str, minutes: int = 30) -> dict[str, Any]:
    return call("extend_vacancy_research", {"p_claim_token": claim_token, "p_minutes": minutes})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BARN vacancy research client")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("claim", help="get one property to research (prints null when none are left)")
    p.add_argument("--city", default="")
    p.add_argument("--zip", action="append", default=None, metavar="ZIP[,ZIP...]",
                   help="only claim properties in these zip codes (repeatable or comma-separated)")
    p.add_argument("--apn", default=None, help="claim only this exact APN")
    p.add_argument("--lease-minutes", type=int, default=45)

    p = sub.add_parser("submit", help="submit a finding and close the claim")
    p.add_argument("--file", required=True, help="path to the finding JSON, or - for stdin")
    p.add_argument("--token", default=None, help="claim token (overrides claim_token in the file)")

    p = sub.add_parser("release", help="give the property back without a finding")
    p.add_argument("--token", required=True)
    p.add_argument("--reason", default="")

    p = sub.add_parser("extend", help="extend the lease on a claim")
    p.add_argument("--token", required=True)
    p.add_argument("--minutes", type=int, default=30)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "claim":
            result: Any = claim(
                city=args.city, lease_minutes=args.lease_minutes,
                zip_codes=parse_zip_codes(args.zip), apn=args.apn,
            )
        elif args.command == "submit":
            try:
                text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
                finding = json.loads(text)
            except (OSError, ValueError) as e:
                raise ClientError(f"could not read finding from {args.file}: {e}") from None
            if not isinstance(finding, dict):
                raise ClientError("the finding must be a JSON object")
            result = submit(finding, claim_token=args.token)
        elif args.command == "release":
            result = release(args.token, args.reason)
        else:
            result = extend(args.token, args.minutes)
    except ClientError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
