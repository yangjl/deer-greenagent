# AGENTS.md

This file provides guidance to AI coding agents (Claude Code, Codex, and others) when working with the DeerFlow frontend. It is the source of truth; the sibling `CLAUDE.md` imports it via `@AGENTS.md`.

## Project Overview

DeerFlow Frontend is a Next.js 16 web interface for an AI agent system. It communicates with a LangGraph-based backend to provide thread-based AI conversations with streaming responses, artifacts, and a skills/tools system.

**Stack**: Next.js 16, React 19, TypeScript 5.8, Tailwind CSS 4, pnpm 10.26.2. Requires Node.js 22+ and pnpm 10.26.2+.

### Core dependencies

- **LangGraph SDK** (`@langchain/langgraph-sdk` ^1.5.3) — Agent orchestration and streaming
- **LangChain Core** (`@langchain/core` ^1.1.15) — Fundamental AI building blocks
- **TanStack Query** (`@tanstack/react-query` ^5.90.17) — Server state management
- **UI**: Shadcn UI, MagicUI, React Bits, and Vercel AI SDK elements (generated from registries — see Code Style)

## Commands

| Command          | Purpose                                           |
| ---------------- | ------------------------------------------------- |
| `pnpm dev`       | Dev server with Turbopack (http://localhost:3000) |
| `pnpm build`     | Production build                                  |
| `pnpm check`     | Lint + type check (run before committing)         |
| `pnpm lint`      | ESLint only                                       |
| `pnpm lint:fix`  | ESLint with auto-fix                              |
| `pnpm format`    | Prettier check (`pnpm format:write` to apply)     |
| `pnpm test`      | Run unit tests with Rstest                        |
| `pnpm test:e2e`  | Run E2E tests with Playwright (Chromium)          |
| `pnpm typecheck` | TypeScript type check (`tsc --noEmit`)            |
| `pnpm start`     | Start production server                           |

Unit tests live under `tests/unit/` and mirror the `src/` layout (e.g., `tests/unit/core/api/stream-mode.test.ts` tests `src/core/api/stream-mode.ts`). Powered by Rstest; import source modules via the `@/` path alias.

E2E tests live under `tests/e2e/` and use Playwright with Chromium. They mock all backend APIs via `page.route()` network interception and test real page interactions (navigation, chat input, streaming responses). Config: `playwright.config.ts`.

## Architecture

```
Frontend (Next.js) ──▶ LangGraph SDK ──▶ LangGraph Backend (lead_agent)
                                              ├── Sub-Agents
                                              └── Tools & Skills
```

The frontend is a stateful chat application. Users create **threads** (conversations), send messages, set thread-scoped `/goal` completion conditions, and receive streamed AI responses. The backend orchestrates agents that can produce **artifacts** (files/code), **todos**, and goal state updates.

### Source Layout (`src/`)

- **`app/`** — Next.js App Router. Routes include `/` (landing), `/workspace/chats/[thread_id]` (chat), `/workspace/agents/[agent_name]` and `/workspace/agents/new` (custom agents), `/blog/…`, the `(auth)/{login,setup,auth/callback}` flow, `/[lang]/docs/…`, and `/api/…` route handlers (e.g. `/api/memory`).
- **`components/`** — React components:
  - `ui/` — Shadcn UI primitives (auto-generated, ESLint-ignored)
  - `ai-elements/` — Vercel AI SDK elements (auto-generated, ESLint-ignored)
  - `workspace/` — Chat page components (messages, artifacts, settings)
  - `landing/` — Landing page sections
  - `docs/` — Docs / MDX rendering components
- **`core/`** — Business logic, the heart of the app. Domains include `threads/` (creation, streaming, state), `api/` (LangGraph client singleton), `agents/` (custom agents), `auth/` (authentication), `artifacts/`, `channels/` (IM connections), `i18n/` (en-US, zh-CN), `settings/`, `memory/`, `skills/`, `messages/`, `mcp/`, `models/`, `input-polish/` (pre-send draft rewrite API), `voice-input/` (browser speech-recognition helpers), `files/` (workspace file-tree listing via `GET /api/threads/{id}/files` plus the per-thread project link APIs under `/api/threads/{id}/project`), `suggestions/`, `tasks/`, `todos/`, `tools/`, `workspace-changes/` (run-scoped changed-file summaries and diff fetching), `config/`, `notification/`, `blog/`, plus rendering helpers (`rehype/`, `streamdown/`) and `utils/`.
- **`hooks/`** — Shared React hooks
- **`lib/`** — Utilities (`cn()` from clsx + tailwind-merge)
- **`content/`** — MDX content (blog posts, docs) rendered by the app
- **`styles/`** — Global CSS with Tailwind v4 `@import` syntax and CSS variables for theming
- **`typings/`** — Ambient TypeScript declarations
- Root files: `env.js` (env validation), `mdx-components.ts` (MDX component map)

### Data Flow

1. Optional composer helpers such as `core/input-polish` can rewrite the local draft before submission, and `core/voice-input` can transcribe browser microphone input into that same local draft; confirmed user input then flows to thread hooks (`core/threads/hooks.ts`) → LangGraph SDK streaming
2. Stream events update thread state (messages, artifacts, todos, goal)
   File-tool artifact auto-open work must run in an effect with timer cleanup; never schedule timers while rendering streamed `write_file` or `str_replace` updates.
3. `useThreadHistory` loads persisted conversation pages from `GET /api/threads/{id}/messages/page`, preserving the backend's thread-global event `seq`; rendering overlays checkpoint/live copies at their matching canonical identities (a summarized checkpoint may contain a protected early input plus a recent tail), suppresses checkpoint/transient prefixes whose canonical position is still behind an unloaded cursor page instead of collapsing that unknown gap before a recent anchor, then adds optimistic messages without timestamp re-sorting. History invalidation preserves already-loaded pages so their established ordering positions are not discarded.
4. Stop actions call the LangGraph SDK stream stop path; `core/threads/hooks.ts` invalidates current-thread, thread-history, token-usage, and sidebar/search caches immediately and schedules one follow-up refetch because SDK stop may finish via abort + fire-and-forget cancel before backend title finalization commits
5. TanStack Query manages server state; localStorage stores user settings
6. Components subscribe to thread state and render updates

Run duration is run-scoped UI metadata even though the compatibility field `additional_kwargs.turn_duration` is repeated on historical AI messages. `core/messages/run-duration.ts` folds those copies into one display anchored after the run's last visible message group. `MessageList` owns the temporary client-side duration for a just-completed live turn until authoritative history arrives. The duration is total run wall-clock time, not per-message reasoning time; reasoning disclosure and run activity/duration are rendered separately.

Composer drafts are tab-scoped browser state. `core/threads/composer-draft.ts` stores only text plus the selected slash-skill name in `sessionStorage`, keyed by user, agent, and logical conversation scope. New-chat pages pass the stable scope `"new"` because their runtime `threadId` is a fresh UUID on every reload; established conversations use their real thread ID. `InputBox` waits for enabled skills before restoring a skill chip, degrades a missing/disabled skill back to editable slash text, and clears the stored draft through `SendMessageOptions.onSent` only after the send passes the in-flight guard. Attachments, sidecar quotes, voice state, and polish undo state are not persisted.

Auth UI note: the login page's "keep me signed in" option submits only `remember_me` to the Gateway and may persist only the email address through `core/auth/remember-login.ts`. Passwords and tokens must never be stored in frontend storage; the `HttpOnly access_token` and readable `csrf_token` cookies remain Gateway-owned.

`/goal` and `/compact` are built-in composer commands, not skill activations. `src/components/workspace/input-box.tsx` intercepts `/goal`, `/goal clear`, and `/goal <condition>` before normal chat submission, calling Gateway `GET/PUT/DELETE /api/threads/{thread_id}/goal`. Setting `/goal <condition>` also submits the condition text as the next user task so the agent starts running immediately; status and clear do not start a run. Goal and compact requests are tied to the current `threadId` with an `AbortController`, so switching threads or unmounting the composer aborts in-flight requests and stale responses cannot update the new thread's composer state. The chat pages render `GoalStatus` above the composer from `AgentThreadState.goal`, with local optimistic state until the next stream `values` update arrives. `/compact` calls `POST /api/threads/{thread_id}/compact` to summarize older active context while leaving the full visible chat history intact; it is skipped on new/empty threads and blocked server-side while a run is in flight.

Human input requests are a structured message protocol layered on normal chat history. The backend writes request payloads to `ToolMessage.artifact.human_input`, `src/core/messages/human-input.ts` owns the runtime validators/types, and `src/components/workspace/messages/human-input-card.tsx` renders the reusable card. `MessageList` owns answered/latest/pending state for visible cards, but derives answered responses from raw `thread.messages` because replies are hidden; pending cards clear when the hidden reply appears, when dispatch is dropped, or when a new `thread.error` reports an async stream failure. Page-level submit callbacks must send a normal human message and put `hide_from_ui: true` plus the response payload in the fourth `sendMessage(..., options)` argument as `options.additionalKwargs`; the third argument remains run context such as `{ agent_name }`. Composer entry points should disable normal bottom input while `hasOpenHumanInputRequest(...)` is true so users answer through the card and preserve response metadata.

Tool-calling AI messages can contain user-visible text as well as `tool_calls`. `core/messages/utils.ts` keeps these turns in an `assistant:processing` group, and `components/workspace/messages/message-group.tsx` must render the visible text as a processing step instead of treating the message as only tool metadata. This preserves provider text such as error explanations or "trying another approach" notes during tool-heavy runs.

### Key Patterns

- **Server Components by default**, `"use client"` only for interactive components
- **Thread hooks** (`useThreadStream`, `useSubmitThread`, `useThreads`) are the primary API interface
- **Thread routes** — construct Web UI chat paths through `core/threads/utils.ts::pathOfThread()`, which percent-encodes both custom agent names and thread IDs before inserting them into route segments
- **LangGraph client** is a singleton obtained via `getAPIClient()` in `core/api/`
- **Run stream options** are sanitized by `core/api/stream-mode.ts`: the Gateway-supported set is `values`, `messages-tuple`, `updates`, `debug`, `tasks`, `checkpoints`, and `custom`; any request containing an unsupported mode throws before HTTP instead of being partially forwarded or silently defaulting to `values`. `streamResumable` is retained by thread hooks only for SDK-side reconnect bookkeeping but stripped before the HTTP request because the Gateway does not implement resumable SSE. Keep this boundary aligned with the backend request schema; `messages` and `events` are not supported and must not be forwarded.
- **Streaming Markdown rendering** is owned by `core/streamdown`: Streamdown's `animated` / `isAnimating` API handles incremental word animation, while the shared `streamdownRenderingPlugins` config registers the named code-highlighting and Mermaid plugins required by Streamdown 2.5. Keep wrappers and derived configs wired to that shared object; do not reintroduce a rehype plugin that wraps every word, because reparsing a growing block remounts old words and replays their animation.
- **Environment validation** uses `@t3-oss/env-nextjs` with Zod schemas (`src/env.js`). Skip with `SKIP_ENV_VALIDATION=1`
- **Subtask step history and runtime metadata** (`core/tasks/`) — the subtask card shows a subagent's full step timeline (#3779): its assistant reasoning turns interleaved with the tools it ran. `Subtask.steps[]` is accumulated live from `task_running` events (appended via `mergeSteps`, not overwritten) and backfilled on expand for historical runs by `fetchSubtaskSteps`, which pages the events endpoint scoped to one task (GET `/runs/{runId}/events?event_types=subagent.step&task_id=…&after_seq=…`) until a short page, so the run-wide limit can't truncate the timeline. `task_started` carries the effective `model_name`; `task_running` carries a cumulative usage snapshot after each completed LLM call. `core/tasks/lifecycle.ts` normalizes these additive events, and `computeNextSubtask` keeps the largest cumulative total so replayed or late SSE frames cannot double-count or roll the folded card backward. Terminal ToolMessage metadata (`subagent_model_name` / `subagent_token_usage`) restores the same values from normal history after reload; no per-card event fetch is needed. `core/tasks/steps.ts` is the pure step model: `messageToStep` (live), `eventsToSteps` (reload), `mergeSteps` (dedup by `message_index`), and `stepsForDisplay` (what the card renders — keeps tool steps + AI steps with text, drops the trailing final-answer AI step when completed since it's shown as `result`). `core/tasks/context.tsx`'s `useUpdateSubtask` applies updates against a `tasksRef` mirroring the latest state (not a closure snapshot), so a late-resolving `fetchSubtaskSteps` backfill merges into current state instead of clobbering SSE steps or sibling subtasks that arrived meanwhile. The owning `run_id` is carried onto history content messages in `buildVisibleHistoryMessages` so the card can resolve the events endpoint.

### Interaction Ownership

- The workspace UI is project-first with three rails (2026-07-25
  foundation-demo rescope, revision 3): the sidebar lists one folder entry per
  project of the active workspace, the project rail carries
  cycles/to-dos/conversations, and chat renders in the main area. The sidebar
  `PROJECTS` group label carries only a `+` create action — there is no
  portfolio route; `/workspace/projects` was removed. Legacy projectless
  conversations stay at `/workspace/chats` under "Unfiled chats".
- Project routes are flat and name-bearing: `/workspace/<project-slug>`
  redirects into `/workspace/<project-slug>/new`, and conversations live at
  `/workspace/<project-slug>/<thread_id>`. Next.js resolves static segments
  before `[project_slug]`, so `projectSlugOfName` must keep guarding new slugs
  against `RESERVED_WORKSPACE_SEGMENTS`; add any new static `/workspace/*`
  route to that list. Slug→project resolution is currently client-side via
  `useProjectBySlug` against the active workspace's project list — a backend
  by-slug lookup is the durable fix.
- `/workspace` has no surface of its own: `workspace-landing.tsx` forwards into
  the first project, or runs the first-run workspace/project create flow.
- `components/workspace/projects/create-dialogs.tsx` owns the project identity
  plus four location choices: default root, existing folder, absolute full
  path, or a new folder under a selected parent.
  `local-folder-picker-dialog.tsx` browses only the Gateway's writable
  allowlisted roots through `GET /api/project-folders`.
  `core/workspaces/project-location.ts` is the React-free payload/path helper
  and must mirror the backend's human-readable folder-name sanitization.
- `useThreadStream` adds `project_id` to the submitted run context when the
  conversation lives under a project route. This is what files a brand-new
  conversation into its project atomically with the first run (the server
  validates membership); the post-creation `PUT` remains as a durable
  belt-and-suspenders. Do not remove either half.
- `src/core/workspaces/project-files-api.ts` owns the project-scoped calls: a
  project's file tree (`GET /api/projects/{id}/files`) and its conversations
  (`GET /api/projects/{id}/threads`, `PUT|DELETE .../threads/{thread}`). Both
  are addressed by project id and need no conversation. Thread creation files
  the new conversation into its project through that PUT — do not reintroduce
  the thread-metadata association it replaced.
- `src/core/workspaces/` owns REST types, API calls, and TanStack Query hooks
  for `/api/workspaces`, `/api/projects`, and `/api/project-folders`, plus
  **pure, React-free**
  modules: `project-threads.ts` (route builders, slug rules, and the
  conversation query key) and `project-location.ts` (creation-location payload
  and path preview helpers). Server components and
  `core/threads/hooks.ts` import these modules directly, never the
  `@/core/workspaces` barrel, which pulls React hooks into server bundles.
  The former browser-local `cycle-planning.ts` projection was **deleted** in
  DBTL Phase 3; cycles are durable server records owned by `src/core/dbtl/`.
  Do not reintroduce a `localStorage` cycle model — the Phase 3 no-go is that
  browser-local state must not be able to override durable cycle state, and
  deleting the module is what makes that structural rather than a convention.
- `src/app/workspace/[project_slug]/layout.tsx` mounts `ChatProviders` and the
  project rail beside its children, so the rail survives navigation between
  conversations.
- Sidebar project rows are two-target: the chevron expands the project's
  folder tree inline (`ProjectFiles`), and the name opens the project
  workspace. This is the tree's single UI home; the project rail must not
  duplicate it. File rows open `ProjectFilePreview`, a project-id-scoped sheet
  that works without a conversation, temporarily collapses the first sidebar
  rail, and restores its prior state on close.
- `src/components/workspace/project-rail/` owns the rail: a disclosure on the
  durable DBTL cycles (each expanding into its five stages with a status word),
  the selected cycle's open blockers, an agents placeholder, and the project's
  conversations. `start-cycle-dialog.tsx` opens a durable research record
  (title, cycle class, optional season/program parent, research question,
  objective, success criteria) and mints one idempotency key per opening so a
  double-submit cannot create two records. Selecting a stage opens
  `cycle-stage-sheet.tsx` — evidence, open blockers, the submit/review panel
  with a required rationale, and the activity timeline with actor and revision
  — using the existing right-side inspection pattern. The Cycles section
  auto-minimizes when no cycle is live; an explicit click on the section header
  overrides that default. Project tree listings use
  `GET /api/projects/{id}/files`, while preview/download uses
  `GET /api/projects/{id}/file`; both work without a conversation. The chat
  header's `FilesTrigger` remains projectless-chat-only.
- `src/core/dbtl/` owns the DBTL feature/readiness contracts, query hooks,
  stable inventory grouping, and JSON export helper. Settings → DBTL readiness
  includes the read-only Phase 0 inventory and the administrator-only Phase 1
  durable-governance checklist. Phase 1 may run a validation, download its
  evidence, show projection mismatches and rollback posture, and approve a
  cutover only after every PostgreSQL check passes. It must not repair data,
  promote knowledge, or start a graph. The project
  rail's “View readiness” action opens that settings section.
  Phase 3 adds the durable cycle layer in the same module: `cycle-view.ts` is
  **pure and React-free** (stage/status vocabulary, why a stage is locked,
  whether a control is available, activity phrasing), `cycles-api.ts` owns the
  `/api/projects/{id}/dbtl/*` calls, and `cycle-hooks.ts` the TanStack hooks.
  Controls are derived from the server's stage status alone — the client never
  decides that a gate is open, it only renders what the durable record says.
  Cycle creation records both class and workflow weight; once one live
  top-level cycle exists, the dialog permits only a computational child under
  a live season/program parent. The stage sheet displays only evidence bound
  to the selected stage and includes the manual URI + SHA-256 attachment form
  required to drive Phase 3 without a graph. Artifact, blocker, and resolution
  calls carry the displayed durable revision plus a fresh idempotency key.
  Action buttons use native `disabled` state while incomplete or pending so a
  keyboard activation cannot bypass the same duplicate-submit protection as a
  pointer click.
  Phase 4 adds the Upgrade Proposal to the same module: `proposal-view.ts` is
  **pure and React-free** (the three actions and each one's stated
  consequence, clarification prompts and completion, the confirmation lines,
  and the two exit-review rates), `proposals-api.ts` owns
  `/api/projects/{id}/dbtl/proposals/*`, and `proposal-hooks.ts` the TanStack
  hooks. `hasProposalToShow` keys on the server's payload rather than the
  route kind, so a client-side heuristic has no way to manufacture a card the
  server withheld — the client has no classifier and must not appear to.
  `PROPOSAL_ACTIONS` is a fixed list, not a derived one, so adding a
  confirmation-skipping shortcut has to be a conscious edit. Reviewed wording
  (the notice, the required gates, the record effect) is rendered from the
  server payload rather than re-typed here. False-upgrade and missed-cycle
  rates use **different denominators** on purpose: one measures what was
  proposed, the other only classifier-sourced ordinary decisions; explicit
  setup routes are not classifier misses. A shared denominator would hide the
  trade-off between them.
- `src/components/workspace/dbtl/` owns the Phase 4 surfaces.
  `upgrade-proposal-card.tsx` is the inline card above the composer: it walks
  offer → clarify → confirm, keeps "No cycle has been created yet." visible
  through the first two steps, and only the final confirm calls the durable
  create endpoint. `use-upgrade-proposal.ts` runs the shadow evaluation
  *beside* the send, never in front of it, and every failure path resolves to
  "no card" so a classifier outage degrades to ordinary chat instead of
  blocking a message. `evaluation-drawer.tsx` is the internal tester view and
  is opt-in (`enabled`), because the endpoint behind it is administrator-only
  and an ordinary member opening settings should not generate a 403. It is
  mounted from Settings → DBTL readiness, provides a project selector, and
  lets the tester label an undecided classifier-ordinary row as correctly
  ordinary or cycle-worthy for missed-cycle calibration.
- `src/core/dbtl/context-chip.ts` is the Phase 5 request-context chip's pure
  logic, and it exists so the two promises the chip makes are unit-testable
  without rendering anything. `nextContextAfterSend` returns ordinary
  **unconditionally** — that is the whole mechanism behind "affects the next
  request only"; it is neither the user's job to switch back nor a cleanup step
  a component can forget. `normalizeContext` resolves a stored selection
  against the live cycle list on every render, because a cycle can be completed
  or abandoned in another tab between the click and the send and the chip must
  not keep claiming a scope that is gone. Terminal cycles are omitted from the
  menu (a completed cycle cannot be continued, so offering it would produce a
  refusal instead of an action), but numbering still comes from the **full**
  list so the chip and the project rail agree about which cycle is "Cycle 02".
  `runContextPayload` emits the one-run `dbtl_supervisor_enabled` flag plus the
  backend's routing keys (`dbtl_explicit_choice`,
  `dbtl_selected_cycle_id`). "Ask the AI to recommend" keeps the supervisor
  flag but omits an explicit choice — precisely the one case where the backend
  precedence ladder consults the classifier. A "cycle" selection with no id
  degrades to ordinary rather than claiming a continuation it cannot name.
- `src/components/workspace/dbtl/context-chip.tsx` renders it above the
  composer, gated on `useDbtlFeature().graph_execution_enabled` **and** a
  project, so the control never appears where changing it would do nothing.
  The payload travels through `sendMessage`'s `extraContext` into the run
  request's `context`, never `config.configurable`, which is checkpointed.
  Interactive threads stay pinned to `lead_agent`; the Gateway selects the
  supervisor for this run only after validating the durable project scope, so
  disabling DBTL cannot strand an existing conversation. The chip stays
  visually quiet for ordinary work — the overwhelmingly common case
  — and gains weight only when a cycle is selected, which is the state worth
  noticing because it changes what the next request means. The scope note lives
  inside the menu rather than beside the chip: it answers a question the user
  only has while choosing, and repeating it above every composer would be noise.
- `src/components/workspace/dbtl/cycle-selection-context.tsx` carries only an
  **explicitly clicked** cycle from the project rail to project chat. The rail
  may display a default cycle's details, but that default does not silently
  route every request as a continuation. The provider is keyed by project slug
  so a selection cannot leak across projects.
- `src/core/memory-scope/` owns the per-project memory scope migration
  (DBTL Phase 2). `review.ts` is **pure and React-free** — count rows, the
  four per-fact decisions, queue advance, provenance labels, and each
  decision's stated consequence — so the privacy rules are unit-tested
  directly rather than inferred from a rendered tree. `hooks.ts` deliberately
  does not fetch the review queue until the user opens it: the landing view is
  counts-only, and fact bodies must not be pulled into the client before then.
  `DECISION_ORDER` is a fixed list, not a derived one, so adding a bulk
  “share all” would have to be a conscious edit. Settings → Memory scope
  (`memory-scope-settings-page.tsx`) renders the counts, the owner's own
  review drawer, manifest download, and rollback for one selected project.
  Decisions submit the exact agent bucket and SHA-256 shown on the card; queue
  identity uses bucket + agent + fact + checksum rather than `fact_id` alone.
  The backend returns only the caller's pending counts and manifest slice.
  Native `disabled` controls prevent duplicate decisions, and rollback is a
  two-click confirmation that affects only the caller's shared copies.
- `src/app/workspace/[project_slug]/[thread_id]/page.tsx` re-exports the
  canonical chat page. `src/app/workspace/chats/[thread_id]/page.tsx` derives
  its project scope from `useParams().project_slug` (falling back to
  `?project_id=`) and routes replaceState/redirect/branch targets through the
  matching base path, so a project conversation never navigates out of its
  project.
- `src/app/workspace/chats/[thread_id]/page.tsx` owns composer busy-state wiring.
- `src/app/workspace/chats/[thread_id]/page.tsx` owns branch-from-turn submission and navigation; sidecar `MessageList` instances do not receive the branch action.
- `src/app/workspace/chats/[thread_id]/page.tsx` gates the Workspace Browser trigger and browser right panel on `/api/features -> browser_control.enabled`; default/failed feature discovery hides the browser control so optional backend installs do not show a dead Live socket.
- `src/app/workspace/chats/[thread_id]/page.tsx` and `src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx` own active-goal display state for their composer overlays.
- `src/components/workspace/messages/message-list.tsx` owns human-input card answered/latest/pending gating; entry pages only translate a submitted card response into `sendMessage` calls.
- `src/components/workspace/browser-view/browser-view-panel.tsx` forwards each physical pointer click as one `click` input; do not also emit `down`/`up` for the same gesture because the remote Playwright click would run twice.
- `src/core/threads/hooks.ts` owns pre-submit upload state and thread submission.

## Code Style

- **Imports**: Enforced ordering (builtin → external → internal → parent → sibling), alphabetized, newlines between groups. Use inline type imports: `import { type Foo }`.
- **Unused variables**: Prefix with `_`.
- **Class names**: Use `cn()` from `@/lib/utils` for conditional Tailwind classes.
- **Path alias**: `@/*` maps to `src/*`.
- **Components**: `ui/` and `ai-elements/` are generated from registries (Shadcn, MagicUI, React Bits, Vercel AI SDK) — don't manually edit these.

## Environment

Backend API URLs are optional; an nginx proxy is used by default:

```
NEXT_PUBLIC_BACKEND_BASE_URL=http://localhost:8001
NEXT_PUBLIC_LANGGRAPH_BASE_URL=http://localhost:8001/api
```

Leave these unset for the standard `make dev` / Docker flow, where nginx serves the public `/api/langgraph/*` prefix and rewrites it to Gateway's native `/api/*` routes.

## Resources

- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [LangChain Core Concepts](https://js.langchain.com/docs/concepts)
- [TanStack Query Documentation](https://tanstack.com/query/latest)
- [Next.js App Router](https://nextjs.org/docs/app)

## Contributing

When adding features:

1. Follow the established `src/` structure
2. Add TypeScript types and proper error handling
3. Write unit tests under `tests/unit/` (`pnpm test`) and E2E tests under `tests/e2e/` (`pnpm test:e2e`)
4. Run `pnpm check` before committing
5. Update this `AGENTS.md` when architecture, commands, or conventions change
