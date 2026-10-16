-- PostGIS creates public.spatial_ref_sys, which the API exposes without RLS.
-- We can't enable RLS on it (the extension owns the table), so remove write
-- access for the API roles instead. SELECT stays because PostGIS functions
-- such as ST_Transform read this table as the calling role.
DO $$
BEGIN
  IF to_regclass('public.spatial_ref_sys') IS NOT NULL THEN
    REVOKE ALL ON public.spatial_ref_sys FROM anon, authenticated;
    GRANT SELECT ON public.spatial_ref_sys TO anon, authenticated;
  END IF;
END
$$;
