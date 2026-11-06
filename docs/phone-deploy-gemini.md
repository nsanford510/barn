# Deploy the assistant from a phone

If you cannot add a GitHub deployment token, the Supabase code editor can deploy
the validated backend directly. The Google key and project ID should already
be in this BARN project's Edge Function secrets.

1. Open the BARN Supabase project (`zezhsmgtdzlagqwlvjui`).
2. Open **Edge Functions → property-assistant → Code** and edit `index.ts`.
   If the function does not exist yet, create it with the name `property-assistant`.
3. Replace the entrypoint with this one line and deploy:

```ts
import "https://raw.githubusercontent.com/nsanford510/barn/4a51813638980d158ab65d5b271ed0bf65e1b53d/web/supabase/functions/property-assistant/index.ts";
```

This imports a fixed, tested commit, including its relative helper modules.
That version passed Deno type checking and all 33 shared-helper tests. It keeps
the Google key in Supabase, retains the BARN sign-in and subscription checks,
and serves both typed chat and native live voice. Leave JWT verification enabled.

The supplied Google key still needs access to `gemini-3.8-live` in `us-central1`.
Once deployed, test a typed property question and a brief voice conversation in
the preview APK. A successful build does not prove the Google key's model access.

The alternative is adding `SUPABASE_ACCESS_TOKEN` to the BARN GitHub repository's
Actions secrets and rerunning **Validate and deploy Gemini assistant**. The
Google API key cannot be used as a Supabase deployment token.
