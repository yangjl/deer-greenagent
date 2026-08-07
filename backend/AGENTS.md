# AGENTS.md

This file provides guidance to AI coding agents (Claude Code, Codex, and others) when working with code in this repository. It is the source of truth; the sibling `CLAUDE.md` imports it via `@AGENTS.md`.

## Project Overview

DeerFlow is a LangGraph-based AI super agent system with a full-stack architecture. The backend provides a "super agent" with sandbox execution, persistent memory, subagent delegation, and extensible tool integration - all operating in per-thread isolated environments.

**Architecture**:

- **Gateway API** (port 8001): REST API plus embedded LangGraph-compatible agent runtime
- **Frontend** (port 3000): Next.js web interface
- **Nginx** (port 2026): Unified reverse proxy entry point
- **Provisioner** (port 8002, optional in Docker dev): Started only when sandbox is configured for provisioner/Kubernetes mode

**Runtime**:

- `make dev`, Docker dev, and production all run the agent runtime in Gateway via `RunManager` + `run_agent()` + `StreamBridge` (`packages/harness/deerflow/runtime/`). Nginx exposes that runtime at `/api/langgraph/*` and rewrites it to Gateway's native `/api/*` routers.
- Gateway streams `write_file` and `str_replace` argument deltas in bounded batches when clients also subscribe to `values`; messages-only consumers retain the original per-chunk contract, while `values` preserves the complete tool call.
- With `stream_subgraphs`, subgraph frames keep their namespace in the SSE event name (`values|<ns>`, LangGraph Platform style) instead of impersonating root frames — a delegated subagent inherits the parent checkpoint namespace, so publishing its `values` snapshot as bare `values` replaces the whole thread view in SDK clients (#4399). Root-only consumers (file-tool chunk batcher, subagent event persistence, LLM error-fallback detection) ignore namespaced frames. The web frontend does not request subgraph streaming; subtask progress rides root-namespace `task_*` custom events.
- Scheduled-task executions must reuse that same Gateway run lifecycle. The scheduler may decide _when_ work runs, but it must dispatch through the existing run path rather than introducing a parallel execution stack.
- Scheduled-task dispatch enforces "at most one active run per task when `overlap_policy=skip`" at the DB layer via the partial unique index `uq_scheduled_task_run_active` (`scheduled_task_runs.task_id WHERE status IN ('queued','running')`). `ScheduledTaskService.dispatch_task`'s `has_active_runs` check is a non-atomic fast path (its own session, separated from the `create()` insert by `await` points), so two concurrent dispatches — a manual `POST /scheduled-tasks/{id}/trigger` racing the poller, a double-click, or a client retry — can both pass it; the index is the atomic arbiter, and the losing `create` surfaces as `ActiveScheduledRunConflict` (translated from `IntegrityError` in the repository) and collapses to the same outcome as the fast path (manual → 409 conflict, scheduled → a `"skipped"` tombstone). The scheduled-skip tombstone is created directly as terminal `"skipped"` (not a transient `"queued"`) so it never occupies the active slot the pre-existing run still holds. Sibling of the `runs` table's `uq_runs_thread_active` (PR #4003), which keys on `thread_id` and so does not cover the default `fresh_thread_per_run` context where every dispatch gets a new thread. Index is status-only, not `overlap_policy`-conditional (the policy is fixed to `"skip"` in the MVP).

**Project Structure**:

```
deer-flow/
├── Makefile                    # Root commands (check, install, dev, stop)
├── config.yaml                 # Main application configuration
├── extensions_config.json      # MCP servers and skills configuration
├── backend/                    # Backend application (this directory)
│   ├── Makefile               # Backend-only commands (dev, gateway, lint)
│   ├── langgraph.json         # LangGraph Studio graph configuration
│   ├── packages/
│   │   ├── extension-api/     # public, host-independent extension contracts (import: deerflow_extension_api.*)
│   │   └── harness/           # deerflow-harness package (import: deerflow.*)
│   │       ├── pyproject.toml
│   │       └── deerflow/
│   │           ├── agents/            # LangGraph agent system
│   │           │   ├── lead_agent/    # Main agent (factory + system prompt)
│   │           │   ├── middlewares/   # middleware components (see Middleware Chain section)
│   │           │   ├── memory/        # Memory extraction, queue, prompts
│   │           │   └── thread_state.py # ThreadState schema
│   │           ├── sandbox/           # Sandbox execution system
│   │           │   ├── local/         # Local filesystem provider
│   │           │   ├── sandbox.py     # Abstract Sandbox interface
│   │           │   ├── tools.py       # bash, ls, read/write/str_replace
│   │           │   └── middleware.py  # Sandbox lifecycle management
│   │           ├── subagents/         # Subagent delegation system
│   │           │   ├── builtins/      # general-purpose, bash agents
│   │           │   ├── executor.py    # Background execution engine
│   │           │   └── registry.py    # Agent registry
│   │           ├── tools/builtins/    # Built-in tools (present_files, ask_clarification, view_image, review_skill_package)
│   │           ├── mcp/               # MCP integration (tools, cache, client)
│   │           ├── integrations/      # Managed first-party integration installers (e.g. Lark CLI skill pack)
│   │           ├── extensions/        # Python plugin loader, registry, placement, and isolation
│   │           ├── models/            # Model factory with thinking/vision support
│   │           ├── skills/            # Skills discovery, loading, parsing
│   │           ├── config/            # Configuration system (app, model, sandbox, tool, etc.)
│   │           ├── community/         # Community tools (search/fetch/scrape, image search, AIO sandbox)
│   │           ├── reflection/        # Dynamic module loading (resolve_variable, resolve_class)
│   │           ├── utils/             # Utilities (network, readability)
│   │           └── client.py          # Embedded Python client (DeerFlowClient)
│   ├── app/                   # Application layer (import: app.*)
│   │   ├── gateway/           # FastAPI Gateway API
│   │   │   ├── app.py         # FastAPI application
│   │   │   └── routers/       # FastAPI route modules (models, mcp, memory, skills, uploads, threads, artifacts, agents, suggestions, channels)
│   │   └── channels/          # IM platform integrations
│   ├── tests/                 # Test suite
│   └── docs/                  # Documentation
├── frontend/                   # Next.js frontend application
└── skills/                     # Agent skills directory
    ├── public/                # Public skills (committed)
    └── custom/                # Custom skills (gitignored)
```

## Important Development Guidelines

### Conversational DBTL discovery contracts

`deerflow.dbtl.discovery` owns the dependency-free pre-cycle vocabulary:
discovery lifecycle, provenance, deterministic readiness, and the
revision-bound start-card payload. A discovery draft is not a cycle and the
type structurally carries no cycle, stage, or gate identity. Persistence,
Supervisor routing, read-only execution policy, and server-bound cycle creation
must build on this contract without adding repository authority to the pure
module. Focused characterization coverage lives in
`tests/test_dbtl_discovery_contract.py`.

Migration `0031_dbtl_conversational_discovery` adds one CAS-revisioned active
discovery per project thread. `DbtlDiscoveryRepository` owns begin, turn, and
lifecycle transitions. With `dbtl.conversational_discovery=true` and
`mode=graph_enabled`, explicit new-cycle requests route to the Supervisor's
terminal `discovery` branch; classifier entry remains off. The branch invokes
the Lead Agent once with request-only `dbtl_discovery_context`.
`DbtlDiscoveryPolicyMiddleware` is the execution fence: one closed
read/inspection allowlist covers model schemas and the response-format package
tool, and it independently rejects forged
mutation, shell, connector-write, memory-write, and delegation calls. The
ordinary path is unchanged when the flag is off.

Phase 2 keeps start authority in `DbtlDiscoveryRepository`: a ready package is
content-hashed and paired with a `dbtl_discovery_outbox` start-card event while
the row remains `ready`; only a reply resolving against that server-emitted
card may atomically create the cycle, its five initial stage rows, provenance
event, creation receipt, and Design-kickoff outbox item. Retries reuse the same
discovery/card identities and cycle. The Supervisor publishes a deterministic
receipt plus the existing Design preflight immediately after the transaction.
Before confirmation, discovery presentation follows ordinary chat: the
structured package carries the Lead's bounded `assistant_response`, native tool
messages remain first, that prose is reconciled as a normal assistant message,
and the following card contains only the no-cycle notice and bound choices.
`Keep discussing` invokes the ordinary Lead under `dbtl_discovery_context` and
the read-only policy instead of publishing a deterministic acknowledgement.
The accepted package and hash live in the cycle projection, and
`LiveStageAdapter` adds the same bounded `discovery_package` block to the
shared Design stage context before any seat-specific prompt is built. Do not
reconstruct that package in the frontend or in individual workers.

Phase 3 context composition lives in `deerflow.dbtl.discovery_context` and is
enabled only by `dbtl.discovery_project_history`. It reuses the stage layer's
metadata-only project manifest, then reads prior project threads through the
server-injected event/thread stores with the authenticated `user_id` on every
query. The current thread is excluded; entry, thread, message, and character
budgets are constants pinned by tests. `ProjectContextMiddleware` renders the
pack as an escaped, hidden `HumanMessage`, not a `SystemMessage`, so content
from files/history cannot raise its own instruction authority. Source refs and
deterministic explicit key/value conflicts are copied into the versioned draft
and immutable package. Runtime store objects use private, server-overwritten
top-level run-config keys only while the graph factory is built; the worker
removes them before graph execution. They must never enter ToolRuntime context,
client context, callbacks, or checkpoints.

Phase 4 is separately gated by `dbtl.discovery_global_memory`. Discovery reads
the private project, human-approved shared-project, and opt-in user-global
DeerMem buckets with independent caps and a combined item/character budget.
Governed publications do not trust the memory projection as lifecycle
authority: `active_publications_for_project` joins active publication and
active claim SQL rows on every turn, so retraction/supersession immediately
removes retrieval. Memory/published values stay escaped hidden HumanMessage
data, carry provenance/revision refs, remain `accepted=false`, and are labeled
potentially stale. Explicit `key: value` conflicts are computed across prior
threads and all memory authorities together. A failed bucket is logged and
omitted without erasing the project manifest/history pack.

Phase 5 keeps suggested entry separate from explicit discovery.
`dbtl.discovery_classifier_entry` lets only the final classifier rung enter the
discovery branch; a durable `declined` discovery suppresses later classifier
entry in that project conversation, but cannot override an explicit start.
`dbtl.discovery_auto_offer` governs cards for classifier-entered ready drafts;
explicit discovery and an explicit request to review the proposal can still
show the server card. `DbtlDiscoveryRepository.get_latest` is the routing and
suppression authority. The authenticated discovery-status endpoint exposes
only active state for the composer, and the admin drawer reads bounded
lifecycle outcomes without gaining cycle mutation authority.

Phase 6 removes the browser from the remaining pre-cycle mutation boundary.
The conversational path remains primary when enabled; its release-window
rollback uses the immediate setup confirmation card, but the Supervisor now
creates that cycle through `DbtlCycleRepository` with the card request as the
deterministic idempotency boundary. The emitted Design-questions card carries
the server-created cycle id, and its answer opens `preview_council` directly;
no browser-authored hidden kickoff is required. Question drafting is fail-soft
after creation so a durable cycle always retains a visible next control. The
preflight binds the server-resolved setup request and uses the setup-card id as
its stable nonce, so a later depth answer cannot lose the owner's setup context
and a retry cannot mint a different control. The
governance readiness report exposes the discovery switches, server creation
authority, and rollback posture without treating rollout preference as a
PostgreSQL foundation check.

### Documentation Update Policy

**CRITICAL: Always update README.md and AGENTS.md after every code change**

When making code changes, you MUST update the relevant documentation:

- Update `README.md` for user-facing changes (features, setup, usage instructions)
- Update `AGENTS.md` for development changes (architecture, commands, workflows, internal systems). `CLAUDE.md` imports it via `@AGENTS.md`, so editing `AGENTS.md` updates both.
- Keep documentation synchronized with the codebase at all times
- Ensure accuracy and timeliness of all documentation

## Commands

**Root directory** (for full application):

```bash
make check      # Check system requirements
make install    # Install all dependencies (frontend + backend)
make detect-thread-boundaries  # Inventory backend executor/thread/event-loop boundaries
make dev        # Start all services (Gateway + Frontend + Nginx), with config.yaml preflight
make start      # Start production services locally
make stop       # Stop all services
```

**Backend directory** (for backend development only):

```bash
make install            # Install backend dependencies
make dev                # Run Gateway API with reload (port 8001)
make gateway            # Run Gateway API only (port 8001)
make test               # Run offline backend tests (excludes live external-API tests)
make test-live          # Explicitly run live DeerFlowClient tests with real APIs
make test-blocking-io   # Run strict Blockbuster runtime gate on tests/blocking_io/
make lint               # Lint with ruff
make format             # Format code with ruff
make migrate-rev MSG="..."  # Autogenerate a new alembic revision (see Schema Migrations section)
```

The root `detect-thread-boundaries` target statically inventories execution
boundaries under `backend/app/` and `backend/packages/harness/deerflow/`. It
prints a concise count by execution domain and writes the complete, versioned
JSON payload to `.deer-flow/thread-boundary-inventory.json`. Every finding has
a stable `boundary_kind`: `asyncio_default_executor`, `dedicated_executor`,
`anyio_worker_thread`, `direct_event_loop_blocking`, `separate_event_loop`, or
`unresolved_dynamic_boundary`.

The AST inventory covers `asyncio.to_thread`, default and explicit
`run_in_executor` submissions, imported aliases, simple same-module helper
wrappers (after pre-registering dedicated executor targets), `set_default_executor`,
`ThreadPoolExecutor` construction/submission,
additional event loops, synchronous LangChain tools, and direct
`BaseChatModel` fallback inheritance. It remains read-only and does not alter
executor routing or sizing.

To supplement the static scan with configured runtime types, run:

```bash
python scripts/detect_thread_boundaries.py \
  --runtime-config config.yaml \
  --json-output .deer-flow/thread-boundary-inventory.json
```

Runtime inspection imports configured tool objects and model classes so it can
record concrete tool names/types/modules, sync functions, async coroutines,
and `_agenerate`/`_astream` ownership. It does not invoke tools, instantiate
models, or call external services; import failures remain in the JSON as
`unresolved_dynamic_boundary` records. The detector implementation and focused
coverage live in `tests/support/detectors/thread_boundaries.py` and
`tests/test_detect_thread_boundaries.py`.

The `detect-blocking-io` target parses `app/`, `packages/harness/deerflow/`,
and `scripts/` with AST. By default it reports only blocking IO candidates that
are inside async code, reachable from async code in the same file, or reachable
from sync-only `AgentMiddleware` before/after hooks that LangGraph can execute
on the async graph path. It prints a concise summary and writes complete JSON
findings to `.deer-flow/blocking-io-findings.json` at the repository root
(both `make detect-blocking-io` from the repo root and `cd backend && make
detect-blocking-io` resolve to the same repo-root path). JSON findings include
`priority`, `location`, `blocking_call`, `event_loop_exposure`, `reason`, and
`code` for model-assisted or manual review. `priority` is a deterministic
review ordering from operation type, not proof of a bug. Bare-name same-file
calls are resolved by function name, so duplicate helper names in one file can
conservatively over-report async reachability. The call graph also resolves
multi-hop `self.`/`cls.` attribute chains (`self.store.flush()`) and local
variables or parameters traced back — within the same function only — to a
`self.`/`cls.` attribute (`store = self.store; store.flush()`); both fall back
to the same bare-method-name resolution as an unresolvable receiver, so they
share its over-report risk rather than adding a new kind. Deeper cross-function
or cross-module aliasing is out of scope and stays an unreported false
negative.

That same-function alias tracing is deliberately narrower than the symbolic
names `dotted_name()` builds for blocking-call pattern matching elsewhere in
this module: receiver/alias extraction uses a restricted extractor that only
recognizes `Name`/`Attribute` chains, so a `Call` or `Subscript` result (e.g.
`factory().flush()`, or `client = factory(); client.flush()` /
`client = clients[0]; client.flush()`) is never treated as inheriting its
base's alias-worthiness — including when the unsupported node is buried
deeper in the chain (`factory().client.flush()`, `clients[0].client.flush()`):
an unrecognized shape anywhere in the chain makes the whole receiver
unresolved, it never falls back to just the chain's trailing attribute name,
or that name alone could still collide with an unrelated traced parameter or
local alias. Reassigning a traced name to a non-traceable value (anything
other than a `self.`/`cls.` attribute or an already-traced name) kills its
alias instead of leaving it traceable, so a stale alias from an earlier
assignment cannot keep exposing an unrelated same-named method after the
variable is reassigned to something else; the assignment's right-hand side is
always analyzed against the alias state as it stood _before_ this kill-or-add
update, matching Python's own evaluate-then-bind order, so
`client = client.flush()` still resolves that call against `client`'s prior
(pre-reassignment) alias instead of the state after it's gone. `if`/`else`
branches get isolated alias state — an alias added in one branch cannot leak
into the other — and the state after the whole `if` is the union of what each
branch produced (a conservative may-alias join), so the result no longer
depends on which branch is textually `body` vs. `orelse`. This branch
isolation is deliberately scoped to `ast.If` only; `ast.Try`/`ast.Match` have
different, more complex control-flow semantics and keep the older unisolated
traversal. Finally, a function's decorators and parameter defaults are
analyzed in the _enclosing_ scope rather than the new function's own, and
parameter/return annotations get the same enclosing-scope treatment unless
the module postpones annotation evaluation (`from __future__ import
annotations`), in which case they are skipped entirely, in either scope —
those expressions run at definition time, before the function has ever been
called (or, when postponed, never run at all), so a call there is never
attributed to the function being defined (it moves to whatever scope actually
contains the `def`, e.g. the enclosing function, or disappears if that scope
is module/class level and therefore never async-reachable). PEP 695
type-parameter bounds are not visited in either scope: CPython evaluates each
one lazily, in its own hidden function, only if something like `T.__bound__`
is actually accessed, never as part of running the `def` statement itself.
A `lambda`'s body and a bare generator expression's element/filters/later
`for` clauses are excluded from traversal ONLY while walking another
function's own definition-time expressions (decorators, parameter defaults/
annotations, return annotation): there, we know structurally that the
enclosing `def` statement is executing right now, and neither a lambda body
nor a generator's element runs just because the lambda/generator object is
created — only a lambda's own parameter defaults and a generator's
outermost iterable are genuinely eager at that moment. This exclusion is
absolute and has no exceptions: even a lambda that is immediately invoked at
its own definition site (`(lambda: ...)()`), or a generator passed directly
to an eager-consuming builtin, is still excluded when it appears inside
another function's decorator/default/annotation — a narrow, intentional
limitation given how rarely a definition-time expression contains an
executed call at all, preferred over special-casing specific shapes there.

Everywhere else — module level, class bodies, and ordinary function-body
statements — a lambda body or generator expression's element is scanned
unconditionally, the same conservative, over-report-rather-than-infer stance
this file already takes for reachability elsewhere (the `ast.If` may-alias
union, the bare-name call-graph resolution). This file does not attempt to
distinguish a lambda that is invoked immediately, invoked later through a
stored variable, passed as a callback, or never called at all, nor a
generator that is consumed by an eager builtin (`list`, `sum`, `any`, etc.),
wrapped in another lazy iterator (`map`, `filter`), or never consumed —
telling these apart in the general case would mean inferring evaluation
order and consumption across arbitrary code rather than reading a fixed,
structural fact, so none of them are special-cased; all are scanned the
same way. This is intentionally informational and is not run from CI in
this round.

For a diff-scoped view of the same findings, `scripts/scan_changed_blocking_io.py`
(repo root) reports findings on the added lines of `git diff <base>...HEAD`
plus findings new versus the merge base (so a new async caller exposing an
untouched sync helper in the same file is still reported) — used by the
`blocking-io-guard` skill (`.agent/skills/blocking-io-guard/`) as the
deterministic scope step before routing each candidate to a fix and/or a
`tests/blocking_io/` runtime anchor.

Regression tests related to Docker/provisioner behavior:

- `tests/test_docker_sandbox_mode_detection.py` (mode detection from `config.yaml`)
- `tests/test_provisioner_kubeconfig.py` (kubeconfig file/directory handling)
- `tests/test_provisioner_request_threading.py` (keeps provisioner sandbox CRUD
  endpoints as sync FastAPI handlers so synchronous K8s client calls run in the
  Starlette worker pool instead of on the ASGI event loop)

Blocking-IO runtime gate (`tests/blocking_io/`):

- Wraps every item under `tests/blocking_io/` with a strict Blockbuster
  context scoped to `app.*` and `deerflow.*` (see
  `tests/support/detectors/blocking_io_runtime.py`). Any sync blocking IO
  call whose stack passes through DeerFlow business code while running on
  the asyncio event loop raises `BlockingError` and fails the test.
- Regression anchors live there: `test_skills_load.py` (locks the
  `asyncio.to_thread` offload around `LocalSkillStorage.load_skills`, fix
  for #1917); `test_sqlite_lifespan.py` (locks the offload around
  SQLite path resolution plus `ensure_sqlite_parent_dir`, fix for #1912);
  `test_jsonl_run_event_store.py` (locks `JsonlRunEventStore`'s async
  API — including idempotent singleton-event writes — offloading its file IO
  via `asyncio.to_thread`); `test_run_journal_callbacks.py` (locks
  `RunJournal.run_inline` tool callbacks to in-memory/event-loop-safe work);
  `test_integrations_router.py` (locks Lark integration install and auth
  completion route handlers offloading archive filesystem work and `lark-cli`
  subprocesses);
  `test_uploads_middleware.py` (locks `UploadsMiddleware.abefore_agent`
  offloading the uploads-directory scan off the event loop);
  `test_uploads_router.py` (locks Gateway upload/list/delete endpoints
  offloading upload directory creation, staged writes, chmod/cleanup,
  directory scans/deletes, and remote sandbox sync off the event loop);
  `test_feishu_receive_file.py` (locks Feishu attachment path preparation and
  persistence plus remote sandbox acquisition/sync off the event loop, and
  skips redundant sandbox sync when thread data is already mounted);
  `test_channel_outbound_files.py` (locks Feishu, Telegram, and WeCom outbound
  attachment open/read/hash work off the event loop);
  `test_openviking_memory_backend.py` (locks the OpenViking backend's async
  add/context/search entrypoints offloading synchronous HTTP and watermark
  filesystem IO); and
  `test_workspace_changes_recorder.py` (locks the offload around the snapshot
  text cache lifecycle — roots resolution, `mkdtemp`, and the `shutil.rmtree`
  on both the capture-failure branch and `record_workspace_changes`' `finally`).
- `test_gate_smoke.py` is a meta-test asserting the gate actually catches
  unoffloaded blocking IO and that the `@pytest.mark.allow_blocking_io`
  opt-out works.
- Coverage boundary: the gate only sees code that test execution actually
  touches. Static AST coverage is a separate concern (out of scope for
  this PR).
- CI: runs on every PR via `.github/workflows/backend-blocking-io-tests.yml`,
  hard-fail.

Boundary check (harness → app import firewall):

- `tests/test_harness_boundary.py` — ensures `packages/harness/deerflow/` never imports from `app.*`

Memory backend async boundary:
- `MemoryMiddleware.aafter_agent` calls `MemoryManager.aadd`; network-backed
  managers must override their `a*` methods to offload or use native async I/O.
- The mem0 backend requires an HTTPS `base_url` by default because requests
  carry an API token. Plain HTTP requires the explicit
  `backend_config.allow_insecure_http: true` local-development opt-in.
- Gateway memory routes offload the synchronous management contract with
  `asyncio.to_thread`, so backend file or HTTP I/O does not run on the ASGI
  event loop. Gateway startup and shutdown also resolve the manager off-loop,
  because a backend's `from_config` may perform a fail-fast connectivity check.
- A backend may set `requires_passive_writes_in_tool_mode = True` when tool-mode
  search is supported but durable writes still depend on conversation-level
  extraction. Such backends receive memory tools and retain `MemoryMiddleware`.
- Prompt recall rethrows `MemoryManagerError` only when backend config declares
  `failure_policy.read: fail_closed`; other recall errors preserve the existing
  log-and-empty-context behavior.

CI runs these regression tests for every pull request via [.github/workflows/backend-unit-tests.yml](../.github/workflows/backend-unit-tests.yml).

Agentic browser sessions are process-local. The Gateway startup safety gate rejects
`GATEWAY_WORKERS > 1` when `browser_navigate` is configured, because ordinary
uvicorn worker dispatch does not provide thread affinity for browser tools, REST
navigation, and the Live WebSocket.

Browser Live screenshots remain JPEG bytes inside the harness and the Gateway's
bounded, drop-oldest frame queue. WebSocket clients that request
`frame_format=binary` receive binary messages; control metadata remains JSON.
The legacy no-parameter protocol still base64-encodes frames into JSON at the
Gateway boundary for backward compatibility. Unknown `frame_format` values
receive a JSON error and close code 1008.

## Architecture

### Harness / App Split

The backend is split into two layers with a strict dependency direction:

- **Harness** (`packages/harness/deerflow/`): Publishable agent framework package (`deerflow-harness`). Import prefix: `deerflow.*`. Contains agent orchestration, tools, sandbox, models, MCP, skills, config — everything needed to build and run agents.
- **App** (`app/`): Unpublished application code. Import prefix: `app.*`. Contains the FastAPI Gateway API and IM channel integrations (Feishu, Slack, Telegram, DingTalk).

**Dependency rule**: App imports deerflow, but deerflow never imports app. This boundary is enforced by `tests/test_harness_boundary.py` which runs in CI.

**Import conventions**:

```python
# Harness internal
from deerflow.agents import make_lead_agent
from deerflow.models import create_chat_model

# App internal
from app.gateway.app import app
from app.channels.service import start_channel_service

# App → Harness (allowed)
from deerflow.config import get_app_config

# Harness → App (FORBIDDEN — enforced by test_harness_boundary.py)
# from app.gateway.routers.uploads import ...  # ← will fail CI
```

Package import hygiene: the `deerflow.agents` and `deerflow.subagents` package
roots expose heavyweight graph/executor entrypoints lazily. Internal modules
that only need lightweight types, config, or registries should import the
concrete submodule instead of adding eager package-root imports that pull in the
tool graph or subagent executor during state/schema imports.

### Agent System

**Lead Agent** (`packages/harness/deerflow/agents/lead_agent/agent.py`):

- Entry point: `make_lead_agent(config: RunnableConfig)` registered in `langgraph.json`
- Dynamic model selection via `create_chat_model()` with thinking/vision support
- Tools loaded via `get_available_tools()` - combines sandbox, built-in, MCP, community, and subagent tools
- System prompt generated by `apply_prompt_template()` with skills, memory, and subagent instructions

**ThreadState** (`packages/harness/deerflow/agents/thread_state.py`):

- Extends `AgentState` with: `sandbox`, `thread_data`, `title`, `artifacts`, `todos`, `uploaded_files`, `viewed_images`, `goal`, `promoted`, `delegations`, `skill_context`, `summary_text`
- Uses custom reducers: `merge_artifacts` (deduplicate), `merge_viewed_images` (merge/clear), `merge_goal` (preserve the active goal across ordinary state updates unless the goal writer replaces it), `merge_promoted` (catalog-hash-scoped deferred tool promotions), `merge_delegations` (append task delegation entries, same id latest wins, terminal status never downgraded, capped to the most recent entries), and `merge_skill_context` (dedupe active-skill references by path, keep the most recently read entries; entries store a name/path/description reference, not the SKILL.md body). `summary_text` is a LastValue channel updated by summarization and projected into model requests as durable context data instead of being stored as a `messages` item.
- Delta-mode `merge_message_writes` normalizes the current message state once,
  then folds normalized writes in order with message-ID position indexes and
  deferred tombstone compaction. It preserves public `add_messages` behavior,
  including duplicate IDs, replacement position, removal errors,
  `REMOVE_ALL_MESSAGES`, null-write errors, and missing-ID allocation order,
  without rescanning the accumulated state for every write. Keep this
  full-parity contract covered by differential tests: LangGraph's private
  `_messages_delta_reducer` is also linear, but intentionally omits some of
  those public `add_messages` semantics and cannot be substituted directly.

**Runtime Configuration** (via `config.configurable`):

- `thinking_enabled` - Enable model's extended thinking
- `model_name` - Select specific LLM model
- `is_plan_mode` - Enable TodoList middleware
- `subagent_enabled` - Enable task delegation tool
- `max_concurrent_subagents` - Per-response `task` call concurrency limit (clamped by `SubagentLimitMiddleware`)
- `max_total_subagents` - Optional per-run total delegation cap override (falls back to `subagents.max_total_per_run`, clamped to 1-50)
  Gateway and `DeerFlowClient.stream()` always provide the runtime `run_id`; custom
  graph integrations must do the same. If it is absent, enforcement deliberately
  counts the thread's full delegation ledger (fail-restrictive) and emits a warning.

### Middleware Chain

Lead-agent middlewares are assembled in strict order across three functions: the shared base in `packages/harness/deerflow/agents/middlewares/tool_error_handling_middleware.py` (`_build_runtime_middlewares`, exposed via `build_lead_runtime_middlewares`), then the lead-only middlewares appended in `packages/harness/deerflow/agents/lead_agent/agent.py` (`build_middlewares`). Items marked _(optional)_ are appended only when their config/runtime condition holds, so the live chain length varies.

**Shared runtime base** (`build_lead_runtime_middlewares`; subagents reuse most of this via `build_subagent_runtime_middlewares`):

1. **InputSanitizationMiddleware** - First, so it is the outermost `wrap_model_call` wrapper; every inner middleware (including LLM retries) sees sanitized messages. `additional_kwargs.original_user_content` is server-owned provenance: Gateway strips caller-supplied values for non-internal run requests, trusted IM calls may carry the string they captured before adding transport/file context, and the middleware replaces any non-string value before wrapping. Uploads and sanitization retain first-writer-wins only for validated strings.
2. **ToolOutputBudgetMiddleware** - Caps tool output size (per app config) before it re-enters the model context. Oversized results are externalized to `tool_output.storage_subdir` (default `.tool-results`, shared constant `TOOL_RESULTS_DIRNAME`) under the thread outputs dir with a typed synopsis + `read_file` reference left in context; those files are process feedback, so the workspace-changes scanner excludes that directory and run delivery verification never counts them as produced artifacts
3. **ToolResultSanitizationMiddleware** - Neutralizes framework/injection tags (e.g. `<system-reminder>`) and boundary markers in *remote-content* tool results (`web_fetch`/`web_search`/`image_search`/`web_capture`) so attacker-controlled fetched pages cannot forge trusted framework context. Mirrors `InputSanitizationMiddleware`'s user-input guardrail for the other untrusted-content entry point; sits inner of `ToolOutputBudgetMiddleware` (neutralizes the raw output, then the budget truncates). Local tool output (bash/read_file) is left untouched. Scope is a name-based allowlist, so MCP remote-content tools registered under other names (e.g. `fetch_url`) are not yet covered — a metadata-tagging follow-up is tracked in the middleware source
4. **ThreadDataMiddleware** - Creates per-thread directories under the user's isolation scope (`backend/.deer-flow/users/{user_id}/threads/{thread_id}/user-data/{workspace,uploads,outputs}`); resolves identity via `resolve_runtime_user_id(runtime)`, including Gateway runtime context and standalone LangGraph Server auth, then falls back to the request ContextVar / `"default"`
5. **UploadsMiddleware** - Tracks and injects newly uploaded files into conversation (lead agent only); upload existence checks use the same runtime-resolved user bucket as thread-data creation
6. **SandboxMiddleware** - Acquires sandbox, stores `sandbox_id` in state
7. **DanglingToolCallMiddleware** - Injects placeholder ToolMessages for AIMessage tool_calls that lack responses (e.g., user interruption), preserving raw provider tool-call payloads in `additional_kwargs["tool_calls"]`; malformed tool-call names and arguments are sanitized in the model-bound request so strict OpenAI-compatible providers do not reject the next request. It also drops **unusable reasoning blocks** from replayed assistant turns. Anthropic streams extended thinking as a `content_block_start` announcing `{"type": "thinking"}` with the text arriving afterwards as deltas, so a stream that ends between the two — a cancelled run, a provider error, even a 400 caused by something else in the same request — checkpoints a thinking block with no `thinking` field. Every later turn in that thread is then rejected with `thinking.thinking: Field required`, on a request unrelated to the one that caused it, and nothing in the thread can recover because the damage is durable: one interrupted stream permanently ends a conversation. The repair is deliberately surgical — a *complete* thinking block must survive byte-identical because Anthropic verifies its signature, so this is not a blanket strip — and it rewrites the **request only**, never the checkpoint, because rewriting recorded history is a much larger claim than "this provider will not accept it". A turn left with no content gains a placeholder, since an empty assistant message is also a 400 and trading one rejection for another would fix nothing
8. **LLMErrorHandlingMiddleware** - Normalizes provider/model invocation failures into recoverable assistant-facing errors before later stages run
9. **Authorization / GuardrailMiddleware** - Up to two independent pre-tool-call gates run here. When `authorization.enabled`, the `AuthorizationProvider` instance already used for Layer 1 capability filtering is wrapped by `GuardrailAuthorizationAdapter` and reused for Layer 2 execution checks. A generated `tool_search` bypasses the adapter's second provider call only when the current build has a concrete deferred setup; its catalog was already filtered by Layer 1, and an ordinary same-named tool without that deferred setup receives no exemption. When `guardrails.enabled`, the explicitly configured `GuardrailProvider` is appended after authorization and still evaluates every call, including `tool_search`. Authorization therefore runs outermost and can deny before an external guardrail call; both use the existing middleware's fail-closed, audit, sync/async, and error-`ToolMessage` behavior. See the authorization RFC and [docs/GUARDRAILS.md](docs/GUARDRAILS.md).
10. **SandboxAuditMiddleware** - Audits sandboxed shell/file operations for security logging before tool execution. Command classification is **defense-in-depth and audit, not a security boundary** — the sandbox itself is the isolation boundary. Command substitution is judged by *position*, not by the presence of `$(`: a substitution in **command position** (`$(curl url)`, `` `curl url` ``, the word after a `|`/`&&`/`;`, or any `eval`/`source` argument) executes fetched or interpreted content and is blocked, while **value position** (`x=$(curl url)`, `echo $(curl url)`, an argument, a `for` word list) only captures output and passes (#4611). `_HIGH_RISK_COMMAND_POSITION_PATTERNS` is therefore matched anchored against each split sub-command, never against the whole compound string, and `_split_compound_command(split_pipes=True)` supplies those sub-commands; rules that span a pipe (`| sh`, `base64 -d | ...`) still rely on `_classify_command`'s whole-command Pass 1. `_COMMAND_POSITION_PREFIX` extends the anchor over leading variable assignments and exec wrappers (`FOO=1 $(curl url)`, `env`/`command`/`builtin`/`exec`/`nohup`/`time`/`sudo`/`doas`), which are still command position; its assignment branch requires whitespace before the substitution, which is exactly what keeps `x=$(curl url)` in value position. Two execution contexts are deliberately **position-blind** and matched against the whole command in Pass 1, because they execute what they receive wherever they appear (including as an argument to something else, e.g. `xargs sh -c "$(curl url)"`): an `eval`/`source` argument, and an interpreter's **code-string flag** — `-c` (shells, `python`), `-e` (`perl`/`ruby`/`node`), `-p` (`perl`/`node`), `-r` (`php`) — plus the here-string (`<<<`) that reaches the same place through stdin. All three substitution spellings (`$(cmd`, `<(cmd`, `` `cmd ``) share one `_RISKY_SUBSTITUTION` opener so a rule cannot cover one spelling and miss another. An unquoted newline splits like `;`, because it separates statements the same way: leaving it joined let `echo hi\n$(curl url)` evade the anchored rules that its `;` spelling triggers. A heredoc body is data rather than statements, so `_split_compound_command` records headers (`<<EOF`, `<<-EOF`, `<<'EOF'`) and consumes their bodies verbatim at the newline that starts them — otherwise a body line beginning with `$(curl url)` would be promoted to a command position the shell never creates. Two things that look like headers must not open one, or a body that never terminates swallows every following statement: `<<<` is a here-string (both a lookahead and a lookbehind are needed, or the trailing `<<` of `<<< "text"` reads as a heredoc with delimiter `text`), and a `<<` inside `$(( ... ))` / `(( ... ))` is a bit shift, so arithmetic depth is tracked alongside the quote flags. That is a heuristic, not shell parsing: it exists only to avoid manufacturing command positions *and* to avoid destroying real ones. An unterminated body consumes the rest of the string; an unclosed `((` only disables heredoc detection, so newlines keep splitting and the failure direction stays towards seeing more command positions rather than fewer. Known, deliberate gaps: process substitution outside `eval`/`source` (`. <(curl u)`) is not detected — closing it would require real shell parsing, which is out of scope for this layer. Two-step forms (`x=$(curl u); eval "$x"`) are inherent rather than incidental: any rule that allows output capture allows the first statement, and connecting it to the later `eval` needs dataflow analysis, not pattern matching. There is currently no config gate: the middleware is appended unconditionally in `_build_runtime_middlewares`, so it applies to both the lead agent and subagents.
11. **ReadBeforeWriteMiddleware** - *(optional, if `read_before_write.enabled`, default on)* Outermost write gate (issue #3857): `read_file` stamps a content hash onto its ToolMessage; `write_file` (append/overwrite-existing) and `str_replace` are blocked unless the newest mark for that path matches the file's current hash. Sits outside ToolProgressMiddleware and ToolErrorHandlingMiddleware so a blocked write returns immediately without consuming a ToolProgress slot. Blocked results call `normalize_tool_result` directly to stamp `deerflow_tool_meta` (`recoverable_by_model=True`) before returning, keeping the result well-formed for any outer consumer. Marks live on messages, so summarization dropping the read result invalidates the gate automatically; writes never refresh marks, forcing a re-read between consecutive edits. Gate check + tool execution are serialized per (thread, path) so same-turn parallel writes cannot reuse one stale mark; on sandboxes whose `read_file` reports failures as `"Error: ..."` strings instead of raising (AIO/E2B), uninspectable targets fail open (creation proceeds, no mark stamped)
12. **ToolProgressMiddleware** - *(optional, if `tool_progress.enabled`)* State-machine-based stagnation guard (RFC #3177). Outer wrapper around ToolErrorHandlingMiddleware so its `wrap_tool_call` receives results already stamped with `deerflow_tool_meta`. Tracks per-(thread, tool) consecutive "no-new-info" calls across three error categories: (a) `recoverable_by_model=True` (no_results, not_found, permission, Jaccard-duplicate success): ACTIVE → WARNED (terminal — hint re-injected on each subsequent problem); (b) `recoverable_by_model=False, action≠stop` (rate_limited, transient): ACTIVE → WARNED → BLOCKED after `warn_escalation_count` more problems; (c) `recoverable_by_model=False, action=stop` (auth, config, internal): immediately BLOCKED on first occurrence. **Division of labor with LoopDetectionMiddleware:** ToolProgressMiddleware is a result-quality guard — fires after tool execution and blocks specific tools that stop producing new information; LoopDetectionMiddleware is a call-pattern guard — fires after the model responds and hard-stops the whole turn when the model repeatedly issues identical tool_calls. Both can inject HumanMessage hints in the same model call without conflict; neither reads the other's internal state.
13. **ToolErrorHandlingMiddleware** - Receives `AppConfig`, converts tool exceptions into error `ToolMessage`s so the run can continue instead of aborting, stamps every result with `deerflow_tool_meta` (status / error_type / recoverable_by_model / recommended_next_action / source) via `tool_result_meta.normalize_tool_result`, stamps structured metadata for task exception wrappers, and stamps skill-read metadata for downstream durable-context capture. Task tool result text is generated from the same status/result/error inputs as the structured metadata so callers do not hand-write a second protocol string.

Authorization identity plumbing is independent of whether authorization enforcement is enabled. Gateway removes client-supplied `is_internal` / `authz_attributes` / `channel_user_id`, derives `is_internal` only from the server-owned `request.state.auth_source`, and accepts `channel_user_id` only from an internally authenticated IM caller's top-level `body.context`; free-form `body.config` can never supply it. `build_principal_from_context` is the shared Principal builder for assembly-time authorization and `GuardrailAuthorizationAdapter`; it applies `default_role`, strict-boolean internal provenance, and copy-on-read `authz_attributes`. The built-in RBAC provider validates `authorization.default_role` during provider resolution so an unknown fallback role fails agent construction instead of degrading into an empty tool set. Task delegation carries `is_internal` plus copied attributes through `SubagentExecutor`, while `GuardrailMiddleware` maps the same runtime fields into `GuardrailRequest`. Phase 1B applies Layer 1 before deferred-tool assembly on the lead, native-subagent, and embedded-client paths, then passes the same provider instance into Layer 2. Framework-provided `describe_skill` and memory tools are included in Layer 1 but restored to their legacy post-`tool_search` ordering afterward. `DeerFlowClient.stream()` treats its in-process caller as trusted and accepts the same identity fields as keyword overrides; it includes the complete Principal in its agent cache key and deep-copies nested attributes so caller mutation cannot make a stale tool set look current.

Gateway route authorization uses `authz.py::resolve_route_permissions()` as the single provider integration point for both `AuthMiddleware` and decorator-only authentication. When enabled, it evaluates the six registered `threads:*` / `runs:*` permissions as `resource="route"` requests whose targets are the full `resource:action` strings. Decisions use the async provider API and are cached for the request in `AuthContext`; decorators do not call the provider again. Provider resolution or decision errors follow `authorization.fail_closed`, scoped per permission for decision errors. When authorization is disabled, the legacy complete permission set is returned without resolving a provider. Existing `owner_check` enforcement and `require_admin_user()` management gates remain independent and unchanged. Tests: `tests/test_authorization_route_permissions.py`, `tests/test_auth.py`, and `tests/test_auth_middleware.py`.

Before changing a later authorization phase, read the [authorization RFC](../docs/plans/2026-07-10-pluggable-authorization-rfc.md) and its [implementation notes](../docs/plans/2026-07-10-pluggable-authorization-implementation-notes.md). The notes are the cumulative handoff record for merged PR behavior, reviewer feedback, trust-boundary decisions, deferred scope, and required regression coverage.

**Lead-only middlewares** (`build_middlewares`, appended after the base):

14. **DynamicContextMiddleware** - Injects the current date (and optionally memory) as a `<system-reminder>` into the first HumanMessage, keeping the base system prompt fully static for prefix-cache reuse
15. **SkillActivationMiddleware** - Detects strict `/skill-name task` syntax on the latest real user message, resolves only enabled and runtime-allowed skills, injects the `SKILL.md` body as hidden current-turn context, and records a `middleware:skill_activation` audit event
16. **SkillToolPolicyMiddleware** - Applies `allowed-tools` only after real activation; passive enabled skills and a custom agent's configured skill allowlist do not clamp the lead toolset. A run-scoped slash activation is authoritative and suppresses `skill_context` as a policy source, so reading another skill cannot widen the explicit skill's tools; without slash activation, skills captured after configured `read_file` loads retain the existing union semantics. The middleware filters model-visible schemas and blocks unauthorized execution, resolving canonical paths against the live enabled/agent-allowed registry on every model call, then stores a versioned, JSON-safe, middleware-token-bound decision signed by policy source plus active paths in run context for the resulting tool calls to reuse. The next model call always refreshes it, and malformed, foreign, stale, or unmatched decisions fall back to live resolution. `tool_search` and `describe_skill` remain framework-safe discovery tools under a restrictive policy; they may reveal or promote metadata, but a deferred business tool must still be declared by the active policy before its schema or execution can survive the policy middleware. The decision's owner token is authorization-sensitive, so its reserved context key is owned by `runtime.secret_context` and included in `REDACTED_CONTEXT_KEYS` for observable and persisted context copies. Registry load failures and a non-empty active set with no authorized skill fail closed to framework-safe tools; an individual stale path is skipped only when at least one valid active skill remains. This is best-effort behavioral scoping rather than a hard security boundary: alternate loads such as `bash cat` are not captured, and bounded autonomous `skill_context` can evict old entries. `task` is not framework-exempt, so a restricted skill cannot delegate around its policy. The middleware must remain immediately after `SkillActivationMiddleware` (which publishes the slash source through `runtime.secret_context`'s public path helpers authenticated by a required token shared only within the assembled middleware chain) and immediately before `DurableContextMiddleware`; assembly and compiled-graph tests pin ordering, token sharing, schema filtering, and execution blocking.
17. **DurableContextMiddleware** - Captures `task` delegations into `ThreadState.delegations` (including in-progress dispatches and terminal result summaries) and loaded skill-file references (name/path/description, parsed in-memory - not the body) into `ThreadState.skill_context` before summarization can compact the paired tool-call/result messages, then projects durable context into each model request. Static authority rules are injected as a `SystemMessage`; untrusted field values (`summary_text`, delegation results, skill descriptions) are injected separately as a hidden `HumanMessage` data block so compressed history, delegated work, and which skills are active stay visible without being stored as `messages` or promoted to system-role instructions. `build_subagent_runtime_middlewares` also attaches this middleware immediately before subagent summarization so a compacted `summary_text` is projected ahead of a preserved assistant/tool tail instead of leaving strict providers with an assistant-first request.
18. **SummarizationMiddleware** - *(optional, if enabled)* Context reduction when approaching token limits
19. **TodoListMiddleware** - *(optional, if `is_plan_mode`)* Task tracking with the `write_todos` tool
20. **TokenUsageMiddleware** - *(optional, if `token_usage.enabled`)* Records token usage metrics; subagent usage is merged back into the dispatching AIMessage by message position
21. **TitleMiddleware** - Auto-generates the thread title after the first complete exchange and normalizes structured message content before prompting the title model. If a first-turn run is interrupted before this middleware can write a title, `runtime/runs/worker.py` keeps the run in a finalizing state, persists a local fallback title from the latest checkpoint or original run input, and then syncs it to `threads_meta.display_name`. Replacement runs admitted by `multitask_strategy="interrupt"` / `"rollback"` wait for older same-thread finalization before entering the graph; the interrupted run only skips the fallback title write once a later run has started and may have advanced the checkpoint.
22. **MemoryMiddleware** - Queues conversations for async memory update (filters to user + final AI responses); captures the runtime-resolved user so standalone LangGraph Server reads and writes stay in the same bucket
23. **ViewImageMiddleware** - *(optional, if the model supports vision)* Injects a hidden HumanMessage with base64 image data, identified by a reserved ID prefix plus a server-owned metadata marker, before the LLM call. Because `before_model`, `model`, and `after_model` are separate graph nodes, the `before_model` and `model` node checkpoints for that call still contain the payload; `after_model` / `aafter_model` then emits `RemoveMessage`, so subsequent checkpoints do not retain it
24. **McpRoutingMiddleware** - *(optional, if `tool_search.enabled` and PR1 MCP routing metadata produce a routing index)* Auto-promotes matching deferred MCP tool schemas before the model call by writing a minimal `promoted` state update. It matches only the latest real `HumanMessage`, uses the global `tool_search.auto_promote_top_k` limit (default 3, clamped to 1..5), never executes tools, and must be installed before `DeferredToolFilterMiddleware`
25. **DeferredToolFilterMiddleware** - *(optional, if `tool_search.enabled`)* Hides deferred (MCP) tool schemas from the bound model until `tool_search` or `McpRoutingMiddleware` promotes them (reads per-thread promotions from `ThreadState.promoted`, hash-scoped)
26. **SystemMessageCoalescingMiddleware** - Merges every SystemMessage into a single leading SystemMessage per request; provider-agnostic fix for strict backends (vLLM/SGLang/Qwen/Anthropic) that reject non-leading system messages. Touches the per-request payload only (checkpoint state unchanged); on midnight crossings only the latest `dynamic_context_reminder` SystemMessage survives
27. **SubagentLimitMiddleware** - *(optional, if `subagent_enabled`)* Truncates excess `task` tool calls to enforce both the per-response concurrency limit (`max_concurrent_subagents`, clamped to 1-4) and the per-run total delegation cap (`max_total_subagents` runtime override or `subagents.max_total_per_run`, default 6, clamped to 1-50). The total cap counts current-run entries in the durable delegation ledger (entries are tagged with `run_id` when captured), so repeated planning checkpoints in one run cannot keep launching legal-sized batches indefinitely, while later user turns in the same thread get a fresh run budget. If the cap is exhausted, the middleware strips remaining `task` calls, forces `finish_reason="stop"`, and appends a visible limit note so the run can synthesize existing results instead of ending with an empty tool-call response.
28. **LoopDetectionMiddleware** - *(optional, if `loop_detection.enabled`)* Detects repeated tool-call loops; hard-stop clears both structured `tool_calls` and raw provider tool-call metadata before forcing a final text answer; stamps `loop_capped` via `consume_stop_reason` (#3875 Phase 2), symmetric to `TokenBudgetMiddleware`
29. **TokenBudgetMiddleware** - *(optional, if `token_budget.enabled`)* Enforces per-run token limits
30. **Custom middlewares** - *(optional)* Any `custom_middlewares` passed to `build_middlewares` are injected here, before config-declared extensions and the terminal-response/safety/clarification tail
31. **Configured extension middlewares** - *(optional, if `extensions.middlewares` is set in `config.yaml` or `extensions_config.json`)* Zero-argument `AgentMiddleware` classes loaded from `module.path:ClassName` entries via `deerflow.reflection.resolve_class`. Missing packages, invalid classes, and broken modules fail loudly at agent creation. These run after built-ins/programmatic custom middleware and after the lead/subagent loop/token guards, but before the terminal-response/safety/clarification tail; subagents receive the same configured extension middleware class list before their safety tail. Treat these files as trusted operator config because middleware paths instantiate arbitrary code. Gateway skill/MCP toggle endpoints preserve this field through `to_file_dict()` but must not add a write path for `extensions.middlewares` without an explicit trust-boundary review. Lead-only vs subagent-only middleware lists and per-context constructor parameters are not expressible in this MVP.
32. **TerminalResponseMiddleware** - When a provider returns an empty terminal `AIMessage` after tool execution, injects a hidden recovery prompt and retries the model once; a second empty response is replaced in checkpoint state by a visible error fallback marked for the run worker, so the run finishes as an error instead of a silent success
33. **ModelLengthFinishReasonMiddleware** - Records `stop_reason=model_length_capped` when provider-specific length detectors match a terminal `AIMessage` without tool-call intent (`finish_reason=length` / `MAX_TOKENS`, or `stop_reason=max_tokens`), preserving the original assistant content and never reparsing textual tool-call-like envelopes
34. **SafetyFinishReasonMiddleware** - *(optional, if `safety_finish_reason.enabled`)* Suppresses tool execution when the provider safety-terminated the response (e.g. `finish_reason=content_filter`); registered after terminal-response/custom/configured middlewares so LangChain's reverse-order `after_model` dispatch runs it first
35. **ClarificationMiddleware** - Intercepts `ask_clarification` tool calls, writes a readable `ToolMessage.content` fallback plus structured `ToolMessage.artifact.human_input` request payload, and interrupts via `Command(goto=END)` (must be last). Payloads are versioned: legacy modes (`free_text` / `choice_with_other`) keep `version: 1` unchanged, while the v2 `form` mode (from `fields`) carries `version: 2` so older frontends reject the payload and degrade to the plain-text fallback. Field normalization is deterministic and lives in the middleware, not the tool schema — the middleware short-circuits before tool execution, so tool-arg typing alone provides no runtime validation. Validation is atomic: any structurally broken entry (non-dict, bad/duplicate name, a name colliding with a JS `Object.prototype` member like `__proto__`/`constructor`, exceeding the caps of 16 fields / 24 options per field / 200 chars per text, or the whole normalized definition exceeding `MAX_FORM_SERIALIZED_BYTES` = 16KB UTF-8 — the per-item caps alone admit forms whose IM text fallback would blow channel delivery limits and truncate away trailing fields) degrades the whole form to the legacy option/free-text modes, so a card can never render "complete" while silently missing a business field; benign issues keep local degradation (unknown types — including unhashable JSON like `type: []`, which must never raise from the membership probe — and option-less selects become `text`), and options are trimmed/deduped with blanks dropped (both form-level and top-level) because the frontend parser rejects blank option labels. Model-produced XML-to-dict option payloads are recursively flattened from dict/list containers in source order, scalar string/number leaves are retained, and residual XML tags are removed before the same trimming and deduplication. Checkbox fields are booleans that default to an explicit "no"; `required` on a checkbox means must-agree/consent semantics. The response protocol is deliberately unchanged (v1 `text`/`option` only): form cards submit a readable text summary as `response_kind: "text"`, so journal persistence and answered-card recovery need no new allowlist entries. Because this middleware can short-circuit tool execution before LangChain emits `on_tool_end`, `RunJournal` performs a root-run final reconciliation for allowlisted clarification `ToolMessage`s whose `tool_call_id` was produced by the current run, so human-input request cards remain recoverable from `run_events` after checkpoint compaction. Human Input Card replies are submitted as `hide_from_ui` `HumanMessage`s with `additional_kwargs.human_input_response`; `RunJournal` persists only allowlisted hidden response sources (currently `ask_clarification`) as `llm.human.input`, which preserves answered-card state after compaction without exposing generic internal hidden context.

### Python Extension System (Middleware Slice)

Third-party Python packages can expose an `install(registry, config)` function and be
loaded, in deterministic order, from the startup-only top-level `plugins:` list in
`config.yaml`. Keep this list out of `extensions_config.json`: the latter is writable
through Gateway APIs, while importing Python entry points is an operator-controlled code
execution boundary. A plugin marked `required: true` fails Gateway construction when it
cannot load; optional plugins fail open with attributed diagnostics.

The public package is `packages/extension-api/` and must never import `deerflow`. In this
slice its registry contract exposes middleware contribution only. Each contribution
declares lead/subagent scope, stable order, and a semantic placement (`MODEL_LOGICAL`,
`MODEL_PHYSICAL`, `TOOL_VISIBLE`, `TOOL_RAW`, or `STANDARD`) rather than a fragile list
index. `extensions/stack.py` is the single final composition point; do not inject inside
the shared base builder because the lead builder appends more middleware afterward.
`extensions/ordering.py` owns host ordering invariants and validates the final composed
stack. Nothing under `extensions/` may import `agents.middlewares` at module scope: the
middleware layer calls into this one, so a module-scope reference points the dependency
backwards and closes a cycle as soon as any middleware imports something under
`extensions/` at module level. Both tables that need middleware classes therefore resolve
on first use — `ordering.py::core_ordering_constraints()` and `stack.py::_anchors()` —
which is `assert_ordering` / composition time, already inside the middleware builder.
Defer by deferring the *call*; do not fake a resolved value with a lazy container
subclass, which reports one answer when iterated and another when measured.

Contributed middlewares are wrapped by `IsolatedMiddleware`: extension failures emit
diagnostics and fail open without repeating a downstream model/tool side effect. The
wrapper mirrors lifecycle hooks, tools, transformers, and state schema implemented by
the inner middleware. LangChain treats each sync/async model or tool wrapper pair as one
capability, so a single-sided wrapper receives a pass-through counterpart; implement
both sides when the extension must observe both synchronous and asynchronous execution
paths. Lead runs and
subagents allocate an `ExtensionData` task store only when middleware contributors are
present and expose it through `EXTENSION_TASK_STORE_KEY`; extensions retrieve it with
`task_store_from_runtime()`. Each run resolves the immutable loaded-extension snapshot
once and binds that same object through task-store allocation and synchronous agent
construction, so a concurrent singleton replacement cannot mix two extension
generations without changing the LangGraph graph-factory ABI. The graph-build binding is
a ContextVar scoped to synchronous construction, so it has already exited by the time the
lead agent delegates; the run worker therefore also publishes the snapshot on runtime
context under the host-internal `EXTENSION_SNAPSHOT_CONTEXT_KEY`, `task_tool` reads it
back through `resolve_run_extensions()` (type-checked — runtime context is
caller-mergeable), and `SubagentExecutor` binds it at construction. That key is written
after the caller merge and popped when the run has none, so a caller-supplied value is
never authoritative. Absent the key — embedded `DeerFlowClient`, standalone LangGraph
Server — the executor keeps its `get_loaded_extensions()` fallback.

Gateway `create_app()` loads plugins once, stores the immutable registry on `app.state`
and in the process-wide singleton, and installs one canonical live diagnostics list.
Changing `plugins` requires a restart. Later extension contribution points must be added
to the public contract and host runtime in the same slice; never accept a registration
method that the current host silently ignores.

### Configuration System

**Main Configuration** (`config.yaml`):

Setup: Copy `config.example.yaml` to `config.yaml` in the **project root** directory.

**Config Versioning**: `config.example.yaml` has a `config_version` field. On startup, `AppConfig.from_file()` compares user version vs example version and emits a warning if outdated. Missing `config_version` = version 0. Run `make config-upgrade` to auto-merge missing fields. When changing the config schema, bump `config_version` in `config.example.yaml`.

**Config Caching**: `get_app_config()` caches the parsed config, but automatically reloads it when the resolved config path or file content signature changes. The signature includes file metadata and a content digest, so Gateway and LangGraph reads stay aligned with `config.yaml` edits even on object-store or network mounts where mtime can remain stale.

**Config Hot-Reload Boundary**: Gateway dependencies route through `get_app_config()` on every request, so per-run fields like `models[*].max_tokens`, `summarization.*`, `title.*`, `memory.*`, `subagents.*`, `tools[*]`, and the agent system prompt pick up `config.yaml` edits on the next message. `AppConfig` is intentionally **not** cached on `app.state` — `lifespan()` keeps a local `startup_config` variable for one-shot bootstrap work and passes it to `langgraph_runtime(app, startup_config)`.

Infrastructure fields are **restart-required**. The authoritative list lives in `packages/harness/deerflow/config/reload_boundary.py::STARTUP_ONLY_FIELDS` and is mirrored by the standardised `"startup-only:"` prefix on the corresponding `Field(description=...)` in `AppConfig`, so IDE hover on those fields surfaces the reason inline (no need to context-switch into this table). Currently registered: `plugins`, `database`, `checkpointer`, `run_events`, `stream_bridge`, `sandbox`, `log_level`, `logging`, `channels`, `channel_connections`, `scheduler`, `run_ownership`. Adding a new restart-required field requires updating the registry; drift is pinned by `tests/test_reload_boundary.py`.

**Persistence backend resolution**: the unified `database` section selects the
Gateway's LangGraph checkpointer, LangGraph Store, and DeerFlow SQL repositories.
The deprecated `checkpointer` section remains backward compatible and, when
present, overrides `database` for the LangGraph checkpointer and Store only;
application repositories continue to use `database`.

Configuration priority:

1. Explicit `config_path` argument
2. `DEER_FLOW_CONFIG_PATH` environment variable
3. `config.yaml` in current directory (backend/)
4. `config.yaml` in parent directory (project root - **recommended location**)

Config values starting with `$` are resolved as environment variables (e.g., `$OPENAI_API_KEY`).
`ModelConfig` also declares `use_responses_api` and `output_version` so OpenAI `/v1/responses` can be enabled explicitly while still using `langchain_openai:ChatOpenAI`.

**Extensions Configuration** (`extensions_config.json`):

MCP servers and skills are configured together in `extensions_config.json` in project root:

Docker development mounts the project directory at `/app/project` and points
`DEER_FLOW_CONFIG_PATH` / `DEER_FLOW_EXTENSIONS_CONFIG_PATH` into that directory.
Keep mutable config files behind a directory bind mount: single-file bind mounts
can become stale or inaccessible when a host editor replaces a file on save.

Configuration priority:

1. Explicit `config_path` argument
2. `DEER_FLOW_EXTENSIONS_CONFIG_PATH` environment variable
3. `extensions_config.json` in current directory (backend/)
4. `extensions_config.json` in parent directory (project root - **recommended location**)

Extensions are optional only in the fallback _search_ mode (priority 3-4 above): `ExtensionsConfig.resolve_config_path()` returns `None` when neither an explicit `config_path` nor `DEER_FLOW_EXTENSIONS_CONFIG_PATH` is given and the search locations find nothing. An explicit `config_path` argument or a set `DEER_FLOW_EXTENSIONS_CONFIG_PATH` (priority 1-2) is an operator assertion that one particular file must be used, so a missing file in either of those modes raises `FileNotFoundError` instead — including when the file existed earlier and has since been deleted. The MCP tools cache's staleness check (`deerflow.mcp.cache._resolve_config_path`) is a narrow, deliberate exception to that rule: it catches that `FileNotFoundError` locally and treats it as "unconfigured" so a previously-valid config disappearing mid-run degrades the cache to serving its last-known-good tools instead of raising out of a per-request hot path (see the MCP System section below).

### Gateway API (`app/gateway/`)

FastAPI application on port 8001 with health check at `GET /health`. Set `GATEWAY_ENABLE_DOCS=false` to disable `/docs`, `/redoc`, and `/openapi.json` in production (default: enabled).

CORS is same-origin by default when requests enter through nginx on port 2026. Split-origin or port-forwarded browser clients must opt in with `GATEWAY_CORS_ORIGINS` (comma-separated exact origins); Gateway `CORSMiddleware` and `CSRFMiddleware` both read that variable so browser CORS and auth-origin checks stay aligned. Those clients also need `CORS_EXPOSED_HEADERS` (`csrf_middleware.py`): run-creating routes return the run's id in `Content-Location`, which is not CORS-safelisted, so JS cannot read it unless it is exposed. The LangGraph SDK resolves run metadata from that header alone — withhold it and `useStream`'s `onCreated` never fires, a new thread keeps its placeholder route, and every action gated on an established thread (edit, regenerate, branch) stays hidden until the page is reloaded. Same-origin nginx deployments never hit this because CORS does not apply.

Browser auth sessions are owned by `app.gateway.auth.session_cookie`. Login accepts a `remember_me` form flag, but the Gateway never stores passwords. `SessionCookiePolicy` persists the `HttpOnly access_token` cookie only for HTTPS/trusted-forwarded HTTPS, direct-host localhost HTTP, or explicit operator opt-in for insecure persistence; public HTTP sandbox URLs degrade to session cookies. Session-creating handlers stamp the final `max_age` on `request.state`, and CSRF cookie creation mirrors that value so the double-submit cookie pair expires together, including explicit re-issue after password changes and OIDC callbacks. If a browser nevertheless retains the access cookie while evicting its JS-readable CSRF partner, an authenticated `GET /api/v1/auth/me` restores only the missing CSRF cookie using the preserved session preference and current deployment policy; it never rotates an existing token because concurrent session checks must not invalidate an in-flight mutation header. A small `HttpOnly` preference cookie preserves the user's remember choice across token re-issue paths. Logout clears all auth cookies and suppresses CSRF re-issue on the logout response.

Localhost persistence deliberately reads the direct request `Host` and ignores `Forwarded` / `X-Forwarded-Host`. Scheme and auth-origin reconstruction still consume forwarding headers. The bundled nginx sets `X-Forwarded-Proto`, but preserves an upstream HTTPS value and does not overwrite every forwarded header, so the outer trusted proxy must replace or strip client-supplied forwarding headers before traffic reaches DeerFlow.

**Routers**:

| Router                                                 | Endpoints                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| ------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Models** (`/api/models`)                             | `GET /` - list models; `GET /{name}` - model details                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| **Features** (`/api/features`)                         | `GET /` - report config-gated feature availability (`agents_api.enabled`, `browser_control.enabled`) for frontend UI gating                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| **Files** (`/api/threads/{id}/files`)                  | `GET /` - list one directory of the thread's sandbox user-data tree (`?path=/mnt/user-data/...`, defaults to the root). Read-only listing for the Web UI file browser: reuses the artifacts router's virtual-path resolution + thread-ownership checks, hides upload staging files, sorts directories first, caps at 500 entries (`truncated` flag); a missing user-data root renders as an empty listing (thread dirs are materialized lazily). Also owns the **per-thread project link** (`GET/PUT/DELETE /api/threads/{id}/project`, `GET /api/threads/{id}/project/candidates`): a thread may link exactly ONE project folder under an operator-configured `sandbox.mounts` entry (candidates = mount roots + first-level subdirs). The link is a JSON marker at `{thread_dir}/project_link.json` (`app/gateway/thread_project.py`; host dir re-derived from live mount config on every read, so removing a mount deactivates stale links). The root listing appends only the linked project — never all mounts — and `app/gateway/path_utils.py::resolve_thread_virtual_path` resolves mount-prefixed paths only inside the linked project (per-link traversal containment), which also scopes artifact preview of mounted files. File content is served by the existing artifacts endpoint                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| **Console** (`/api/console`)                           | Read-only cross-thread observability for the current user (the data layer for an operations dashboard or external monitoring): `GET /stats` - headline counters (runs/threads/agents/tokens/cost); `GET /runs` - paginated run history joined with thread titles (per-run cost); `GET /usage` - zero-filled daily token series + per-model breakdown with spend. Queries `runs`/`threads_meta` directly as a reporting layer (no new `RunStore` methods); requires a SQL database backend — returns 503 on `database.backend: memory`. Real-cost estimation reads optional `models[*].pricing` (`currency`, `input_per_million`, `output_per_million`, `input_cache_hit_per_million`; `ModelConfig` is `extra="allow"`, so no schema change) and prices each run from its `token_usage_by_model` input/output split. Pricing is **cache-aware**: `RunJournal` accumulates prompt-cache hits from `usage_metadata.input_token_details.cache_read` into a sparse `cache_read_tokens` bucket key (also threaded through `SubagentTokenCollector` → `record_external_llm_usage_records`), and cache-hit input tokens are billed at `input_cache_hit_per_million` (omitted → billed at the miss price, a conservative upper bound). Legacy rows fall back to run-level totals at `model_name`; unpriced models yield `cost: null` and cost fields are null when no pricing is configured                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| **MCP** (`/api/mcp`)                                   | `GET /config` - get config; `PUT /config` - update config (saves to extensions_config.json)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| **Skills** (`/api/skills`)                             | `GET /` - list skills; `GET /{name}` - details; `PUT /{name}` - update enabled; `POST /install` - install from .skill archive (accepts standard optional frontmatter like `version`, `author`, `compatibility`); `POST /reload` - admin-only process-local prompt-cache invalidation after trusted external filesystem changes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| **Memory** (`/api/memory`)                             | `GET /` - memory data; `POST /reload` - force reload; `GET /config` - config; `GET /status` - config + data                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| **Uploads** (`/api/threads/{id}/uploads`)              | `POST /` - upload files (auto-converts PDF/PPT/Excel/Word); `GET /list` - list; `DELETE /{filename}` - delete                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| **Threads** (`/api/threads/{id}`)                      | `DELETE /` - remove DeerFlow-managed local thread data after LangGraph thread deletion; `POST /branches` - create a new main-thread branch from a completed assistant turn checkpoint and, when an addressable pre-user replay checkpoint exists, materialize it into the branch namespace so the inherited response remains regeneratable. Workspace files are not checkpointed, so the branch only best-effort copies the current workspace when branching from the **latest** turn (`workspace_clone_mode="current_thread_best_effort"`); branching from an older/historical turn skips the copy (`workspace_clone_mode="skipped_historical_turn"`) so the branch never inherits files that only exist in a later timeline. Thread-scoped runtime channels (`sandbox`, `thread_data`) are not copied onto the branch: the parent's `sandbox_id` binds path mappings and the release lifecycle to the parent's workspace, so the branch lazily acquires its own sandbox instead. Branch creation also seeds the new thread's run-event feed from the branch checkpoint's visible messages (`history_seed_mode` in the response): the thread feed reads run_events, not checkpoints, so without the seed the inherited history disappears from the UI after the branch's first run (#4380). Seeded rows are grouped into one synthetic run per inherited turn (`branch-seed-{thread_id}-{n}`, a new turn opening at every persisted human message, including an allowlisted hidden `ask_clarification` reply) because `run_id` is a turn identity to the feed's consumers, not a provenance tag: regenerating an inherited answer supersedes that row's whole `run_id` in `GET /messages/page`, so one shared id for the entire seed deleted the complete inherited history on a branch's first regenerate (#4458); `GET /goal`, `PUT /goal`, `DELETE /goal` - read, set, and clear the active thread goal; `POST /compact` - manually summarize older active context into `summary_text` and retain the recent message window, blocked while a run is in flight; unexpected failures are logged server-side and return a generic 500 detail |
| **Artifacts** (`/api/threads/{id}/artifacts`)          | `GET /{path}` - serve artifacts; active content types (`text/html`, `application/xhtml+xml`, `image/svg+xml`) are always forced as download attachments to reduce XSS risk; `?download=true` still forces download for other file types                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| **Suggestions** (`/api/suggestions`)                   | `GET /config` - returns global suggestions config boolean; `POST /threads/{id}/suggestions` - generate follow-up questions; rich list/block model content is normalized and inline reasoning (`<think>...</think>`, including unclosed/truncated blocks from reasoning models like MiniMax-M3) is stripped before JSON parsing                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| **Input Polish** (`/api/input-polish`)                 | `POST /` - rewrite a composer draft before it is sent. This is a short authenticated `runs:create` LLM request using `input_polish` config; it does not create a LangGraph run, persist a message, or modify thread state. Shares the non-graph one-shot LLM path (`deerflow.utils.oneshot_llm.run_oneshot_llm`) with the suggestions route so model build + Langfuse metadata + invoke stay in one place; validates the same stripped view of the draft it sends to the model, and preserves literal `<think>` substrings in the rewrite (`strip_think_blocks(truncate_unclosed=False)`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| **Thread Runs** (`/api/threads/{id}/runs`)             | `POST /` - create background run; `POST /stream` - create + SSE stream; `POST /wait` - create + block; `POST /regenerate/prepare` - prepare clean input + checkpoint metadata for regenerating the latest assistant answer; `GET /` - list runs; `GET /{rid}` - run details; `POST /{rid}/cancel` - cancel; `GET /{rid}/join` - join SSE; `GET /{rid}/messages` - paginated per-run messages `{data, has_more}`; `GET /{rid}/events` - full event stream; `GET /{rid}/workspace-changes` - workspace/output file change summary and optional diffs; `GET /../messages` - legacy thread message array; `GET /../messages/page` - backward thread-global `seq` history page with middleware/subagent-AI/successful-regenerate filtering and page-run-scoped feedback enrichment; subagent AI callbacks remain available through run events while parent `task` ToolMessages stay visible for card restoration; `GET /../token-usage` - aggregate tokens                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| **Feedback** (`/api/threads/{id}/runs/{rid}/feedback`) | `PUT /` - upsert feedback; `DELETE /` - delete user feedback; `POST /` - create feedback; `GET /` - list feedback; `GET /stats` - aggregate stats; `DELETE /{fid}` - delete specific                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| **Runs** (`/api/runs`)                                 | `POST /stream` - stateless run + SSE; `POST /wait` - stateless run + block; `GET /{rid}/messages` - paginated messages by run_id `{data, has_more}` (cursor: `after_seq`/`before_seq`); `GET /{rid}/feedback` - list feedback by run_id                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| **GitHub Webhooks** (`/api/webhooks/github`)           | `POST /` - receive GitHub App / repo webhook deliveries. Verifies `X-Hub-Signature-256` against `GITHUB_WEBHOOK_SECRET`; exempt from auth + CSRF because authenticity is enforced by HMAC. The route is fail-closed: mounted only when `GITHUB_WEBHOOK_SECRET` is set, or when explicit dev opt-in `DEER_FLOW_ALLOW_UNVERIFIED_GITHUB_WEBHOOKS=1` is set. Recognized events include `ping`, `issues`, `issue_comment`, `pull_request`, `pull_request_review`, and `pull_request_review_comment`; unknown events return 200 with `handled=false`. Fan-out runtime failures return 503, keeping the delivery recorded as failed for manual/API/scripted redelivery (GitHub does not automatically retry any failed delivery, 5xx included); permanent/non-retryable conditions such as `channels.github.enabled: false`, unknown events, malformed payloads, or unavailable channel service return 200 with a skipped/handled response.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| **GitHub Event-Driven Agents**                         | Custom agents can declare a `github:` block in their `config.yaml` to bind to repos and event triggers. Webhook fan-out publishes one `InboundMessage` per matching binding to the channel bus; `GitHubChannel` routes those messages through `ChannelManager`. The response `dispatch` summarizes matched/fired/skipped agents.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |

| Router | Endpoints |
|--------|-----------|
| **Models** (`/api/models`) | `GET /` - list models; `GET /{name}` - model details |
| **Features** (`/api/features`) | `GET /` - report config-gated feature availability (`agents_api.enabled`, `browser_control.enabled`) for frontend UI gating |
| **Console** (`/api/console`) | Read-only cross-thread observability for the current user (the data layer for an operations dashboard or external monitoring): `GET /stats` - headline counters (runs/threads/agents/tokens/cost); `GET /runs` - paginated run history joined with thread titles (per-run cost); `GET /usage` - zero-filled daily token series + per-model breakdown with spend. Queries `runs`/`threads_meta` directly as a reporting layer (no new `RunStore` methods); requires a SQL database backend — returns 503 on `database.backend: memory`. Real-cost estimation reads optional `models[*].pricing` (`currency`, `input_per_million`, `output_per_million`, `input_cache_hit_per_million`; `ModelConfig` is `extra="allow"`, so no schema change) and prices each run from its `token_usage_by_model` input/output split. Pricing is **cache-aware**: `RunJournal` accumulates prompt-cache hits from `usage_metadata.input_token_details.cache_read` into a sparse `cache_read_tokens` bucket key (also threaded through `SubagentTokenCollector` → `record_external_llm_usage_records`), and cache-hit input tokens are billed at `input_cache_hit_per_million` (omitted → billed at the miss price, a conservative upper bound). All priced models must use one currency; mixed currencies disable cost reporting and leave cost/currency fields null instead of producing invalid aggregates. Legacy rows fall back to run-level totals at `model_name`; unpriced models yield `cost: null` and cost fields are null when no pricing is configured |
| **MCP** (`/api/mcp`) | `GET /config` - get config; `PUT /config` - replace the full config with whole-payload stdio validation; `PATCH /config` - toggle one server while preserving the raw extensions config and validating only an enabled target; both writes reload config and reset the process-local MCP cache |
| **Skills** (`/api/skills`) | `GET /` - list skills; `GET /{name}` - details; `PUT /{name}` - update enabled; `POST /install` - install from .skill archive (accepts standard optional frontmatter like `version`, `author`, `compatibility`); `POST /reload` - admin-only process-local prompt-cache invalidation after trusted external filesystem changes |
| **Integrations** (`/api/integrations`) | `GET /lark/status` - inspect managed Lark/Feishu CLI integration state, including `sandbox_runtime_mode` / `sandbox_runtime_ready` (whether `lark-cli` will actually be present in the sandbox at chat time); `POST /lark/install` - admin-only install of the official `lark-*` managed skill pack; `POST /lark/config/start` and `/lark/config/complete` - internal first-time Lark connection setup; `POST /lark/auth/start` and `/lark/auth/complete` - browser device-flow user authorization without terminal access, with optional `domains` / exact `scope` for incremental permission grants |
| **Memory** (`/api/memory`) | `GET /` - memory data; `POST /reload` - force reload; `GET /config` - config; `GET /status` - config + data |
| **Uploads** (`/api/threads/{id}/uploads`) | `POST /` - upload files (auto-converts PDF/PPT/Excel/Word); `GET /list` - list; `DELETE /{filename}` - delete |
| **Threads** (`/api/threads/{id}`) | `DELETE /` - remove DeerFlow-managed local thread data after LangGraph thread deletion; `POST /branches` - create a new main-thread branch from a completed assistant turn checkpoint and, when an addressable pre-user replay checkpoint exists, materialize it into the branch namespace so the inherited response remains regeneratable. Workspace files are not checkpointed, so the branch only best-effort copies the current workspace when branching from the **latest** turn (`workspace_clone_mode="current_thread_best_effort"`); branching from an older/historical turn skips the copy (`workspace_clone_mode="skipped_historical_turn"`) so the branch never inherits files that only exist in a later timeline. Thread-scoped runtime channels (`sandbox`, `thread_data`) are not copied onto the branch: the parent's `sandbox_id` binds path mappings and the release lifecycle to the parent's workspace, so the branch lazily acquires its own sandbox instead. Branch creation also seeds the new thread's run-event feed from the branch checkpoint's visible messages (`history_seed_mode` in the response): the thread feed reads run_events, not checkpoints, so without the seed the inherited history disappears from the UI after the branch's first run (#4380). Seeded rows are grouped into one synthetic run per inherited turn (`branch-seed-{thread_id}-{n}`, a new turn opening at every persisted human message, including an allowlisted hidden `ask_clarification` reply) because `run_id` is a turn identity to the feed's consumers, not a provenance tag: regenerating an inherited answer supersedes that row's whole `run_id` in `GET /messages/page`, so one shared id for the entire seed deleted the complete inherited history on a branch's first regenerate (#4458); `GET /goal`, `PUT /goal`, `DELETE /goal` - read, set, and clear the active thread goal; `POST /compact` - manually summarize older active context into `summary_text` and retain the recent message window, blocked while a run is in flight; unexpected failures are logged server-side and return a generic 500 detail |
| **Artifacts** (`/api/threads/{id}/artifacts`) | `GET /{path}` - stream regular text and binary artifacts with `FileResponse`, including byte-`Range` 206/416 behavior used by bounded text previews and media seeking; active content types (`text/html`, `application/xhtml+xml`, `image/svg+xml`) are always forced as download attachments to reduce XSS risk; `?download=true` still forces download for other file types. `PUT /{path}` atomically replaces an existing UTF-8 text file under `/mnt/user-data/outputs` when its expected SHA-256 still matches; active runs conflict, and non-mounted sandbox providers receive the same update explicitly. Atomic replacement applies the existing POSIX permission handling when descriptor-based APIs are available and otherwise keeps the platform-native temporary-file permissions (Windows). |
| **Suggestions** (`/api/suggestions`) | `GET /config` - returns global suggestions config boolean; `POST /threads/{id}/suggestions` - generate follow-up questions; rich list/block model content is normalized and inline reasoning (`<think>...</think>`, including unclosed/truncated blocks from reasoning models like MiniMax-M3) is stripped before JSON parsing |
| **Input Polish** (`/api/input-polish`) | `POST /` - rewrite a composer draft before it is sent. This is a short authenticated `runs:create` LLM request using `input_polish` config; it does not create a LangGraph run, persist a message, or modify thread state. Shares the non-graph one-shot LLM path (`deerflow.utils.oneshot_llm.run_oneshot_llm`) with the suggestions route so model build + Langfuse metadata + invoke stay in one place; validates the same stripped view of the draft it sends to the model, and preserves literal `<think>` substrings in the rewrite (`strip_think_blocks(truncate_unclosed=False)`) |
| **Thread Runs** (`/api/threads/{id}/runs`) | `POST /` - create background run; `POST /stream` - create + SSE stream; `POST /wait` - create + block. Before the first journaled run, an empty run-event message feed is seeded from an existing checkpoint head so legacy checkpoint-only history receives earlier thread-global sequence numbers and remains visible after the new run; a thread with no checkpoint or an already-populated feed skips this compatibility path. `POST /regenerate/prepare` - prepare clean input + checkpoint metadata for regenerating the latest completed or interrupted assistant answer, carrying the latest non-empty thread title in graph input so resuming an older checkpoint cannot roll back a later manual rename (#4457); `POST /edit-regenerate/prepare` - prepare a checkpoint replay from the latest editable human turn with a replacement user message and edit replay metadata; it carries the current thread title the same way, but only when the replay base already has one — an untitled base belongs to a thread the title middleware has not named yet, so pinning the current title there would keep a name generated from the prompt the edit just replaced; `GET /` - list runs; `GET /{rid}` - run details; `POST /{rid}/cancel` - cancel; `GET /{rid}/join` - join SSE; `GET /{rid}/messages` - paginated per-run messages `{data, has_more}`; `GET /{rid}/events` - full event stream; `GET /{rid}/workspace-changes` - workspace/output file change summary and optional diffs; `GET /../messages` - legacy thread message array; `GET /../messages/page` - backward thread-global `seq` history page with middleware/subagent-AI/successful-regenerate/edit-replay filtering and page-run-scoped feedback enrichment; subagent AI callbacks remain available through run events while parent `task` ToolMessages stay visible for card restoration; `GET /../token-usage`  - aggregate tokens plus an optional `context_usage` percentage. Context usage approximately counts messages from the latest materialized thread state through `build_thread_checkpoint_state_accessor`, so full and delta checkpoint modes expose the same input. The percentage uses the latest run's model and its configured `context_window`.  |
| **Feedback** (`/api/threads/{id}/runs/{rid}/feedback`) | `PUT /` - upsert feedback; `DELETE /` - delete user feedback; `POST /` - create feedback; `GET /` - list feedback; `GET /stats` - aggregate stats; `DELETE /{fid}` - delete specific |
| **Runs** (`/api/runs`) | `POST /stream` - stateless run + SSE; `POST /wait` - stateless run + block; `GET /{rid}/messages` - paginated messages by run_id `{data, has_more}` (cursor: `after_seq`/`before_seq`); `GET /{rid}/feedback` - list feedback by run_id |
| **GitHub Webhooks** (`/api/webhooks/github`) | `POST /` - receive GitHub App / repo webhook deliveries. Verifies `X-Hub-Signature-256` against `GITHUB_WEBHOOK_SECRET`; exempt from auth + CSRF because authenticity is enforced by HMAC. The route is fail-closed: mounted only when `GITHUB_WEBHOOK_SECRET` is set, or when explicit dev opt-in `DEER_FLOW_ALLOW_UNVERIFIED_GITHUB_WEBHOOKS=1` is set. Recognized events include `ping`, `issues`, `issue_comment`, `pull_request`, `pull_request_review`, and `pull_request_review_comment`; unknown events return 200 with `handled=false`. Fan-out runtime failures return 503, keeping the delivery recorded as failed for manual/API/scripted redelivery (GitHub does not automatically retry any failed delivery, 5xx included); permanent/non-retryable conditions such as `channels.github.enabled: false`, unknown events, malformed payloads, or unavailable channel service return 200 with a skipped/handled response. |
| **GitHub Event-Driven Agents** | Custom agents can declare a `github:` block in their `config.yaml` to bind to repos and event triggers. Webhook fan-out publishes one `InboundMessage` per matching binding to the channel bus; `GitHubChannel` routes those messages through `ChannelManager`. The response `dispatch` summarizes matched/fired/skipped agents. |

Thread identifiers use the shared `deerflow.utils.thread_id` contract
`^[A-Za-z0-9_-]{1,64}$`. Caller-provided opaque IDs remain supported; UUIDs
are generated only for `None`, while explicit empty strings fail validation.
Gateway creation and state-producing request boundaries, embedded-client
entry points, filesystem/upload/event-store consumers, scheduled launches,
and the standalone Provisioner enforce the same contract before persistence
or workspace initialization. Route-addressable legacy IDs remain accepted by
pure reads and cleanup/control endpoints. Deleting a noncanonical legacy ID
best-effort removes its metadata and checkpoints but deliberately skips local
filesystem cleanup, so the raw value is never interpolated into a host path;
new runs, workspace/sandbox operations, and other state-producing mutations
remain blocked.

**Workspace change review**: `packages/harness/deerflow/workspace_changes/`
captures a pre-run and post-run snapshot of the thread-owned `workspace` and
`outputs` directories. `runtime/runs/worker.py` performs the filesystem scan via
`asyncio.to_thread` and writes a `workspace_changes` event with category
`workspace` when changes exist. Uploads are intentionally excluded. Text diffs
are size-limited; binary, large, and sensitive-looking paths are persisted as
metadata only. Internal process-feedback directories never count as changes:
the scanner's `EXCLUDED_DIR_NAMES` drops `BROWSER_FRAMES_DIRNAME` (transient
browser screenshots) and `TOOL_RESULTS_DIRNAME` (the tool-output budget
middleware's default externalization subdir, `constants.py` is the shared
source of truth for both writers and the scanner), and the worker threads the
configured `tool_output.storage_subdir` through the snapshot capture as an
extra excluded dir name so custom storage locations stay excluded too.

**Run delivery receipts**: `RunJournal` records each non-empty artifact update
once per tool `Command` for the terminal `run.delivery` event. When a command
contains multiple messages, a unique tool name resolved from matching
`ToolMessage` entries supplies attribution; additional command messages do not
duplicate artifact paths or counts. If multiple different tool names resolve
for one flat artifact update, the paths remain counted but unattributed because
the command does not carry a per-path mapping. `RunJournal` callbacks set
`run_inline=True`: they do only in-memory bookkeeping or schedule async writes,
and staying on the run's event-loop thread serializes parallel tool callbacks
before terminal delivery recording and flushing. Each worker creates a separate
journal per run before cancellable/fallible preflight work, so checkpoint
compatibility failures and cancellation while waiting for prior finalization
still emit a zero-delivery receipt. The worker flushes ordinary journal events,
idempotently persists the run-scoped receipt, and only then persists the staged
terminal run status. A receipt failure is retried on a short bounded schedule
while the owning worker still knows the real outcome and holds the lease. The
worker derives delivery requirements from the run's workspace snapshots rather
than a client request option: every regular file created or modified under
`/mnt/user-data/outputs` is a candidate produced artifact. Internal
process-feedback files are not candidates: the snapshot capture excludes the
scanner's `EXCLUDED_DIR_NAMES` (including the default tool-output
externalization subdir) plus the configured `tool_output.storage_subdir`, so a
run that only externalized oversized tool outputs does not fail delivery. At
least one candidate must be covered by a path attributed by the journal to
`present_files`; presenting only an unrelated pre-existing path does not
satisfy delivery.
Receipts for such runs add `produced_paths`, `presented_paths`, `matched_paths`,
`verification`, `stage`, and `satisfied` to the Slice 1 fact fields. Missing a
matching presentation becomes a run error; a successful presentation is also
downgraded to error if its receipt cannot be durably verified. Runs without
changed outputs preserve ordinary chat behavior and the original receipt shape.
Orphan recovery first
atomically claims an expired lease, then uses the same singleton write to
backfill a zero-delivery receipt. This ordering prevents a stale recovery scan
from overwriting a live run's later detailed receipt; an event-store outage
does not undo the terminal takeover. An existing detailed receipt is preserved
when a worker crashed after writing it. Event stores
serialize `put_if_absent` with ordinary thread writers: memory and JSONL provide
the documented single-process guarantee, while the DB store adds per-thread
in-process locks and PostgreSQL advisory locks for cross-process writers.
Moving journal construction ahead of preflight is receipt-only on early failure
paths: a separate boundary flag preserves the previous completion-data
semantics, so checkpoint incompatibility or cancellation while waiting for an
older finalizing run does not persist an empty completion snapshot. Worker tests
pin one accumulated receipt across multiple goal-continuation `_stream_once`
calls; journal tests drive LangChain's real async callback dispatcher against a
single journal to pin serialized, deduplicated parallel tool callbacks.
Multi-worker deployments therefore require `run_events.backend: db` for shared,
ordered delivery events; the startup gate rejects process-local memory and
JSONL event stores when `GATEWAY_WORKERS > 1`.

**RunManager / RunStore contract**:
- LangGraph-compatible run requests validate their supported subset before creating a run. `runtime/stream_modes.py` is the shared backend contract for public stream modes and the worker's `graph.astream` mapping; the public `messages-tuple` mode maps to LangGraph's internal `messages` mode, while public `messages`, `events`, and other unsupported modes are rejected instead of being dropped or replaced with `values`. `app/gateway/run_models.py::RunCreateRequest` is shared by HTTP and internal scheduled launch paths, retains only truthful compatibility defaults for unimplemented options (`if_not_exists="create"` plus `None` placeholders), returns 422 for unsupported values including `on_completion="complete"`, `on_completion="continue"`, and `multitask_strategy="enqueue"`, and forbids undeclared SDK options so fields such as `checkpoint_during` and `durability` cannot be silently discarded. A placeholder must still accept the stock SDK's own default: `langgraph_sdk` drops only `None` from its run payload, so `stream_resumable=False` reaches every request and means "non-resumable", which is what DeerFlow serves — rejecting it 422'd every IM channel run (#4466). `tests/test_run_request_validation.py::test_gateway_accepts_langgraph_sdk_default_payload` pins the real SDK payload against this boundary; channel tests mock the SDK client and cannot catch this class of drift.
- `RunManager.get()` is async; direct callers must `await` it.
- The history batch helpers `list_successful_regenerate_sources()`, `list_edit_regenerate_runs()`, and `get_many_by_thread()` default to `user_id=AUTO`: they resolve the request user and fail closed when no user context exists. Migration/admin callers that intentionally need an unscoped read must pass `user_id=None` explicitly.
- Edit-and-rerun visibility is derived from edit replay runs (`metadata.replay_kind="edit"` plus `regenerate_from_run_id`) by `RunManager.list_edit_replay_visibility()`: the newest attempt for each source run is authoritative. Pending/running/success attempts hide the original source run; failed, timed-out, or interrupted attempts hide only the failed attempt so the original conversation reappears.
- When a persistent `RunStore` is configured, `get()` and `list_by_thread()` hydrate historical runs from the store. In-memory records win for the same `run_id` so task, abort, and stream-control state stays attached to active local runs.
- Thread metadata status switches to `running` only after `RunManager.try_start()` succeeds. Pending-cancelled runs therefore skip the old `running` projection, while clients may observe the prior thread status during the short worker-startup window.
- `cancel()` returns a :class:`~deerflow.runtime.CancelOutcome` enum: `cancelled` (local cancel), `requested` (the non-owning worker durably recorded the first cancellation action for the live owner), `taken_over` (non-owning worker claimed the run because the owner's lease expired — marks it as `error`), `lease_valid_elsewhere` (legacy/custom store lacks the durable request primitive — caller retains the safe 409 + `Retry-After` fallback), `not_active_locally` (heartbeat disabled, preserving the old 409 path), `not_cancellable` (terminal state), or `unknown` (not found in memory or store). `create_or_reject(..., multitask_strategy="interrupt"|"rollback")` persists interrupted status through `RunStore.update_status()`, matching normal `set_status()` transitions.
- Interrupt/rollback admission registers the replacement before its best-effort persistence of locally interrupted predecessors. If the admitting caller is cancelled during that post-registration await, `RunManager` drains a shielded replacement cleanup before propagating `CancelledError`, including across repeated cancellation. The cleanup normally persists `interrupted`; if that best-effort transition fails, it retries the active-to-`interrupted` store transition strictly and verifies the result with the replacement's captured owner identity. A concurrent peer terminal transition wins and is synchronized back into the local record rather than being overwritten or deleted.
- Store-only hydrated runs are readable history. In multi-worker mode with heartbeat enabled, cancel on a store-only run records `runs.cancel_action` / `cancel_requested_at` while the owner's lease is live; the first action wins even if a retry later lands on the owner. `RunStore.request_cancel()` and owner completion through `finalize_if_not_cancelled()` are competing active-row CAS operations, so an accepted cancel cannot be overwritten by a later success. `RunStore.renew_lease()` renews and observes the request atomically in the SQL implementation. The owner then executes the normal process-local interrupt/rollback and terminal stream path without transferring the lease. An expired owner is still taken over and marked `error`. `wait=true` and cancel-then-stream use the shared bridge to observe owner finalization; a non-standard process-local bridge returns accepted 202 instead of subscribing to an unreachable stream. In single-worker mode (heartbeat off), store-only runs still return 409.
- A local worker's `RunRecord.lease_expires_at` is the last durably confirmed ownership deadline. `_renew_leases()` bounds each renewal attempt by that deadline: transient store exceptions remain retryable while it is valid, but an exception or blocked call that reaches expiry sets the process-local `ownership_lost` fence, raises `abort_event`, and cancels the run task. Successful renewals collect durable cancellation actions; after all local renewals have been attempted, heartbeat only signals the corresponding process-local tasks, leaving status writes and rollback cleanup to the worker finalization path. Fenced workers do not perform subsequent journal/delivery-receipt, progress/completion/status, checkpoint/thread-metadata, or `on_run_completed` writes; the peer recovery path owns the terminal receipt. `RunStore.update_run_completion()` also refuses to replace a different terminal status, closing the peer-takeover/late-finalization race. `grace_seconds` delays peer reclamation for clock skew but is not extra execution time for an owner that can no longer confirm its lease. Already-committed remote tool side effects remain outside this local cancellation boundary.
- Startup/orphan reconciliation must claim stale active rows with `RunStore.claim_for_takeover()`, not a plain `update_status()`. The final claim re-checks `status` and lease expiry atomically, so a heartbeat renewal between the candidate scan and the recovery write keeps the run active.
- Run admission and independent writes are first-class thread operations. `runs.operation_kind` distinguishes user-visible `run` rows from internal `checkpoint_write` and `artifact_write` reservations, while every active kind shares the existing durable active-thread uniqueness constraint. New operation kinds must go through `RunStore.create_thread_operation_atomic()` and `RunManager.reserve_thread_operation()` rather than adding another lock or metadata marker. Live and lease-less reservations are non-interruptible; an expired leased reservation can be reclaimed immediately by interrupt/rollback admission without waiting for orphan reconciliation. Lease-less rows stay fail-closed because the store cannot distinguish a stale row from a live writer in another heartbeat-disabled worker; a rare failed delete therefore requires startup reconciliation, and heartbeat-disabled multi-worker deployment remains unsupported. Reservation bodies are attached to their caller task so loss detected by lease renewal cancels the writer before it can continue after takeover; the context manager translates that lease-loss cancellation to `ConflictError` after cleanup so Gateway mutation routes return a retryable 409 instead of dropping the HTTP request. The cleanup scope begins immediately after durable admission, including the await that attaches the caller task, so cancellation cannot strand a locally renewed pending reservation. A failed renewal is revalidated under the manager lock before cancellation; if the reservation completed and unregistered while the store update was in flight, its request task must not be cancelled after the write. Reservations are excluded from run history/reporting and from run-only helpers such as `list_by_thread()` and `has_inflight()`, release uses the captured owner rather than ambient user context, and local cleanup still runs when the best-effort store delete fails. `RunStore.create_run_atomic()` remains a deprecated compatibility shim for external stores that only admit normal runs; new stores must implement `create_thread_operation_atomic()` to support internal operation kinds.
- Gateway checkpoint mutations outside run execution must use `services.reserve_checkpoint_write()`, which composes the process-local thread lock with the durable `checkpoint_write` reservation. Manual compaction, `POST /threads/{id}/state`, and both goal mutation routes (`PUT` / `DELETE /threads/{id}/goal`, including creation of a missing goal checkpoint) use this boundary, so an existing run blocks the write and the reservation blocks new reject/interrupt/rollback runs across workers.
- `POST /wait` (both thread-scoped and `/api/runs/wait`) drains the stream bridge via `wait_for_run_completion()` instead of bare `await record.task`, so it honours the run's `on_disconnect` setting and cancels the background run on real client disconnect rather than returning a stale checkpoint (issue #3265).
- Memory and Redis `StreamBridge` implementations retain only `stream_bridge.queue_maxsize` data events. A syntactically valid `Last-Event-ID` older than the retained watermark, or a live subscriber that falls behind it, yields `StreamGap` before any partial replay. `sse_consumer` maps that control item to an id-less SSE `gap` payload (`stream_replay_gap`) and intentionally leaves the run active; internal `/wait` consumers resume from its latest retained ID because they only need terminal completion. Redis checks bounds plus the non-blocking read in one transaction, using blocking `XREAD` only as a wake-up before repeating the atomic snapshot. For a no-cursor subscriber that established a wait on an empty stream, the first wake response remains provisional until that next snapshot verifies its tail is still retained; this closes the pre-first-delivery trimming window without changing malformed-cursor live tailing. The correctness tradeoff is one three-command snapshot pipeline per poll plus the blocking wake round trip while idle. Malformed cursor behavior remains backend-specific. Memory treats a syntactically numeric cursor below its watermark conservatively as a gap even when the evicted timestamp can no longer be verified; unknown ids at or above the watermark retain the legacy replay-from-earliest policy.
- Redis `StreamBridge` keys use a rolling retained-buffer TTL (`stream_bridge.stream_ttl_seconds`, refreshed on `publish()` / `publish_end()`) as a leak safety net, not as a run timeout. Startup and lease-driven periodic orphan recovery share one Gateway stream-terminalization path: after `RunManager` durably marks a run `error` with `stop_reason=orphan_recovered`, Gateway publishes `END_SENTINEL` and schedules stream cleanup. The periodic store scan, per-row status writes, and Gateway callback run as one supervised single-flight task, so a slow pass is skipped at the next interval instead of piling up or pausing the sole lease-renewal loop. Store retries have bounded attempts/backoff; an individual operation still relies on the database driver/pool timeout. `RunManager.shutdown()` gives active user runs priority within its shared deadline, then drains or cancels orphan recovery. Gateway tracks delayed recovered-stream cleanups and converts unfinished delays to immediate deletes before closing the bridge; the Redis TTL remains the outage safety net. Only startup recovery, before the runtime yields to requests, projects the latest affected thread to `error`; periodic recovery deliberately avoids that non-atomic projection because `ThreadMetaStore` has no `latest_run_id` conditional-update contract. Store-only SSE and `/wait` consumers wait for the bridge's real END marker after an ordinary durable terminal status, because status persistence can precede tail events. The explicit `orphan_recovered` signal is the only heartbeat fallback: its publisher is known to be gone, so it supplies the liveness boundary if END publication fails or the retained key expires. Malformed `Last-Event-ID` reconnect values live-tail new Redis events rather than replaying the retained buffer. Keep cross-component recovery orchestration in Gateway through the generic `RunManager.on_orphans_recovered` callback; do not introduce a harness-to-app dependency. Callback failure warnings include every recovered `run_id` so operators can identify rows whose Gateway-side terminalization needs inspection.
- Thread-scoped run creation accepts `checkpoint` / `checkpoint_id`; Gateway validates the checkpoint belongs to the request thread before writing `checkpoint_id` / `checkpoint_ns` into `config.configurable` for LangGraph branching. In `delta` checkpoint mode the worker rewrites that fork into a linear head write before the graph starts (see "A delta-mode run cannot fork" under Checkpoint Channel Modes), because delta state for a fork replays the abandoned sibling's writes.
- Thread-scoped Gateway runs evaluate an active `ThreadState.goal` after the visible turn completes. `runtime/goal.py` asks a non-thinking evaluator model to judge only visible conversation evidence and return a typed blocker; the evaluator model is created once per run and reused across hidden continuation checks. The evaluator runs after the graph root's tracing scope has already closed, so `create_goal_evaluator_model`/`evaluate_goal_completion` attach their own model-level tracing callbacks (`attach_tracing=True`) and inject Langfuse trace metadata (`thread_id`/`user_id`/`deerflow_trace_id`) directly onto the `ainvoke` call — the same standalone-caller pattern as `oneshot_llm.run_oneshot_llm` and `MemoryUpdater` (see Tracing System below). Satisfied goals are cleared; every non-satisfied evaluation — continuable or stand-down — is persisted with `last_evaluation` (the blocker, reason, and evidence summary; outcomes that stop the loop additionally record a `stand_down_reason` for observability), but only `goal_not_met_yet` evaluations are streamed as hidden `HumanMessage` continuations, and only when a durable assistant end-of-turn checkpoint exists, the run has not been aborted, the thread did not change during evaluation, and the no-progress breaker has not fired. The continuation cap is 8 — a hard maximum in the `0`–`8` range; callers requesting more are clamped (`set_goal`/TUI) or rejected with 422 (`PUT /goal`). The no-progress breaker keys on the latest visible assistant evidence (not the evaluator's free-text reason, which an LLM rewords every turn), so two consecutive continuations that add no new visible assistant output stop the loop after 2 attempts. Model-response cleanup helpers such as think-block stripping and code-fence stripping live in `deerflow.utils.llm_text` so `runtime/goal.py` and Gateway suggestion parsing share the same JSON-prep behavior.
- Run event stream changes must keep producer code, `deerflow/constants.py`, `runtime/events/catalog.py`, `contracts/run_event_stream_contract.json`, `backend/docs/RUN_EVENT_STREAM.md`, and `tests/test_run_event_stream_contract.py` in sync. The dependency-free constants module owns the persisted envelope limits (`event_type` 32 characters, `category` 16) and cross-layer workspace event identity; the catalog owns validated runtime definitions and categories. Dynamic middleware tags are limited to 21 characters after the `middleware:` prefix. The JSON contract owns payload schemas, backend-specific storage semantics, legacy aliases, and compatibility rules; conformance tests require both views and all producer groups to agree. `run.end.content` remains opaque and may retain nested Python values in memory while JSONL/database stores stringify non-JSON nested values, so consumers must not assume backend-identical nested output representations.

### Runtime agent activity (`runtime/activity/`)

**A presence-and-lineage projection, not a transcript.** `runtime/activity/`
answers who is working now, what they are honestly doing, and which runtime
actor authorized them — so an apparently-idle governed run is legible without
opening a debug console. Live stream name `agent_activity`, persisted catalog
name `runtime.agent.activity`, dedicated category `activity` (the thread feed
filters by category, so sharing `message` or `trace` would put every routing
transition inside a conversation).

**Safety is a property of the constructor, not a rule to remember.**
`build_activity_event` accepts a closed field set: `actor_kind`, `state`, and
`transition` are enums, `operation` is a key into the server-owned
`OPERATION_LABELS` table, and the six identifier fields are shape-constrained
(`[A-Za-z0-9._:@-]`, ≤128) rather than merely length-bounded — 128 characters is
ample room for a truncated credential or a host path to ride in a field nobody
thinks of as a text field. `display_name` is the one field that legitimately
carries human words and is length-bounded only. There is therefore no channel
through which a prompt, chain-of-thought, tool argument, shell output, or secret
could reach a screen. **No visible string is an internal identifier**: the
supervisor renders as **Cycle supervisor** and the adapter as **`<Stage>`
stage**, the same protocol-keeps-its-identifiers split that `council_*` uses
under "meeting".

**That guarantee holds only for payloads the constructor built, so anything
crossing back in is rebuilt rather than copied.** `parse_activity_event` is the
inbound boundary: it re-validates every field against the same closed vocabulary
and returns a fresh dict containing exactly `ACTIVITY_EVENT_FIELDS`, dropping
anything else. `activity_run_event` persists *that*, not the chunk it was handed
— storing the inbound mapping would move the closed-field-set guarantee to
whoever emitted the frame, so a field attached by any future emitter edit would
be persisted and served to clients. It returns `None` rather than raising,
because a boundary that raises turns a malformed frame into a failed run. The
JSON contract states the same rule with `additionalProperties: false` on both
the content and its `scope`. `parse_activity_event` also matches `operation`
against the *rendered* label set (`OPERATION_VALUES`): the wire carries the
label, not the key it resolved from.

**Two rules govern `activity_span` and they pull against each other.** *A span
always closes* — a row left open is a spinner that never stops, which is the
failure this exists to remove — so the opening emit sits **inside** the
try/finally, not before it: `aemit_custom_event` hands the payload to the writer
synchronously and then awaits a best-effort dispatch, and a lease loss landing on
that await raises `BaseException`, which the emitter's own `except Exception`
does not catch. And *a span never raises*: instrumentation that can end a run is
worse than none, so a missing writer, a rejected envelope, or a bridge outage
costs the row and nothing else. Cancellation settles `cancelled`, not `failed` —
work taken away and work gone wrong are different things to a reader. Those two
rules together are why `ActivityHandle.update`/`settle` validate **before**
touching the handle's last-known-good state: storing first and letting `_emit`
reject the value cost two events rather than one, because the rejected value
stuck to the handle and the span's own closing emit was then built from it and
rejected too. One bad update left a row open forever, through the method meant
to report progress.

**An agent cannot use a span, so `AgentActivityMiddleware` owes the close.** A
run begins in one graph node and ends in another, so the lead agent's row is
opened by `make_activity_handle` + `handle.open()` in `abefore_agent` and closed
in `aafter_agent`, with `awrap_model_call`/`awrap_tool_call` reporting Thinking,
Computing, and — for `task` alone — Waiting. Delegation is the only tool the
vocabulary distinguishes; a per-tool vocabulary would leak tool names into a
projection whose whole safety argument is that it carries none. Handles are keyed
by `run_id` rather than held on the instance, because the middleware is built
once per agent and the agent is cached, so an instance attribute would let one
run close another's row.

**"Only the async hooks exist" has to be spelled, not inherited.** LangGraph
decides a node's synchronous half by asking whether the subclass *overrode* the
hook — not whether the attribute is callable, because `AgentMiddleware` always
supplies one. An async-only middleware therefore raises *"No synchronous
function provided to `abefore_agent`"* the moment a sync graph invocation
reaches it, and this middleware is appended to the lead chain unconditionally.
The embedded `DeerFlowClient.stream()`, the TUI, and the CLI all invoke
synchronously, so the observability feature took down the entire embedded path —
`tests/test_client_e2e.py` failed eleven ways with a `TypeError` naming a hook
nobody had called. The sync hooks are now overridden explicitly: `before_agent`
and `after_agent` no-op (emitting nothing is the documented fallback), while
`wrap_model_call` and `wrap_tool_call` **must still call their handler** —
a wrapper that returns `None` drops the model call itself. Observing nothing is
the cost of a synchronous run; swallowing the run is not. A handle held across hooks also resolves the stream
writer **per emit** rather than capturing one: a writer belongs to the node that
asked for it, and a captured one is bound to a task that has already finished by
the time `aafter_agent` runs. Only the async hooks exist — every surface this
feeds is async, and a synchronous embedded caller emits nothing rather than
something half-wired.

**Closing from a hook is best-effort, so the run worker settles what is left.**
`aafter_agent` does not run on cancellation, on lease loss, or when the graph
raises past it, and nothing afterwards would ever close those rows — a reload
would show work that finished weeks ago as still in progress.
`ActivityEventBuffer.close_open_activities`, called in the worker's `finally`
before the final flush, settles every row it saw opened and not closed as
`interrupted`: not `completed`, which would claim an outcome nobody observed, and
not `failed`, which would blame the work for the run being taken away.

`runtime/activity/reducer.py` is the consumer half, written once so the durable
projection, a reconnecting client, and the rail cannot each invent their own
answer. Three rules: **replay is not new information** (folding a sequence twice,
or a prefix then the whole thing, gives the same rows — a reconnect re-delivers
and a backfill overlaps the live tail by design); **a settled row is closed for
good** (later frames, including another `started`, are refused); and **a row must
be opened before it can be updated** (an `updated` for an unknown id is dropped,
because a late subscriber's synthesised row would misreport lineage the reader
never saw). `active_leaves` answers "who is working now" with the innermost row —
a supervisor that has dispatched a stage is waiting, and naming it describes the
tree rather than the work. A reducer may also receive the persisted run-event
wrapper (`seq` + `content`); it records the last server sequence per row and
refuses an older or duplicate wrapper even when pages and live replay arrive out
of order.

**Lineage crosses a graph-node boundary by derivation, not by carrying.** A
ContextVar carries the current activity down a call stack and across the isolated
subagent loop (`_copy_isolated_subagent_context` copies ambient ContextVars while
stripping `deerflow_loop_bound` handlers), but the routing edge and the branch
node it selects are separate scheduled units. `lineage.supervisor_activity_id`
derives the same id on both sides from the run id, so the branch node names its
dispatcher without anything having been handed to it. The ordinary branch uses
`activity_parent_context` rather than a second span: the Lead middleware owns the
single Lead row, while the context-only boundary parents it to the already
reported supervisor row without emitting a duplicate actor. The activity id is
never written to `configurable` — that section is checkpointed, and a lineage
accepted from there would keep asserting itself after the work it describes had
ended.

**`run_id` is the key every row hangs from, so the worker overwrites it rather
than defaulting it.** `_install_runtime_context` writes `context["run_id"]`
unconditionally: by that point the dict carries the client's own `body.context`,
so a `setdefault` let a request name its own run. That is what makes
`spans.run_id_from_config` safe to read from the request context — the overwrite,
not an assumption about client behaviour. Outside a graph that context is
top-level; before invoking a node LangGraph relocates it to
`configurable["context"]` and also exposes the server-owned copy through
`configurable["__pregel_runtime"].context`, so the accessor must understand all
three shapes. A unit test that passes a convenient top-level dict is not enough;
the supervisor stream contract pins the real node shape. The exposure was never activity-only:
the per-run delegation cap counts current-run ledger entries by `run_id`, so a
supplied value also reset that budget on every request.

**One row per `execute`, not one per dispatch round, and a terminal row is never
reopened.** `LiveStageAdapter.execute` is a thin wrapper holding an
`AsyncExitStack`; `_execute_stage` enters `_stage_activity` from inside, once
`_executable_stage` has resolved which stage this is (a row opened in the wrapper
would have to name a stage nobody had worked out yet), and the stack closes it
however the call ends — including an exception, which settles it `failed` rather
than leaving it spinning. `_execute_review_meeting` opens its own the same way,
from the stage the server registered its deck against. That single row moves
through Preparing → Dispatching → Coordinating → Recording, so repository reads,
roster planning, waiting on workers, evidence recording, and an early failure are
all visible as one actor being present. A row per dispatch round made a Design
meeting's coordinator appear to finish and restart between waves (positions, red
team, chair), and a shared deterministic id across those rounds would have
emitted `started → completed → started` for one row outright.

`ActivityEventBuffer` persists through `put_batch` for the same reason
`_SubagentEventBuffer` does — `put` is a documented low-frequency path taking a
per-thread advisory lock — and is fed **before** `_publish_stream_item`'s
namespace early-return: that function returns early for namespaced frames, so a
client requesting subgraph streaming would otherwise make the durable projection
disappear. Persistence must not depend on what a client asked to stream.

Tests: `tests/test_agent_activity_vocabulary.py`,
`tests/test_agent_activity_emitter.py`,
`tests/test_agent_activity_persistence.py`,
`tests/test_agent_activity_reducer.py`,
`tests/test_agent_activity_lead_middleware.py`, plus the activity producer case
in `tests/test_run_event_stream_contract.py`.

**Delegation lineage rides on the events that already exist.** `task_started`,
`task_running`, and the terminal task events gain optional `activity_id`,
`parent_activity_id`, and `dispatcher_activity_id` keys, omitted rather than
sent as null so an older consumer sees exactly the payload it saw before. They
are needed because a `task_started` proves only that a worker started: whether
it came from the lead agent or from a stage adapter is what a reader wants, and
the frontend would otherwise have to infer it from display names, card types, or
`dbtl_stage`, none of which are authoritative. `task_tool` opens a `subagent`
row parented to `lead_activity_id(run_id)` — derived, because the delegation
runs in the tools node several graph nodes from the hook that opened the lead's
row — and `LiveStageAdapter._dispatch_units` opens one `stage_worker` row per
work unit, parented to the adapter's own row through the ContextVar that
`asyncio.gather` copies into each worker task. Meeting seats keep their
validated role label; ordinary stage work is numbered (`Build worker 2`).
A delegated subagent is named from its **registry** name (`subagent_label`),
shape-checked first because the model chooses which registered agent to invoke.

**The rail is behind `run_events.agent_activity_visibility`** (default off), and
`/api/features` publishes it beside `durable`, which is false on the in-memory
run-event backend — the rail renders that as **Status unavailable** rather than
as an idle agent, a claim it has no rows to support. `GET
/api/threads/{id}/activity` is the cursor-paginated conversation read: it is
thread-scoped rather than run-scoped because a conversation's activity spans
every run in it, including hidden deck-triggered runs no browser subscribed to.
That needed a genuinely new store method (`list_thread_events`) on the base plus
all three implementations — a `db`-only signature raises `TypeError` on the
other two at runtime, not at import.

**Retention is decided, and the decision is "no policy yet, and the UI says
so".** `run_events` has no TTL and no pruning, so activity rows accumulate
exactly like every other run event. Rather than ship an expiry boundary that can
never fire, `run_events.activity_page_limit` bounds what a *reader* is served
and nothing deletes rows: a projection that quietly discarded audit-adjacent
history would be a larger claim than a visibility feature should make. The
frontend has no "no longer retained" boundary for the same reason. Adding a real
policy means adding it for run events generally, not for this event type alone.

Still open (plan:
[docs/plans/2026-08-01-runtime-agent-activity-visibility-plan.md](../docs/plans/2026-08-01-runtime-agent-activity-visibility-plan.md)):
the mobile entry point (the rail does not render below `md`, so there is no host
for the sheet), and removing the static placeholder once rollout evidence is
clean — the placeholder still renders when the flag is off.

Proxied through nginx: `/api/langgraph/*` → Gateway LangGraph-compatible runtime, all other `/api/*` → Gateway REST APIs.

**Branch/regenerate checkpoint invariant**: `app/gateway/checkpoint_lineage.py`
walks `parent_config` rather than globally ordered checkpoint history so replay
anchors stay on the selected lineage after regenerations create sibling branches.
New conversation branches persist the pre-user replay anchor before their visible
head through the state mutation graph, which preserves materialized state in both
full and delta checkpoint modes. Only an explicitly absent legacy parent link may
use chronological compatibility lookup; cycles, dangling links, and depth-limit
exhaustion fail closed. Existing single-checkpoint branches are never repaired by
copying a raw checkpoint because delta state is not self-contained in one tuple.
Both lookups additionally require the replay base to be a **settled** checkpoint
(`has_pending_tasks` — no scheduled `next` tasks). A checkpoint with pending tasks
is a mid-run snapshot: resuming from it replays the writes of the node that was
about to run. Message ids alone cannot exclude those, because middleware may
rewrite a message's id inside the run that produced it — `DynamicContextMiddleware`
moves the first user turn to `{id}__user` and gives `{id}` to the injected
reminder, so every checkpoint written before it holds the same prompt under an
unmatched id. Selecting one of those re-added the original prompt *after* the
edited one, and the model answered the question the edit was replacing (#4531).
`next` is not derivable on the degraded raw-checkpoint read path, which reports no
tasks; absence of evidence stays permissive there rather than failing closed.
Edit replay resolves its base through the same lineage-first path as regenerate;
it must pass `head_checkpoint` or it silently degrades to the chronological scan
that cannot tell sibling branches apart.

### Sandbox System (`packages/harness/deerflow/sandbox/`)

**Interface**: Abstract `Sandbox` with `execute_command(command, env=None)`, `read_file`, `write_file`, `list_dir`, `glob`, and `grep`. `grep` accepts either one text file or a directory tree. The optional `env` injects per-call environment variables (request-scoped secrets — see Request-Scoped Secrets below); `LocalSandbox` merges it via `subprocess.run(env=...)` and `AioSandbox` routes env-bearing commands through the `bash.exec(env=...)` API on a fresh session.
**Provider Pattern**: `SandboxProvider` with `acquire`, `acquire_async`, `get`, `release` lifecycle. Async agent/tool paths call async sandbox lifecycle hooks so Docker sandbox creation, discovery, cross-process locking, readiness polling, and release stay off the event loop.
**Environment policy** (`sandbox/env_policy.py`): `execute_command` no longer inherits the full `os.environ`. `build_sandbox_env()` scrubs secret-looking names (`*KEY*`/`*SECRET*`/`*TOKEN*`/`*PASS*`/`*CREDENTIAL*`) from the inherited environment before layering injected request secrets on top, so platform credentials (e.g. `OPENAI_API_KEY`) never leak into skill subprocesses. Benign vars (`PATH`, `HOME`, `LANG`, `VIRTUAL_ENV`, ...) are preserved.
**Implementations**:
- `LocalSandboxProvider` - Local filesystem execution. `acquire(thread_id)` returns a per-thread `LocalSandbox` (id `local:{thread_id}`) whose `path_mappings` resolve `/mnt/user-data/{workspace,uploads,outputs}` and `/mnt/acp-workspace` to that thread's host directories, so the public `Sandbox` API honours the `/mnt/user-data` contract uniformly with AIO. `acquire()` / `acquire(None)` keeps the legacy generic singleton (id `local`) for callers without a thread context. Per-thread sandboxes are held in an LRU cache (default 256 entries) guarded by a `threading.Lock`. Public, custom, legacy, and managed integration skill mappings point at stable enabled-only projection roots rather than raw skill directories.
- `AioSandboxProvider` (`packages/harness/deerflow/community/`) - Docker-based isolation. Active-cache and warm-pool entries are checked with the backend during acquire/reuse; definitively dead containers are dropped from all in-process maps so the thread can discover or create a fresh sandbox instead of reusing a stale client. Backend health-check failures are treated as unknown, not dead; local discovery likewise treats an unverifiable container as not adoptable and falls through to create rather than failing acquire. `get()` remains an in-memory lookup for event-loop-safe tool paths — it never touches the ownership store (that would be blocking IO on the event loop); ownership is published on acquire/reclaim and refreshed off the event loop by the dedicated renewal thread (`_renew_owned_leases`). `uses_thread_data_mounts` defaults to backend detection (`LocalContainerBackend=True`, remote/provisioner backends=False), while the optional `sandbox.thread_data_mounts` boolean takes precedence for deployments that guarantee the Gateway and sandbox share the same thread user-data directories. Setting it `true` skips upload-time sandbox acquire/sync; a false positive leaves uploads unavailable to the sandbox. Local-container and hostPath-provisioner mounts use the same stable skill projection roots; PVC-backed skills remain governed by the operator-supplied PVC layout until PVC materialization is implemented. Readiness probes and `agent_sandbox` clients classify loopback/private IPs, single-label cluster hosts, and Docker/Podman internal hostnames as direct control-plane destinations and set `trust_env=False`; external FQDNs and public IPs retain environment proxy support.
- `E2BSandboxProvider` (`packages/harness/deerflow/community/e2b_sandbox/`) provides E2B remote isolation.
  New sandboxes receive a one-shot upload from the enabled-only public, custom,
  legacy, and managed integration projections. Existing E2B VMs keep their
  creation-time snapshot because E2B has no shared host mount.
  Acquire and release share a per-user and thread lock. The provider lock does
  not cover remote IO. `burst_limit` adds capacity only for the `burst` policy.
  The `wait` policy fails the turn after `acquire_timeout`. The runtime does not
  retry the turn automatically. E2B acquisition uses a bounded executor.
  Waiting calls do not consume the default asyncio executor. The `reject`
  policy can evict one warm VM before it returns an error. With memory
  ownership, `replicas` limits one Gateway process. Redis ownership shares one
  `<ownership.key_prefix>:e2b-capacity` Hash, making the limit (plus a bounded
  burst) deployment-wide. Lua atomically manages VM and in-flight-create
  entries; missing or unavailable state fails closed. E2B reservation metadata
  repairs interrupted creates. Inventory replacement is revision-CAS guarded,
  and incomplete inventories never remove entries; complete omissions get a grace period.
  Uncertain cleanup keeps a tombstone slot. Shutdown tracks owned remote
  operation IDs. Discovery can find a VM from another Gateway. Shutdown closes
  an unowned discovery client without destroying its VM. Release ends its
  transition count when the VM enters the warm pool. Local client cleanup does
  not consume a second slot. A create that returns after shutdown retries one
  failed kill through a new client. An unconfirmed remote ID stays tracked.
  `reset()` uses full shutdown semantics. It destroys tracked active and warm
  VMs. It wakes capacity waiters. Callers cannot reuse the old provider
  instance. A background startup pass and periodic reconciliation list
  provider-tagged remote sandboxes within page/item/time budgets, probe every
  candidate until a healthy canonical sandbox is found, adopt only through the
  shared ownership store, and reap duplicates/orphans only after their
  configured grace/TTL and an atomic `del:` claim. Lease renewal is independent
  of reconciliation. Failed canonical adoption clears both its capacity
  reservation and acquire-intent marker even if a peer takes ownership between
  the initial claim and bootstrap cleanup. Shutdown kills only IDs whose leases
  are owned by this provider instance; peer-owned clients are merely closed.
  - **Cross-instance ownership store** (`aio_sandbox/ownership/`, #4206): gateway instances sharing a container backend coordinate container ownership through a pluggable lease store, selected by `sandbox.ownership.type` (`memory` | `redis`) and resolved like `stream_bridge` (`factory.py`, lazy per-branch import, `redis` optional extra, `DEER_FLOW_SANDBOX_OWNERSHIP_REDIS_URL` env escape hatch; a set `DEER_FLOW_STREAM_BRIDGE_REDIS_URL` implies a multi-instance deployment and infers `redis`). `memory` is single-instance only and declares `supports_cross_process = False`.
    - **A lease answers "who reaps this container", not "who may use it".** That splits the interface in two: `take()` transfers ownership on the **acquire** path (a container is deterministic per user/thread, so consecutive turns legitimately land on different instances — a conditional claim there would strand the thread until the previous lease expired), while `claim()` succeeds only if the container is unowned or already ours and gates every **adopt/reap** path. `release()` never clears a peer's lease.
    - **A lease carries a state, and that is what makes the destroy window safe.** `own:` = responsible for this container; `del:` = tearing it down (`claim(..., for_destroy=True)`). `take()` is refused against a `del:` lease, so a container cannot be re-acquired between a destroy path's claim and its container stop. Without the two states an unconditional `take()` would silently overwrite the destroyer's claim and the peer's stop would land on a container the new owner had already handed to an agent — i.e. #4206 again. That pairing is what replaced the previous same-host `flock` guard, which is gone; Redis makes the scope genuinely multi-instance instead of same-host. A destroyer that dies mid-stop leaves a `del:` marker that lapses with the TTL. On the acquire path a refused take raises `SandboxBeingDestroyedError`: the reuse/reclaim paths drop the container and cold-start, and the discover path propagates (falling through to create would collide with the not-yet-removed container name).
    - **The `del:` state has to be _held_ for the stop, not just written before it.** The two states alone do not make `flock` redundant — a held lock cannot expire, whereas a lease can, and `claim(..., for_destroy=True)` writes the marker with the ordinary lease TTL. Nothing else refreshes it: `renew()` extends only `own:` and deliberately reports a teardown as `LOST`, and the destroy paths drop the sandbox from the maps `_renew_owned_leases` iterates. So a container stop that outlived the TTL let the marker lapse, a peer's `take()` succeeded against the still-running container, and the stop then landed on the turn that had just been handed it — the exact window `del:` exists to close, reopened by its own expiry. `_held_teardown_lease` wraps **every** `del:`-marked stop — `_destroy_warm_entry`, `destroy()`, and `_drop_unhealthy_sandbox` — and re-claims the marker every `renewal_interval_seconds` until the stop returns. `_drop_unhealthy_sandbox` needs it most: it untracks _before_ claiming (under its `expected_info` TOCTOU guard), so `_renew_owned_leases` cannot see the id either. **The final release is the heartbeat's own last act, not the caller's** — a refresh `claim` still in flight when the context exits (the store's socket timeout bounds it, but it can be mid-round-trip) would otherwise land _after_ a caller-side release and rewrite `del:` on a container whose stop already completed, stranding a fresh `take()` (or rolling back a fresh create) until the TTL. Releasing from inside the heartbeat, after its loop stops, sequences the release strictly after the last refresh, so no claim can follow it; the context join is bounded and, on a genuine wedge, defers the release to that thread rather than clearing the marker itself. This covers a **failed** stop too (the container is probably still up, and a marker left behind refuses its own thread's `take()` until the TTL lapses); `destroy()` still lets the error propagate out of the `with` — `shutdown()` logs per sandbox off it. `RedisOwnershipStore` sets a `socket_timeout` so no store round trip — and so no heartbeat refresh — can block unbounded, keeping that deferred release finite. This needs no abnormal backend: the schema bounds only `renewal_interval_seconds` (> 0) and `ttl_multiplier` (>= 2), so a legal config puts the TTL below a normal container stop. `LocalContainerBackend._stop_container` now passes a `timeout` to `subprocess.run` (`_STOP_TIMEOUT_SECONDS`) so a wedged daemon cannot block unbounded — that bounds the residual window independently of the ownership layer, for the case where the `del:` marker lapses mid-stop (a store outage longer than the TTL) and the stop then lands on a container a peer has been handed. A timed-out stop propagates rather than being swallowed like a `CalledProcessError`: the container is probably still running, so reporting a clean stop would drop the warm entry and leak it. The TTL stays finite on purpose — the heartbeat dies with the process, so a destroyer that crashes mid-stop still releases the container one TTL later instead of marking it undestroyable forever. Raising a separate teardown TTL instead would only be sufficient if it were bounded above every backend's real stop deadline.
    - **Fail-closed both directions.** Establishment: a sandbox whose ownership cannot be published is never handed out (a just-created container is destroyed rather than leaked as an adoptable orphan) — acquiring raises `OwnershipBackendError`, matching the stream bridge's fail-hard v1 policy. Reaping: a store that cannot answer is treated as peer-owned, so an outage never turns live peer containers into orphans. **Renewal is the deliberate exception**: an unanswerable store there means _unknown_, not lost, so `_refresh_ownership` keeps the sandbox and retries — failing closed on that path would evict every live sandbox on every instance the moment the store blinked. The TTL still bounds how long a genuinely dead owner holds a lease. Both paths that stop a container they still track — `destroy()` and `_destroy_warm_entry` — claim **before** untracking, so a refused claim cannot leave a container running and untracked. (`_drop_unhealthy_sandbox` untracks first, under its `expected_info` TOCTOU guard, then claims before the stop; a refused claim there leaves the container to the next reconcile, which re-adopts it after the grace.)
    - **A lease excludes peers, never ourselves — same-process exclusion is the provider's job.** `claim()` and `take()` both succeed against this instance's own `own:` lease by design (that is what lets a destroy path claim what it already owns), so `del:` says nothing to this process's _other_ threads. Every reaper — idle checker, renewal, warm eviction, unhealthy drop — decides outside `self._lock`, because a store round trip must not be held under the lock that guards every acquire; so each one acts on a decision its own acquire path may already have invalidated. Two guards cover the two directions, and both live in `AioSandboxProvider`, not the store:
      - **Reaping** (`_reserve_local_teardown` / `_local_teardown`): the reaper marks the id, and every promote path — `_reuse_in_process_sandbox`, `_reclaim_warm_pool_sandbox`, `_register_discovered_sandbox` — refuses a marked id exactly as it refuses a peer's `del:` (drop and cold-start). The "is this still reapable?" check runs **in the same critical section as the mark**, passed down as a `still_reapable` predicate rather than run by the caller beforehand: checking first and marking second _is_ the window, not a narrower version of it. This matters most where the entry deliberately stays visible during the stop — both warm reapers defer their pop so a refused claim cannot lose the container — and where the maps are cleared first (`_drop_unhealthy_sandbox`), which leaves backend discovery as the open path. On `main` the mixin's `_evict_oldest_warm` / `_reap_expired_warm` popped under the lock, so the deferred pop is what made this reachable.
      - **Forgetting** (`_acquire_epoch`): when `renew()` reports `LOST` the peer legitimately wins, so here the _promote_ is the thing to detect. `_publish_ownership` bumps a per-id acquire epoch; `_renew_owned_leases` and `release()` snapshot it before the round trip and hand it to `_forget_lost_sandbox`, which skips the pop if it moved. Object identity is not enough: the reuse path re-publishes ownership while handing out the **same** tracked `AioSandbox`, so an identity check sees nothing and the pop closes a client mid-turn.
      - **A guard must become visible no later than the transition it guards.** The epoch cannot satisfy that for `take()`: the takeover is durable before `take()` returns (redis has committed the SET while the reply is in flight), and the epoch can only be written afterwards, so a renewal holding an older `LOST` walks through the gap, drops the maps, and closes the client the acquire is about to hand back — acquire then returns an id whose `get()` is `None`. `_publish_ownership` therefore publishes an **intent mark** (`_acquire_inflight`) under `_lock` _before_ the round trip; the epoch covers the other half, "an acquire completed since you decided". `_forget_lost_sandbox` honours the intent mark unconditionally, not only when an epoch is supplied — "no epoch" must not read as "no guard".
      - **A reservation must cover the removal, not just the stop.** `_destroy_warm_entry` pops the warm entry itself, inside the reservation. Releasing the reservation when the stop returns and letting the caller pop afterwards leaves a gap where the container is stopped, the entry is still in `_warm_pool`, and nothing marks it — a reclaim there hands out a dead container. The pop stays deferred relative to the _stop_ (a refused or failed stop keeps the entry), just no longer relative to the reservation.
      - **A check taken before a round trip must be retaken after it.** `_reuse_in_process_sandbox` re-verifies both its map entry and the local teardown reservation, `_reclaim_warm_pool_sandbox` re-checks the reservation, and `_register_discovered_sandbox` re-checks before installing its client, all after publishing ownership. Before the intent mark is set a renewal's `LOST` is both current and correct, so the forget can legitimately remove the entry the acquire decided to hand out; independently, a local reaper can reserve an id while reuse is outside `_lock` for its health/store calls and deliberately leaves the map entry present until its destroy claim succeeds. Falling through re-discovers or cold-starts instead of returning/installing a client for either stale decision. The pre-round-trip checks remain as early-outs that skip backend and store work on an already-doomed entry.
      - **Adoption is a promote too.** `_reconcile_orphans` honours the reservation: a container being torn down is untracked and still running, which is exactly the shape that loop adopts, and neither the claim (ours) nor the recovery grace (skipped entirely on `memory`, where `supports_cross_process` is `False`) excludes it.
      - **Active and warm are exclusive, and only a promote can violate it.** Both register paths pop `_warm_pool` inside the same locked section that inserts into `_sandboxes`: a warm entry for an id is stale the moment that id becomes active, and leaving it gives the container _two_ reapers — `_reap_expired_warm` judges it by the warm timestamp and never consults `_last_activity`, so it stops a container an agent is using while `_sandboxes` still hands out its client. Reachable because reconciliation adopts into the warm pool inside the register's publish → track window, and on `memory` it adopts on sight (`_adoptable_after_grace` short-circuits when `supports_cross_process` is `False`, so an id carrying this process's own lease reads as adoptable). On `main` the track was a single locked insert with nothing before it, so the window did not exist.
        A non-destroy `claim()` is the one case the store does police against its own owner: it refuses to overwrite our own `del:`, because the stop it marks is already in flight and downgrading the marker would let a `take()` hand out a container about to die. Enforced in both backends (Lua and Python) so they cannot drift.
    - **Renewal is independent of `idle_timeout`** (`_start_lease_renewal`, own daemon thread; TTL = `renewal_interval_seconds × ttl_multiplier`). Renewal used to ride on the idle checker, which `__init__` only starts when `idle_timeout > 0` — so `idle_timeout: 0` ("keep warm VMs until shutdown", a documented config) let every lease lapse. Liveness and reaping must not share a switch. Renewal covers warm entries as well as active ones; losing a lease drops the sandbox from this instance's maps **without touching the container** (`_forget_lost_sandbox`) — destroying it there would be the very cross-instance kill this store prevents.
      - A warm teardown is the local exception to that forget rule: `_destroy_warm_entry` deliberately keeps the entry in `_warm_pool` until the backend stop succeeds, while its own `del:` marker makes ordinary `renew()` report `LOST`. `_forget_lost_sandbox` therefore honours `_local_teardown`; otherwise the renewal thread can pop the retained entry mid-stop and a failed stop leaves a running container untracked.
    - **`renew()` distinguishes lapsed from lost** (`RenewOutcome`), and the two must not be collapsed. `LAPSED` means the lease is simply absent — nobody took it — so `_refresh_ownership` re-establishes it; `LOST` means a peer holds it and it is never re-taken. Treating an absent lease as lost meant a Redis restart without persistence (every key gone) evicted every in-flight sandbox on every instance at once.
      - Renewal's fail-open rule covers both store round trips. If `renew()` returns `LAPSED` but the follow-up `claim()` cannot answer, ownership is still unknown rather than lost, so the provider keeps the sandbox and retries. The ordinary `_claim_ownership` helper remains fail-closed for adopt/reap callers and is intentionally not used for this re-claim.
    - **Teardown join budget covers refresh plus release.** Redis bounds each ownership operation at five seconds, and context exit can catch the heartbeat in one final refresh before its `finally` performs the final release. `_TEARDOWN_JOIN_TIMEOUT_SECONDS` is therefore 12 seconds — greater than both sequential operation bounds — so a normal pair of socket timeouts does not emit the deferred-release warning; a still-running heartbeat continues to own the release safely.
    - **An absent lease means the same thing on both paths, and reconciliation must say so too.** The `LAPSED` rule above only covers an owner renewing its _own_ lease; on its own it does not make state loss safe, because reconciliation reads the same absent key as "orphan, adopt". After a Redis flush (restart without persistence, or eviction under `maxmemory`) every owner is alive and merely pre-renewal-tick, so whichever instance reconciles first would adopt every live container, each real owner's next renewal would report `LOST`, and it would drop a sandbox mid-turn for the adopter to idle-destroy — #4206 through the back door. `_adoptable_after_grace` closes it: an untracked container must be seen unowned (`owner()`, a read-only peek — the atomic `claim()` is still what actually gates adoption) across a full lease TTL before it can be adopted, tracked per container in `_unowned_since`. That rebuilds the delay the flush erased — a live owner republishes within one renewal interval, shorter than the TTL by construction (`ttl_multiplier >= 2`) — while a genuinely crashed owner never republishes, so its containers are still adopted one grace later rather than leaking. A republished lease **resets** the grace; a pausing-only timer would still expire over a live owner's lease. The grace is skipped when `supports_cross_process` is `False`: no peer can hold a lease such a store would show us, so single-instance deployments keep instant orphan cleanup, and a grace could not help a multi-worker gateway on `memory` anyway (peers are invisible to each other's leases with or without it).
    - **The `memory` store is single-instance only** and says so via `supports_cross_process = False`; the provider logs a warning at startup when the configured store cannot see peers. A multi-worker gateway on `memory` has no cross-process coordination at all — same contract as `stream_bridge`'s memory backend. This is why the redis inference matters: it reads `app_config.stream_bridge` **and** the env var, in the same order the bridge's own resolver does, so any deployment already pointing the bridge at Redis (i.e. every multi-instance one) gets a redis ownership store without extra config.
    - `get()` stays a pure in-memory lookup and must never call the store (that is blocking filesystem/network IO on the event loop); anchored by `tests/blocking_io/test_aio_sandbox_get.py`, which injects a deliberately-blocking probe store so the anchor keeps its teeth regardless of the configured backend. Tests: `tests/test_sandbox_ownership_store.py` (store contract, defined once for **both** backends — the redis tier is `@pytest.mark.integration`, uses `DEER_FLOW_TEST_REDIS_URL` when set, and otherwise self-skips without a reachable Redis. Backend CI provisions Redis, so the merge gate executes the real Lua tier; there is no fake-redis tier because a fake would not execute the Lua exclusions) and `tests/test_sandbox_orphan_reconciliation.py` (provider behaviour, two providers sharing one store).
- `BoxliteProvider` (`packages/harness/deerflow/community/boxlite/`) - BoxLite micro-VM isolation. The `boxlite` runtime is optional (`deerflow-harness[boxlite]`) and lazy-imported only when this provider is selected. The provider owns one private asyncio event loop on a daemon thread because BoxLite handles are loop-affine; sync `Sandbox` calls marshal onto that loop with `run_coroutine_threadsafe`.
  Boxes are named deterministically from `user_id:thread_id`, released into an in-process warm pool after each agent turn, and reclaimed only by the same user/thread. Warm-pool health checks use a short explicit timeout and forward that timeout through both BoxLite `exec(timeout=...)` and the private-loop `.result(timeout)` bridge so a hung VM cannot pin the per-thread acquire lock indefinitely.
  `sandbox.replicas` caps active + warm VMs per gateway process; if capacity is exhausted, only warm-pool VMs are evicted. `sandbox.idle_timeout` stops idle warm VMs after the configured seconds. `reset()` is intentionally a lightweight registry clear for `reset_sandbox_provider()` and does not close boxes, stop the idle reaper, or close the private loop; full teardown remains `shutdown()`.
- `TenkiSandboxProvider` (`packages/harness/deerflow/community/tenki/`) - Tenki cloud microVM isolation. The `tenki-sandbox` SDK is optional (`deerflow-harness[tenki]`) and lazy-imported (`_import_client`) only when this provider is selected. Unlike Boxlite, the SDK is synchronous, so the adapter calls it directly with no event-loop bridge. File transport uses Tenki's native `sandbox.fs` API (`read_text`/`read_stream`/`write_stream`/`mkdir`/`stat`) — binary-safe and streaming, no base64/shell hop; only directory/content *search* (`list_dir`/`glob`/`grep`) shells out to busybox-portable `find`/`grep`, parsed with the shared `deerflow.sandbox.search` helpers like `community/e2b_sandbox`. Sandboxes run as the unprivileged `tenki` user, so DeerFlow's `/mnt/user-data` prefix is remapped under a writable HOME (`_resolve_path`) and best-effort `sudo`-symlinked at bootstrap. Boxes are named deterministically from `sha256(user_id:thread_id)[:16]` (64-bit, matching E2B; the warm pool is keyed by this id alone with no full-seed fallback), released into an in-process warm pool, and reclaimed only by the same user/thread after a liveness check. A terminal session error (named SDK errors plus builtin `ConnectionError`/`BrokenPipeError`/`EOFError`) routes through `_invalidate_sandbox` to evict the dead microVM. Cross-process orphan reconciliation is a follow-up (single-process warm pool today).


**Shared warm-pool lifecycle:** community sandbox providers that keep released sandboxes alive for fast reuse share `deerflow.community.warm_pool_lifecycle.WarmPoolLifecycleMixin`. The mixin owns the common `DEFAULT_IDLE_TIMEOUT=600`, `IDLE_CHECK_INTERVAL=60`, `DEFAULT_REPLICAS=3`, idle-checker loop, warm-pool expiry, oldest-warm eviction, replica counting, and soft-cap logging. Providers remain responsible for their own active registries, creation/discovery, health checks, and destroy hook (`_destroy_warm_entry`): AIO destroys `SandboxInfo` through its backend; Boxlite closes loop-affine `BoxliteBox` handles; Tenki closes the microVM session (`TenkiSandbox.close`, which terminates the remote sandbox). AIO keeps active-idle cleanup outside the mixin and delegates only warm-pool expiry to the shared helper.

**Virtual Path System**:

- Agent sees: `/mnt/user-data/{workspace,uploads,outputs}`, `/mnt/skills`
- Physical: `backend/.deer-flow/users/{user_id}/threads/{thread_id}/user-data/...`; raw skills stay under `deer-flow/skills/` and managed integration storage, while sandboxes read `backend/.deer-flow/skills_view/public/` and `backend/.deer-flow/users/{user_id}/skills_view/{custom,legacy,integrations}/`
- Translation: `LocalSandboxProvider` builds per-thread `PathMapping`s for the user-data prefixes at acquire time; `tools.py` keeps `replace_virtual_path()` / `replace_virtual_paths_in_command()` as a defense-in-depth layer (and for path validation). AIO has the directories volume-mounted at the same virtual paths inside its container, so both implementations accept `/mnt/user-data/...` natively.
- Detection: `is_local_sandbox()` accepts both `sandbox_id == "local"` (legacy / no-thread) and `sandbox_id.startswith("local:")` (per-thread)

**Sandbox Tools** (in `packages/harness/deerflow/sandbox/tools.py`):

- `bash` - Execute commands with path translation and error handling. For `LocalSandbox` (host bash), POSIX output is captured through bounded pipe-drain threads and stdin is `/dev/null`, so a backgrounded long-lived process (`server &`) returns immediately instead of blocking the turn on an inherited pipe, while unredirected background output is drained without growing anonymous temp files. Commands that read stdin get immediate EOF. The command runs in its own process group with a wall-clock timeout (`sandbox.bash_command_timeout`, default 600s); on timeout the whole group is killed and the agent gets a notice telling it to background long-lived processes. The bash tool description itself also instructs the model to background long-lived processes (e.g. servers) up front so it doesn't waste the turn waiting on a foreground server. See `LocalSandbox.execute_command` / `_run_posix_command` and `bash_tool`'s docstring.
- `ls` - Directory listing (tree format, max 2 levels)
- `glob` - Find files or directories below a root directory with bounded results
- `grep` - Search one text file or recursively search a directory, with optional glob filtering and bounded line-level results
- `read_file` - Read file contents with optional line range
- `write_file` - Write/append to files, creates directories; overwrites by default and exposes the `append` argument in the model-facing schema for end-of-file writes; subject to the read-before-write gate when `read_before_write.enabled` (see Middleware Chain)
- `str_replace` - Substring replacement (single or all occurrences); same-path serialization is scoped to `(sandbox.id, path)` so isolated sandboxes do not contend on identical virtual paths inside one process; subject to the read-before-write gate when `read_before_write.enabled` (see Middleware Chain)

### Subagent System (`packages/harness/deerflow/subagents/`)

**Built-in Agents**: `general-purpose` (all tools except `task`) and `bash` (command specialist)
**Benefit-based routing policy**: Enabling subagents exposes delegation as an optimization, not a default response to complexity. The lead prompt defaults to direct execution and permits `task` only when parallel latency, specialist capability, or context-isolation benefit clearly exceeds startup, duplicate-discovery, synthesis, state-conflict, and side-effect costs. Inter-agent output dependencies and overlapping mutable state are hard vetoes for parallel dispatch, while duplicate discovery and a cheap direct path remain costs rather than categorical vetoes; a bounded sequential chain may run in one subagent when specialist or context-isolation benefit clearly wins. Parallel scopes must be independent and non-overlapping, the lead uses the fewest useful subagents, and every later batch is re-evaluated while retaining any within-batch parallel benefit. When the enforced per-response limit is 1, the rendered prompt removes parallel and multi-batch benefit guidance and permits delegation only for material specialist or context-isolation benefit. Keep this policy aligned across `lead_agent/prompt.py`, the `task` tool description, and both built-in role descriptions; routing regressions are pinned in `tests/test_subagent_routing_prompt.py`, `tests/test_subagent_prompt_security.py`, and `tests/test_lead_agent_prompt.py`.
**User-scoped Skills**: Subagents resolve their configured skills through `get_or_new_user_skill_storage(user_id)` using the parent runtime identity, with `DEFAULT_USER_ID` only when no identity is available. This keeps custom-skill shadowing and visibility aligned with the lead agent instead of reading the global-only catalog.
**Execution**: Dual thread pool - `_scheduler_pool` (3 workers) + `_execution_pool` (3 workers)
**Concurrency and total delegation cap**: `MAX_CONCURRENT_SUBAGENTS = 3` is enforced by `SubagentLimitMiddleware` (truncates excess tool calls in `after_model`; runtime `max_concurrent_subagents` is clamped to 1-4). The same middleware also enforces `subagents.max_total_per_run` (default 6, config schema 1-50, runtime override `max_total_subagents` clamped to the same range) against current-run entries in the durable delegation ledger, so a long lead-agent run cannot bypass concurrency limits by launching repeated legal-sized batches at each planning checkpoint, but historical delegations from previous runs in the same thread do not consume the new run's budget. The lead-agent prompt uses the same clamped values, so model-visible limits match enforcement. Gateway `run_agent()` and embedded `DeerFlowClient.stream()` both provide a per-invocation `run_id` in runtime context; `DeerFlowClient.stream()` also tags its input `HumanMessage` with that same id so durable-context capture can identify the current request boundary. Gateway resume paths may not append a new `HumanMessage`, so the worker also exposes the pre-run checkpoint's message ids in runtime context; durable-context capture uses that as the current-run boundary and never re-tags older task calls as the resumed run. When no delegation slots remain, task calls are stripped, provider raw tool-call metadata is synced, `finish_reason` is forced to `stop`, and a visible "subagent delegation limit" note is appended so the agent can synthesize already-collected results. Default subagent timeout `subagents.timeout_seconds=1800` (30 min) and built-in `general-purpose` `max_turns=150` (raised from 100/15-min so deep-research subtasks stop hitting `GraphRecursionError` out of the box)
**Flow**: `task()` tool → `SubagentExecutor` → background thread → poll 5s → SSE events → result. `task_started` carries the resolved effective model name. The per-subagent `SubagentTokenCollector` publishes a cumulative usage snapshot to the shared `SubagentResult` after every completed LLM response; the next `task_running` event carries that snapshot, so collapsed workspace cards can update without re-accounting parent-run totals. Terminal ToolMessage metadata (`subagent_model_name`, `subagent_token_usage`) and the persisted `subagent.end` event retain the model/usage after reload; absent provider usage stays absent rather than being estimated as zero.
**Events**: `task_started`, `task_running`, `task_completed`/`task_failed`/`task_timed_out`

**Step streaming is shared, terminal mapping is not** (`subagents/step_streaming.py`).
`SubagentExecutor` appends captured steps to `SubagentResult.ai_messages`, and
two callers turn that into `task_running` events: the `task` tool, which polls
its background entry, and `LiveStageAdapter`, which previously awaited one
terminal result and so rendered a DBTL stage worker as a single opaque stretch —
no reads, no tools, no Bash output, and, when the worker's structured result was
rejected, nothing at all to explain why. `SubagentStepStreamer` owns the cursor,
the 1-based indexing, and the cumulative usage snapshot;
`run_with_step_stream` runs the blocking `executor.execute` on a worker thread
and drains beside it, including once more after it returns so a step appended in
the last instant is not lost to the completion check. The streamer owns
`type`/`task_id`/`message`/`message_index`/`total_messages`/`usage` and merges a
caller's base payload *under* them, so a hand-built base cannot mislabel which
step an event describes.

Two rules shape it. **Progress reporting must never end the work it reports
on**: an emit that raises costs one event, and the cursor advances *before*
emitting so a failed emit cannot re-report the same step on every later poll.
And **terminal mapping stays with each caller** — the `task` tool answers to the
subagent status protocol while the adapter answers to a DBTL stage contract, and
one shared notion of "done" would have to lie to one of them. Stage steps carry
`dbtl_stage` and the worker's activity lineage but deliberately **not**
`council_seat`: the seat is asserted once at `task_started`, and ordinary Build
work carrying one would render as a design meeting. Tests:
`tests/test_subagent_step_streaming.py`, `tests/test_dbtl_stage_worker_progress.py`.
**Handled LLM failures**: `LLMErrorHandlingMiddleware` deliberately converts provider/model exceptions into an `AIMessage` so the graph can end cleanly, stamping `additional_kwargs.deerflow_error_fallback=true` plus error metadata. Clean graph termination does not imply subagent success: `SubagentExecutor` inspects the last assistant message at terminalization and maps a marked fallback to `SubagentStatus.FAILED`, which then emits `task_failed` and the existing structured `subagent_error`. Only the marker is authoritative — error-looking assistant prose without it remains a normal completed result, so neither the executor nor frontend parses display text as a status protocol.
**Guardrail caps & `stop_reason` (#3875 Phase 2)**: three independent axes can end a subagent run early, and all now surface _why_ through one additive field rather than a new status enum. **Turn axis**: `recursion_limit` on the subagent `run_config` equals `max_turns`, so exhausting the turn budget raises `GraphRecursionError` from `agent.astream`; `executor.py::_aexecute` catches it specifically (before the generic `except Exception`). **Token axis**: `TokenBudgetMiddleware` is attached per-agent via `build_subagent_runtime_middlewares` from `subagents.token_budget` (default `max_tokens` **coupled to `summarization.enabled`** — 1,000,000 when subagent summarization is on, 2,000,000 when off, warn at 0.7, hard-stop at 1.0; a user-set budget always wins regardless of the switch — #3875 Phase 3; a backstop against a subagent that burns tokens on trivial work). It does _not_ raise: at the hard-stop threshold it strips the in-flight turn's tool calls, forces `finish_reason="stop"`, and lets the run complete naturally with a final answer. **Loop axis**: `LoopDetectionMiddleware` (attached at the same point) catches repeated identical tool-call sets — or one tool _type_ called many times with varying args — and its hard-stop likewise strips `tool_calls` and forces a final answer without raising, recording `loop_capped`. Each guard exposes its cap on a per-`run_id` `consume_stop_reason(run_id)` accessor; `_aexecute` collects **every** middleware with that method (duck-typed via `hasattr`, so the executor has no import coupling to the guard classes) and surfaces the first non-`None` reason — adding a future guard needs no executor change. **Surfacing**: whichever axis fired, `_aexecute` stamps a normal status plus an additive reason — `completed` + `stop_reason=token_capped|turn_capped|loop_capped` when a usable final answer (or partial recovered from the last streamed chunk via `_extract_final_result` → `utils/messages.py::message_content_to_text`, returning a `"No response Generated"` sentinel when no text survived) was produced; `failed` + `stop_reason=turn_capped` when nothing usable survived. `SubagentResult.stop_reason` flows through `task_tool.py::_task_result_command` → `format_subagent_result_message` (renders `Task Succeeded (capped: ...)` / `Task failed (capped: ...)`) and `make_subagent_additional_kwargs`, which stamps the additive `subagent_stop_reason` key alongside the normal `subagent_status`. **Why additive, not an enum**: a new status value would break v1 consumers; an optional field is ignored by older frontends and ledger readers, so the cross-language contract (`contracts/subagent_status_contract.json` v2 + `subagents/status_contract.py` + `frontend/.../subtask-result.ts`, pinned by `test_status_values_match_contract` / `test_stop_reason_values_match_contract`) stays backward-compatible. The durable delegation ledger captures `stop_reason` onto the entry and renders model-facing guidance ("hit a guardrail cap with a partial result; reuse it, retry tighter, or raise the per-agent budget (`max_turns` / `token_budget`)") so the lead reuses a capped completion knowingly instead of mistaking it for a clean one. (Phase 1 shipped this surfacing as a `MAX_TURNS_REACHED` status enum in #3949; Phase 2 replaced that enum with the additive `stop_reason` field per the agreed design — the `max_turns_reached` status value and `SubagentStatus.MAX_TURNS_REACHED` are gone.)
**Context compaction (#3875 Phase 3, #4039)**: subagents inherit `DeerFlowSummarizationMiddleware` via `build_subagent_runtime_middlewares`, gated on the **same** `summarization.enabled` switch the lead reads (one config covers both chains; trigger/keep/model/prompt come from the shared `summarization` config so they cannot drift). Compaction never delegates authority to the summary model: the server-tagged initial subagent `SystemMessage` and `HumanMessage` stay verbatim, so a long Build cannot replace its phase manifest, Done conditions, or `DBTL_INPUT_n` grants with a paraphrase. Role alone is not provenance; an untagged system-role message remains compressible. The compaction-anchor marker is server-owned and stripped from external run input. Anchors are excluded only from message-trigger counting so an uncompressible fixed contract cannot make every subsequent ReAct pair invoke the summary model; they remain in token/fraction accounting because their bytes still occupy the provider context window. The subagent builder attaches `DurableContextMiddleware` immediately before summarization, using the same skills path/read-tool settings as the lead chain. Compaction stores the generated summary in `ThreadState.summary_text` rather than as a `messages` item; the durable-context wrapper therefore projects it into the next model request as guarded hidden human data. This is required when a message-count keep policy preserves only an assistant tool-call plus its tool results: without the injected summary the next request begins with assistant/tool history and strict OpenAI-compatible providers can reject it. Because `DurableContextMiddleware` inserts a second `SystemMessage(authority_contract)` after the subagent's leading system prompt, the builder also appends `SystemMessageCoalescingMiddleware` innermost (mirroring the lead chain, appended after the optional summarization middleware so it is unconditionally last) to merge every `SystemMessage` into one leading `system_message` — otherwise the durable fix would trade #4039's assistant-first HTTP 400 for a duplicate-system 400 on the same strict backends (#4040). The factory is called with `skip_memory_flush=True` on the subagent path: the lead's `memory_flush_hook` (attached when `memory.enabled`) flushes pre-compaction messages into durable memory keyed by `thread_id`, and subagents share the parent's `thread_id`, so without skipping the hook a subagent's internal turns would pollute the **parent** thread's durable memory. Placement differs from the lead chain (lead appends summarization _before_ the guard trio; subagent appends it _after_) — benign because the middleware implements only `before_model` (compaction) with no `after_model`/`consume_stop_reason`, so it cannot disturb the Phase 2 guard-cap stop-reason channel. Compaction rewrites the messages channel via `RemoveMessage(id=REMOVE_ALL_MESSAGES)`, which shrinks `len(messages)` below the step-capture cursor mid-run; `capture_new_step_messages` (see Step capture below) resets the cursor to the new tail on contraction so steps appended after the compaction point are not silently dropped.
**Step capture & persistence (#3779)**: `executor.py` captures both assistant turns (`AIMessage`) **and** tool outputs (`ToolMessage`) via `subagents/step_events.py::capture_new_step_messages`, which walks the _newly-appended tail_ of each `stream_mode="values"` chunk (not just `messages[-1]`) so a multi-tool-call turn — where LangGraph's `ToolNode` appends several `ToolMessage`s in one super-step — keeps every tool output instead of dropping all but the last. Every child graph is tagged `TAG_NOSTREAM`: these internal messages belong only in the subtask timeline and must not enter the parent `messages-tuple` stream or `RunJournal` thread feed. `runtime/runs/worker.py::_SubagentEventBuffer` additionally persists these `task_*` custom events to the `RunEventStore` as `subagent.start`/`subagent.step`/`subagent.end` (`category="subagent"`, `task_id` in `metadata`). It **batches** writes via `put_batch` (flushing on a terminal `subagent.end`, at `FLUSH_THRESHOLD` events, and in the worker's `finally`) rather than one `put()` per step, since `put()` is a documented low-frequency path (per-thread advisory lock per call) and a deep subagent (`max_turns=150`) emits hundreds of steps on the hot stream loop. `subagent_run_event` rejects malformed chunks that lack a non-empty `task_id`; running chunks additionally require a non-negative integer `message_index` and a message object, so persisted records always satisfy the required lifecycle envelope. `build_subagent_step` caps both the per-step `text` and each tool call's serialized `args` at `SUBAGENT_STEP_MAX_CHARS` (flagged `truncated` / `args_truncated`) so a large `write_file`/`bash` payload can't produce an unbounded row. The dedicated category keeps them out of `list_messages` (the thread feed) while `list_events` returns them for the frontend's fetch-on-expand backfill. `list_events` accepts `task_id` (filters on `metadata["task_id"]` — SQL-side in `DbRunEventStore` via `event_metadata["task_id"].as_string()`, in-memory in the JSONL/memory stores) plus an `after_seq` forward cursor, so the card pages through one subagent's steps without the run-wide `limit` truncating the tail (no schema migration: the filter rides the existing run-scoped index). `step_events.py` is a pure, unit-tested layer (`build_subagent_step` / `subagent_run_event`). **History contraction (#3875 Phase 3)**: `capture_new_step_messages` assumes append-only growth, but `DeerFlowSummarizationMiddleware` rewrites the messages channel via `RemoveMessage(id=REMOVE_ALL_MESSAGES)`, shrinking `len(messages)` below the cursor mid-run. On contraction (`total < processed_count`) the cursor resets to the new tail; `capture_step_message`'s id/content dedup prevents re-emitting pre-compaction steps, so steps appended after the compaction point are still captured instead of being dropped until `total` overtakes the stale cursor.
**Deferred MCP tools** (if `tool_search.enabled`): `SubagentExecutor._build_initial_state` assembles deferral after policy filtering via the shared `assemble_deferred_tools` (fail-closed), appends the `tool_search` tool, injects the `<available-deferred-tools>` section into the subagent's `SystemMessage`, and threads the setup to `_create_agent`, which attaches `McpRoutingMiddleware` (when PR1 routing metadata matches deferred tools) before `DeferredToolFilterMiddleware` through `build_subagent_runtime_middlewares(...)`. Subagents thus withhold full MCP schemas until promotion, same as the lead agent; each task run gets a fresh `ThreadState` so promotion is isolated per run

**Guardrail caps & `stop_reason` (#3875 Phase 2)**: three independent axes can end a subagent run early, and all now surface *why* through one additive field rather than a new status enum. **Turn axis**: `recursion_limit` on the subagent `run_config` equals `max_turns`, so exhausting the turn budget raises `GraphRecursionError` from `agent.astream`; `executor.py::_aexecute` catches it specifically (before the generic `except Exception`). **Token axis**: `TokenBudgetMiddleware` is attached per-agent via `build_subagent_runtime_middlewares` from `subagents.token_budget` (default `max_tokens` **coupled to `summarization.enabled`** — 1,000,000 when subagent summarization is on, 2,000,000 when off, warn at 0.7, hard-stop at 1.0; a user-set budget always wins regardless of the switch — #3875 Phase 3; a backstop against a subagent that burns tokens on trivial work). It does *not* raise: at the hard-stop threshold it strips the in-flight turn's tool calls, forces `finish_reason="stop"`, and lets the run complete naturally with a final answer. **Loop axis**: `LoopDetectionMiddleware` (attached at the same point) catches repeated identical tool-call sets — or one tool *type* called many times with varying args — and its hard-stop likewise strips `tool_calls` and forces a final answer without raising, recording `loop_capped`. Each guard exposes its cap on a per-`run_id` `consume_stop_reason(run_id)` accessor; `_aexecute` collects **every** middleware with that method (duck-typed via `hasattr`, so the executor has no import coupling to the guard classes) and surfaces the first non-`None` reason — adding a future guard needs no executor change. **Surfacing**: whichever axis fired, `_aexecute` stamps a normal status plus an additive reason — `completed` + `stop_reason=token_capped|turn_capped|loop_capped` when a usable final answer (or partial recovered from the last streamed chunk via `_extract_final_result` → `utils/messages.py::message_content_to_text`, returning a `"No response Generated"` sentinel when no text survived) was produced; `failed` + `stop_reason=turn_capped` when nothing usable survived. `SubagentResult.stop_reason` flows through `task_tool.py::_task_result_command` → `format_subagent_result_message` (renders `Task Succeeded (capped: ...)` / `Task failed (capped: ...)`) and `make_subagent_additional_kwargs`, which stamps the additive `subagent_stop_reason` key alongside the normal `subagent_status`. **Why additive, not an enum**: a new status value would break v1 consumers; an optional field is ignored by older frontends and ledger readers, so the cross-language contract (`contracts/subagent_status_contract.json` v2 + `subagents/status_contract.py` + `frontend/.../subtask-result.ts`, pinned by `test_status_values_match_contract` / `test_stop_reason_values_match_contract`) stays backward-compatible. The durable delegation ledger captures `stop_reason` onto the entry and renders model-facing guidance ("hit a guardrail cap with a partial result; reuse it, retry tighter, or raise the per-agent budget (`max_turns` / `token_budget`)") so the lead reuses a capped completion knowingly instead of mistaking it for a clean one. (Phase 1 shipped this surfacing as a `MAX_TURNS_REACHED` status enum in #3949; Phase 2 replaced that enum with the additive `stop_reason` field per the agreed design — the `max_turns_reached` status value and `SubagentStatus.MAX_TURNS_REACHED` are gone.)
**Context compaction (#3875 Phase 3, #4039)**: subagents inherit `DeerFlowSummarizationMiddleware` via `build_subagent_runtime_middlewares`, gated on the **same** `summarization.enabled` switch the lead reads (one config covers both chains; trigger/keep/model/prompt come from the shared `summarization` config so they cannot drift). The subagent builder attaches `DurableContextMiddleware` immediately before summarization, using the same skills path/read-tool settings as the lead chain. Compaction stores the generated summary in `ThreadState.summary_text` rather than as a `messages` item; the durable-context wrapper therefore projects it into the next model request as guarded hidden human data. This is required when a message-count keep policy preserves only an assistant tool-call plus its tool results: without the injected summary the next request begins with assistant/tool history and strict OpenAI-compatible providers can reject it. Because `DurableContextMiddleware` inserts a second `SystemMessage(authority_contract)` after the subagent's leading system prompt, the builder also appends `SystemMessageCoalescingMiddleware` innermost (mirroring the lead chain, appended after the optional summarization middleware so it is unconditionally last) to merge every `SystemMessage` into one leading `system_message` — otherwise the durable fix would trade #4039's assistant-first HTTP 400 for a duplicate-system 400 on the same strict backends (#4040). The factory is called with `skip_memory_flush=True` on the subagent path: the lead's `memory_flush_hook` (attached when `memory.enabled`) flushes pre-compaction messages into durable memory keyed by `thread_id`, and subagents share the parent's `thread_id`, so without skipping the hook a subagent's internal turns would pollute the **parent** thread's durable memory. Placement differs from the lead chain (lead appends summarization *before* the guard trio; subagent appends it *after*) — benign because the middleware implements only `before_model` (compaction) with no `after_model`/`consume_stop_reason`, so it cannot disturb the Phase 2 guard-cap stop-reason channel. Compaction rewrites the messages channel via `RemoveMessage(id=REMOVE_ALL_MESSAGES)`, which shrinks `len(messages)` below the step-capture cursor mid-run; `capture_new_step_messages` (see Step capture below) resets the cursor to the new tail on contraction so steps appended after the compaction point are not silently dropped.
**Step capture & persistence (#3779)**: `executor.py` captures both assistant turns (`AIMessage`) **and** tool outputs (`ToolMessage`) via `subagents/step_events.py::capture_new_step_messages`, which walks the *newly-appended tail* of each `stream_mode="values"` chunk (not just `messages[-1]`) so a multi-tool-call turn — where LangGraph's `ToolNode` appends several `ToolMessage`s in one super-step — keeps every tool output instead of dropping all but the last. `runtime/runs/worker.py::_SubagentEventBuffer` additionally persists these `task_*` custom events to the `RunEventStore` as `subagent.start`/`subagent.step`/`subagent.end` (`category="subagent"`, `task_id` in `metadata`). It **batches** writes via `put_batch` (flushing on a terminal `subagent.end`, at `FLUSH_THRESHOLD` events, and in the worker's `finally`) rather than one `put()` per step, since `put()` is a documented low-frequency path (per-thread advisory lock per call) and a deep subagent (`max_turns=150`) emits hundreds of steps on the hot stream loop. `subagent_run_event` rejects malformed chunks that lack a non-empty `task_id`; running chunks additionally require a non-negative integer `message_index` and a message object, so persisted records always satisfy the required lifecycle envelope. `build_subagent_step` caps both the per-step `text` and each tool call's serialized `args` at `SUBAGENT_STEP_MAX_CHARS` (flagged `truncated` / `args_truncated`) so a large `write_file`/`bash` payload can't produce an unbounded row. The dedicated category keeps them out of `list_messages` (the thread feed) while `list_events` returns them for the frontend's fetch-on-expand backfill. `list_events` accepts `task_id` (filters on `metadata["task_id"]` — SQL-side in `DbRunEventStore` via `event_metadata["task_id"].as_string()`, in-memory in the JSONL/memory stores) plus an `after_seq` forward cursor, so the card pages through one subagent's steps without the run-wide `limit` truncating the tail (no schema migration: the filter rides the existing run-scoped index). `step_events.py` is a pure, unit-tested layer (`build_subagent_step` / `subagent_run_event`). **History contraction (#3875 Phase 3)**: `capture_new_step_messages` assumes append-only growth, but `DeerFlowSummarizationMiddleware` rewrites the messages channel via `RemoveMessage(id=REMOVE_ALL_MESSAGES)`, shrinking `len(messages)` below the cursor mid-run. On contraction (`total < processed_count`) the cursor resets to the new tail; `capture_step_message`'s id/content dedup prevents re-emitting pre-compaction steps, so steps appended after the compaction point are still captured instead of being dropped until `total` overtakes the stale cursor.
**Deferred MCP tools** (if `tool_search.enabled`): `SubagentExecutor._build_initial_state` applies the subagent name allow/deny list and assembly-time authorization before calling the shared `assemble_deferred_tools`, appends the `tool_search` tool, injects the `<available-deferred-tools>` section into the subagent's `SystemMessage`, and threads the setup to `_create_agent`, which attaches `McpRoutingMiddleware` (when PR1 routing metadata matches deferred tools) before `DeferredToolFilterMiddleware` through `build_subagent_runtime_middlewares(...)`. Runtime skill policy is intentionally later and dynamic: `tool_search` may disclose/promote catalog metadata, but `SkillToolPolicyMiddleware` still removes or blocks any promoted business tool omitted by the active skill. Subagents thus withhold full MCP schemas until promotion, same as the lead agent; each task run gets a fresh `ThreadState` so promotion is isolated per run
**Checkpointer isolation**: Subagent graphs are compiled with `checkpointer=False` to avoid inheriting the parent run's checkpointer, since subagents are one-shot and never resume.
**Checkpoint lineage / stream isolation**: `_aexecute` deliberately omits checkpoint-coordinate keys (`thread_id`, `checkpoint_ns`, `checkpoint_id`, `checkpoint_map`) from the child `RunnableConfig`. LangGraph must inherit those coordinates from the copied parent ContextVar so the delegated graph retains a non-root subgraph namespace; explicitly re-supplying even the same parent `thread_id` starts a new root lineage on LangGraph 1.2.6+ and can route child AI/tool frames into the parent `messages` stream. DeerFlow business components still receive the parent `thread_id` through `runtime.context`, which is the preferred lookup path for sandbox, middleware, and attribution code. Regression coverage in `tests/test_subagent_executor.py::TestSubagentCheckpointLineage` keeps the invocation-contract assertion active on every supported version and version-gates the production-shaped parent-stream test to LangGraph 1.2.6+, where the leak exists.

**Isolated-loop callback boundary**: sync delegation from an active event loop and `execute_async()` copy the ambient ContextVars into the persistent subagent loop so checkpoint lineage, user identity, tracing context, tags, metadata, and LangGraph's namespaced message-stream handler survive. Before submission, `_copy_isolated_subagent_context()` copies the callback manager/list and removes only handlers marked `deerflow_loop_bound`; `RunJournal` carries that marker because it owns parent-loop tasks and a SQL store/pool. LangGraph merges inherited callbacks with the child run's explicit `SubagentTokenCollector`/tracing callbacks, so letting `RunJournal` cross loops causes duplicate accounting and `Future attached to a different loop` failures, while dropping the whole callback chain silently removes child token frames. Do not replace the boundary with a blank `Context`; the inherited checkpoint namespace and framework stream callback are required by the stream-isolation contract above.

### Tool System (`packages/harness/deerflow/tools/`)

`get_available_tools(groups, include_mcp, model_name, subagent_enabled)` assembles:

1. **Config-defined tools** - Resolved from `config.yaml` via `resolve_variable()`
2. **MCP tools** - From enabled MCP servers (lazy initialized, cached with resolved-path + content-signature invalidation)
3. **Built-in tools**:
   - `present_files` - Make output files visible to user (only `/mnt/user-data/outputs`); virtual paths use `resolve_runtime_user_id(runtime)` so validation resolves the same user-scoped outputs directory established by `ThreadDataMiddleware`
   - `ask_clarification` - Request clarification (intercepted by ClarificationMiddleware, which preserves text fallback and adds `artifact.human_input` for Web UI Human Input Cards). Beyond free text and single choice, the request-side v2 protocol supports `fields` (structured form card collecting several values at once; field types: text/textarea/number/select/multi_select/checkbox/date, validated and normalized server-side in the middleware — invalid entries are dropped, unknown types degrade to `text`; a standalone multi-select question is a one-field form). Replies stay on the v1 response protocol (`text`/`option`): the form card submits a readable text summary
   - `view_image` - Read image as base64 (added only if model supports vision)
   - `setup_agent` - Bootstrap-only: persist a brand-new custom agent's `SOUL.md` and `config.yaml`. Bound only when `is_bootstrap=True`.
   - `update_agent` - Custom-agent-only: persist self-updates to the current agent's `SOUL.md` / `config.yaml` from inside a normal chat (partial update + atomic write). Bound when `agent_name` is set and `is_bootstrap=False`.
4. **Subagent tool** (if enabled):
   - `task` - Delegate to subagent (description, prompt, subagent_type)

Scheduled-task runtime note:

- Scheduled background runs set `context.non_interactive=true` and therefore exclude `ask_clarification` from the lead-agent tool list. This keeps scheduler-triggered runs from stalling on human confirmation mid-execution. `non_interactive` is an internal-only context key: it is merged from `body.context` only when the request authenticated as the process-internal user (the scheduler path), never from arbitrary HTTP/IM clients.

**Community tools** (`packages/harness/deerflow/community/`): optional integrations, each in its own subpackage and wired through `config.yaml`. Documented examples:

- `tavily/` - Web search (5 results default) and web fetch (4KB limit)
- `jina_ai/` - Web fetch via Jina reader API with readability extraction
- `firecrawl/` - Web scraping via Firecrawl API
- `image_search/` - Image search via DuckDuckGo
- `aio_sandbox/` - Docker-based isolation (`AioSandboxProvider`)
- `browser_automation/` - Agentic browser control (stateful `navigate → observe → click/type` loop) via Playwright, distinct from the read-only `web_fetch`/`web_capture` tools. Tools: `browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_get_text`, `browser_back`, `browser_screenshot`, `browser_close` (config `group: browser`). A process-local `BrowserSessionManager` owns one private, loop-affine Playwright event-loop thread (same pattern as the BoxLite provider) so a per-thread browser session survives across turns regardless of the caller's loop (Gateway / TUI / test). Each action returns a fresh page snapshot whose interactive elements are addressed by a stable numeric `[ref]` index (stamped as `data-df-ref` during snapshot), so the model acts on what it just observed instead of holding stale handles or guessing selectors. URLs are SSRF-screened via the shared `validate_public_http_url` (opt-out `allow_private_addresses` only for intentional internal targets). CDP attachment cannot install the request guard on an existing Chrome context, so `cdp_url` fails closed unless the operator explicitly sets `allow_unguarded_cdp: true` for a trusted local browser. Browser REST/Live access also requires an exact non-NULL thread owner, rather than the general legacy shared-thread policy, because retained pages may contain authenticated state. Session admission is a hard `max_sessions` cap: pinned Live/operation sessions are never evicted, and a new thread is rejected when no unpinned session can be closed; one Live viewer owns a session at a time. Optional dependency: `cd backend && uv sync --extra browser && uv run playwright install chromium`; `scripts/detect_uv_extras.py` preserves the extra when `config.yaml` enables `browser_navigate`, and Gateway startup fails fast if configured browser control cannot import Playwright. Tests: `tests/test_browser_automation.py` (mocked tools + a real-Chromium integration test guarded by `importorskip`); `tests/manual_browser_live_check.py` is a manual DeepSeek-driven end-to-end check (not collected by pytest).
  Live UI input dispatch is kept independent from JPEG capture: non-move actions start a rate-limited background refresh loop, so pointer, wheel, or keyboard input stays responsive while continuous gestures still produce frames throughout the interaction.

Additional providers also live here (`boxlite`, `brave`, `browserless`, `crawl4ai`, `ddg_search`, `e2b_sandbox`, `exa`, `fastcrw`, `groundroute`, `infoquest`, `searxng`, `serper`, `tenki`); see each subpackage for specifics. E2B bootstrap is required. If it fails, the provider kills and closes the unusable remote sandbox. New sandbox creation raises an error. Warm-pool reclaim and remote discovery discard the sandbox and continue acquisition. E2B mounts remain optional.

E2B output sync records remote file versions and actual host file metadata in a thread-local manifest. The manifest binds to the remote sandbox ID. A complete output listing removes entries for deleted files. This avoids repeat downloads when the host filesystem rounds modification times. A single release-time sync pass is bounded by aggregate ceilings (`_MAX_SYNC_TOTAL_BYTES`, `_MAX_SYNC_FILES`, `_SYNC_DEADLINE_SECONDS`) on top of the per-file `_MAX_DOWNLOAD_SIZE` cap, so a pathological outputs tree cannot make release download unboundedly; a truncated pass logs what it dropped and leaves the manifest un-pruned (only entries observed in that pass are reconciled), so files it never reached are retried on the next release rather than being forgotten.

**ACP agent tools**:

- `invoke_acp_agent` - Invokes external ACP-compatible agents from `config.yaml`
- ACP launchers must be real ACP adapters. The standard `codex` CLI is not ACP-compatible by itself; configure a wrapper such as `npx -y @zed-industries/codex-acp` or an installed `codex-acp` binary
- Missing ACP executables now return an actionable error message instead of a raw `[Errno 2]`
- Each ACP agent uses a per-thread workspace at `{base_dir}/users/{user_id}/threads/{thread_id}/acp-workspace/`. The workspace is accessible to the lead agent via the virtual path `/mnt/acp-workspace/` (read-only). In docker sandbox mode, the directory is volume-mounted into the container at `/mnt/acp-workspace` (read-only); in local sandbox mode, path translation is handled by `tools.py`

### MCP System (`packages/harness/deerflow/mcp/`)

- Uses `langchain-mcp-adapters` `MultiServerMCPClient` for multi-server management
- **Lazy initialization**: Tools loaded on first use via `get_cached_mcp_tools()`
- **Cache invalidation**: Detects extensions-config changes by comparing the resolved config path and a `(mtime, size, sha256)` content signature against the values recorded at initialization, not a strict mtime `>` comparison. This catches same-second edits, mtime that stays put or moves backward (`git checkout`, `cp -p` / backup restore, `tar` / `rsync`, object-store / network mounts), and a switch to a different config file with an equal-or-older mtime. The signature helper (`config/file_signature.py::get_config_signature`) is shared with `config/app_config.py::get_app_config()` for the sibling runtime-editable config file, rather than each maintaining its own copy. `ExtensionsConfig.resolve_config_path()` raises `FileNotFoundError` for an explicit `config_path`/`DEER_FLOW_EXTENSIONS_CONFIG_PATH` that points at a missing file — an operator-asserted path going missing is a real misconfiguration, so this is intentionally loud for callers that load the config for actual use (e.g. `from_file()` via `get_mcp_tools()`); only the fallback search mode returns `None`. The MCP cache's own path resolution (`mcp/cache.py::_resolve_config_path`) is narrower: it catches that specific `FileNotFoundError` locally and treats it the same as "unconfigured", so this staleness check degrades to "not stale" instead of propagating an exception when a previously-valid explicit/env-var config disappears mid-run
- **Transports**: stdio (command-based), SSE, HTTP
- **Per-server tool-name prefixing**: `mcpServers.<server>.tool_name_prefix` defaults to `true`, preserving the collision-safe `<server_name>_` prefix. Servers whose tools already carry a stable namespace may set it to `false`; discovery then calls `langchain_mcp_adapters.tools.load_mcp_tools` with that server's flag. Source routing and stdio session-pool wrapping are based on the producing server and transport, never on whether the visible tool name starts with the server prefix.
- **OAuth (HTTP/SSE)**: Supports token endpoint flows (`client_credentials`, `refresh_token`) with automatic token refresh + Authorization header injection
- **Routing hints**: `extensions_config.json -> mcpServers.<server>.routing` and
  `tools.<original_tool_name>.routing` are soft preference metadata. The effective
  routing is resolved while `mcp/tools.py::get_mcp_tools()` still has both
  `source_name` and the original MCP tool name, then stored on `tool.metadata`
  under `deerflow_mcp_routing`. Prompt rendering uses
  `tools/builtins/tool_search.py::get_mcp_routing_hints_prompt_section`, which
  references `tool_search` when a hinted MCP tool is currently deferred; do not
  add a parallel routing middleware for PR1-style preference hints.
- **Stdio file outputs**: Persistent stdio sessions are scoped by `user_id:thread_id`. For stdio transports only, DeerFlow pins the subprocess default `cwd` to the thread workspace and `TMPDIR`/`TMP`/`TEMP` to `workspace/.mcp/tmp/`, unless the operator explicitly configured `cwd` or temp env values. SSE/HTTP transports skip this filesystem prep entirely.
- **Stdio path translation**: MCP-returned local file references are not copied. If a `ResourceLink` or conservative free-text path resolves to an existing file inside the thread's mounted user-data tree, it is translated deterministically to `/mnt/user-data/...`; paths outside that tree remain unchanged.
- **Runtime updates**: Gateway API saves to extensions_config.json; the Gateway-embedded runtime detects changes via the resolved-path + content-signature check above, so multi-worker / stale-mtime deployments still pick up an added/removed MCP server without a restart (`PUT /api/mcp/config` keeps whole-payload validation, while `PATCH /api/mcp/config` changes only one server's `enabled` field, normalizes the same `type`/MCP-spec `transport` alias as the runtime config model, and validates the target only when enabling it; either endpoint's reset clears the cache only in its own worker). MCP, skill, and embedded-client writers share `atomic_write_extensions_config()`, which writes and fsyncs a same-directory temporary file before `os.replace()` and preserves an existing file's mode and symlink target; failed serialization or replacement leaves the prior config intact and cleans up the temporary file.
- **Stdio launch policy at the HTTP boundary** (`routers/mcp.py::_validate_mcp_update_request`, shared by `PUT` and the enable branch of `PATCH`): a config file may express anything, but the API is untrusted input, so an API-registered stdio server must (a) name a bare executable from the allowlist — `_DEFAULT_MCP_STDIO_COMMAND_ALLOWLIST` = `{npx, uvx}`, extended by `DEER_FLOW_MCP_STDIO_COMMAND_ALLOWLIST`, with path separators, whitespace, and shell metacharacters rejected in `command`; (b) carry no `args` flag in `_ARBITRARY_EXEC_ARGS`; and (c) set no `env` name in `_CODE_INJECTING_ENV_VARS`. Checks (b) and (c) exist because the command check alone names a binary without constraining what that binary runs. The `env` denylist applies to **every** allowlisted command, and both denylists match `--flag=value` as well as `--flag value`. The `args` denylist's **scope depends on the command**, because where a launcher stops parsing its own flags is what decides whether a token is an exec flag at all:

  - For a **package launcher** in `_PACKAGE_LAUNCHERS` (`{npx, uvx}`) only the launcher's own **option region** is screened. `npx`/`uvx` stop parsing their flags at the package name and hand every later token to the spawned server's argv, where `-c` is routinely "config" and `-e` "env" — screening those rejected ordinary third-party servers while covering nothing. A bare `--` ends the region too: only the *first* token after it is the package name. Finding that boundary needs each launcher's option **arity**, since a value is not a positional — `npx -p <pkg> -c '<command>'` **runs** the command (`-p` is `npm exec`'s `--package`, so `<pkg>` is its value and npm keeps parsing), so ending the region at the first non-flag token would walk straight past it. `_NPX_BOOLEAN_ARGS` is generated from `@npmcli/config`'s definitions (npm 10.9.4) minus the `-p` exec override; `_UVX_VALUE_ARGS` comes from `uvx --help` (uv 0.11.1). Regenerate these against a newer launcher rather than hand-editing. The unknown-option default is deliberately **opposite** per launcher, following the exec set rather than symmetry: npx owns real exec flags (`-c`/`--call`), so an unknown option consumes a value and keeps the region open (npm errors on options it does not define, so this cannot reject a working invocation); uvx owns no string-eval flag at all, so its screen is a tripwire, an unknown option consumes nothing, and uv's large boolean surface cannot over-block. uvx's exec set also drops the short spellings, because `-c` is uv's `--constraints` and `-p` its `--python`.
  - Every **other** command is screened whole, with two extra rules, because it is an interpreter rather than a package runner: `-p` counts as an exec flag there (node's `--print`), and single-dash short-option clusters are decomposed letter by letter so `node -pe` cannot pass a check that only splits on `=`.

  Verdicts are pinned against the real launchers: for npx, every argument vector the validator rejects is one `npx` actually executes, and every vector it allows is one `npx` passes through to the server. `env` screening covers names that execute code **unconditionally** at process startup, e.g. `PYTHONPATH`/`PYTHONHOME`, which run a caller-controlled `sitecustomize.py` at interpreter startup under plain `uvx`. Caller-controlled **search paths** are a weaker, conditional class and are an accepted residual: `LD_LIBRARY_PATH`/`DYLD_LIBRARY_PATH` (conditional on the process loading a shadowable library, and legitimately set by native-dependency servers) and `NODE_PATH` (searched *after* the local `node_modules` chain, so it cannot shadow an installed dependency, and ignored entirely by ESM `import` — it can only supply a CJS module that would otherwise fail to resolve). Do not move a search path into the set: it would make the "unconditional" rule untrue, which is how a defense-in-depth list starts being mistaken for a boundary. Remote transports skip all three — they spawn nothing.
  **This is defense in depth, not a trust boundary.** `npx`/`uvx` exist to fetch and execute remote packages, so an admin can still point one at a package they published; the boundary is admin authentication plus network reachability. Do not add a check here on the assumption that it makes MCP registration safe for untrusted admins — it does not, and the fix for that is not a bigger denylist.

### Skills System (`packages/harness/deerflow/skills/`)

- **Location**: global public skills live under `deer-flow/skills/public/`; user-authored custom skills live under `{DEER_FLOW_HOME}/users/{user_id}/skills/custom/`; globally managed integration skills live under `{DEER_FLOW_HOME}/integrations/skills/{provider}/`; per-user integration credentials remain under `{DEER_FLOW_HOME}/users/{user_id}/integrations/{provider}/{config,data}`
- **Format**: Directory with `SKILL.md` (YAML frontmatter: name, description, license, allowed-tools, required-secrets)
- **Loading**: `load_skills()` recursively scans public, per-user custom, global integration, and legacy custom locations for `SKILL.md`, parses metadata, and reads enabled state from extensions_config.json plus per-user skill state for non-public categories; that directory is a package boundary, so no nested `SKILL.md` is registered as a runtime skill. SkillScan has a deliberately narrower packaging rule: known eval fixtures are permitted as support data, while other nested `SKILL.md` files are reported as package defects. It parses runtime metadata and reads enabled state from extensions_config.json.
- **External reload**: `POST /api/skills/reload` is an admin-only, process-local invalidation hook for trusted MinIO/NFS/CSI writes. `SkillStorage` instances do not cache a catalog — `load_skills()` scans on every call — so the route clears all `(app_config, user_id)` entries and the rendered prompt-section LRU, then waits up to the shared refresh timeout for the existing off-loop single-flight refresh. Each invalidation receives a generation-bound result handle; a successful scan atomically replaces the global enabled-skills cache, while a loader-level failure propagates to the HTTP waiter and preserves the last-known-good global cache. Per-user/config scans capture the refresh version and cannot repopulate shared caches if invalidation occurs while they are loading. A timed-out HTTP wait fails generically while the daemon refresh worker continues. Subsequent runs rescan after a successful reload; active runs keep their existing snapshot. Each Uvicorn worker/Kubernetes Pod must be targeted separately. Direct mount writes bypass install/edit validation, SkillScan, and history, so mounted roots are an operator-controlled trust boundary.
- **Tool policy**: Agent `allowed-tools` declarations apply dynamically only to slash-activated skills and skills captured in `ThreadState.skill_context` through configured `read_file` loads; passive enabled skills and custom-agent/subagent skill allowlists remain discoverable without clamping the baseline toolset. Subagents render only skill discovery metadata at startup and reuse the same adjacent `SkillActivationMiddleware` + `SkillToolPolicyMiddleware` pair as the lead; their configured `skills` field limits discovery and activation instead of eagerly loading bodies or unioning policies. Slash policy is dominant for its run, preventing subsequently read skills from widening explicit authority; autonomous captured skills use the existing union only when no slash source exists. `tool_search` and `describe_skill` stay available as framework discovery infrastructure, while every discovered or promoted business tool still requires active-policy permission for schema visibility and execution; `task` likewise requires an explicit declaration. Each active model call intentionally reloads the full live registry so enable/disable changes, frontmatter edits, and custom/public name-shadow winners take effect without a stale TTL or unsafe direct-path cache; all tool calls produced by that model step reuse the resulting source-and-path-signed decision. Registry failures and all-invalid active sets fail closed, while stale individual paths are skipped when another valid skill remains. This is best-effort behavioral scoping, not a hard security boundary: alternate loading paths are not captured and bounded autonomous context may evict entries.
- **Sandbox projection**: `skills/projection.py` materializes enabled-only trees at `{base_dir}/skills_view/public` and `{base_dir}/users/{user_id}/skills_view/{custom,legacy,integrations}`. It hardlinks files when possible and falls back to copies across filesystems. Storage writes, archive installs, deletes, and toggles rebuild under a cross-process lock; Gateway boot ensures only the shared public view, while each user view is repaired lazily on first sandbox acquire. Managed integration packages are global, but their projected category is per-user because enabled state is isolated. Rebuilds stage a complete tree and reconcile it with per-file atomic replacement, so unrelated enabled skills remain continuously visible; disable/delete paths remove only the affected package before mutating to preserve fail-closed behavior. User projection rebuilds re-read global enable state from disk instead of the process singleton, so a toggle handled by another Gateway worker is reflected on the next acquire. Gateway public-skill toggles take the public projection lock before the shared `extensions_config_write_lock`, re-read an existing config from disk, persist the full model shape, and rebuild before responding; keep this as one worker-owned critical section so MCP writes cannot interleave and request cancellation cannot release either lock while the worker still runs. The shared public steady-state signature check runs without the global projection lock; stale/error paths take the lock and re-check before rebuilding or clearing. User-scope checks remain serialized per user. Category root inodes remain stable so live bind mounts observe content changes without sandbox recreation. Projection failures clear the affected view before raising.
- **Injection (legacy / default)**: Enabled skills are listed in the agent system prompt with full metadata and container paths (`<available_skills>` block). Controlled by `skills.deferred_discovery: false` (default).
- **Deferred discovery** (`skills.deferred_discovery: true`): Skills are listed by name only in a compact `<skill_index>` block, keeping the system prompt prefix-cache friendly. The agent calls the `describe_skill` tool at runtime to fetch full metadata for skills it wants to use, then loads the SKILL.md via `read_file`. Two new modules support this path:
  - `skills/catalog.py` — `SkillCatalog` (immutable, searchable; query forms: `select:a,b`, `+prefix`, free-text regex); `select:` returns all requested skills without a result cap; other modes cap at `MAX_RESULTS=5`.
  - `skills/describe.py` — `build_describe_skill_tool(catalog)` builds the `describe_skill` tool as a closure; `build_skill_search_setup(skills, enabled, ...)` produces a `SkillSearchSetup(describe_skill_tool, skill_names)` that is wired into both the LangGraph agent factory (`agent.py`) and the embedded client (`client.py`).
- **Slash activation**: `/skill-name task` loads that enabled skill's `SKILL.md` for the current model call only. The resolver rejects leading whitespace, missing separators, reserved channel commands (`/new`, `/help`, `/bootstrap`, `/status`, `/models`, `/memory`, `/goal`), disabled skills, and skills outside a custom agent's whitelist.
- **Installation**: `POST /api/skills/install` extracts .skill ZIP archive to custom/ directory
- **Managed integrations**: Lark/Feishu CLI support installs one global official `lark-*` pack as read-only `SkillCategory.INTEGRATION` entries under `/mnt/skills/integrations/lark-cli/...`; enabled flags, app configuration, and OAuth data remain per-user. Install resolves the newest `larksuite/cli` release from GitHub (`releases/latest`) at install time (falling back to a bottom-line pinned version if the lookup fails) rather than hard-coding the pack version; integrity relies on the official host + structural archive guards + a recorded hash of the effective installed tree after shared guidance injection (not a pinned archive-byte SHA, which GitHub does not keep stable). The Gateway image still installs a pinned `@larksuite/cli` binary, so `get_lark_integration_status` surfaces `latest_available_version` and `runtime_version_mismatch` for the UI. AIO installs additionally verify and publish official Linux amd64/arm64 binaries under `{DEER_FLOW_HOME}/integrations/lark-cli/sandbox-cli`, mounted read-only at `/mnt/integrations/lark-cli/runtime`; `/mnt/integrations/lark-cli/config` (app credentials, incl. the long-lived `appSecret`) is mounted **read-only** into the sandbox and `/mnt/integrations/lark-cli/data` (refreshable OAuth tokens) stays writable, both mapping to owner-only per-user credential directories. **Sandbox trust boundary:** those two dirs are still *readable* by arbitrary sandbox processes (the agent's `bash` tool, or code reached via prompt-injection in a tool result), so the app secret and tokens are exposed to sandbox-side code even though they never reach the browser — the read-only config mount only prevents in-sandbox tampering, not read/exfiltration. The sidecar credential-broker (Pattern B, issue #4338) is the fix that removes these plaintext mounts from sandbox execution: set `LARK_CLI_BROKER_IMAGE` on the provisioner (see `docker/lark-cli-broker/`) and the Gateway sends `provision_lark_cli_broker` on sandbox create. The provisioner then runs a `lark-cli-broker` sidecar that owns the per-user `config`/`data` (mounted into the **sidecar only**, at `/var/lark/{config,data}`) and serves the `lark-cli` command surface on Pod loopback (`http://127.0.0.1:8788`); a shim init container (`install-shim`) writes a forwarding `lark-cli` into the shared runtime `emptyDir`, so the sandbox gets `DEERFLOW_LARK_BROKER_URL` + a shim on PATH but **no** credential files. The on-PATH `bin/lark-cli` is a `/bin/sh` launcher that resolves a Python 3 interpreter and execs the Python shim body (`bin/lark-cli-shim.py`) beside it, so broker mode does not silently ENOEXEC on a sandbox image without a `#!/usr/bin/env python3`-resolvable interpreter — it fails loudly (exit 127, actionable message) and can be pinned with `DEERFLOW_LARK_BROKER_PYTHON`. The broker runs `lark-cli` in the sidecar's cwd and cannot see the sandbox filesystem, so cwd is intentionally **not** forwarded and file-I/O subcommands relative to the sandbox cwd are unsupported (command surface only). An optional `DEERFLOW_LARK_BROKER_DENY_SUBCOMMANDS` denylist (comma-separated command prefixes, forwarded from the provisioner) lets the broker refuse secret-dumping subcommands before spawning the binary. `lark_cli_env_overlay(broker=True)` therefore omits `LARKSUITE_CLI_CONFIG_DIR`/`DATA_DIR`; `sandbox_lark_broker_active()` (TTL-cached provisioner `/api/capabilities` probe, tight timeout + longer negative caching on the bash hot path) selects broker vs. binary mode for both the bash env overlay and status. `DEER_FLOW_LARK_CLI_SANDBOX_RUNTIME_DIR` supplies a validated, symlink-free pre-staged runtime for air-gapped deployments. For the remote provisioner (K8s), the runtime binary is otherwise provisioned by an optional init container + shared `emptyDir` (Pattern A): set `LARK_CLI_INIT_IMAGE` on the provisioner (see `docker/lark-cli-init/`) and the Gateway sends `provision_lark_cli_runtime` on sandbox create once the pack is installed, so remote installs skip the Gateway-side GitHub download entirely. Broker (Pattern B) supersedes the init-container binary (Pattern A) when both images are configured. `get_lark_integration_status(check_runtime=True)` surfaces `sandbox_runtime_mode` (`none` / `gateway-download` / `init-container` / `broker`) and `sandbox_runtime_ready` (remote modes read the provisioner `GET /api/capabilities`: `lark_cli_init_image` / `lark_cli_broker_image`) so a green UI can't hide a chat-time `lark-cli: command not found`. Cheap status probes are explicitly not live-verified; users authorize or reconnect through the browser device-flow endpoints instead of running terminal commands.
- **SkillScan**: `packages/harness/deerflow/skills/skillscan/` is the native deterministic scanner for `.skill` archives and agent-managed skill writes. It runs offline before the LLM scanner, emits structured findings (`rule_id`, `severity`, `file`, `line`, `message`, `remediation`, redacted `evidence` — category/analyzer are encoded in the `rule_id` prefix), blocks `CRITICAL`, and passes warning findings into `scan_skill_content()`. `scan_archive_preflight()` / `scan_skill_dir()` are pure sync functions (dispatch off the event loop); `enforce_static_scan()` applies the blocking policy and the `skill_scan.enabled` kill switch. The Python instance-client signal deliberately follows only a one-level, same-scope evidence chain (PR #4265 review): a proven imported constructor bound to a simple name, optional name-to-name alias propagation, rebinding invalidation, and a constructor-supported outbound method or context-manager use; bare canonical-looking names never fall back to module identity. Nested scopes never inherit client handles and inherit only constructor aliases proven stable by a binding-only enclosing-scope prepass. Comprehensions, walrus-bearing statements, annotations, executable expressions inside complex binding targets, unsupported operations, and ambiguous flows produce no finding from this signal; skipped constructs invalidate all names they may bind, while representative false negatives are pinned by `test_python_declared_false_negatives_stay_unreported`. Compound bodies are walked from isolated copies so wrapping code in `if True:` is not a bypass, while copied scope entries, binding-only prepasses, and AST visits consume a deterministic work budget and the walk stops after its first sink. Budget or recursion exhaustion skips only this best-effort signal and retains deterministic findings already collected for the file. Do not add Semgrep/OpenGrep or YAML rule-engine dependencies to the core path; Phase 1 rule specs live in Python constants next to their analyzers in `skillscan/orchestrator.py`.
- **Skill Review Core**: `packages/harness/deerflow/skills/review/` provides read-only package snapshots, deterministic facts, resource/eval analysis, report rendering, and the CLI (`python -m deerflow.skills.review.cli`). It reuses the shared frontmatter helper and SkillScan; it must not import `app.*`, execute target scripts, install dependencies, or call networks. JSON contracts live in `contracts/skill_review/`. The `review_skill_package` built-in tool labels results with `review_subject_entry` and never `skill_context_entry`, so reviewing a target does not activate it, bind its `required-secrets`, or apply its `allowed-tools`. Its model-visible `ToolMessage.content` is a compact JSON payload with untrusted control tags neutralized; the full raw review payload, including Markdown renders, stays in `ToolMessage.artifact`. CI should run the CLI with `--fail-on error --fail-on-incomplete` so blocker/error findings and truncated/not-assessed packages fail the gate. The public `skills/public/skill-reviewer` skill owns semantic readiness review and suggestions only; mutation and runtime experiments remain owned by `skill-creator`.

#### Request-Scoped Secrets (`required-secrets`)

Lets a caller pass per-request, short-lived end-user credentials (e.g. an ERP token) to a skill's sandbox scripts without the value entering the prompt, tool arguments, the executed command string, or traces (issue #3861).

- **Declare**: a skill lists the secrets it needs in `SKILL.md` frontmatter — `required-secrets:` as a string list or `{name, optional}` mappings. `name` is both the lookup key and the env var name exposed to scripts. Parsed by `skills/parser.py::parse_required_secrets` into `Skill.required_secrets` (`SecretRequirement`); malformed entries are dropped with a warning.
- **Carry**: the caller sends values out-of-band in the run request's `context.secrets` mapping (never a message). `runtime/secret_context.py` owns the contract (`SECRETS_CONTEXT_KEY`, `extract_request_secrets`). The existing `context` passthrough carries it to `runtime.context` without mirroring into `configurable`. `build_run_config` still sets `configurable.thread_id` on the context path — the checkpointer requires it. MCP tool interceptors can read the same live carrier from `langgraph.config.get_config()["context"]["secrets"]`; see `docs/MCP_SERVER.md`.
- **Admission and redaction ownership**: `services.py::start_run()` validates both legacy request mappings, `metadata.auth_token` and `config.metadata.auth_token`, before any run or thread persistence. `runtime/secret_context.py::redact_config_secrets()` also removes nested config metadata secrets from observable and persisted config copies; historical `RunResponse.kwargs` applies the same redaction non-mutatively, leaving stored `RunRecord` data unchanged. Keep callers on `config.context.secrets` rather than adding another credential carrier. Scheduled task definitions have no durable credential carrier: `ScheduledTaskService` supplies only `scheduled_task_id`, `scheduled_task_run_id`, and `scheduled_trigger` as run metadata.
- **Bind (point A+)**: `SkillActivationMiddleware._resolve_secret_bindings` recomputes the injection set (`runtime.context[__active_skill_secrets]`) on every model call from two unioned sources, then REPLACES the key. (1) *Slash*: the run's most recent `/skill` activation, persisted as a source on the run context (only the activated skill's **canonical container path**, never its declared secrets) so the whole tool loop after the activation call keeps the binding; a new activation replaces it. Slash reads the genuine user text via `get_original_user_content_text`; `InputSanitizationMiddleware` preserves it (`ORIGINAL_USER_CONTENT_KEY`), so activation fires even after sanitization. (2) *In-context* (autonomous invocation): skills the model actually loaded in this thread — `ThreadState.skill_context` entries. **Both sources resolve the live registry skill by normalized container path on every call** (`_resolve_registry_skill`) and bind only that skill's own declared secrets — enabled + allowlist checked for both; the `secrets-autonomous: false` opt-out (malformed values fail closed to `false`) additionally gates the in-context path but exempts explicit slash. Resolving by registry — not by trusting the source's stored data — is what makes a caller-forged `__slash_skill_secret_source` harmless (`runtime.context` is caller-mergeable; the gateway also strips caller `__`-keys in `build_run_config`), #3938. Authorization is three-gated regardless of activation style: skill **enabled** by the operator × values **supplied per-request** by the caller (`context.secrets`) × names **declared** in frontmatter (∩ semantics). Because the set is recomputed per call, a skill evicted from `skill_context` (capacity) or a caller that stops supplying a value loses injection on the next call. The injected value always comes from the caller's request, never the host environment (scrubbed first — see below), so a declared name that also exists in the host env is safe: the caller's value wins and the host value is dropped (the #3861 per-user-key-overrides-shared-key case). Missing required secrets are logged once per binding change, not injected; binding changes are recorded as a `middleware:skill_secrets` journal event (skill and secret names only, never values).
- **Inject**: `bash_tool` reads the injection set and passes it as `execute_command(env=...)`. Scope is the activation turn/run only — a run without `/skill` activation injects nothing.
- **AIO image requirement**: on `AioSandbox` the env path uses the `bash.exec` API (`POST /v1/bash/exec`), which upstream all-in-one-sandbox only ships since `1.9.3` — older images (including a `latest` tag frozen on the `1.0.0.x` line) 404 the whole `/v1/bash/*` namespace. `AioSandbox` detects the 404, remembers the capability gap on the instance, and fails fast with an actionable upgrade error instead of letting the model retry raw 404s; there is deliberately **no** fallback through the legacy shell path because none keeps the secret values out of the command string (#3921). Regression tests: `tests/test_aio_sandbox.py::TestBashExecUnsupportedFailFast`.
- **Inherited-env scrub**: `execute_command` no longer leaks the Gateway's `os.environ` to skill subprocesses — `env_policy.build_sandbox_env` drops secret-looking names (`*KEY*`/`*SECRET*`/`*TOKEN*`/`*PASS*`/`*CREDENTIAL*`/`*DSN*` + a connection-string denylist like `DATABASE_URL`/`REDIS_URL`/`GH_PAT`, plus no-flag credential sources like `MYSQL_PWD`/`REDISCLI_AUTH`/`PGPASSFILE`/`PGSERVICEFILE`) so platform credentials never reach a skill; a skill that needs one must declare it.
- **Leak surfaces sealed** (verified by a real-gateway e2e run — secret reaches the sandbox but none of these): prompt (value never in a message), trace (`tracing/metadata.py` never copies `context`), checkpoint (secrets live on `runtime.context`, not graph state), audit (journal records names only), stdout (`tools.py::mask_secret_values` redacts injected values from bash output), and **run-record persistence + run API** (`services.py::start_run` stores `redact_config_secrets(body.config)` so `runs.kwargs_json` and `RunResponse.kwargs` never carry the secret).
- **Historical retention**: API response hiding prevents legacy `metadata.auth_token` and `config.metadata.auth_token` from being returned now; it does not delete values already retained in databases, run events, logs, snapshots, exports, or backups. Deployments that ever used either legacy carrier must rotate the credential and clean every retained copy under their retention policy. Restarting or upgrading DeerFlow performs neither action.
- **Scope / non-goals**: no persistence/vaulting — values are request-scoped and never stored server-side, so long-lived use means the caller re-supplies `context.secrets` on each request while the skill stays in `skill_context`; subagents do not inherit the skill injection set. MCP interceptors may independently consume the same supported request-scoped carrier. Tests: `tests/test_skill_request_scoped_secrets.py`, `tests/test_mcp_session_pool.py`.

### Model Factory (`packages/harness/deerflow/models/factory.py`)

- `create_chat_model(name, thinking_enabled)` instantiates LLM from config via reflection
- `utils/oneshot_llm.run_oneshot_llm` tags every invocation with LangGraph's
  `TAG_NOSTREAM`, which is enforced by both LangGraph streaming and
  `RunJournal`. These calls return internal text directly to their caller and
  may run inside a graph node (for example, DBTL setup-question drafting);
  their private prompt and raw structured response never become thread
  messages, while their token usage remains part of the run accounting.
- Supports `thinking_enabled` flag with per-model `when_thinking_enabled` overrides
- Supports vLLM-style thinking toggles via `when_thinking_enabled.extra_body.chat_template_kwargs.enable_thinking` for Qwen reasoning models, while normalizing legacy `thinking` configs for backward compatibility
- Supports `supports_vision` flag for image understanding models
- Config values starting with `$` resolved as environment variables
- Missing provider modules surface actionable install hints from reflection resolvers (for example `uv add langchain-google-genai`)

### vLLM Provider (`packages/harness/deerflow/models/vllm_provider.py`)

- `VllmChatModel` subclasses `langchain_openai:ChatOpenAI` for vLLM 0.19.0 OpenAI-compatible endpoints
- Preserves vLLM's non-standard assistant `reasoning` field on full responses, streaming deltas, and follow-up tool-call turns
- Designed for configs that enable thinking through `extra_body.chat_template_kwargs.enable_thinking` on vLLM 0.19.0 Qwen reasoning models, while accepting the older `thinking` alias
- `cumulative_stream_usage` is an opt-in model setting (default `false`) for endpoints that repeat cumulative token totals on each streaming chunk. The provider converts snapshots to deltas only when a stable completion id is present, isolates interleaved streams by id, and leaves the original usage untouched otherwise. Per-model tracking is lock-protected and cleared on the trailing empty-`choices` frame whether or not that frame carries usage. A soft cap of 1024 ids evicts only entries idle for at least one hour; active streams may temporarily exceed the cap so eviction cannot corrupt their deltas. Regression coverage lives in `tests/test_vllm_provider.py`.

### IM Channels System (`app/channels/`)

Bridges external messaging platforms (Feishu, Slack, Telegram, Discord, DingTalk, GitHub) to the DeerFlow agent via Gateway's LangGraph-compatible API.

**Architecture**: Channels communicate with Gateway through the `langgraph-sdk` HTTP client (same as the frontend), ensuring threads are created and managed server-side. The internal SDK client injects process-local internal auth plus a matching CSRF cookie/header pair so Gateway accepts state-changing thread/run requests from channel workers without relying on browser session cookies.

**Components**:

- `message_bus.py` - Async pub/sub hub (`InboundMessage` → queue → dispatcher; `OutboundMessage` → callbacks → channels)
- `store.py` - JSON-file persistence mapping `channel_name:chat_id[:topic_id]` → `thread_id` (keys are `channel:chat` for root conversations and `channel:chat:topic` for threaded conversations)
- `manager.py` - Core dispatcher: creates threads via `client.threads.create()`, routes commands including `/goal` (setting a goal persists it through Gateway and then routes the objective as a chat turn), keeps Slack/Discord on `client.runs.wait()`, uses `client.runs.stream(["messages-tuple", "values"])` for Feishu/Telegram incremental outbound updates, serializes same-thread Feishu turns in-manager when the channel's `ChannelRunPolicy.serialize_thread_runs=True` so rapid follow-ups queue instead of tripping the runtime busy reply, and switches to `client.runs.create()` (fire-and-forget, returns once the run is `pending`) for channels whose `ChannelRunPolicy.fire_and_forget=True` so long autonomous runs do not hit the SDK default 300s `httpx.ReadTimeout`
  A swallowed streaming failure publishes its final outbound before releasing the inbound dedupe key, so a provider redelivery can retry without overtaking the terminal reply.
  **What may be published from the stream is an allowlist, not a denylist** (`_accumulate_stream_text`): only assistant message types — LangChain serializes `AIMessage.type` as `"ai"` and `AIMessageChunk.type` as `"AIMessageChunk"`, plus the OpenAI-style `"assistant"` spelling for foreign runtimes — become displayable text. The previous rule rejected only payloads whose `type` contained `"tool"` and therefore published everything else, which leaked DeerFlow's hidden model context to every streaming IM channel: `DynamicContextMiddleware` injects recalled memory as a hidden `HumanMessage` (`type == "human"`) and rewrites the user's own turn into a new `HumanMessage`, `DurableContextMiddleware` injects a hidden `<durable_context_data>` `HumanMessage`, and LangGraph fans those state writes out on the `messages-tuple` stream. Proved live on a Buzz relay, which published a `<memory>` fact block and, in another run, a verbatim echo of the user's own message as the assistant's reply. Matching is by prefix (`ai` / `assistant`), never substring, because ordinary words contain `"ai"` (`chain`, `domain`). The message type is resolved by `_stream_payload_type`, which handles both the `model_dump()` shape DeerFlow's own gateway emits and LangChain's `to_json()` constructor shape (whose top-level `type` is the literal `"constructor"`, with the class name at the tail of the `id` path). A bare `str` payload is no longer accepted at all: it carries no type information, so it cannot be attributed to the assistant, and nothing in DeerFlow produces one (`runtime/serialization.py::serialize_messages_tuple` always emits `[message_dict, metadata]`).
- `base.py` - Abstract `Channel` base class (start/stop/send lifecycle)
- `service.py` - Manages lifecycle of all configured channels from `config.yaml`
- `slack.py` / `feishu.py` / `telegram.py` / `discord.py` / `dingtalk.py` - Platform-specific implementations (`feishu.py` tracks the running card `message_id` in memory and patches the same card in place; `telegram.py` accepts inbound text/photos/documents, preserves media captions, hands token-free attachment bytes to the shared upload pipeline, edits the "Working on it..." stream target in place via `editMessageText`, and can optionally send final Markdown replies as Rich Messages through `channels.telegram.rich_messages`; `dingtalk.py` optionally uses AI Card streaming for in-place updates when `card_template_id` is configured, and overrides `receive_file` to download inbound images (`picture`/`richText`) and documents (`file`) by `downloadCode` into the thread uploads bucket, mirroring `feishu.py`)
- `buzz.py` - Buzz (Nostr relay) implementation: one NIP-42-authenticated websocket, pubkey-allowlist + mention/DM/thread-follow gating, streaming replies via in-place kind-40003 edits; requires the `buzz` dependency extra. **Subscription model** (operator-facing version in [IM_CHANNEL_CONNECTIONS.md](docs/IM_CHANNEL_CONNECTIONS.md#buzz-subscription-model)): the relay fans kind-9 chat events out **only** to channel-scoped subscriptions, proved against a live relay — `REQ {"kinds":[9]}` is accepted and answered with `EOSE` but never receives an event (the connector authenticated and then sat silent forever), `REQ {"kinds":[9],"#h":[uuid]}` works, and a multi-value `#h` matches nothing, so it is strictly one REQ per channel (the same shape as Buzz's own `buzz-acp` harness). Every connection therefore rebuilds three kinds of subscription after NIP-42 auth: `buzz-discovery` (`{"kinds":[39000]}`, a historical query returning exactly the channels this identity belongs to, one stored event each then `EOSE` — adding `#p` returns zero, do not "narrow" it), `buzz-membership` (`{"kinds":[44100,44101],"#p":[us],"since":<connect time − `MEMBERSHIP_LOOKBACK_SECONDS`>}`, the relay-signed member-added/member-removed notifications whose `p` tag names the affected member and `h` tag the channel), and one `buzz-chat-<uuid>` per discovered channel. Chat subscriptions open as each kind-39000 arrives; the discovery `EOSE` is the completeness barrier that retries any that failed and warns when discovery found nothing. A kind-44100 for our pubkey subscribes to the new channel **live** (then re-issues discovery so its name/type reach the DM-detection cache, but only when that channel's metadata is actually missing — an unconditional refresh is how a burst of 44100s multiplied into one discovery pass each); a kind-44101 issues `buzz_nostr.close_frame` for exactly that channel's subscription and drops its metadata. **The membership subscription is scoped to LIVE events** and this is load-bearing: buzz-relay *stores* 44100/44101 and serves history newest-first (default limit 2000), so an unscoped filter replayed the whole membership history on every connect — every stored add read as live, re-running discovery once each (M+1 discovery passes × N stored kind-39000 events per connect, observed live as two `channel discovery complete` lines and one channel logged `<unnamed>` because the 44100 path subscribed before its metadata arrived), re-subscribing channels we have since been removed from, and letting a stored 44101 transiently unsubscribe a channel we are still in. `since` is anchored at the moment the socket opened (`_session_started_at`) minus `MEMBERSHIP_LOOKBACK_SECONDS` (60) of slack, which covers both relay clock skew and a membership change published *during* the connect/auth handshake; the slack can only cost an idempotent replay of the last minute. **A relay `CLOSED` frame is recovered, not merely forgotten** — every subscription on the socket fails silently when dropped, so `_handle_closed` re-issues it, bounded by `MAX_RESUBSCRIBE_ATTEMPTS` (3) per subscription id per connection *and per auth epoch* (`_resubscribe_attempts` is reset on session start, on re-auth, and by `stop()`, so pre-auth rejections — which the auth branch already recovers wholesale — never spend the authenticated session's budget). The first retry is immediate (the common case is a one-off hiccup); later ones back off 1s then 2s, awaited inline in the read loop rather than in a background task that could outlive its own socket. **`auth-required:` before this socket has completed its NIP-42 handshake is the expected bootstrap sequence, not a refusal**, and `_handle_closed` short-circuits it ahead of every recovery path: the connector opens its control REQs immediately in case the relay serves unauthenticated reads, a closed relay answers `auth-required:` plus an `AUTH` challenge, and the auth branch re-opens everything. That case is logged at DEBUG and consumes neither the permanent-refusal branch nor the retry budget (a chat subscription is still dropped from `_chat_subscriptions`, since it genuinely is not subscribed and discovery is what re-opens it). Treating it as permanent produced an operator-facing warning claiming discovery/membership tracking was DOWN in the same run where discovery then completed, every channel was subscribed, and a brand-new channel's kind-44100 was picked up one second later. The boundary is the per-socket `_auth_completed` flag, set once the signed AUTH event has been sent and cleared on session entry, session exit, and `stop()`; the same reason *after* that stays loud, and says the subscription is down until the relay's next AUTH challenge or the next reconnect rather than borrowing the non-auth wording. Then `_is_transient_close` decides whether to retry at all: NIP-01/NIP-42 `auth-required:`/`restricted:`/`blocked:`/`mute:`/`invalid:`/`pow:` prefixes and buzz-relay's own removal/revocation prose are permanent (do not fight the relay over a channel that is no longer ours; a post-auth `auth-required:` is recovered by the AUTH branch, not by re-issuing), `rate-limited:`/`error:`/no-reason-at-all and anything unrecognized are transient — the default resolves toward "keep listening" because going silently deaf is the failure this exists to remove, and the attempt budget bounds a wrong guess. A chat `CLOSED` is only ever recovered for a channel already in `_chat_subscriptions`: a `CLOSED` is relay-supplied, so acting on an unknown one would let a relay induce a subscription just by naming a channel. Every subscription that goes unlistened is logged at WARNING, never INFO. Subscription ids are deterministic per channel precisely so one can be replaced or closed without disturbing the others on the socket, and `_chat_subscriptions` is per-socket state cleared on session end, on re-auth (a pre-auth REQ may have been rejected), and by `stop()`. Three bounds on remote-fed state: `MAX_CACHED_CHANNELS` (512) caps the kind-39000 metadata cache, the watermark map, and the resubscribe-attempt map; `MAX_CHANNEL_SUBSCRIPTIONS` (256, well under buzz-relay's own 1024-per-connection ceiling) caps live chat subscriptions — at the cap new channels are refused and named in a warning rather than evicting a working subscription. **Known bound (documented, not fixed):** the relay caps historical delivery at 2000 events per subscription, newest-first, even with a `since`, so >2000 unread messages in a *single* channel across a disconnect loses the oldest — the relay never sends them and the watermark advances past them. That is the one remaining path that can skip; everything else fails toward replay. **Trust model** (operator-facing version in [IM_CHANNEL_CONNECTIONS.md](docs/IM_CHANNEL_CONNECTIONS.md#buzz-trust-model)): every inbound `EVENT` is authenticated at the single `handle_relay_frame` choke point — the NIP-01 id is recomputed from the delivered payload and the BIP-340 Schnorr signature verified against the claimed `pubkey` (`buzz_nostr.verify_event`, pure and total: malformed input returns `False`, never raises) — so `ev["pubkey"]`, the authorization principal for both the allowlist and the `/connect` bind, cannot be forged by a relay the DeerFlow operator does not run. What remains trusted is the *authorship* of kind-39000 channel metadata: any member can sign one, and because per-channel subscriptions are now driven by discovery, a forged kind-39000 has two effects rather than one — it can mark a channel `type: "dm"` (relaxing `require_mention` for that channel) **and** it can induce a chat subscription for a channel of the forger's choosing, since the channels we listen to are exactly the channels we hold metadata for. Neither makes anything be *acted on*: `allowed_users` and per-event signature verification are independent gates, so an induced subscription only means the relay reads its own traffic back to a subscriber that drops it, bounded by `MAX_CHANNEL_SUBSCRIPTIONS` (which refuses rather than evicts, so it cannot displace a real channel). Same for a forged kind-44100, except its `p` tag is re-checked locally so it must at least name us. Closing this needs a configured trusted relay pubkey, which `relay_url` is not. `allowed_users` is deny-by-default (empty = nobody, unlike siblings' empty = everyone), so `start()` logs a WARNING when it is empty and each drop logs at DEBUG. The resubscribe cursor (`since`) is **per channel**, advances only for events that were actually processed, and never past `now + MAX_FUTURE_SKEW_SECONDS`, because it is peer-supplied (`created_at`) and a single future-dated event otherwise made the connector permanently deaf. Per channel rather than global is the safety-critical half: subscriptions are per channel, so one shared cursor is the newest event seen in *any* channel and a busy channel would drag it past a quiet channel's unread messages, skipping them on the next reconnect — measured on a live relay, three channels of one identity sat ~28h apart. Per-channel cursors can only ever cost duplicate delivery (absorbed by the manager's `event_id` dedupe), and an evicted cursor degrades to "no `since`", i.e. the relay's default backlog — both fail toward replay, never toward a miss. Streaming tracks every oversize chunk index (`_stream_targets` for chunk 0, `_stream_tails` for the rest), since the manager republishes cumulative text and reposting `chunks[1:]` per update flooded the channel; all of it is per-connection state cleared by `stop()`, and the remote-fed kind-39000 cache is capped at `MAX_CACHED_CHANNELS`. **`send()` refuses outright to publish text carrying a hidden model-context wrapper** (`<memory>`, `<durable_context_data>`, `<system-reminder>` — `_HIDDEN_CONTEXT_MARKERS`), logging at ERROR and clearing the stream bookkeeping on a blocked `is_final`. This is defense in depth behind the manager's allowlist, and it lives here rather than in a sibling connector because on Buzz a leak is permanent: every streaming update is an immutable public Nostr event, so a corrective edit only changes what clients render while the original leaked event stays on the relay. Matching is on the literal opening tag, so a reply that merely talks about memory is still published
- `github.py` - Webhook-driven GitHub channel. Inbound messages come from `POST /api/webhooks/github`; outbound is log-only because GitHub agents post explicitly with `gh` from their sandbox when they choose to comment or create a PR
- `app/gateway/routers/channel_connections.py` - Browser-facing user connection and disconnect APIs
- `deerflow.persistence.channel_connections` - SQL-backed user-owned connection, optional credential, connect state, and conversation store

**Message Flow**:

1. External platform -> Channel impl -> `MessageBus.publish_inbound()`
   - For GitHub, the webhook router verifies the delivery then calls `fanout_event(bus, ...)`; matching agent bindings publish one `InboundMessage` each instead of a long-polling channel worker.
   - Telegram photo/document updates use the largest photo size or document metadata, preserve `message.caption`, enforce the hosted Bot API's 20,000,000-byte download ceiling before and after download, and never expose the token-bearing Bot API file URL. Downloaded bytes cross the adapter/manager boundary only through `message_bus.INBOUND_FILE_CONTENT_KEY`; the manager consumes that transient field before persisting safe upload metadata.
2. `ChannelManager._dispatch_loop()` consumes from queue
3. For user-owned channel connections, incoming messages carry `connection_id`, `owner_user_id`, and `workspace_id`; `owner_user_id` becomes the DeerFlow run `user_id`, while the raw platform user id remains `channel_user_id`. The Gateway accepts `channel_user_id` only from an internally authenticated channel caller's top-level `body.context`, clears it from both free-form `body.config` sections, and writes it into runtime context only (never `configurable`, which is checkpointed). `bash_tool` exposes it to sandbox commands as the fixed env var `DEERFLOW_CHANNEL_USER_ID` — via a shell-quoted command-string prefix, NOT the `execute_command(env=...)` channel, which is reserved for request-scoped secrets and would switch `AioSandbox` onto the `bash.exec` path (image >= 1.9.3, fresh session per call). Per-call injection keeps group-chat identity correct (one thread/sandbox, many senders) **without depending on the AIO shell's session semantics**: every IM-channel command carries an explicit `export VAR=<id>; ` (valid id) or `unset VAR; ` (empty / non-str / over the 256-char cap). The AIO no-env path reuses a persistent shell session (the reason for the class lock, #1433), so a bare command could otherwise resolve a stale id an earlier sender exported; the `unset` closes the window the length/type guard would open (a dropped id would inherit the previous sender's value). Non-IM runs (no `channel_user_id` in context) are left untouched. Not injected on the Windows local sandbox (its PowerShell/cmd.exe fallback has no `export`/`unset`). Propagates across `task` delegation: `task_tool` captures the dispatching turn's id and the subagent executor forwards it into the subagent's runtime context, same as the guardrail attribution fields. The runtime-context value is authorization-grade at the Gateway/guardrail boundary, but the exported shell variable remains informational because any bash command can overwrite its own environment; skills must not treat the shell variable itself as authenticated identity. Tests: `tests/test_gateway_services.py`, `tests/test_channel_user_id_env.py`
4. For chat: look up/create thread through Gateway's LangGraph-compatible API
5. Feishu/Telegram chat: `runs.stream()` → accumulate AI text → publish multiple outbound updates (`is_final=False`) → publish final outbound (`is_final=True`)
6. Slack/Discord chat: `runs.wait()` → extract final response → publish outbound
   6b. GitHub chat (`ChannelRunPolicy.fire_and_forget=True`): `runs.create()` returns once the run is `pending`; the manager does not wait for the final state and does not publish an outbound. The agent posts its own reply mid-run via `gh` from the sandbox. `ConflictError` on a busy thread still trips the standard `THREAD_BUSY_MESSAGE` path (log-only on GitHub); when the channel's policy also sets `buffer_followups_on_busy=True` (GitHub's default — see "Follow-up buffering while busy" below), the triggering message is additionally captured into a per-thread buffer instead of only logged, so a concurrent comment is not silently dropped.
7. Feishu channel sends one running reply card up front, then patches the same card for each outbound update (card JSON sets `config.update_multi=true` for Feishu's patch API requirement). Messages already sent inside an existing Feishu topic carry a compact source-message preview in that card, and queued same-thread follow-ups patch their own source message's card from queued → running → final without falling back to the generic busy reply.
8. Telegram streaming: the "Working on it..." placeholder message is registered as the stream target; non-final updates `editMessageText` it in place (channel-side throttle: 1s in private chats, 3s in groups due to Telegram's 20 msg/min group cap; 4096-char truncation; rate-limited updates dropped); the final update performs the last edit and splits >4096 texts into follow-up messages
9. DingTalk AI Card mode (when `card_template_id` configured): `runs.stream()` → create card with initial text → stream updates via `PUT /v1.0/card/streaming` → finalize on `is_final=True`. Falls back to `sampleMarkdown` if card creation or streaming fails
10. For commands (`/new`, `/status`, `/models`, `/memory`, `/goal`, `/help`): handle locally or query Gateway API
11. Outbound → channel callbacks → platform reply
    - GitHub is the exception: the channel logs the final assistant message and does **not** auto-post it to GitHub. Agents use the sandbox `gh` CLI (`gh issue comment`, `gh pr comment`, `gh pr create`, etc.) for intentional writeback, so silence is cheap when several agents fan out on the same event.

**Owner-scoped file storage**: inbound files, uploads, and output artifacts are staged under the DeerFlow owner's bucket so they land where the agent run reads/writes (`users/{user_id}/threads/{thread_id}/user-data/{uploads,outputs}`). `ChannelManager._handle_chat` resolves the storage owner once via `_channel_storage_user_id(msg)` (sanitized owner id, falling back to `safe(msg.user_id)` for unbound auth-enabled channels — mirroring `_resolve_run_params`'s run identity; `None` only when no identity is available) and threads it as the `user_id=` kwarg through the file pipeline:

- `Channel.receive_file(msg, thread_id, user_id=...)` — owner-bound channels persist downloaded files under the owner's bucket instead of the default bucket
- `_ingest_inbound_files(...)` and the underlying `ensure_uploads_dir` / `get_uploads_dir` — owner-scoped via the same kwarg
- `_resolve_attachments` / `_prepare_artifact_delivery` — resolve output artifacts from the bound owner's bucket
  The cached value is reused for both the blocking (`runs.wait`) and streaming (`_handle_streaming_chat`) paths, so uploads and artifact delivery always target the same bucket even if a channel returns a rewritten `InboundMessage` from `receive_file`. The bucket id matches the memory bucket resolved by `_resolve_memory_user_id` (both normalize through `make_safe_user_id`).

**Configuration** (`config.yaml` -> `channels`):

- `langgraph_url` - LangGraph-compatible Gateway API base URL (default: `http://localhost:8001/api`)
- `gateway_url` - Gateway API URL for auxiliary commands (default: `http://localhost:8001`)
- In Docker Compose, IM channels run inside the `gateway` container, so `localhost` points back to that container. Use `http://gateway:8001/api` for `langgraph_url` and `http://gateway:8001` for `gateway_url`, or set `DEER_FLOW_CHANNELS_LANGGRAPH_URL` / `DEER_FLOW_CHANNELS_GATEWAY_URL`.
- Per-channel configs: `feishu` (app_id, app_secret), `slack` (bot_token, app_token), `telegram` (bot_token, optional `rich_messages` for final Markdown Rich Messages), `dingtalk` (client_id, client_secret, optional `card_template_id` for AI Card streaming), `github` (operator kill-switch `enabled`, plus `default_mention_login` for mention-required GitHub triggers), `buzz` (relay_url, private_key)

**User-owned channel connections** (`config.yaml` -> `channel_connections`):

- Disabled by default. It is a user-binding layer on top of the existing `channels.*` runtime config, not a replacement for provider bot credentials.
- No public IP, OAuth callback URL, or provider webhook route is required by the current implementation.
- Telegram uses a deep-link `/start <code>` flow over the existing long-polling worker. Slack, Discord, Feishu/Lark, DingTalk, WeChat, and WeCom use `/connect <code>` over their existing outbound channel workers.
- WeChat timing settings (`polling_timeout`, `polling_retry_delay`, `qrcode_poll_interval`, `qrcode_poll_timeout`) accept only positive finite seconds; invalid values fall back to their defaults so polling cannot enter a hot loop or sleep forever.
- Frontend APIs: `GET /api/channels/providers`, `GET /api/channels/connections`, `POST /api/channels/{provider}/connect`, and `DELETE /api/channels/connections/{connection_id}`.
- Browser APIs remain protected by normal Gateway auth/CSRF. Provider messages arrive through the already-configured channel workers.
- Provider-level `connection_status` reflects the user's newest connection row. With no binding it is `not_connected`, except in auth-disabled local mode where a configured running channel reports `connected` because all channel messages already route to the default user.
- Slack replies use the configured operator bot token from `channels.slack` unless per-connection credentials are present; unreadable or corrupt stored credentials are treated as unavailable.
- Telegram, Slack, Discord, Feishu/Lark, DingTalk, WeChat, and WeCom workers resolve incoming platform identities to connection records before reaching `ChannelManager`.
- **Connect-code ordering vs `allowed_users`**: inbound workers consume a valid `/connect <code>` (or Telegram `/start <code>`) **before** applying the `allowed_users` filter, so a newly allowlisted-but-unbound user can bootstrap their first bind via the browser flow. Consequence: `allowed_users` is **not** a bind-time defense — any sender who possesses a valid code can consume it (not only allowlisted users). The bind security model rests on the code's confidentiality: `secrets.token_urlsafe(16)`, 600 s TTL, one-time `consume_oauth_state`, and codes surfaced only in the initiating browser (never echoed to chat). `allowed_users` still gates ordinary (non-bind) messages.
- **Single-active-owner transfer semantics**: an external identity is keyed by `(provider, external_account_id, workspace_id)`. The latest successful bind wins — `upsert_connection` revokes other owners' active rows for the same identity (ownership transfer). This invariant is enforced at the DB layer by the partial unique index `uq_channel_connection_active_identity` (`WHERE status != 'revoked'`), so concurrent connects from different owners cannot both end `connected`; the losing writer retries against the now-visible state. `find_connection_by_external_identity` therefore resolves deterministically.
- See `backend/docs/IM_CHANNEL_CONNECTIONS.md` for provider setup, operational notes, and the architecture diagrams (connect-code flow, single-active-owner transfer, sync vs streaming dispatch, owner-scoped file storage pipeline).

**GitHub event-driven agents** (webhook-driven IM channel):

- Custom agents declare a `github:` block in their `config.yaml` to bind to repos and event triggers; the webhook route is fail-closed by default (mounted only when `GITHUB_WEBHOOK_SECRET` is set) and exempt from auth/CSRF because authenticity is enforced by HMAC.
- Outbound is **log-only** by design: each agent posts its own reply mid-run via the `gh` CLI from its sandbox, so the manager uses `fire_and_forget=True` and `runs.create()` returns once pending.
- **Follow-up buffering while busy** (issue #4121): because outbound is log-only, the pre-existing `THREAD_BUSY_MESSAGE` reply on a `ConflictError` was invisible to the commenter — a comment posted while a run was already active looked like it had been silently ignored. When `ChannelRunPolicy.buffer_followups_on_busy=True` (GitHub's default), a `ConflictError` on `runs.create()` now also appends the triggering message to a per-thread, in-memory buffer (`ChannelManager._followup_buffers`) — deduped by GitHub delivery id, capped at `FOLLOWUP_BUFFER_MAX_PER_THREAD` (20, oldest dropped with a WARNING log on overflow). The first successful `runs.create()` on a thread now captures its `run_id` and spawns a background watcher that subscribes to that run's `StreamBridge` stream; once it observes `END_SENTINEL`, the watcher drains up to `FOLLOWUP_DRAIN_BATCH_SIZE` (10) buffered entries into one `<followups-while-busy>`-wrapped input and fires a follow-up `runs.create()` — itself watched the same way, so a backlog deeper than one batch chains into further drain cycles instead of growing one unbounded input. If that follow-up `runs.create()` itself hits `ConflictError` (e.g. a manual Web UI turn or a scheduled run raced onto the same thread), the batch is requeued rather than lost, and is retried whenever this manager next successfully creates and watches a run on that thread. Reactions/acknowledgment (e.g. GitHub's `eyes`/`confused` reaction API) on buffered comments are intentionally **out of scope** for this mechanism and left to a follow-up — comments are coalesced silently. **Plumbing**: the watcher needs the Gateway's `StreamBridge` singleton, which `ChannelManager` did not previously have access to; it is threaded from `app.py`'s lifespan (where `app.state.stream_bridge` is already set by `langgraph_runtime`) through `start_channel_service(get_stream_bridge=...)` → `ChannelService.__init__` → `ChannelManager.__init__`, as a zero-arg closure mirroring the existing `launch_run=lambda **kwargs: launch_scheduled_thread_run(app=app, **kwargs)` pattern used for `ScheduledTaskService` in the same lifespan function. A `ChannelManager` constructed without it (e.g. directly in a test) still buffers safely — it just has no watcher to auto-drain. **Scope limitation**: the buffer and watcher state are per-process, in-memory. Under `GATEWAY_WORKERS>1` or multi-pod, a follow-up comment routed to a different worker process than the one running the busy thread's agent will not see that buffer. This is a known, deliberately deferred limitation with the same shape as the cross-pod gap described for issue #4120 (a shared buffer store or IM-leader election would be needed to close it) — single-process/single-pod deployments, the safe default, see no correctness issue from this, only the documented per-process scope.
- See [backend/docs/GITHUB_AGENTS.md](docs/GITHUB_AGENTS.md) for the architecture diagrams: webhook → fan-out → `InboundMessage` dispatch, `preferred_thread_id = UUID5(repo, number, agent_name)` thread determinism, mention-handle precedence chain, GH token lifecycle via `GH_TOKEN`/`GITHUB_TOKEN` per-call `extra_env`, and the narrow `ConflictError` (HTTP 409) thread-create race recovery.

### Memory System (`packages/harness/deerflow/agents/memory/`)

**Components**:

- `updater.py` - LLM-based memory updates with fact extraction, whitespace-normalized fact deduplication, optimistic revision checks, and repository change sets
- `queue.py` - Debounced update queue (per-thread deduplication, configurable wait time); captures `user_id` at enqueue time so it survives the `threading.Timer` boundary
- `prompt.py` - Prompt templates for memory updates
- `storage.py` - File repository with one user-global summary JSON, agent-owned single-fact Markdown, target-only journaled changes, strict fact validation, shared-user plus per-fact optimistic revisions, lock-protected migration, deep-copy caching, and a RetrievalPort adapter boundary
- `retrieval.py` - Built-in scope-aware SQLite FTS5/BM25 adapter; it stores only rebuildable derived data and can be disabled with an empty `retrieval_adapter`. Chinese jieba tokenization is optional via the backend `memory-zh` extra; without it the adapter uses SQLite unicode tokenization and the substring fallback. A corrupt persistent derived database is deleted and recreated once before falling back to substring retrieval. The Gateway closes the derived SQLite connection after its shutdown flush; reads and writes remain serialized by the adapter lock, with connection pooling deferred as a performance follow-up.
- `tools.py` - Tool-driven memory mode (`memory_search`, `memory_add`, `memory_update`, `memory_delete`) using the same storage/update primitives

**Per-User Isolation**:

- Memory is stored per-user at `{base_dir}/users/{user_id}/memory.json`
- Per-agent facts at `{base_dir}/users/{user_id}/agents/{agent_name}/facts/{sha256-prefix}/{fact-id}.md`, where the prefix is the first two hexadecimal characters of `SHA-256(fact_id)`; there is no per-agent `memory.json`
- Custom agent definitions (`SOUL.md` + `config.yaml`) are also per-user at `{base_dir}/users/{user_id}/agents/{agent_name}/`. The legacy shared layout `{base_dir}/agents/{agent_name}/` remains read-only fallback for unmigrated installations
- Middleware mode captures `user_id` via `resolve_runtime_user_id(runtime)` at enqueue time; tool mode resolves `user_id` and `agent_name` from `ToolRuntime.context` via the same helper so both Gateway and standalone LangGraph Server runs stay scoped to the authenticated user and active custom agent
- The `/api/memory*` endpoints resolve the owner through `_resolve_memory_user_id(request)`: trusted internal callers (IM channel workers carrying the `X-DeerFlow-Owner-User-Id` header, e.g. a bound `/memory` command) act for the connection owner; browser/API callers fall back to `get_effective_user_id()`. The header is only honored after `AuthMiddleware` validated the internal token, mirroring `get_trusted_internal_owner_user_id` used by the threads router
- In no-auth mode, `user_id` defaults to `"default"` (constant `DEFAULT_USER_ID`)
- Absolute `storage_path` in config opts out of per-user isolation
- **Migration**: Run `PYTHONPATH=. python scripts/migrate_user_isolation.py` to move legacy `memory.json`, `threads/`, and `agents/` into per-user layout. Supports `--dry-run` (preview changes) and `--user-id USER_ID` (assign unowned legacy data to a user, defaults to `default`).

**Data Structure**:

- **User Context**: `workContext`, `personalContext`, `topOfMind` (1-3 sentence summaries)
- **History**: `recentMonths`, `earlierContext`, `longTermBackground`
- **Global JSON**: `{base_dir}/users/{user_id}/memory.json` stores only `version`, shared revision/time, `user`, and `history`; it never stores facts or a fact index
- **Facts**: Schema-v2 Markdown documents under `agents/{agent_name}/facts/{sha256-prefix}/{fact-id}.md`; YAML front matter contains structure and the body contains the atomic fact
- **Default agent compatibility**: DeerMem resolves an omitted `agent_name` to the reserved `__default__` fact bucket at the manager boundary. The sentinel is accepted only by DeerMem storage and is outside the custom-agent name grammar, so a real custom `lead-agent` remains isolated. Public agent identifiers are case-insensitive and canonicalized to lowercase before storage
- **Compatibility view**: direct global storage reads return `facts: []`, while DeerMem Manager/API reads select the explicit agent or reserved default and return its facts, so existing Settings and embedded-client schemas remain stable. Markdown keeps structured `source` metadata internally; the manager projects it to the historical string field before returning a public document
- **Incremental result contract**: `FileMemoryStorage.apply_changes()` returns `complete: false` plus `upsertedFacts`/`deletedFactIds`; it never presents a partial cache as a complete memory document. Public compatibility callers explicitly reload a fresh complete view only where their response contract requires it, including after successful disjoint-create rebases
- **Repository**: `get/list/upsert/delete_fact`, `apply_changes`, summary operations, migration, index lifecycle/status, and scoped search. `apply_changes` and direct fact CRUD touch only target Markdown files; direct fact CRUD accepts separate expected user-memory and fact revisions. Supplied summary child keys merge over their persisted section, while import normalizes complete replacement sections first. Whole-document `load/save` remains for compatibility but validates the complete `facts` list and diffs it before persistence. An unscoped manager clear first migrates facts from unread legacy agent JSON without adopting potentially conflicting summaries, then removes the global summaries and every agent's canonical facts while preserving agent configuration; an explicit agent clear removes only that bucket's facts and preserves the shared summaries

**Workflow**:
- `memory.mode: middleware` (default) keeps the passive path: `MemoryMiddleware` filters messages (user inputs + final AI responses), captures `user_id` via `resolve_runtime_user_id(runtime)`, queues conversation with the captured `user_id`, and the debounced background thread invokes the LLM to extract context updates and facts using the stored `user_id`. `DynamicContextMiddleware` passes the same resolved identity to the memory read path. On standalone Agent Server runs, server-owned auth identity is also resolved during lead-agent construction, normalized through `make_safe_user_id` for DeerFlow storage, and explicitly reused for custom-agent config/SOUL, user skills, skill policy, and prompt assembly; ordinary client `user_id` values cannot override `langgraph_auth_user_id`. On the embedded Gateway path, `inject_authenticated_user_context` removes client-supplied `langgraph_auth_user` / `langgraph_auth_user_id` from both RunnableConfig sections before graph construction, so those reserved fields cannot impersonate Agent Server auth.
- The optional `openviking` backend under
  `packages/harness/deerflow/agents/memory/backends/openviking/` is a
  remote-only HTTP adapter. Select it with
  `memory.manager_class: openviking` and keep `memory.mode: middleware`. It
  commits filtered turns to OpenViking Sessions and maps remote memory search
  results into the shared contract. It hashes `(user_id, agent_name)` into a
  safe OpenViking trusted-user identity for hard scope isolation and keeps
  bounded message watermarks below
  `{storage_path}/openviking/sessions/`. The watermark combines a constant-size
  ordered-prefix digest for append-only histories with a bounded recent-ID
  fallback for compaction and separately records submitted and committed
  progress. Once batch submission succeeds, a later update never resubmits
  those messages or retries an ambiguous commit; a future batch can commit the
  still-open Session together with new messages. Schema-v2 recent-ID
  watermarks migrate without duplicating their anchored history. The shared
  HTTP client has explicit total/keep-alive connection limits and jittered
  exponential retry delays, and its configuration representation omits the API
  key. Session locks are weakly cached, async entrypoints offload synchronous
  HTTP and file IO, and graceful shutdown rejects new work before draining all
  in-flight client operations within its timeout. It does not implement
  DeerMem fact CRUD/import/export and must not import the OpenViking embedded
  runtime.
- `memory.mode: tool` skips `MemoryMiddleware` and registers `memory_search`, `memory_add`, `memory_update`, and `memory_delete` on the agent. The model decides when to search, add, update, or delete facts; this is opt-in/experimental and should not be described as better than middleware mode without eval evidence.
- Both modes share `FileMemoryStorage`, per-user/per-agent isolation, manual CRUD primitives, and the updater backend. Injection is mode-aware: middleware mode injects global `user`/`history` summaries plus the selected agent's facts, while tool mode injects only the global summaries and leaves every agent fact behind `memory_search` to avoid duplicating automatically injected and retrieval-returned context. `memory.injection_enabled: false` suppresses the complete block in either mode.
- Middleware extraction classifies proposed facts with extraction-only `scope`/`durability`/`authority` labels. `_apply_updates` accepts only `user` + `durable` + `descriptive` new/consolidated facts, accepts only wholly user-scoped summary prose with `authority=descriptive`, and rejects missing labels per item without aborting unrelated updates. Contradiction removals use object entries with `id`, `scope`, `reason`, and optional zero-based `replacementFactIndex`; task/project removals fail closed, and a paired removal runs only when the referenced replacement survives the scope/confidence gates, deduplication, and max-fact trim under another fact ID. The labels are not persisted, so no storage migration is required. Staleness removals retain their independent candidate/cap guardrails, while tool-mode CRUD remains outside this extraction gate. Custom `memory.backend_config.prompts_dir` templates (including per-agent overrides) must carry the same classification fields; an un-migrated template makes the fail-closed gate reject every extraction-driven write, observable only through `rejected_by_scope_gate` and the >60% fact-rejection warning.
- Middleware mode queue debounces (30s default), batches updates, and commits global summaries plus the selected/default agent's fact delta through a user-level lock, optimistic user-memory revisions, per-fact revisions, and a recoverable target-file journal. Only explicitly marked point operations may rebase a stale shared revision, and only while every addressed fact still satisfies its original absent/revision precondition. Snapshot-derived clear/trim/consolidation operations instead reload the complete document and recompute their intent on a manifest conflict, with a bounded retry. Typed manifest/fact conflict subclasses keep that decision independent of exception text, and same-ID creates and stale same-fact writes fail. Scope-lock objects are weakly cached so inactive users do not grow a process-lifetime map. Cache validation does not scale with the fact-file count: its token combines the shared JSON's `(mtime_ns, size, revision)`, so the persisted revision invalidates stale caches even when a coarse-mtime filesystem reports identical metadata for same-size writes; direct out-of-band Markdown edits require `reload()`. Atomic replacement also syncs the parent directory on POSIX so the rename is durable. DeerMem translates private storage conflict/corruption exceptions to the backend-neutral MemoryManager contract; the Gateway maps them to HTTP 409 and a stable HTTP 500 response respectively. A normal default-manager read automatically migrates legacy facts from the global JSON into `__default__`; it also adopts the earlier implicit `lead-agent` fact bucket only when that directory has no custom-agent `config.yaml`, and rejects unexpected files instead of deleting them. The v1-to-v2 migration is one-way for the running application: operators must stop DeerFlow and snapshot the configured storage root before upgrade. Before any destructive v2 write, every migrated JSON source is durably retained as `{manifest_filename}.v1.bak`; a missing-write or mismatched existing backup aborts without modifying v1 data. Legacy per-agent JSON is deleted only after its non-empty summaries are safely adopted or confirmed identical; summary conflicts keep the source file and fail loudly.
- **Proactive Markdown migration CLI**: from `backend/`, run `PYTHONPATH=. python scripts/migrate_memory_markdown.py --all-users --dry-run` to audit and omit `--dry-run` to migrate before serving traffic. Use repeated `--user-id` values when selecting exact original identities, especially standalone raw IDs containing `@` or other characters that are normalized in directory names; `--storage-path` selects a non-default DeerMem root. The CLI reuses `FileMemoryStorage.migrate`, is idempotent, continues across per-user failures, and exits non-zero if any user fails. It is optional because the first normal read still performs the same migration automatically.
- `retrieval_adapter` owns indexing and retrieval. `fts5` is the DeerMem default and uses a persistent derived SQLite index under `.retrieval/`; an empty value disables the adapter and selects `substring_fallback`. File storage sends upsert/remove notifications for normal writes and both explicit and lazy migrations after releasing durable storage locks, then delegates search. Gateway startup schedules `DeerMem.warm_retrieval()` as a background full rebuild so readiness is not delayed, while a first search lazily rebuilds its exact scope until warm-up completes. Individual malformed facts are logged and skipped without triggering repeated full scans; only a fatal adapter rebuild failure keeps lazy retry enabled. During shutdown, the Gateway waits at most one second for this derived rebuild and leaves the full configured timeout to the canonical memory flush; if the rebuild is still active, its adapter remains open until process exit. Adapter failures mark the scope dirty and fall back to canonical substring search until rebuilding succeeds. `FileMemoryStorage` owns and closes the adapter so higher layers do not reach into private storage state.
- Staleness pass (same LLM invocation as the regular updater, no extra API call): when `staleness_review_enabled` is `true` and at least `staleness_min_candidates` aged facts exist, `_select_stale_candidates` selects facts older than their individual review window (`expected_valid_days`, or the global `staleness_age_days` fallback) that are not in `staleness_protected_categories` (default: `correction`), surfaces them in the prompt with a `valid:Nd` annotation, and the LLM judges each as KEEP, REMOVE, or EXTEND. REMOVE entries go in `staleFactsToRemove`; EXTEND entries go in `staleFactsToExtend` with an `extend_by_days` value, which sets the fact's `expected_valid_days` to `min(days_since_created + extend_by_days, staleness_max_extension_days)`. The LLM assigns `expected_valid_days` when creating a fact; it is clamped at write time to `staleness_age_days × staleness_max_lifetime_multiplier` (creation cap). `_apply_updates` enforces the guardrail unconditionally at apply time: it intersects both the removal and extension sets with `_select_stale_candidates` output before applying the per-cycle cap (`staleness_max_removals_per_cycle`), so protected and non-aged facts can never be targeted regardless of model behavior or the feature flag setting. Facts the LLM proposed for removal are excluded from extension even if the per-cycle cap prevented their actual deletion that cycle. Extensions use an absolute ceiling (`staleness_max_extension_days`) rather than the creation multiplier so a deliberate review decision can advance the window beyond the initial cap while preventing `timedelta` overflow from a malformed `extend_by_days`.
- Consolidation pass (same LLM invocation as the regular updater, no extra API call): when `consolidation_enabled` is `true` and at least one category holds `consolidation_min_facts` or more facts, `_select_consolidation_candidates` identifies fragmented categories and surfaces at most `consolidation_max_groups_per_cycle` of them (largest first) in the prompt. The LLM decides which groups to merge and proposes a synthesised fact per group. `_apply_updates` enforces guardrails: source IDs must exist and must not overlap across groups, group size is capped at `consolidation_max_sources`, the merged fact's confidence cannot exceed the source maximum, and facts below `fact_confidence_threshold` are not written. The merged fact carries the newest source's `createdAt` (so the staleness clock reflects the underlying information, not synthesis time) and inherits `expected_valid_days` set so the merged fact is re-reviewed at the earliest source review deadline (`min(createdAt + effective_lifetime)` across sources, where a source's effective lifetime is its `expected_valid_days` or the global `staleness_age_days` fallback for legacy facts without one - so a legacy source's default window is not swallowed by a long-lived sibling), relative to the merged `createdAt`, clamped to a minimal positive window if a source is already past its deadline, then capped at the creation-time `staleness_max_lifetime_multiplier`; this keeps a volatile or legacy sub-detail from inheriting a stable source's long window and escaping staleness review for years, while a merge of uniformly stable sources does not re-enter review prematurely.
- Next interaction injects selected facts + context into `<memory>` tags in the system prompt when `injection_enabled` is true.

**Run-level memory identity**:

- Every Gateway run with an effective hidden memory block hashes the exact `HumanMessage.content`, including the `<memory>` wrapper, and records one `context:memory` event through its run-scoped `RunJournal`. Later runs and checkpoint-based branches reuse the frozen message without reloading memory; goal continuations are deduplicated to one event per run.
- A first-run block is trusted only when it comes from `DynamicContextMiddleware`'s current update. A reused block must have existed in the checkpoint before the run, and the Gateway strips dynamic-context markers from untrusted input so a caller cannot forge the identity event by reusing a known message ID.
- The production consumer is the existing debug/audit endpoint `GET /api/threads/{thread_id}/runs/{run_id}/events?event_types=context:memory`. Event content has exactly one field, `content_sha256`, which operators use to compare the effective memory identity across runs. The full memory text stays in checkpoint state and is not duplicated into `run_events`.

**Token counting** (`packages/harness/deerflow/agents/memory/prompt.py`):

- `_count_tokens` budgets the injection. In default `tiktoken` mode, the encoding is loaded lazily and cached.
- Failed tiktoken loads are cached with a timestamp. During the fixed cooldown (`_TIKTOKEN_RETRY_COOLDOWN_S`, 600s), callers fall back to char estimation immediately instead of re-triggering the blocking BPE download; after the cooldown, transient outages can self-heal without a restart.
- In-flight loads are cached as a LOADING sentinel so concurrent callers fall back instead of spawning more blocking threads.
- Set `memory.token_counting: char` to skip tiktoken entirely and use the network-free CJK-aware char estimate.

Focused regression coverage for the updater lives in `backend/tests/test_memory_updater.py`.

**Configuration** (`config.yaml` → `memory`):

- `enabled` / `injection_enabled` - Master switches
- `mode` - Operation mode: `middleware` (default passive background extraction) or `tool` (experimental model-driven memory tools). Modes are mutually exclusive.
- `storage_path` - DeerMem storage root; one global summary JSON lives under each user and Markdown facts remain under agent buckets
- `storage_class` - `file` or a dotted `MemoryStorage` class; invalid persistent backends fail fast
- `strict_user_scope` - Require `user_id` for all storage access (default `false` for no-auth/legacy compatibility)
- `manifest_filename` - User-global summary JSON filename (kept for configuration compatibility)
- `file_lock_timeout_seconds` - Scope-lock wait; Markdown facts and the recovery journal are required storage invariants rather than configurable modes
- `retrieval_adapter` - `fts5` by default, empty to disable, or a dotted factory receiving `DeerMemConfig` and returning a retrieval-port implementation
- `debounce_seconds` - Wait time before processing (default: 30)
- `shutdown_flush_timeout_seconds` - Hard budget (seconds) reserved for draining the memory backend's pending-update buffer on Gateway graceful shutdown (default: 30; 1–300). Each pending item does one LLM call, so large IM batches may need more. The Gateway lifespan calls `MemoryManager.shutdown_flush(timeout)` after channels/scheduler stop and after waiting at most one additional second for the derived retrieval warm-up; the backend short-circuits on an idle buffer, so the host calls it unconditionally (no pending/processing gate). The retrieval wait does not reduce this canonical flush budget. The combined shutdown hooks, brief retrieval wait, flush budget, and scheduling margin must fit inside the pod's K8s `terminationGracePeriodSeconds` (gateway Helm chart default: 45s) or K8s SIGKILLs the drain mid-flight.
- `model_name` - LLM for updates (null = default model)
- `max_facts` / `fact_confidence_threshold` - Fact storage limits (100 / 0.7)
- `max_injection_tokens` - Token limit for prompt injection (2000)
- `token_counting` - Token counting strategy for the injection budget: `tiktoken` (default, accurate but may download BPE data from a public endpoint on first use — can block for a long time in network-restricted environments, see issues #3402/#3429) or `char` (network-free CJK-aware char estimate, never touches tiktoken)
- `staleness_review_enabled` - Enable proactive staleness pruning of aged facts (default: `true`; only triggers when aged candidates exist)
- `staleness_age_days` - Age in days before a fact becomes a staleness candidate (default: 90; range: 30–365)
- `staleness_min_candidates` - Minimum aged candidates required to trigger a review cycle (default: 3; range: 1–50)
- `staleness_max_removals_per_cycle` - Maximum facts removed in a single cycle; lowest-confidence entries are kept when the LLM requests more (default: 10; range: 1–50)
- `staleness_protected_categories` - Fact categories that are never pruned by staleness review (default: `["correction"]`)
- `staleness_max_lifetime_multiplier` - Creation-time cap multiplier for a fact's LLM-assigned `expected_valid_days`: stored value is clamped to `staleness_age_days × multiplier` so the model cannot defer first review indefinitely (default: 20.0; range: 1.0–100.0). Default 20.0 (90 × 20 = 1800 d ≈ 5 years) is generous enough to support the very-stable prompt tier without needing multiple review cycles to escape the cap.
- `staleness_max_extension_days` - Absolute upper bound (in days) on `expected_valid_days` after a lifetime extension (`staleFactsToExtend`). Applied at write time as `min(days_since + extend_by, staleness_max_extension_days)`. Uses an absolute ceiling rather than the multiplier because extensions are deliberate review decisions; prevents `timedelta` overflow and LLM misfire from permanently deferring a fact (default: 3650 = 10 years; range: 90–36500).
- `consolidation_enabled` - Enable memory consolidation (default: `true`; no extra API call — runs in the same LLM invocation as the normal memory update)
- `consolidation_min_facts` - Minimum facts in a category to trigger consolidation review (default: 8; range: 3–30)
- `consolidation_max_groups_per_cycle` - Maximum categories the LLM can merge in one cycle (default: 3; range: 1–10; also controls the LLM's prompt instruction)
- `consolidation_max_sources` - Maximum source facts per merge group; prevents over-merging (default: 8; range: 2–20)
- `watermark_max_keys` - Soft cap on the in-memory conversation-watermark cache (one entry per distinct thread/user/agent). A bounded LRU: when over capacity the least-recently-used entry is dropped, and a dropped key re-extracts one batch on that thread's next turn (same as a restart). Bounds memory in long-lived gateways handling many threads (default: 4096; 0 = unbounded)

### Reflection System (`packages/harness/deerflow/reflection/`)

- `resolve_variable(path)` - Import module and return variable (e.g., `module.path:variable_name`)
- `resolve_class(path, base_class)` - Import and validate class against base class

### Schema Migrations (`packages/harness/deerflow/persistence/migrations/`)

DeerFlow's application tables (`runs`, `threads_meta`, `feedback`, `users`, `run_events`, plus the four `channel_*` tables) are owned by alembic via a **hybrid bootstrap** strategy. LangGraph's checkpointer tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`) live in the same database but are owned by LangGraph and excluded from alembic's view via `migrations/_env_filters.py::include_object`.

**Convention**: every ORM model change (new column, new table, new index) MUST ship as an alembic revision under `migrations/versions/`. The Gateway runs `alembic upgrade head` automatically on startup; users do not run `alembic` manually in production.

**Hybrid bootstrap** (`persistence/bootstrap.py::bootstrap_schema`, invoked from `persistence/engine.py::init_engine`):

| DB state                                       | Action                                                                                         |
| ---------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| empty (no DeerFlow tables)                     | `create_all` + `alembic stamp head`                                                            |
| legacy (DeerFlow tables, no `alembic_version`) | `create_all` (baseline tables only, backfill) + `alembic stamp 0001_baseline` + `upgrade head` |
| versioned (`alembic_version` row exists)       | `alembic upgrade head`                                                                         |

The legacy branch handles pre-alembic databases that already have at least one DeerFlow-owned table. `create_all` runs first because stamping at `0001_baseline` makes alembic skip the baseline's own `create_table` DDL on the subsequent upgrade — so any baseline table introduced into `Base.metadata` after the user's DB was first provisioned (e.g. the `channel_*` tables from PR #1930 for users upgrading across multiple releases) would otherwise never be created, and the first request hitting that table would 500 with `no such table`. The backfill is **restricted to `_BASELINE_TABLE_NAMES`** so it does not also create tables that future revisions introduce — those revisions' own `op.create_table` would otherwise fail with `relation already exists`. A guard test pins `_BASELINE_TABLE_NAMES` against `0001_baseline.upgrade()`'s actual output, so editing 0001 to add or remove a table forces a matching update to the constant. Column-level shape (pre-#3658 vs post-#3658 vs manual-ALTER for `token_usage_by_model`) is answered by each `versions/*.py` revision via the idempotent helpers in `migrations/_helpers.py` (`safe_add_column` / `safe_drop_column`) which no-op when the change is already present and `logger.warning` on shape drift. **Adding a new ORM column / table only requires a new revision file — no edit to `bootstrap.py` is needed** _unless_ the new revision adds a new baseline table (rare; only happens when a new model is part of the baseline rather than introduced by its own revision).

The empty-DB path keeps using `create_all` because `Base.metadata` is the only authoritative schema source — `create_all` renders both SQLite (JSON, type affinity) and Postgres (JSONB, partial indexes) correctly without anyone having to keep a hand-written baseline in lockstep. `0001_baseline.upgrade()` is therefore almost never executed in practice; it exists as a stamp target + chain root.

**Concurrency safety**: Postgres uses `pg_advisory_lock` to serialise concurrent Gateway instances. SQLite uses a per-engine `asyncio.Lock` for same-process startup and is best-effort across processes via SQLite's file-level write lock + `PRAGMA busy_timeout`; multi-instance deployments should use Postgres. Column revisions in `versions/` additionally use idempotent helpers (`_helpers.py::safe_add_column`, `safe_drop_column`) so repeated post-baseline changes and retries are no-ops when the change is already present.

**Authoring a new revision**:

```bash
cd backend && make migrate-rev MSG="add foo column to runs"
```

This invokes `alembic revision --autogenerate` against the live ORM models. Review the generated file under `migrations/versions/` and switch raw `op.add_column` / `op.drop_column` calls to the idempotent helpers from `_helpers.py` before committing. There is no `make migrate` / `make migrate-stamp` target on purpose — the only execution path is Gateway startup, which keeps operational mistakes off the table.

**Where things live**:

- `migrations/env.py` — alembic env, delegates filter to `_env_filters.py`, sets `render_as_batch=True` for SQLite ALTER support
- `migrations/_env_filters.py::include_object` — drops LangGraph checkpointer tables from alembic's view
- `migrations/_helpers.py` — `safe_add_column` / `safe_drop_column`
- `migrations/versions/0001_baseline.py` — chain root, matches the schema `create_all` produces from `Base.metadata`
- `migrations/versions/0002_runs_token_usage.py` — fixes issue #3682
- `migrations/versions/0004_run_ownership.py` — `runs` multi-worker ownership + the `uq_runs_thread_active` partial unique index, with a `_dedupe_active_runs_per_thread()` pre-step so `CREATE UNIQUE INDEX` cannot fail on a field DB that already has duplicate active rows per thread
- `migrations/versions/0007_scheduled_run_active_index.py` — the `uq_scheduled_task_run_active` partial unique index (at most one queued/running `scheduled_task_runs` row per `task_id`), with a `_dedupe_active_scheduled_runs_per_task()` pre-step (keeps the newest active row per task, supersedes the rest to `interrupted` with an explanatory `error` + `finished_at`) mirroring 0004; chains after `0006_agents`
- `migrations/versions/0008_thread_operation_kind.py` — adds `runs.operation_kind` for durable non-run thread reservations; chains after `0007_scheduled_run_active_index`
- `migrations/versions/0010_run_cancel_request.py` — adds the nullable `runs.cancel_action` / `cancel_requested_at` handoff used by non-owning workers; chains after `0009_webhook_dedupe`
- `persistence/bootstrap.py` — `bootstrap_schema(engine, backend=...)`, the three-branch decision + locking
- Tests: `tests/test_persistence_bootstrap.py` (branches), `tests/test_persistence_bootstrap_concurrency.py` (concurrency), `tests/test_persistence_bootstrap_regression.py` (issue #3682), `tests/test_persistence_migrations_env.py` (filter), `tests/blocking_io/test_persistence_bootstrap.py` (asyncio.to_thread anchor), `tests/test_migration_0004_run_ownership_dedupe.py` + `tests/test_migration_0007_scheduled_run_active_dedupe.py` (dedupe-before-unique-index pre-steps)

### Checkpoint Channel Modes (`full` / `delta`)

Checkpointer storage runs in one of two channel modes, selected by `checkpoint_channel_mode` in `config.yaml` (default `full`). `delta` mode adopts LangGraph 1.2's `DeltaChannel` for `messages`: checkpoints store a sentinel + per-step writes instead of the full message list, so storage/serde grows O(N) instead of O(N²) in turns. All checkpointer backends (memory/sqlite/postgres) serve both modes unchanged — the semantics live in the compiled graph's channel table, not in the saver.

**Mode is process-frozen and restart-required.** `make_lead_agent` and the embedded `DeerFlowClient` freeze the resolved mode (`runtime/checkpoint_mode.py::freeze_checkpoint_channel_mode`) before compiling the graph with the mode-matched schema (`agents/thread_state.py::get_thread_state_schema`, plus `adapt_state_schema_for_mode` / `normalize_middleware_state_schemas` for middleware state). Adapted middleware schemas are cached by schema, mode, and resolved snapshot frequency so a pre-freeze ephemeral graph cannot leave a stale default-frequency schema behind. A second, different mode or frequency in the same process raises `CheckpointModeReconfigurationError`. To switch: edit config, restart.

**Delta snapshot cadence is configurable but frozen with the mode.** `database.checkpoint_delta.snapshot_frequency` (default `10`) sets the `DeltaChannel` snapshot cadence. It is frozen alongside the mode (`freeze_checkpoint_snapshot_frequency`; non-positive direct inputs raise `ValueError`, while a frozen-value mismatch raises `CheckpointModeReconfigurationError`), restart-required, and must match across every process sharing one checkpoint database — the cadence lives in each compiled graph's channel table and is deliberately NOT stamped into checkpoint metadata, so the mode-compatibility marker and full -> delta migration semantics are unchanged. Schema helpers resolve it explicit-arg -> frozen -> default, and every schema/graph cache (`_delta_thread_state_schema`, `_adapt_state_schema_for_delta`, the client agent-config key, the gateway accessor-graph cache) keys on the resolved value.

**Compiled-graph cache cap is configurable and hot-reloadable.** `database.checkpoint_graph_cache.accessor_graph_max` (default `64`) bounds the gateway accessor-graph cache, which clears wholesale at the cap. The cap is re-read on every eviction check (`resolve_checkpoint_graph_cache_max`), so a config.yaml reload takes effect without a restart — a size change never affects graph semantics, only eviction timing.

**Compatibility is asymmetric and fail-closed.** Every checkpoint written in delta mode carries metadata marker `deerflow_checkpoint_channel_mode: "delta"` (injected via `inject_checkpoint_mode`; absence of marker = full, so pre-feature checkpoints need no migration). Before any state read/write, `ensure_checkpoint_mode_compatible` rejects a full-mode process opening a delta thread with `CheckpointModeMismatchError` (surfaced as HTTP 409 with the cause and thread id by the threads router; `CheckpointModeReconfigurationError` maps to 503) — a full-mode raw read of a delta blob would silently return empty/partial `messages`. The reverse direction is allowed: delta-mode processes read full checkpoints transparently (old full checkpoints seed the delta channel), so full → delta is the smooth migration path; delta → full requires materializing/converting the data first. Detection also honors upstream's `counters_since_delta_snapshot.messages` metadata, and an explicit config marker takes precedence over any ambient context value.

**Never bypass `CheckpointStateAccessor` (`runtime/checkpoint_state.py`) for thread-state access.** It is the single choke point binding graph + checkpointer + mode: it injects the mode marker into configs, runs the compatibility check before every `get`/`update`/`history`, and returns materialized state (delta checkpoints lack `channel_values.messages` — raw `get_tuple` reads see a sentinel). Gateway `services.py` builds and passes the accessor; thread-owned reads (state/history/regeneration) must use `build_thread_checkpoint_state_accessor` so the recorded assistant's middleware schema materializes every channel. `history(limit)` semantics: `0` means zero items (explicit empty), `None` means unlimited — do not pass `limit=0` through to `graph.get_state_history`. Assistant metadata lookup is fail-closed for mutation accessors so a store outage cannot silently select the default schema and discard extension channels. In `full` mode the read path degrades to a raw checkpointer read (`_RawCheckpointReadAccessor`) when the agent factory cannot build the graph (bad model config, MCP outage) — full checkpoints carry complete `channel_values`, so reads don't need the graph; degraded snapshots take `created_at` from the standard checkpoint `ts` field, falling back to metadata only for compatibility. The delta gate still applies on the degraded path; `next`/`tasks` degrade to empty and thread status falls back to the stored status because task presence is not derivable, while delta mode has no fallback (materialization needs the channel table).

**Replay checkpoint lookup prefers lineage and degrades only for an explicitly missing legacy parent link.** Branch and regenerate paths first walk `parent_config`, which prevents a global chronological scan from selecting a sibling created by regeneration. `CheckpointParentMissingError` alone enables the bounded newest-first history fallback in `app/gateway/checkpoint_lineage.py`; cycles, dangling/non-addressable parents, target mismatches, and depth exhaustion raise `CheckpointLineageIntegrityError` and fail closed instead of selecting a sibling. The compatibility scans request 400 raw checkpoints so up to 200 duration-only entries do not consume the effective branch-history budget; the fallback scans oldest-to-newest internally, skips duration-only checkpoints, and accepts only checkpoints with an addressable id as the replay base. A source history with no discoverable pre-user checkpoint preserves the historical single-checkpoint branch behavior instead of rejecting the branch; regeneration remains unavailable for that inherited response. Existing single-checkpoint branches are not mutated by regenerate preparation, and no raw checkpoint tuple is copied across threads because delta state depends on ancestry and pending writes. Regenerate source-run lookup uses the current thread's exact event, then the server-stamped `run_id` on the copied human message, then verified RunManager content matching; it does not read parent-thread events. When an interrupted response was streamed but never checkpointed, regeneration accepts only the latest visible human message's server-stamped `run_id` after verifying that it belongs to the same thread and still has `interrupted` status. Storage or checkpoint-mode failures are not treated as a missing base and still fail closed.

**A delta-mode run cannot fork; `runtime/runs/worker.py` linearizes the resume instead.** Resuming from an older checkpoint (regenerate, or any client-supplied `checkpoint`) forks the lineage, and delta state for a fork is not materializable: `BaseCheckpointSaver.get_delta_channel_history` — and the bespoke overrides in `InMemorySaver`/`PostgresSaver` — collect **every** `pending_writes` entry stored on each on-path ancestor, but a shared parent also carries the writes of the sibling child that was abandoned. Those writes replay into the fork, so the run starts from a message list still containing the answer it was supposed to replace (#4458: regenerating in a branched thread showed the superseded assistant message beside the new one after a reload; reproduced on postgres, sqlite, and the in-memory saver). Write-to-child ownership belongs to the upstream delta contract, so DeerFlow does not reimplement the walk: `_linearize_delta_checkpoint_resume` materializes the requested checkpoint's complete state and writes every channel onto the **current head** (which has no siblings) through the state mutation graph, using `Overwrite` for reducer channels and resetting newer head-only channels to their schema default (or `None` when no constructible default exists); it then drops the `checkpoint_id` selector and lets the run proceed linearly, while the abandoned turn stays in history as the rewritten head's ancestry. The worker holds `_checkpoint_thread_lock` across `_capture_rollback_point` and the optional linear rewrite, making the rollback snapshot and rewrite atomic with graph streaming and the preceding run's duration-metadata checkpoint write. Capture preserves the complete real pre-run state; cancel-with-rollback then linearly replaces the current delta head with that captured state rather than forking the now-shared pre-run checkpoint, so the abandoned turn is restored without replaying the resume sibling's writes. The worker also recomputes the current-run message boundary from the rewritten state and fails closed (an unreadable resume checkpoint raises rather than falling back to the corrupt fork). `full` mode keeps forking — its checkpoints carry complete `channel_values` and need no replay — so LangGraph branching semantics are unchanged there. Root namespace only; subgraph namespaces are left alone.

**Wholesale state replacement uses a state-only mutation graph + `Overwrite`.** `update_state` values pass through channel reducers (`add_messages` merge in full, append in delta), so replacing reducer values requires `Overwrite` rather than an ordinary update. Full-mode rollback and context compaction replace `messages`; delta resume and delta rollback replace every materialized channel and reset current-head-only channels to their schema default (or `None`). These writes go through `build_state_mutation_graph(as_node, mode, state_schema)`, and `state_schema` MUST be the thread's effective schema (`graph_state_schema(assistant_graph)`), because the base-ThreadState fallback silently discards written channels contributed by custom `AgentMiddleware.state_schema`. Channels absent from a full-mode fork write inherit the parent's channel blobs, so middleware channels survive rollback/compaction (locked by `test_rollback_preserves_middleware_contributed_channels` and `test_compact_thread_context_preserves_middleware_contributed_channels`). The compiled mutation graph has one no-op node (entry = finish) whose checkpoint machinery (channels/versions/metadata) is identical to the agent graph's but schedules no pending tasks, so the restored/compacted head stays idle instead of re-triggering the agent. Never hand-write checkpoints via `checkpointer.aput` for this; raw writers elsewhere must preserve checkpoint parentage — severed ancestry breaks delta replay (see `runtime/runs/worker.py` writer parenting and `checkpoint_patches.py`).

**Run rollback flow** (`runtime/runs/worker.py`): `_capture_rollback_point` materializes the complete pre-run state via the accessor and captures raw `pending_writes` via `aget_tuple` into an immutable `RollbackPoint` before the run starts — capture failure disables rollback (fail-closed), never restores partial state. In `full` mode, cancel-with-rollback forks from the pre-run checkpoint via the mutation graph and inherits non-message channels from that parent. In `delta` mode, forking is unsafe once the cancelled path has attached sibling writes to the pre-run checkpoint, so rollback replaces every captured channel on the current head, using `Overwrite` for reducers and schema defaults for current-head-only channels. Both modes reattach only the captured pre-run pending writes to the restored checkpoint. Edit replay runs (`metadata.replay_kind="edit"`) also restore the pre-run checkpoint on failed, timed-out, or interrupted completion and publish the restored `values` snapshot to the stream before `end`, so clients do not remain on a transient edited branch when the replay did not produce a successful replacement.

**Where things live**:
- `runtime/checkpoint_mode.py` — mode + snapshot-frequency freeze, marker injection, delta detection, compatibility gate, both error types
- `runtime/checkpoint_state.py` — `CheckpointStateAccessor`, `build_state_mutation_graph`, `RollbackPoint`
- `checkpoint_patches.py` (package root) — checkpoint-machinery patches: delta-history folding for `InMemorySaver` (delegating to the base walk), stable message IDs across materialization, upstream first-write drop fix, and `BinaryOperatorAggregate` unwrapping an `Overwrite` first write into an empty (MISSING) channel — Union-typed reducer channels (`sandbox`/`goal`/`todos`/`promoted`) have no constructible default, so a replace-style write into a fresh branch thread or a never-written channel stored the wrapper literally and crashed the next consumer (#4380; probe-guarded, stands down if upstream fixes it)
- `agents/thread_state.py` — `ThreadState`/`DeltaThreadState`, `delta_messages_field` / `DELTA_MESSAGES_FIELD` (`DeltaChannel` at the configured `snapshot_frequency`, default 10), schema adaptation helpers
- `runtime/context_compaction.py` — compaction via accessor + mutation graph (reference consumer)
- `runtime/checkpoint_cache/` + `runtime/checkpointer/cached_saver.py` — delta-mode checkpoint history cache; checkpoint state reads MUST go through `CheckpointStateAccessor`, and the checkpointer may be a `CachedHistorySaver` wrapper — never rely on concrete saver types
- Tests: `tests/test_checkpoint_mode.py` (freeze/detect/gate), `tests/test_checkpoint_state.py` (accessor/mutation graph), `tests/test_delta_channel_checkpointers.py` (saver parity), `tests/test_threads_checkpoint_mode.py`, `tests/test_gateway_checkpoint_mode.py` (dual-mode e2e parity), `tests/test_context_compaction.py` (mutation-graph write, no scheduling), `tests/test_run_worker_rollback.py`, `tests/test_cached_history_saver.py` + `tests/test_cached_history_saver_integration.py` (history cache)

**Checkpoint channel benchmark**: `scripts/benchmark/checkpoint/bench_channels.py`
runs paired `full`/`delta` message-only StateGraphs in a fresh child process per
case, using sync `InMemorySaver` or `SqliteSaver` so reducer, serialization, and
saver costs stay separate from Gateway/async scheduling. It reports deterministic
correctness digests, write windows/percentiles, warm and graph-rebuilt cold reads,
logical checkpoint/write bytes, SQLite DB/WAL/SHM footprint, reducer replay time,
and peak RSS as versioned JSONL. The controller alternates mode order and rejects
performance data when paired modes materialize different state. Its default 1 GiB
estimated cumulative full-payload cap skips both modes of an oversized pair when
`full` is selected, including every delta cadence in a `--snapshot-frequencies`
sweep; intentional `--modes delta` diagnostics bypass this full-payload cap, so
size those runs explicitly. Use `--allow-large-cases` only
on a provisioned machine. Duplicate CSV matrix values are ignored with a warning;
use `--repetitions` for repeated samples. Summarize paired successful repetitions
with `scripts/benchmark/checkpoint/summarize_channels.py` (all ratios are
`delta/full`). `--profile-dir /tmp/checkpoint-profiles` writes one cProfile
artifact per case for attribution. Profiled rows carry `profiled: true`, and the
summarizer automatically excludes them from baseline summaries with a warning.
Storage-size collection relies on saver-specific diagnostic layouts; if those
layouts change, the timing/correctness row remains successful while storage
fields become `null` and `storage_stats_error` records the diagnostic failure.
Example:

```bash
cd backend
PYTHONPATH=. uv run python scripts/benchmark/checkpoint/bench_channels.py \
  --backends sqlite --updates 100,500,999,1000,1001 --payload-bytes 128 \
  --repetitions 7 --output /tmp/checkpoint-bench.jsonl
PYTHONPATH=. uv run python scripts/benchmark/checkpoint/summarize_channels.py \
  /tmp/checkpoint-bench.jsonl
```

The production-shaped layer lives in
`scripts/benchmark/checkpoint/bench_production.py`: per-case child processes
run graph-level `ainvoke` turns through the real lead-agent graph (scripted
deterministic model, real `AsyncSqliteSaver`), then measure
`GET /threads/{id}/state` and `POST /threads/{id}/history` through the real
Gateway route stack in the same event loop (httpx ASGITransport), split into
cold/warm accessor-graph-cache samples. It sweeps `snapshot_frequency`
(config: `checkpoint_delta.snapshot_frequency`, process-frozen like the mode),
pairs every delta frequency against the same full row, and fails both
rows of a pair when materialized or wire digests diverge. Each case must have
more than the two discarded warm-up turns, and SQLite DB/WAL/SHM sizes are
captured while the saver is still open so they represent the online storage
footprint. Summarize with
`scripts/benchmark/checkpoint/summarize_production.py` (ratios are
`delta/full`; it also emits `snapshot_write_spike` and `cache_effect_ms`,
the decision inputs for the production snapshot-frequency and accessor-cache
defaults). Harness tests live in `tests/test_bench_checkpoint_production.py`
and `tests/test_summarize_checkpoint_production.py`; timing thresholds are
not CI gates. The matrix test pins that every `(repetition, turns)` group
contains both modes and that their execution order flips between consecutive
groups, including across repetition boundaries.

Operational limits learned from the first runs (the default matrix is too
large to run blindly):

- The default `--timeout-seconds 900` is insufficient for delta mode at
  `snapshot_frequency=1000` once turns reach 500 (measured: delta-500 takes
  ~1100-1200s; delta-2000 takes ~45min). Pass an explicit
  `--timeout-seconds` for any large matrix, and treat the turns=2000 corner
  as practical only at small snapshot frequencies.
- Full-mode 2000-turn runs produce a ~33GB sqlite DB. Point `TMPDIR` at real
  disk, not tmpfs (the benchmark uses `tempfile.TemporaryDirectory`, which
  honors `TMPDIR`), or the run dies mid-case.
- The history route clamps `limit` to 100 (`le=100` on
  `ThreadHistoryRequest.limit`), so `--history-limits` values above 100 are
  measured and reported by their effective (clamped) limit.

Example:

```bash
cd backend
PYTHONPATH=. uv run python scripts/benchmark/checkpoint/bench_production.py \
  --turns 10,100,500,1000,2000 --payload-bytes 128 \
  --snapshot-frequencies 10,50,100,500,1000 \
  --repetitions 7 --output /tmp/production-bench.jsonl
PYTHONPATH=. uv run python scripts/benchmark/checkpoint/summarize_production.py \
  /tmp/production-bench.jsonl
```

### Terminal Workbench / TUI (`packages/harness/deerflow/tui/`)

A terminal-native UI over the embedded harness, exposed as the `deerflow` console script (`[project.scripts]` in `packages/harness/pyproject.toml`). It is a UI shell over `DeerFlowClient` and does **not** fork agent behavior. `textual` is an optional dependency (`deerflow-harness[tui]`; also in the backend dev group); the console script degrades to headless help when it is absent. Full guide: [docs/TUI.md](docs/TUI.md).

**Module layout** (all layers except `app.py` are pure / Textual-free and unit-tested directly):
- `cli.py` — `plan_launch()` (pure launch-mode decision) + headless `--print` / `--json` + `main()` entry point. TTY → TUI, else headless help. `--tui-transparent` / `DEER_FLOW_TUI_TRANSPARENT` opt into terminal-default backgrounds without changing the solid-theme default. Uses an **absolute** `from deerflow.tui.app import run_tui` so the `app.py` module name doesn't trip `test_harness_boundary.py` (which records relative import module names verbatim).
- `view_state.py` — `ViewState` + `reduce(state, action)`, the testable heart. Rows: user / assistant / tool / system. Title captured from `values` events.
- `runtime.py` — `translate(StreamEvent) -> [Action]` (pure) + `stream_actions()` which brackets a run with `RunStarted`/`RunEnded` and turns model errors into an `AssistantError` row.
- `message_format.py` / `command_registry.py` / `input_history.py` / `render.py` / `theme.py` — pure helpers (tool summaries, slash registry + `resolve()`, ↑/↓ history, Rich renderers).
- `app.py` — Textual `App`. Runs `DeerFlowClient.stream()` (sync) on a worker thread and marshals actions to the UI thread via `call_from_thread`. Slash palette with `/goal` management + model/thread modal pickers; routes idle display-only `/clear` through `ClearRows` without replacing the active thread, and blocks state-resetting local commands like `/new` and `/clear` with the standard "Still working" message during an active run; priority key bindings gated by `check_action` so they never steal keys from overlays or the composer.
- `session.py` / `persistence.py` — builds the client + checkpointer and the `ThreadMetaWriter`.

**Web UI visibility**: the Web UI lists threads from the `threads_meta` SQL table (user-scoped), not the checkpointer. `persistence.py` writes a `threads_meta` row under the default user (`"default"`) into the same DB the Gateway reads — via the harness-only `deerflow.persistence.engine.init_engine_from_config()` — so TUI sessions appear in the Web UI sidebar **without** running the Gateway. Best-effort: a no-op on the `memory` backend. All DB work runs on one long-lived background event loop (a SQLAlchemy async engine is bound to its creating loop).

**Tests**: `tests/test_tui_*.py` — pure layers via plain pytest, the app/palette/overlays via Textual's pilot harness with a fake in-process session, and `test_tui_persistence.py` for the `threads_meta` round-trip.

### Request Trace Context (`packages/harness/deerflow/trace_context.py`)

Request trace correlation is controlled by `logging.enhance.enabled` at **both** entry points, gated through the shared helper `deerflow.config.app_config.is_trace_correlation_enabled` so the Gateway and embedded paths cannot drift:

- **Gateway HTTP**: `app.gateway.trace_middleware.TraceMiddleware` binds one request-level trace id per HTTP request, inheriting inbound `X-Trace-Id` when present or generating a new id otherwise. A **valid** inbound header also marks the request so `runtime/runs/worker.py` prefers that id over `config.metadata.deerflow_trace_id`, keeping logs, response headers, Langfuse, and runtime context aligned when callers send both. The middleware writes the final value to every HTTP response at `http.response.start`, which covers SSE / streaming responses without consuming the body.
- **Embedded / TUI / CLI**: `DeerFlowClient.stream()` mints (or inherits) a request-level trace id per turn only when the flag is on. When it is off, no fresh id is minted — a caller that explicitly wraps `stream()` in `request_trace_context(...)` still opts in, because the downstream `get_current_trace_id()` read propagates that value into Langfuse metadata regardless of the flag. Because `stream()` is a sync generator (which shares the caller's context), the id binding is set/reset around each `next()` step rather than around `yield from`: this keeps LangGraph node execution and its log records inside the binding, while returning control to the caller with the ContextVar restored — avoids cross-request leak between yields and `ValueError: <Token> was created in a different Context` on GC-driven close of an abandoned generator (regression pinned by `tests/test_client_langfuse_metadata.py::test_stream_does_not_leak_trace_id_to_caller_context_between_yields` and `::test_stream_abandoned_generator_close_does_not_raise_cross_context`).

The same ContextVar value is injected into enhanced log records as `trace_id` and into Langfuse metadata as `deerflow_trace_id`.

`logging` is registered as a **restart-required** field
(`STARTUP_ONLY_FIELDS["logging"]`): `configure_logging()` installs the trace-context
filter and enhanced formatter on root handlers only during app.py lifespan startup,
and `TraceMiddleware` captures `logging.enhance.enabled` once when the FastAPI app
is constructed (via `resolve_trace_enabled(get_app_config())` in `create_app()`,
itself a thin alias for `is_trace_correlation_enabled`). This keeps the response
`X-Trace-Id` header, log `trace_id` fields, and Langfuse `deerflow_trace_id`
coherent — a runtime `config.yaml` edit to `logging.enhance.*` needs a Gateway
restart to take effect. The `deerflow_trace_id` chain inherits this guarantee
transitively because every injection point ultimately reads the same
`trace_context` ContextVar that the middleware alone populates. `DeerFlowClient`
reads its own `self._app_config` snapshot (captured at `__init__`) through the
same helper for the embedded gate.

`deerflow_trace_id` is a DeerFlow correlation metadata key, not Langfuse's native
trace id and not a DeerFlow `run_id`. Keep the existing subagent `trace_id` field
separate: that short id is still only for subagent execution logs/status.

### Tracing System (`packages/harness/deerflow/tracing/`)

LangSmith and Langfuse are both supported. The wiring lives in two layers:

- `factory.py::build_tracing_callbacks()` — returns the LangChain `CallbackHandler` list for the providers currently enabled via env vars (`LANGSMITH_TRACING`, `LANGFUSE_TRACING`, etc.). The handlers are attached at the **graph invocation root** for in-graph runs (`make_lead_agent` and `DeerFlowClient.stream` both append them to `config["callbacks"]` before invoking the graph) so a single run produces one trace with all node / LLM / tool calls as child spans. Standalone callers — anything that invokes a model outside such a graph (e.g. `MemoryUpdater`) — keep `create_chat_model`'s default `attach_tracing=True`, which falls back to model-level callback attachment.
- `metadata.py::build_langfuse_trace_metadata()` — builds the Langfuse-reserved trace attributes for `RunnableConfig.metadata`. The Langfuse v4 `langchain.CallbackHandler` lifts these onto the root trace (see its `_parse_langfuse_trace_attributes`), but only when it sees `on_chain_start(parent_run_id=None)` — which is why the callbacks have to live at the graph root, not the model.

**Trace-attribute injection points**: both `runtime/runs/worker.py::run_agent` (gateway path) and `client.py::DeerFlowClient.stream` (embedded path) merge the metadata into `config["metadata"]` right before constructing the graph. `subagents/executor.py::_aexecute` does the same for every subagent run so subagent traces group under the parent thread's session card (carrying the parent `thread_id` → `langfuse_session_id`, the user_id captured at `task_tool` → `langfuse_user_id`, and a `subagent:<normalized-name>` trace name). Caller-supplied keys win via `setdefault`, so an external `session_id` override is preserved. Field mapping:

| Langfuse field        | Source                                                                                                                                                                                                                                                                                                                                              |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `langfuse_session_id` | LangGraph `thread_id`                                                                                                                                                                                                                                                                                                                               |
| `langfuse_user_id`    | `get_effective_user_id()` (`default` in no-auth); for subagents, captured from `runtime.context` at `task_tool` time via `resolve_runtime_user_id()`                                                                                                                                                                                                |
| `langfuse_trace_name` | `RunRecord.assistant_id` / client `agent_name` (defaults to `lead-agent`); for subagents, `subagent:<name>` (lowercased, `_` → `-`)                                                                                                                                                                                                                 |
| `langfuse_tags`       | `env:<DEER_FLOW_ENV>` + `model:<model_name>`                                                                                                                                                                                                                                                                                                        |
| `deerflow_trace_id`   | Current request/entry trace id from `deerflow.trace_context`; matches `X-Trace-Id` for enhanced Gateway HTTP requests. Gated by `logging.enhance.enabled` in both gateway and embedded paths via `is_trace_correlation_enabled` — off by default; embedded callers can still opt in per-turn by wrapping `stream()` in `request_trace_context(...)` |

Returns `{}` when Langfuse is not in the enabled providers — LangSmith-only deployments are unaffected. Set `DEER_FLOW_ENV` (or `ENVIRONMENT`) to tag traces by deployment environment. Tests live in `tests/test_tracing_factory.py`, `tests/test_tracing_metadata.py`, `tests/test_worker_langfuse_metadata.py`, `tests/test_client_langfuse_metadata.py`, and `tests/test_subagent_executor.py::TestSubagentTracingWiring`.

**Monocle telemetry** is a third provider, structurally unlike LangSmith/Langfuse. It is **not** a LangChain callback: `tracing/monocle.py::setup_monocle_tracing_if_enabled()` calls `monocle_apptrace.setup_monocle_telemetry()` once, which installs a **process-global OTel `TracerProvider`**, patches span serialization, and auto-instruments the openai/langchain/langgraph clients. Because that is a one-time, process-global side effect (not a per-run callback), it is initialized from the **Gateway lifespan** (`app/gateway/app.py`) — never from `build_tracing_callbacks()` — and it is **off by default**. The setup call was deliberately moved out of `agents/__init__.py`, so `import deerflow.agents` must never start tracing (pinned by `tests/test_monocle_tracing.py::test_no_import_time_setup`). The Gateway lifespan is the **sole call site** (pinned by `test_gateway_lifespan_initializes_monocle`), so unlike LangSmith/Langfuse — which attach at the graph roots and cover every path — the embedded `DeerFlowClient` and the TUI are not instrumented; embedded users who want Monocle traces call `setup_monocle_tracing_if_enabled()` themselves before running the agent.

Unlike the Langfuse metadata above, DeerFlow injects **no** per-run fields into Monocle traces — the only attribute it sets is `workflow_name="deer-flow"`; every span attribute (`span.type`, `entity.*`, token usage, span inputs/outputs, `scope.agentic.session`) is produced by Monocle's own metamodel and auto-instrumentation, so there is no DeerFlow trace-attribute layer to maintain here.

Config is env-driven like the others — `MonocleTracingConfig`, built in `get_tracing_config()` and gated by `is_monocle_tracing_enabled()`. `MONOCLE_TRACING` enables it; `MONOCLE_EXPORTERS` selects exporters (default `file` → trace JSON in `.monocle/`; also `console`, `okahu`, `s3`, `blob`, `gcs`, where `okahu` requires `OKAHU_API_KEY`). `setup_monocle_tracing_if_enabled()` stays a thin wrapper on purpose: `monocle_apptrace` already guards duplicate setup (`instrumentor.py::check_duplicate_setup`) and never force-overrides an existing global provider, so the wrapper only gates on config. Coexistence with Langfuse (v4, also OTel-based) is **verified**: whichever library initializes second reuses the existing global `TracerProvider` and attaches its own span processor, so neither side loses spans (pinned by `test_coexists_with_langfuse`). Both processors see all spans, so Monocle's exporters also capture Langfuse's spans when both are enabled. (LangSmith is a plain callback and coexists trivially.) Tests: `tests/test_monocle_tracing.py`.

### Config Schema

**`config.yaml`** key sections:

- `models[]` - LLM configs with `use` class path, `supports_thinking`, `supports_vision`, provider-specific fields
- `logging.enhance` - Optional request trace correlation (`enabled`, `format`) for Gateway `X-Trace-Id`, log `trace_id`, and Langfuse `deerflow_trace_id`
- vLLM reasoning models should use `deerflow.models.vllm_provider:VllmChatModel`; for Qwen-style parsers prefer `when_thinking_enabled.extra_body.chat_template_kwargs.enable_thinking`, and DeerFlow will also normalize the older `thinking` alias
- `tools[]` - Tool configs with `use` variable path and `group`
- `tool_groups[]` - Logical groupings for tools
- `sandbox.use` - Sandbox provider class path
- `skills.path` / `skills.container_path` - Host and container paths to skills directory
- `skills.deferred_discovery` - When `true`, replaces the full-metadata `<available_skills>` prompt block with a compact `<skill_index>` (names only) and registers the `describe_skill` tool so the agent fetches metadata on demand. Defaults to `false` (legacy full-metadata injection)
- `title` - Auto-title generation (enabled, max_words, max_chars, model_name; null model_name uses fast local fallback, explicit model_name uses the prompt_template LLM path)
- `summarization` - Context summarization (enabled, trigger conditions, keep policy)
- `subagents.enabled` - Master switch for subagent delegation
- `memory` - Memory system (enabled, storage_path, debounce_seconds, shutdown_flush_timeout_seconds, model_name, max_facts, fact_confidence_threshold, injection_enabled, max_injection_tokens, staleness_review_enabled, staleness_age_days, staleness_min_candidates, staleness_max_removals_per_cycle, staleness_protected_categories, staleness_max_lifetime_multiplier, staleness_max_extension_days)

**`extensions_config.json`**:
- `mcpServers` - Map of server name → config (enabled, type, command, args, env, url, headers, oauth, description, `routing`, `tools`, `tool_call_timeout`, `session_init_timeout`). `routing.mode="prefer"` emits `<mcp_routing_hints>` prompt guidance; if `tool_search` defers the hinted tool, `McpRoutingMiddleware` can also auto-promote matching deferred schemas before the model call. It does not hard-disable other tools. `session_init_timeout` (default `DEFAULT_MCP_SESSION_INIT_TIMEOUT` = 60s, `null` to disable) bounds server bring-up: tool discovery and persistent stdio session initialization, so a hung server cannot block agent construction indefinitely. `tool_call_timeout` bounds individual stdio tool calls.
- `tool_search.auto_promote_top_k` - Global MCP routing auto-promote breadth. Default `3`, clamped to `1..5`; applies only when `tool_search.enabled=true` and only to deferred MCP tools with `routing.mode="prefer"` and non-empty keywords. For lead agents the deferred catalog is built from the full configured MCP set; auto-promotion never grants authority because an active skill's runtime policy still filters model-visible schemas, `tool_search` results, and execution.
- `skills` - Map of skill name → state (enabled)
- `middlewares` - Zero-argument `AgentMiddleware` class paths for lead and subagent runtime extension. `config.yaml -> extensions` can override these fields after validation; overrides are replace-per-field, not list concatenation.

Gateway API endpoints and `DeerFlowClient` methods can modify MCP servers and skill state at runtime; their `extensions_config.json` writes use the shared atomic replacement helper, while `middlewares` remains an operator-controlled config-file extension point.

### Embedded Client (`packages/harness/deerflow/client.py`)

`DeerFlowClient` provides direct in-process access to all DeerFlow capabilities without HTTP services. All return types align with the Gateway API response schemas, so consumer code works identically in HTTP and embedded modes.

**Architecture**: Imports the same `deerflow` modules that Gateway API uses. Shares the same config files and data directories. No FastAPI dependency.

**Agent Conversation**:

- `chat(message, thread_id)` — synchronous, accumulates streaming deltas per message-id and returns the final AI text
- `stream(message, thread_id)` — subscribes to LangGraph `stream_mode=["values", "messages", "custom"]` and yields `StreamEvent`:
  - `"values"` — full state snapshot (title, messages, artifacts); AI text already delivered via `messages` mode is **not** re-synthesized here to avoid duplicate deliveries; serialized `ToolMessage` entries preserve a non-`None` native `artifact`
  - `"messages-tuple"` — per-chunk update: for AI text this is a **delta** (concat per `id` to rebuild the full message); tool calls and tool results are emitted once each, and tool results preserve a non-`None` native `artifact`
  - `"custom"` — forwarded from `StreamWriter`; DeerFlow-built-in custom events are dual-emitted through `deerflow.utils.custom_events`, so `astream_events(version="v2")` consumers also receive one `on_custom_event` with `name=payload["type"]` and the unchanged payload as `data`
  - `"end"` — stream finished (carries cumulative `usage` counted once per message id)
- **Custom-event invariant** — production DeerFlow emitters must use `emit_custom_event` / `aemit_custom_event`, not call `StreamWriter` alone. Every built-in payload must carry a non-empty string `type`; typeless payloads remain writer-only and are intentionally absent from `astream_events`. The writer runs first and remains authoritative for Gateway, Web UI, and embedded-client compatibility; callback dispatch is best-effort and must not break that path. Async graph hooks must await the async helper rather than invoking synchronous dispatch on a running event loop.
- Agent created lazily via `create_agent()` + `build_middlewares()`, same as `make_lead_agent`
- Supports `checkpointer` parameter for state persistence across turns
- `reset_agent()` forces agent recreation (e.g. after memory or skill changes)
- See [docs/STREAMING.md](docs/STREAMING.md) for the full design: why Gateway and DeerFlowClient are parallel paths, LangGraph's `stream_mode` semantics, the per-id dedup invariants, and regression testing strategy

**Gateway Equivalent Methods** (replaces Gateway API):

| Category  | Methods                                                                                               | Return format                                                       |
| --------- | ----------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| Models    | `list_models()`, `get_model(name)`                                                                    | `{"models": [...]}`, `{name, display_name, ...}`                    |
| MCP       | `get_mcp_config()`, `update_mcp_config(servers)`                                                      | `{"mcp_servers": {...}}`                                            |
| Skills    | `list_skills()`, `get_skill(name)`, `update_skill(name, enabled)`, `install_skill(path)`              | `{"skills": [...]}`                                                 |
| Goals     | `get_goal(thread_id)`, `set_goal(thread_id, objective, max_continuations=8)`, `clear_goal(thread_id)` | `{"goal": {...}}` or `{"goal": None}`                               |
| Memory    | `get_memory()`, `reload_memory()`, `get_memory_config()`, `get_memory_status()`                       | dict                                                                |
| Uploads   | `upload_files(thread_id, files)`, `list_uploads(thread_id)`, `delete_upload(thread_id, filename)`     | `{"success": true, "files": [...]}`, `{"files": [...], "count": N}` |
| Artifacts | `get_artifact(thread_id, path)` → `(bytes, mime_type)`                                                | tuple                                                               |

**Key difference from Gateway**: Upload accepts local `Path` objects instead of HTTP `UploadFile`, rejects directory paths before copying, and reuses a single worker when document conversion must run inside an active event loop. Artifact returns `(bytes, mime_type)` instead of HTTP Response. The new Gateway-only thread cleanup route deletes `.deer-flow/threads/{thread_id}` after LangGraph thread deletion; there is no matching `DeerFlowClient` method yet. `update_mcp_config()` and `update_skill()` automatically invalidate the cached agent.

**Tests**: `tests/test_client.py` (offline unit tests including
`TestGatewayConformance`), `tests/test_client_live.py` (live integration tests,
requires a root `config.yaml`, valid API credentials, and explicit opt-in via
`make test-live` or `DEER_FLOW_RUN_LIVE_TESTS=1`). The live suite calls real
external APIs and may incur API costs or create local sandboxes, artifacts, and
files. It is marked `live`, excluded from `make test`, and skipped in default
CI.

**Gateway Conformance Tests** (`TestGatewayConformance`): Validate that every dict-returning client method conforms to the corresponding Gateway Pydantic response model. Each test parses the client output through the Gateway model — if Gateway adds a required field that the client doesn't provide, Pydantic raises `ValidationError` and CI catches the drift. Covers: `ModelsListResponse`, `ModelResponse`, `SkillsListResponse`, `SkillResponse`, `SkillInstallResponse`, `McpConfigResponse`, `UploadResponse`, `MemoryConfigResponse`, `MemoryStatusResponse`.

### LLM Space run exporter

`scripts/export_llm_space_thread.py` converts the Gateway's persisted message
projection into a native LLM Space Thread JSON file. It accepts a built-in
sample, a saved message response, or a live authenticated thread/run. Live
exports page backward through
`GET /api/threads/{thread_id}/messages/page` or
`GET /api/threads/{thread_id}/runs/{run_id}/messages`, using
`DEERFLOW_COOKIE` and/or `DEERFLOW_BEARER_TOKEN` when set.

DeerFlow persists assistant tool requests and `ToolMessage` results as
separate rows; LLM Space nests a result under its originating assistant
`toolCall`. The exporter joins them by `tool_call_id`, filters hidden and
non-lead-agent AI rows, preserves visible content, and infers permissive
replay-only function schemas from observed arguments. It deliberately does
not claim to export the complete effective runtime prompt, tool registry,
trace stream, subagent internals, or executable tool backends. Use
`--system-prompt-file` when a known prompt should replace the explicit
replay-oriented default. Output creation is exclusive unless the caller
passes `--force`. Tests live in
`tests/test_export_llm_space_thread.py`.

## Development Workflow

### Test-Driven Development (TDD) — MANDATORY

**Every new feature or bug fix MUST be accompanied by unit tests. No exceptions.**

- Write tests in `backend/tests/` following the existing naming convention `test_<feature>.py`
- Run the full offline suite before and after your change: `make test`
- Tests must pass before a feature is considered complete
- For lightweight config/utility modules, prefer pure unit tests with no external dependencies
- If a module causes circular import issues in tests, add a `sys.modules` mock in `tests/conftest.py` (see existing example for `deerflow.subagents.executor`)
- **A test states the deployment switch it exercises; it never inherits one.**
  Rules such as `build_workflow_steps_enabled()` read the ambient `config.yaml`
  at call time. `tests/conftest.py` provides `build_workflow_steps_off` to pin
  the shipped default so local configuration cannot silently change unrelated
  tests. When adding a config-read rule, add its pinning fixture in the same
  change set.

```bash
# Run all offline tests
make test

# Explicit live integration tests (requires config.yaml and credentials;
# calls real APIs and may create local side effects)
make test-live

# Run a specific test file
PYTHONPATH=. uv run pytest tests/test_<feature>.py -v
```

Direct pytest collection or execution of `tests/test_client_live.py` remains
skipped unless `DEER_FLOW_RUN_LIVE_TESTS=1` is set. Do not add that opt-in to
default CI workflows.

### Running the Full Application

From the **project root** directory:

```bash
make dev
```

This starts all services and makes the application available at `http://localhost:2026`.

**All startup modes:**

|          | **Local Foreground**                         | **Local Daemon**                                             | **Docker Dev**                                      | **Docker Prod**                     |
| -------- | -------------------------------------------- | ------------------------------------------------------------ | --------------------------------------------------- | ----------------------------------- |
| **Dev**  | `./scripts/serve.sh --dev`<br/>`make dev`    | `./scripts/serve.sh --dev --daemon`<br/>`make dev-daemon`    | `./scripts/docker.sh start`<br/>`make docker-start` | —                                   |
| **Prod** | `./scripts/serve.sh --prod`<br/>`make start` | `./scripts/serve.sh --prod --daemon`<br/>`make start-daemon` | —                                                   | `./scripts/deploy.sh`<br/>`make up` |

| Action      | Local                                       | Docker Dev                                        | Docker Prod                                |
| ----------- | ------------------------------------------- | ------------------------------------------------- | ------------------------------------------ |
| **Stop**    | `./scripts/serve.sh --stop`<br/>`make stop` | `./scripts/docker.sh stop`<br/>`make docker-stop` | `./scripts/deploy.sh down`<br/>`make down` |
| **Restart** | `./scripts/serve.sh --restart [flags]`      | `./scripts/docker.sh restart`                     | —                                          |

**Nginx routing**:

- `/api/langgraph/*` → Gateway embedded runtime (8001), rewritten to `/api/*`
- `/api/*` (other) → Gateway API (8001)
- `/` (non-API) → Frontend (3000)

### Running Backend Services Separately

From the **backend** directory:

```bash
# Gateway API
make gateway
```

Direct access (without nginx):

- Gateway: `http://localhost:8001`

### Frontend Configuration

The frontend uses environment variables to connect to backend services:

- `NEXT_PUBLIC_LANGGRAPH_BASE_URL` - Defaults to `/api/langgraph` (through nginx)
- `NEXT_PUBLIC_BACKEND_BASE_URL` - Defaults to empty string (through nginx)

When using `make dev` from root, the frontend automatically connects through nginx.

## Key Features

### File Upload

Multi-file upload with automatic document conversion:

- Endpoint: `POST /api/threads/{thread_id}/uploads`
- Supports: PDF, PPT, Excel, Word documents (converted via `markitdown`)
- Rejects directory inputs before copying so uploads stay all-or-nothing
- Reuses one conversion worker per request when called from an active event loop
- Files stored in thread-isolated directories under the resolving user's bucket (`users/{user_id}/threads/{thread_id}/user-data/uploads`). For IM channels the owner is threaded explicitly via the `user_id=` kwarg (see IM Channels → Owner-scoped file storage); HTTP/embedded callers resolve it from `get_effective_user_id()`
- Duplicate filenames in a single upload request are auto-renamed with `_N` suffixes so later files do not truncate earlier files
- Gateway HTTP uploads stage bytes as `.upload-*.part` files and atomically replace the destination only after size validation. These staging files are hidden from upload listings, agent upload context, and sandbox listing/search tools, and swept on Gateway startup if a hard crash leaves one behind.
- Gateway HTTP upload/list/delete handlers offload filesystem work through `deerflow.utils.file_io.run_file_io`, a dedicated ContextVar-preserving file IO executor. Non-mounted sandbox uploads acquire sandboxes with `SandboxProvider.acquire_async()` and offload `read_bytes()` plus `sandbox.update_file()` together.
- Mounted upload paths skip both sandbox acquisition and per-file synchronization. For AIO remote/provisioner deployments this requires an explicit, accurate `sandbox.thread_data_mounts: true`; omission preserves backend auto-detection.
- Agent receives uploaded file list via `UploadsMiddleware`

See [docs/FILE_UPLOAD.md](docs/FILE_UPLOAD.md) for details.

### Breeding Workspace Persistence

**A project owns its data; a conversation is a view onto it — and the
project's workspace is a real, human-visible folder** (`projects.root/<name>`,
default `~/DeerFlowProjects`, e.g. `~/Documents/projects/G2F`), browsable
in Finder and editable outside DeerFlow. That ownership is realized in three
places that must stay aligned:

1. **Storage** — `deerflow.projects.storage` owns the layout: the project
   root IS the workspace (`/mnt/user-data` and `/mnt/user-data/workspace`
   both alias it in the sandbox), with `uploads/` and `outputs/` as plain
   visible subfolders. `project_folder_name` keeps folder names human-readable
   but path-safe; `resolve_project_virtual_path` enforces containment.
   **The sandbox's `PathMapping` table is not the only place that aliasing has
   to hold.** Tool calls resolve virtual paths through
   `sandbox/tools.py::replace_virtual_path`, whose
   `_thread_virtual_to_actual_mappings` built its `/mnt/user-data` root entry
   only when workspace, uploads, and outputs shared a common parent — true of a
   thread sandbox, where the three are siblings under `user-data/`, and false
   of a project, where the workspace *is* the parent of the other two. The root
   mapping was therefore silently absent, a project file at the root
   (`/mnt/user-data/trial.csv`) came back unresolved, and
   `_validate_resolved_user_data_path` then rejected the literal virtual path
   as traversal. Both layouts are now recognized. The failure was invisible
   until DBTL, because `_project_manifest` emits exactly
   `/mnt/user-data/<relative>` for project files: a design meeting reported
   every read denied, its red team returned `blocked`, and the round produced
   no evidence at all — while the same file read fine through
   `/mnt/user-data/workspace/…`, and everything under `uploads/` or `outputs/`
   was unaffected. When a mapping table and a resolver both claim to know where
   a virtual path lives, they need one test that agrees; tests:
   `tests/test_project_scoped_sandbox.py::TestFilesAtTheProjectRootAreReadable`.
   `LocalSandboxProvider.acquire(..., project_id=..., project_root=...)` maps
   the whole `/mnt/user-data` tree there; the Gateway upload path
   (`get_uploads_dir(..., project_root=...)`), `ThreadDataMiddleware` /
   `UploadsMiddleware` (read `context["project_root"]`), `workspace_changes`
   snapshots, and `resolve_thread_virtual_path(..., project_root=...)` (files
   - artifacts routers) all resolve the same folder — changing one view
     without the others splits the agent's and the human's reality. Providers
     without project storage (AIO, E2B, BoxLite) ignore both kwargs.
     `Paths` keeps no project layout; the old internal
     `.deer-flow/users/{user}/projects/…` bucket is gone.
     Project creation supports four explicit location modes: `default` under
     `projects.root`, `existing` folder adoption, an existing-or-new
     `full_path`, and `new_under_parent`. Custom locations are canonicalized and
     must remain inside `projects.root` or a writable
     `sandbox.mounts[*].host_path`; `GET /api/project-folders` provides the
     membership-gated directory browser for those allowlisted roots. Folder
     ownership is global across active project rows: one physical directory
     cannot be claimed by projects in two workspace security boundaries.
2. **Scope record** — `threads_meta.project_id/workspace_id/scope_type` is the
   durable membership (`create(..., project_id=...)`,
   `set_conversation_scope()`, `list_by_project()`, owner-scoped in both
   stores), and `projects.root_path` (migration 0010) records the folder.
   `app/gateway/project_scope.py` owns folder computation/adoption, lazy
   backfill for pre-0010 rows, and `resolve_thread_project_scope(request,
thread_id) -> (project_id, project_root)` used by uploads/files/artifacts.
3. **Run context** — a new conversation's run request may carry
   `context.project_id` as intent; `services.py::file_thread_into_requested_project`
   honors it only after verifying membership (closes the first-run race).
   `services.py::resolve_run_owner_user_id` resolves ordinary browser/API
   callers from the authenticated session user and trusted internal callers
   from their server-validated owner header; both paths must resolve the owner
   before filing, or the first model call can load projectless memory before a
   later frontend thread-assignment request arrives.
   Then `apply_project_scope_context` stamps `context["project_id"]` **and**
   `context["project_root"]` from the durable records, dropping any
   caller-supplied values first — both are authorization-sensitive because
   they decide which folder the sandbox mounts read-write.

`ProjectContextMiddleware` (lead chain, immediately before
`SystemMessageCoalescingMiddleware`) injects a request-only system block so
the model knows where it lives: `<project_context>` (scoped runs — the
workspace IS the human folder) and `<local_folders>` (every run — each
operator-configured `sandbox.mounts` entry as a host↔container path map with
its access mode). Without these the model assumes it cannot touch the local
filesystem and hands back terminal commands. Never checkpointed; mounts are
re-read from live config per request; tests in
`tests/test_project_context_middleware.py`.
For a project-scoped local sandbox, a custom mount whose host path contains the
project root is deliberately omitted: `/mnt/user-data/workspace` is the
canonical project path, and retaining an overlapping parent such as
`/mnt/projects` lets a vague request accidentally target a sibling project.
The prompt applies the same filter and explicitly anchors relative file
requests (for example `README.md`) to the canonical workspace. Projectless
conversations retain the configured shared mount.
Project scope also forms a memory-isolation boundary. `agents.memory.scope`
derives a stable backend bucket from the authenticated `user_id` plus durable
`project_id`; `DynamicContextMiddleware`, `MemoryMiddleware`, and tool-mode
memory CRUD all use that same bucket. Projectless chats retain the existing
user-global bucket. On an older project thread's next run, dynamic context
replaces a frozen legacy/global `__memory` snapshot with project memory (or
removes it when that project has not learned anything yet). The
pre-summarization hook intentionally does not backfill project memory from
legacy history, while post-run memory learns only current-run messages.
`ProjectContextMiddleware` additionally removes any mismatched snapshot from
the model-bound request and injects a compact hidden current-project data block
immediately before the latest visible user message. That recency anchor plus
the request-only system contract makes durable `project_id`/`project_root`
authoritative over stale assistant replies and summaries without rewriting
visible conversation history.

DBTL Phase 2 adds the **canonical scope layer** in
`deerflow.agents.memory.scopes`, which expresses five contexts — `personal`,
`agent`, `project` (a member's private project memory), `shared_project`, and
`publication` (reserved) — on DeerMem's two storage keys without moving a byte
of existing data: `adapter.bind_scope` reproduces `scoped_memory_user_id`
bit-identically for the `project` kind, pinned by a test. The two project-wide
buckets occupy reserved namespaces (`--project--<digest>` and
`--published--<digest>`). `bind_scope` rejects internally inconsistent scope
objects and any authenticated user id containing either separator, protecting
both shared buckets and the per-user project buckets that already existed.

Retrieval spans the chain `reader.scoped_memory_bucket_chain` returns (private
project bucket, then the project's shared bucket); writing targets only its
head, so nothing auto-writes shared memory. `_get_memory_context` renders the
shared buckets in their own `<project_shared_memory>` section and skips an
unavailable bucket rather than blanking the whole injection. A project run
never reads the user-global bucket — that is the Phase 2 no-go.

Migration is recompute-only and never guesses: `classify.build_bucket_index`
derives the bucket every authoritative `(member, project)` pair would have
produced (including the `root:<path>` fallback) and quarantines anything
claimed by zero or by more than one authority. `inventory.build_manifest`
produces a deterministic, checksum-preserving manifest that deliberately
carries **no fact bodies**, never follows symlinks, and is sliced to the
requesting member before download so other buckets and the absolute host
storage path are not exposed. `migration.apply_decision` copies — never moves
— an approved fact into the shared bucket under a deterministic
`shared_<digest>` id. A decision binds `fact_id`, agent bucket, and the exact
source SHA-256 shown to the reviewer; stale or conflicting decisions fail
closed. Every write is serialized by a cross-process project lock and
bracketed with `journal.MigrationJournal` records
(`started` before the write, `completed` after, `rollback` on undo) so the
migration is idempotent, restartable, and reversible. Backends expose their
own root and store through the tier-3 `MemoryManager.scope_bindings()` hook;
never construct a second `FileMemoryStorage` over the same directory.

`app/gateway/routers/memory_scope.py` mounts the per-project surface under
`/api/projects/{id}/memory/migration`: the landing view returns counts only,
`/suggestions` returns bodies for exactly one person (the owner), `/decisions`
takes **one** checksum-bound `(fact_id, agent_name, source_sha256)` decision
because bulk sharing is deliberately absent, and `/manifest` and `/rollback`
complete the evidence/undo loop. The source bucket is resolved from the
authenticated caller, never from the request body; rollback removes only
copies sourced from that caller's private project buckets.
`WorkspaceRepository.list_project_members` is the authoritative membership
source. Tests: `tests/test_memory_scope_*.py`, with
`tests/test_memory_scope_two_user_sharing.py` driving the real
`FileMemoryStorage` for the two-member exit review.

Project-scoped Gateway routes live in `app/gateway/routers/workspaces.py`:
`GET /api/projects/{id}/threads`, `PUT|DELETE /api/projects/{id}/threads/{thread_id}`,
`GET /api/projects/{id}/files`, `GET /api/projects/{id}/file`, and
`GET /api/project-folders` (project path resolution in
`app/gateway/project_files.py`, with directory listings reusing the files
router's `scan_directory`). The singular `file` endpoint serves text, inline
binary previews, and downloads through the same response rules as thread
artifacts, but is addressed by project id so it needs no conversation.
Creation computes and creates (or adopts) the human folder before the row is
committed. Tests:
`tests/test_project_paths.py`, `tests/test_project_scoped_sandbox.py`,
`tests/test_thread_conversation_scope.py`,
`tests/test_project_workspace_router.py`,
`tests/test_run_project_scope_context.py`.

`deerflow.persistence.workspaces` owns the additive `workspaces`,
`workspace_members`, and `projects` tables. `WorkspaceRepository` enforces
active membership before project reads or writes; workspace creation
atomically adds the creator as owner. The Gateway exposes these records through
`app.gateway.routers.workspaces` and initializes the repository from the shared
SQL session factory in `langgraph_runtime`.

Project rows currently carry the generic collaboration foundation plus
`crop_profile`, `dbtl_phase`, and `reconciliation_status`. Keep crop-specific
attributes out of these core tables; maize-specific structure belongs in
versioned extension records in later breeding-data migrations. PostgreSQL is
the intended production authority. No application module may read `.greenagent`
as authoritative state.

DBTL Phase 0 is an explicit audit-only safety boundary. `AppConfig.dbtl.mode`
defaults to `audit_only`; `app.gateway.dbtl_readiness` may inspect legacy
`.greenagent/dbtl-cycles/*.json` and `.greenagent/handoffs/*.json` beside the
active config solely to produce `GET /api/dbtl/readiness`. The scanner is
deterministic, exposes only relative paths, and never opens a file for writing.
The four operator review classifications are `compatible`, `repairable`,
`invalid_or_ambiguous`, and `safe_to_supersede` (the last requires an explicit
`superseded_by` marker). This inventory is not authoritative DBTL state.
`start_run` rejects the reserved `project_supervisor` before creating a run,
thread, checkpoint, or artifact unless `dbtl.mode=graph_enabled`; `manual`
does not enable LangGraph execution. Preserve this fail-closed ordering.

DBTL Phase 1 adds the durable governance foundation without enabling the
workflow graph. Alembic revision `0011_dbtl_governance` owns project-scoped
cycles, stage runs, transition intents/transitions, gate evaluations, typed
human reviews, work items, artifacts, activity events, memory candidates, and
knowledge claims/promotions/links, plus validation and cutover evidence.
`deerflow.persistence.dbtl.DbtlGovernanceRepository` enforces optimistic
`db_revision` checks, projection hashes, artifact/policy binding, and
single-use idempotency. The Gateway captures reviewer identity and project
role from the authenticated membership; client actor fields, internal
principals, stale revisions, replays, and cross-project access fail closed.
Serialized LangGraph resume payloads are not review records. Never copy
reviewer identity or authorization from a resume payload into graph authority.
`GET /api/dbtl/governance/readiness` and the validation/cutover endpoints are
administrator-only. A cutover can be approved only from a successful
PostgreSQL validation; SQLite remains useful for local inspection but can
never report cutover-ready. These controls do not start or advance a cycle.

DBTL Phase 3 turns the workflow on for humans. `deerflow.dbtl.cycle_state` is
the **pure** state machine both the manual UI and the later Supervisor Graph
call, so the two cannot drift into different notions of a legal transition:
the governed path (`design → build → test → learn`) with `ready_for_build` as
an explicit state rather than an inference. Design approval opens Build.
Reconciliation remains an opt-in evidence workflow and cannot block or advance
the cycle. Refusals raise `TransitionRefused`; the machine never falls back to
a default state.

Migration `0012_dbtl_cycle_hierarchy` adds `dbtl_cycles.parent_cycle_id`
(self-FK with `RESTRICT`, so deleting a parent cannot erase child records). It
also added a partial unique index enforcing one live top-level cycle per
project; **migration `0018_allow_parallel_top_level_cycles` drops that index**.
A breeding project legitimately runs cycles in parallel across different traits,
populations, or seasons, and refusing the second one blocked real work. The
index was doing two jobs and only the product rule was dropped: the
double-click guard it also provided lives on in
`uq_dbtl_cycle_create_idempotency`, which collapses two simultaneous submissions
onto one record because the dialog mints a single create key per opening. That
is what makes removing the rule safe rather than a reopened race.
`DbtlTopLevelCycleExists` is gone with it — an exception that can no longer be
raised is worse than none. 0018's downgrade retires extra live cycles to
`abandoned` (newest kept) before restoring the index, mirroring 0012's own
dedupe pre-step; nothing is deleted, because a cycle is a research record.

`deerflow.persistence.dbtl.DbtlCycleRepository` owns the durable workflow.
Three separate mechanisms defend three different failures: `expected_db_revision`
loses a race between two reviewers on the same screen (`DbtlRevisionConflict`),
an idempotency key makes a replay return the prior result instead of applying
twice, and the constraint above arbitrates concurrent creation
(`DbtlTopLevelCycleExists`). A stage cannot be submitted without an artifact,
and a review binds to the exact evidence revision it was shown, so a later
revision cannot inherit an approval. Every mutation writes an
`activity_events` row carrying actor and committed revision — that feed is
what the human exit review inspects. Descriptive fields (research question,
objective, success criteria) ride in `projection_json` and are carried across
every revision explicitly; rebuilding the projection from scratch would drop
the question the cycle exists to record.

Creation idempotency is also a database invariant:
`uq_dbtl_cycle_create_idempotency` binds a non-null create key to one project,
including child cycles and concurrent retries. Parent links are constrained in
both the ORM and migration by `fk_dbtl_cycles_parent_cycle_id` with
`RESTRICT`; the repository additionally permits only a live top-level
season/program cycle to parent a computational cycle. Evidence and work-item
mutations carry expected revisions and idempotency keys, lock the cycle row on
PostgreSQL, and bump the durable cycle revision. New evidence is accepted only
while that stage is actually workable, so an approved stage cannot acquire a
later artifact that silently inherits the old approval. Submitting Build from
`ready_for_build` records the explicit move into Build before review; Build,
Test, and Learn approvals therefore advance to Test, Learn, and Completed
without the cycle state lagging a stage behind.

`app/gateway/routers/dbtl_cycles.py` mounts `/api/projects/{id}/dbtl/cycles`.
Reads stay available in any mode so the rail can say "no cycles" honestly;
**mutations** are gated on `dbtl.mutations_enabled` (`mode=manual` or
`graph_enabled`) and return 409 otherwise. Reviewer identity is server-owned —
the request schema is `extra="forbid"`, so a client-supplied
`reviewer_user_id` is a 422, not a trusted claim. The recorded project role
comes from current workspace membership, and the review endpoint rejects the
Gateway's internal/service principal even when it carries an owner's identity.
The review row binds the pre-decision projection hash and revision the human
actually saw, not the projection produced after applying their verdict.
Removing a live cycle is the revision-checked `cycle.abandoned` mutation, not a
SQL delete: it requires a human rationale and idempotency key, refuses terminal
cycles and parents with live children, and retains all evidence and activity.

Project removal follows the same preserve-data rule. `DELETE /api/projects/{id}`
is owner/admin-only and archives the project record, frees its visible slug,
and clears every member's conversation scope back to the inbox. It never
deletes the human-visible project folder or the DBTL records rooted in it.

DBTL Phase 4 adds classifier shadow mode and the Upgrade Proposal. The rule it
implements is "AI may recommend DBTL, but only a human can turn ordinary work
into a durable cycle", and the code is arranged so that rule has no
expressible violation.

`deerflow.dbtl.routing` is **deterministic-first**: an explicit user choice, a
typed "start a cycle" (including DBTL, research, or learning-cycle variants),
the selected cycle, and the current project are all consulted — in that order —
_before_ `deerflow.dbtl.classifier` is called at
all. A classifier consulted first would occasionally be confident enough to
overrule someone who had already said what they wanted. `RouteSource` records
which rung answered, so telemetry can tell a deterministic route from a
classified one without re-deriving it.

`deerflow.dbtl.classifier` is rule-based and pure, and deliberately so: shadow
telemetry is only comparable across runs if the same text classifies the same
way every time, and the exit review has to judge _why_ a request was flagged,
which means inspectable rule hits rather than a model's account of its own
reasoning. Data work is a deliberate high-confidence exception to the otherwise
conservative policy: `_DATA_TASK_PATTERN` recognizes structured-data assets,
tabular concepts, and analytical operations; `intent.data_task` clears the high
band by itself, and `override.data_task` defeats the ordinary explain/read/edit/
single-artifact veto. Explicit routing choices still outrank classification, so
a human can keep data work in ordinary chat. Outside that exception ordinary
remains the default and negative request-shape rules veto a positive score.
After at least one positive text rule matches, three small, auditable lifecycle
priors may raise a borderline request: first visible user turn (`0.10`), no
unfinished project cycle (`0.10`), and a project with no cycles yet (`0.08`).
They appear in telemetry as `context.*` rule hits and never fire on their own.
The narrowly recognized common simulation misspelling "similate" carries the
same intent as "simulate" without making classification generally fuzzy. The
live supervisor derives conversation freshness from checkpoint messages,
so a browser reconnect cannot claim a new turn. Project-cycle facts are stamped
server-side from `DbtlCycleRepository.project_cycle_summary`. The parallel
shadow-evaluation request echoes the pre-send welcome state only for telemetry;
it has no mutation authority and mounts no card. A model classifier can be
layered in later; routing already evaluates every deterministic signal ahead of
where it would sit.

**The rules come in two layers, because DBTL is a way of working rather than a
vocabulary.** Design/Build/Test/Learn fits a data pipeline, a benchmark, a
refactor, or a root-cause investigation as well as it fits a breeding
experiment: each needs a plan, produces artifacts (data, code, figures,
reports) along the way, and is finished only when something confirms it worked.
So beside the breeding rule tables sits a domain-neutral set — generic intent
(`intent.build_system`, `intent.optimize`, `intent.analyze`, `intent.diagnose`,
`intent.reproduce`, `intent.quantify`), generic objects (`object.pipeline`,
`object.metric`, `object.baseline`), and generic scope
(`scope.plan_and_verify`, `scope.checked_against`, `scope.success_criteria`,
`scope.multi_step`, `scope.iterate_until`). Neither layer is required: a
request with the shape and none of the vocabulary is still a cycle, which is
the point of the split. `_PLAN_VERBS` deliberately excludes
`create`/`make`/`write` — those open single-deliverable errands far more often
than projects, and anchoring the plan-and-verify rule on them would turn "make
a chart and check it renders" into a research record. `deliverable.*` fires
once per *kind* of artifact named (dataset, code, figure, table, report,
model), each at a near-noise weight, so only a request naming several of them
accumulates real score; they stay separate rules rather than one counted rule
because each hit must carry the literal span it matched, and a synthetic "3
deliverables" hit would have no evidence to show. Bare "data" is excluded for
the same reason `object.environments` is weak — it appears in nearly every
request here.

Three overrides (`override.plan_and_verify`, `override.then_verify`,
`override.success_criteria`) let a stated test beat the single-artifact veto,
so "write a script … then verify …" is no longer read as an errand. An override
only lifts the veto; the score must still clear `_DECISION_THRESHOLD` on its
own, which is what makes that list safe to extend — "fix the path then check it
runs" stops being vetoed and is then rejected on its merits.

Clarification fields follow the same split: `_BREEDING_CLARIFICATION_FIELDS`
(target trait, season range, validation expectation, population scope) when
`_BREEDING_DOMAIN_PATTERN` matches, otherwise `_GENERIC_CLARIFICATION_FIELDS`
(input data, expected outputs, validation expectation). Both ask the same three
questions underneath — what goes in, what comes out, how we will know it
worked — and the breeding set names two of them in this deployment's
vocabulary. The domain guess never affects the decision, so being wrong costs a
differently-worded question and nothing else. Rule-hit truncation now keeps the
decisive veto/override hit (`_bounded_hits`) instead of tail-cutting it: a
decision whose own evidence was truncated away cannot be reviewed.

Telemetry is stored **outside** the DBTL domain, in
`deerflow.persistence.telemetry` (`dbtl_classifier_evaluations`, migration
`0013_classifier_evaluations`). That package imports no DBTL model, which is
what makes "a shadow event cannot mutate cycle state" a property of the code
rather than a promise about it; the table's only foreign key is to `projects`.
The row stores rule hits and the derived objective but **not** the user's
message — chat content in a telemetry table would outlive the conversation and
sit outside the memory-scope controls Phase 2 built for exactly that data. Both
writes are first-writer-wins: a replayed evaluation must not overwrite the
evidence a reviewer saw, and a late second click must not rewrite the choice
actually made first, or the false-upgrade and missed-cycle rates stop meaning
anything. Evaluation idempotency is content-bound by a request fingerprint,
so reusing one key for different text or routing context returns 409 instead
of replaying unrelated evidence. Outcome writes use one conditional SQL
update rather than a select-then-update race. Missed-cycle statistics include
only classifier-sourced ordinary decisions; deterministic explicit setup
routes are not classifier misses.

`app/gateway/routers/dbtl_proposals.py` mounts
`/api/projects/{id}/dbtl/proposals`. `evaluate` classifies and records but
holds only the telemetry repository, so it has nothing to create a cycle with;
`{id}/outcome` attaches the human's choice from the supervisor's native setup
card; `evaluations` is the internal drawer and is administrator-only.
Classifier measurement is always on. The evaluation response contains routing
identity, not a second browser proposal payload; the supervisor owns visible
setup and confirmation. A telemetry write failure is logged and swallowed: a
measurement surface must not degrade the product it measures.

DBTL Phase 5 adds the thin project supervisor graph. Interactive project
threads remain durably pinned to `lead_agent`; after the Gateway has re-derived
project scope, `resolve_run_agent_factory` accepts the runtime-only
`dbtl_supervisor_enabled=true` opt-in for that run. This preserves the normal
state/checkpoint graph and makes rollback to audit/manual mode degrade to the
lead agent instead of stranding threads pinned to a disabled assistant.
`project_supervisor` remains a reserved direct/headless target in
`services.py::_DBTL_GRAPH_ASSISTANT_IDS` (one set, so the resolve path and
pre-run safety gate cannot disagree), and stays fail-closed until
`dbtl.mode=graph_enabled`.

`deerflow.agents.dbtl.supervisor` routes one request to one of four **terminal**
branches and ends; it never loops between them, so a request cannot silently
become several. The ordinary branch **is** the compiled lead agent, added
directly as a node sharing this graph's `ThreadState` schema — not a wrapper that
copies fields across — so middleware, tools, sandbox mounts, and artifact paths
behave identically because they are the same graph. Branch selection comes from
`deerflow.dbtl.branches`, a pure layer over Phase 4's `deerflow.dbtl.routing`;
re-deriving that precedence ladder would give the graph a second opinion about
the same question, and the first symptom would be a proposal card offering one
thing while the graph did another. The one decision `branches` adds is whether
enough is known to show a confirmation at all: an explicit start request that
names no trait, season, population, or validation criterion becomes a
clarification instead of a dialog with blanks in it.

**Approval comes before questions.** `resolve_branch` sends anything that could
become a cycle straight to `CYCLE_SETUP` — the confirmation card — whether or
not fields are missing. Asking someone to describe a research record before
asking whether one should exist inverts the human gate: they fill in a form to
discover they are being offered a cycle. The classifier's gaps still ride on
`BranchDecision.missing_fields`; they seed the questions raised *after*
approval, which is work the person has already agreed to. `CLARIFICATION` is
therefore unreachable from `resolve_branch` and is now the post-approval step:
only the graph can tell approval happened, because that fact lives in an
answered card rather than in the request text. `route()` sends an approved
confirmation there, guarded by `_has_emitted_card` — an approval stays in
history forever, so without the guard every later turn would re-raise the card.
Answering the questions is terminal (`_design_inputs_acknowledgement`), keyed on
the questions card rather than the approval because `_card_answer` reads only
the newest message; falling through would ask someone to approve what they just
approved.

**A deterministic supervisor reply is a receipt or it is nothing.** These
replies are authored by the graph with no model call behind them, so no LLM
callback exists to persist them: without the server-owned
`deerflow_graph_receipt` marker they live in the checkpoint alone — streamed
once, gone on refresh, absent from the thread's durable feed. Three nodes built
them as bare `AIMessage(content=...)`: `cycle_setup`'s acknowledgement and both
`cycle_continuation` stage-note replies. So someone answered the Design setup
questions and the conversation went silent — the sentence telling them where to
review the Design was written, held in graph state, and never saved, which from
the chat side is indistinguishable from a stalled cycle. All three now use
`receipt_message`, which also mints the id reconciliation identifies a message
by. `tests/test_dbtl_supervisor_receipts.py` reads `supervisor.py` and refuses
any `AIMessage` built without either `additional_kwargs` or `tool_calls`,
mirroring `RunJournal._should_reconcile`: the marker *or* an allowlisted
`ask_clarification`/`present_files` tool call is what makes a graph-authored
turn durable, so the `present_files` pair is legitimately not an offender.

**And the acknowledgement has to name an action that exists.** A cycle created
through this chat setup branch does **not** convene the Design meeting:
automatic kickoff is written by the project-rail creation endpoints alone
(`dbtl_design_kickoff` in `app/gateway/routers/dbtl_cycles.py`), so a
chat-started cycle reaches `design` with no worker runs and **no artifact**. The
acknowledgement told its owner to "review the Design stage and submit it for
approval", which is exactly what `submit_stage_for_review` refuses in that
state — *"Stage 'design' has no artifact to review; attach evidence first."* So
the one sentence standing between the owner and an apparently dead cycle named
the single action guaranteed to fail and never mentioned the step that produces
a package. It now says the meeting has not run and that the composer's
per-request cycle scope is how to run it. Restoring a message's delivery is not
enough when what it says cannot be done, which is why the two fixes ship
together. Tests: `tests/test_dbtl_supervisor_receipts.py`, plus the wording
assertions in `tests/test_dbtl_supervisor_graph.py` — the old ones pinned the
refused instruction.

**The questions are written by a model, not by the rule table.** The card used
to list the classifier's unmatched regex fields ("target trait", "season
range") as bullets — rule names rather than questions, identical in every
conversation. `deerflow.dbtl.setup_questions` replaces that: one non-graph LLM
call (`dbtl.setup_draft_model_name`) writes at most `MAX_SETUP_QUESTIONS` real
questions **and answers each one with a recommendation**, so the human corrects
rather than composes. Two rules are enforced in the parser rather than the
prompt: a recommendation
carries `grounded`, and only an explicit `true` counts, so a value the model
invented can never render as one the scientist stated (`render_questions` labels
them "suggested" vs "from your request"); and nothing fails loudly — a
malformed reply, a disabled model, or an outage degrades to
`fallback_questions`, which asks the deterministic gaps plainly and invents no
answers. `build_supervisor_graph(question_writer=...)` is the injected seam, so
tests drive the card without a model; the production writer is fail-soft by
construction because raising there would cost the user the cycle they just
approved. The structured questions also ride on the card artifact as
`setup_questions`, so a richer per-question UI needs no second emission path.

**Setup, Design, and post-approval handoffs use DeerFlow's native Human Input
Card, and a card is only half the feature.** Setup clarification, setup
confirmation, the Design council's `needs_input`, and the deterministic choice
that follows a deck approval all use the existing `ask_clarification` AI-tool /
ToolMessage pair. Their request-id prefixes are `dbtl-setup__`,
`dbtl-setup-confirm__`, `dbtl-design__`, and `dbtl-stage-handoff__` because they
resume differently: a design answer feeds a running stage, a setup answer
re-routes a request that has started nothing, and a confirmation answer either
opens the design questions (approval) or receives a deterministic no-write
acknowledgement, while the authenticated frontend action performs any requested
cycle creation. A deck approval starts one hidden cycle-scoped supervisor run;
it emits **Start &lt;next stage&gt;** / **Hold here** without calling the stage
adapter. Hold is terminal and does no work. Start uses the server-emitted option
value, recovers the cycle from the card's `dbtl_cycle_id`, and validates both
the card's `cycle_revision` and `next_stage` before calling the live adapter;
the adapter repeats that validation against the cycle it loads, so a stale card
cannot start whichever stage happens to be current. Approval and prompt delivery
are separate durable outcomes in the feedback-action ledger. Synchronous run
admission failure or a later terminal background failure writes
`handoff_failed` without undoing the review, and an exact-payload retry under the
original client submission id starts only a new handoff run. The process-local
watcher posts the failure into chat and updates the ledger; the authenticated
surface read model reconstructs the same retry state after a Gateway restart.
No DBTL-specific card is mounted by proposal evaluation,
and continuation is not a proposable route because that request already
executes the selected stage. Emitting a card without handling its answer is the failure
mode to watch for — routing reads the newest _visible_ user message, so a hidden
card reply would be skipped and the request re-derived from the original prompt,
looping straight back into the same question. `_routing_input` closes that: it
combines the originating request with the answer (the answer supplies the
fields, the request is what still says a cycle was being started) and restores
the `START_CYCLE` intent, which is otherwise gone because the composer scope
applies to one request by design. The recovered intent **outranks** the request's
own `dbtl_explicit_choice`: the client sends a scope with every request and
falls back to `ordinary` for a card it has no special handling for, so honouring
that filled-in default would drop the answer into the lead agent on exactly the
turn the server knows what the user is doing. The originating request is read
back from the card the server itself emitted (`source_request` on the
`human_input` artifact), never from the reply, so a forged `request_id` matches
no card and routes as ordinary text — and summarization compacting the original
turn cannot strand the reply. Pinned by
`tests/test_dbtl_supervisor_graph.py::TestSetupClarificationIsACard`.

**Delegation depends on idempotent reducers, and that is a framework fact, not a
convention.** A compiled child used as a parent node returns its _entire final
state_ as its update, which the parent re-applies through its own reducers. This
is safe only because every `ThreadState` channel merges by id or key
(`add_messages` dedupes by message id, `merge_artifacts` by value) rather than
accumulating. A channel added later with a naive `operator.add` reducer would
duplicate the whole conversation on the first delegated turn, so
`tests/test_dbtl_supervisor.py` pins both the per-channel reducer check and an
end-to-end idempotency test in **both** checkpoint channel modes.

**Stream contract.** Delegation moves the lead agent off the root namespace, with
three consequences pinned by `tests/test_dbtl_supervisor_stream_contract.py`:
model token frames still reach a `subgraphs=False` consumer (otherwise every
ordinary answer would stop streaming); child `values` frames stay namespaced and
never impersonate a root frame (#4399); and those token frames now carry
`langgraph_checkpoint_ns="ordinary:<id>"` in their _metadata_. The run worker
uses that value only as part of the large-file-tool batching identity key, never
as a root-vs-subagent gate — anything that later treats a non-empty metadata
namespace as "not the main thread" would misclassify every ordinary answer.
**Known cost:** per-super-step `values` snapshots from inside the ordinary branch
become namespaced, so a root-only consumer sees state at the branch boundary
rather than during it. Nothing is lost (the final snapshot is complete, tokens
are unaffected), but progressive artifact/todo/title updates arrive at the end of
the turn. This is a known framework cost confined to graph-enabled project
runs; projectless, custom-agent, scheduled, and rollback-mode runs remain on
the root lead-agent graph.

Production continuation requests receive
`deerflow.agents.dbtl.stage_execution.LiveStageAdapter` from
`make_project_supervisor`. Stage execution preserves the critical invariant:
`satisfies_gate` is a constant-false property, and gate satisfaction stays a
typed human-review record in the governance tables rather than a graph output.

The selected project and cycle arrive as **explicit runtime context**.
`supervisor_context_from_config` reads `project_id` from the merged runtime view
(the Gateway owns it: caller-supplied values are dropped and re-stamped from the
durable membership record), but reads `dbtl_selected_cycle_id` and
`dbtl_explicit_choice` from `context` **only, never the merged view**.
`configurable` is checkpointed, so a selection accepted from there would survive
into later turns and keep steering them — reading one key from one place is what
enforces the chip's "affects the next request only" promise. An unrecognized
choice falls through to normal routing rather than raising, so a stale frontend
loses a preference instead of breaking a conversation. Before live work is
dispatched, `LiveStageAdapter` loads the cycle by the pair
`(selected_cycle_id, project_id)` from the durable repository. A forged or
foreign cycle therefore produces no worker call and no write. The resume gate
applies the same project/cycle scope check.

DBTL Phase 6 defines the executable contracts and durable reconciliation gate
for Design and Data Reconciliation. Its pure contracts live in
`deerflow.dbtl` and own no storage, so the same functions answer "is this
legal?" for a person clicking a button and for the repository about to commit.
The supervisor's continuation branch is asynchronous and invokes the production
`LiveStageAdapter`; the manual adapter is never selected by the production graph.

`stage_spec.py` is the versioned registry. A stage is _data_, and the version
is the load-bearing part: a human approves an attempt that ran under one
contract, so `resolve_stage_spec` always returns a **pinned** version and an
attempt records `StageSpec.spec_key`. `EXECUTABLE_STAGES` contains all five
DBTL stages. Learn uses a candidate-only memory-write policy: its worker output
cannot itself create authoritative knowledge.
`HumanGatePolicy.allows_agent_approval` is a constant `False` **property**, not
a field, so the design's "revisit only through a separately reviewed policy
change" has no configuration value anyone could flip.

`capabilities.py` + `agent_selector.py` implement "each work unit declares
capabilities, not a fixed role name". Selection is constrained and refusal is a
real outcome (`SelectionResult.unmet_capabilities`); a generalist may cover a
capability but the fallback is recorded in `used_generalist_for` and surfaced,
because a reviewer reading "quantitative genetics: general-purpose" knows what
they are looking at and a reviewer reading nothing does not. Selection is
deterministic — an attempt that cannot be reproduced from its record is not
evidence. An **undeclared** agent covers nothing; treating it as capable of
everything would make the selection record meaningless.

`worker_result.py` is the structured contract. A claim with no `evidence_refs`
is rejected, a non-boolean `quality_checks[].passed` is refused rather than
coerced (a truthy string would turn an unanswered check into a passing one),
and `claims` accepts either canonical strings or explicitly named structured
objects with a non-empty `claim`, `statement`, or `text` field. Fully typed
nested `evidence_ref(s)` are merged into the canonical evidence list. Arbitrary
objects are never stringified, and string-only nested evidence ids are not
promoted to a made-up evidence kind, so normalizing a model's more-structured
shape cannot manufacture support,
and `capability`/`agent_name` come from the **dispatcher**, never the payload —
accepting the worker's own account of what it exercised would let a selection
failure look like a satisfied requirement. `is_trustworthy` folds `status`
together with `stop_reason`: `SubagentExecutor` reports a token/turn/loop cap as
a _completed_ run carrying a partial answer, so reading status alone would file
a truncated investigation as finished work.

`stage_runner.py` plans, dispatches, and folds results. Its synchronous and
asynchronous injected dispatcher seams receive the complete `WorkerBudget`
(worker, turn, token, and timeout caps), so the plan, each worker's prompt, and
partial-failure handling are testable without a model or a sandbox. An
unsatisfiable plan is never dispatched — partial evidence that looks complete
is worse than none — and a crashed or unparseable worker is kept as a `failed`
result rather than dropped. `StageExecutionOutcome.satisfies_gate` is the Phase
5 stub's constant `False` property, carried over unchanged.

`agents/dbtl/stage_execution.py` is the stable production import facade;
`agents/dbtl/live_stage/adapter.py` contains the concrete bridge. Replay and
typed Test review/write authority are separate services in
`agents/dbtl/live_stage/replay.py` and `agents/dbtl/live_stage/test_review.py`.
The adapter derives the active stage from durable cycle state, refuses
locked/awaiting-review stages, and builds candidates from currently available
subagents. Custom specialists opt in
with `subagents.custom_agents.<name>.dbtl_capabilities`; undeclared specialists
cover nothing, while `general-purpose` remains the explicit recorded fallback.
Each work unit gets its own `SubagentExecutor`, but its `max_turns`,
`timeout_seconds`, and token-budget middleware are clamped by the stage spec.
The child context carries the authenticated user/run identity and the verified
project id/root, so its sandbox mounts the same human-visible project workspace.
Units run concurrently and emit the normal task-started/completed/failed stream
events.

**A stage worker is graded on its final message, so it needs a deadline it can
meet.** The turn axis is enforced by `recursion_limit`, which raises
`GraphRecursionError` from the middle of a tool loop; the executor then recovers
"the last `AIMessage` carrying text", and mid-loop that is prose or nothing. So
a worker that spent its budget could never satisfy `extract_result_payload`
however well it investigated — which is exactly how a whole Design council
failed, three seats at a time, every rejection reading "returned prose instead
of a structured result". `agents/middlewares/finalization_deadline_middleware.py`
closes that axis the way the token and loop guards already close theirs: it
warns at `DEFAULT_RESERVE_CALLS` remaining and removes tools from the following
model request, so “write the result now” cannot be ignored in favor of another
tool loop; stripping in-flight `tool_calls` at the hard edge remains the
fallback. Use the effective per-agent value after clamping, not the possibly
higher stage budget, or a configured lower agent limit will abort before the
deadline fires.

**Turns are not the only budget a worker can run out of, and for Build they are
not the binding one.** A Build worker gets 450 turns against 120K tokens, so the
tokens go first and the turn deadline never fires. `TokenBudgetMiddleware` does
hard-stop at the ceiling, but it strips tool calls from *the message the model
just wrote* — mid-loop, that is prose — and records `token_capped`, so even a
good answer is discarded as untrustworthy. The observed failure was a phase that
spent 125.5K tokens and was reported as having "returned prose instead of a
structured result": it had been cut off, not badly formatted. The middleware
therefore takes an optional `max_tokens` and **warns without ever forcing** on
that axis. Forcing would duplicate a working hard stop and relabel a worker that
blew through its budget as one that met a deadline; the turn axis still forces
because `recursion_limit` *raises* and nothing downstream can recover an answer
from that. The warning fires at `DEFAULT_TOKEN_RESERVE_FRACTION` (25% held back
— one more full call must re-send the conversation *and* produce the result, so
a smaller reserve warns a worker that can no longer afford to answer) and
removes tools, exactly as the turn axis does. There is **one** warning per run,
on whichever axis binds first: the instruction and the tool removal are
identical either way, so a second notice spends budget to change nothing on a
run that is short of budget by definition.

`_token_usage.accumulate_usage` is shared by the deadline and
`TokenBudgetMiddleware` on purpose. A deadline that counted differently from the
guard it front-runs would fire at the wrong moment and the disagreement would be
invisible, because both numbers look plausible in isolation. The accounting is
delta-per-message rather than a sum over history, because `TokenUsageMiddleware`
rewrites a message's `usage_metadata` retroactively once its subagents report.
`LiveStageAdapter` passes `max_tokens=_token_limit_for_worker(unit,
dispatch_budget)` — the same resolver the executor's own token budget uses,
pinned by a test that counts both call sites — and `None` (metered-only
execution) leaves the axis inert.

**A cap must be reported as a cap.** The deadline only helps if the failure it
prevents is named correctly when it does happen. `_terminal_seat_event` checked
`parse_worker_result` *before* the capped branch, so the capped message could
only ever reach a worker whose output already parsed, and `collect_results` —
the durable record — had the identical ordering. A capped worker's truncated
prose was therefore reported as a contract violation in both the live lane and
the audit trail, sending a reader to fix formatting when the budget was the only
lever. Both now resolve through `stage_runner.worker_rejection_failure`; an
unparseable answer *inside* budget still names the contract, because the two
need opposite fixes. Separately, a Build that refuses for a **stage-level**
reason — every worker healthy, no structured rerun record — used to print "Why
each worker did not count:" followed by nothing; `MISSING_STRUCTURED_RERUN_REASON`
is stated once and reaches both the durable workflow step and the note.

**A turn is not two super-steps, and reading it that way made the deadline
useless.** `recursion_limit` counts super-steps, and LangGraph gives *every*
middleware `before_model`/`after_model` hook its own graph node — so a turn is
the model node, plus one node per hook in the shared subagent chain, plus the
tools node, plus this middleware's own `after_model`. That is
`SUBAGENT_SUPERSTEPS_PER_TURN` (currently 11 — upstream #4497/#4538 added two
hook nodes to the shared subagent chain, and the depth budgets were re-derived
so each still buys its intended 6/12/20 model calls), not 2. Halving `max_turns` set the
deadline at more than four times the calls a worker could actually make, so it
fired after the graph had already aborted and every council seat reported prose.
`model_call_budget` divides by the real cost and reserves a turn's worth of
headroom so the forced answer can be produced *and committed*; the constant is
measured from the live chain by
`tests/test_finalization_deadline_middleware.py::TestTheTurnCostAssumption`,
which fails and names the new number if a middleware gains or loses a hook. The
council's `DEPTH_POLICIES` turn budgets were sized under the old reading and
bought two to eight model calls each; they are now sized so every dispatchable
depth buys a usable number. Token enforcement is deliberately disabled for all
Design-council depths while provider-reported usage remains metered. The direct
executor path reports each seat's records to the parent run journal,
`DispatchOutcome.token_usage` binds the meter to the worker result, and the
review package stores both per-seat and aggregate input/output/total counts.
Light sits exactly at the six-call floor with a 180-second per-seat timeout.
`_light_design_context` also limits the
manifest to 24 entries, prior history to the latest chair turn, declared
datasets to 12, and tells each seat to inspect at most two relevant files and
return a concise pilot decision. Lowering the guardrail without lowering the
research assignment only makes a worker fail sooner; both halves are the Light
contract. Production Light workers receive only `read_file`; shell, web, and
other execution tools are withheld so a pilot does not spend its time
reconstructing packages or surveying the environment.

Light also has a fail-soft **review** boundary, not a fail-open gate.
`_light_pilot_chair_fallback` runs only when a capped or contract-invalid chair
returned non-empty recoverable text. It creates a new server-attributed
`system:light-pilot-fallback` result from that actual chair draft plus the
cycle's research question, objective, and success criterion. A provider,
credential, executor, or sandbox failure that returns no chair output is not a
pilot design: the failed record remains auditable and no package, deck, or
feedback surface is created. The fallback never relabels the capped worker as
complete: the source agent, stop reason, missing data/tools, and strict-contract
failure are recorded in provenance and limitations. The package's
`pilot_review` block says whether strict evidence completed and that pre-existing
data/tools were not required; the Markdown leads with the same warning. This
synthetic bounded result is enough to attach a Design artifact and make it
eligible for authenticated human approval, which can advance the cycle to Data
reconciliation. It does not approve itself. Medium and Heavy retain their
research-sized contexts, tools, budgets, and strict evidence requirement.

The one thing this middleware must **not** do is report a `stop_reason`. That
channel feeds `CAPPED_STOP_REASONS`, which makes `is_trustworthy` false —
correct for a run that blew through a safety limit, wrong for one that was
warned and answered on time, and folding the two together would discard the
evidence this exists to recover. It therefore defines no `consume_stop_reason`
(the executor enrols guards by duck-typing on that name) and surfaces
`forced_any()` instead, which `collect_results` folds into the result's
`limitations` so a reviewer still sees that the investigation was cut short.
Attaching it uses `SubagentExecutor(extra_middlewares=...)`, appended after the
shared subagent chain.

Only trustworthy structured outcomes produce a review package. The package is
written atomically under
`<project_root>/outputs/dbtl/<cycle-hash>/<stage>/` with a content-addressed
filename, then `record_worker_runs` commits every success/failure and the
artifact row in one cycle revision. The event idempotency key is bound to the
server run and cycle. A retry reads that completed event before dispatch and
returns the recorded counts/artifact; a conflicting replay is refused at the
repository boundary.

`reconciliation.py` is the bridge. `HUMAN_RESOLVED_CHECKS` draws the line the
phase's no-go depends on: a unit conversion is arithmetic and an agent may close
it, but a contradiction about what a treatment code _means_ is a judgement the
evidence underdetermines, so an agent may only reach `RowStatus.PROPOSED` there.
A proposed row still **blocks** the gate. Reporting a blocker is deliberately
open to agents — it only makes the gate stricter. `evaluate_gate` picks the
outcome code from the worst blocker present (conflicting sources > missing data

> indeterminate) so the code names what has to be fixed first.
> `dataset_fingerprint` is order-independent and binds source key, URI, content
> hash, role, and immutability, so a reordering is not a data change while a
> swapped or reclassified source is.

Migration `0014_dbtl_stage_execution` adds `dbtl_datasets` (one row per
`(cycle, source_key)`, so the fingerprint cannot depend on which duplicate a
query read), `dbtl_stage_worker_runs`, and three nullable columns on
`dbtl_stage_runs` (`stage_spec_key`, `approved_dataset_fingerprint`,
`approved_policy_version`). All three are nullable on purpose: Phase 3 attempts
predate the registry, and backfilling a guess would manufacture the false
reassurance the binding exists to prevent.

Migration `0015_repair_dbtl_schema_drift` is a forward-only shape repair for
databases whose Alembic ledger was already at 0012/0013 while
`dbtl_cycles.create_idempotency_key` or
`dbtl_classifier_evaluations.request_fingerprint` was physically absent. It
restores the nullable cycle idempotency column/index, gives unrecoverable legacy
telemetry rows a stable ID-derived fingerprint, and restores the telemetry
column's non-null contract. Its downgrade is intentionally a no-op because
0012/0013 already promise those columns; dropping them when moving back to 0014
would recreate the drift.

`ReconciliationOpsMixin` is mixed into `DbtlCycleRepository` rather than given
its own repository, because datasets, matrix rows, and worker runs belong to the
**same cycle aggregate** — same `db_revision`, same activity-event idempotency
ledger. A sibling repository would need its own copy of that machinery and the
first divergence would show up as a lost review. Three enforcement points:
`submit_stage_for_review` evaluates the gate for `reconciliation` _before_ a
reviewer is asked (sending an unresolved matrix to review invites an approval on
a contradiction nobody settled); `resolve_work_item` **refuses** reconciliation
rows, because that path takes no actor type and so cannot apply the human-
decision rule — refusing is what stops it being the way around the rule; and
`review_stage` binds the approval via `_bind_stage_approval`; a later material
dataset change moves a `ready_for_build` cycle back to reconciliation, marks
that stage changes-requested, and locks downstream stages. Identical
redeclarations are idempotent and do not reopen the approval.

Matrix rows are stored as `work_items` with `payload.kind = "reconciliation"`,
which is what the plan's "Data Readiness/Reconciliation work items and gate"
asks for and reuses Phase 3's tested revision/idempotency machinery. A row whose
payload no longer parses is reported in
`reconciliation_view.unreadable_row_ids` and **blocks** the gate; excluding it
would make corruption fail open. An empty matrix also blocks even when datasets
are declared. Approved matrix rows are immutable until a material dataset
change explicitly invalidates and reopens reconciliation.
Row dicts carry the row's **own** `db_revision`, not the cycle's; conflating the
two would make every second decision in a session fail its optimistic check.

`app/gateway/routers/dbtl_cycles.py` adds `GET .../dbtl/stage-specs`,
`GET .../cycles/{id}/reconciliation`, `GET .../cycles/{id}/stages/{stage}/workers`,
`POST .../cycles/{id}/datasets`, `POST .../cycles/{id}/reconciliation/rows`, and
`POST .../reconciliation/rows/{row_id}/decide`. The decide endpoint hardcodes
`actor_type="human"` and passes `_require_human_reviewer`: there is deliberately
no way to submit an agent decision over HTTP, because letting a browser assert
`actor_type="agent"` would make the human-decision rule a matter of what the
client chose to send. Reviewer identity stays server-owned as elsewhere.

Tests: `tests/test_dbtl_reconciliation.py` (pure rules),
`tests/test_dbtl_stage_contracts.py` (specs, selection, worker contract,
fan-out), `tests/test_dbtl_reconciliation_repository.py` (every write path goes
through the rules, including the ones that could route around them),
`tests/test_dbtl_live_stage_execution.py` (scope, stage lifecycle, partial
failure, artifacts, replay), and
`tests/test_dbtl_live_stage_graph_integration.py` (compiled supervisor graph to
real SQLite repository). Note that Phase 6 changes existing behaviour:
reconciliation can no longer be submitted for review with no declared inputs or
no readable matrix rows, so the Phase 3 manual-path tests now declare a dataset
and settle one matrix row first.

DBTL Phase 7 extends the same execution seam through Build and Test.
`deerflow/dbtl/validity.py` is the pure authority for versioned validity packs.
The legacy `generic-predictive:v1` vocabulary required every cross-domain
check; current `generic-predictive:v2` keeps the universally applicable
predictive checks and leaves population structure, within-group analysis, and
duplicates/relatedness out unless a later explicitly selected pack requires
them. A worker can retain such concerns as limitations but cannot invent them
as gates. The pack keeps `HeadlineMetric` separate from
`ValidityCheck` and computes `supported`, `not_supported`, `inconclusive`, or
`invalidated` fail-closed; high performance cannot override a failed validity
check or an explicit plausible ceiling. Only supported and valid-negative
results may recommend `advance_to_learn`.

`persistence/dbtl/build_test_ops.py` is mixed into `DbtlCycleRepository` because
Build lineage and Test decisions share the cycle revision and activity ledger.
`dbtl_build_lineage` binds a Build revision to its input fingerprint, stage
spec, code/config, environment, versioned outputs, deviations, and logs. When
Reconciliation is required that fingerprint remains the approved dataset set;
Build workers report the workspace files they examined and
the server computes their hashes automatically from files that existed before
the run. No separate dataset declaration or human-supplied digest is required.
Test then owns leakage, split, and validity checks against that lineage.
The live stage context makes that policy explicit: it projects Reconciliation
as not required for later stages, identifies
server-bound Build lineage as the data authority, and defines the retained
`reconciled_inputs` validity key as bound input provenance. Missing dataset
declarations or matrix rows are therefore neither a limitation nor a failed
Test check. Historical Build prose cannot override the active server policy.
The assessment writer enforces the same boundary: it replaces the
submitted `reconciled_inputs` status with a server-owned pass bound to Build
lineage, while the existing no-lineage refusal prevents that normalization from
manufacturing provenance. The frontend constructs the review form from the
server's `required_checks` list instead of the larger display vocabulary.
`dbtl_validity_assessments` binds a human reviewer and
typed recommendation to the exact Test attempt and latest Build lineage.
Migration `0016_dbtl_build_test_validity` owns both tables. A Build submission
without lineage is refused. The generic Test review path is also refused:
`POST .../test/assessment` computes the outcome server-side and permits only a
route legal for that outcome. Internal principals cannot call this human review
endpoint, and reviewer identity/role are taken from authenticated project
membership rather than request data.

`LiveStageAdapter` maps `ready_for_build` to Build, runs Build/Test through the
same bounded fan-out, and creates Build lineage after the content-addressed
Build package is committed. Build uses the single current
`generic:build:v12` contract: the approved Design and server-bound workspace
inputs are its inputs, each worker has an enforced 120K-token, 450-superstep,
15-minute ceiling, and one failed implementation check may receive one fresh
40K-token correction. Each phase must return a passing
`phase_done_condition`; a failed or missing assertion stops the plan and leaves
earlier committed phases available for replay. Build records a structured
rerun procedure, while Test and the human decide whether the result is
reproducible.

Build and Test share one Python environment contract. The `dbtl-build` optional
extra in `packages/harness/pyproject.toml` owns NumPy, SciPy, pandas,
Matplotlib, statsmodels, scikit-learn, seaborn, Jupyter, nbconvert, and
ipykernel. `scripts/detect_uv_extras.py` selects it whenever `dbtl.mode` is
`manual` or `graph_enabled`; local, Docker-dev, and deploy launchers all honor
that detector when `UV_EXTRAS` is not explicitly set. For a local sandbox,
`LiveStageAdapter` adds the active Gateway interpreter's venv `bin` directory
to the front of `PATH` and sets `VIRTUAL_ENV` for all Build phase workers, the
server phase verifier, and the Test rerun worker. Portable Build/Test receipts
continue to record `python`, while remote sandboxes receive no host path and
must provide their scientific runtime in the sandbox image. Do not add an
interpreter-name fallback table or pass `sys.executable` as a command: either
approach separates the executable from its installed packages. A direct manual
Gateway launch is valid only after `uv sync --extra dbtl-build` and through the
backend venv (`uv run ...`); otherwise the Build preflight fails before any
planner or worker is dispatched. Tests pin this in
`test_dbtl_stage_worker_progress.py`, `test_dbtl_build_phase_verification.py`,
and `test_dev_entrypoint.py`.

Build's structured-result parser is deliberately looser only about the label on
a concrete implementation file. Models often return semantic kinds such as
`manifest`, `execution_log`, `implementation`, or `test_suite` even though the
shared contract's `kind` field describes location. When `parse_worker_result`
is called with `stage="build"`, an otherwise unknown kind whose reference starts
under `/mnt/user-data/` is normalized to `workspace_file`. The live terminal
event, authoritative `collect_results`, and committed-phase replay all pass the
same stage context, so Activity cannot fail a result that persistence accepts
or vice versa. Unknown logical ids are still rejected, Design/Reconciliation/
Test/Learn remain strict, and Build publication still checks containment,
regular-file type, bytes, and hashes before evidence is governed. The prompt
also tells workers to put semantic file roles in `description`. Tests:
`tests/test_dbtl_stage_contracts.py::TestWorkerResultContract::test_build_normalizes_descriptive_file_kinds_to_workspace_files`,
`tests/test_dbtl_stage_contracts.py::TestStageFanOut::test_build_collection_accepts_descriptive_kinds_for_workspace_files`,
and
`tests/test_dbtl_council_seat_events.py::TestTerminalSeatEvent::test_build_file_role_kinds_complete_the_live_lane`.
Artifact publication distinguishes a reference that is missing, unreadable,
or outside its isolated worker grant. When server verification rejects a
worker after its JSON contract passed, it emits a correcting `task_failed`
event under the same task id; the transcript cannot keep showing “Subtask
completed” for bytes the server never found.
The Design chair has one separate, equally narrow compatibility case: an
`artifact_refs` entry shaped as
`{"kind":"workspace_file","reference":"/mnt/user-data/..."}` is normalized
to the identical canonical path. This preserves an otherwise valid chair
synthesis when it copied the typed evidence-reference shape into the artifact
list. It applies only to `stage="design"` plus
`capability="design_council_chair"`; logical ids, non-workspace references,
other Design participants, and every other stage remain strict.
The same Build-only compatibility boundary accepts a compact mapping of quality
check names to boolean verdicts and normalizes it to the canonical list of
`QualityCheck` rows. Mapping values may also be full check objects; strings,
numbers, empty names, and non-Build mappings remain rejected. A false check is
recorded rather than converted into a failed Build result because Build records
what ran and Test decides validity. Live events, authoritative collection, and
replay all receive the same `stage="build"` context.

That boundary now also covers a `summary` reported as a **metrics object**.
A validation phase returned 28 of 28 mandatory fidelity checks passed, claims
bound to evidence, and artifacts written, then reported its summary as an
object rather than a sentence — and the entire phase was discarded, 344,504
input tokens of sandbox work thrown away over the JSON type of one field.
`_summary_text` renders a Build mapping as one `name: value` line per field
(nested values as compact JSON), preserving the worker's own numbers and adding
nothing: a summary carries no claim, check, or evidence reference, so the worst
a rendered one can do is read like machine output, while the refusal it replaces
cost real work. Every other stage still requires prose, a list is still refused
on Build (no field names, so its numbers could not be attributed), and an empty
object is refused rather than letting a phase satisfy the contract by saying
nothing. The refusal also stopped misdiagnosing itself: a *missing* summary
still reports "must report a 'summary'", while a wrongly typed one names the
type it got — reporting a field that is plainly present as absent sends a reader
looking for the wrong thing. Tests:
`tests/test_dbtl_worker_summary_shape.py`.

`generic:test:v3` retains that allowance and pins
`generic-predictive:v2`, its exact required-check list, optional-Reconciliation
semantics, and the no-invented-gates rule into the worker context. Test
completion additionally requires `provenance.validity_assessment` with typed
headline metrics and exactly the pinned checks. The server reconstructs the
validity objects, owns the optional-mode Build-lineage check, and computes the
outcome; a prose PASS or an unregistered JSON/Markdown file remains
non-reviewable. The Supervisor presents the meeting choice and final legal
route as `ask_clarification` cards bound to that snapshot. A card answer
recovers its cycle from the server-emitted request and records the authenticated
user/project role, so the one-shot composer scope is not an authority or a
resume dependency. Build/Test/Learn decks remain registered evidence but carry
no feedback bridge; only Design keeps its deck bridge during migration. The
cycle read model also projects the narrowly known
Build-approved/Test-active rollout shape as Test, and the Test decision commits
that cursor repair with its normal audited revision. Ordinary stage workers
omit `council_seat` from lifecycle
events; actual review meetings carry their stage in that identity so the
frontend uses Design, Build, Test, or Learn meeting copy correctly. A meeting
artifact also binds the exact core evidence artifact id, revision, and content
hash it reviewed; a newer evidence revision on the same attempt reopens the
meeting gate instead of inheriting a stale completion. It records the explicit
Build checkpoint inside a one-click approval before advancing to Test. For
checkpoints created before that repair, the adapter treats a single
`in_progress` or `changes_requested` stage row as more specific than the cycle
summary, so a Build-approved/Test-active record resumes Test rather than
dispatching Build twice; terminal and unknown cycle states are never reopened
from a stale row. Build records
`workspace:unversioned` plus a deviation
when the runtime provides no source-control revision rather than manufacturing
one. Tests: `test_dbtl_validity.py`, `test_dbtl_build_test_repository.py`,
`test_dbtl_stage_contracts.py`, and the existing live/router/bootstrap suites.

DBTL Phase 8 extends that seam through Learn and adds a governed knowledge
lifecycle. `deerflow/dbtl/knowledge.py` is the pure fail-closed policy:
`supported` and `not_supported` outcomes may produce bounded positive,
valid-negative, methodological, or QA candidates as compatible with the
outcome; `inconclusive` and `invalidated` closeouts may record a synthesis but
cannot create a claim candidate. Learn workers still return
`StageWorkerResult`, and only trustworthy claims with evidence references
become `memory_candidates`.

`persistence/dbtl/knowledge_ops.py` separates four durable acts: candidate
keep/discard, human project-scope promotion, selected-project publication, and
human supersession/retraction. Owner or admin project authority is enforced at
both the HTTP and repository boundaries for promotion, publication,
supersession, and retraction. Promotion never publishes. Publication validates
that every target is an active project in the same workspace. Every knowledge
event binds its idempotency key to a canonical request digest, so a changed
payload is a conflict rather than a silent replay. Supersession and retraction
retain claims, links, publication rows, and immutable `knowledge_events`, but
close every old active publication. Migration `0017_dbtl_learn_knowledge` adds
the publication and event tables and refuses to stamp itself if its governance
foundation tables are absent; existing candidate, claim, promotion, and link
tables remain the authority for the rest of the lifecycle.

Gateway Phase 8 endpoints live under
`/api/projects/{id}/dbtl/{knowledge,candidates,claims}`. Reviewer identity and
project role are server-owned. A promotion writes a portable
`knowledge/<claim>.md` projection in the source workspace; an explicit
publication writes `knowledge/published/<claim>.md` plus a bounded DeerMem
pointer in the target project's publication bucket. Project retrieval reads
private, shared-project, then publication buckets. Retraction and supersession
remove the pointer and mark the Markdown projection while SQL and events remain
the audit authority. Tests live in `test_dbtl_phase8_knowledge.py`, the stage
contract/live execution suites, and `test_memory_scope_reader.py`.

**Withdrawing retrieval must reach every target.** SQL commits the retraction
(or supersession) _before_ the router touches DeerMem, so a per-target loop
that aborts on the first failure leaves the claim retrievable in every project
after it — permanently, because the agent read path
(`scopes/reader.py::scoped_memory_bucket_chain`) reads the publication bucket
directly and never re-checks SQL. `_withdraw_publication_retrieval` therefore
treats each target independently, returns the ids whose pointer removal failed,
and the response carries them as `stale_retrieval_project_ids` rather than
reading as "withdrawn everywhere". Pointer removal is deliberately **not** gated
on the reviewer's membership of the target project: only the human-readable
projection needs that project's folder, retrieval is keyed by target id alone,
and gating it meant a membership change silently pinned a withdrawn claim in
place. Pinned by
`test_dbtl_cycles_router.py::test_one_failing_target_does_not_strand_retrieval_in_the_others`.

**Known gaps against the Phase 8 plan** (`docs/plans/2026-07-25-dbtl-human-visible-phased-implementation-plan.md`),
carried deliberately rather than silently:

- The plan's rollout ladder (`… → proposal → supervisor → staged_execution →
knowledge`) is collapsed into `DbtlMode = disabled|audit_only|manual|graph_enabled`,
  and every knowledge endpoint gates on `mutations_enabled` (`manual` or
  `graph_enabled`). Enabling Phase 3's manual workflow therefore also enables
  knowledge promotion and cross-project publication, so "a mode may be raised
  only after its phase exit review" is currently unenforceable.
- The four knowledge mutations take no `expected_db_revision`, unlike every
  other DBTL mutation.
- `MemoryWritePolicy` and Learn's `validity_gates` are declared on the
  `StageSpec` but read by nothing — they document intent, they do not enforce it.
- `knowledge_view` is source-project scoped, so a project that a claim was
  published _into_ returns no claims/publications and cannot see or manage it.

**A stage worker can only read through the sandbox's virtual prefix, so the
manifest must name paths in that form.** `_project_manifest` emits
`/mnt/user-data/<relative>` (`WORKSPACE_VIRTUAL_ROOT`), the context carries
`workspace_root`, and every worker prompt embeds `WORKSPACE_PATH_NOTE`.
Listing project-relative paths instead cost a whole design meeting: `read_file`
refuses anything outside the prefix, so every participant reported "every file
read was denied", each returned no result, and the chair could only record that
it had nothing to synthesize from. The manifest is where most workers learn a
path exists, so it has to name the path they can actually open.

**Routing rung 2b: a conversation recovers the cycle it opened.** The
composer's cycle scope is next-request-only, so the second consecutive cycle
request arrives unscoped and `route_request` had nothing to continue — it fell
through to the classifier, which read "run the meeting again" as ordinary chat
and handed it to the lead agent, which then read the trial data and wrote the
design package itself: no meeting, no worker rows, no artifact, and a Design
stage that looked answered while its gate had not moved.
`RoutingRequest.thread_cycle_id` is resolved server-side from
`dbtl_cycles.originating_thread_id` — never accepted from the caller, because it
decides which research record a request may touch — and continues that cycle
when `wants_new_debate` matches. Narrow by construction: only the deterministic
re-run phrases, only in the originating conversation, only while the cycle is
live, and an explicit `ordinary` choice still wins, because that is a person
saying "not the cycle" and a phrase match must not argue with them. It recovers
a cycle and never invents one. The resolver is fail-soft, so an unreadable
repository costs the recovery rather than the turn, and `cycle_continuation`
re-resolves rather than carrying the value: nodes are scheduled separately, and
a branch chosen on a recovered cycle whose handler cannot see it would route to
continuation and then find nothing to continue. The phrase table moved to
`deerflow.dbtl.meeting_intent`, below both routing and the stage adapter (which
re-exports it under its original private names), because routing must answer the
same question one step earlier and two regexes that agree today drift apart the
first time either is edited. Tests:
`tests/test_dbtl_conversation_cycle_routing.py`.

Current Design attempts resolve to `generic:design:v2`. The adapter supplies a
bounded metadata-only project workspace manifest and up to four compact prior
Design chair syntheses (never the full accumulated worker payloads),
then records at least two independent positions (a required design specialist
and an adversarial red team) before dispatching a chair synthesis. Only the
chair counts toward Design-stage output. A `needs_input` chair must return one
`clarification_question`; no artifact is attached, replay preserves the
question, and the supervisor emits the standard `ask_clarification` AI-tool /
ToolMessage pair so the existing chat card owns the interaction. The card
response is routed back to the same selected cycle and appears in the next
council context. Every cycle-scoped council card records the server-resolved
`dbtl_cycle_id`; an answered card recovers that cycle as a continuation when
the browser's temporary selected-cycle context is missing, and request-text
recovery scans through hidden card replies to the automatic Design kickoff
instead of re-reading the older visible cycle-setup request. A completed chair
emits the standard `present_files` message
pair and adds the package to the thread's existing artifact reducer/inspector.
It may create a `design_brief.v2` package, but `satisfies_gate` remains
structurally false. Explicit review language in cycle-scoped chat (`approve`,
`reject`, or `request changes`) returns deterministic guidance to the
authenticated project review sheet without dispatching workers or creating a
new artifact; chat text is not a typed review record. DBTL workers also do not
implicitly inherit every enabled skill: an explicit specialist skill whitelist
is preserved, while a `None` whitelist becomes `[]` for the bounded stage run.

**A meeting convenes when a person asks for one.** A Design stage stays
`in_progress` until someone submits it for review, so before this every later
cycle-scoped message re-ran the whole council over a design already sitting on
the table. The frontend consumes both the composer scope and the project-rail
cycle selection as soon as a non-empty send is accepted; resetting only the
composer state let the still-selected rail cycle silently re-arm the next
request. The backend also treats a deterministic read/explain question as
ordinary chat when it arrives with a selected cycle but no explicit
`continue_cycle` choice. An explicit cycle scope still wins, as do answers
bound to server-emitted council cards.

**A typo must not change what a request means, on any rung.** Chat is not a
production artifact: the record keeps the owner's words verbatim while
interpretation absorbs the errors. Both routing rungs that read text absorb
one-slip misspellings deterministically, enumerated as literals (never fuzzy
matches) so the rung stays auditable — `routing._START_VERB_TYPOS` for the
typed-start verbs (a slip there skips rung 2, hands the request to the
classifier, and tells someone who asked in words to start a cycle that nothing
happened) and `classifier._EXPLAIN_TYPOS` / `_READ_TYPOS` for the `ordinary.*`
openers. The latter are *negative* rules, which makes them the safe direction to
absorb: recognizing a typo there can only make the classifier more
conservative, while missing one promotes a read-only question into cycle work
that may spend a whole council's budget. Both typo groups still require their
existing context (a start verb must name a cycle/DBTL; an opener must lead the
message), so a misspelled verb alone trips nothing. `route_request` itself stays
pure and sync — an LLM on that path would add a model call to every project
turn, and its expensive misread (promoting a question into cycle work) is
already caught downstream by the adapter's hold. The LLM readings live where a
misread is cheap and human-gated: the preflight depth (`interpret_depth`) and
the held-design re-run decision (`_interpreted_wants_new_debate`). Rung 5's
proposal classifier is still purely rule-based.

Three additional rules live in
`stage_execution`:

- `_unreviewed_design_package` **holds**: a package on the attempt plus no
  `changes_requested` review means nothing is dispatched and the reply points at
  the review sheet. `_wants_new_debate` is the deterministic override (the same
  named-phrase style as `recommend_depth`; its verb group also enumerates
  one-slip "restart" typos — `_RESTART_TYPOS` — the same narrow way the
  classifier recognizes "similate"), and a `changes_requested` attempt is
  deliberately excluded from the hold — that verdict *is* the request to argue
  again, and it already carries what to argue about. Requests the phrases do
  not match get one more reading: `_interpreted_wants_new_debate` sends the
  owner's verbatim text (typos included — the record keeps what was said,
  interpretation absorbs the errors) to the injected `intent_interpreter`
  (`make_llm_intent_interpreter`, a nostream one-shot on
  `dbtl.setup_draft_model_name`, the same fail-soft seam contract as the
  roster writer). Only a reply whose first word is CONVENE convenes; an
  absent interpreter, provider failure, or any other reply holds, so routing
  never depends on provider health and a misread costs one rephrase, never a
  council's budget. The deterministic match is checked first and skips the
  model call entirely.
  **A re-run is a new meeting, so it is set up like one.** The preflight card is
  once-only per cycle, which is right for every turn belonging to the meeting it
  opened and wrong for a deliberate re-run: that request arrives with the guard
  already tripped, so nobody was asked before a second council's budget was
  spent — and none of the confirmed settings could be read back either, because
  `confirmed_council_depth`, `confirmed_council_proposal`, and
  `confirmed_participant_settings` are scoped to the turn that *answers* a card
  and a re-run answers nothing. The server re-derived everything: a different
  number of participants, on different agents, on different models, with the
  owner's per-seat instructions gone. `rerun_requested_after_preflight` raises
  the card again and `prior_council_setup` opens it on the setup last confirmed
  (depth, approved roster, participant dials), which `preview_council` now
  accepts as `depth`/`proposal`/`participant_settings` — a supplied proposal is
  used as-is rather than written again, since re-writing it is what changed the
  seats. The guard is **positional**, not phrase-only: once emitted the card is
  newer than the request that asked for it, so the answering turn dispatches
  instead of asking forever — which also covers a stale client sending a depth
  the server cannot read, where a phrase-only guard would loop. An adjustment
  redraw recovers nothing by the same rule, because asking for a different
  roster is the opposite of reusing the old one.
  **And the roster on the card is the roster that runs.** An
  `approved_council_proposal` suppresses the `_resumed_chair_unit` revision
  branch: folding an objection into the existing synthesis is the right default
  for an unattended refinement and the wrong answer to a person who read a
  roster of three and pressed Start meeting, where the card described a council
  and one chair ran. The objection is not lost with the route — it still travels
  into the round as `human_change_request`. Tests:
  `tests/test_dbtl_meeting_rerun_settings.py`.
- `_resumed_chair_unit` **resumes**: an answer to the chair's own `needs_input`
  question dispatches the chair alone over `_prior_positions` (the durable
  worker runs), carrying the question and the owner's words verbatim, and skips
  the roster proposal entirely — there is no roster to draw for a council that
  will not convene. `_pending_design_question` reads only the *newest* chair
  run, so a stray card reply after a completed synthesis is not a resume. The
  supervisor passes the answer as an explicit `clarification_answer` rather than
  letting the adapter infer it from request text: the answer and an ordinary
  cycle request are the same string. The clarification reply is newer than the
  preflight answer, so `_resumed_council_setup` deliberately scans back to that
  server-emitted card only on this resume path and restores its exact proposal
  and participant dials. Worker results also persist `execution.model`,
  `execution.max_tokens`, and `execution.reasoning`; `_prior_chair_execution`
  supplies the durable fallback after message compaction or process restart.
  Rebuilding a resumed chair from the current default council plan is a
  correctness bug: the synthesis would be finished by a model the person did
  not approve.
- `dbtl.council_model_name` sets the **default model for seats that do not name
  one**. Inheriting the composer's model meant a meeting convened from an
  expensive chat ran every unassigned seat on it — a model chosen to talk to,
  not a budget for four workers. `_council_model` validates it against the
  configured list and falls back with a warning rather than failing the meeting
  on an operator typo. A seat's own model and the setup card's per-participant
  override both still win. `_dispatch_units` pins that effective name onto the
  `SubagentConfig` passed to `SubagentExecutor`; using it only for tool loading
  and stream labels while leaving `model="inherit"` would make the UI report one
  model while the worker actually calls the composer's provider.

**Every round with a real chair outcome writes a slide deck.**
`deerflow.dbtl.council_deck` is a pure renderer over a trustworthy completed
chair result or an uncapped `needs_input` result: agreements → contested →
needs-your-decision → synthesis → limitations → next, the same
disagreement-before-synthesis ordering as `review_markdown`. One self-contained
HTML file (inline CSS/JS, no network, `@media print` page breaks) written by
`_write_council_deck` beside the package as `design-slides-rev<N>-<hash>.html`
(`review_paths.stage_file_name` gained the `slides` kind). It is a renderer
rather than a worker prompt because a model asked to summarise a meeting can
smooth a contested point into a bullet. It is **not** registered as a durable
artifact and is listed *after* the review Markdown in `present_files`: an
approval must bind to the reviewed document, and a deck listed first is the one
a reader opens and reviews. `LiveStageResult.deck_uri` carries it; a paused
meeting gets one too, presented ahead of the `ask_clarification` card, since the
round that asks for a decision is the one that most needs its context on screen.
Failed, blocked, and capped chair records remain durable worker evidence but
must not produce a deck or feedback surface: record existence is not a meeting
outcome, and rendering a provider failure would falsely present an unfinished
task as a conclusion. The chair is necessary but not sufficient: the round must
also contain both an independent-position report and a red-team report with a
validated `completed` or `needs_input` status. Light can retain
completed-but-capped reports as an explicitly limited pilot, while provider
failures, blocked workers, and contract-rejected output do not count as debate
input. A resumed chair is allowed because the paused round's validated positions
are already durable and supplied back to that chair.
Failure to render or write returns `None` and logs — the deck is a presentation
of a record already committed, so it must never fail the turn.

**The deck has one canonical editorial style.** Its palette, typography, and
meeting-specific emphasis live directly in `council_deck._DECK_TEMPLATE`; there
is no operator setting, theme parser, or skill-loading fallback. This keeps a
newly rendered feedback surface visually consistent across deployments and
removes the failure mode where a missing or disabled skill silently restored the
older plain deck. The exact self-contained HTML bytes are still hash-registered
as the actionable gate surface. Config version 37 removes the retired
`dbtl.council_deck_theme_skill` key during `make config-upgrade`. Tests:
`test_dbtl_deck_theme.py` and `test_config_version.py`.

**A paused chair may return a structured `decision_request`, and the deck renders
it as an inert choice.** `deerflow.dbtl.decision_request` defines
`DecisionOption`/`DecisionRequest` plus `parse_decision_request(raw, *,
question)`, which **never raises** and returns a `DecisionParse` carrying either
a request or a `refusal` string. Bounds: 2–5 options (`MIN_OPTIONS`/`MAX_OPTIONS`
— card mode needs a real choice and more than a handful is a form), unique option
ids matching a conservative slug pattern, and per-field character caps. Text is
truncated, but an **over-long or malformed id is refused rather than truncated**,
because truncating would silently merge two distinct options into one answer.
The `question` argument is authoritative and overwrites the payload's own copy:
the card a person answers must be the copy the audit record keeps. `DecisionParse`
distinguishes three states callers care about — parsed, refused-with-reason, and
nothing-to-parse (absent is not a refusal, since a chair that asked a plain
question did nothing wrong).

`parse_worker_result` attaches it **only** when `status is NEEDS_INPUT` and a
non-empty `clarification_question` survived; a refusal is logged and dropped, so
a misshapen sub-object costs the option cards and never the meeting.
`StageWorkerResult.__post_init__` additionally rejects a decision request with no
question behind it — options with no question would render as a decision the
chair never asked for, and a person would answer it. `as_dict` omits the key when
absent, matching `consensus`. `DECISION_REQUEST_CONTRACT` is appended to both the
first-pass and resumed chair prompts beside `CONSENSUS_CONTRACT`.

`render_council_deck(..., decision_request=...)` renders a real
`fieldset`/`legend`/radio group rather than styled divs, because a
mutually-exclusive choice has to be *told* to assistive technology, not shown.
**Nothing is preselected, including `recommended_option_id`** — that option gets a
bordered `Recommended` badge (not colour alone, which is the first thing to
disappear in print) and the chair's reasoning is labelled as its recorded view.
Every control carries `disabled` and the slide ends with `INERT_NOTICE` ("Open
this deck in DeerFlow to respond."): the persisted file is safe to open from a
download, an attachment, or a hostile frame, and activation belongs to an
authenticated parent that does not exist yet. Options replace only the question's
own bullet on that slide; unresolved contested topics and open questions still
list below them. The deck's keydown handler now returns early for a focused
`input`/`textarea`/`select`/`button`/`a`/contenteditable — space selects a radio,
so a deck that always paged would make the choice unusable by keyboard — while
`closest('.bar')` exempts the deck's own arrows so clicking one does not kill
keyboard navigation. Tests: `tests/test_dbtl_decision_request.py`,
`tests/test_dbtl_deck_decision_cards.py`.

**A rendered deck is registered as a durable feedback surface.** Nothing about
an HTML file distinguishes the deck DeerFlow rendered from any other page an
agent wrote, so before a parent application may treat one as a Design surface it
has to ask a server: *did you produce these exact bytes, for which cycle, from
which evidence, and which conversation may they answer?*
`dbtl_design_feedback_surfaces` (migration `0022_dbtl_design_feedback_surface`,
`DesignFeedbackOpsMixin` on `DbtlCycleRepository`) is the answer, and only that
— it is **not** a review, does not replace `dbtl_reviews`, and grants no
authority on its own.

Two rules shape the write path. **Server-owned binding**: `bound_db_revision`,
`projection_hash`, and `policy_version` are read off the cycle rather than
accepted from the caller, because a deck that could assert what it was rendered
against could assert that a stale one is current. **Supersede, never mutate**:
registering a new deck for an attempt points every earlier live surface at it
and leaves those rows intact, since each is the record of what somebody was
actually shown and a review may already refer to it; `is_current` is derived
from the absence of a successor rather than stored, so a second column cannot
disagree with it. Re-registering identical bytes for the same attempt and mode
returns the existing descriptor — a retried turn re-renders the same file, and a
second id for it would leave two live surfaces answering one question. Surface
revision allocation, insert, and supersession run while holding the cycle row
lock, so concurrent registrations cannot allocate the same revision or both
remain live.

Refusals are at the write boundary: an unknown `mode`, a `deck_content_hash`
that is not a lowercase SHA-256 (refused rather than normalized, so two
spellings of one hash cannot compare unequal when the bridge later checks the
file it was handed), a surface naming no conversation, a cycle from another
project, a stage attempt from another cycle, or an evidence artifact from
another cycle. A `stage_review` surface **must** name its evidence; a
`chair_feedback` surface must not, because a paused meeting has no package yet.
`get_design_feedback_surface` is project-scoped and the route additionally
requires the path cycle to match, so a surface resolving under a different cycle
is not that cycle's to serve.

`LiveStageAdapter._register_feedback_surface` runs after `_write_council_deck`,
which now returns a `RenderedDeck` carrying the hash of the bytes it actually
wrote (recomputing it in the caller could differ from the file on disk without
anyone noticing). Mode is chosen from what exists: `chair_feedback` when the
chair paused, `stage_review` when the review package can be matched by
`_bound_evidence` — **by content hash, never by attachment order**, since a deck
must bind to the document it was rendered from or to nothing — and `read_only`
otherwise, including when there is no originating thread. Registration is
fail-visible: meeting evidence is already durable by the time it runs, but an
unregistered deck is an owner who cannot answer the gate. The run therefore
fails and may safely retry registration instead of returning a surface id that
does not exist.

`GET /api/projects/{id}/dbtl/cycles/{cycle_id}/design-feedback/{surface_id}`
serves the read model in **every** mode including `audit_only`: a read model
that disappeared when mutations were off could not tell an owner why their deck
is inert. It always reports `allowed_actions: []` and `interactive: false` in
this phase — both served rather than omitted, so a client cannot read a missing
key as permission — plus `newest_surface_id` for pointing a stale deck forward.

`safe_create_table` / `safe_drop_table` were added to `migrations/_helpers.py`
because a revision introducing a table cannot assume the table is absent: a
database whose alembic ledger sits behind its physical schema (stamped after a
full `create_all` — the drift `0015` exists to repair) already has every ORM
table, and a bare `op.create_table` there aborts the upgrade and strands that
database one revision short of head. Like `safe_add_column`, an existing table
is left as-is and the skip is logged rather than repaired. Note that several
tests pin the current head literal (`test_persistence_bootstrap*`,
`test_migration_0004/0007/0015`), so a new revision updates them.

Tests: `tests/test_dbtl_design_feedback_surface.py` (write boundary and
scoping), `tests/test_dbtl_design_feedback_router.py` (read model, membership,
cycle scoping), `tests/test_migration_0022_design_feedback_surface.py` (clean,
drifted, and rollback paths), plus registration coverage in
`tests/test_dbtl_live_stage_execution.py`.

**The deck carries its own half of the bridge, and only when registered.**
`_bridge_script` is emitted **only** for a deck the server registered; a legacy
or unregistered deck carries no bridge at all rather than a disabled one,
because the safest version of "this file cannot answer" is a file with no code
that could. The deck announces `ready` and waits: it holds no endpoint, no
token, and no way to reach a server. It validates `event.source ===
window.parent`, the message source string, the protocol version, its own
`surfaceId`, and — after the handshake — the per-mount channel the parent
issued. `window.parent === window` returns early, so a deck opened directly
never even announces itself. Terminal states **latch**: once `accepted` or
`stale`, a later `initialize` cannot re-arm it. That latch is defence in depth
(the parent's reducer already refuses to re-initialize a settled surface) and it
exists because the browser suite caught the deck happily re-arming, which no
source-level assertion would have noticed.

**The deck embeds its own surface id, which is why the id is derived rather than
random.** A deck must carry the id a parent uses to ask the server about it, but
registration binds the deck's content hash — so the id cannot be assigned
afterwards without changing the bytes it was assigned for.
`_plan_feedback_surface` decides mode and id *before* rendering, deriving
`dfs-<sha256(execution_key, mode, round)[:32]>` so a retried turn produces the
same id, the same bytes, the same hash, and re-registration collapses onto the
existing row instead of superseding it with a copy of itself. Mode is part of
the derivation because one execution can legitimately render twice (a round that
paused, then completed) and those are different surfaces;
`register_design_feedback_surface` accepts a caller-supplied `surface_id` and
disambiguates a genuine id clash rather than colliding on the primary key.
A `read_only` plan renders with no surface id at all.

**Activation has to reach every control, not just the fieldset.** Each radio and
each `[data-deck-issue]` checkbox is rendered carrying its own `disabled`, so the
persisted file is inert wherever it is opened — and an enabled `<fieldset>` does
**not** re-enable a descendant that carries that attribute itself. `setEnabled`
therefore drives a cached `choices` collection alongside the fieldset, submit,
action buttons, and comment box. Without it the deck reached a state no
source-level assertion would call wrong and no person could use: the parent
verified the bytes, the server allowed `chair_option`, the submit button came
alive, and every click answered "Choose one option first." because `selected()`
read an empty radio group. The same omission silently emptied
`request_changes`'s `optionIds`, so a reviewer's selected contested topics could
never reach the audit record. Deactivation must drive the same collection —
freezing a submission in flight has to freeze the choice behind it.

Tests: `tests/test_dbtl_deck_bridge.py` (protocol shape, refusals, and that
activation reaches every control),
`frontend/tests/e2e/design-deck-bridge.spec.ts` (the script driven in a real
browser against a stub parent),
`frontend/tests/e2e/design-deck-feedback.spec.ts` (the whole path through the
real application — artifact panel, `ArtifactFilePreview`, SHA-256 verification,
and the action request's bindings; this is the suite that caught the disabled
options),
`frontend/tests/unit/core/dbtl/design-deck-feedback.test.ts` (the
parent's parser and reducer), and `tests/test_dbtl_deck_fixture_drift.py`, which
keeps the browser suite's committed deck fixture byte-identical to this
renderer — a fixture that drifts would keep the browser suite passing against a
deck the product no longer produces.

The feedback path in
[docs/plans/2026-07-28-design-deck-feedback-plan.md](../docs/plans/2026-07-28-design-deck-feedback-plan.md)
is now live for newly rendered registered surfaces. Migration `0023` adds the
single-use action ledger and immutable Design review provenance. Surface reads
return only server-computed allowed actions after membership, user-owned
originating-thread, current-surface, current-evidence, feature-mode, and human
principal checks. Writes repeat those checks, bind the normalized payload to a
client submission id, and arbitrate each surface/action group with a unique
constraint. An identical retry replays; another payload, tab, DB revision,
artifact revision, or deck hash conflicts without rebasing.

Chair answers use the recorded option/value or exact free text, bind to the
supervisor-emitted `human_input_request_id`, and start the run only in the
originating thread. Once the run is admitted, the router also appends one
ordinary, server-owned visible `llm.ai.response` to that thread's durable event
feed. It records the selected decision and marks where the resumed meeting
began; it does not carry a `dbtl_meeting_progress` snapshot or ask the frontend
to render a bespoke meeting card. The resumed chair's ordinary reply and
`present_files` output follow in transcript order, leaving the slide deck below
the meeting. Failure to publish this turn is fail-soft and cannot roll back an
already admitted chair run.

Supervisor branches create their `ask_clarification` and `present_files`
message pairs as graph output rather than model/tool callbacks. `RunJournal`'s
root-chain reconciliation therefore persists both visible allowlisted AI turns
and their ToolMessages after the current run input, deduplicating identities
already seen through callbacks. For `present_files`, it also records the
server-authored filepaths in `run.delivery`; otherwise a background chair can
successfully write and register its final review deck while durable chat reports
zero presented artifacts. Retained messages before the current input are never
reconciled.

Stage review remains two explicit transitions:
`submit_for_review`, then one of `approve`, `request_changes`, or `reject`.
Reviews bind canonical evidence and deck hashes and separately retain selected
card ids, the nullable human comment, and the labelled server rationale
projection. Request changes queues the existing focused refinement with the
selected issue ids and comment. Lifecycle logs use the
`design_feedback.*` vocabulary and omit comments and raw deck content.

The chair action is not complete merely because `start_run` returned. A failed
chair worker is valid stage audit data, so its parent run can finish with
`success` while producing no successor feedback surface. The authenticated
surface read repairs that stranded `resume_started` action to `failed` once the
run is terminal and the old chair surface is still current. It surfaces the
latest failed chair worker's bounded contract detail, rather than replacing a
specific refusal with “no follow-up deck.” A retry keeps the same client
submission id and deck hash but may replace the selected option or comment,
because a contract-rejected byte-identical answer is guaranteed to fail again;
the old payload, run, and refusal remain in bounded `failed_attempts` receipt
history. Successful chair answers and non-chair actions remain immutable. The
authenticated read includes the failed action's selected cards and comment so
a remount begins from the recorded draft, while the enabled deck remains
editable before the next attempt.

`dbtl.design_deck_feedback=false` is the rollback switch. Descriptor and review
records are retained when it is off; consumed surfaces never reopen. Historical
decks without a descriptor, direct/downloaded decks, stale evidence, and
superseded surfaces remain inert.

**Being superseded is not the same as being revoked.** Supersession records what
somebody was most recently *shown*, so `is_current` is derived from the absence
of any successor. Authority is a different question, and conflating the two made
a Design undecidable: a round that produces no package renders a `read_only`
deck, that deck superseded the `stage_review` deck bound to the package still
awaiting a verdict, and — because actionability required `is_current` — no
surface anywhere could record the decision. The read model therefore asks
`latest_design_feedback_surface(..., mode="stage_review")`: a review deck may
act while no newer *review* deck exists for the same stage attempt and its
evidence still matches, while every other mode stays live only until anything
supersedes it. The audit record is untouched — the older row still reports
`is_current: false` and names its successor. Since the Design stage sheet is now
inspection-only, the registered deck is the sole surface that can record a
Design verdict, which is what makes this failure total rather than inconvenient.
Tests: `test_dbtl_design_feedback_surface.py::TestRegenerationSupersedesRatherThanMutates`
and `test_dbtl_design_feedback_router.py::test_a_read_only_deck_does_not_report_the_reviewable_deck_as_replaced`.

The generic stage-review request (`POST .../stages/{stage}/review`) takes
`rationale` as **optional**, mirroring the deck path: a blank one is stored as a
labelled server projection with `rationale_source: server_projection`, so a
reader can always tell the server's sentence from the reviewer's. A **rejection
still requires the reviewer's own words** (422 otherwise) — it ends the attempt,
and a generated sentence there tells the next reader nothing.

**Conditional Test: an approved Build may close to Learn instead of qualifying**
(`dbtl.conditional_test`, default false; plan:
`docs/plans/2026-08-02-research-dbtl-conditional-test-plan.md`, debate:
`docs/plans/2026-08-02-dbtl-test-stage-debate.md`). The rule lives where the
other optional-stage rules live — `deerflow.dbtl.reconciliation_policy.
conditional_test_enabled()`, fail-safe to **mandatory Test**, because a
deployment that cannot read its own rule has not asked for the looser one.

`cycle_state.BuildDisposition` is the typed answer to "what is this finished
Build for?": `keep_and_validate` (byte-identical to passing nothing) or
`learn_exploratory`. `apply_review(..., build_disposition=...)` accepts it
**only** on an approved Build and raises `TransitionRefused` anywhere else,
because silently dropping it would skip Test for a reason nobody recorded. The
skip itself is refused unless Test is still `LOCKED` (`_SKIPPABLE_FROM`):
skipping an in-flight Test discards work nobody agreed to discard, and skipping
an approved one retroactively unmakes a qualification that already happened.

Three pure-layer changes carry it. `StageStatus.SKIPPED` is a **seventh member**,
not a flavour of `LOCKED` or `APPROVED`. `_settled()` sits beside `_approved()`
and accepts a skip only for `_SKIPPABLE_STAGES` (`{"test"}`), so a skip cannot
walk past a stage that never offered the choice. And `next_cycle_state` steps
**over** a skipped target rather than entering it, while `can_enter_stage` opens
Learn from `build` only when Test itself carries the skip — the shortcut is
keyed on the recorded status, never on the stage pair.

`stage_routes.RouteSlug.LEARN_EXPLORATORY` is the edge. `RouteContext.
conditional_test` defaults **false** because a route menu is a safety surface.
`transition_target` refuses the route from any stage
but Build. The route is offered *beside* `advance`, never instead of it — the
point is that a person chooses between qualifying and not, and a menu showing
one option has taken the decision for them.

Persistence adds **no table**. `review_stage(..., build_disposition=...)` folds
the decision into the existing aggregate: the disposition joins the replay
`expected_payload` (a replay carrying a different disposition is a different
decision, not the same one twice), `_parse_build_disposition` enforces the
deployment switch at the write boundary, and `_append_stage_transition` records
`chosen_route="learn_exploratory"` rather than the `approve` verdict behind it,
so the append-only path history shows the Build → Learn edge with its evidence
hash and reviewer. A `dbtl_build_dispositions` table was considered and
rejected: the review row and the transition row already bind reviewer, evidence
id/revision/hash, and route, and a third record of one act can only drift from
the other two.

`knowledge_ops.record_learn_synthesis` now runs on **either** authority — a
human-owned validity assessment, or a human-owned decision that this Build was
not worth qualifying — and refuses a cycle carrying neither, so the absent
assessment stays meaningful. On the exploratory path it accepts a synthesis and
**refuses any candidate**. That is the promotion block, and it is structural
rather than a downstream rule: no candidate means no claim, so promotion and
publication have nothing to act on. Both `learn.synthesized` events carry
`retention_status` (`not_validated` / `qualified`) and a nullable
`test_outcome`.

Wiring: `stage_feedback.STAGE_ALLOWED_INTENTS["build"]` gains
`learn_exploratory`; `design_feedback_ops._ACTION_GROUP` maps it to
`stage_review`, because the exploratory closeout **is** the Build verdict and a
reviewer records one decision about a Build, not an approval plus a second
thought. The router refuses it from any stage but Build and from any deployment
that did not enable it (so the deck is told why rather than getting a generic
workflow error), then calls `review_stage(decision="approve",
build_disposition="learn_exploratory")` — it is still an approval of the Build;
what differs is what the reviewer decided it was for. `_exploratory_actions` is
deliberately independent of the progressive-transition gate: whether a Build is
worth qualifying is a question about the research, not about how much ceremony
the next transition needs. The Build deck renders the control unconditionally
and disabled (`BUILD_DECK_SURFACE_VERSION` bumped to
`build-review-surface-v4-exploratory-closeout`, since the deck's exact bytes are
hash-registered); the server's read model is what enables it. Tests:
`tests/test_dbtl_conditional_test.py` (pure),
`tests/test_dbtl_conditional_test_repository.py` (real SQL), plus the
`mandatory_test` pinning fixture in `tests/conftest.py`.

**Progressive-gate Phases 0-1: the cycle is a recorded walk over a D/B/T/L stage
graph** (plan: `docs/plans/2026-07-29-progressive-dbtl-gate-plan.md`).
`deerflow.dbtl.stage_routes` is the pure authority for legal edges: the graph's
nodes are Design, Build, Test, Learn only, and reconciliation is never a
destination. Build edges are never conditioned on reconciliation. The
production Test-validity write path consults
this authority before applying its legacy recommendation; the old
`return_to_reconciliation` value is refused without changing cycle state.
`TransitionOpsMixin`
(`persistence/dbtl/transition_ops.py`) appends one `dbtl_stage_transitions`
row inside the same transaction as each gate decision — design/build/learn
review verdicts, Test validity routes, and cycle closure — so a committed
decision and its path edge cannot disagree; reconciliation reviews append
nothing, because data work is not a path event. ORM update/delete of a path row
is refused: corrections append another edge rather than rewriting history.
Writes are unconditional. `dbtl.progressive_gate` (default false,
`config_version` 34) gates the path read model plus the Phase 1 Design
transition gate: `transition_assessment` runs once bound Design evidence
exists, with null model configuration, provider failure, and malformed output
all falling back to `standard`. The registered deck persists the original
assessment/rationale in its server-owned decision request and exposes
server-computed routes.

**The gate is one question with three answers.** The deck renders a single
radio group — **Approve**, **Revise**, **Park** — plus a comment box and one
"Record my decision" button, and nothing else: no review-depth override select,
no per-route buttons, no contested-topic checkboxes, no separate submit step.
The earlier slide offered all of those at once and made the commonest decision
in the product read as a form. The bridge maps one choice to one intent
(`revise` → `request_changes`, `park` → `park`, approve → `advance` while the
route is offered, else `approve`), and both `advance` and a gate-issued
`request_changes` call `review_stage(auto_submit=True)`, producing one normal
evidence-bound review and one transition with no separate `stage.submitted`
event. **Depth changes how carefully a verdict must be justified, not how many
actions it takes to record one**: the routine-only restriction on one-click
approval is gone, and high stakes now costs the reviewer their own words on any
approving or ending verdict rather than an explicit downward override. The
`difficulty_override` field remains on the API and is still recorded beside the
assessment when a caller supplies one; the deck no longer sends it. A blocked
route disables only its own choice and prints its reason beside it — activation
must not clear a `data-route-blocked` control. Decks with no transition gate
keep the legacy submit/approve/request-changes/reject controls, so the shared
bridge script still names them.

Park writes an append-only park edge and stores the
exact evidence URI/hash as `approval_status=unapproved`; the supervisor sends
selected-cycle ordinary work to the lead agent with that warning injected into
request-only project context, and the next gate verdict clears the projection.
`GET .../dbtl/cycles/{id}` exposes transitions and the current gate only while
the flag is on; toggling it therefore restores the old ceremony without
creating an audit gap. Tests:
`test_dbtl_stage_routes.py` (route-legality matrix + Phase 7 golden mapping),
`test_dbtl_stage_transitions.py` (append per gate, immutability, replay safety,
real Test→Design revisit, scoping), `test_migration_0024_stage_transitions.py`
(idempotent backfill + drift + rollback), `test_dbtl_transition_assessment.py`
(fail-safe assessment), `test_dbtl_design_feedback_router.py` (one-action
approve at every depth, one-action revise, high-stakes rationale, and deck
binding), and `test_dbtl_deck_bridge.py::TestProgressiveTransitionGate` (the
three choices, blocked-route handling, and the choice→intent mapping).

**The Build workflow is five versioned steps, and the digest chain is what
makes a partial Build reusable** (`deerflow/dbtl/build_workflow.py`,
`persistence/dbtl/step_ops.py`, migration `0026`). Build executes today as one
opaque `WorkUnit`: when the final structured answer fails to parse — or the deck
fails to render — scientifically complete sandbox work is discarded and the
whole thing runs again. `generic:build-workflow:v1` names the five steps that
never vary (`load_design`, `plan_build`, `execute_phases`,
`summarize_results`, `render_review_deck`); what varies is the phase plan step 2
produces, which is why a phase attempt carries its own `plan_digest` — a phase
whose plan changed is a *different* phase, not a retry of this one.

Every step's identity is `input_digest(step, predecessors, material)`: the step
itself, its selected predecessors' outputs **in order**, and server-owned
material. `project_workflow` therefore cannot be a filter — it is a walk, since
the expected digest of step N depends on the selected output of N-1. Three
properties fall out of the arithmetic rather than out of care: an upstream
change invalidates **all and only** its descendants, a successful predecessor is
never rerun because a later step failed, and an invalidated success is
*reported* rather than deleted, because the record of what was attempted is the
point. `BuildErrorCode` is closed and has two deliberate absences —
`needs_input` (it renders as **Waiting for you** and must not count against
failure telemetry) and any code for "the result looked wrong", because no code
here should stop a Build on a judgement that belongs to Test or a person.
`is_presentational` is what lets the UI say "the build ran; the write-up broke".

`dbtl_stage_step_runs` is deliberately **not** an overload of
`dbtl_stage_worker_runs`: worker rows describe model-backed work units, while
`load_design` and `render_review_deck` are deterministic server code that needs
the same audit and retry semantics without the review record claiming a model
read the Design. Two database constraints carry semantics calling code cannot:
`uq_dbtl_stage_step_attempt` collapses a replayed dispatch, and the partial
unique `uq_dbtl_stage_step_running` enforces one running attempt per step where
two concurrent dispatches cannot both pass a check-then-write. `open_step_attempt`
returns `(attempt, dispatched=False)` when a success already exists against the
same input digest — the resume algorithm's replay-without-dispatch — and only
the running index is translated into `DbtlStepConflict`; reporting every
integrity failure that way would present a missing stage run as a busy step. A
settled attempt is immutable, `needs_input` included: a human answer creates a
new attempt rather than reopening the row holding the question.

A known parent-run cancellation settles only that run's still-running step rows
as `cancelled` before propagating `CancelledError`; the six-hour orphan reclaim
is only the hard-crash backstop. The stage-level Activity row likewise settles
to terminal `paused` when a plan or worker question needs a person and to
terminal `failed` when execution returns no evidence. A successfully delivered
failure card still leaves the parent run transport-successful: the Activity
timeline carries the governed-work outcome instead of misreporting that the
Gateway failed to deliver it.

Stage workers whose writable root is the virtual
`/mnt/user-data/outputs/.dbtl-stage-work/...` path receive the sandbox-native
file tools, not host-rooted `filesystem_*` MCP tools that cannot resolve that
namespace. MCP origin metadata keeps that exclusion correct when an operator
renames the server or disables its visible tool-name prefix. Under
`sandbox-exec`, the process-level write sandbox remains the authority for shell
commands, but a quoted delimiter means only that Bash will not expand the
body—it does not make the receiving interpreter inert. A shared heredoc
classifier preserves virtual paths only for positively recognized,
non-expanding `cat`/`tee` payloads targeting known data extensions. Immediate
Python, stored Python, and stored shell/code bodies remain subject to local path
translation and host-path audit; extensionless targets fail closed. Python
source contributes its string literals rather than arithmetic operators to the
path scanner, including recursively tokenizing program-shaped strings used by
a small Python writer. A literal, ordered virtual-path assignment may authorize
`cd "$STAGE"`; later, dynamic, or reassigned values may not. The standard shell
self-directory idiom is normalized only for shell-syntax auditing. Direct
file-writing tools and non-isolated shell mode remain statically fail-closed.
This keeps durable JSON provenance virtual without hiding executable host-path
access from the policy boundary.

**A path a worker repeats is a path the run pays for, on every later model
call.** A stage-work path appears in every `write_file`/`read_file`/`bash`
argument, in the echoed result, and in the manifest — and the conversation so
far is re-sent on each of that phase's model calls, so its length is multiplied
by the phase's whole tool history. Two things in that path cost out of
proportion to what they say, and both are now bounded.
`workspace.safe_token` emits **12 hex characters** rather than 20: random hex
tokenizes at roughly half the density of prose, so each character is paid twice
over, while 48 bits is far wider than the tens of unit ids within one stage
attempt and attempt ids within one project that it separates. It is a
collision-avoidance width, not a security one — widen it if what it separates
ever becomes adversarial or global. And `workspace.SHELL_WORKSPACE_IDIOM` is
quoted into both the phased Build prompt and the monolithic stage instruction,
so a worker binds the prefix once (`STAGE=…; cd "$STAGE"`) instead of repeating
it per command. It shows the **exact shape the audit above permits** — one
literal, ordered assignment — because a prompt that described the idea would
get a reassignment or a command substitution back and have its own example
refused; the phase prompt's older blanket "do not cd" is gone, since that
assignment is precisely what authorizes the `cd`. The second half of the idiom
is load-bearing rather than padding: the variable is a *shell* convenience, and
`write_file`, `str_replace`, and every path in the result contract are resolved
by tools with no shell to expand it, so teaching the idiom without naming where
it does not apply would trade a few tokens for a run of refused writes. Neither
change is a correctness property — nothing fails when a path grows — which is
why `tests/test_dbtl_stage_path_token_cost.py` pins them, including that the
taught command actually passes `validate_local_bash_command_paths`. Tests must
derive attempt paths through `safe_token` rather than restating the digest
width, or a cost change reads as a behaviour change.

The review-deck step opens only after execution produced both the review
artifact URI and content hash; an incomplete Build therefore leaves the deck
waiting rather than recording a secondary renderer failure.

`GET /projects/{id}/dbtl/cycles/{cycle}/stages/{stage}/workflow` serves the
projection in **every** mode, including with `dbtl.build_workflow_steps` off
(default): the flag governs whether the workflow drives execution, and a read
model that vanished with it could not tell an owner why their Build looks the
way it does. It returns bounded metadata only — never prompts, secrets, or shell
logs, which stay behind the run-events endpoint, and `execution`/`error_summary`
are bounded and scalar-only **when written**, since bounding on the way out
still stores the unbounded value for the next reader of the table to find.

A step's reported status is **what actually happened**, not where the walk
stopped: the newest attempt's own state (running, failed, `needs_input`,
cancelled) outranks position, because "where did this Build stop and why" is the
one question this read model exists to answer. `invalidated` is the exception —
a success the digest chain rejected is a judgement about an attempt rather than
a state it recorded. `waiting` is a UI projection for a queued step behind an
unfinished one, and applies only when no attempt exists at all. Expected digests
are computed from **current durable state** through the same
`build_step_material` the writer uses; when the stage run cannot be resolved the
view reports `staleness_checked: false` and asserts nothing about validity,
rather than comparing against empty material and calling the result a check.

**The material a step is opened against must be the material the read model
recomputes**, or every step reads as stale the instant it succeeds. Two fields
were self-invalidating and are gone from it: the cycle's `db_revision`, which
bumps when this very Build records its own workers, and the stage run's stored
`stage_spec_key`, which `record_worker_runs` writes partway through the same
run. The approved dataset fingerprint and the *currently resolved* stage spec
answer the question that actually matters — "did the material change?", "would
today's contract produce this?" — and are stable within a run.
`build_step_material_for` is public so the writer and the projection share one
query; a fake repository computes both sides from the same stub and proves
nothing, which is why `tests/test_dbtl_build_workflow_execution.py` drives the
real schema through the real adapter.

That rule also decides what may *not* be added. The **capability registry** is
bound to `plan_build`, because registering or retiring a capability changes
which plans are legal and which specialist a phase resolves to, and both sides
can derive the fingerprint from the enum. The runtime environment and a code
revision are deliberately absent: only the running process knows them, so
binding them would make the two sides disagree by construction and report every
step stale the moment it succeeded — they are bound where they are actually
checkable, in Build lineage, which Test reads. Immutable inputs are absent for
the opposite reason: they already reach a phase through `plan_build`'s
input-bound output digest, and binding them twice would only make the reason a
phase invalidated harder to read.

**`dbtl.build_workflow_steps` now drives Build.** With it on, the governed
workflow, its provenance checks, and its review gate move together behind the
same switch:

* **`load_design` refuses before it dispatches.** `live_stage/design_input.py`
  resolves the approved Design attempt, reads it through the project mapping,
  and verifies its content hash against the one the approval bound. A missing,
  unapproved, unreadable, or moved document stops the Build with a specific code
  — `DESIGN_MISSING_OR_STALE` (the governance record does not support a Build)
  versus `DESIGN_UNREADABLE` (the record is fine and the file is not), because
  different people fix them. The worker is then handed a `BuildInputBundle`
  rather than asked to rediscover its own design, which is where a wrong guess
  used to produce plausible work against the wrong plan.
* **`plan_build` proposes a decomposition, and most failures still run.**
  `deerflow/dbtl/build_plan.py` parses a typed `BuildPhasePlan`: `single_phase`
  is a real verdict (a short script is not four phases pretending to be a
  project), and shape problems degrade to one phase *with a recorded note* so a
  reviewer can tell "it did not decompose" from "we could not read the planner".
  An **unregistered capability is the exception and stops the plan**
  (`needs_input`, naming the capability and offering the ones that exist).
  Collapsing it to one software-engineering phase was the silent swap wearing
  the degradation rule's clothes: the work still ran, under a capability nobody
  asked for, and the only trace was a note. The plan is content-addressed, and
  its digest excludes the rationale so rewording it cannot invalidate finished
  phases — but what `plan_build` *records* is `plan_output_digest`, the plan
  folded together with the input digest it was drawn from, because two
  different approved Designs can imply the same decomposition and a phase
  chained to the plan's own content would survive a Design change that reshaped
  the work.
* **Each phase is its own attempt, and succeeds only once its outputs are
  published.** Phases run sequentially through `live_stage/build_phases.py`,
  declaring a **capability** and receiving the best registered agent with
  `via_generalist` recorded; a later phase receives the preceding phases'
  outputs as read-only inputs. Publication used to happen once, after every
  phase had already been settled from the worker's own JSON, so a phase naming a
  file that was missing, escaped its workspace, or changed underneath it left a
  *reusable success* in the chain; each phase's bytes are now validated and
  copied into the governed tree before its row is settled. A phase's unit id
  carries its step attempt, so a retry gets a clean workspace instead of
  inheriting the failed attempt's half-written files. "Generalist" means the
  agent **registered** as one: falling back to whichever candidate sorted first
  would have run a modelling phase on `bash` and recorded it as a stand-in, so a
  capability nothing can cover refuses the phase and names it. Phases sit on
  their own digest chain *beneath* the container: phase N binds phase N−1, while
  `execute_phases` still binds the plan, and a phase's material includes the
  selection outcome so a newly registered specialist invalidates the stand-in's
  run rather than inheriting it. Every phase success also binds the exact
  workspace-input hashes it read. Replay re-hashes those bytes, so editing an
  input between attempts reopens that phase and every dependent phase instead
  of attributing old work to new data.

  A worker may declare either a regular file or a directory as an output.
  `live_stage.workspace.verified_workspace_files` is the shared containment
  rule for Build inputs and outputs: it refuses symlinks and non-regular files,
  constrains output expansion to that worker's isolated grant, and returns a
  stable project-relative file order. The publisher expands a directory into
  individually hashed, content-addressed artifacts, flattens the worker's
  durable `artifact_refs`, and remaps directory/file evidence plus figure paths
  to the governed URIs. An empty directory is not evidence and fails the phase.
  Keep future workspace verification on this resolver instead of adding a
  second directory walker with different containment semantics. Tests:
  `test_dbtl_live_stage_execution.py` and
  `test_dbtl_build_workflow_execution.py::TestABuildStopsBeingOneOpaqueWorker`.

  Build output declarations may use either a full `/mnt/user-data/...` path or
  a grant-relative path rooted in the server-created worker directories
  (`src/`, `tests/`, `config/`, `artifacts/`, `logs/`, or `README.md`). Relative
  output paths are resolved only when the publisher supplies that worker's
  exact containment root; ordinary project inputs retain project-relative
  semantics. Traversal, schemes, host-absolute paths, symlinks, non-regular
  files, and cross-worker references remain refused. The exact relative file
  locator is remapped into manifests and evidence, but a directory is never
  inferred to be an entry point even when it contains one file.

  Build v8 introduced the `structured_rerun_spec` gate, parsed by
  `dbtl.build_execution`, remapped
  from the worker grant to governed published URIs, and stored on the existing
  `DbtlBuildLineageRow`; do not introduce a parallel execution record. The
  lineage writer enforces the pinned spec, so v8+ cannot persist without a valid
  entry point, exact command, seed, declared inputs, non-empty environment,
  configuration list, and expected outputs. Compatible multi-phase records
  merge bound inputs/outputs, while conflicting commands fail closed.
  Historical v4-v7 rows retain an empty object and project as
  `rerun_unverified`. Human-facing rerun prose is derived from the typed command
  and is never execution authority. Tests: `test_dbtl_build_summary_and_deck.py`,
  `test_dbtl_build_test_repository.py`, and
  `test_dbtl_build_workflow_execution.py`.

  **A phased Build's rerun record is written by the server, not asked for.**
  Test re-runs a Build as one command, and a phased Build has one entry point
  per phase; `merge_rerun_specs` keeps a record only while the phases agree, so
  two phases naming different entry points conflicted and the bundle carried no
  rerun record at all. The phase prompt never asked for one either
  (`build_phases.build_prompt` passes the generic `RESULT_CONTRACT`, not the
  Build clause that requests `provenance.rerun_spec`), so `structured_rerun_spec`
  was **unsatisfiable under the phased workflow** for every spec that declares
  it (v9+): a Build could run every planned phase, publish every output, and
  still fail the gate with no message naming the real cause, because the
  incomplete-plan branch reports first and prints an empty "It stopped there:".
  `deerflow.dbtl.build_driver` is the pure renderer: `render_driver_script`
  emits one `set -euo pipefail` shell script running each verified entry point
  in plan order — `set -e` is what makes it a check rather than a
  demonstration, since without it a failed phase is stepped over and the script
  still exits 0, so Test would record a successful reproduction of a Build that
  did not reproduce. Inputs are exported **per phase** because `DBTL_INPUT_n` is
  numbered within a phase, and a union would hand phase two the wrong file under
  the right name. It shares `build_phase_verification.entry_command` with the
  server's own v12 execution, so the driver re-runs a phase exactly the way the
  server ran it and a reproduction failure cannot be an artefact of the driver.
  `_write_build_driver` writes it beside the review package
  (`stage_file_name(kind="rerun")`) and derives the spec, whose `inputs` union
  the phases' runtime inputs with the Build lineage the server bound — a
  pre-v3 manifest reports no `execution_inputs` at all, and lineage is the set
  Test verifies has not changed. The derivation runs **only** when the workers
  produced no record of their own, so a single-phase Build keeps its worker's
  account. Nothing runnable still records nothing, or the gate would be
  satisfied by a promise nobody could perform. Tests:
  `test_dbtl_build_driver.py`, plus
  `test_dbtl_build_workflow_execution.py::TestAPresentationalFailureKeepsTheScience::test_a_phased_build_records_the_servers_own_rerun_driver`.

  **A boundary after the last phase is not a boundary.** `pause_after` stops
  *before the next phase*, so on the final phase there is nothing to stop
  before — but both the replay and fresh-execution paths honoured it anyway.
  The card offered "Continue — runs the remaining 0 phases", whose only real
  option did nothing, and because a pause marks the plan incomplete, a Build
  that had run 3 of 3 planned phases wrote no review package and could never
  reach its human gate. A planner setting `pause_after` on every phase is not
  wrong, so `_stops_at_boundary` reads it as the plan asking to be looked at
  wherever a look is still possible; both paths ask through that one predicate,
  because a boundary honoured on one and skipped on the other pauses a plan that
  cannot then be resumed past it. Tests:
  `test_dbtl_build_workflow_execution.py::TestABuildStopsBeingOneOpaqueWorker::test_a_boundary_after_the_last_phase_is_not_a_boundary`.

  Build v9 introduced the
  `server_verified_phase_manifest` gate reuses the per-phase publisher,
  `phase_done_condition`, `BuildStepRecorder`, and phase output digest. After
  publication remaps worker paths to individually hashed governed files, the
  server requires a version-1 manifest whose `entry_point` is one exact
  published file, whose `declared_outputs` name the complete published set,
  and whose `completion_condition` is byte-for-byte the condition in the
  recorded plan. A directory is never guessed to be an entry point, even when
  it contains one file. Replay rechecks the manifest, current bytes, input
  bindings, and completion marker before reusing a phase. A failure after the
  model's typed response emits a correcting `task_failed` event and persists a
  failed worker result, so the live lane and durable audit do not contradict
  the failed workflow row. Historical v1-v8 attempts keep their pinned
  contracts. Tests: `test_dbtl_build_workflow_execution.py` and
  `test_dbtl_stage_contracts.py`.

  Build phase workers reuse `build_subagent_runtime_middlewares`; do not copy
  the lead-agent chain. That already supplies read-before-write, normalized
  recoverable tool errors, progress, sandbox/output policy, durable context,
  summarization without memory flush, and the token/loop guards while keeping
  delegation, uploads, memory, chat controls, and title generation absent.
  `BuildPhaseCorrectionMiddleware` adds one same-run correction only after an
  explicit failed implementation check. It runs from `after_agent`, after the
  native model guards have recorded their verdict, retains the failed AI report
  so deadline accounting cannot refund the call, and refuses to jump after a
  token/loop/safety stop or forced finalization. Its one-time graph nodes are
  reserved as deadline headroom. A forced-finalized Build phase is a typed
  failure in both the live task event and `collect_results`. Tests:
  `test_build_phase_correction_middleware.py`,
  `test_finalization_deadline_middleware.py`, and
  `test_dbtl_council_seat_events.py`.

  Current Build treats `BuildPhase.skills` as the complete
  per-phase allowlist: the planner may name at most eight enabled skill names,
  the adapter resolves the same per-user registry `SubagentExecutor` uses,
  hashes each winning `SKILL.md`, binds `skill:<name>:sha256:<digest>` into the
  phase input material, and sets the child config's `skills` list (empty means
  none). The live registry is re-resolved after execution; any missing or
  changed skill fails the durable phase and emits a correcting live failure, so
  work cannot commit against a revision it did not finish under. Do not add a
  parallel skill loader or eagerly union passive skill policies.

  `_build_input_artifacts` binds and re-hashes exact workspace files consumed
  by the phase (plus durable datasets), so files read only for
  discovery/orientation do not invalidate execution. Tests:
  `test_dbtl_build_plan.py`, `test_dbtl_build_workflow.py`,
  `test_dbtl_build_workflow_execution.py`, and
  `test_dbtl_live_stage_execution.py`.

  Current Build is `generic:build:v12`, with a 500,000-token worker ceiling. A
  failed implementation check receives one fresh 40,000-token correction
  worker carrying only the refusal and previous staged workspace; it does not
  inherit the first worker's growing ReAct transcript.

  V12 requires phase-manifest v3. `declared_inputs` binds the narrow set of
  workspace files consumed while implementing or executing the phase;
  `execution_inputs` is the subset the declared entry point consumes at
  runtime. The latter must come from the server-issued grant, but it does not
  renumber `DBTL_INPUT_n`: server execution receives the original issued order
  so a script using only `DBTL_INPUT_2` still receives that exact variable.
  `_execute_server_build_command` owns notebook runtime state as part of that
  same grant: it overrides `JUPYTER_CONFIG_DIR`, `JUPYTER_DATA_DIR`,
  `JUPYTER_RUNTIME_DIR`, and `IPYTHONDIR` with paths beneath the phase workspace
  before local or remote sandbox execution. Do not widen `HOME` or grant the
  host configuration directory; structure-only notebook checks should use the
  standard library's `json.tool`, while `nbconvert --execute` is reserved for
  evidence that actually requires executed cells.

  **A path a phase composes for itself is a guess about a filesystem it cannot
  see.** `generic:build:v12` issues the paths instead of asking for them, after
  two workers on one cycle — a registered specialist and the generalist —
  independently wrote the same nonexistent host path into generated code and
  spent 650K tokens between them failing to run it. Agent identity was not the
  variable; the contract was. `build_prompt`'s rerun clause used to instruct
  workers to *"use absolute /mnt/user-data paths for the entry point, inputs,
  and configuration"*, so the failure was specified rather than improvised. That
  sentence is gone: the record still names absolute paths (it is the server's,
  and it is what lets Test re-run the work), while the *code* reads
  `DBTL_INPUT_1`, `DBTL_INPUT_2`, … in declared order (`DBTL_INPUT_COUNT` holds
  how many) and writes beneath `DBTL_WORKSPACE`.
  `deerflow.dbtl.build_grant` is the pure half. `build_input_grant` refuses an
  input outside the grant, because the environment must not become the channel
  by which a foreign path reaches the script the scanner would have refused for
  naming it directly. `scan_foreign_paths` reads a phase's declared source outputs and
  reports absolute literals no granted root covers, compared **segment-wise**
  (`/mnt/user-data-other` starts with `/mnt/user-data` and is a different
  directory). It is deliberately narrow about what it forgives: interpreters and
  devices (`/usr/bin/`, `/dev/`, …) are executables rather than places a Build
  reads its data, a lone `"/"` is a separator far more often than a file, and a
  hardcoded `/tmp` output is refused because it is an ungoverned one. Findings
  are bounded at `MAX_REPORTED_PATHS` for the reader, not for the count.
  `verify_granted_paths` returns a message rather than raising, on the same
  footing as `verify_phase_manifest` — one failed phase with a reviewer-readable
  reason, naming the literal and the line, instead of a lost run. An
  **unreadable** source output is not refused: a compiled or binary one has no
  source to judge, and refusing it would be the check asserting something it
  never read. That case belongs to `server_executed_entry_point`, the sibling
  gate v12 also declares. Before publication, the server runs the declared
  entry point in the run's sandbox under the same exact writable-workspace
  boundary used by model-facing Bash. Numbered inputs and `DBTL_WORKSPACE` are
  injected as environment variables; fixed receipt files provide exit status,
  bounded stdout/stderr logs, and output hashes, all retained in the Build step
  digest chain. Local macOS uses the shared `sandbox-exec` profile builder;
  its server-verification profile also hides the project tree except for the
  phase workspace and issued inputs. Remote providers must pass a `bwrap`
  preflight and run inside an equivalent bubblewrap mount namespace: the
  project mount becomes an empty tmpfs and only those same paths are mounted
  back. A provider without that boundary fails before the entry point runs;
  a local platform without a process-tree write boundary likewise fails
  closed. The refusal is the cheap early
  half; the execution is what actually decides whether the paths were real.
  Build phases use the normal capability selector: a registered specialist wins,
  otherwise the registered generalist is recorded as the stand-in
  (`via_generalist=True`). With neither available, the phase is refused rather
  than silently routed to an arbitrary agent. Tests: `tests/test_dbtl_build_grant.py`,
  `tests/test_dbtl_build_granted_paths.py`.

  Each persisted `subagent.step` AI row also carries that AIMessage's own
  provider usage delta (`input_tokens`, `output_tokens`, `total_tokens`). The
  cumulative `task_running` meter is insufficient here: several messages
  drained together can share one snapshot, and reload used to lose every
  per-ReAct boundary. Tool rows never repeat the meter. The frontend attaches
  it once to visible thinking, or to the first tool row when the model call had
  no visible prose, so the expanded Build worker shows exact input/output cost
  without double-counting a multi-tool turn. Tests:
  `test_subagent_step_events.py`, `core/tasks/steps.test.ts`, and
  `core/tasks/tool-transcript.test.ts`.

  Current Test execution is pinned to `generic:test:v4`. The focused
  `live_stage.test_rerun` module is the sole new owner for the rerun protocol;
  it exists to keep command execution and byte verification out of the already
  broad adapter while reusing `WorkUnit`, the native sandbox `bash_tool`, the
  existing per-unit stage grant, `StageWorkerResult`, and the validity pack.
  Its model-visible tool has no arguments and is return-direct: the exact
  lineage command is held in server-only `WorkUnit.tool_contract`, may run once,
  and writes fixed stdout/stderr/exit-status receipts. The wrapper caps every
  created file at 512 MiB; verification accepts at most 5 MiB per log, 512 MiB
  per output, 2 GiB total outputs, and 5,000 fresh files. Expected outputs must
  have unique, non-reserved filenames and match the approved Build hashes.
  Test overrides the worker's `reproducibility` check with this record; a
  missing historical record becomes `missing`, while nonzero exit, changed
  input/output, timeout/cap, containment failure, or ambiguous output becomes
  `failed`. `TestReviewService` recovers only the server-owned rerun unit id and
  fails closed if a v4 attempt has no such row. V1-v3 remain resolvable and do
  not retroactively acquire the new execution contract. Tests:
  `test_dbtl_test_rerun.py`, `test_dbtl_test_chat_review.py`, and
  `test_dbtl_live_stage_execution.py`.

  **What the run itself published is an input like any other.** A phase's
  declared inputs were judged only against the pre-run workspace snapshot, and
  that snapshot cannot contain an earlier phase's output by construction — the
  server wrote it minutes into the same run. So the normal shape of a
  multi-phase plan was refused: the second phase returned a clean,
  contract-valid result, its step failed with `input_changed_during_execution`
  ("was not present when this Build run started"), and the Build stopped behind
  a recovery card with the finished phase stranded — a message describing the
  snapshot rather than anything wrong with the work. `_build_input_artifacts`
  now also consults `run_published`, an index of what this run has published so
  far keyed the same way `_project_file_snapshot` keys its entries. These are
  **bindings, not exemptions**: the file is re-hashed and must still match the
  hash the publisher recorded, so a governed output altered after publication is
  refused exactly like a source that changed mid-run. The index passed to a
  phase deliberately holds only the phases *before* it — `published` is extended
  with the phase's own outputs after the call — or a phase could bind what it
  just wrote as something it read. Build lineage is then assembled from
  `_PhaseRun.input_artifacts`, the per-phase bindings established as the run
  went, rather than one end-of-run recomputation: the aggregate view cannot
  reconstruct what existed at each phase's own starting point, and its
  non-strict pass silently *dropped* every cross-phase input — so a plan whose
  inputs were all upstream outputs produced empty lineage, which
  `record_build_lineage` refuses, failing the Build at the very end after every
  phase had run. A replayed phase contributes its recorded bindings through
  `_restore_phase`'s fourth return value for the same reason. Tests:
  `tests/test_dbtl_live_stage_execution.py::TestAPhaseMayReadWhatTheRunAlreadyPublished`,
  `tests/test_dbtl_build_workflow_execution.py::TestAPhaseMayBuildOnThePhaseBeforeIt`.

  **A phase does not pay for the Design on every turn.** Whatever sits in a
  phase's prompt is re-sent on every model call it makes, and on a measured
  pilot the inlined Design excerpt was 19,380 characters — 92% of the input
  bundle and roughly 45% of each of that phase's four model calls (43,032 input
  tokens against 4,549 output), for a document `plan_build` had already
  decomposed into that phase's objective, inputs, outputs, and done-condition.
  `_build_phase_context` therefore drops `design_text` while keeping the
  binding: the phase is still told which approved artifact it implements and
  the hash it was approved under, and reads it when it needs the wording.
  `_plan_build` builds its own context and is untouched — it is the one seat
  whose whole job is reading the Design, and it makes a single call. Nothing
  here moves a digest; `BuildInputBundle.digest` already excludes the excerpt.
  Tests:
  `tests/test_dbtl_build_workflow_execution.py::TestAPhaseDoesNotPayForTheDesignOnEveryTurn`.

  A failed phase stops the run rather than
  spending the remaining budget producing evidence nobody planned. A worker's
  typed `needs_input` result raises its exact question through a durable Build
  control (with the optional meeting route) and resumes the same phase with the
  human exchange verbatim; `pause_after` stops at a committed, lease-free
  boundary.
* **A plan that did not finish is not a Build.** Every phase that ran ran
  truthfully, so a plan whose second phase failed — or that stopped at a
  `pause_after` boundary — looks exactly like a completed one to
  `produced_usable_evidence`, and used to write a review package and a deck for
  both: a fraction of the planned work presented as the completed thing a person
  approves and Test measures. The container settles failed, `summarize_results`
  is not opened at all (opening it would settle as a *presentational* code,
  which the UI renders as "the build ran; the write-up broke"), and the reply
  says how far it got. The committed phases stay committed and reusable, which
  is the whole point of recording them separately. The submission boundary
  checks the durable workflow projection too: a Build whose required steps or
  registered review deck are incomplete cannot enter human review merely
  because an artifact row exists.
* **A finished Build has one review channel.** The review Markdown still leads
  the `present_files` payload so evidence binding remains unambiguous, and the
  authenticated HTML deck remains second. Build evidence slides use the exact
  Design deck shell—theme, navigation, motion, print behaviour, and bridge
  slot—and its final **Human gate** slide owns Submit/Approve/Revise/Reject. No
  parallel chat review card is emitted. The deterministic supervisor completion
  text tells the owner to use that slide and then answer the distinct **Start
  Test / Hold here** transition card.
  The Build renderer itself carries the shared inert stage-review controls and
  authenticated feedback bridge whenever its preplanned surface is an
  answerable `stage_review`. Registration without that bridge is forbidden in
  practice: it advertises an actionable surface whose persisted HTML cannot
  emit an intent. Test remains owned by its typed chat cards; this Build
  exception does not move Test authority back into a deck. Once Test has a
  complete server-readable validity pack, the live-stage adapter submits that
  evidence into `awaiting_review` itself; submission is not an outcome verdict,
  and this is what makes the chat card reachable while the Test deck stays
  deliberately inert. A Test left `in_progress` with registered evidence has no
  human path and is a workflow defect.
  `BUILD_DECK_SURFACE_VERSION` participates in Build's deterministic surface
  id and must be bumped when the bridge-bearing Build deck structure changes.
  Design ids deliberately retain their older derivation. This prevents a
  re-render from embedding an id already bound to legacy bytes and then having
  persistence suffix the row id after the file has been written.
* **A read-only summarizer writes the reviewed document, and writes only prose.**
  The `summarizer` and `planner` roles are withheld execution and write tools and
  granted no writable path (`_tools_for_unit`), so "read-only" is a property of
  the seat rather than an instruction in a prompt — the planner's contract
  promised "you write nothing, run nothing, and dispatch nobody" while it held
  the full Build tool set. The summarizer may cite **only** figures the server
  published (an invented one fails `summarize_results` alone), and selecting a
  few for the deck never removes one from the record. It also cannot restate
  evidence: key outcomes and the rerun procedure come from the execution and
  only from it, and its own deviations and limitations are **appended after** the
  recorded ones rather than replacing them. Each of those was `parsed or
  bundle.<field>`, the single edit that turns a caveat the work reported about
  itself into a caveat nobody ever mentioned. `deerflow/dbtl/build_deck.py` then
  renders those contents through the canonical Design shell, with figures
  embedded as `data:` URIs under per-figure and per-deck byte caps; a format it
  cannot inline is named and skipped. Published image artifacts are recovered
  deterministically when a worker omitted optional figure metadata. If neither
  a verified numeric outcome nor a verified figure exists, the runtime writes
  no review package, deck, or feedback surface and emits one recovery Human
  Input Card offering replan, restart, or hold; retrying a summarizer cannot
  manufacture evidence.

**One worker has one output-contract owner.** `WorkUnit.output_contract`
distinguishes the shared `StageWorkerResult` schema from Build's plan, result
summary, and advisory work-meeting contracts. The live task projection selects
that declared parser instead of assuming every final answer has `status` and
`summary`; the generic `collect_results` boundary refuses typed helper units so
a future caller cannot silently restore the duplicate gate. Review-meeting
units do use `StageWorkerResult`, so their prompts include the same result
contract their collector enforces. For long-running Build phases,
`worker_result._compatible_payload` accepts only meaning-preserving drift:
common field aliases, exact status/boolean aliases, path-only artifact objects,
identical redundant evidence locators, and a missing status backed by either an
explicit clarification or the server-required `phase_done_condition`. It never
relaxes workspace containment/publication, claim-to-evidence traceability,
failed quality checks, capped-run handling, phase completion, or human gates.

**A committed step is replayed, not re-run** (`live_stage/step_store.py`). The
step table records that a step succeeded and what its output digest was, which
is enough to judge a later step still valid and nowhere near enough to skip
re-running it: a digest is not a plan and not a worker's structured result. So
the recorder reported `replayed=True` and every caller dispatched anyway — the
resume was in the record and the re-run was in reality. `succeed` now keeps the
step's own output beside its digest under the stage work root (scratch, never
evidence, already excluded from the manifest and from Build's input snapshot),
and `replay` hands it back. Three rules: a payload is only accepted against the
digest it was recorded under, so a truncated write or a payload from a different
plan costs one re-run rather than filing work nobody did; **writes fail closed**
— an unreadable replay payload costs one fresh attempt, but failing to persist a
new payload or step transition stops the Build with a bounded visible refusal;
and the payload is written *before* the row is settled, since a payload with no
row is unreachable while a row with no payload is a step that reports a replay
and silently dispatches.
`record_worker_runs` skips a unit already recorded on the stage attempt — a run
whose phases all replayed arrives with the same unit ids and the unique index
raised an unhandled `IntegrityError`.

**An Alembic stamp is not proof of the physical step-table shape.** Some manual
databases reached `0027` after applying an early `0026` layout whose uniqueness
still used nullable `phase_key` and which lacked `phase_slot`; Alembic therefore
had nothing left to run while every ORM read failed on the missing column.
`0028_repair_dbtl_step_phase_slot` is the forward repair: it adds/backfills the
non-null slot, preserves every legacy attempt, deterministically renumbers only
NULL-enabled duplicate attempts, cancels only older duplicate running leases,
and rebuilds both uniqueness definitions against `phase_slot`. Downgrade keeps
the repair because this is the schema `0026` already promises, not a new domain
feature.

**"Accepted against the digest it was recorded under" is the caller's job, and
it was nobody's.** `StepOutputStore.load` returns any valid JSON at the
digest-named path, and a shape check answers "is this a phase result?" rather
than "is this *the* result this attempt committed?" — so scratch, which is an
ordinary file inside a folder the person can open, could become review evidence
by being edited into a plausible shape. `phase_output_digest` is now one
function both the writer and `_restore_phase` use (two copies of that
arithmetic would eventually disagree, and the symptom is either a phase that
re-runs forever or an edited payload accepted as work that happened),
`_published_bytes_intact` re-hashes every published output before the phase
counts as replayed, and `_restored_build_plan` recomputes `plan_output_digest`
against **the chain's** `load_design` output rather than the one this run
recomputed — the project manifest rides in the input bundle, so a Build that
wrote outputs moves its own recomputed digest and would invalidate the plan and
every phase beneath it by their own success.

**A re-run that settles nothing is worse than either outcome available.** When
the payload behind a committed digest cannot be produced, the phase (or plan, or
write-up) ran again on a handle that said `replayed`, so `succeed` no-opped: the
work happened, the record still described the previous attempt, and every later
step stayed computed from a digest that no longer described anything on disk.
`BuildStepRecorder.reopen` appends a fresh attempt at the same identity —
`open_step_attempt(force_new_attempt=True)`, the one caller that legitimately
declines the replay — and rewinds the chain cursor to what `begin` bound the
attempt against, so a re-run that then fails leaves the chain where the failure
left it.

**A deck retry must not re-run the summarizer.** A replayed `summarize_results`
still dispatched, and the review document embeds the cycle revision, so the
rewrite hashed differently while the replayed step could no longer record it —
the deck rendered one write-up and the chain named another. The step now keeps
the summarizer's own answer beside its digest and `_restore_build_summary`
re-parses it against the same execution bundle, accepting it only when the
package digest recomputes and the review document on disk still hashes to what
the attempt committed.

**A deck nobody can answer is not a review surface.** `render_review_deck`
succeeded on rendered bytes alone, so a surface plan that returned `None` left a
finished-looking Build whose deck could never carry a verdict — and a
registration that *raised* did so before the step opened, so
`deck_registration_failed` could never be recorded at all. Registration failure
is now recorded against that step first and then re-raised (evidence is already
durable, so the retry is safe), and an unregistered deck fails the step rather
than completing the workflow.

**A boundary somebody already crossed is not a boundary.** Only the control
riding on the *current* request released a replayed `pause_after` phase, so a
plan continued in one turn and stopped later by a presentational failure paused
again at the finished phase on every retry — the same question re-asked forever
with the cheap summary/deck retry unreachable behind it.
`BuildControlGate.boundary_released` reads the durable record instead: an
answered `continue`/`retry` against *this plan digest* (a replan draws different
work, and `hold` is the opposite decision). `start_build` is deliberately not in
that set — it agrees to run the plan, whose first boundary has not been reached,
let alone shown to anybody — and a freshly run phase still stops at its own
boundary, because that one has never been shown.

**A paused Build is a decision waiting to be made, and somebody has to be able
to make it.** A Build stops for four different reasons — the plan is drawn and
nobody has agreed to it, a phase declared a boundary, a step failed, or a worker
cannot continue without one answer — and every one of them used to end the same
way: a paragraph in chat and no way to act. `pause_after` broke the loop and
raised nothing; a failed phase kept its finished work and offered no retry.

`deerflow.dbtl.build_control` is the typed shape all four share, and it owns no
repository, message type, or rendering. Every option **states what choosing it
costs** — Replan says plainly that finished phases are discarded, Retry says
they are kept — because a card listing them side by side without that is
offering a choice nobody can make well. Nothing is preselected, including
`recommended_option_id`, which `resolve_answer` deliberately never consults: a
default that becomes the answer is a decision nobody made. **Hold is a
first-class action** rather than the absence of one, since "the person chose to
stop" and "nobody has answered" lead to opposite behaviour. The four kinds share
one action vocabulary on purpose — a retry card and a pause card both offer
"change the plan", and two verbs for it would mean two code paths that must stay
in agreement about what it does.

`dbtl_build_collaborations` (migration `0027`) records the exchange, because the
step row records *that* a step is waiting and the sentence it waits on but not
which formats were offered, which option was chosen, by whom, or which attempt
resumed — and "who decided to replan" is exactly the question asked when the
phases beneath a plan are gone. `uq_dbtl_build_collab_open` enforces one open
control per stage attempt at the database boundary, since a Build pauses in
exactly one place and a second open request is a stale card competing with a
live one. A superseded control is kept, because it is the record of what
somebody was actually shown.

**An answer names the emission it answers.** The card id is derived so a
retried turn re-renders the same question — which means the same pause
*recurring* mints the same id again, on a second row. Keying the answer on the
id alone made every later answer to a recurring pause collide with the first on
`uq_dbtl_build_collab_submission` and be swallowed by the fail-soft write: Retry
then Replan wrote nothing, the epoch never moved, the committed plan replayed,
and the person's words went nowhere — the exact no-op button the epochs exist to
prevent, arriving through the write path. So a card carries the durable row it
was emitted from (`collaboration_id`), the reply carries it back, and
`answer_build_collaboration` settles *that* row. A redelivered answer to an
earlier emission therefore replays against its own record instead of settling
the control now open, which for Replan and Restart would move the digest chain a
second time and discard committed phases nobody asked to discard again. An
answer naming no emission still settles the open control, and a caller that
supplies no submission id gets payload idempotency: the same action and the same
words replay, anything else conflicts.

**A Build decision is never silently lost.** Raising a control, recording its
answer, and moving a Restart/Replan epoch are all authoritative writes. Any one
that fails raises `BuildControlNotRecorded`; the adapter stops before dispatch,
returns a bounded receipt, and re-presents an existing control when one can be
recovered. Re-presenting is not decoration: a reply counts as answered the
moment it resolves, not when the write lands, so without it the routing fence
has already stood down and the "try again" the message asks for reaches ordinary
chat. Step-attempt recording follows the same rule through
`BuildStepRecordingError`: no recorder means no governed Build and therefore no
review artifact.

**That table is also what makes Restart and Replan mean anything.**
`build_control_epochs` counts answered restart/replan decisions, and
`build_step_material` folds them into `load_design` and `plan_build`. Without
them a restart recomputes the same input digest as the run it is restarting,
`open_step_attempt` replays the committed success, and the button does nothing
at all. They satisfy the recomputability rule because the writer and the read
model count the same durable rows — nothing in the running process is bound. A
restart implies a replan (it begins again above it); a hold moves neither.

The card id is **derived, not random**: a retried turn re-renders the same
question, and a random id would open a second control for it with the record
unable to say which was answered. It is computed from what the pause *is* —
cycle, stage attempt, kind, whether it asks for a choice or for words, step, and
digests. That "choice or words" component is load-bearing: a plan card and the
free-text follow-up it raises are two questions about the same pause, and
without it they derive one id, the second collapses onto the first's already
answered row, and the words the person typed reach nothing.

Answers are resolved against the request the **server** emitted
(`answered_build_control`): a forged request id matches no card, an invented
option id matches nothing on a real one, and either way the request falls
through to ordinary routing rather than starting, replanning, or restarting a
governed Build. While a control is unanswered, a request that would otherwise
become ordinary work is answering *that control*, so `_route_decision` fences it
beside the Start/Hold fence and the supervisor re-presents the card. The escape
is the same shape and the natural reply is worse: "yes, looks good, go ahead"
names no stage, matches no intent phrase, and would otherwise reach a lead agent
that cannot start a build. Fence and handler share one predicate
(`pending_build_control`), because a fence that intercepts a request the handler
then declines falls straight through to stage execution. A stale control is
refused **once**, with the receipt recording which control it closes — repeating
the refusal on every later message would make ordinary work unreachable for the
life of the thread, which is worse than the escape. That marker is server-owned
and stripped from external run input beside `STAGE_HANDOFF_REFUSED_KEY`: the
refusal check compares by value across every message, so a caller able to assert
one would release the fence permanently. An unanswered control also outranks the
parked-Design route, which otherwise sends cycle-scoped work to the lead agent —
the fence's premise is that no path to ordinary work skips it. `build_dbtl_status_reminder`
also names a paused Build in the lead agent's read-only snapshot: it cannot
answer one, but a lead agent that does not know a build is paused will
cheerfully offer to run one.

"Change the plan" is a second exchange on purpose: the person's words travel
verbatim into the replan rather than being paraphrased into a planner-owned
decision. A free-text answer to a plan card is recorded as a **replan** rather
than a generic reply, because that is what the decision is — recording it
otherwise would leave the epoch unmoved. Continuing past an answered boundary
does not re-present it (that is what Continue meant), while a boundary not yet
reached still stops.

Both workers that can pause a Build raise a bound control. The planner's
`needs_input` used to render as a Design clarification card whose answer went
wherever the next request went; the summarizer's was recorded as a refusal,
which reads as "fix something" when it means "decide something". `needs_input`
stays out of the failure taxonomy — the summary step settles as waiting rather
than failed, the execution behind it stays selected, and the answer resumes the
write-up rather than the Build — and the paused step records which control holds
its question. **The answer reaches the worker that asked**: it is quoted into
that worker's next attempt (`owner_answer_to_your_question` for the planner, an
appended block for the summarizer). Without that the worker re-runs on
byte-identical inputs, asks the same question, and re-derives the same card id
— an ask loop with no exit. A summarizer question also outranks a pause card
derived from where the Build stopped, since it is already recorded and already
bound to the step waiting on it.

**The Build work meeting** (`live_stage/build_meeting.py`,
`generic:build-work-meeting:v1`, behind `dbtl.build_work_meetings`) is the
escalation for a question one exchange cannot settle. Three rules keep it from
becoming an expensive replacement for a one-line answer. It is **convened, never
self-started** — no model opens a meeting by labelling its own question complex,
so the module builds seats and parses a result but never decides to run. It is
**advisory**: three seats (implementation position, red team, chair), and the
same question comes back with the briefing above it, because only the person's
answer resumes anything; a meeting that could resume the work would be a second,
unreviewed approval path. And it **reads without writing** — no workspace grant,
since the later phase attempt remains the single writer. The briefing lists
options before the recommendation (a reader already told what to pick reads the
alternatives as objections), a recommendation naming nothing on the table costs
the label rather than the meeting, and a provider outage costs the advice while
leaving the question answerable directly.

**A step left running by a dead process is reclaimed.** The one-running-attempt
rule has no expiry, so a Gateway killed mid-phase left a row blocking that step
forever and — recording being fail-soft — every later Build stopped recording
rather than saying so. `open_step_attempt` settles a `running` row from a
*different* run, older than `_ORPHAN_RECLAIM_SECONDS` (6 hours), as `cancelled`
rather than `failed`: work taken away and work gone wrong are different things
to a reader, and nobody observed this one fail. The window is deliberately far
longer than any real step, because reclaiming early would settle a live worker's
row and admit a second dispatch beside it. A same-run row is never reclaimed.

The read model also carries the **recorded plan** (`plan`), flattened onto the
`plan_build` row's bounded `execution` map by `_plan_execution` and read back by
`_recorded_plan`. A queued phase has no row of its own, so without it the view
can say a Build has four phases and name none of them — the difference between a
plan a person can check and a progress bar. Each phase row also carries its own
title, so a running phase reads the same way.

Still not implemented: retrying one step from a button in the read model (the
retry card owns mutation deliberately — §8.2), and the armed-composer reply chip
(§7.2); a free-text control is a visible card instead, which §7.3 permits.
Tests: `tests/test_dbtl_build_workflow.py`, `tests/test_dbtl_build_step_runs.py`,
`tests/test_dbtl_build_input_bundle.py`, `tests/test_dbtl_build_plan.py`,
`tests/test_dbtl_build_summary_and_deck.py`,
`tests/test_dbtl_build_workflow_execution.py`,
`tests/test_dbtl_build_control.py`, `tests/test_dbtl_build_collaborations.py`,
`tests/test_dbtl_build_control_cards.py`,
`tests/test_dbtl_build_control_flow.py`,
`tests/test_dbtl_build_work_meeting.py`, plus the workflow cases in
`tests/test_dbtl_cycles_router.py`.

**Progressive-gate Phases 2-3:** migration
`0025_dbtl_stage_feedback_surfaces` adds `stage` and `surface_revision` to the
existing feedback tables instead of renaming them. This preserves foreign keys,
captured Design surface ids, and the `design_deck_feedback` rollback path.
`DesignFeedbackOpsMixin` exposes stage-first registration/read/action methods
and retains Design-named wrappers; `deerflow.dbtl.stage_feedback` is the
server-side intent matrix. `/stage-feedback/` is canonical and
`/design-feedback/` remains an alias.

**A feedback surface belongs to a stage, not to Design.** `_FeedbackSurfacePlan`
carries `stage` and `round_number`, `_plan_feedback_surface(stage=...)` binds
that stage's own `dbtl_stage_runs` attempt, `_register_feedback_surface` records
it, and `_write_council_deck(stage=...)` writes under that stage's output
directory with its own deck title. The stage is deliberately **absent from the
surface-id digest**: Design ids were derived before stages were a parameter, and
adding one would move every already-registered Design surface off the row a
retried turn must land back on — one execution never spans two stages, so the
attempt id inside `execution_key` already separates them. Design output stays
byte-identical, pinned by `test_dbtl_deck_fixture_drift.py`. Tests:
`tests/test_dbtl_stage_meeting_surface.py`.

The Phase 3 policy boundary is `deerflow.dbtl.stage_meetings`: `routine` skips
a meeting, `standard` makes it optional, and `high_stakes` locks transition
routes until completion unless a human records an explicit downward override.
Review contracts are pinned as `generic:test-review:v1`,
`generic:build-review:v1`, and `generic:learn-review:v1`. Meeting output is
attached to immutable core evidence through a sanitizer that removes Test
outcome and Learn promotion/publication claims. Each stage flag under
`dbtl.stage_meetings` defaults false and is reported through `/api/features`.
The meeting artifact is an annotation, never the stage result: gate submission,
park, review verdicts, Test validity assessment, feedback-surface currency, and
the successor deck all select the latest non-`<stage>_review_meeting` artifact.
This prevents a completed chair summary from replacing the Build record, Test
validity pack, or Learn synthesis in the eventual evidence-bound human record.
Non-Design decks render their own stage label and a real
`convene_review_meeting` control; the server still enables only the intents in
the authenticated surface read model. A terminal background meeting run with
no successor surface marks the initiating ledger action failed/retryable, just
like a failed Design chair resume. `dbtl_round_watch` posts the bounded failure
notice to the originating conversation on an error/timeout/interruption and on
the audited-worker case where the parent reports success but no successor
surface appears after the consistency grace period.

**A card's tool-call id must satisfy every provider it may be replayed to.**
`supervisor.card_request_id` builds every card/present-files id as
`<prefix><cycle-token>__<digest>` and caps it at `MAX_CARD_REQUEST_ID_CHARS`
(64). Embedding a full cycle id produced 74 characters, which Anthropic accepts
and OpenAI's Responses API rejects — so the card succeeded and then *every*
later GPT turn in that thread failed with `Invalid 'input[N].call_id': string
too long`, on an unrelated request, unrecoverable from inside the conversation.
Bounding new ids cannot rescue a thread that already contains one, so
`DanglingToolCallMiddleware._shorten_overlong_tool_call_ids` also rewrites
over-long ids in the **model-bound request** (never the checkpoint), on both
halves of the call/result pair together — renaming one without the other trades
a length error for an orphaned tool result. `CodexChatModel` now reads a
streamed error body before raising, so a provider 400 names its own cause
instead of reporting a bare status. Successful HTTP streams may still terminate
with `response.failed`, `response.incomplete`, or `error`; those SSE frames are
terminal failures too, and `_stream_failure_message` carries their provider
code/reason/message into the worker error instead of reducing all three to
“stream ended without response.completed”. When thinking is disabled the Codex
factory defaults `reasoning_effort` to `none`, but a model-specific
`when_thinking_disabled.reasoning_effort` wins; this is required for
GPT-5.3-Codex-Spark, whose lowest accepted effort is `low`. The same model
rejects the optional `reasoning.summary` payload member, so
`CodexChatModel.include_reasoning_summary=false` omits it without changing
reasoning effort.

The Design council preflight is a native `single_choice` Human Input request;
its options carry `id`/`label`/`value` plus descriptions and the server-owned
recommendation. User-facing wording calls it the **design meeting** (card
titles, notes, review Markdown); internal identifiers keep the council
vocabulary. The card additionally carries `council_participants` — one
editable entry per seat, built by `deerflow.dbtl.council_settings` from the
same `CouncilPlan` dispatch runs, prefilled with the roster writer's
suggestions (model, reasoning strength, instructions = the seat's brief) plus
an explicit “metered, no cap” token policy. Edits come back on the reply's
`participants` key (read off
the raw payload, since the typed reader strips unknown keys), are validated
field by field against the configured models, must name a
card the server emitted, apply only to the answering turn, and land on both
the recorded plan (`apply_participant_settings`) and the dispatched
`WorkUnit`s: per-seat `model`, `reasoning: "extended"` →
`SubagentExecutor(thinking_enabled=True)`,
and instructions quoted verbatim into that seat's prompt — unless byte-identical
to the prefill, which is the writer's suggestion, not the owner's words.
Legacy replies may still carry `max_tokens`; it is recorded for compatibility
but `_token_limit_for_worker` ignores it whenever the council depth has
`token_limit_enforced=false`, so a cached card cannot restore the kill switch.
The card also serializes the question-specific `council_proposal`.
`_confirmed_council_proposal` recovers it only from the matching server-emitted
card and passes it as `approved_council_proposal`; `LiveStageAdapter.execute`
trims its positions to the confirmed depth and replays it without invoking the
roster writer again. Red-team and chair work units take their agent and model
from that approved plan rather than copying the first position. A second roster
model call here is a correctness bug: it can replace the seats and model edits
the person just approved. A paused chair note counts roles from their
`WorkUnit.role` values (independent positions exclude the red team), and names
the synthesis as partial when any non-chair participant returned no trustworthy
result. Router-created clarification pairs do not necessarily fire
model or tool callbacks, so `RunJournal` final reconciliation discovers
allowlisted pairs only after the current run's input message and persists the
ToolMessage artifact. Retained cards before that input are never re-journaled.

Live council terminal events report the validated stage contract, not merely a
stopped child graph. Prose, malformed structured output, blocked/failed output,
and unusable capped output emit `task_failed`, matching
`dbtl_stage_worker_runs`; a capped failure names the guardrail and keeps the
worker's partial success-like summary out of the error channel. Only
contract-valid completed or needs-input results
emit `task_completed`. The automatic kickoff is a hidden
`dbtl_design_kickoff` HumanMessage so its owner answers reach
`_latest_cycle_request_text` without appearing as user-authored chat.

`threads_meta` carries nullable `workspace_id` and `project_id` plus explicit
`scope_type` and `visibility`. Existing and newly projectless conversations
default to `inbox` / `private-owner`; never infer workspace sharing from a
missing project id.

### Plan Mode

TodoList middleware for complex multi-step tasks:

- Controlled via runtime config: `config.configurable.is_plan_mode = True`
- Provides `write_todos` tool for task tracking
- One task in_progress at a time, real-time updates

See [docs/plan_mode_usage.md](docs/plan_mode_usage.md) for details.

### DBTL conversation and worker convergence

`dbtl_cycles.originating_thread_id` is immutable creation provenance. It is
nullable for legacy/admin creation, is validated against authenticated project
membership at the API boundary, intentionally has no foreign key to thread
metadata, and is excluded from the governance projection hash.

Human Input controls have a stable `request_id`/tool-call id for the durable
decision and a unique message id for each physical delivery. Run-journal
boundary comparison is message-id-first while within-run tool-result
deduplication remains tool-call-first. Gateway normalization mints ids for
id-less run inputs.

`GET /api/threads/{thread_id}/stage-worker-events` is the narrow all-run read
model for durable DBTL `subagent.start`/`subagent.end` convergence. Detailed
steps remain run-and-task scoped. Terminal events may carry bounded
`display_summary`; raw typed results remain persisted for audit.

### Context Summarization

Automatic conversation summarization when approaching token limits:

- Configured in `config.yaml` under `summarization` key
- Trigger types: tokens, messages, or fraction of max input
- Keeps recent messages while summarizing older ones
- Manual compaction uses `POST /api/threads/{id}/compact`, reuses the same
  `DeerFlowSummarizationMiddleware`, writes a new checkpoint with updated
  `messages` and `summary_text`, and bumps only those channel versions.
  The route uses the shared `reserve_checkpoint_write()` boundary (also used by
  manual state updates). Its short-lived `checkpoint_write` thread operation
  shares the durable active-thread uniqueness constraint with run admission,
  preventing either worker-local or cross-worker checkpoint-write races.

See [docs/summarization.md](docs/summarization.md) for details.

### Vision Support

For models with `supports_vision: true`:

- `ViewImageMiddleware` processes images in conversation
- `view_image_tool` added to agent's toolset
- Images are converted to base64 and injected into a hidden message carrying both a reserved ID prefix and a server-owned metadata marker for the model call; Gateway strips that marker from untrusted input, and the middleware requires both identifiers before removing the message. The `before_model` and `model` node checkpoints for that call still contain the payload; after `after_model` cleanup, subsequent checkpoints retain only lightweight `viewed_images` metadata, while client-chosen IDs survive

## Code Style

- Uses `ruff` for linting and formatting
- Line length: 240 characters
- Python 3.12+ with type hints
- Double quotes, space indentation

## Documentation

See `docs/` directory for detailed documentation:

- [CONFIGURATION.md](docs/CONFIGURATION.md) - Configuration options
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) - Architecture details
- [API.md](docs/API.md) - API reference
- [SETUP.md](docs/SETUP.md) - Setup guide
- [FILE_UPLOAD.md](docs/FILE_UPLOAD.md) - File upload feature
- [PATH_EXAMPLES.md](docs/PATH_EXAMPLES.md) - Path types and usage
- [summarization.md](docs/summarization.md) - Context summarization
- [plan_mode_usage.md](docs/plan_mode_usage.md) - Plan mode with TodoList
