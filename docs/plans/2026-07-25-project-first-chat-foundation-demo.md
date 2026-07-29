# Project-First Chat — Foundation Demo Design (Cycle Rescope)

- Cycle: `workspace-oriented-breeding-design`
- Date: 2026-07-25
- Author: Designer (claude, human-operated VS Code session)
- Human decision: `human-decision-2026-07-25-foundation-demo-rescope`
- Supersedes the delivery scope of `workspace-oriented-breeding-design-package.json`
  for this cycle only; the long-term architecture package remains approved
  reference material.

## 1. Human direction recorded

Resolving the Tester escalation
(`plan/test/2026-07-24-workspace-oriented-breeding-human-escalation.md`, removed with the retired `plan/` tree — see git history), the
human chose the second offered route and added new UI direction:

1. Route the stale acceptance contract back to **Designer**; redefine this
   delivery as a **narrower foundation demonstration**.
2. **Do not integrate DBTL yet.** DBTL orchestration, phase widgets, and
   reconciliation surfaces are out of scope this cycle.
3. The left-hand UI becomes **project-focused** (reference: Biomni-style rail —
   projects displayed as folders).
4. **No independent chat.** Chat exists only inside a project: clicking a
   project opens its workspace, which includes the conversations.
5. Keep the cycle **short**: deliver the minimum reviewable result; the human
   reviews it and provides comments that drive the next cycle.

## 2. Foundation demo scope — revision 1

> Superseded by §4 (revision 2) for the rail and chat placement. The
> association contract below still holds unchanged.

### Layout

```
┌──────────┬──────────────────────────────────────────────┐
│ Sidebar  │  Main area                                   │
│          │                                              │
│ GreenAg. │  /workspace/projects   → portfolio + create  │
│          │  /workspace/projects/x → project workspace:  │
│ PROJECTS │      name + breeding objective               │
│ 🗀 Drought│      Conversations (project-scoped list)     │
│ 🗀 GWAS   │      [ New conversation ]                    │
│ 🗀 …      │  /workspace/chats/…?project_id=x → chat      │
│          │      (existing chat surface, project-scoped) │
│ Agents   │                                              │
│ Sched.   │                                              │
└──────────┴──────────────────────────────────────────────┘
```

- **Sidebar** lists the active workspace's projects as folder entries
  (`FolderClosed` icon), replacing the previous `Projects / Inbox / Chats`
  nav items. The global "New chat" entry is removed. `Agents` and
  `Scheduled tasks` remain.
- **Project page** becomes the conversation surface: objective header,
  project-scoped conversation list (newest first), and a "New conversation"
  action. The DBTL phase strip, reconciliation banner, and capability grid
  from the earlier foundation build are removed from the page this cycle
  (per direction #2); the underlying persistence fields remain untouched.
- **Chat** reuses the existing `/workspace/chats/[thread_id]` surface. A chat
  opened from a project carries `?project_id=…`; on lazy thread creation the
  frontend patches the thread metadata with `project_id`, mirroring the
  existing `agent_name` pattern.

### Association contract (no backend change this cycle)

- Thread → project association is thread metadata `project_id`, written via the
  existing `PATCH /api/threads/{id}` (LangGraph SDK `threads.update`) after
  `onCreated`.
- A project's conversations are listed via the existing
  `POST /api/threads/search` exact-match metadata filter
  (`{"metadata": {"project_id": …}}`).
- The durable `threads_meta.project_id` column and scope migrations from
  B0–B3 remain in place; promoting the metadata association into those
  first-class columns is next-cycle work once the UI direction is confirmed.

### Explicitly deferred (pending human comments)

| Deferred item | Reason |
| --- | --- |
| DBTL orchestration, gate evaluation, reconciliation surfaces | Direction #2: no DBTL integration this cycle. Cycles/to-dos in the rail are a browser-local planning projection only. |
| Global Inbox nav | Superseded by revision 2: legacy projectless threads route to `/workspace/chats` under an "Unfiled chats" entry |
| Durable, database-backed cycles and to-dos | Revision 2 ships them as a localStorage projection for interaction review first |
| First-class `threads_meta.project_id` write path + authenticated/PostgreSQL evidence | Next cycle after direction confirmed |

## 3. Acceptance criteria (rescoped)

1. Sidebar shows the active workspace's projects as folder entries, one entry
   per project, with no independent New-chat/Inbox nav entries.
2. Opening a project shows its cycles, the selected cycle's to-dos, and its
   conversations in a rail; starting a conversation renders chat beside that
   rail without leaving the project.
3. A conversation started from a project appears in that project's list
   (thread metadata `project_id` round-trips through search).
4. No DBTL UI is presented; no backend schema change ships this cycle.
5. `pnpm check` and unit tests pass; new pure logic has unit tests.

## 4. Review round 1 — answers and revision 2

Human comments received 2026-07-25 (`human-review-2026-07-25-rail-revision`):

| Question | Answer | Applied as |
| --- | --- | --- |
| Duplicate project folders in the sidebar | "Two project folders that include three different projects — simplify, be concise" | The "All projects" folder entry is gone; the `Projects` group label itself links to the portfolio, so the group holds exactly one entry per project |
| Secondary rail | Adopt the Biomni-style 2nd rail: **Tasks → Cycles**, **Drive → per-cycle To-Do list**; be creative | `ProjectRail` renders Cycles (each with an inline four-dot DBTL phase control), the selected cycle's To-Do list, and the project's conversations |
| Third rail | Chat renders there | Chat is nested at `/workspace/projects/[project_id]/chats/[thread_id]`; a project layout keeps the rail mounted while the chat surface fills the main area |
| Legacy projectless threads | Route to `/workspace/chats` | Kept at that route under an "Unfiled chats" nav entry, outside the Projects group |
| DBTL surfaces | (see above) | Still deferred; the rail's cycles/phases are a local planning aid, not orchestration |

### Revision 2 layout

```
┌────────────┬──────────────┬────────────────────────────┐
│ Sidebar    │ Project rail │  Chat (3rd rail)           │
│            │              │                            │
│ PROJECTS ▸ │ CYCLES     + │  ← existing chat surface,  │
│ 🗀 Drought  │ ↻ Cycle 01 ●●○○│    nested under the       │
│ 🗀 GWAS     │ ↻ Cycle 02 ●○○○│    project route          │
│            │              │                            │
│ Unfiled    │ TO-DO·Cyc 01 │                            │
│ Agents     │ ✓ Score trial│                            │
│ Scheduled  │ ○ Pick parents│                           │
│            │ + Add a to-do │                           │
│            │              │                            │
│            │ CONVERSATIONS+│                           │
│            │ ▤ Trial plan  │                           │
└────────────┴──────────────┴────────────────────────────┘
```

The phase dots on each cycle are clickable: they set that cycle's DBTL phase
(design/build/test/learn), with the active phase pulsing. The plan persists per
project in `localStorage` under `deer-flow:project-cycle-plan:<projectId>`.

## 5. Review round 2 — answers and revision 3

Human comments received 2026-07-25 (`human-review-2026-07-25-flat-routes`):

| Comment | Applied as |
| --- | --- |
| Remove `/workspace/projects` and the portfolio icon on the sidebar group label | The route and its page are deleted; the `PROJECTS` label is plain text with a single `+` action that opens the create-project dialog inline |
| Project URL should carry the project name directly after `/workspace/` | Projects live at `/workspace/<slug>` and conversations at `/workspace/<slug>/<thread_id>`; the slug comes from the project name and is resolved to the project through the active workspace's project list |
| Opening a project must land in chat, not a brief page | `/workspace/<slug>` server-redirects to `/workspace/<slug>/new`, so clicking a project shows the chat welcome with the rail beside it; the brief page is deleted |

### Revision 3 routes

| Route | Surface |
| --- | --- |
| `/workspace` | No surface of its own — forwards into the first project, or runs the first-run workspace/project create flow |
| `/workspace/<slug>` | Redirect into a new conversation |
| `/workspace/<slug>/new` | Project workspace: rail + chat welcome |
| `/workspace/<slug>/<thread_id>` | Project workspace: rail + conversation |
| `/workspace/chats`, `/workspace/chats/<thread_id>` | Legacy projectless conversations ("Unfiled chats") |

Because Next.js resolves static segments before `[project_slug]`, project
slugs are guarded at creation time against shadowing a real workspace route
(`agents`, `chats`, `inbox`, `projects`, `scheduled-tasks`, `settings`) —
`projectSlugOfName` suffixes `-project` in that case.

Known limitation carried into the next round: the slug is resolved client-side
from the active workspace's project list, so a project in a non-active
workspace, or a slug renamed elsewhere, will not resolve. A backend
`GET /api/projects/by-slug` (or workspace-scoped slug uniqueness) is the
durable fix.

## 6. Review round 3 — answers and revision 4

Human comments received 2026-07-25 (`human-review-2026-07-25-rail-sections`):

| Comment | Applied as |
| --- | --- |
| Clicking the project name should display its subfolders; the Files folder can live there and be removed from the top tab | The rail's project header is a disclosure button that expands a lazy folder tree (`ProjectFiles`). The chat header's `FilesTrigger` is hidden inside a project — it stays for unfiled chats, which have no rail |
| Show the agents working on this project (placeholder for now) | An `Agents` section lists the lead agent with a live dot, plus a note that per-project breeding agents are assigned in a later cycle |
| Minimize the Cycles section when no active cycle is running | `DbtlCycle` gained a `status` (`active`/`done`) with a per-cycle complete/reopen control. The section auto-collapses to `Cycles · N` once nothing is running, and an explicit click on the section header always overrides that default |

### File-scope limitation (carried to the next round)

The backend scopes the file tree to a **thread**
(`GET /api/threads/{id}/files`), not to a project, so the rail lists the active
conversation's workspace and shows a prompt when no conversation is open.
A project-level file root (or promoting a project's linked folder into the
rail) is the durable fix and needs a backend decision.

### Rail sections, revision 4

```
▸ 🌱 Drought resistent      ← click to expand the folder tree
▸ CYCLES · 2                ← minimized: nothing running
  TO-DO · Cycle 02
  AGENTS
    🤖 Lead agent        ●
  CONVERSATIONS         +
```

## 7. Review round 4 — the backend becomes project-owned

Human direction received 2026-07-25 (`human-review-2026-07-25-project-owned-backend`):

> "Fix the backend: scope files to a project not a thread — or make that thread
> associated with the project. The whole refactor of this project is around a
> project, not chat. This will make later changes much more clear."

This round does **both**, because either alone is hollow: a project file API
with nothing writing into the project directory would list an empty tree, and a
thread association that does not move the storage leaves files stranded in
whichever chat produced them. The rule is now explicit:

> **A project owns its data. A conversation is a view onto it.**

### What changed (first backend change of this cycle)

| Layer | Change |
| --- | --- |
| Storage | `Paths.project_dir / project_work_dir / project_uploads_dir / project_outputs_dir / ensure_project_dirs / resolve_project_virtual_path` put a project's tree at `users/{user}/projects/{project}/user-data/…` |
| Sandbox | `SandboxProvider.acquire(..., project_id=…)` is an optional keyword; `LocalSandboxProvider` maps the agent-visible `/mnt/user-data/workspace` and `/outputs` to the project, so every conversation in a project shares one workspace. Providers without project storage ignore it |
| Scope record | `threads_meta.project_id / workspace_id / scope_type` is written and read: `create(..., project_id=…)`, `set_conversation_scope()`, `list_by_project()`, owner-scoped, in both the SQL and memory stores |
| Run context | `apply_project_scope_context()` stamps `context["project_id"]` from that row on every run, after dropping any caller-supplied value from `context` and `configurable` |
| API | `GET /api/projects/{id}/threads`, `PUT|DELETE /api/projects/{id}/threads/{thread}`, `GET /api/projects/{id}/files` — all membership-gated through `WorkspaceRepository.get_project` |
| Frontend | The rail's file tree and conversation list call the project endpoints (no conversation needed); creating a conversation files it into the project through the PUT. The thread-metadata association is deleted |

### Deliberate boundaries

- **Uploads stay conversation-scoped.** They belong to the message that carried
  them, and the Gateway upload path writes them into the thread's directory —
  moving only the sandbox mapping would have hidden uploads from the agent.
- **`project_id` is authorization-sensitive.** It selects which project's
  workspace the sandbox mounts, so it is server-derived only; a client value is
  stripped from both `context` and `configurable`. Pinned by tests.
- **Only the local sandbox implements project storage.** AIO/E2B/BoxLite accept
  and ignore the keyword, falling back to conversation-scoped directories.
- **Existing conversations are untouched.** Unfiled chats keep their own
  workspace; filing one into a project rebuilds its sandbox mappings rather
  than reusing the pre-filing ones.

### Tests (TDD, written before each implementation)

`test_project_paths.py` (16), `test_project_scoped_sandbox.py` (9),
`test_thread_conversation_scope.py` (14, both store backends),
`test_project_workspace_router.py` (6), `test_run_project_scope_context.py` (7).

## 8. Review round 5 — uploads move, first-run race fixed

Human answers received 2026-07-25 (`human-review-2026-07-25-uploads-and-race`):
question 1 → "moving both"; question 2 → explain the sandbox providers,
implement later; plus a field report: the G2F conversation hit sandbox write
denials and the project folder tree stayed empty.

### Diagnosis of the G2F symptom (not a UI bug)

1. **First-run race (mine):** the thread is created by the same request that
   starts its first run, so the frontend's filing `PUT` landed after
   `apply_project_scope_context` had already run — the first run mounted
   conversation-scoped storage and the project tree was never written.
2. The agent also tried host paths (`/Users/...`), which the sandbox rightly
   denied — that was the "not allowed in certain dir" message.

### Fixes

- **Race:** the run request now carries `context.project_id` as intent;
  `file_thread_into_requested_project` validates project membership and files
  the conversation owner-scoped *before* the scope is stamped, so the first
  run already mounts the project workspace.
- **Uploads moved (answer #1):** a filed conversation's entire
  `/mnt/user-data` tree — workspace, uploads, outputs — is the project's.
  Sandbox mapping, Gateway upload read/write/delete, `ThreadDataMiddleware`,
  `UploadsMiddleware`, workspace-change snapshots, and the files/artifacts
  virtual-path resolution all move together.
- The rail's empty tree now explains itself ("No files yet — work done in
  this project's conversations lands here.").

The existing G2F conversation is already correctly filed; its next message
runs with the project workspace mounted, and files will appear in the rail.

### Deferred per answer #2

Project-owned storage for the container/VM sandbox providers (AIO Docker,
E2B, BoxLite): mount the project directory into the container the way the
local provider maps it. Needed before project workspaces are used with
Docker isolation; irrelevant for local-sandbox deployments.

## 9. Review round 6 — projects become human-visible folders

Human direction received 2026-07-25 (`human-review-2026-07-25-human-folders`):

> "The project G2F should be a folder the user added in the local system
> (human can access), not the sandbox the AI interacts with — e.g.
> ~/Documents/projects/G2F."

Applied: **the project's workspace IS a real folder the human owns.**

- `config.yaml -> projects.root` names the parent (set to
  `~/Documents/projects` for this deployment; example default
  `~/DeerFlowProjects`). Creating "G2F" creates — or **adopts** — 
  `~/Documents/projects/G2F`.
- The folder itself is the agent's workspace: `/mnt/user-data` and
  `/mnt/user-data/workspace` both alias the root, so `src/`, `data/` land
  directly in the human folder; `uploads/` and `outputs/` are plain visible
  subfolders.
- `projects.root_path` (migration 0010) records the folder; rows created
  before it backfill lazily on first read. The old internal
  `.deer-flow/.../projects` bucket is deleted from the code.
- Folder names stay human-readable (`G2F`, `Drought Resistance 2032`) but are
  sanitized against path separators/traversal; a folder claimed by another
  project row gets a numeric suffix.
- The run context now carries the server-derived `project_root` next to
  `project_id`; both are stripped from client input.
- The rail shows the folder location under the project name.

## 10. Review round 7 — the agent learns where it lives; sidebar shows folders

Field report (2026-07-25, `human-review-2026-07-25-agent-awareness`): in a new
G2F chat the agent answered "I can't see your local machine's filesystem" and
offered copy-paste terminal commands; clicking G2F in the sidebar started a
new chat instead of showing subfolders.

Diagnosis: the plumbing worked (thread filed, folder created, mount ready) —
the agent never called a tool. Nothing told the model that its workspace IS
the human's folder, so it assumed it had no filesystem access.

Applied:

- **`ProjectContextMiddleware`** (lead chain, before system-message
  coalescing): scoped runs get a per-request `<project_context>` system block
  stating that `/mnt/user-data/workspace` and the human folder (e.g.
  `~/Documents/projects/G2F`) are the same directory, and instructing the
  model to operate with its own tools instead of handing back terminal
  commands. Request-only — never checkpointed, follows filing changes.
- **Sidebar folder trees**: each project row gained a chevron that expands
  the project's folder tree inline in the sidebar; clicking the name still
  opens the project workspace. The rail's folder disclosure now defaults to
  open.

## 11. Review round 8 — shared mounts become agent knowledge

Field report (`human-review-2026-07-25-mount-awareness`): the agent now knows
G2F, but told the human it "can't directly write to
~/Documents/projects/greenagent-test" — a sibling folder that is in fact
mounted read-write at `/mnt/projects`. Same class of gap as round 7, one
level up: the model had no host↔container map for operator mounts.

Applied: `ProjectContextMiddleware` now also injects a `<local_folders>`
block into **every** run (project-scoped or not), listing each
`sandbox.mounts` entry as "host path X is available to you at container path
Y (read-write/read-only)" with the instruction to translate and operate
directly. Mounts are re-read from live config each request, so config edits
apply on the next message.

## 12. Open review questions for the next round

1. Should "New project" also offer picking an existing folder outside
   `projects.root` (explicit allow-list of parents)?
2. When should AIO (Docker) get project-folder mounts?
3. Durable cycles/to-dos, and agent-writable to-dos?
4. Conversation-per-cycle vs conversation-per-project?
5. Migrate-into-project action for "Unfiled chats"?
6. Server-side slug resolution + per-workspace uniqueness?
7. What belongs on an agent row once assignment is real?
