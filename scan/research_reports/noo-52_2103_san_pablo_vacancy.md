# NOO-52: Vacancy research — 2103 San Pablo Ave, Oakland

**APN:** `003 003900300` (`003-0039-003-00` in some records)  
**Research date:** 2026-10-15  
**Verdict:** `likely_vacant`  
**Confidence:** `0.82`  
**Estimated vacancy start:** 2021 (reported; exact date unverified)  
**Duration supported by dated evidence:** shuttered since 2021 and still described as shuttered/defunct in July 2025, or at least about four years by that report. Current occupancy after July 2025 is unverified.

## Rationale

The property is the former Greyhound terminal. News coverage in July 2025 described it as shuttered since 2021, vacant/defunct, and accumulating graffiti and trash. It reported a March 2025 purchase and early redevelopment intent, not an operating business. These sources support likely vacancy through July 2025, but do not establish the exact closing date or confirm status in October 2026.

## Evidence

- **Dated closure/vacancy:** [SFist, July 27, 2025](https://sfist.com/2025/07/27/temple-sf-owner-buys-historic-oakland-greyhound-site-for-event-venue-housing/) says the 2103 San Pablo Avenue Greyhound station had been shuttered since 2021. It reports the Hemming Family Trust purchased it in March 2025 and discusses future event-space/housing plans. Its description of the 2022 underground parties means occasional unauthorized events should not be mistaken for normal occupancy.
- **Condition and redevelopment context:** [SFGATE, July 26, 2025](https://www.sfgate.com/local/article/vacant-bay-area-outpost-return-from-dead-20786484.php) describes the station as shuttered/defunct, graffiti-covered and accumulating trash; the purchaser described future venue ambitions. This is consistent with vacancy at publication, though not direct inspection by this researcher.
- **Parcel identity:** [LoopNet property listing](https://www.loopnet.com/Listing/2103-San-Pablo-Ave-Oakland-CA/31050998/) identifies 2103 San Pablo Ave, Oakland, CA 94612 and APN `003-0039-003-00`. Listing was on market February 26, 2024 and is no longer advertised; listing status alone does not prove building occupancy.
- **Official historical identity:** [City of Oakland FY2021–22 Municipal Regional Permit annual report](https://www.oaklandca.gov/files/assets/city/v/1/public-works/documents/sustainability/mrp/oakland-mrp-fy-21-22-annualreport_final.pdf) lists Greyhound Lines Inc at 2103 San Pablo Ave as a transportation facility. This is a regulatory/business association, not proof that the terminal was open during that fiscal year.
- **Current operator locator:** [Greyhound’s Oakland stop page](https://www.greyhound.com/bus/oakland-berkeley-east-bay-ca/oakland-west-oakland-bart-station) lists Oakland service at West Oakland BART, not this former terminal. This supports that intercity service moved, but does not establish whether any new tenant currently occupies the building.

## Queue submission status

Not submitted. The documented `claim_vacancy_research` RPC supports city and ZIP filters but no APN filter. Using the supported client with Oakland/94612 returned higher-priority APN `003 004100600`; both claims were released immediately. Enqueueing the target APN added zero tasks, and targeted requeue for `done` and `failed` added zero. Do not treat this report file as a submitted `vacancy_findings` row.

## Proposed queue payload

```json
{
  "verdict": "likely_vacant",
  "confidence": 0.82,
  "rationale": "July 2025 reporting says the former Greyhound terminal had been shuttered since 2021 and remained defunct, with graffiti/trash and future redevelopment plans. This supports vacancy for at least about four years by July 2025; current status after then is unverified.",
  "evidence": [
    {
      "signal": "reported shuttered since 2021; still described as defunct in July 2025",
      "detail": "SFist reports the 2103 San Pablo Greyhound station was shuttered since 2021, mentions unauthorized underground parties in 2022, and says the trust purchased the property in March 2025 with future redevelopment plans.",
      "source_url": "https://sfist.com/2025/07/27/temple-sf-owner-buys-historic-oakland-greyhound-site-for-event-venue-housing/",
      "observed_at": "2025-07-27"
    },
    {
      "signal": "defunct station and deteriorated condition",
      "detail": "SFGATE describes the station as defunct, shuttered, graffiti-covered and accumulating trash; reported redevelopment intent was prospective.",
      "source_url": "https://www.sfgate.com/local/article/vacant-bay-area-outpost-return-from-dead-20786484.php",
      "observed_at": "2025-07-26"
    },
    {
      "signal": "Greyhound service listed at a different Oakland location",
      "detail": "Greyhound's current Oakland stop page lists West Oakland BART rather than 2103 San Pablo Ave. This supports relocation of transit service, not current building vacancy by itself.",
      "source_url": "https://www.greyhound.com/bus/oakland-berkeley-east-bay-ca/oakland-west-oakland-bart-station",
      "observed_at": "2026-10-15"
    }
  ]
}
```
