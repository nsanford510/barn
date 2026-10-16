-- Route queue RPCs for authenticated users.
-- These helpers preserve list_properties.sort_order deterministically while
-- keeping anonymous access blocked.

CREATE OR REPLACE FUNCTION public.append_properties_to_list(
    p_list_id BIGINT,
    p_apns TEXT[]
)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    next_sort_order INTEGER;
    inserted_count INTEGER := 0;
    current_apn TEXT;
BEGIN
    IF auth.uid() IS NULL THEN
        RAISE EXCEPTION 'BARN scanner access requires an authenticated session'
            USING ERRCODE = '42501';
    END IF;

    SELECT COALESCE(MAX(sort_order) + 1, 0)
    INTO next_sort_order
    FROM public.list_properties
    WHERE list_id = p_list_id;

    FOR current_apn IN
        WITH requested AS (
            SELECT trim(value) AS apn, MIN(ord) AS ord
            FROM unnest(COALESCE(p_apns, ARRAY[]::TEXT[])) WITH ORDINALITY AS u(value, ord)
            WHERE trim(value) <> ''
            GROUP BY trim(value)
            ORDER BY MIN(ord)
        )
        SELECT apn FROM requested ORDER BY ord
    LOOP
        IF EXISTS (
            SELECT 1
            FROM public.list_properties
            WHERE list_id = p_list_id
              AND apn = current_apn
        ) THEN
            CONTINUE;
        END IF;

        INSERT INTO public.list_properties (list_id, apn, sort_order)
        VALUES (p_list_id, current_apn, next_sort_order)
        ON CONFLICT (list_id, apn)
        DO UPDATE SET sort_order = EXCLUDED.sort_order;

        next_sort_order := next_sort_order + 1;
        inserted_count := inserted_count + 1;
    END LOOP;

    RETURN inserted_count;
END;
$$;

REVOKE ALL ON FUNCTION public.append_properties_to_list(BIGINT, TEXT[]) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.append_properties_to_list(BIGINT, TEXT[]) TO authenticated;

CREATE OR REPLACE FUNCTION public.reorder_list_properties(
    p_list_id BIGINT,
    p_apns TEXT[]
)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    existing_apns TEXT[];
    requested_apns TEXT[];
    current_apn TEXT;
    sort_order INTEGER := 0;
    updated_count INTEGER := 0;
BEGIN
    IF auth.uid() IS NULL THEN
        RAISE EXCEPTION 'BARN scanner access requires an authenticated session'
            USING ERRCODE = '42501';
    END IF;

    SELECT COALESCE(array_agg(apn ORDER BY sort_order, apn), ARRAY[]::TEXT[])
    INTO existing_apns
    FROM public.list_properties
    WHERE list_id = p_list_id;

    IF COALESCE(array_length(existing_apns, 1), 0) = 0 THEN
        RETURN 0;
    END IF;

    WITH requested AS (
        SELECT trim(value) AS apn, MIN(ord) AS ord
        FROM unnest(COALESCE(p_apns, ARRAY[]::TEXT[])) WITH ORDINALITY AS u(value, ord)
        WHERE trim(value) <> ''
        GROUP BY trim(value)
        ORDER BY MIN(ord)
    )
    SELECT COALESCE(array_agg(apn ORDER BY ord), ARRAY[]::TEXT[])
    INTO requested_apns
    FROM requested
    WHERE apn = ANY(existing_apns);

    FOR current_apn IN
        SELECT unnest(requested_apns)
    LOOP
        INSERT INTO public.list_properties (list_id, apn, sort_order)
        VALUES (p_list_id, current_apn, sort_order)
        ON CONFLICT (list_id, apn)
        DO UPDATE SET sort_order = EXCLUDED.sort_order;
        sort_order := sort_order + 1;
        updated_count := updated_count + 1;
    END LOOP;

    FOR current_apn IN
        SELECT unnest(existing_apns)
    LOOP
        IF current_apn = ANY(requested_apns) THEN
            CONTINUE;
        END IF;

        INSERT INTO public.list_properties (list_id, apn, sort_order)
        VALUES (p_list_id, current_apn, sort_order)
        ON CONFLICT (list_id, apn)
        DO UPDATE SET sort_order = EXCLUDED.sort_order;
        sort_order := sort_order + 1;
        updated_count := updated_count + 1;
    END LOOP;

    RETURN updated_count;
END;
$$;

REVOKE ALL ON FUNCTION public.reorder_list_properties(BIGINT, TEXT[]) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.reorder_list_properties(BIGINT, TEXT[]) TO authenticated;
