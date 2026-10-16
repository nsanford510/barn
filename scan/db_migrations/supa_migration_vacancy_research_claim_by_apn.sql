-- Target a single vacancy-research task without claiming unrelated queue items.
-- APNs are normalized to digits so county spacing/dash formatting is accepted.

CREATE OR REPLACE FUNCTION public.claim_vacancy_research_for_apn(
    p_agent_token   TEXT,
    p_apn           TEXT,
    p_lease_minutes INTEGER DEFAULT 45
)
RETURNS JSON
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_agent      TEXT := public._research_agent_name(p_agent_token);
    v_lease      INTEGER := LEAST(GREATEST(COALESCE(p_lease_minutes, 45), 5), 240);
    v_apn_digits TEXT := regexp_replace(COALESCE(p_apn, ''), '[^0-9]', '', 'g');
    v_apn        TEXT;
    v_token      UUID := gen_random_uuid();
    v_result     JSON;
BEGIN
    IF v_apn_digits = '' THEN
        RAISE EXCEPTION 'p_apn must contain digits' USING ERRCODE = '22023';
    END IF;

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
    WHERE regexp_replace(COALESCE(t.apn, ''), '[^0-9]', '', 'g') = v_apn_digits
      AND (t.status = 'pending' OR (t.status = 'claimed' AND t.lease_expires_at < now()))
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

REVOKE ALL ON FUNCTION public.claim_vacancy_research_for_apn(TEXT, TEXT, INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.claim_vacancy_research_for_apn(TEXT, TEXT, INTEGER)
    TO anon, authenticated, service_role;
