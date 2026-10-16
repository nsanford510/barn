-- Vacancy research queue for remote AI agents.
-- Agents hold only the publishable (anon) key plus a personal agent token. The
-- tables below have RLS on with no policies, so that key alone reads and writes
-- nothing; the only way in is the SECURITY DEFINER functions at the bottom, each
-- of which checks the agent token first.
-- Apply manually via Supabase SQL Editor.

-- ============================================================
-- 1. research_agents — one row per agent, token stored hashed
-- ============================================================
CREATE TABLE IF NOT EXISTS public.research_agents (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT        NOT NULL UNIQUE,
    token_hash   TEXT        NOT NULL UNIQUE,
    active       BOOLEAN     NOT NULL DEFAULT true,
    last_seen_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- 2. vacancy_research_tasks — one row per property to research
-- ============================================================
CREATE TABLE IF NOT EXISTS public.vacancy_research_tasks (
    apn              TEXT        PRIMARY KEY REFERENCES public.bills(apn) ON DELETE CASCADE,
    status           TEXT        NOT NULL DEFAULT 'pending'
                                 CHECK (status IN ('pending', 'claimed', 'done', 'failed')),
    priority         INT         NOT NULL DEFAULT 0,
    claimed_by       TEXT,
    claim_token      UUID,
    lease_expires_at TIMESTAMPTZ,
    attempts         INT         NOT NULL DEFAULT 0,
    last_error       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_vacancy_research_tasks_queue
    ON public.vacancy_research_tasks (status, priority DESC, apn);

CREATE UNIQUE INDEX IF NOT EXISTS idx_vacancy_research_tasks_claim_token
    ON public.vacancy_research_tasks (claim_token)
    WHERE claim_token IS NOT NULL;

-- ============================================================
-- 3. vacancy_findings — append-only, one row per agent run
-- ============================================================
CREATE TABLE IF NOT EXISTS public.vacancy_findings (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    apn        TEXT        NOT NULL REFERENCES public.bills(apn) ON DELETE CASCADE,
    agent      TEXT        NOT NULL,
    verdict    TEXT        NOT NULL CHECK (verdict IN ('likely_vacant', 'likely_occupied', 'unknown')),
    confidence REAL        NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    rationale  TEXT        NOT NULL CHECK (char_length(rationale) BETWEEN 1 AND 500),
    evidence   JSONB       NOT NULL DEFAULT '[]'::jsonb,
    report_md  TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_vacancy_findings_apn_created_at
    ON public.vacancy_findings (apn, created_at DESC);

-- ============================================================
-- 4. Lock the tables down
-- ============================================================
ALTER TABLE public.research_agents        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.vacancy_research_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.vacancy_findings       ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.research_agents, public.vacancy_research_tasks, public.vacancy_findings
    FROM PUBLIC, anon, authenticated;
GRANT ALL ON public.research_agents, public.vacancy_research_tasks, public.vacancy_findings
    TO service_role;

-- ============================================================
-- 5. Token check shared by the RPCs (not callable by agents)
-- ============================================================
CREATE OR REPLACE FUNCTION public._research_agent_name(p_agent_token TEXT)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_name TEXT;
BEGIN
    UPDATE public.research_agents
    SET last_seen_at = now()
    WHERE active
      AND token_hash = encode(sha256(convert_to(COALESCE(p_agent_token, ''), 'UTF8')), 'hex')
    RETURNING name INTO v_name;

    IF v_name IS NULL THEN
        RAISE EXCEPTION 'Unknown or revoked agent token' USING ERRCODE = '42501';
    END IF;
    RETURN v_name;
END;
$$;

REVOKE ALL ON FUNCTION public._research_agent_name(TEXT) FROM PUBLIC, anon, authenticated;

-- ============================================================
-- 6. claim_vacancy_research — hand one property to an agent
--    Returns JSON null when nothing is left to claim.
--    p_zip_codes limits the claim to those 5-digit ZIPs (NULL or empty = any).
--    p_apn limits the claim to one exact APN when supplied.
-- ============================================================
-- Drop older signatures so PostgREST never sees ambiguous overloads.
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
    -- A task whose lease lapsed on its third attempt is not handed out again.
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
      AND (p_apn IS NULL OR TRIM(p_apn) = '' OR t.apn = TRIM(p_apn))
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

-- ============================================================
-- 7. submit_vacancy_finding — record a finding and close the task
-- ============================================================
CREATE OR REPLACE FUNCTION public.submit_vacancy_finding(
    p_agent_token TEXT,
    p_claim_token UUID,
    p_verdict     TEXT,
    p_confidence  REAL,
    p_rationale   TEXT,
    p_evidence    JSONB DEFAULT '[]'::jsonb,
    p_report_md   TEXT  DEFAULT NULL
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

    INSERT INTO public.vacancy_findings (apn, agent, verdict, confidence, rationale, evidence, report_md)
    VALUES (v_task.apn, v_agent, v_verdict, p_confidence, v_rationale, v_evidence, p_report_md)
    RETURNING id INTO v_finding_id;

    UPDATE public.bills
    SET ai_vacancy_verdict = v_verdict,
        ai_vacancy_confidence = p_confidence,
        ai_vacancy_rationale = v_rationale,
        ai_vacancy_updated_at = now()
    WHERE apn = v_task.apn;

    UPDATE public.vacancy_research_tasks
    SET status = 'done',
        lease_expires_at = NULL,
        last_error = NULL,
        updated_at = now()
    WHERE apn = v_task.apn;

    RETURN json_build_object('finding_id', v_finding_id, 'apn', v_task.apn, 'verdict', v_verdict);
END;
$$;

-- ============================================================
-- 8. release_vacancy_research — give a property back unfinished
-- ============================================================
CREATE OR REPLACE FUNCTION public.release_vacancy_research(
    p_agent_token TEXT,
    p_claim_token UUID,
    p_error       TEXT DEFAULT NULL
)
RETURNS JSON
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_agent  TEXT := public._research_agent_name(p_agent_token);
    v_apn    TEXT;
    v_status TEXT;
BEGIN
    UPDATE public.vacancy_research_tasks
    SET status = CASE WHEN attempts >= 3 THEN 'failed' ELSE 'pending' END,
        claim_token = NULL,
        lease_expires_at = NULL,
        last_error = LEFT(COALESCE(NULLIF(TRIM(p_error), ''), 'released by agent'), 1000),
        updated_at = now()
    WHERE claim_token = p_claim_token AND status = 'claimed' AND claimed_by = v_agent
    RETURNING apn, status INTO v_apn, v_status;

    IF v_apn IS NULL THEN
        RAISE EXCEPTION 'Claim not found for this agent' USING ERRCODE = 'P0001';
    END IF;
    RETURN json_build_object('apn', v_apn, 'status', v_status);
END;
$$;

-- ============================================================
-- 9. extend_vacancy_research — more time for a long run
-- ============================================================
CREATE OR REPLACE FUNCTION public.extend_vacancy_research(
    p_agent_token TEXT,
    p_claim_token UUID,
    p_minutes     INTEGER DEFAULT 30
)
RETURNS JSON
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_agent   TEXT := public._research_agent_name(p_agent_token);
    v_apn     TEXT;
    v_expires TIMESTAMPTZ;
BEGIN
    UPDATE public.vacancy_research_tasks
    SET lease_expires_at = now() + make_interval(mins => LEAST(GREATEST(COALESCE(p_minutes, 30), 5), 240)),
        updated_at = now()
    WHERE claim_token = p_claim_token AND status = 'claimed' AND claimed_by = v_agent
    RETURNING apn, lease_expires_at INTO v_apn, v_expires;

    IF v_apn IS NULL THEN
        RAISE EXCEPTION 'Claim not found for this agent' USING ERRCODE = 'P0001';
    END IF;
    RETURN json_build_object('apn', v_apn, 'lease_expires_at', v_expires);
END;
$$;

REVOKE ALL ON FUNCTION public.claim_vacancy_research(TEXT, INTEGER, TEXT, TEXT[], TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.submit_vacancy_finding(TEXT, UUID, TEXT, REAL, TEXT, JSONB, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.release_vacancy_research(TEXT, UUID, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.extend_vacancy_research(TEXT, UUID, INTEGER) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION public.claim_vacancy_research(TEXT, INTEGER, TEXT, TEXT[], TEXT) TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.submit_vacancy_finding(TEXT, UUID, TEXT, REAL, TEXT, JSONB, TEXT) TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.release_vacancy_research(TEXT, UUID, TEXT) TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.extend_vacancy_research(TEXT, UUID, INTEGER) TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
