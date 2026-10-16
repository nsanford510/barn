# Repository Guidelines

## Project Structure & Module Organization
BARN is a monorepo. `web/` contains the Vite + React + TypeScript site; app code lives in `web/src`, tests in `web/src/test`, and Supabase assets in `web/supabase/`. `scan/` contains the Python scanner and Flask UI, with reusable modules in `scan/scanner/`, tests in `scan/tests/`, and service units in `scan/service/`. `android/` contains the Kotlin Scout app under `android/app/src/main` with JVM tests in `android/app/src/test`. `symphony/` contains the Symphony agent orchestrator (Python, spec: https://github.com/openai/symphony/blob/main/SPEC.md) with modules in `symphony/symphony/` and tests in `symphony/tests/`. Root `deploy.sh` and `scripts/` handle deployment and maintenance.

## Build, Test, and Development Commands
- `cd web && npm install && npm run dev` starts the web app locally.
- `cd web && npm run build`, `npm run lint`, `npm run test` build, lint, and run Vitest once.
- `cd scan && ./install.sh` creates `.venv`, installs Python deps, and installs Playwright Chromium.
- `cd scan && source .venv/bin/activate && python run_all.py --city=OAKLAND` runs the scanner plus Flask UI.
- `cd scan && source .venv/bin/activate && python -m unittest discover -s tests` runs scanner tests.
- `cd symphony && source ../scan/.venv/bin/activate && pip install -r requirements.txt && python -m unittest discover -s tests` runs Symphony tests; `python -m symphony path/to/WORKFLOW.md --port 4100` runs the orchestrator.
- `cd android && ./gradlew test` runs JVM tests; `cd android && ./gradlew assembleDebug` builds a debug APK.

## Coding Style & Naming Conventions
Follow the existing style in each app. In `web/`, use 2-space indentation, PascalCase for components/pages (`AdminDashboard.tsx`), camelCase for helpers, and keep shared primitives in `web/src/components/ui/`. In `scan/`, use 4-space indentation, type hints where practical, snake_case module names, and keep reusable logic in `scan/scanner/`. In `android/`, use Kotlin conventions: 4-space indentation, PascalCase types, and package names under `com.vpt.scout`. Do not hand-edit `web/src/integrations/supabase/types.ts`.

## Testing Guidelines
Web tests use Vitest with jsdom and should follow `src/**/*.{test,spec}.{ts,tsx}`. Python tests use `unittest` and should be named `test_*.py`. Android unit tests use JUnit4 and should end with `Test.kt`. Any behavior change should include or update the nearest relevant test.

## Commit & Pull Request Guidelines
Recent history follows Conventional Commit prefixes such as `feat:`, `fix:`, `docs:`, `style:`, and `chore:`. Keep messages imperative and concise, for example `fix: disambiguate get_bills_filtered rpc overloads`. PRs should state which surface changed (`web`, `scan`, `android`), list verification steps run, note any env/schema/service changes, and include screenshots for user-facing UI updates.

## Security & Configuration Tips
Never commit `.env` files, Supabase keys, `MAPS_API_KEY`, or deploy SSH secrets. Scanner setup may require `playwright install chromium` after dependency changes. Android local secrets belong in `android/local.properties`; web and scan secrets belong in local `.env` files.

## Auth
`scan/webgui` (deployed to Vercel as `barn-scan`, root dir `scan/`) and the Android app are invite-only: a Supabase account needs an `admin` or `scout` row in `public.user_roles` (checked by `has_app_access()`, migration `web/supabase/migrations/20261016150000_scout_role_app_access.sql`). Flask routes are protected by a global `before_request` guard; add new public routes to `PUBLIC_ENDPOINTS` in `scan/webgui/app.py` and admin-only ones with `@admin_required`. Webgui tests that hit protected routes should use `signed_in_client()` from `scan/tests/webgui_auth_helpers.py`.

## Cursor Cloud specific instructions

Standard build/test/run commands live in the sections above and in `web/README.md` / `scan/README.md`; the notes below only capture what is non-obvious for this cloud environment.

### Backend: both apps are thin clients over Supabase
`web/` and `scan/` do almost nothing without a Supabase backend (Postgres + Auth + PostgREST). For local dev we run a local Supabase stack via Docker. The dependency update script only refreshes JS/Python deps — it does NOT start Docker, Supabase, or the dev servers. Start those yourself each VM boot:

- Start the Docker daemon (not auto-started): `sudo dockerd > /tmp/dockerd.log 2>&1 &` then `sudo chmod 666 /var/run/docker.sock` (or re-login so the `docker` group applies).
- Start Supabase (from `web/`, which holds `supabase/config.toml` + migrations): `cd web && supabase start`. This applies `web/supabase/migrations` and prints URL/keys (API on `http://127.0.0.1:54321`, DB on `:54322`, Studio on `:54323`).
- The env files `web/.env.local` and `scan/.env` are gitignored and already point at the local stack using the standard Supabase local demo keys. `web/` reads `VITE_SUPABASE_PUBLISHABLE_KEY` (NOT `VITE_SUPABASE_ANON_KEY`, despite `web/.env.example`).

### GOTCHA: grant DML after `supabase start` (and after adding migrations)
The local stack's default privileges grant `anon`/`authenticated` only `Dxtm` (no `SELECT/INSERT/UPDATE/DELETE`), so migration-created tables reject public form inserts with `permission denied for table` until you grant DML. Run once against the DB container after `supabase start`:

```sql
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO anon, authenticated, service_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO anon, authenticated, service_role;
-- keep PostGIS's spatial_ref_sys read-only for API roles (see migration 20261016120000)
DO $$ BEGIN IF to_regclass('public.spatial_ref_sys') IS NOT NULL THEN REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.spatial_ref_sys FROM anon, authenticated; END IF; END $$;
```
Apply with: `docker exec -i "$(docker ps --format '{{.Names}}' | grep supabase_db)" psql -U postgres -d postgres`.

### Web RLS behavior (not a bug)
Public forms (`property_reports`, `volunteers`, etc.) allow anon `INSERT` but only admins can `SELECT`. So `.insert()` works, but an anon `.insert().select()` / `Prefer: return=representation` fails RLS by design. The public form components correctly insert without selecting.

### scan test suite: 3 known failures without production schema
`python -m unittest discover -s tests` passes 55/58. The 3 `test_gallery_city_filter` failures call `db.get_lists()` which needs base tables `lists`/`bills`/`parcels` that are NOT checked into the repo (only incremental migrations exist in `scan/db_migrations/`). They require a production-like Supabase schema and are expected to fail against the local stack.

### Misc
- `android/` is empty in this checkout — nothing to build/test there.
- Playwright/PG&E power scanning is optional and disabled (`VPT_ENABLE_PGE=false`); no Chromium download is needed for lint/test/build/run.
- Ports: web `8080`, scan Flask `5000`, Supabase API `54321` / DB `54322` / Studio `54323`.
