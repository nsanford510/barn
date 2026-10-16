# Vacancy research: 2617 San Pablo Ave, Oakland

- **APN:** 003-0005-003-00 (Alameda shorthand 3-5-3)
- **Address:** 2617 San Pablo Ave, Oakland, CA 94612
- **Candidate verdict:** `likely_vacant`
- **Confidence:** 0.72
- **Rationale:** A prior official Alameda County lookup reported a $3,000 MEAS-W OAKLAND VPT charge on the parcel's FY 2026-27 secured bill. A current portal lookup confirms that FY 2026-27 secured account exists but its summary does not itemize charges. Oakland ties VPT to under-50-days-in-use during calendar year 2025. This supports possible vacancy in 2025, but the VPT line, start date, continuity, unit-level occupancy, and 2026 status remain unverified.
- **Vacancy period:** Calendar year 2025 is the supported assessment period. No continuous vacancy start or end date is established. The VPT threshold implies over 315 aggregate non-use days that year if the reported assessment is correct; this does not establish one continuous vacancy spell.
- **Queue status:** Not submitted. The required BARN agent token is unavailable in this runtime, so no claim or finding was created.

## Evidence

1. **County tax assessment (reported prior lookup, 2026-10-15).** The current public portal lookup for APN 3-5-3 returned a 2026-2027 Secured account, tracer 01387600, total $5,862.18; its account summary does not itemize special charges. A prior issue note reports a $3,000 `MEAS-W OAKLAND VPT` line on that bill, but the bill image is not retained and the line was not independently confirmed here. The portal's View Bill form redirected (HTTP 303) to its homepage. A candidate direct county download URL also returned HTTP 404 on 2026-10-16: https://acgov.org/ptax_pub_app/PublicDownload.do?deviceId=TPP&deviceType=TPP&getStreamInfo=true&pubBillId=01387600&pubBillType=SEC&pubCurrRollYear=2026&tokenId=TPP. These responses do not establish that the reported VPT charge is absent. [Alameda County property tax portal](https://propertytax.alamedacountyca.gov/search)
2. **Vacancy rule.** Oakland's VPT page says a property is considered vacant when it is in use fewer than 50 days in a calendar year, subject to exemptions. [City of Oakland VPT](https://www.oaklandca.gov/Business/For-Landlords/Vacant-Property-Tax-VPT)
3. **Assessment year.** The FY 2026-27 petition form applies to a Notice of Vacancy for calendar year 2025. [Oakland FY 2026-27 Petition of Vacancy](https://www.oaklandca.gov/files/assets/city/v/1/finance/documents/vpt/vpt-fy-26-27-en/oakland-vpt-petition-of-vacancy-fy-26-27.pdf)
4. **Parcel identity.** Oakland's parcel GIS query matches SORTPARCEL `003 000500300` to the subject address. [Oakland parcel GIS query](https://gismaps.oaklandca.gov/server/rest/services/Accela/Parcels/MapServer/0/query?where=SORTPARCEL%3D%27003%20000500300%27%26outFields=SORTPARCEL%2CFULL_SITUSADDRESS%2CSHP_USECODE%26returnGeometry=false%26f=pjson)

## Limits

The tax assessment is a strong indicator for the 2025 VPT classification, not direct observation of occupancy. The bill artifact and any petition, exemption, or correction are not retained here. The evidence does not establish a continuous start date, current 2026 occupancy, or occupancy of each residential/commercial portion. Recheck the county bill and City VPT disposition before treating the parcel-specific charge as independently verified.
