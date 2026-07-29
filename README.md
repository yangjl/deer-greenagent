# GreenAgent

GreenAgent is a project-first AI workspace for plant-breeding research. It is
built on [ByteDance DeerFlow 2.0](https://github.com/bytedance/deer-flow) and
adds human-visible project folders, project-scoped memory, and durable
Design–Build–Test–Learn (DBTL) governance.

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](./backend/pyproject.toml)
[![Node.js](https://img.shields.io/badge/Node.js-22%2B-339933?logo=node.js&logoColor=white)](./Makefile)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

## What GreenAgent adds

- **Project-owned data** — each project is a normal folder that remains visible
  and editable outside GreenAgent.
- **Project-first workspace** — projects organize conversations, files, DBTL
  cycles, evidence, reviews, and work items. The project rail can collapse to
  an icon-width navigation spine for Cycles, Blockers, Agents, and
  Conversations when the conversation needs more room. Agent and Skill
  management remain directly accessible from the first rail. The Agents page
  separates built-in agents and runtime-available subagents from user-created
  agents and deployment-configured custom subagents.
- **Safe workspace cleanup** — project, cycle, and conversation rails expose
  removal actions when expanded; folded icon rails hide destructive controls.
  Project removal archives the project without deleting its local folder and
  returns its conversations to Unfiled chats; cycle removal records an audited
  abandonment; conversation deletion removes the thread.
- **Scoped memory** — project conversations use project-specific memory rather
  than leaking context across unrelated projects.
- **Durable DBTL governance** — cycle state, evidence, blockers, revisions, and
  human reviews are stored in the application database. Build captures
  reproducibility lineage, while Test keeps headline performance separate from
  validity checks so leakage or failed holdouts cannot be presented as success.
  Learn creates provisional candidates from eligible outcomes; only an explicit
  human promotion creates project knowledge, and publication to each additional
  project is a separate, retractable decision.
- **Human-controlled automation** — AI may recommend or route DBTL work, but it
  cannot satisfy scientific gates or create authoritative results by itself.
  Starting a cycle launches a project-grounded Design council: independent
  specialist and red-team positions are synthesized by a chair, which either
  asks one focused clarification or presents a Design package for human review.
  After the meeting convenes, its registered slide deck is the input surface:
  the owner can answer the chair, submit the exact Design package, and record a
  final verdict there. The authenticated parent verifies the exact deck and
  evidence hashes before enabling controls; downloaded, stale, or wrong-thread
  copies remain read-only. Approval language in ordinary chat does not mutate
  the gate or rerun the council.
- **One native setup interaction** — DBTL setup and confirmation are emitted
  through DeerFlow's existing `ask_clarification` Human Input Card in the chat
  transcript. Internal model prompts, structured drafting responses, subagent
  reasoning, and subagent tool output stay out of the visible conversation;
  council progress remains available through its task timeline. Shadow
  proposal evaluation records telemetry only; it does not mount a second card
  beside the composer.
- **Inspectable Design debate** — before a council runs, chat shows its roster,
  depth choices, stand-ins, and recommendation. Choosing a depth first updates
  the visible participant list; a separate confirmation starts exactly that
  approved roster, including its per-seat model choices. During the run it
  reports validated seat progress and counted consensus, and labels a
  chair-only fallback as a partial synthesis when another participant failed;
  common structured claim objects from participants are normalized without
  discarding an otherwise valid position, while unsupported claims still fail
  evidence validation;
  Light debate is a quick-pilot contract with six model calls per seat, a
  24-entry manifest, and at most two targeted file reads rather than a smaller
  headcount running full research budgets. Design-council token use is metered,
  shown per participant and in aggregate, and recorded in the review package;
  it is not currently used to discard a participant result. Light
  exposes only targeted file reads—not shell or research tools—and missing
  data/packages become review limitations rather than Design blockers. If the
  strict chair contract still fails, DeerFlow creates a clearly labeled pilot
  draft from cycle metadata and recoverable meeting output so a person can
  approve it into Data reconciliation; Medium and Heavy retain strict evidence
  contracts and also meter tokens without enforcing a token cap;
  if the chair pauses for a decision, the resumed synthesis keeps that same
  chair model and participant settings rather than inheriting the current chat
  model;
  council cards remain bound to their server-resolved cycle even if the
  browser's temporary cycle selection is lost while the card is waiting;
  the waiting meeting remains scoped to that conversation, so a new project
  chat opens cleanly and can start a separate cycle;
  after a scoped request is accepted the cycle selection is consumed, and
  ordinary read/explain follow-ups go to normal chat instead of dispatching
  the council again;
  the final feedback deck pairs the structured decision map with the exact
  Markdown evidence and records the reviewed deck projection separately.
- **DeerFlow capabilities** — sandboxed execution, tools, skills, MCP,
  subagents, persistent conversations, and multiple model providers, including
  local reuse of Claude Code and Codex subscription logins through the
  CLI-backed providers.

## Quick start

Requirements: Python 3.12+, Node.js 22+, pnpm 10.26+, and optionally Docker.

```bash
make setup
make doctor
make dev
```

Open [http://localhost:2026](http://localhost:2026).

For a production-style Docker deployment:

```bash
make up
```

Run `make help` for all commands. See [Install.md](./Install.md) for detailed
installation guidance.

### Faster manual DBTL testing

Developers iterating on DBTL review or Design-deck interactions can capture a
quiet human-decision checkpoint once, then restore its isolated SQLite database
and project artifacts together instead of rerunning the expensive meeting.

```bash
make dbtl-manual-init
make dbtl-manual-dev
# Stop at a completed run or human-input boundary:
make dbtl-manual-capture SCENARIO=chair-choice

make stop

make dbtl-manual-dev
make dbtl-manual-restore SCENARIO=chair-choice

```

The pipeline is local and gitignored; it does not add a product stage bypass.
See the [Manual DBTL testing runbook](./docs/dbtl-manual-test-pipeline.md) for
the repeatable checklist, checkpoint strategy, safety rules, and troubleshooting.

## Configuration

Runtime configuration lives at the repository root:

- `config.yaml` — application, models, projects, database, sandbox, and DBTL
- `extensions_config.json` — MCP servers and skills
- `.env` — secrets

Generate local configuration with `make setup` or `make config`. The files
`config.yaml`, `extensions_config.json`, and `.env` are intentionally
gitignored.

Important GreenAgent settings:

```yaml
projects:
  root: ~/Documents/GreenAgentProjects

dbtl:
  mode: graph_enabled # disabled | audit_only | manual | graph_enabled
  classifier_shadow_enabled: true
  proposals_visible: true
  design_deck_feedback: true # false restores legacy Design controls during rollback
```

DBTL modes:

- `audit_only` — readiness and legacy inventory are visible; mutations and
  graph execution remain blocked.
- `manual` — authorized humans can operate durable DBTL cycles.
- `graph_enabled` — enables the opt-in project supervisor. Design,
  Reconciliation, Build, Test, and Learn use bounded, versioned stage contracts;
  every scientific gate still requires a human decision.

The DBTL classifier gives borderline research-shaped requests a modestly higher
proposal prior on the first turn of a conversation and in projects with no
unfinished cycles, especially projects with no cycles yet. These lifecycle
signals cannot propose a cycle without positive research language, and starting
the durable record still requires explicit human confirmation. A typed request
such as "start a cycle" enters native DBTL setup directly, even when the project
already has unfinished cycles.

PostgreSQL is the intended production database. SQLite is suitable for local
development but cannot be approved for DBTL production cutover.

## Architecture

| Service     | Port | Purpose                                         |
| ----------- | ---: | ----------------------------------------------- |
| Nginx       | 2026 | Browser entry point                             |
| Gateway     | 8001 | REST API and agent runtime                      |
| Frontend    | 3000 | Next.js workspace                               |
| Provisioner | 8002 | Optional remote/Kubernetes sandbox provisioning |

The project folder is the canonical research workspace. Conversations are
views onto that folder, not owners of its data.

## Development

```bash
# Full stack
make dev

# Backend
cd backend
make test
make lint

# Frontend
cd frontend
pnpm test
pnpm check
```

Backend features and bug fixes require tests. Keep user-facing documentation
and the relevant `AGENTS.md` synchronized with architectural changes.
Gateway hot reload watches backend runtime code and configuration, but excludes
`backend/tests/` so editing or formatting tests does not interrupt the running
development server.

### Replay a DeerFlow run in LLM Space

`backend/scripts/export_llm_space_thread.py` converts DeerFlow's persisted
message projection into a native LLM Space Thread. Start with the bundled
sample:

```bash
cd backend
PYTHONPATH=. .venv/bin/python scripts/export_llm_space_thread.py \
  --sample \
  --output ~/.llm-space/workspace/DeerFlow-debug/deerflow-exporter-smoke-test.json
```

With DeerFlow running, export a real thread or one run:

```bash
DEERFLOW_COOKIE='your authenticated session cookie' \
PYTHONPATH=. .venv/bin/python scripts/export_llm_space_thread.py \
  --thread-id THREAD_ID \
  --run-id RUN_ID \
  --output ~/.llm-space/workspace/DeerFlow-debug/failed-run.json
```

Use `DEERFLOW_BEARER_TOKEN` instead when the deployment accepts bearer
authentication. The exporter preserves visible user/assistant messages, pairs
tool results with their calls, and infers replay-only function schemas from
the observed arguments. The generated tools do not execute automatically in
LLM Space, and the persisted message feed does not contain DeerFlow's complete
effective system prompt; provide one with `--system-prompt-file` when exact
prompt reproduction matters. Pass `--force` only when intentionally replacing
an existing Thread file.

## Git workflow

- `upstream/main` — canonical ByteDance DeerFlow
- `main` — unchanged mirror of `upstream/main`
- `greenagent/main` — primary GreenAgent branch
- `feat/*` — GreenAgent feature branches
- `integrate/upstream-YYYY-MM-DD` — selective upstream integration branches

Do not merge upstream directly into `greenagent/main`. Review upstream commits
individually, cherry-pick relevant fixes or enhancements onto an integration
branch, preserve GreenAgent refactors, test, and then fast-forward
`greenagent/main`.

## Documentation

- [AGENTS.md](./AGENTS.md) — repository architecture and agent instructions
- [backend/AGENTS.md](./backend/AGENTS.md) — backend design and test guidance
- [frontend/AGENTS.md](./frontend/AGENTS.md) — frontend design and commands
- [SECURITY.md](./SECURITY.md) — security policy
- [CONTRIBUTING.md](./CONTRIBUTING.md) — contribution guide

## Upstream and license

GreenAgent is a maintained customization of
[ByteDance DeerFlow](https://github.com/bytedance/deer-flow). Upstream changes
are incorporated selectively to protect GreenAgent’s project, memory, and DBTL
architecture.

Licensed under the [MIT License](./LICENSE).
