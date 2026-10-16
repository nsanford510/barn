# Vacancy research: 229 Brush St, Oakland (APN 001-0111-001-00)

**Research date:** 2026-10-15  
**Verdict:** `likely_vacant`
**Confidence:** 0.78
**Estimated vacancy start:** During calendar year 2024 (exact date unknown)
**Estimated vacancy duration:** About 1–2 years through calendar year 2025; the VPT charge was assessed again for 2025.

## Rationale

Alameda County secured tax bills for this APN itemize a $6,000 Oakland VPT charge for tax years 2025–26 and 2026–27, corresponding to calendar years 2024 and 2025. This strongly supports likely vacancy across those years. Oakland describes VPT notices as likely-vacant determinations that owners may petition; no current site observation or petition disposition confirms physical vacancy.

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
| Tax year 2024–25 | The County bill portal search for APN 1-111-1 showed no MEAS-W Oakland VPT line item. | A useful comparison year; absence of the charge is not proof of occupancy. | [Alameda County secured tax bill search](https://propertytax.alamedacountyca.gov/search) |
| Tax year 2025–26 (calendar year 2024) | The County secured bill portal itemizes “MEAS-W OAKLAND VPT” at $6,000; both installments show paid. | Parcel-specific municipal VPT assessment supports likely vacancy during calendar year 2024. | [Alameda County secured tax bill search](https://propertytax.alamedacountyca.gov/search) |
| Tax year 2026–27 (calendar year 2025) | The County secured bill portal again itemizes “MEAS-W OAKLAND VPT” at $6,000. | A second successive VPT assessment supports likely vacancy during 2025; it does not establish present physical condition or petition outcome. | [Alameda County secured tax bill search](https://propertytax.alamedacountyca.gov/search); [Oakland FY 2026–27 petition form](https://www.oaklandca.gov/files/assets/city/v/1/finance/documents/vpt/vpt-fy-26-27-en/oakland-vpt-petition-of-vacancy-fy-26-27.pdf) |

## Finding payload for the vacancy research queue

```json
{
  "verdict": "likely_vacant",
  "confidence": 0.78,
  "rationale": "Alameda County secured bills for tax years 2025-26 and 2026-27 both itemize a $6,000 MEAS-W Oakland VPT charge, corresponding to calendar years 2024 and 2025. This strongly supports likely vacancy across both years. Oakland treats notices as likely-vacant determinations that can be petitioned; no current site observation or case disposition confirms physical vacancy.",
  "evidence": [
    {"source_url": "https://sf.eater.com/2013/10/8/6356185/venga-paella-homestyle-spanish-in-oakland", "date": "2013-10-08", "summary": "Reported Venga Paella opened at 229 Brush St and described restaurant service."},
    {"source_url": "https://www.alamedamagazine.com/venga-paellas-manchego-con-membrillo/", "date": "2014-07", "summary": "Identified Venga Paella at 229 Brush St and described a meal there."},
    {"source_url": "https://oakland.legistar.com/View.ashx?GUID=6A7754CC-510C-44CB-807A-C0C7E579D7CD&ID=8871387&M=F", "date": "2017", "summary": "City permit B1702334 for 229 Brush St describes restroom accessibility alterations."},
    {"source_url": "https://www.loopnet.com/Listing/225-229-Brush-St-Oakland-CA/9242340/", "date": "2017-09-20", "summary": "Commercial listing history records 225–229 Brush St offered for lease; this does not establish vacancy."},
    {"source_url": "https://dehpra.acgov.org/Lopinfo/ReadFile?record=RO0003196%5CCAP_PP_L_2018-02-05.pdf", "date": "2018-02-05", "summary": "County report lists the APN and address under an occupant field, which may be a mailing entry."},
    {"source_url": "https://www.propertyshark.com/mason/Property/38673400/229-Brush-St-Oakland-CA-94607/", "date": "2025-02-21 to 2026-05-24", "summary": "Third-party property summary lists several permit entries but masks descriptions and statuses; recent permits alone do not establish occupancy."},
    {"source_url": "https://www.mapquest.com/us/california/venga-paella-popup-catering-286045033", "date": "crawled 2025", "summary": "Third-party directory marks the listed restaurant permanently closed; building vacancy is unproven."},
    {"source_url": "https://propertytax.alamedacountyca.gov/search", "date": "2024-25 through 2026-27 tax years", "summary": "County secured tax bill search for APN 1-111-1: no Oakland VPT line in 2024-25; 2025-26 and 2026-27 bills each itemize MEAS-W OAKLAND VPT at $6,000. 2025-26 installments were paid."},
    {"source_url": "https://www.oaklandca.gov/Business/For-Landlords/Vacant-Property-Tax-VPT", "date": "accessed 2026-10-15", "summary": "Oakland explains VPT notices indicate likely vacancy based on available data and can be petitioned."},
    {"source_url": "https://www.oaklandca.gov/files/assets/city/v/1/finance/documents/vpt/vpt-fy-26-27-en/oakland-vpt-petition-of-vacancy-fy-26-27.pdf", "date": "tax year 2026-27", "summary": "Official Oakland petition form maps calendar year 2025 to tax year 2026-27."}
  ],
  "estimated_vacancy_start_date": "during calendar year 2024 (exact date unknown)",
  "estimated_vacancy_duration": "about 1-2 years through calendar year 2025; VPT assessed again for 2025",
  "report_md": "Vacancy start date: during calendar year 2024 (exact date unknown). Estimated duration: about 1-2 years through calendar year 2025, with VPT assessed again for 2025. Alameda County secured tax bill portal records for APN 1-111-1 itemize MEAS-W OAKLAND VPT at $6,000 in tax years 2025-26 and 2026-27, corresponding to calendar years 2024 and 2025; the 2025-26 installments show paid. The 2024-25 bill showed no VPT line. These assessments strongly support likely vacancy during those years, but Oakland describes the notice basis as likely-vacant and allows petitions. No current site observation or petition disposition establishes physical vacancy in 2026. Historic restaurant operation (2013-14), a 2017 permit, the 2018 occupant entry, listing history, and third-party closure label are context only. County bill search: https://propertytax.alamedacountyca.gov/search ; Oakland VPT overview: https://www.oaklandca.gov/Business/For-Landlords/Vacant-Property-Tax-VPT ; City calendar/tax year mapping: https://www.oaklandca.gov/files/assets/city/v/1/finance/documents/vpt/vpt-fy-26-27-en/oakland-vpt-petition-of-vacancy-fy-26-27.pdf"
}
```

## Limits

The subject is a commercial building, so residential utility assumptions were not used. The parcel-specific VPT bill rows are strong evidence of Oakland's likely-vacant determinations for 2024 and 2025, but the bill alone does not establish the City's underlying inspection or data, whether an owner petition changed the determination, or current occupancy in 2026. Oakland's [VPT overview](https://www.oaklandca.gov/Business/For-Landlords/Vacant-Property-Tax-VPT) describes notices as likely-vacant and allows an owner to petition. The County's public search uses APN 1-111-1 for the subject parcel. No current site observation was available.
