-- Approved-users-only access for the scanner web UI and the Android Scout app.
-- Signing in is no longer enough: the account also needs an 'admin' or 'scout'
-- row in public.user_roles. Safe to re-run, and safe on a database where the
-- original web/ role migration never ran.

-- 1. Roles table (same shape as 20260201191757, created here if missing).
DO $$
BEGIN
  IF to_regtype('public.app_role') IS NULL THEN
    CREATE TYPE public.app_role AS ENUM ('admin', 'moderator', 'user', 'scout');
  END IF;
END
$$;
ALTER TYPE public.app_role ADD VALUE IF NOT EXISTS 'scout';

CREATE TABLE IF NOT EXISTS public.user_roles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
  role public.app_role NOT NULL,
  UNIQUE (user_id, role)
);
ALTER TABLE public.user_roles ENABLE ROW LEVEL SECURITY;

-- Users may read their own roles; only the service role (or SQL editor) grants them.
DROP POLICY IF EXISTS user_roles_select_own ON public.user_roles;
CREATE POLICY user_roles_select_own ON public.user_roles
  FOR SELECT TO authenticated USING (user_id = auth.uid());
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.user_roles FROM anon, authenticated;
GRANT SELECT ON public.user_roles TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.user_roles TO service_role;

-- 2. Role helpers. Roles are compared as text so this file works in the same
-- transaction that adds the 'scout' enum value.
CREATE OR REPLACE FUNCTION public.is_admin()
RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.user_roles
    WHERE user_id = auth.uid() AND role::text = 'admin'
  )
$$;

CREATE OR REPLACE FUNCTION public.has_app_access()
RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.user_roles
    WHERE user_id = auth.uid() AND role::text IN ('admin', 'scout')
  )
$$;

REVOKE ALL ON FUNCTION public.is_admin() FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.has_app_access() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.is_admin() TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.has_app_access() TO authenticated, service_role;

-- 3. Scanner tables: replace "any signed-in user" policies with approved users.
DO $$
DECLARE
  t text;
  p record;
BEGIN
  FOREACH t IN ARRAY ARRAY['bills', 'parcels', 'lists', 'list_properties', 'scout_results', 'favorites'] LOOP
    IF to_regclass('public.' || t) IS NULL THEN
      CONTINUE;
    END IF;
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    -- Drop every policy that lets authenticated users in unconditionally.
    FOR p IN
      SELECT policyname FROM pg_policies
      WHERE schemaname = 'public' AND tablename = t
        AND 'authenticated' = ANY (roles) AND coalesce(qual, 'true') = 'true'
    LOOP
      EXECUTE format('DROP POLICY %I ON public.%I', p.policyname, t);
    END LOOP;
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_approved_users', t);
    IF t IN ('bills', 'parcels') THEN
      EXECUTE format(
        'CREATE POLICY %I ON public.%I FOR SELECT TO authenticated USING (public.has_app_access())',
        t || '_approved_users', t);
    ELSE
      EXECUTE format(
        'CREATE POLICY %I ON public.%I FOR ALL TO authenticated USING (public.has_app_access()) WITH CHECK (public.has_app_access())',
        t || '_approved_users', t);
    END IF;
    EXECUTE format('REVOKE ALL ON public.%I FROM anon', t);
  END LOOP;
END
$$;

-- 4. SECURITY DEFINER scanner RPCs skip RLS and only checked "is signed in".
-- Swap that check for has_app_access() without retyping each function body.
DO $$
DECLARE
  fn text;
  def text;
BEGIN
  FOREACH fn IN ARRAY ARRAY[
    'public.append_properties_to_list(bigint, text[])',
    'public.reorder_list_properties(bigint, text[])'
  ] LOOP
    IF to_regprocedure(fn) IS NULL THEN
      CONTINUE;
    END IF;
    def := pg_get_functiondef(to_regprocedure(fn));
    def := regexp_replace(def, 'auth\.uid\(\)\s+is\s+null', 'NOT public.has_app_access()', 'gi');
    def := replace(def, 'requires an authenticated session', 'requires an approved account');
    EXECUTE def;
  END LOOP;
END
$$;

-- get_bills_filtered has several overloads; patch whichever exist.
DO $$
DECLARE
  f oid;
  def text;
BEGIN
  FOR f IN
    SELECT p.oid FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public' AND p.proname = 'get_bills_filtered' AND p.prosecdef
  LOOP
    def := regexp_replace(pg_get_functiondef(f), 'auth\.uid\(\)\s+is\s+null', 'NOT public.has_app_access()', 'gi');
    def := replace(def, 'requires an authenticated session', 'requires an approved account');
    EXECUTE def;
  END LOOP;
END
$$;
