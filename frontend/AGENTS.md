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

Rstest runs them as two projects (`rstest.config.ts`). `*.test.ts` / `*.test.tsx` run in a plain **node** environment — that is nearly the whole suite, and it is the default for anything that is pure logic. `*.dom.test.ts` / `*.dom.test.tsx` run in **happy-dom**, for tests that need a document: hooks driven through `renderHook` from `@testing-library/react`, and components. Keep the split — a DOM environment costs roughly 3x the runtime of the node suite, so tests that do not render should not opt into it. A hook whose behavior only exists under real React (effect ordering, cleanup on unmount, re-render on store change) belongs in a `.dom.test.*` file rather than a node test that mocks `react` itself.

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
2. Stream events update thread state (messages, artifacts, todos, goal). The main thread stream uses the LangGraph SDK's `throttle: true` mode so updates received in the same macrotask coalesce before React is notified; do not replace it with a numeric delay without validating the SDK's trailing-debounce behavior on a continuous stream.
   File-tool artifact auto-open work must run in an effect with timer cleanup; never schedule timers while rendering streamed `write_file` or `str_replace` updates.
   `ThreadState.artifacts` remains the authoritative artifact list. The artifacts provider persists only thread-scoped panel UI state (`open`, selected path, and a refresh bootstrap cache) in session storage; an initial empty stream value must not overwrite that restored state before history finishes loading.
   Formal artifact content is refreshed once when the run finishes; transient `write-file:` previews remain message-driven.
   The detail view exposes explicit editing only for an already-opened formal UTF-8 text artifact under `/mnt/user-data/outputs`. Drafts stay in provider memory until Save so switching right-side panels cannot discard them, render in Markdown/HTML preview, and are protected from remote refreshes by the loaded SHA-256 revision. Saving is disabled during an active run; a changed revision preserves the draft and surfaces a conflict instead of overwriting agent output.
3. `useThreadHistory` loads persisted conversation pages from `GET /api/threads/{id}/messages/page`, preserving the backend's thread-global event `seq`; rendering overlays checkpoint/live copies at their matching canonical identities (a summarized checkpoint may contain a protected early input plus a recent tail). Context-compaction rescue diffs every retained visible identity rather than slicing at the first anchor, and keeps a run-scoped ledger of committed visible messages so replacement updates and repeated rolling checkpoint windows cannot erase an already displayed step. The resolver suppresses checkpoint/transient prefixes whose canonical position is still behind an unloaded cursor page instead of collapsing that unknown gap before a recent anchor, then adds optimistic messages without timestamp re-sorting. History invalidation preserves already-loaded pages so their established ordering positions are not discarded. Dynamic context re-keys the submitted user message from `X` to `X__user`; UI identity matching normalizes that reserved suffix only for human messages so the submitted frame and checkpoint replacement remain one visible turn. A locally submitted turn also records its pre-submit identity baseline: if `messages-tuple` publishes new AI/tool steps before `values` publishes that turn's human message, render ordering moves only those non-baseline visible steps behind the new human while leaving history, hidden controls, and reconnected runs untouched. Keep that local order anchor through finish, stop, and stream error because the SDK's settled frame can retain transient event order; replace it on the next local submit and clear it on thread switch or replay-gap recovery.
4. Stop actions call the LangGraph SDK stream stop path; `core/threads/hooks.ts` invalidates current-thread, thread-history, token-usage, and sidebar/search caches immediately and schedules one follow-up refetch because SDK stop may finish via abort + fire-and-forget cancel before backend title finalization commits
5. TanStack Query manages server state; localStorage stores user settings. The
   Settings > Tools MCP switch calls the targeted `PATCH /api/mcp/config`
   mutation, disables switches until that mutation's success refetch completes,
   displays the backend error `detail` through a toast, and invalidates
   `["mcpConfig"]` only after success.
6. Components subscribe to thread state and render updates

The chat header's context-window control is intentionally persistent: while `context_usage` is unavailable, `ContextUsageBadge` renders a gauge placeholder rather than unmounting; once data arrives, the same position shows the percentage. `useThreadTokenUsage` retains placeholder data only when the response `thread_id` still matches the active route, so same-thread refetches do not flicker and cross-thread navigation never displays the previous chat's usage.

Run duration is run-scoped UI metadata even though the compatibility field `additional_kwargs.turn_duration` is repeated on historical AI messages. `core/messages/run-duration.ts` folds those copies into one display anchored after the run's last visible message group. `MessageList` owns the temporary client-side duration for a just-completed live turn until authoritative history arrives. The duration is total run wall-clock time, not per-message reasoning time; reasoning disclosure and run activity/duration are rendered separately.

The workspace-change card follows the same rule: it is resolved from `(threadId, runId)` alone, so every AI message of a run would render an identical copy. A run ends in more than one terminal assistant bubble whenever the model emits answer text that never gains a tool call, so `core/messages/workspace-change-anchor.ts` picks the run's last assistant bubble and `MessageListItem` renders the badge only for that anchor (#4555). Any future run-scoped display belongs in the same place — do not hang one off every message. The two anchor helpers deliberately differ in which group types they accept as a run's last position, because an anchor is only useful where the display is actually rendered: run duration is emitted by `MessageList` around every group, so it accepts any type, while the workspace-change card comes from `MessageListItem` and so restricts to `assistant`. Keep a new helper's candidate set matched to its own render site rather than unifying them.

Composer drafts are tab-scoped browser state. `core/threads/composer-draft.ts` stores only text plus the selected slash-skill name in `sessionStorage`, keyed by user, agent, and logical conversation scope. New-chat pages pass the stable scope `"new"` because their runtime `threadId` is a fresh UUID on every reload; established conversations use their real thread ID. `InputBox` waits for enabled skills before restoring a skill chip, degrades a missing/disabled skill back to editable slash text, and clears the stored draft through `SendMessageOptions.onSent` only after the send passes the in-flight guard. Attachments, sidecar quotes, voice state, and polish undo state are not persisted.

Auth UI note: the login page's "keep me signed in" option submits only `remember_me` to the Gateway and may persist only the email address through `core/auth/remember-login.ts`. Passwords and tokens must never be stored in frontend storage; the `HttpOnly access_token` and readable `csrf_token` cookies remain Gateway-owned.

`/goal` and `/compact` are built-in composer commands, not skill activations. `src/components/workspace/input-box.tsx` intercepts `/goal`, `/goal clear`, and `/goal <condition>` before normal chat submission, calling Gateway `GET/PUT/DELETE /api/threads/{thread_id}/goal`. Setting `/goal <condition>` also submits the condition text as the next user task so the agent starts running immediately; status and clear do not start a run. Goal and compact requests are tied to the current `threadId` with an `AbortController`, so switching threads or unmounting the composer aborts in-flight requests and stale responses cannot update the new thread's composer state. The chat pages render `GoalStatus` above the composer from `AgentThreadState.goal`, with local optimistic state until the next stream `values` update arrives. `/compact` calls `POST /api/threads/{thread_id}/compact` to summarize older active context while leaving the full visible chat history intact; it is skipped on new/empty threads and blocked server-side while a run is in flight. Thread rename uses the same serialized state-write route; the rename dialog stays open and surfaces the server error when an active run returns 409.

The `/` skill list stays reachable after a skill is selected: typing `/` in the editable text beside the chip reopens it, and picking an entry swaps the chip rather than adding a second one, because the wire format carries exactly one leading `/skill`. That list offers skills only while a chip is selected — a builtin command owns the whole composer line, so `/goal` behind a selected skill would submit as chat text instead of running the command. The trigger itself is unchanged: a slash only opens the list at the start of the input (`getLeadingSlashSkillQuery`), pinned by `tests/e2e/chat.spec.ts`.

Human input requests are a structured message protocol layered on normal chat history. The backend writes request payloads to `ToolMessage.artifact.human_input`, `src/core/messages/human-input.ts` owns the runtime validators/types, and `src/components/workspace/messages/human-input-card.tsx` renders the reusable card. The protocol is versioned on the request side only: v1 covers `free_text` / `choice_with_other`, and v2 adds `form` (typed fields — text/textarea/number/select/multi_select/checkbox/date — with required-field validation in the card). Replies deliberately stay on the v1 response protocol: the form card submits a `response_kind: "text"` reply whose value is the human-readable summary plus one JSON block keyed by stable field names (`buildHumanInputFormSubmissionValue` — the readable part alone is ambiguous because labels/values may contain the separators), so the model can reconstruct the submitted mapping without a structured response kind. The validators reject unknown versions/modes (and field names colliding with JS `Object.prototype` members) so future protocol bumps degrade to the plain-text ToolMessage fallback rather than rendering a broken card. Form values are read through own-property access only (`readHumanInputFormValue`); select fields stay controlled from their empty-string placeholder state through selection; checkbox fields are native `<input type="checkbox">` controls seeded to an explicit `false` (`buildInitialHumanInputFormValues`) so an untouched checkbox submits as "no" while a `required` checkbox keeps must-agree semantics (no HTML `required` attribute — native constraint validation would intercept the custom submit path), and form controls carry label/`htmlFor`, `aria-required` plus a visually-hidden localized "required" marker, and `aria-invalid`/error associations whose error node stays mounted while any field is still invalid. Composer-bypass closure: `deriveHumanInputThreadState` treats a visible plain human message as answering the latest unanswered request opened before it (only the latest — nothing guarantees a single outstanding request across runs, and closing all would silently swallow older decisions; an older request left open simply becomes the active card again). This lets current users bypass a structured form through the normal composer and preserves compatibility with old v1-only frontends that degrade a v2 request to plain text. `MessageList` owns answered/latest/pending state for visible cards, but derives answered responses from raw `thread.messages` because replies are hidden; pending cards clear when the hidden reply appears, when dispatch is dropped, or when a new `thread.error` reports an async stream failure. Page-level card submit callbacks must send a normal human message and put `hide_from_ui: true` plus the response payload in the fourth `sendMessage(..., options)` argument as `options.additionalKwargs`; the third argument remains run context such as `{ agent_name }`. Composer entry points remain enabled while a human-input request is open; a normal visible message intentionally bypasses the card and starts the next run without structured response metadata.

Tool-calling AI messages can contain user-visible text as well as `tool_calls`. `core/messages/utils.ts` keeps these turns in an `assistant:processing` group, and `components/workspace/messages/message-group.tsx` must render the visible text as a processing step instead of treating the message as only tool metadata. This preserves provider text such as error explanations or "trying another approach" notes during tool-heavy runs.
While the current turn is still loading, a content-only AI message after the latest visible human input also stays in that processing group until the turn settles: a provider may append tool-call chunks to the same message later, and classifying it as a final assistant bubble too early makes the text jump into the steps panel. `MessageGroup` therefore renders processing text even before the first tool call arrives.
The same rule applies after an earlier tool call: a later content-only AI message remains visible after the current last tool-call step while streaming, because that message may itself gain another tool call before the turn settles.
Because the same message is rendered by two different components over its lifetime, reasoning must sit above the answer text in both. `MessageListItem` paints the settled bubble's `<Reasoning>` disclosure above its content, so `MessageGroup` puts the trailing reasoning disclosure above the assistant text that follows it and `convertToSteps` emits a message's reasoning step before its content step — otherwise the two swap places the instant the turn settles (#4576). Assistant text emitted _before_ that reasoning keeps its earlier position; only the answer the reasoning produced moves below it.

Edit-and-rerun is deliberately latest-turn-only. `core/messages/utils.ts::getLatestEditableTurn()` exposes a human turn only when the transcript is idle and the most recent visible turn ends in a terminal assistant message. `core/threads/hooks.ts::editAndRegenerateMessage()` calls `POST /api/threads/{id}/runs/edit-regenerate/prepare`, submits the returned replacement message/checkpoint/metadata through the same LangGraph stream path as regenerate, optimistically hides the superseded message ids, and clears the optimistic replacement once the persisted replacement arrives.

`MessageGroup` builds its tool-result and browser-preview lookups once per processing group before converting messages to steps. The lookup preserves the first non-empty result and first screenshot-bearing browser view for each tool-call ID, matching the streamed-message display semantics without repeatedly scanning the full group for every tool call.

### Key Patterns

- **Server Components by default**, `"use client"` only for interactive components
- **Thread hooks** (`useThreadStream`, `useSubmitThread`, `useThreads`) are the primary API interface
- **Thread routes** — construct Web UI chat paths through `core/threads/utils.ts::pathOfThread()`, which percent-encodes both custom agent names and thread IDs before inserting them into route segments
- **LangGraph client** is a singleton obtained via `getAPIClient()` in `core/api/`
- **Run stream options** are sanitized by `core/api/stream-mode.ts`: the Gateway-supported set is `values`, `messages-tuple`, `updates`, `debug`, `tasks`, `checkpoints`, and `custom`; any request containing an unsupported mode throws before HTTP instead of being partially forwarded or silently defaulting to `values`. `streamResumable` is retained by thread hooks only for SDK-side reconnect bookkeeping but stripped before the HTTP request because the Gateway does not accept that request option; actual replay uses the SSE `Last-Event-ID` cursor. Keep this boundary aligned with the backend request schema; `messages` and `events` are not supported and must not be forwarded.
- **SSE replay gaps** are handled in `core/api/api-client.ts`, which wraps both initial and joined run streams because the upstream SDK ignores unknown event names. An id-less backend `gap` control frame clears stale reconnect metadata, emits an internal `stream_replay_gap` custom event, reloads durable thread values, and rejoins after the server-provided retained tail, with up to five recovery rejoins after the original stream (six total stream calls on an all-gap exhaustion path). The wrapper remains a lazy async iterable because the SDK consumes it with `for await`. `core/threads/hooks.ts` clears optimistic/transient/subtask state, invalidates durable history caches, and shows the localized recovery warning; never let a gap fall through as a normal stream finish or cancel the still-running backend run.
- **Streaming Markdown rendering** is owned by `core/streamdown`: Streamdown's `animated` / `isAnimating` API handles incremental word animation, while the shared `streamdownRenderingPlugins` config registers the named code-highlighting and Mermaid plugins required by Streamdown 2.5. Keep wrappers and derived configs wired to that shared object; do not reintroduce a rehype plugin that wraps every word, because reparsing a growing block remounts old words and replays their animation.
- Citation links in message and artifact Markdown must derive their `citation:` label from the full `ReactNode` children tree, since Streamdown may provide element or array children during streaming rather than a plain string.
- **Environment validation** uses `@t3-oss/env-nextjs` with Zod schemas (`src/env.js`). Skip with `SKIP_ENV_VALIDATION=1`
- **Subtask step history and runtime metadata** (`core/tasks/`) — the subtask card shows a subagent's full step timeline (#3779): its assistant reasoning turns interleaved with the tools it ran. `Subtask.steps[]` is accumulated live from `task_running` events (appended via `mergeSteps`, not overwritten) and backfilled on expand for historical runs by `fetchSubtaskSteps`, which pages the events endpoint scoped to one task (GET `/runs/{runId}/events?event_types=subagent.step&task_id=…&after_seq=…`) until a short page, so the run-wide limit can't truncate the timeline. `task_started` carries the effective `model_name`; `task_running` carries a cumulative usage snapshot after each completed LLM call. `core/tasks/lifecycle.ts` normalizes these additive events, and `computeNextSubtask` keeps the largest cumulative total so replayed or late SSE frames cannot double-count or roll the folded card backward. Terminal ToolMessage metadata (`subagent_model_name` / `subagent_token_usage`) restores the same values from normal history after reload; no per-card event fetch is needed. `core/tasks/steps.ts` is the pure step model: `messageToStep` (live), `eventsToSteps` (reload), `mergeSteps` (dedup by `message_index`), and `stepsForDisplay` (what the card renders — keeps tool steps + AI steps with text, drops the trailing final-answer AI step when completed since it's shown as `result`). `core/tasks/context.tsx`'s `useUpdateSubtask` applies updates against a `tasksRef` mirroring the latest state (not a closure snapshot), so a late-resolving `fetchSubtaskSteps` backfill merges into current state instead of clobbering SSE steps or sibling subtasks that arrived meanwhile. The owning `run_id` is carried onto history content messages in `buildVisibleHistoryMessages` so the card can resolve the events endpoint.

### Interaction Ownership

- The workspace UI is project-first with three rails (2026-07-25
  foundation-demo rescope, revision 3): the sidebar lists one folder entry per
  project of the active workspace, the project rail carries
  cycles/to-dos/conversations, and chat renders in the main area. The sidebar
  `PROJECTS` group label carries only a `+` create action — there is no
  portfolio route; `/workspace/projects` was removed. Legacy projectless
  conversations stay at `/workspace/chats` under "Unfiled chats". Agent and
  Skill management are peer destinations in the first rail; Skills lives at
  `/workspace/skills`, not inside Settings. The Agents page consumes
  `GET /api/agents/inventory`: its default Built-in tab shows the Lead Agent
  and runtime-available built-in subagents, while Custom combines user agents
  with deployment-configured subagents. Provenance and agent/subagent type are
  visible labels; only top-level agents can start direct chats, and only
  user-created top-level agents expose settings or deletion.
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
  the selected cycle's **Build plan**, an agents placeholder, and the project's
  conversations. **The rail is read-only** — it holds no text inputs, and the
  Cycles header is disclosure-only with no creation or demo actions. New-cycle
  intent is classified from normal project chat and enters the native setup
  flow there. A dedicated top-row panel toggle collapses the second rail from
  16rem to a 3rem navigation spine: the full rail body unmounts and is replaced
  by labeled icon controls for Cycles, Blockers, Agents, and Conversations.
  Choosing one restores the rail at that section. The panel toggle stays
  separate from the Cycles header. Selecting a stage opens
  `cycle-stage-sheet.tsx` — evidence, open blockers **and the form that records
  one**, the submit/review panel with a required rationale, and the activity
  timeline with actor and revision — using the existing right-side inspection
  pattern. **Design is the exception: its sheet is inspection-only.** It keeps
  the cycle timeline (`src/core/dbtl/timeline.ts`, which replaced the Phase 0
  path strip: a compact `Design 1 → Build 1 → …` walk plus an explicit
  disclosure revealing each decided edge's assessment, recorded override,
  routes not taken, decider, the bound evidence file — matched by content hash
  against the cycle's artifact list, with the hash prefix kept either way —
  the durable `dst-…` record id, and a link to the conversation whose
  registered deck recorded the decision, built from the server-joined
  `decided_in_thread_id` on the transition row),
  review package, evidence, open blockers with their recording
  form, and the activity timeline, but carries no submit control and no
  Approve/Request changes/Reject panel, and says so. Design is submitted and
  decided in the meeting's registered slide deck and nowhere else — project
  chat records no verdict; the two former Design branches (deck handoff and
  legacy) collapsed into one read-only body, and `dbtl.design_deck_feedback`
  now only decides whether the deck pointer is shown beside that notice.
  With `dbtl.progressive_gate`, that inspection-only fallback also mirrors the
  registered deck's agent assessment/rationale, explicit recorded override,
  legal routes with blocked reasons, and parked marker; authority still stays
  in the authenticated deck bridge.
  Every other stage keeps its controls
  unchanged. Blocker creation lives in that sheet, not the rail: a durable record
  is written from the surface that shows the evidence it refers to. The Cycles
  auto-minimizes when no cycle is live; an explicit click on the section header
  overrides that default. The transient DBTL feature request does not render a
  readiness notice in the rail; a settled frozen mode still does, so its reason
  and readiness settings remain discoverable. Project tree listings use
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
  The cycle-creation dialog is a bounded viewport layout, not one long
  scrolling modal: its header and action footer stay visible while only the
  form body scrolls. Title, research question, and any required parent are
  named as required, and the persistent footer explains why `Start cycle` is
  disabled. Keep that structure when fields are added; putting actions back
  inside the scroll region makes the dialog unusable at browser zoom or on
  short/mobile viewports.
  Phase 3 adds the durable cycle layer in the same module: `cycle-view.ts` is
  **pure and React-free** (stage/status vocabulary, why a stage is locked,
  whether a control is available, activity phrasing), `cycles-api.ts` owns the
  `/api/projects/{id}/dbtl/*` calls, and `cycle-hooks.ts` the TanStack hooks.
  Controls are derived from the server's stage status alone — the client never
  decides that a gate is open, it only renders what the durable record says.
  Cycle creation records both class and workflow weight. A project may run
  several live top-level cycles at once (backend migration 0018 dropped the
  single-active-cycle rule), so the dialog must not gate creation on an
  existing live cycle; a computational child under a live season/program
  parent remains available as before. The stage sheet displays only evidence bound
  to the selected stage and includes the manual URI + SHA-256 attachment form
  required to drive Phase 3 without a graph. Its evidence fields stay visibly
  labeled and required, and both evidence attachment and stage submission name
  the missing prerequisite beside their native disabled action. Artifact,
  blocker, and resolution calls carry the displayed durable revision plus a
  fresh idempotency key.
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
  Phase 6 adds the data readiness bridge: `reconciliation-view.ts` is **pure
  and React-free** (row/gate/blocker vocabulary, ordering, what a person may
  decide and what each decision does, the approval-invalidation notice),
  `reconciliation-api.ts` owns the dataset/row/decision calls and the stage-spec
  registry read, and `reconciliation-hooks.ts` the TanStack hooks. `blocksGate`
  is not recomputed client-side — the server sends it, so the matrix cannot show
  a settled row the gate still refuses. A **proposed** resolution renders as
  outstanding work, matching the backend exactly; if these two disagreed the
  reviewer would have no way to find out why the gate was stuck. Every status,
  outcome, and blocker kind has a _word_; `rowStatusTone` is offered only
  alongside that word, never instead of it. `gateHeadline` names the settled
  count even when ready, because "Ready for Build" with no denominator gives a
  reviewer nothing to check against. `orderedRows` sorts blocking rows first —
  creation order buries the one blocked row under twelve resolved ones.
  Decision payloads carry the row's own `db_revision` as
  `expected_work_item_revision` plus the cycle's as `expected_db_revision`; the
  two are different numbers. The API client has no `actor_type` field: a
  person's decision is the only kind the endpoint accepts.
- `src/components/workspace/project-rail/reconciliation-matrix.tsx` renders the
  matrix inside the reconciliation stage sheet, above the generic evidence list
  — on that stage the matrix _is_ the review. Decision controls state their
  consequence on the control itself, because settling a contradiction is a
  scientific call and "Resolve" alone does not say Build will proceed on it.
  Blocking requires naming _why_ before the submit enables, which is what lets
  the server's outcome code name the real problem. Recorded evidence references
  render with the decision, and an unreadable durable row is explicitly shown
  as gate-blocking rather than excluded.
- `src/components/workspace/dbtl/` owns the Phase 4 surfaces.
  `use-upgrade-proposal.ts` runs shadow evaluation _beside_ the send, never in
  front of it, and records proposal outcomes as telemetry. It renders nothing.
  The page passes `isNewConversation` from its pre-send welcome state so shadow
  telemetry mirrors the supervisor's first-visible-turn prior; this value has
  no mutation authority and the live supervisor independently derives
  freshness from checkpoint messages. Project cycle count and unfinished-cycle
  status are always resolved server-side.
  Setup clarification and final confirmation arrive from the supervisor as
  `ask_clarification` artifacts and render inline through DeerFlow's existing
  `HumanInputCard`; there is no DBTL-specific composer card or card wrapper.
  Selected-cycle continuation is deliberately not a proposable route because
  the same request already runs `LiveStageAdapter`. `evaluation-drawer.tsx` is
  the internal tester view and
  is opt-in (`enabled`), because the endpoint behind it is administrator-only
  and an ordinary member opening settings should not generate a 403. It is
  mounted from Settings → DBTL readiness, provides a project selector, and
  lets the tester label an undecided classifier-ordinary row as correctly
  ordinary or cycle-worthy for missed-cycle calibration.
- **The composer is the only input surface.** The two left rails navigate,
  summarize, and review; they never collect a request. A rail action that needs
  input _arms the composer_ (sets its scope, moves the cursor there) instead of
  opening a form, and the human types the request in the chatbox. `InputBox`
  supports this with two generic slots so it stays feature-agnostic:
  `extraTools` (extra controls appended to the tool row beside attachments,
  voice, polish, and mode) and `focusSignal` (a monotonic counter; each change
  focuses the textarea, so an outside surface can hand the user back to typing
  without reaching in with a ref). The `focusSignal` effect deliberately skips
  the first render — an unsolicited mount focus would steal the cursor on every
  conversation merely opened.
- Project, cycle, and project-conversation rows expose compact icon actions
  with native confirmation dialogs. Project removal archives the server record
  and preserves its folder, cycle removal requires a rationale and records an
  abandonment, and conversation deletion uses the shared `useDeleteThread`
  cascade before navigating an open thread back to the project's new-chat
  route. The first rail hides each project's overflow/delete control while
  collapsed, leaving the project icon as the row's only target; expanding the
  rail restores those actions. Abandoned cycles are omitted from the active
  project rail. Cycle
  titles are disclosure controls: clicking a folded row unfolds it, clicking
  the open row folds it, and opening one cycle folds the previous one.
- `src/core/dbtl/composer-scope.ts` is the pure logic for the composer's DBTL
  scope selector, and it exists so the three promises the selector makes are
  unit-testable without rendering anything. `nextContextAfterSend` returns
  ordinary **unconditionally** — that is the whole mechanism behind "affects the
  next request only"; it is neither the user's job to switch back nor a cleanup
  step a component can forget. It matters most for `start_cycle`, because setup
  continues through the assistant's own follow-up questions and a sticky scope
  would re-enter the setup branch on every later message. `normalizeContext`
  resolves a stored selection against the live cycle list on every render,
  because a cycle can be completed or abandoned in another tab between the click
  and the send and the label must not keep claiming a scope that is gone.
  Terminal cycles are omitted from the menu (a completed cycle cannot be
  continued, so offering it would produce a refusal instead of an action), but
  numbering still comes from the **full** list so the menu and the project rail
  agree about which cycle is "Cycle 02". `runContextPayload` emits the one-run
  `dbtl_supervisor_enabled` flag plus the backend's routing keys
  (`dbtl_explicit_choice`, `dbtl_selected_cycle_id`). "Ask the AI to recommend"
  keeps the supervisor flag but omits an explicit choice — precisely the one case
  where the backend precedence ladder consults the classifier. A "cycle"
  selection with no id degrades to ordinary rather than claiming a continuation
  it cannot name, and `start_cycle` never carries a cycle id at all, so a stale
  id cannot make setup read as a continuation. `humanInputRunContext` sends an
  untyped native `ask_clarification` reply back through the automatic supervisor
  decision instead of manufacturing an explicit ordinary choice; typed setup
  and Design replies retain their dedicated start/continue behavior.
- **There is no scope selector.** The composer is the only input surface and it
  carries no mode control: the assistant judges whether a request belongs in a
  cycle. `AUTO_REQUEST_CONTEXT` is the resting state and it sends
  `dbtl_supervisor_enabled` **without** `dbtl_explicit_choice` — that absence is
  the one case where the backend's precedence ladder consults the classifier. A
  menu defaulting to "ordinary" transmitted an explicit choice on every message,
  which settled routing on the ladder's first rung and meant the classifier
  never ran: every shadow-telemetry row read
  `route_source=explicit_choice, confidence=0.0`, measuring nothing. A default
  is the _absence_ of a choice, not a choice. Anything other than `auto` now
  comes from a deliberate act — the rail's `+` arms `start_cycle` for one
  request, and a cycle clicked in the rail rides along as
  `dbtl_selected_cycle_id` so continuation stays reachable without a menu.
  `nextContextAfterSend` still returns the resting state unconditionally, so no
  scope can capture later turns.
- The `start_cycle` scope is how a cycle is opened: the request the user types
  becomes the setup conversation, routed to the backend supervisor's
  `cycle_setup` branch, which proposes an objective, names the missing fields,
  states the required human gates, and asks for confirmation. **Nothing is
  recorded until that confirmation**. Missing fields and the final no-write
  confirmation both use the transcript-native Human Input Card. The native
  confirmation artifact carries the bounded server-owned cycle setup payload;
  choosing Create invokes the existing authenticated cycle endpoint.
  `cycle_continuation` instead runs `LiveStageAdapter` directly and never raises
  a proposal card alongside that work.
- **Creating a cycle and starting its Design council are two steps, and both
  paths must do both.** The debate is a `continue_cycle`-scoped request, so it
  cannot be sent before a cycle id exists. `StartCycleDialog.onCreated` does
  `selectCycle` + `requestDesignKickoff`; the conversational path does the same
  after the chat page handles the native setup confirmation, which is why
  `useDbtlUpgradeProposal.createFromNativeSetup` **returns the created
  `CycleRecord`** instead of discarding it (`null` on failure, so a failed
  confirmation cannot start a debate about a record that does not exist).
  Wiring a new creation route
  without the kickoff leaves a cycle that never gets designed — that regression
  shipped once already.
- **On the conversational path the kickoff is armed, not sent.** Approving the
  native confirmation calls `armDesignKickoff`; the supervisor answers that
  same approval with its design questions, and `releaseDesignKickoff(answer)`
  fires only when the human answers the `cycle_setup` card. Sending it at
  creation raced the questions and the council convened knowing nothing but the
  objective, which is how it produced confident syntheses of no use to anyone —
  and it made the questions pointless, since the debate they exist to ground
  had already happened. The answers ride along as `PendingDesignKickoff.
designNotes` and are handed to the council as the owner's decisions rather
  than as suggestions to revisit; re-deriving them from the transcript would
  make the council's grounding depend on summarization. `StartCycleDialog`
  still calls `requestDesignKickoff` directly: that form collects the same
  facts up front, so there is nothing left to ask.
- `POST .../dbtl/proposals/draft-setup` and its merge helpers remain available
  to the degraded form path, but graph-enabled project chat does not invoke
  them. The supervisor derives bounded setup data from the conversation and
  asks for missing facts through the native card instead of mounting a second
  form above the composer.
- **Do not point `setup_draft_model_name` at a Claude subscription model.** The
  Claude Code OAuth path impersonates the CLI (`claude_provider`'s billing
  header), and the OAuth inference endpoint returns `stop_reason=refusal` with
  empty content for backend prompts that do not look like Claude Code traffic —
  drafting came back empty every time on `claude-fable-5`, while the same prompt
  worked on the Codex/ChatGPT subscription and on OpenRouter. Expect the same
  constraint for any other internal backend prompt.
- A textless send does **not** consume the armed one-shot scope. The DBTL routes
  read the request's text, so a textless send cannot start or continue anything,
  and disarming on it stranded users who armed the composer and then sent
  nothing. `start-cycle-dialog.tsx`
  is retained only as the degraded path for installs where the supervisor graph
  is off (`audit_only` / `manual`): chat structurally cannot run setup there, so
  removing the form would leave no way to open a record at all. The project
  rail picks between them on `graph_execution_enabled` — do not make the form
  the primary path again on a graph-enabled install.
- `src/components/workspace/dbtl/cycle-selection-context.tsx` carries an
  **explicitly clicked** cycle from the project rail to project chat. The sole
  automatic selection is a cycle returned by the user's own Start Cycle
  submission: it queues one Design-council kickoff and is consumed only after
  the chat stream accepts it. The rail may display a default cycle's details,
  but that default does not silently route requests as continuations. The
  provider is keyed by project slug so a selection cannot leak across projects.
  It also carries `pendingScopeRequest` / `requestComposerScope` /
  `consumeComposerScope`, the rail→composer handoff described above. That handoff
  **arms and never sends**: it sets the composer's scope and bumps the focus
  signal, leaving the request itself for the human to type.
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
- DBTL Phase 7 lives in `src/core/dbtl/validity-{view,api,hooks}.ts` and
  `project-rail/build-test-review.tsx`. `validity-view.ts` is pure and mirrors
  the backend validity projection: a headline metric meeting its threshold does
  not receive positive presentation when a validity check fails. Build renders
  one restrained provenance ledger (dataset binding, stage contract,
  code/config, environment, versioned outputs, deviations). Test renders
  headline metrics beside validity checks, with the computed scientific outcome
  visually dominant. The generic stage sheet is inspection-only: it renders
  evidence, computed outcomes, transition context, blockers, and activity but
  mounts no generic evidence, submission, blocker, or verdict inputs.
  Specialized Reconciliation/Learn controls remain during their chat migration.
  Test's authenticated Human Input Cards live in the originating conversation:
  the first offers or requires the configured review meeting, and the second
  offers only routes allowed by the server-computed outcome. Status always has
  a text label and never depends on colour alone.
  A deck approval that opens another stage uses the same generic card renderer:
  the backend posts a `dbtl_stage_handoff` single-choice request with **Start
  &lt;next stage&gt;** and **Hold here**. The frontend does not infer or persist a
  stage transition from that card; it submits the ordinary hidden card response,
  and the backend recovers the card-bound cycle if the one-shot composer scope
  has already cleared. A `handoff_failed` receipt is not a failed review: the
  parent restores the original action/comment/client submission id, sends the
  original reviewed revision on retry, and re-enables only that approval action
  so the backend can redeliver the prompt without recording a second verdict.
- DBTL Phase 8 lives in `src/core/dbtl/knowledge-{view,api,hooks}.ts` and
  `project-rail/learn-review.tsx`. The Learn sheet labels every agent-created
  item as a provisional candidate and keeps candidate disposition, human
  project promotion, selected-project publication, supersession, and
  retraction as visibly distinct controls. Publication requires an explicit
  project picker and rationale; promotion cannot infer targets. Retraction
  previews how many active project scopes will lose retrieval, while old claims
  remain visible as superseded or retracted audit records. The client renders
  server-owned status and never infers that stage approval promoted or
  published knowledge.
- `src/core/dbtl/phase7-demo.ts` owns the no-write Phase 7 human-demo fixtures,
  and `project-rail/phase7-demo-dialog.tsx` renders them. The project rail no
  longer exposes that preview. The three cases deliberately separate a
  strong-but-invalid result, missing evidence, and a supported result. The
  dialog says that it is a preview and never invokes a mutation hook.
- A successful `StartCycleDialog` calls its `onCreated` handoff. Project chat
  sends one visible, cycle-scoped Design-council prompt. Design questions use
  the existing `ask_clarification` card (`clarification_type=design_decision`)
  and retain that cycle scope instead of falling back to ordinary chat.
  Completed packages use the existing `present_files` group,
  `ArtifactFileList`, thread artifact reducer, and artifact inspector.
- **Answering a card must return to the branch that asked.** A card reply is a
  reply, not a scope choice, but a scope is still sent with it — so the fallback
  decides where the answer lands, and defaulting to ordinary strands it in the
  lead agent. `humanInputRunContext` in `composer-scope.ts` keys the scope off
  the request's own `clarification_type`: `design_decision` continues the
  selected cycle, `cycle_setup` returns to the setup branch (naming **no** cycle
  id — setup has not created a record, and claiming a continuation of some other
  cycle would misroute it), `council_preflight` continues the selected cycle
  **and carries the chosen depth** as `dbtl_council_depth` (the backend reads
  the depth from this same per-request context, so a reply carrying only the
  scope would re-raise the card it just answered; an unrecognized value is
  omitted rather than guessed at, and the backend falls back to its own
  recommendation; every option the card offers must appear in `COUNCIL_DEPTHS`,
  because an omission is silent — `human_input` was missing, so "Write it myself"
  sent no depth and convened the council the person had just declined),
  `council_adjustment` continues the selected cycle carrying **no** depth (that
  option starts nothing; the redrawn roster is shown again before a depth is
  chosen), and anything unrecognized stays ordinary. The preflight card
  itself needs no bespoke component — it is a native `select` human-input
  request whose roster rides in the Markdown `context`. Adding a
  new `clarification_type` on the backend means adding its case here too; the
  backend also recovers the intent from the card it emitted, so a stale frontend
  degrades rather than breaking, but the two should agree.
- `src/app/workspace/[project_slug]/[thread_id]/page.tsx` re-exports the
  canonical chat page. `src/app/workspace/chats/[thread_id]/page.tsx` derives
  its project scope from `useParams().project_slug` (falling back to
  `?project_id=`) and routes replaceState/redirect/branch targets through the
  matching base path, so a project conversation never navigates out of its
  project.
- `src/app/workspace/chats/[thread_id]/page.tsx` owns composer busy-state wiring.
- `src/app/workspace/chats/[thread_id]/page.tsx` owns branch-from-turn submission and navigation; sidecar `MessageList` instances do not receive the branch action.
- `src/app/workspace/chats/[thread_id]/page.tsx` and `src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx` own edit-and-rerun submission wiring because the page must preserve normal/custom-agent run context; `MessageList` only detects the latest editable user turn and renders the inline editor.
- `src/app/workspace/chats/[thread_id]/page.tsx` gates the Workspace Browser trigger and browser right panel on `/api/features -> browser_control.enabled`; default/failed feature discovery hides the browser control so optional backend installs do not show a dead Live socket.
- `src/app/workspace/chats/[thread_id]/page.tsx` and `src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx` own active-goal display state for their composer overlays.
- `src/components/workspace/messages/message-list.tsx` owns human-input card answered/latest/pending gating; entry pages only translate a submitted card response into `sendMessage` calls.
- `src/components/workspace/browser-view/browser-view-panel.tsx` forwards each physical pointer click as one `click` input; do not also emit `down`/`up` for the same gesture because the remote Playwright click would run twice.
- `src/core/threads/hooks.ts` owns pre-submit upload state and thread submission.
- `src/components/workspace/chats/chat-box.tsx` owns the desktop right-panel layout, and **all three** right panels (artifacts, sidecar, browser) share one `ResizablePanelGroup` — do not fork a non-resizable branch per panel kind, which is how the artifacts divider silently lost its drag handle (#4465). Open/close is `collapse()` / `resize()` on the side panel's imperative handle, not conditional rendering, so the width can animate. Three constraints hold that together: the size transition is applied from the group as `[&>[data-panel]]:transition-[flex-grow]` because the sized flex item is the library's own `[data-panel]` element rather than the child `className` lands on; it is applied only while an open/close is in flight, so a drag is not interpolated frame by frame; and during the animation the panel content is held at its final width in `cqw` and clipped, because a reflowing message list re-runs its scroll-to-bottom (pinned by `tests/e2e/sidecar-chat.spec.ts`'s no-animated-scroll test) and a re-wrapping composer changes which responsive labels it shows. Because the panel is `collapsible`, the library can also collapse it to `0%` on its own when a drag crosses `minSize`, without going through the state that owns it. `onResize` records the last positive size while the pointer moves, but the owning `sidecar` / `browserView` / `artifactsOpen` state must only mirror a final `0%` layout from `onLayoutChanged`, after pointer release; closing on the first `0%` resize frame breaks a continuous drag that reaches the edge and then reverses before release.

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

To reach a dev server on anything other than localhost — a LAN address, or a proxied hostname — list the host in `DEER_FLOW_DEV_ALLOWED_ORIGINS` (comma-separated; a full URL is reduced to its host). It feeds Next's `allowedDevOrigins`, which gates `/_next/*`, fonts, and HMR. Without it those requests get a 403 and the page renders server-side but never hydrates, so nothing on it — including the login form — responds. Development only; production builds ignore it.

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

## The Build plan in the project rail

`src/core/dbtl/build-plan-view.ts` is the pure layer and
`project-rail/build-plan-block.tsx` renders it. It replaced the rail's Blockers
section, which was the only place a person could look while a Build ran and said
nothing about the Build.

**One source, two projections.** The rail and the transcript's workflow block
read the same server view (`GET .../stages/{stage}/workflow`, via
`useStageWorkflow`, shared through the query cache rather than fetched twice). A
rail that derived its own notion of progress would eventually disagree with the
transcript and the person would have no way to tell which was right, so nothing
here recomputes a status — it only decides how to say one.

**Read-only.** No retry, no hold, no answer, no confirm; selecting a phase opens
the stage. Mutation stays in the chat card, where the decision becomes durable.

The constraints are the rail's, not this feature's. `ProjectRailFrame` is
`w-64` — about 224px of content — and scrolls as one column shared with Cycles,
Agents, and Conversations, so a phase row is `StageRow`'s existing shape and
nothing more: a `size-3.5` state icon, a truncated title, and a right-aligned
`text-[11px]` state word. Titles **truncate, never wrap** (a wrapping title
changes the rail's height as work moves; the full title lives in
`title`/`aria-label` and in the transcript), capability rides in the accessible
name because there is no room for a third column and the transcript already
names it, and `MAX_RAIL_PHASES` (8) mirrors the backend's own bound so an
uncollapsed list is safe in a shared scroll column.

**Exactly one thing moves**, on the running phase only, with
`motion-reduce:animate-none` — the same rule the Agents section directly below
applies, and a property of the rail rather than of either feature. `movingRow`
returns a single key rather than a per-row flag, so two spinners are
unrepresentable rather than merely unlikely, and the icon is keyed by phase so a
state change updates the row without remounting and restarting the rotation.
Tone pairs are reused from `STATUS_MARK` (no new hue family); the *keys* differ
because these are step states, not DBTL stage statuses.

The plan supplies phases that have not started — a rail that could only show
rows that exist would say a four-phase Build has one phase until the fourth
finished — while a recorded row wins wherever both describe the same phase,
since a row is what actually happened, and the newest attempt of a retried phase
is the one shown.

**Blockers do not disappear.** Open work items are how a person learns
Reconciliation is unsettled, and deleting that surface to make room would trade
one real signal for another. Reconciliation-kind rows ride as a count on the
stage row they belong to (in words, never colour alone); the rest keep a
collapsed line beneath the Build plan, which is the plan's own stated fallback.

## Runtime agent activity

`src/core/activity/` owns the rail's live presence-and-lineage projection, and
it is **pure and React-free** except for `context.tsx`. `reducer.ts` restates
the three rules the backend reducer enforces rather than trusting them from
upstream, because the browser sees a *different* stream — one that reconnects,
replays, and joins mid-flight: replay is not new information (folding twice, or
a prefix then the whole thing, gives the same rows), a settled row is closed for
good (later frames including another `started` are refused), and a row must be
opened before it can be updated (an `updated` for an unknown id is dropped;
backfill supplies the opening, and a synthesised row would misreport lineage the
reader never saw).

**Coalescing lives in the reducer, not the view.** An update that changes
neither actor, state, operation, nor lineage returns the **same state object**,
so React bails out of the render entirely. That is not only a wasted-work
concern: a remounted `animate-spin` restarts its rotation, so a token-rate
stream would make the one moving thing on screen stutter. Two rules follow — the
leaf selector is memoized on that object (`view.ts`, a `WeakMap`), and the
spinner is keyed by `activity_id` alone so a state change on the same actor
updates the row without remounting the icon.

`view.ts::activityView` is the single derived value the block reads: the active
leaf (deepest actor genuinely working, ties broken by highest sequence), its
dispatcher chain, its siblings, and one of five modes. `live` / `waiting` /
`settled` / `idle` / `unavailable` replace the old static green dot, which
looked like proof the Lead Agent was active. `waiting` is only honest when
*nothing* is working — one actor waiting on a person while another computes is
still a live run — and `unavailable` is never guessed as active: the in-memory
run-event backend loses rows on restart, which the rail states rather than
papering over.

Display names come from the **server's** registry (`Cycle supervisor`,
`Build stage`, `Build worker 2`, a seat's validated role label); the frontend
keeps only `ACTIVITY_STATE_LABELS` for the state words, and an unknown state
degrades to its raw value rather than blanking the row. `labels.ts` lives in
`core/activity` rather than `core/dbtl` because activity describes every
conversation, including projectless ones with no cycle near them.

`ThreadScopedActivityProvider` is mounted beside `ThreadScopedSubtasksProvider`
in `ChatProviders`, keyed the same way. Two keys, and conflating them is a bug:
the *provider* is scoped to the conversation, which is what gets torn down,
while an activity's identity is `(run_id, activity_id)`, which dedupes rows
inside it. On mount, replay gaps, stream errors, and terminal runs, the provider
reconciles the live tail with `GET /api/threads/{thread_id}/activity`. Live
frames are buffered while that read is in flight; persisted rows are folded in
server `seq` order and reject stale overlaps. A failed authoritative read makes
the rail say **Unavailable**, never **Idle**. The sheet pages backward through
the same endpoint as the reader reaches the end of its scroll (with an explicit
Load earlier control as the keyboard-accessible equivalent); older pages are
merged and the projection rebuilt in sequence order rather than appended in
arrival order.

`project-rail/agent-activity-block.tsx` is **fixed height with no internal
scrolling and every line truncated**: at ~224px of content width a wrapped actor
name changes the block's height, and a block whose height changes as work moves
is the flicker this design exists to avoid. Banned outright, because each
reintroduces a constraint the rail already has: an internal scroll container,
any grow-in-place disclosure (`Collapsible`/`Accordion`), a virtualized list, and
a height that varies with sibling count. If it needs more room, that is the
signal to put content in the sheet.

**Exactly one thing moves** — one `Loader2` with `animate-spin`, on the active
leaf. Ancestors are static, `dispatching` uses a static glyph, and every
animated indicator carries `motion-reduce:animate-none`. Colour is never the
only signal: each state has a distinct icon shape and its word is always
present, and the tone pairs are reused from `STATUS_MARK` (no new hue family) —
but those keys are DBTL *stage statuses* and these are *activity states*, so
reuse the tones, not the key names. This applies to the expanded sheet as well
as the compact block: every active row is visible there, but only the leaf
selected by `activityView` animates.

**The block is not an `aria-live` region and no ancestor of it may be one.** Its
DOM churns on every transition; announcing that would flood a screen reader
during a governed run, for a surface the reader did not ask to monitor.
Announcements come from one `sr-only role="status" aria-live="polite"` sibling
the provider writes to, kept **outside** the block's subtree, carrying only
actor-level changes coalesced to at most one every few seconds. The expanded
timeline is `role="log"` with `aria-live="off"` — a reader who opened it is
reading it, not being read to.

`agent-activity-sheet.tsx` is the expanded surface, following
`cycle-stage-sheet`'s convention rather than growing in the rail (which would
push Cycles/Blockers/Conversations out of view and nest a second scroll region).
Tree indentation caps at three levels. Every child row explicitly names its
immediate dispatcher with `Dispatched by …`, not only rows beyond the depth cap:
settled parents are filtered from the live view, so indentation alone can make
an active worker appear to have been dispatched by the wrong visible ancestor.

Gated by `useAgentActivityFeature()` → `/api/features -> agent_activity`, which
carries `enabled` (rollout) and `durable` (false on the in-memory run-event
backend). Both fail closed on a missing or failed read, so an older backend
shows today's placeholder rather than an empty block that never fills. Scoped to
`md` and above, because `ProjectRailFrame` does not render below it — a mobile
entry point is follow-up work, not a sheet with no host.

Note for tests that mock `react`: `core/threads/hooks.ts` imports
`useActivityContext` from `@/core/activity/context`, so a node test mocking
React must mock that module the same way it already mocks `@/core/tasks/context`.

## Design council debate view

`src/core/tasks/council-seat.ts` is the pure layer for the live Design meeting
view, and `src/components/workspace/messages/debate-panel.tsx` renders it. Its position comes from
`message-list.tsx::debatePanelPosition` + `insertDebatePanel` (both exported and
unit-tested): **bottom of the transcript while the run is in flight**, and
**immediately above the closing answer once it settles**, so the conclusion
reads below the meeting that produced it. The panel still belongs to the run
rather than to a message; only its placement is anchored. Do not anchor it to
the _request_ instead — that puts it above the preflight card, far up a long
transcript, where it is mounted and streaming but effectively invisible.
Out-of-range positions clamp rather than dropping the panel.

The task ledger is **conversation-scoped**, even though project layouts persist
while navigating between conversations. `ChatProviders` keys
`ThreadScopedSubtasksProvider` by the routed `thread_id`, so a paused meeting
remains visible when its conversation is reopened but cannot render over
`/workspace/<project>/new` or another thread. The native-history transition
from a new-chat route to the UUID created by its first send can leave Next's
route param as `"new"`; `useThreadStream` therefore also clears the ledger when
its canonical `currentViewThreadId` changes. `MessageList` refuses to mount a
debate panel with zero transcript groups, preventing even a one-frame stale
paint on a blank chat. Do not move the unkeyed `SubtasksProvider` back to
project scope: it makes a clean new-chat transcript display the previous
conversation's debate panel and can make its pending decision look
project-global.

Each participant lane is clickable and opens
`meeting-participant-inspector.tsx`, a right-side sheet showing that
participant's steps: reasoning turns, tool calls paired with their output, and
the closing position, with a timeline on the left and the selected step's
request/output on the right. `src/core/tasks/meeting-transcript.ts` is the pure
layer for it, and the pairing half now lives in
`src/core/tasks/tool-transcript.ts` because nothing about it was
meeting-specific — a council seat and an ordinary delegated subagent record
steps in exactly the same shape, so both read the same way and a Build worker
never needs a renderer of its own. `parseMeetingResult` renders a participant's closing position as
sections rather than the validated JSON it is transmitted as — **contested
items before the synthesis**, because where the meeting disagreed is what tells
a reader whether the synthesis is a conclusion or an average, and it is
worthless once they have read the synthesis as settled. An unresolved
disagreement keeps its empty resolution and renders as "Not resolved"; prose or
a malformed payload degrades to the raw text rather than costing the reader the
result. `tool-transcript.ts` handles pairing a tool result to the call that asked for it (position is
the only honest join; the recorded step shape carries a tool name but no call
id), keeping a result whose requesting turn was compacted away, and marking a
still-running call `pending` rather than rendering it as an empty success. Its
one option, `dropTrailingAnswer`, removes a completed task's closing turn (which
the caller already renders as `task.result`) *before* pairing, so the tool-call
turns it depends on are still intact — the meeting inspector deliberately does
not use it, because a participant's final position is part of its transcript.
`meeting-transcript.ts` keeps the meeting's own vocabulary and appends the
closing answer. Lanes also show the latest step inline, so a three-minute
meeting reports what it is doing rather than spinning. A reloaded run has no
live SSE steps, so the lane backfills once via `fetchSubtaskSteps` — the same
endpoint the subtask card uses — which is why `DebatePanel` takes `threadId`
and `runId`.

**A tool row opens to show what was asked and what came back**
(`subtask-tool-step.tsx`). The card named the tool and stopped there, while the
request arguments and the tool's output sat unread in the step model — which is
exactly the information that explains a failure (which path was denied, what the
command printed, why a contract was rejected). Bounded twice on purpose: the
backend truncates what it persists (`SUBAGENT_STEP_MAX_CHARS`) and the row caps
what it renders, and **both say so** rather than trailing off silently. A
pending call still opens, because "no output recorded yet" is the answer during
a live run and is deliberately distinguished from "the tool returned no output".

**Governed stage work gets a transcript anchor** (`stage-work-panel.tsx`,
`src/core/tasks/stage-work.ts`). A `SubtaskCard` is mounted from an assistant
message's `task` tool call; a DBTL stage worker is dispatched by the stage
adapter and its task id is a work-unit id, so it had no message to hang from and
rendered nowhere at all — a Build ran invisibly and the only evidence was a
package appearing at the end. The panel adopts tasks whose **server-set**
`dbtlStage` is present and that carry no `councilSeat`; both exclusions matter,
since adopting too little makes Build invisible again and adopting too much
renders a meeting seat twice (here and in `DebatePanel`). Never infer the stage
from a task id or description. `dbtlStage` rides on the terminal event as well
as `task_started`, because a page joining mid-run can miss the start entirely.

The backend sends a `council_seat` block on every `task_started` /
`task_completed` / `task_failed` event. **Do not derive the seat from the task
id** — every field on it is technically recoverable from the id today, which is
exactly why it must not be: a view that parses identifiers to decide who is
speaking is one rename away from labelling every seat wrong, silently.
`readCouncilSeat` is defensive in one direction only: a malformed or absent
block means "ordinary subtask", never a seat with guessed fields.

Three things are shown live that previously appeared only afterwards in the
review package, by which time it is too late to intervene: which seat is the red
team, that a differently-labelled expert is actually a stand-in generalist, and
which lane's answer counts toward the stage. `debateRounds` sorts by debate role
rather than arrival, because a reader is following an argument rather than a
race.

`consensusState` is **derived** from the seats rather than reported alongside
them — a separate field would be a second source of truth about something the
seats already say, and the two would eventually disagree in front of a user.
`stalled` is a distinct state from `settled` and `debating`: a council whose
chair failed has neither reached consensus nor merely finished, and leaving a
spinner running over a council that is already over is the exact experience this
redesign removes. `partial` is distinct too: the chair reported, but at least
one non-chair participant failed, so its output must not be presented as a
successful discussion. Status always carries a word, never colour alone.

The native council preflight uses `input_mode=single_choice`; every option has
an `id`, `label`, and `value`, with optional description, and
`recommended_option_id` names the server recommendation. Keep this aligned with
`core/messages/human-input.ts` — an unrecognized mode or valueless option makes
the entire card fail closed. User-facing copy calls the feature the **design
meeting**; internal keys (`council_preflight`, `council_participants`, event
`council_seat`) keep the council vocabulary. The preflight artifact also
carries `council_participants` — one editable card per participant, prefilled
with the roster writer's suggestions — parsed fail-soft per entry in
`human-input.ts` (a malformed participant is skipped; nothing usable falls
back to the plain roster text). `meeting-participant-editor.tsx` renders the
collapsed cards (model select, token budget, reasoning strength,
instructions textarea) inside `HumanInputCard`. Meeting depth is a local
selection first, not an immediate submit: `participantsForCouncilDepth` removes
positions the selected policy will not run while preserving the red team and
chair, and a separate **Start meeting** action confirms the now-visible roster.
Only those visible participants contribute edits, so changing `position-2`
cannot silently ride on a Light reply that keeps only `position-1`. On submit,
`buildCouncilParticipantEdits` diffs against the prefills so an untouched
editor submits nothing, and the edits ride the option reply's optional
`participants` key (skipped for the roster-adjust option, whose redraw
returns fresh prefills). The backend re-validates every field, so the editor
is honest about prefills rather than defensive. Automatic Design kickoff messages carry
`hide_from_ui=true` plus `dbtl_design_kickoff=true`: they are explicit graph
input containing the owner's setup decisions, but must not impersonate text the
person typed.

`core/tasks/lifecycle.ts` owns all four custom-event transitions. A council
`task_started` creates an in-progress seat; `task_completed` and `task_failed`
are terminal and preserve result/error, cap reason, and seat identity. The
debate panel shows the current round, reported-seat count, bounded latest
argument summary, and counts from the chair's recorded `consensus` object.

`core/dbtl/design-consensus-view.ts` reads the machine package filename from the
bound review Markdown and parses only the chair's recorded consensus. The stage
sheet renders that decision map above the Markdown as a reading guide; it never
recomputes consensus, and the approval remains bound to the Markdown bytes.

## Design deck feedback bridge

`src/core/dbtl/design-deck-feedback.ts` is the parent's half of the Design deck
bridge: **pure**, React-free, and the security-critical boundary between an
agent-rendered page and the application. A deck runs in an opaque-origin iframe
and may _collect_ a decision; it may never authorize one. Everything it sends is
an intent parsed into a closed vocabulary (`ready`, `submit_intent`,
`open_evidence_intent`, `open_originating_conversation_intent`), and everything
sent back is state (`initialize`, `pending`, `accepted`, `stale`, `failed`). No
endpoint, token, or write capability crosses the boundary in either direction.

Two rules are easy to erode and worth restating. **Parsing here is a UX
boundary, not an authorization boundary** — the server repeats membership,
surface identity, evidence revision, and deck-hash checks on the write itself,
and if the backend ever trusts this module the model collapses to "any page that
can talk to the parent can write". **A settled surface never rebases**:
`reduceDeckState` refuses to return an `accepted` or `stale` surface to
readiness, because retrying a pending answer against a newer revision is how an
approval ends up bound to a document nobody read.

`parseDeckIntent` refuses a foreign source, a different protocol version, another
surface id, an unknown type, an unknown action kind, a selection that is not
exactly one slug-shaped option id, and any post-handshake message not carrying
the per-mount channel this parent issued. A non-string comment is treated as
absent rather than coerced, and an over-long one is bounded.

The deck's own half lives in the Python renderer
(`deerflow.dbtl.council_deck._bridge_script`) and is emitted **only** for a
server-registered surface — a legacy or unregistered deck carries no bridge at
all rather than a disabled one, because the safest version of "this file cannot
answer" is a file with no code that could. `tests/e2e/design-deck-bridge.spec.ts`
drives that script in a real browser against
`tests/e2e/fixtures/design-deck.html`, which is generated from the Python
renderer and guarded against drift by
`backend/tests/test_dbtl_deck_fixture_drift.py` (regenerate with
`cd backend && PYTHONPATH=.:tests uv run python -c 'import _design_deck_fixture as f; f.FIXTURE_PATH.write_text(f.render_fixture_deck())'`).
That browser suite earns its keep: it is what caught a deck that would happily
re-arm itself after being superseded, which no source-level assertion noticed.

`ArtifactFilePreview` owns the live controller only when it has trusted project
and thread context. It verifies `event.source`, resolves the opaque surface id
through the authenticated parent, hashes the exact HTML bytes with SHA-256, and
issues a per-mount channel before sending the server's allowed-action set.
`submit_intent` maps to fixed API handlers; endpoints and credentials never
cross into the iframe. A failed request keeps the DOM draft and client
submission id, while a 409 refresh that proves revision change or consumption
latches the deck stale instead of rebasing.

Chair answers start a background run outside the page's normal LangGraph
stream. After `resume_started`, `ArtifactFilePreview` therefore keeps the deck
pending and polls the authenticated surface read model. A successor surface
settles the old deck and invalidates durable thread/history queries so the new
deck appears in the originating conversation. A terminal run with no successor
returns `failed` plus the original allowed chair action; the bridge re-enables
the still-mounted controls without clearing the selected option, comment, or
client submission id, so the retry remains one payload-bound answer. On a
reload—or after a 409 caused by changing that failed answer—the authenticated
receipt supplies the original selected option/comment in the next
`initialize` message, and the deck restores them before re-enabling controls.
The persisted HTML still carries no answer and remains inert outside DeerFlow.

That background path must still speak through the main chat. The Gateway writes
one visible `llm.ai.response` carrying a bounded
`additional_kwargs.dbtl_meeting_progress` snapshot after it admits the chair
run. `message-list-item.tsx` renders it as
`DesignMeetingProgressCard`, reconstructing the already-reported independent
and red-team lanes from durable stage-worker rows and showing the chair as
synthesizing. The card polls the existing stage-workers read endpoint until the
new chair row completes or fails, so a reload does not depend on the live
`task_*` stream. Each durable lane opens the shared
`MeetingParticipantInspector`; the worker endpoint supplies the complete
structured closing report, while unavailable intermediate step events remain
uninvented. Keep this separate from `DebatePanel`: that panel is the rich
live-run view with inspectable step events, while this card is the durable
fallback for a run started by the authenticated deck parent.

The Design gate is two-step in the same deck at standard depth: submit first,
then refresh and show the three verdicts. With `dbtl.progressive_gate`, the
deck also shows the agent assessment/rationale, records a bounded explicit
difficulty override, exposes the legal server routes, collapses routine
submit+approve to one `advance` intent, and can Park the cycle. Park refreshes
the same still-live deck instead of settling it, so the user can return and
continue; a later gate decision clears the parked state. A deck-backed Human
Input request stays in
thread state for supervisor recovery but `MessageList` suppresses the duplicate
card and `hasOpenHumanInputRequest` leaves the ordinary composer unlocked.

**A surface id does not by itself mean the deck owns the card.**
`isDeckOwnedHumanInputRequest` is the single predicate for that suppression, and
it keys on `clarification_type`, not on the mere presence of
`design_feedback_surface_id`. On a Design decision the deck *is* the input
surface. On a `dbtl_stage_handoff` the surface id is only an audit binding —
which approval opened this stage — and the deck holds no Start/Hold control that
could answer it, so suppressing it rendered the card as nothing at all: the
control sat in durable thread history and nobody could see or answer it, while
the backend's routing fence re-presented that same invisible card on every
later message. The check is an **allowlist** of chat-answered types, so any
future surface-bound card stays suppressed until someone decides otherwise —
invisible is recoverable, wrongly interactive is not. `humanInputRunContext`
routes the handoff reply back to its cycle for the same reason every other card
has a case there.
With `design_deck_feedback` enabled, the project rail says **Open feedback
deck** and the Design stage sheet points at it; Reconciliation, Build,
Test, and Learn sheets are unchanged.

Progressive-gate Phase 2 makes this parent bridge stage-aware without changing
its security model. `cycles-api.ts` uses the canonical `/stage-feedback/`
aliases and surfaces include `stage`, monotonic `surface_revision`, and the
server-derived lifecycle `open | consumed | superseded`.
`ArtifactFilePreview` renders those fields above the opaque iframe and gives a
superseded surface a link to the registered newest deck. The server, not the
TypeScript parser, remains the intent authority. Phase 3 rollout flags are
available as `dbtl.stage_meetings.{build,test,learn}` and default false.
Build/Test/Learn decks can emit `convene_review_meeting`; the parent treats it
as a background successor-producing action, polls the authenticated surface
read model, refreshes the originating conversation, and remounts the successor
deck. A terminal run with no successor restores the ledger-bound action for an
identical retry. Progress and failure copy uses the surface's stage instead of
calling every action Design.

Human Input rendering deduplicates by stable request id but displays only the
newest physical delivery. A structured response settles that request; a
plain-composer compatibility guess is cleared if the server re-delivers it.
Both `dbtl_stage_handoff` and `dbtl_build_control` route back through the cycle.

`StageWorkPanel` converges from the thread-scoped stage-worker lifecycle read
after a run settles, even when a partial live task already exists. Async
terminal hydration uses eager batch reconciliation rather than the
render-deferred ToolMessage path. Governed cards render `displaySummary` (or a
safe legacy structured summary), never raw contract JSON. The Build rail
projects an open collaboration as **Waiting for you**.

A cycle may name its immutable `originating_thread_id`. The expanded rail links
it only when that conversation remains in the authenticated project list; the
link clears cycle selection and never arms request scope. Missing origins stay
visible as unavailable provenance.
