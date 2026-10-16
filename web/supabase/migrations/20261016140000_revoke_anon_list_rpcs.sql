-- These SECURITY DEFINER list RPCs skip lists' signed-in-only RLS, so keep them away from anon.
DO $$
BEGIN
  IF to_regprocedure('public.append_properties_to_list(bigint, text[])') IS NOT NULL THEN
    REVOKE ALL ON FUNCTION public.append_properties_to_list(BIGINT, TEXT[]) FROM PUBLIC, anon;
    GRANT EXECUTE ON FUNCTION public.append_properties_to_list(BIGINT, TEXT[]) TO authenticated, service_role;
  END IF;
  IF to_regprocedure('public.reorder_list_properties(bigint, text[])') IS NOT NULL THEN
    REVOKE ALL ON FUNCTION public.reorder_list_properties(BIGINT, TEXT[]) FROM PUBLIC, anon;
    GRANT EXECUTE ON FUNCTION public.reorder_list_properties(BIGINT, TEXT[]) TO authenticated, service_role;
  END IF;
END
$$;
