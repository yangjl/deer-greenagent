# Design: Chat-Oriented → Project-Oriented DeerFlow

> **Status: superseded and consolidated.** This plan is retained as source
> material. The authoritative integrated Designer plan is
> [`workspace-oriented-breeding-system-design.md`](workspace-oriented-breeding-system-design.md).
> In particular, the earlier proposal that `.greenagent` remain authoritative
> is no longer valid: the human has directed that PostgreSQL is the final
> production authority and `.greenagent` is a temporary development
> policy/projection adapter.

**Role:** designer · **Status:** superseded · **Date:** 2026-07-24
**Goal:** Convert deer-flow from a chat-first UI into a long-term breeding
project management system where DBTL (greenagent) cycles, AI role agents
(designer/builder/tester/learner), and human coworkers collaborate around
durable projects — not ephemeral conversations.

---

## 1. Design principles

1. **Project is the root entity; chat becomes a tool inside it.** Today every
   surface hangs off a thread. Target: every thread, file, run, cycle, and
   review hangs off a project.
2. **greenagent stays the deterministic authority.** DBTL state, work items,
   handoffs, and gates live in `.greenagent/` files validated by the CLI.
   deer-flow *renders* that state and *executes* stage work; it never becomes
   a second source of truth. (Mirrors the dbtl_orchestrator design: graph
   proposes, greenagent permits.)
3. **Additive, upstream-pullable.** New routers, new routes, new tables via
   alembic — no rewriting of the chat/run internals we just pulled 1058
   commits of. The chat page survives intact as a project sub-view.
4. **Domain-agnostic core, breeding as configuration.** Same rule as the DBTL
   harness: seasons, trials, germplasm, Slurm are personas/skills/knowledge/
   mounts in a project's config — never baked into the platform.
5. **Humans are first-class actors.** The `users` table + auth already exist.
   Role assignment (human vs agent per DBTL role) is a per-project decision,
   inspectable in greenagent's `assign` records.

## 2. Current state (inventory)

- **Frontend shell:** sidebar = New chat / Chats / Agents / Scheduled tasks /
  Recent chats (`workspace-sidebar.tsx`, `workspace-nav-chat-list.tsx`).
  Chat page owns everything; right-panel system (sidecar/browser/artifacts/
  files) is per-thread. Files panel already supports a per-thread **project
  link** (`/api/threads/{id}/project`) — the seed of project orientation.
- **Backend:** `threads_meta(thread_id, assistant_id, user_id, display_name,
  status, metadata_json)`; runs/run_events per thread; per-thread sandbox
  dirs; custom agents (SOUL.md + config.yaml per user); scheduled tasks;
  `dbtl_orchestrator` run target (Phase 1, gated by greenagent CLI, committed
  d62ab7e2); Console API (cross-thread stats, no UI page yet).
- **greenagent (v0.10.0):** initialized in-repo; role contracts in
  `.greenagent/agents/*.yaml`; work items / handoffs / dbtl-cycles as files;
  `doctor`, `dashboard`, `work`, `assign`, `dbtl validate/check-transition`.
- **Observability:** Langfuse cloud tracing live for lead-agent runs; DBTL
  factory not yet traced; DBTL emits no task_* events (invisible in UI).

## 3. Target information architecture

```
Sidebar (global)                    Project home (per project)
├── Projects        ◀ NEW root      ├── Overview   — goal, phase, current DBTL state,
│   ├── BGEM-UAV-GS                 │                next gate, recent activity
│   └── …                           ├── Cycles     — DBTL timeline: D→B→T→L per cycle,
├── Chats (cross-project inbox)     │                gate history, greenagent validate status
├── Agents (roster)                 ├── Work       — work items + handoffs (greenagent files)
├── Scheduled tasks                 ├── Conversations — project-scoped threads (chat page as-is)
└── Settings                        ├── Files      — the Files tree rooted at the project folder
                                    ├── Team       — humans (users) + agents per DBTL role,
                                    │                assignment status, trust prompts
                                    ├── Knowledge  — .greenagent knowledge index + memory docs
                                    └── Settings   — linked folder, mounts, personas, skills
```

A breeding project maps naturally: **project = breeding program**, **cycle =
season/experiment iteration**, work items = trials/analyses, knowledge = the
program's accumulated evidence (heritability estimates, GS models, provenance).

## 4. Domain model (new, additive)

| Entity | Storage | Notes |
|---|---|---|
| `projects` | new table (alembic) | id, name, goal, owner_user_id, root folder (host path under a mount), greenagent_scope (path to repo with `.greenagent/`), status, created/updated |
| `project_members` | new table | project_id, user_id \| agent_name, dbtl_role (designer/builder/tester/learner/none), added_by |
| thread → project | `threads_meta.metadata_json.project_id` (no migration) or nullable column later | new-chat-in-project stamps it; existing threads = "unassigned" |
| DBTL cycle/work/handoff | **not stored** — read live from `.greenagent/` via CLI/files | deer-flow caches per request only |
| per-thread project link | superseded: threads inherit their project's folder; `project_link.json` remains the low-level mechanism (the thread's link is written from the project at thread creation) |

## 5. Phased plan

### Phase A — Project entity + shell (foundation, ~1 wk)
- Backend: `projects` + `project_members` tables (alembic rev), CRUD router
  `/api/projects` (owner-checked), thread creation accepts `project_id` and
  stamps `metadata_json` + writes the thread's `project_link.json` from the
  project folder. Thread list endpoint gains a `project_id` filter.
- Frontend: `/workspace/projects` (list + create: name, goal, pick folder from
  mount candidates — reuses the project-candidates endpoint) and
  `/workspace/projects/[id]` shell with tab nav (Overview stub, Conversations,
  Files). Sidebar gets Projects section above Chats. Chat page mounted
  unchanged under `/workspace/projects/[id]/chats/[thread_id]`
  (old route stays for unassigned chats).
- Files tab = existing FileTreePanel rooted at the project folder, full-page.

### Phase B — greenagent read integration (visibility, ~1 wk)
- Backend: `/api/projects/{id}/greenagent/{cycles,work,handoffs,roles}` — thin
  read-only adapters over the `.greenagent/` files + `greenagent … --json`
  CLI (same subprocess pattern as `greenagent_cli.py`; never write).
- Frontend: Overview (current cycle state, next gate), Cycles timeline, Work
  list, Team tab (role contracts + `assigned_agent` from role YAMLs).
- Console page: `/workspace/projects/[id]/activity` over the existing
  Console API filtered by project threads (first consumer of that data layer).

### Phase C — DBTL execution in the UI (the core loop, ~2 wks)
- DBTL factory: attach `build_tracing_callbacks()` + Langfuse metadata
  (small, do first — already flagged in memory).
- DBTL orchestrator emits `task_started/task_running/task_completed` per
  stage → stages render as live subtask cards in chat with zero frontend work;
  Cycles tab subscribes to the same run stream for a live stage board.
- Human gates: the 3 interrupt transitions surface as Human Input Cards
  (protocol already exists) + a project Review queue (greenagent
  `local-review/` bridge). Approve/reject calls `check-transition` with the
  actor's identity as authorization ref.
- "Start cycle" button → creates a project thread with
  `assistant_id=dbtl_orchestrator`.
- Phase 2 of the DBTL harness lands here: stage executors = role-scoped
  `make_lead_agent` with the project's personas/skills/tools (SOUL.md per
  role, e.g. designer persona from `.greenagent/agents/designer.yaml`
  responsibilities).

### Phase D — Team & collaboration (humans as coworkers, ~1-2 wks)
- Role assignment UI (Team tab) writing through `greenagent assign`;
  human-assigned roles route stage work to a review task (notification +
  review queue) instead of an agent run.
- Members: invite existing users to a project; project-scoped authorization
  (extend the existing owner_check to membership check — the authz layer's
  Layer 1/2 seams support this without new architecture).
- Notifications: reuse scheduled-tasks/channels plumbing for gate-pending and
  handoff events (IM channels already bridge to threads).

### Phase E — Breeding domain pack (configuration, ongoing)
- A "breeding program" project template: personas (designer=quant geneticist,
  builder=pipeline engineer, tester=field-trial analyst, learner=program
  strategist), GS/UAV skills, Slurm tool group, knowledge seeding from
  `doc/DATA_PROVENANCE.md`. Season calendar = scheduled tasks per cycle.
  All config — zero platform code.

## 6. Risks & open questions (for human review)

1. **Two dashboards problem:** greenagent ships its own `dashboard`. Do we
   embed/link it (fast, honest to the authority boundary) or re-render state
   in deer-flow (better UX, duplication risk)? *Designer's lean: re-render
   read-only in deer-flow; keep writes CLI/dashboard-only until Phase C gates.*
2. **Project ↔ repo coupling:** greenagent scope requires a `.greenagent/`
   repo. Breeding projects whose folder is a data dir (not a git repo) need
   either `greenagent init` there or a companion repo. Needs a decision.
3. **Multi-user + single `.greenagent`:** role YAMLs are per-repo, not
   per-user; concurrent human edits go through git, not deer-flow — acceptable?
4. **Upstream drift:** the sidebar/shell edits touch files upstream also
   touches. Keep them small and mechanical (one new nav section, one route
   subtree) to keep pulls cheap.
5. **Thread migration:** existing chats stay unassigned; no forced migration.

## 7. What this is NOT

- Not a rewrite: chat internals, run lifecycle, streaming, middleware chain,
  memory, channels untouched.
- Not a greenagent replacement: no DBTL state authority in deer-flow, ever.
- Not domain-locked: breeding is Phase E config; the platform stays general.
