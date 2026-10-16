---
# Copy to WORKFLOW.md in the target repository and adjust. Changes are picked up live.
tracker:
  kind: linear
  provider:
    project_slug: your-linear-project-slug   # Linear project "slugId"
    # api_key defaults to $LINEAR_API_KEY (host-side; never passed to the agent)
    # assignee: me                           # only dispatch issues assigned to the key owner
  required_labels: [agent]
  active_states: [Todo, In Progress]
  terminal_states: [Done, Canceled, Cancelled, Duplicate]

polling:
  interval_ms: 30000

workspace:
  root: ~/symphony_workspaces

hooks:
  after_create: |
    git clone --depth 50 https://github.com/your-org/your-repo.git .   # REPLACE with your repo
  before_run: |
    git fetch origin && git status --short
  timeout_ms: 120000

agent:
  max_concurrent_agents: 3
  max_turns: 20
  max_concurrent_agents_by_state:
    "In Progress": 2

codex:
  command: codex app-server
  approval_policy: never
  thread_sandbox: workspace-write
  turn_sandbox_policy:
    type: workspaceWrite
    writableRoots: [$WORKSPACE]
    networkAccess: true          # needed for git push / gh; default is false

# server:
#   port: 4100                   # enables the dashboard at http://127.0.0.1:4100/
---
You are an autonomous engineer working on Linear issue {{ issue.identifier }}: {{ issue.title }}.

{% if attempt %}This is retry/continuation #{{ attempt }}. Inspect the workspace for prior progress
before changing anything.{% endif %}

Issue URL: {{ issue.url }}
Current state: {{ issue.state }}
Labels: {% for label in issue.labels %}{{ label }}{% unless forloop.last %}, {% endunless %}{% endfor %}

Description:
{{ issue.description }}

Workflow:
1. Use the `linear_graphql` tool to move the issue to "In Progress" (issue id: {{ issue.id }}).
2. Implement the change on branch `{{ issue.branch_name }}` with tests.
3. Push the branch, open a pull request, and comment the PR link on the issue.
4. Move the issue to "Human Review" when done. Do not mark it Done yourself.
