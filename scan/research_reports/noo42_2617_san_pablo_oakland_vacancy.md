# Vacancy research: 2617 San Pablo Ave, Oakland

- **APN:** 003-0005-003-00 (Alameda shorthand 3-5-3)
- **Address:** 2617 San Pablo Ave, Oakland, CA 94612
- **Candidate verdict:** `likely_vacant`
- **Confidence:** 0.78
- **Rationale:** A prior official Alameda County lookup reported a $3,000 MEAS-W OAKLAND VPT charge on the parcel's FY 2026-27 secured bill. Oakland ties VPT to under-50-days-in-use during calendar year 2025. This supports likely vacancy during 2025, but the bill image is not retained here; start date, continuity, unit-level occupancy, and 2026 status remain unverified.
- **Vacancy period:** Calendar year 2025 is the supported assessment period. No continuous vacancy start or end date is established. The VPT threshold implies over 315 aggregate non-use days that year if the reported assessment is correct; this does not establish one continuous vacancy spell.
- **Queue status:** Not submitted. The required BARN agent token is unavailable in this runtime, so no claim or finding was created.

## Evidence

1. **County tax assessment (reported prior lookup, 2026-10-15).** The prior lookup note records an FY 2026-27 secured bill for APN 3-5-3, tracer 01387600, with a $3,000 `MEAS-W OAKLAND VPT` line. This session did not retrieve or retain the bill image, so treat the parcel-specific charge as prior-session evidence pending recheck. This session also tried the following candidate county download URL on 2026-10-16 and received HTTP 404: https://acgov.org/ptax_pub_app/PublicDownload.do?deviceId=TPP&deviceType=TPP&getStreamInfo=true&pubBillId=01387600&pubBillType=SEC&pubCurrRollYear=2026&tokenId=TPP. Since the endpoint parameters may be wrong, this response does not establish whether the bill exists. [Alameda County property tax portal](https://propertytax.alamedacountyca.gov/search)
2. **Vacancy rule.** Oakland's VPT page says a property is considered vacant when it is in use fewer than 50 days in a calendar year, subject to exemptions. [City of Oakland VPT](https://www.oaklandca.gov/Business/For-Landlords/Vacant-Property-Tax-VPT)
3. **Assessment year.** The FY 2026-27 petition form applies to a Notice of Vacancy for calendar year 2025. [Oakland FY 2026-27 Petition of Vacancy](https://www.oaklandca.gov/files/assets/city/v/1/finance/documents/vpt/vpt-fy-26-27-en/oakland-vpt-petition-of-vacancy-fy-26-27.pdf)
4. **Parcel identity.** Oakland's parcel GIS query matches SORTPARCEL `003 000500300` to the subject address. [Oakland parcel GIS query](https://gismaps.oaklandca.gov/server/rest/services/Accela/Parcels/MapServer/0/query?where=SORTPARCEL%3D%27003%20000500300%27%26outFields=SORTPARCEL%2CFULL_SITUSADDRESS%2CSHP_USECODE%26returnGeometry=false%26f=pjson)

## Limits

The tax assessment is a strong indicator for the 2025 VPT classification, not direct observation of occupancy. The bill artifact and any petition, exemption, or correction are not retained here. The evidence does not establish a continuous start date, current 2026 occupancy, or occupancy of each residential/commercial portion. Recheck the county bill and City VPT disposition before treating the parcel-specific charge as independently verified.
