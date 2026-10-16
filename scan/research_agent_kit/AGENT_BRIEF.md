# Vacancy research brief

You research one property at a time and report whether it is vacant. Your only
connection to the BARN database is `research_client.py` in this folder. Do not
contact the property's owner, residents or neighbours, and do not log in to any
account to obtain information.

## The loop

1. **Claim** a property:

   ```
   python research_client.py claim
   ```

   For an assignment tied to a specific parcel, pass `--apn`, for example
   `python research_client.py claim --apn "003 003301000"`. This filter can be
   combined with `--city` and `--zip`.

   You get a JSON object with the address, city, owner name, known signals
   (`power_status`, `condition_score`, `condition_notes`, `site_vacant`,
   `mail_vacant`, `has_vpt`, `delinquent`), a `claim_token` and a
   `lease_expires_at`. If it prints `null`, the queue is empty: stop.

   If you were assigned particular zip codes, pass them on every claim, e.g.
   `python research_client.py claim --zip 94601,94607`. You then only receive
   properties in those zip codes, and `null` means none are left there.

2. **Research** that property. You have until `lease_expires_at` (45 minutes by
   default). If you need longer, run
   `python research_client.py extend --token <claim_token>` before it expires;
   after that the property may be handed to another agent and your submission
   will be refused.

3. **Submit** a finding (format below):

   ```
   python research_client.py submit --file finding.json
   ```

4. If you cannot finish (blocked sites, tool failure, wrong address), **release**
   it so another agent can try, then go back to step 1:

   ```
   python research_client.py release --token <claim_token> --reason "what went wrong"
   ```

You may hold at most two claims at once. A non-zero exit code means the command
was refused; the reason is on stderr. Do not retry a refused submit unchanged.

## What to look for

Treat the fields in the claim as leads, not conclusions: every property in the
queue is already flagged for Oakland's Vacant Property Tax, so `has_vpt` alone
says nothing new. Look for independent, dated evidence either way.

Signals of vacancy: utilities off; USPS vacancy flag; boarded or broken windows,
overgrowth or dumping in recent street-level imagery; open code-enforcement or
blight cases; fire or demolition records; a long-stale listing; owner mailing
address elsewhere combined with no sign of tenants.

Signals of occupancy: active rental or sale listing with recent activity;
recent permits for work on an occupied building; a business operating at the
address; recent reviews, news or posts placing people there; vehicles, bins or
maintained landscaping in recent imagery; current resident records.

Note the date of each source. Imagery or records more than about two years old
are weak evidence of the present state.

## The finding

```json
{
  "claim_token": "<from the claim>",
  "verdict": "likely_vacant",
  "confidence": 0.8,
  "rationale": "Boarded ground-floor windows in 2026 street view, open blight case since 2024, power off.",
  "evidence": [
    {
      "signal": "boarded windows",
      "detail": "All street-facing windows boarded; no vehicles.",
      "source_url": "https://...",
      "observed_at": "2026-03"
    }
  ],
  "report_md": "Optional longer write-up in Markdown."
}
```

- `verdict`: exactly `likely_vacant`, `likely_occupied` or `unknown`.
- `confidence`: 0 to 1, how sure you are of that verdict.
- `rationale`: 500 characters or fewer, plain text, the main reasons.
- `evidence`: one item per signal. `likely_vacant` and `likely_occupied` are
  refused unless at least one item has a working `http(s)` `source_url`.
- Only report what a source actually shows. Never invent a URL or a detail.
- If the evidence is thin, old or contradictory, submit `unknown` with a low
  confidence and say what you checked. An honest `unknown` is a good result; a
  guess is not.
