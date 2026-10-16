-- Allow a research agent to claim one known APN without claiming unrelated
-- properties from the same city or ZIP. Apply after
-- supa_migration_vacancy_research.sql, then reload the PostgREST schema cache.

DROP FUNCTION IF EXISTS public.claim_vacancy_research(TEXT, INTEGER, TEXT);
DROP FUNCTION IF EXISTS public.claim_vacancy_research(TEXT, INTEGER, TEXT, TEXT[]);

CREATE OR REPLACE FUNCTION public.claim_vacancy_research(
    p_agent_token   TEXT,
    p_lease_minutes INTEGER DEFAULT 45,
    p_city          TEXT    DEFAULT '',
    p_zip_codes     TEXT[]  DEFAULT NULL,
    p_apn           TEXT    DEFAULT NULL
)
RETURNS JSON
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_agent  TEXT := public._research_agent_name(p_agent_token);
    v_lease  INTEGER := LEAST(GREATEST(COALESCE(p_lease_minutes, 45), 5), 240);
    v_apn    TEXT;
    v_token  UUID := gen_random_uuid();
    v_result JSON;
BEGIN
    UPDATE public.vacancy_research_tasks
    SET status = 'failed',
        last_error = COALESCE(last_error, 'lease expired'),
        claim_token = NULL,
        lease_expires_at = NULL,
        updated_at = now()
    WHERE status = 'claimed' AND lease_expires_at < now() AND attempts >= 3;

    IF (SELECT COUNT(*) FROM public.vacancy_research_tasks
        WHERE status = 'claimed' AND claimed_by = v_agent AND lease_expires_at >= now()) >= 2 THEN
        RAISE EXCEPTION 'Agent % already holds 2 live claims; submit or release one first', v_agent
            USING ERRCODE = 'P0001';
    END IF;

    SELECT t.apn INTO v_apn
    FROM public.vacancy_research_tasks t
    JOIN public.bills b ON b.apn = t.apn
    WHERE (t.status = 'pending' OR (t.status = 'claimed' AND t.lease_expires_at < now()))
      AND (p_city = '' OR p_city IS NULL OR LOWER(b.city) = LOWER(p_city))
      AND (COALESCE(cardinality(p_zip_codes), 0) = 0 OR LEFT(TRIM(b.zip_code), 5) = ANY(p_zip_codes))
      AND (NULLIF(BTRIM(p_apn), '') IS NULL OR b.apn = BTRIM(p_apn))
    ORDER BY t.priority DESC, t.apn
    LIMIT 1
    FOR UPDATE OF t SKIP LOCKED;

    IF v_apn IS NULL THEN
        RETURN NULL;
    END IF;

    UPDATE public.vacancy_research_tasks
    SET status = 'claimed',
        claimed_by = v_agent,
        claim_token = v_token,
        lease_expires_at = now() + make_interval(mins => v_lease),
        attempts = attempts + 1,
        updated_at = now()
    WHERE apn = v_apn;

    SELECT json_build_object(
        'claim_token',          v_token,
        'lease_expires_at',     t.lease_expires_at,
        'attempt',              t.attempts,
        'apn',                  b.apn,
        'address',              b.location_of_property,
        'city',                 b.city,
        'zip_code',             b.zip_code,
        'owner_name',           b.owner_name,
        'mailing_address',      p.row_json->>'MailingAddress',
        'property_search_url',  b.property_search_url,
        'mailing_search_url',   b.mailing_search_url,
        'has_vpt',              b.has_vpt,
        'delinquent',           b.delinquent,
        'power_status',         b.power_status,
        'condition_score',      b.condition_score,
        'condition_notes',      b.condition_notes,
        'site_vacant',          b.site_vacant,
        'mail_vacant',          b.mail_vacant,
        'owner_occupied',       b.owner_occupied,
        'lat',                  b.lat,
        'lng',                  b.lng
    ) INTO v_result
    FROM public.vacancy_research_tasks t
    JOIN public.bills b ON b.apn = t.apn
    LEFT JOIN public.parcels p ON p.apn = b.apn
    WHERE t.apn = v_apn;

    RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION public.claim_vacancy_research(TEXT, INTEGER, TEXT, TEXT[], TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.claim_vacancy_research(TEXT, INTEGER, TEXT, TEXT[], TEXT)
    TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
