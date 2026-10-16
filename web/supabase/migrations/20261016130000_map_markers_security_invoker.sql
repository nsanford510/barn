-- map_markers ran with its owner's rights, so anon could read every row past
-- the base tables' RLS. Run it as the caller and keep it signed-in only.
DO $$
BEGIN
  IF to_regclass('public.map_markers') IS NOT NULL THEN
    ALTER VIEW public.map_markers SET (security_invoker = true);
    REVOKE ALL ON public.map_markers FROM anon;
  END IF;
END
$$;
