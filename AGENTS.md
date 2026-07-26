# AGENTS.md

This file provides guidance to AI coding agents (Claude Code, Codex, and others) when working with code in this repository. It is the source of truth; the sibling `CLAUDE.md` imports it via `@AGENTS.md`.

It is the **monorepo orientation layer**: it maps the whole repo and points to the
module guides that own the depth. For anything inside a module, read that module's
guide rather than expecting full detail here:

- **[backend/AGENTS.md](backend/AGENTS.md)** — backend depth: harness/app split, agent &
  middleware chain, sandbox, MCP, skills, memory, IM channels, persistence/migrations,
  config system, test layout.
- **[frontend/AGENTS.md](frontend/AGENTS.md)** — frontend depth: Next.js App Router layout,
  thread/streaming data flow, code style, commands.

## What is DeerFlow

DeerFlow is a LangGraph-based AI super-agent system with a full-stack architecture. The
backend runs a "super agent" with sandboxed execution, persistent memory, subagent
delegation, and extensible tools (built-in, MCP, community), all per-thread isolated. The
frontend is a Next.js chat UI. External IM platforms (Feishu, Slack, Telegram, Discord,
DingTalk) bridge into the same agent through the Gateway.

## Service Topology

A single `make dev` / Docker stack runs four cooperating services:

| Service         | Port   | Role                                                                 |
| --------------- | ------ | ------------------------------------------------------------------- |
| **Nginx**       | `2026` | Unified reverse-proxy entry point — open this in the browser        |
| **Gateway API** | `8001` | FastAPI REST API + embedded LangGraph-compatible agent runtime      |
| **Frontend**    | `3000` | Next.js web interface                                               |
| **Provisioner** | `8002` | Optional — only when sandbox is configured for provisioner/K8s mode |

Nginx is the single public entry: it serves the frontend and proxies `/api/langgraph/*`
to the Gateway's LangGraph runtime, rewriting it to Gateway's native `/api/*` routes; all
other `/api/*` go straight to the Gateway REST routers. See
[backend/AGENTS.md](backend/AGENTS.md) for the runtime and router detail.

## Repository Map

```
deer-flow/
├── Makefile                        # Root orchestration: drives the full stack (dev/start/stop, docker, setup)
├── config.example.yaml             # Template → copy to config.yaml (gitignored) at repo root
├── extensions_config.example.json  # Template → copy to extensions_config.json (gitignored): MCP servers + skills
├── backend/                        # Python backend — see backend/AGENTS.md
│   ├── Makefile                    # Per-module backend commands (dev, gateway, test, lint, migrate-rev)
│   ├── packages/harness/           # deerflow-harness package (import: deerflow.*) — agent framework
│   └── app/                        # FastAPI Gateway + IM channels (import: app.*)
├── frontend/                       # Next.js frontend (pnpm) — see frontend/AGENTS.md
├── docker/                         # docker-compose files, nginx config, provisioner
├── skills/                         # Agent skills: public/ (committed), custom/ (gitignored)
├── contracts/                      # Cross-component JSON contracts (e.g. subagent status, skill review)
├── scripts/                        # Root orchestration scripts invoked by the Makefile (check, configure, doctor, support_bundle, serve, nginx, docker, deploy, setup_wizard)
├── tests/                          # Root-level tests (currently tests/skills/ — public skill tests)
└── docs/                           # Cross-cutting docs, plans, and design notes
```

Runtime config lives at the **repo root**: copy `config.example.yaml` → `config.yaml`
(main app config) and `extensions_config.example.json` → `extensions_config.json` (MCP
servers + skills). Both real files are gitignored and may be edited at runtime via the
Gateway API. Config schema and resolution order are documented in
[backend/AGENTS.md](backend/AGENTS.md).

Skill quality review note:
- `skills/public/skill-reviewer/` is the built-in read-only skill quality reviewer.
  It uses the harness-layer `review_skill_package` tool and contracts in
  `contracts/skill_review/`. Model-visible review data is compact and
  tag-neutralized; full raw payloads stay in tool artifacts. See
  [backend/AGENTS.md](backend/AGENTS.md) for the non-activation, SkillScan, and
  `skill-creator` ownership boundaries.

Scheduled-task note:
- The scheduled-task MVP adds a workspace page at `/workspace/scheduled-tasks` plus a background scheduler service gated by `config.yaml -> scheduler.enabled`.
- Scheduled background runs are intentionally non-interactive: they execute through the normal run lifecycle, but the lead-agent toolset excludes `ask_clarification` when `context.non_interactive=true`. The key is honored only for internally-authenticated callers (the scheduler launch path); client-supplied `context.non_interactive` is dropped.

Breeding-workspace note:
- **The project owns the data, in a human-visible folder.** A project's
  workspace is a real directory under `config.yaml -> projects.root` (e.g.
  `~/Documents/projects/G2F`) that the human can browse in Finder; the sandbox
  mounts it for every conversation in the project, the file API lists it
  (`GET /api/projects/{id}/files`), and membership is a durable record
  (`threads_meta.project_id`, written via `PUT /api/projects/{id}/threads/{thread}`
  or validated first-run intent). Conversations are views onto that folder,
  not its owner. See [backend/AGENTS.md](backend/AGENTS.md) for the
  storage/scope/run-context triple that must stay aligned.
  Project creation can use the configured default root, adopt an existing
  folder, accept an absolute existing-or-new path, or create a named folder
  under a selected parent. Custom locations must be under `projects.root` or a
  writable `sandbox.mounts` host root. Project-scoped local sandboxes hide an
  overlapping parent mount so `/mnt/user-data/workspace` remains the canonical
  and unambiguous write target.
- The authenticated product surface is project-first with three rails: sidebar
  projects, a project rail (DBTL cycles, per-cycle to-dos, conversations), and
  chat in the main area. Project workspaces live at `/workspace/<project-slug>`
  and their conversations at `/workspace/<project-slug>/<thread_id>`; opening a
  project lands directly in a conversation. Conversations belong to a project
  through thread metadata `project_id`; legacy projectless conversations stay
  at `/workspace/chats`. Cycles/to-dos are a browser-local review projection —
  DBTL orchestration is deferred per the 2026-07-25 foundation-demo rescope.
- The project folder tree appears only under the expandable project rows in
  the first sidebar rail. File selection opens the project-scoped content
  inspector and temporarily collapses that rail; the second project rail does
  not duplicate the project header/tree.
- Memory follows the same durable project scope: project conversations load
  and learn from a `(user_id, project_id)`-specific bucket, while unfiled chats
  retain user-global memory. Older project conversations replace legacy global
  snapshots on their next run, and current project identity overrides stale
  claims in their existing visible history.
- The shared SQL persistence layer owns `workspaces`, `workspace_members`, and
  `projects`; membership is enforced before project access. PostgreSQL is the
  production authority, while `.greenagent` remains an application-independent
  development protocol and projection.
- DBTL Phase 1 adds a durable, project-scoped governance schema and strict
  human-review records, but does not enable cycle execution. Settings → DBTL
  readiness exposes the administrator validation/evidence/cutover checklist;
  SQLite is always cutover-blocked and PostgreSQL approval remains distinct
  from enabling the future Supervisor Graph.
- DBTL Phase 5 adds that Supervisor Graph as a thin, per-run project router,
  fail-closed until `dbtl.mode=graph_enabled`. Interactive threads remain
  durably pinned to `lead_agent`; a runtime-only opt-in selects the supervisor
  for one project request, preserving normal checkpoint access and safe
  rollback. It routes one request to one of four terminal branches — ordinary
  work, clarification, cycle setup/confirmation, existing-cycle continuation —
  and the ordinary branch *is* the existing lead agent. Stage execution remains
  behind a stub that structurally cannot write results or satisfy gates. A
  per-request context chip above the composer shows
  `[ Ordinary project work ▾ ]` or `[ Cycle 01 · Design ▾ ]` and applies to the
  next request only. See [backend/AGENTS.md](backend/AGENTS.md) for the
  reducer-idempotency and stream-contract constraints that make delegation safe.
- DBTL Phase 6 replaces the continuation stub with a production
  `LiveStageAdapter` for Design and Data Reconciliation. It verifies the
  selected cycle belongs to the runtime project, fans bounded work units out
  through `SubagentExecutor`, and atomically records structured worker results
  plus a content-addressed review package in the project workspace. Retries
  replay the durable stage event instead of dispatching workers again. Stages
  are versioned `StageSpec` data, work units declare capabilities rather than
  role names, and workers return a structured `StageWorkerResult` — free-form
  text cannot satisfy a stage contract. Data
  Readiness and Reconciliation is a real gate: every declared input is pinned
  by content hash, raw data must be declared immutable, and Build stays locked
  until every required matrix row is settled. An agent may propose a
  resolution but may never close a *judgement* row (contradictory sources,
  trait direction, exclusions, leakage, train/test separation) — that stays a
  person's decision, enforced at the write boundary. An approval binds the
  dataset fingerprint, stage-spec version, and policy version it was granted
  against, so a later dataset change invalidates it instead of carrying it
  into Build.
- DBTL Phase 7 adds executable Build and Test `StageSpec` contracts while Learn
  remains unavailable until Phase 8. Build can start only at
  `ready_for_build`; every reviewable Build records the approved dataset
  fingerprint, code/config revisions, environment, versioned outputs,
  deviations, and logs. Test stores headline metrics separately from a
  versioned validity pack. Its outcome is computed as `supported`,
  `not_supported`, `inconclusive`, or `invalidated`; a generic stage approval
  cannot bypass that computation. Leakage, broken folds, structure artifacts,
  ceiling/direction failures, holdout failure, unreconciled inputs, or
  irreproducible execution dominate strong headline metrics. A server-owned
  human reviewer chooses only an outcome-compatible route to Learn, repeat,
  Design, Reconciliation, Build, or cycle closure. See
  [backend/AGENTS.md](backend/AGENTS.md) for persistence and routing contracts
  and [frontend/AGENTS.md](frontend/AGENTS.md) for the two-column review model.
- New cycles started from the project rail automatically queue the current
  `generic:design:v2` Design council in project chat. Its context includes a
  bounded project-file manifest, declared cycle inputs, prior council turns,
  and the latest human clarification. It always produces at least an
  independent design position and red-team position before a chair synthesis.
  A `needs_input` chair result renders through the existing
  `ask_clarification` human-input card and resumes in the same selected cycle.
  A completed synthesis is emitted through the existing `present_files`
  message shape and thread artifact inspector; it only creates review evidence
  and never submits or approves the human gate. The
  presentation icon beside Cycles opens a client-only Phase 7 three-case demo
  and never mutates durable records.

## Commands: Root vs. Module

**Root `make` targets drive the whole stack** (run from the repo root):

```bash
make setup       # Interactive setup wizard (recommended for new users)
make doctor      # Check configuration and system requirements
make support-bundle  # Generate redacted troubleshooting summary, AI issue draft, and optional zip
make config      # Generate local config files from the examples
make check       # Check that required tools are installed
make install     # Install all dependencies (frontend + backend + pre-commit hooks)
make dev         # Start all services with hot-reload (Gateway + Frontend + Nginx)
make start       # Start all services in production mode (local, optimized)
make stop        # Stop all running services
make up / down   # Build/stop the production Docker stack (browser at localhost:2026)
make docker-start / docker-stop / docker-logs   # Docker development environment
```

Run `make help` for the full list.

**Per-module commands drive a single module** (run inside that module):

```bash
# Backend (see backend/AGENTS.md for the full set)
cd backend && make dev        # Gateway API with reload (port 8001)
cd backend && make test       # Backend test suite
cd backend && make lint       # ruff check
cd backend && make format     # ruff format

# Frontend (see frontend/AGENTS.md for the full set)
cd frontend && pnpm dev       # Dev server with Turbopack (port 3000)
cd frontend && pnpm check     # Lint + type check (run before committing)
cd frontend && pnpm test      # Unit tests
```

Rule of thumb: **root `make` = the full application**; **`backend/Makefile` and `frontend/`
(`pnpm`) = per-module work.**

## Where to Go Next

- Backend work → **[backend/AGENTS.md](backend/AGENTS.md)**
- Frontend work → **[frontend/AGENTS.md](frontend/AGENTS.md)**
- Setup & install → **[Install.md](Install.md)**, **[CONTRIBUTING.md](CONTRIBUTING.md)**
- Project overview & usage → **[README.md](README.md)** (translations: `README_zh.md`,
  `README_ja.md`, `README_fr.md`, `README_ru.md`)
- Security policy → **[SECURITY.md](SECURITY.md)**
- Changes → **[CHANGELOG.md](CHANGELOG.md)**
- Cutting a release → **[RELEASING.md](RELEASING.md)**

## Cross-Cutting Conventions

These apply repo-wide; module guides own the module-specific detail.

- **Documentation update policy** — keep docs in sync with code: update `README.md` for
  user-facing changes and the relevant `AGENTS.md` for development/architecture changes in
  the same change set.
- **Test-driven development** — features and bug fixes ship with tests. Backend tests live
  in `backend/tests/` (TDD is mandatory there; see [backend/AGENTS.md](backend/AGENTS.md));
  frontend tests live in `frontend/tests/`.
- **Format before pushing** — run `make format` (backend) / `pnpm check` (frontend). Backend
  CI enforces `ruff format --check`, so formatting must be clean before a push.
