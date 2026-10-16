---
# Changes are picked up live.
tracker:
  kind: linear
  provider:
    project_slug: barn-b64634e7def4   # Linear project "slugId"
    # api_key defaults to $LINEAR_API_KEY (host-side; never passed to the agent)
    # assignee: me                           # only dispatch issues assigned to the key owner
  required_labels: [symphony]
  active_states: [Todo, In Progress]
  terminal_states: [Done, Canceled, Cancelled, Duplicate]

polling:
  interval_ms: 30000

workspace:
  root: ~/symphony_workspaces

hooks:
  after_create: |
    git clone --depth 50 https://github.com/nsanford510/barn.git .
  before_run: |
    # Write BARN research-queue credentials into a gitignored env file in the workspace.
    # Values come from files already on this machine; nothing is printed or committed.
    umask 077
    SCAN=/home/noob/barn/scan
    getv() { grep -E "^$1=" "$SCAN/.env" 2>/dev/null | tail -1 | cut -d= -f2- | sed -e "s/^[\"']//" -e "s/[\"']\$//"; }
    {
      echo "BARN_SUPABASE_URL='$(getv SUPABASE_URL)'"
      echo "BARN_SUPABASE_ANON_KEY='$(getv SUPABASE_PUBLISHABLE_KEY)'"
      if [ -f "$HOME/.config/barn/research_agent.env" ]; then
        grep -E '^BARN_AGENT_TOKEN=' "$HOME/.config/barn/research_agent.env"
      fi
    } > .barn_agent.env
    mkdir -p .git/info
    grep -qxF .barn_agent.env .git/info/exclude 2>/dev/null || echo .barn_agent.env >> .git/info/exclude
    git fetch origin && git status --short
  timeout_ms: 120000

agent:
  max_concurrent_agents: 8
  max_turns: 20
  max_concurrent_agents_by_state:
    "In Progress": 8

codex:
  command: codex app-server
  approval_policy: never
  thread_sandbox: workspace-write
  turn_sandbox_policy:
    type: workspaceWrite
    writableRoots: [$WORKSPACE]
    networkAccess: true          # needed for web research and the BARN research RPCs

# server:
#   port: 4100                   # enables the dashboard at http://127.0.0.1:4100/
---
You are a vacancy-research agent working on Linear issue {{ issue.identifier }}: {{ issue.title }}.

{% if attempt %}This is retry/continuation #{{ attempt }}. Check the Linear issue comments and the workspace for prior progress
before starting over. Do not post a duplicate verdict if one already exists on the issue.{% endif %}

Issue URL: {{ issue.url }}
Current state: {{ issue.state }}
Labels: {% for label in issue.labels %}{{ label }}{% unless forloop.last %}, {% endunless %}{% endfor %}

Description (identifies the ONE property you research):
{{ issue.description }}

YOUR JOB: determine whether this one property is actually vacant and, if so, for how long. This is research only.
DO NOT implement code changes, edit the repository, create branches, or open pull requests. Do not write
docs/report files into the repo.

Rules: use public evidence only (listing/sale history, permits, code-enforcement/blight cases, utility signals,
dated street-view imagery, news, tax and vacancy-tax records). Do not contact the owner, residents or neighbours and
do not log in to any account. Note the date of every source; evidence older than about two years is weak for the
present state. Never fabricate a URL or a detail. An honest "unknown" with low confidence is a good result.

Workflow:

1. Use the `linear_graphql` tool to move the issue to "In Progress" (issue id: {{ issue.id }}).
2. Research the property from the description above (address, APN, city, ZIP).
3. Decide:
   - verdict: exactly one of likely_vacant, likely_occupied, unknown
   - confidence: number from 0 to 1
   - rationale: plain text, 500 characters or fewer
   - estimated vacancy start date and length of vacancy (for example "vacant since about 2019-03, roughly 7.5 years"),
     with the evidence and source URLs behind it; say "not determinable" if it is unknown.
   - evidence: a list of items, each with signal, detail, source_url (http/https) and observed_at (YYYY or YYYY-MM).
     likely_vacant / likely_occupied require at least one working source_url.
4. Record the finding in the BARN research queue using the client, never by touching database tables directly.
   The credentials are in the gitignored file `.barn_agent.env` in your workspace (do NOT print, echo or commit it):
       set -a; . ./.barn_agent.env; set +a
       python3 /home/noob/barn/scan/research_agent_kit/research_client.py claim
   The client reads BARN_SUPABASE_URL, BARN_SUPABASE_ANON_KEY and BARN_AGENT_TOKEN. Write the finding as JSON
   (claim_token, verdict, confidence, rationale, evidence, vacant_since, report_md) and submit it.
   vacant_since is the estimated vacancy start as "YYYY", "YYYY-MM" or "YYYY-MM-DD", only as precise as the evidence
   supports: the earliest dated source showing it vacant if the true start is unknown, or today minus the duration if
   you only know a duration. Omit vacant_since when it is not determinable or the verdict is likely_occupied; BARN
   computes the length of vacancy from it. Also describe the start date and length in report_md:
       python3 /home/noob/barn/scan/research_agent_kit/research_client.py submit --file finding.json
   Save finding.json in the workspace root and delete it after submitting.
   Notes:
   - claim hands out the next queued property and cannot target an APN. If the claimed property is the one in this
     issue, submit your finding for it. If it is a different property, research and submit the claimed one (do not
     release it, releases burn its retry attempts), mention the claimed APN in your issue comment, and ALSO include your
     findings for this issue's own property in the comment.
   - A non-zero exit means the call was refused; read stderr and do not retry a refused submit unchanged.
   - If BARN_AGENT_TOKEN is missing or empty, or the RPC fails, do NOT fail silently: do not claim anything, and
     start the Linear comment with "NOT RECORDED IN BARN DB: <reason>" followed by the full finding.
5. Use `linear_graphql` to add a comment on the issue with: verdict, confidence, estimated vacancy start and length of
   vacancy, rationale, and the source links; state whether it was recorded in the BARN DB.
6. Move the issue to "Done" once the finding is recorded (or, if recording was impossible, once the full finding is
   posted in the comment marked as not recorded). Never use "Human Review". To find the Done state id, query the
   issue's team workflow states with `linear_graphql`.
