-- Structured vacancy start for the vacancy research queue (additive).
-- Agents submit vacant_since as YYYY, YYYY-MM or YYYY-MM-DD: the estimated start of the
-- vacancy, or the earliest date the evidence shows it vacant. The UI derives the length of
-- vacancy from it, so the figure stays current without re-research.
-- Apply in Supabase SQL Editor after supa_migration_vacancy_research.sql.

ALTER TABLE public.vacancy_findings ADD COLUMN IF NOT EXISTS vacant_since TEXT;
ALTER TABLE public.bills            ADD COLUMN IF NOT EXISTS ai_vacant_since TEXT;

COMMENT ON COLUMN public.vacancy_findings.vacant_since IS 'YYYY | YYYY-MM | YYYY-MM-DD; estimated vacancy start';
COMMENT ON COLUMN public.bills.ai_vacant_since IS 'vacant_since from the latest vacancy finding';

-- ============================================================
-- submit_vacancy_finding gains p_vacant_since. The old signature is dropped so PostgREST
-- has one candidate; callers that omit the new argument get the default.
-- ============================================================
DROP FUNCTION IF EXISTS public.submit_vacancy_finding(TEXT, UUID, TEXT, REAL, TEXT, JSONB, TEXT);

CREATE OR REPLACE FUNCTION public.submit_vacancy_finding(
    p_agent_token  TEXT,
    p_claim_token  UUID,
    p_verdict      TEXT,
    p_confidence   REAL,
    p_rationale    TEXT,
    p_evidence     JSONB DEFAULT '[]'::jsonb,
    p_report_md    TEXT  DEFAULT NULL,
    p_vacant_since TEXT  DEFAULT NULL
)
RETURNS JSON
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_agent      TEXT := public._research_agent_name(p_agent_token);
    v_task       public.vacancy_research_tasks%ROWTYPE;
    v_verdict    TEXT := LOWER(TRIM(COALESCE(p_verdict, '')));
    v_rationale  TEXT := TRIM(COALESCE(p_rationale, ''));
    v_evidence   JSONB := COALESCE(p_evidence, '[]'::jsonb);
    v_since      TEXT := NULLIF(TRIM(COALESCE(p_vacant_since, '')), '');
    v_since_date DATE;
    v_finding_id UUID;
BEGIN
    -- The claim token changes whenever a task is handed out again, so a token
    -- that still matches a claimed task means nobody else has picked it up.
    SELECT * INTO v_task
    FROM public.vacancy_research_tasks
    WHERE claim_token = p_claim_token AND status = 'claimed'
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Claim not found: it was already submitted, released, or reassigned after its lease expired'
            USING ERRCODE = 'P0001';
    END IF;
    IF v_task.claimed_by IS DISTINCT FROM v_agent THEN
        RAISE EXCEPTION 'Claim belongs to another agent' USING ERRCODE = '42501';
    END IF;

    IF v_verdict NOT IN ('likely_vacant', 'likely_occupied', 'unknown') THEN
        RAISE EXCEPTION 'verdict must be likely_vacant, likely_occupied or unknown' USING ERRCODE = '22023';
    END IF;
    IF p_confidence IS NULL OR p_confidence < 0 OR p_confidence > 1 THEN
        RAISE EXCEPTION 'confidence must be a number from 0 to 1' USING ERRCODE = '22023';
    END IF;
    IF char_length(v_rationale) = 0 OR char_length(v_rationale) > 500 THEN
        RAISE EXCEPTION 'rationale is required and must be 500 characters or fewer' USING ERRCODE = '22023';
    END IF;
    IF jsonb_typeof(v_evidence) <> 'array' THEN
        RAISE EXCEPTION 'evidence must be a JSON array' USING ERRCODE = '22023';
    END IF;
    IF v_verdict <> 'unknown' AND NOT EXISTS (
        SELECT 1 FROM jsonb_array_elements(v_evidence) AS e
        WHERE jsonb_typeof(e) = 'object' AND (e->>'source_url') ~* '^https?://'
    ) THEN
        RAISE EXCEPTION 'a % verdict needs at least one evidence item with a source_url; submit unknown if there is none', v_verdict
            USING ERRCODE = '22023';
    END IF;

    IF v_since IS NOT NULL THEN
        IF v_since !~ '^\d{4}(-\d{2}(-\d{2})?)?$' THEN
            RAISE EXCEPTION 'vacant_since must be YYYY, YYYY-MM or YYYY-MM-DD (omit it if not determinable)' USING ERRCODE = '22023';
        END IF;
        BEGIN
            v_since_date := (CASE char_length(v_since)
                                 WHEN 4 THEN v_since || '-01-01'
                                 WHEN 7 THEN v_since || '-01'
                                 ELSE v_since END)::date;
        EXCEPTION WHEN others THEN
            RAISE EXCEPTION 'vacant_since % is not a real date', v_since USING ERRCODE = '22023';
        END;
        IF v_since_date < DATE '1900-01-01' OR v_since_date > current_date THEN
            RAISE EXCEPTION 'vacant_since must be between 1900 and today' USING ERRCODE = '22023';
        END IF;
    END IF;
    -- An occupied property has no running vacancy; drop it rather than refuse the finding.
    IF v_verdict = 'likely_occupied' THEN
        v_since := NULL;
    END IF;

    INSERT INTO public.vacancy_findings (apn, agent, verdict, confidence, rationale, evidence, report_md, vacant_since)
    VALUES (v_task.apn, v_agent, v_verdict, p_confidence, v_rationale, v_evidence, p_report_md, v_since)
    RETURNING id INTO v_finding_id;

    UPDATE public.bills
    SET ai_vacancy_verdict = v_verdict,
        ai_vacancy_confidence = p_confidence,
        ai_vacancy_rationale = v_rationale,
        ai_vacant_since = v_since,
        ai_vacancy_updated_at = now()
    WHERE apn = v_task.apn;

    UPDATE public.vacancy_research_tasks
    SET status = 'done',
        lease_expires_at = NULL,
        last_error = NULL,
        updated_at = now()
    WHERE apn = v_task.apn;

    RETURN json_build_object('finding_id', v_finding_id, 'apn', v_task.apn, 'verdict', v_verdict,
                             'vacant_since', v_since);
END;
$$;

REVOKE ALL ON FUNCTION public.submit_vacancy_finding(TEXT, UUID, TEXT, REAL, TEXT, JSONB, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.submit_vacancy_finding(TEXT, UUID, TEXT, REAL, TEXT, JSONB, TEXT, TEXT)
    TO anon, authenticated, service_role;

-- ============================================================
-- map_markers (read by the list, gallery and map views): same columns, plus ai_vacant_since.
-- ============================================================
CREATE OR REPLACE VIEW public.map_markers AS
 SELECT b.apn,
    b.location_of_property AS location,
    b.parcel_number,
    b.tracer_number,
    b.city,
    b.zip_code,
    b.power_status,
    b.has_vpt,
    b.vpt_marker,
    b.delinquent,
    b.tax_year,
    b.last_payment,
    b.bill_url,
    b.condition_score,
    b.streetview_image_path,
    b.research_status,
    b.ai_vacancy_verdict,
    b.owner_name,
    b.deceased_count,
    b.prop_last_sale_date AS last_sale_date,
    b.property_search_url,
    b.mailing_search_url,
    b.outreach_score,
    b.outreach_stage,
    b.added_at,
    b.lat,
    b.lng,
    p.row_json ->> 'MailingAddress'::text AS mailing_address,
    p.row_json ->> 'SitusAddress'::text AS situs_address,
    p.row_json ->> 'SitusZip'::text AS situs_zip,
    COALESCE(NULLIF(upper(TRIM(BOTH FROM p.row_json ->> 'MailState'::text)), ''::text), 'CA'::text) <> 'CA'::text AS is_out_of_state,
    fav.apn IS NOT NULL AS is_favorite,
    sr.apn IS NOT NULL AS is_scouted,
    b.ai_vacancy_confidence,
    b.site_vacant,
    b.ai_vacant_since
   FROM bills b
     LEFT JOIN parcels p ON p.apn = b.apn
     LEFT JOIN LATERAL ( SELECT 1 AS apn
           FROM scout_results s
          WHERE s.apn = b.apn
         LIMIT 1) sr ON true
     LEFT JOIN LATERAL ( SELECT 1 AS apn
           FROM list_properties lp
             JOIN lists l ON l.id = lp.list_id
          WHERE lp.apn = b.apn AND l.name = 'Favorites'::text
         LIMIT 1) fav ON true
  WHERE b.lat IS NOT NULL AND b.lng IS NOT NULL;

NOTIFY pgrst, 'reload schema';
