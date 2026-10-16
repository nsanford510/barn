# Vacancy research: 229 Brush St, Oakland (APN 001-0111-001-00)

**Research date:** 2026-10-15  
**Verdict:** `unknown`  
**Confidence:** 0.35  
**Estimated vacancy start:** Unknown  
**Estimated vacancy duration:** Unknown; the public record reviewed does not establish that the property is currently vacant or bound the start of any vacancy.

## Rationale

The parcel is a commercial storefront/restaurant, with documented restaurant operation in 2013–14, an accessibility-restroom permit in 2017, and a county 2018 record listing an occupant at the address. A 2017 commercial lease listing later became inactive, but that does not establish vacancy or occupancy. No reliable recent evidence establishes current use or when vacancy may have begun.

## Evidence

| Date | Evidence | How it bears on vacancy | Source |
| --- | --- | --- | --- |
| 2013-10-08 | Eater SF reported Venga Paella opened at 229 Brush St and described its restaurant service. | Direct evidence of commercial operation at the address at that time; not evidence of current occupancy. | [Eater SF](https://sf.eater.com/2013/10/8/6356185/venga-paella-homestyle-spanish-in-oakland) |
| 2014-07 | Alameda Magazine identified Venga Paella at 229 Brush St and described a meal there. | Further evidence of restaurant operation in 2014. | [Alameda Magazine](https://www.alamedamagazine.com/venga-paellas-manchego-con-membrillo/) |
| 2017 | City of Oakland permit record B1702334 for 229 Brush St describes altering a restroom to meet accessibility requirements. | Confirms permitted building work associated with the address; does not prove work completion or occupancy. | [City of Oakland Legistar attachment](https://oakland.legistar.com/View.ashx?GUID=6A7754CC-510C-44CB-807A-C0C7E579D7CD&ID=8871387&M=F) |
| 2017-09-20 | LoopNet's listing history records a 225–229 Brush St commercial space as offered for lease. | An offer for lease is not proof the space was vacant, and the listing's later inactivity does not establish a lease or vacancy date. | [LoopNet](https://www.loopnet.com/Listing/225-229-Brush-St-Oakland-CA/9242340/) |
| 2018-02-05 | Alameda County Environmental Health's report includes APN 001 011100100 and the address under an “OCCUPANT” field. | A public record tying an occupant/mailing entry to the address, but not strong proof of actual site use. | [Alameda County Environmental Health PDF](https://dehpra.acgov.org/Lopinfo/ReadFile?record=RO0003196%5CCAP_PP_L_2018-02-05.pdf) |
| 2025-02 through 2026-05 | A third-party property summary lists permit entries for 229 Brush St dated 2025-02-21, 2026-02-03, 2026-02-24, 2026-03-19, and 2026-05-24. The record descriptions and statuses are masked. | Possible recent building activity, but the available summary does not identify the work or show that anyone occupied the property. | [PropertyShark permit summary](https://www.propertyshark.com/mason/Property/38673400/229-Brush-St-Oakland-CA-94607/) |
| Crawled 2025 | MapQuest labels Venga Paella Popup & Catering “Permanently closed” at this address. | Weak, third-party business-directory evidence that the listed restaurant may have closed; it does not establish whether the building is vacant or occupied by another user. | [MapQuest](https://www.mapquest.com/us/california/venga-paella-popup-catering-286045033) |

## Finding payload for the vacancy research queue

```json
{
  "verdict": "unknown",
  "confidence": 0.35,
  "rationale": "The parcel is a commercial storefront/restaurant, with documented restaurant operation in 2013–14, an accessibility-restroom permit in 2017, and a county 2018 record listing an occupant at the address. A 2017 commercial lease listing later became inactive, but that does not establish vacancy or occupancy. No reliable recent evidence establishes current use or when vacancy may have begun.",
  "evidence": [
    {"source_url": "https://sf.eater.com/2013/10/8/6356185/venga-paella-homestyle-spanish-in-oakland", "date": "2013-10-08", "summary": "Reported Venga Paella opened at 229 Brush St and described restaurant service."},
    {"source_url": "https://www.alamedamagazine.com/venga-paellas-manchego-con-membrillo/", "date": "2014-07", "summary": "Identified Venga Paella at 229 Brush St and described a meal there."},
    {"source_url": "https://oakland.legistar.com/View.ashx?GUID=6A7754CC-510C-44CB-807A-C0C7E579D7CD&ID=8871387&M=F", "date": "2017", "summary": "City permit B1702334 for 229 Brush St describes restroom accessibility alterations."},
    {"source_url": "https://www.loopnet.com/Listing/225-229-Brush-St-Oakland-CA/9242340/", "date": "2017-09-20", "summary": "Commercial listing history records 225–229 Brush St offered for lease; this does not establish vacancy."},
    {"source_url": "https://dehpra.acgov.org/Lopinfo/ReadFile?record=RO0003196%5CCAP_PP_L_2018-02-05.pdf", "date": "2018-02-05", "summary": "County report lists the APN and address under an occupant field, which may be a mailing entry."},
    {"source_url": "https://www.propertyshark.com/mason/Property/38673400/229-Brush-St-Oakland-CA-94607/", "date": "2025-02-21 to 2026-05-24", "summary": "Third-party property summary lists several permit entries but masks descriptions and statuses; recent permits alone do not establish occupancy."},
    {"source_url": "https://www.mapquest.com/us/california/venga-paella-popup-catering-286045033", "date": "crawled 2025", "summary": "Third-party directory marks the listed restaurant permanently closed; building vacancy is unproven."}
  ],
  "estimated_vacancy_start_date": null,
  "estimated_vacancy_duration": null
}
```

## Limits

The subject is a commercial building, so residential utility assumptions were not used. The third-party permit summary lists recent entries, but masks descriptions and statuses. Oakland's [official Accela building-record search](https://aca-prod.accela.com/OAKLAND/Cap/CapHome.aspx?TabName=Home&module=Building) returned HTTP 403 in this session, so the entries could not be verified or interpreted against the primary record. No reliable current evidence was found that establishes occupancy or vacancy in 2026. The close-of-business listing is not treated as proof that the structure itself is empty.
