# Research agent kit

Everything a remote agent needs to work the BARN vacancy research queue. Copy
this folder to the agent's machine; it needs Python 3.9+ and nothing else.

## Setup on the agent's machine

1. On the BARN machine, create the agent and note the token it prints (shown
   once):

   ```
   cd scan && source .venv/bin/activate
   python research_queue.py add-agent grokbot-1
   ```

2. On the agent's machine, set three environment variables:

   ```
   export BARN_SUPABASE_URL=https://<project>.supabase.co
   export BARN_SUPABASE_ANON_KEY=<publishable key>   # never the secret/service key
   export BARN_AGENT_TOKEN=<token from step 1>
   ```

3. Check it works: `python research_client.py claim` should print a property (or
   `null` if nothing is queued). Release it again with
   `python research_client.py release --token <claim_token> --reason "setup test"`.

4. Give the agent `AGENT_BRIEF.md` as its instructions.

## Operating

- Seed work: `python research_queue.py enqueue --city OAKLAND` (add `--limit 5`
  for a pilot).
- Target zip codes: tell the agent to claim with `--zip 94601,94607` (repeatable
  or comma-separated; combines with `--city`). It only filters what that agent
  is handed; the properties must already be in the queue.
- Target one property: use `--apn "002 000902900"` to claim that exact APN.
  This option requires the updated vacancy research migration to be applied.
- Watch progress: `python research_queue.py status`, or **Scan > Vacancy** in the
  web UI.
- Retry failures: `python research_queue.py requeue --status failed`.
- Cut an agent off: `python research_queue.py revoke-agent grokbot-1`. Running
  `add-agent` again with the same name issues a new token and re-enables it.

A property is handed to one agent at a time. If an agent goes quiet, its claim
expires and the property goes to the next agent that asks; after three attempts
it is marked failed instead.
