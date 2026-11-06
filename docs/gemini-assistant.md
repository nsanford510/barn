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
not continuous microphone/speaker streaming. Native live voice needs a separate
authenticated audio transport and Android audio/lifecycle work.

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

## Android source to resolve

At BARN commit `1100ac49a43dd865e9f1b8a30a48d48b1cdeb2b6`, `android/` is a
submodule of `Zer0phucks/BARN-scout`, pinned to
`9e56661f31fbc9f1cb52d76938d73ac7d29e8452`. That revision and the checked
`BARN-scout` main branch do not contain `AssistantScreen.kt` or an `assistant/`
package, although the BARN guidelines describe one. Locate the branch/repository
used for the current Android assistant before making native voice changes.
This change does not repoint the submodule or alter either BARN-scout repository.
