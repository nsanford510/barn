# Gemini 3.8 Live for the Scout assistant

`property-assistant` now uses the same Google Agent Platform model and server-side
configuration names as Claimtrace's Alex relay: `gemini-3.8-live` in `us-central1`.
The old Bedrock/Kimi transport is no longer called by the function.

The existing Android request and response stay compatible:

```json
{"messages":[{"role":"user","text":"Who owns this property?"}],"apn":"123"}
```

The response is still `{ "reply": "...", "steps": [], "sources": [] }`. Gemini
Live produces audio; the function requests its output transcription and returns
that transcript. Existing on-device speech recognition and text-to-speech can
continue using this API. This is a model replacement for the existing chat API,
also compatible with on-device voice turns. The rebuilt Android Assistant now
uses a separate WebSocket mode on this same endpoint for native live voice.

## Server configuration

Bind these secrets to the **BARN Supabase project** from the same secret source
Claimtrace uses:

- `GOOGLE_AGENT_PLATFORM_API_KEY`
- `GOOGLE_AGENT_PLATFORM_PROJECT`
- `BRAVE_API_KEY` (existing web search key)

Claimtrace's deployment script refers to `/etc/claimtrace/claimtrace.env` on its
GCP host. Repository access does not grant access to that file or its values.
Do not commit the env file or add these credentials to Android `BuildConfig`,
resources, `local.properties`, or the APK. Provision through a secret binding
or a protected env file with `supabase secrets set --env-file <protected-file>`.
No credential values are included in this change.

Optional overrides are `ASSISTANT_GEMINI_LOCATION` and `ASSISTANT_GEMINI_MODEL`.
They default to `us-central1` and `gemini-3.8-live`. Existing `FIRECRAWL_URL` and
`FIRECRAWL_KEY` still enable page reads. Google API availability and the key's
authorization must be verified against the actual project before deployment;
the model and region match Claimtrace's code, not an authenticated probe here.

After provisioning, deploy only the function:

```sh
supabase functions deploy property-assistant --project-ref zezhsmgtdzlagqwlvjui
```

No database migration is needed. Requests still require a valid BARN user and
`has_app_access()` before opening a Google connection or using service-role reads.
The function closes Google sockets after each answer, at 110 seconds, on errors,
and when the caller cancels. Tool calls are capped at six per request. Upstream
errors are kept out of the response and logs to avoid revealing credentials.

## Validation

```sh
node --experimental-strip-types web/supabase/functions/_shared/gemini-live.test.ts
deno test web/supabase/functions/_shared/
```

Before production, exercise a signed-in paid/admin user's question, a property
lookup plus web search, a missing Firecrawl configuration, and denied anonymous
and unsubscribed users. Confirm `reply`, `steps`, and `sources` in Android and
check cold-start/response latency. The full function and real Gemini connection
need a Supabase/Deno runtime and the server credentials.

## Native Android live voice

The rebuilt app is in `nsanford510/BARN-scout`, branch `codex/gemini-live-android`,
based on the Android revision BARN already used. It adds an Assistant tab and an
Ask Assistant action to property sheets. Typed questions use the existing POST
contract. Live voice connects with an authenticated GET WebSocket upgrade to
`/functions/v1/property-assistant`. Supabase JWT validation and `has_app_access()`
run before either transport opens a Google session.

The phone sends `{type: "start", messages, apn}`, then binary 16 kHz mono PCM16.
It sends `{type: "mic", open: false}` after the last microphone frame when the
user taps Finish speaking. The relay returns binary 24 kHz mono PCM16 plus JSON
`ready`, `user`/`assistant` (running transcripts), `turn`, `activity`, `sources`,
`interrupted`, `error`, and `ended` events. Typed input during voice uses
`{type: "text", text}`. The Google key is never sent to Android. Voice sessions
stop on screen exit, backgrounding, sign-out, disconnect, 90 seconds of idle,
or an eight-minute cap. The user taps Live voice to reconnect.

The preview is debug-signed under `com.vpt.scout.geminipreview` to install beside
production without its private signing key. If Actions has no Supabase public
key, its setup screen accepts a publishable/anon key and rejects service-role
and other secret-key types. Optional Maps key configuration still comes from
Actions secrets. The preview workflow runs unit tests before publishing an APK
as a prerelease download.

## Deployment workflow

`.github/workflows/gemini-assistant.yml` checks the function with Deno and runs
all shared-helper tests. It deploys only `property-assistant` when the repository
has a `SUPABASE_ACCESS_TOKEN` Actions secret. The Google credentials already in
Supabase are not deployment credentials. Create a personal access token from
Supabase account settings, add it as a GitHub Actions secret, and rerun the
workflow. For a phone-only alternative, see [the Supabase editor steps](phone-deploy-gemini.md). No database migrations, Claimtrace updates, or user secrets are
included in the APK or repository.

A successful build and deployment do not establish the Google project's model
permissions. Verify a signed-in question and live conversation on a device.
