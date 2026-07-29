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
  the gate or rerun the council. When a chair-resume run ends without producing
  a follow-up deck—for example, after a provider outage—the original choice and
  comment remain in place and the same answer becomes retryable instead of
  leaving Design permanently stuck. Because the resumed chair runs in the
  background, DeerFlow also writes a durable Design-meeting card into the main
  conversation. It reconstructs the independent position and red-team lanes
  from the recorded worker results, shows the chair synthesizing, and updates
  from the durable worker endpoint when the chair reports; the slide deck does
  not become a second conversation surface. Each durable lane opens the
  participant’s complete recorded report. When synthesis finishes, the review
  Markdown and registered approval/request-changes/reject deck are delivered as
  normal chat artifacts.
  Registered feedback surfaces are stage-aware while retaining compatibility
  with captured Design decks: the artifact parent labels the owning stage,
  lifecycle (`open`, `consumed`, or `superseded`), and surface revision, and a
  superseded deck points to its replacement. Build, Test, and Learn review
  meetings have independent default-off rollout flags; their pinned contracts
  preserve the computed Test outcome and keep Learn promotion/publication as
  separate human actions.
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

<a href="https://trendshift.io/repositories/14699" target="_blank"><img src="https://trendshift.io/api/badge/repositories/14699" alt="bytedance%2Fdeer-flow | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>
> On February 28th, 2026, DeerFlow claimed the 🏆 #1 spot on GitHub Trending following the launch of version 2. Thanks a million to our incredible community — you made this happen! 💪🔥

DeerFlow (**D**eep **E**xploration and **E**fficient **R**esearch **Flow**) is an open-source **super agent harness** that orchestrates **sub-agents**, **memory**, and **sandboxes** to do almost anything — powered by **extensible skills**.

https://github.com/user-attachments/assets/a8bcadc4-e040-4cf2-8fda-dd768b999c18

> [!NOTE]
> **DeerFlow 2.0 is a ground-up rewrite.** It shares no code with v1. If you're looking for the original Deep Research framework, it's maintained on the [`1.x` branch](https://github.com/bytedance/deer-flow/tree/main-1.x) — contributions there are still welcome. Active development has moved to 2.0.

## Official Website

Learn more and see **real demos** on our [**official website**](https://deerflow.tech).

## Sister Projects

<img width="446" height="280" alt="image" align="middle" src="https://github.com/user-attachments/assets/077edef4-d560-41af-bb0d-d0a5f14fcc20" />

- [**LLM Space**](https://github.com/deer-flow/llm-space) - Meet our secret weapon behind DeerFlow — one desktop tool to prototype agent ideas, inspect each harness step, replay failures, and benchmark performance.

## Coding Plan from ByteDance Volcengine

- We strongly recommend using Doubao-Seed-2.0-Code, DeepSeek v3.2 and Kimi 2.5 to run DeerFlow
- [Learn more](https://www.byteplus.com/en/activity/codingplan?utm_campaign=deer_flow&utm_content=deer_flow&utm_medium=devrel&utm_source=OWO&utm_term=deer_flow)
- [中国大陆地区的开发者请点击这里](https://www.volcengine.com/activity/codingplan?utm_campaign=deer_flow&utm_content=deer_flow&utm_medium=devrel&utm_source=OWO&utm_term=deer_flow)

## InfoQuest

DeerFlow has newly integrated the intelligent search and crawling toolset independently developed by BytePlus--[InfoQuest (supports free online experience)](https://docs.byteplus.com/en/docs/InfoQuest/What_is_Info_Quest)

<a href="https://docs.byteplus.com/en/docs/InfoQuest/What_is_Info_Quest" target="_blank">
  <img
    src="https://sf16-sg.tiktokcdn.com/obj/eden-sg/hubseh7bsbps/20251208-160108.png"   alt="InfoQuest_banner"
  />
</a>

---

## Table of Contents

- [🦌 DeerFlow - 2.0](#-deerflow---20)
  - [Official Website](#official-website)
  - [Coding Plan from ByteDance Volcengine](#coding-plan-from-bytedance-volcengine)
  - [InfoQuest](#infoquest)
  - [Table of Contents](#table-of-contents)
  - [One-Line Agent Setup](#one-line-agent-setup)
  - [Quick Start](#quick-start)
    - [Configuration](#configuration)
    - [Running the Application](#running-the-application)
      - [Deployment Sizing](#deployment-sizing)
      - [Option 1: Docker (Recommended)](#option-1-docker-recommended)
      - [Option 2: Local Development](#option-2-local-development)
    - [Advanced](#advanced)
      - [Sandbox Mode](#sandbox-mode)
      - [MCP Server](#mcp-server)
      - [IM Channels](#im-channels)
      - [LangSmith Tracing](#langsmith-tracing)
      - [Langfuse Tracing](#langfuse-tracing)
      - [Monocle Tracing](#monocle-tracing)
      - [Using Multiple Providers](#using-multiple-providers)
  - [From Deep Research to Super Agent Harness](#from-deep-research-to-super-agent-harness)
  - [Core Features](#core-features)
    - [Skills \& Tools](#skills--tools)
      - [Claude Code Integration](#claude-code-integration)
    - [Session Goals](#session-goals)
    - [Manual Context Compaction](#manual-context-compaction)
    - [Sub-Agents](#sub-agents)
    - [Sandbox \& File System](#sandbox--file-system)
    - [Context Engineering](#context-engineering)
    - [Long-Term Memory](#long-term-memory)
  - [Recommended Models](#recommended-models)
  - [Embedded Python Client](#embedded-python-client)
  - [Scheduled Tasks](#scheduled-tasks)
  - [Terminal Workbench (TUI)](#terminal-workbench-tui)
  - [Documentation](#documentation)
  - [⚠️ Security Notice](#️-security-notice)
    - [Improper Deployment May Introduce Security Risks](#improper-deployment-may-introduce-security-risks)
    - [Security Recommendations](#security-recommendations)
  - [Contributing](#contributing)
  - [License](#license)
  - [Acknowledgments](#acknowledgments)
    - [Key Contributors](#key-contributors)
  - [Star History](#star-history)

## One-Line Agent Setup

If you use Claude Code, Codex, Cursor, Windsurf, or another coding agent, you can hand it the setup instructions in one sentence:

```text
Help me clone DeerFlow if needed, then bootstrap it for local development by following https://raw.githubusercontent.com/bytedance/deer-flow/main/Install.md
```

That prompt is intended for coding agents. It tells the agent to clone the repo if needed, choose Docker when available, and stop with the exact next command plus any missing config the user still needs to provide.

## Quick Start

### Configuration

1. **Clone the DeerFlow repository**

   ```bash
   git clone https://github.com/bytedance/deer-flow.git
   cd deer-flow
   ```

2. **Run the setup wizard**

   From the project root directory (`deer-flow/`), run:

   ```bash
   make setup
   ```

   This launches an interactive wizard that guides you through choosing an LLM provider, optional web search, and execution/safety preferences such as sandbox mode, bash access, and file-write tools. It generates a minimal `config.yaml` and writes your keys to `.env`. Takes about 2 minutes.

   The wizard also lets you configure an optional web search provider, or skip it for now.

   Run `make doctor` at any time to verify your setup and get actionable fix hints.
   If you are opening a GitHub issue about a local setup or runtime problem, run
   `make support-bundle`. The command prints reporter next steps, writes a
   `*-issue-summary.md` file to paste into the issue, a `*-issue-draft.md` file
   for AI-assisted issue filing, and an optional evidence zip under
   `.deer-flow/support-bundles/`. If an AI assistant files the issue, start from
   the draft and replace every REQUIRED placeholder instead of inventing missing
   facts. Attach the zip only if a maintainer asks for it, or if the summary
   alone is not enough. Maintainers and AI triage tools can start with
   `triage.json`; the bundle includes redacted diagnostics and file manifests
   only, and does not include `.env`, raw conversation messages, or user file
   contents.

   > **Advanced / manual configuration**: If you prefer to edit `config.yaml` directly, run `make config` instead to copy the full template. See `config.example.yaml` for the complete reference including CLI-backed providers (Codex CLI, Claude Code OAuth), OpenRouter, Responses API, subagent runtime caps such as `subagents.max_total_per_run`, and more.

   Optional per-model pricing must use one currency across all priced models.
   DeerFlow disables Console cost estimates when currencies are mixed rather
   than presenting an invalid aggregate.

   <details>
   <summary>Manual model configuration examples</summary>

   ```yaml
   models:
     - name: gpt-4o
       display_name: GPT-4o
       use: langchain_openai:ChatOpenAI
       model: gpt-4o
       api_key: $OPENAI_API_KEY

     - name: openrouter-gemini-2.5-flash
       display_name: Gemini 2.5 Flash (OpenRouter)
       use: langchain_openai:ChatOpenAI
       model: google/gemini-2.5-flash-preview
       api_key: $OPENROUTER_API_KEY
       base_url: https://openrouter.ai/api/v1

     - name: gpt-5-responses
       display_name: GPT-5 (Responses API)
       use: langchain_openai:ChatOpenAI
       model: gpt-5
       api_key: $OPENAI_API_KEY
       use_responses_api: true
       output_version: responses/v1

     - name: qwen3-32b-vllm
       display_name: Qwen3 32B (vLLM)
       use: deerflow.models.vllm_provider:VllmChatModel
       model: Qwen/Qwen3-32B
       api_key: $VLLM_API_KEY
       base_url: http://localhost:8000/v1
       supports_thinking: true
       when_thinking_enabled:
         extra_body:
           chat_template_kwargs:
             enable_thinking: true
   ```

   OpenRouter and similar OpenAI-compatible gateways should be configured with `langchain_openai:ChatOpenAI` plus `base_url`. If you prefer a provider-specific environment variable name, point `api_key` at that variable explicitly (for example `api_key: $OPENROUTER_API_KEY`).

   To route OpenAI models through `/v1/responses`, keep using `langchain_openai:ChatOpenAI` and set `use_responses_api: true` with `output_version: responses/v1`.

   For vLLM 0.19.0, use `deerflow.models.vllm_provider:VllmChatModel`. For Qwen-style reasoning models, DeerFlow toggles reasoning with `extra_body.chat_template_kwargs.enable_thinking` and preserves vLLM's non-standard `reasoning` field across multi-turn tool-call conversations. Legacy `thinking` configs are normalized automatically for backward compatibility. If the endpoint reports a cumulative usage snapshot on every streaming chunk, set `cumulative_stream_usage: true` so DeerFlow converts those snapshots into per-chunk deltas; the option is disabled by default and leaves usage unchanged when a stable completion id is unavailable. Reasoning models may also require the server to be started with `--reasoning-parser ...`. If your local vLLM deployment accepts any non-empty API key, you can still set `VLLM_API_KEY` to a placeholder value.

   CLI-backed provider examples:

   ```yaml
   models:
     - name: gpt-5.4
       display_name: GPT-5.4 (Codex CLI)
       use: deerflow.models.openai_codex_provider:CodexChatModel
       model: gpt-5.4
       supports_thinking: true
       supports_reasoning_effort: true

     - name: claude-sonnet-4.6
       display_name: Claude Sonnet 4.6 (Claude Code OAuth)
       use: deerflow.models.claude_provider:ClaudeChatModel
       model: claude-sonnet-4-6
       max_tokens: 4096
       supports_thinking: true
   ```

   - Codex CLI reads `~/.codex/auth.json`
   - Claude Code accepts `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_CREDENTIALS_PATH`, or `~/.claude/.credentials.json`
   - ACP agent entries are separate from model providers — if you configure `acp_agents.codex`, point it at a Codex ACP adapter such as `npx -y @zed-industries/codex-acp`
   - On macOS, export Claude Code auth explicitly if needed:

   ```bash
   eval "$(python3 scripts/export_claude_code_oauth.py --print-export)"
   ```

   API keys can also be set manually in `.env` (recommended) or exported in your shell:

   ```bash
   OPENAI_API_KEY=your-openai-api-key
   TAVILY_API_KEY=your-tavily-api-key
   ```

   </details>

### Running the Application

#### Deployment Sizing

Use the table below as a practical starting point when choosing how to run DeerFlow:

| Deployment target | Starting point | Recommended | Notes |
|---------|-----------|------------|-------|
| Local evaluation / `make dev` | 4 vCPU, 8 GB RAM, 20 GB free SSD | 8 vCPU, 16 GB RAM | Good for one developer or one light session with hosted model APIs. `2 vCPU / 4 GB` is usually not enough. |
| Docker development / `make docker-start` | 4 vCPU, 8 GB RAM, 25 GB free SSD | 8 vCPU, 16 GB RAM | Image builds, bind mounts, and sandbox containers need more headroom than pure local dev. |
| Long-running server / `make up` | 8 vCPU, 16 GB RAM, 40 GB free SSD | 16 vCPU, 32 GB RAM | Preferred for shared use, multi-agent runs, report generation, or heavier sandbox workloads. |

- These numbers cover DeerFlow itself. If you also host a local LLM, size that service separately.
- Linux plus Docker is the recommended deployment target for a persistent server. macOS and Windows are best treated as development or evaluation environments.
- If CPU or memory usage stays pinned, reduce concurrent runs first, then move to the next sizing tier.

#### Option 1: Docker (Recommended)

**Development** (hot-reload, source mounts):

```bash
make setup
make doctor
make dev
```

Open [http://localhost:2026](http://localhost:2026).

`make docker-start` starts `provisioner` only when `config.yaml` uses provisioner mode (`sandbox.use: deerflow.community.aio_sandbox:AioSandboxProvider` with `provisioner_url`).

Docker builds use the upstream `uv` registry by default. If you need faster mirrors in restricted networks, export `UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple` and `NPM_REGISTRY=https://registry.npmmirror.com` before running `make docker-init` or `make docker-start`.

Local AIO sandbox control traffic is always direct: loopback/private addresses,
single-label cluster hosts, and Docker/Podman internal hostnames do not inherit
`HTTP_PROXY` or `HTTPS_PROXY`. External sandbox FQDNs and public IPs still
honor environment proxy settings.

Backend processes automatically pick up `config.yaml` changes on the next config access, so model metadata updates do not require a manual restart during development.
The checkpoint storage settings `database.checkpoint_channel_mode` and
`database.checkpoint_delta_snapshot_frequency` are exceptions: both are frozen
when the process first builds an agent (including through `DeerFlowClient`) and
require a process restart to change safely.

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

Set it up and capture a checkpoint once:

   The local `make check`, `make install`, `make dev`, and `make start` entry points use a direct `pnpm`/`pnpm.cmd` executable when available and otherwise fall back to `corepack pnpm`. Corepack runs from `frontend/`, so it honors the `packageManager` version pinned in `frontend/package.json`; enabling a global pnpm shim is not required.

2. **Install dependencies**:
   ```bash
   make install  # Install backend + frontend dependencies + pre-commit hooks
   ```

3. **(Optional) Pre-pull sandbox image**:
   ```bash
   # Recommended if using Docker/Container-based sandbox
   make setup-sandbox
   ```

4. **(Optional) Load sample memory data for local review**:
   ```bash
   python scripts/load_memory_sample.py
   ```
   This copies the sample fixture into the default local runtime memory file so reviewers can immediately test `Settings > Memory`.
   See [backend/docs/MEMORY_SETTINGS_REVIEW.md](backend/docs/MEMORY_SETTINGS_REVIEW.md) for the shortest review flow.

5. **Start services**:
   ```bash
   make dev
   ```

6. **Access**: http://localhost:2026

#### Startup Modes

DeerFlow runs the agent runtime inside the Gateway API. Development mode enables hot-reload; production mode uses a pre-built frontend.

| | **Local Foreground** | **Local Daemon** | **Docker Dev** | **Docker Prod** |
|---|---|---|---|---|
| **Dev** | `./scripts/serve.sh --dev`<br/>`make dev` | `./scripts/serve.sh --dev --daemon`<br/>`make dev-daemon` | `./scripts/docker.sh start`<br/>`make docker-start` | — |
| **Prod** | `./scripts/serve.sh --prod`<br/>`make start` | `./scripts/serve.sh --prod --daemon`<br/>`make start-daemon` | — | `./scripts/deploy.sh`<br/>`make up` |

| Action | Local | Docker Dev | Docker Prod |
|---|---|---|---|
| **Stop** | `./scripts/serve.sh --stop`<br/>`make stop` | `./scripts/docker.sh stop`<br/>`make docker-stop` | `./scripts/deploy.sh down`<br/>`make down` |
| **Restart** | `./scripts/serve.sh --restart [flags]` | `./scripts/docker.sh restart` | — |

Gateway owns `/api/langgraph/*` and translates those public LangGraph-compatible paths to its native `/api/*` routers behind nginx.

For workflows that invoke `backend/langgraph.json` through LangGraph Studio or
a direct LangGraph Server, DeerFlow consumes the authenticated identity
published by that runtime and uses it for custom-agent configuration/SOUL, user
skills and skill policy, uploads, thread data, and memory reads/writes. This
keeps authenticated runs out of the shared `default` filesystem bucket, and the
server-owned identity takes precedence over ordinary client-supplied `user_id`
values. External identities such as email addresses are mapped to stable,
collision-resistant directory-safe user IDs before accessing DeerFlow storage.
The default DeerFlow service topology remains the Gateway-embedded runtime
described above.

Gateway runs automatically enforce native delivery for artifacts created or modified under `/mnt/user-data/outputs`: `present_files` must present at least one output produced by the current run, and the terminal `run.delivery` receipt must be durably recorded. Runs that do not produce output artifacts keep ordinary conversational behavior.

DeerFlow's built-in custom events are available through both LangGraph streaming interfaces: native clients can continue subscribing to `stream_mode="custom"`, while callback-based integrations can consume the same payloads as `on_custom_event` records from `astream_events(version="v2")`. The callback event name matches the payload's `type` field.

#### Docker Production Deployment

`deploy.sh` supports building and starting separately:

```bash
make dbtl-manual-init
make dbtl-manual-dev
# Stop at a completed run or human-input boundary:
make dbtl-manual-capture SCENARIO=chair-choice
```

Progressive-gate checkpoints can also record the state the tester expects:

```bash
make dbtl-manual-capture SCENARIO=design-awaiting-verdict \
  PATH_HEAD=design ROUTES=approve,request_changes,reject NEXT_ACTION=approve
```

Then repeat that decision as often as you like. Leave `make dbtl-manual-dev`
attached in its own terminal and swap scenarios from a second one:

```bash
make dbtl-manual-restore-hot SCENARIO=chair-choice
# Then hard-refresh the browser.
```

This takes seconds because only the Gateway is recycled; the frontend and proxy
keep running. It refuses unless the stack is up and the isolated database is
quiet — no in-flight run or review action — and otherwise keeps every restore
safety check.

Restore with the stack stopped when you want a guaranteed-clean process, or
after changing dependencies or config the reload watcher does not see:

```bash
make stop
make dbtl-manual-restore SCENARIO=chair-choice
make dbtl-manual-dev
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

Managed integrations install shared read-only skill packs without mixing them
into custom skills. The Lark/Feishu CLI integration is available under
`Settings → Integrations → Lark / Feishu CLI`; an administrator installs or
upgrades the official `lark-*` pack once under
`{DEER_FLOW_HOME}/integrations/skills/lark-cli`, and every user discovers that
same pack with an independent enabled state. Each user's app configuration and
OAuth data remain isolated under
`{DEER_FLOW_HOME}/users/{user_id}/integrations/lark-cli/{config,data}`. These
secret directories are restricted to `0700`, regular credential files to
`0600`, and symlinks are rejected.

After installation, users can click **Connect Lark** to open a browser
authorization link; no terminal authorization is required. The same UI can
request additional permission domains such as Calendar, Docs, or Drive, or a
specific OAuth scope reported by `lark-cli`. A cheap status refresh only
inspects the local credential tree, so the UI reports **Credentials configured
(not live-verified)** until an explicit browser completion performs live token
verification. The action then remains **Reconnect Lark** so users can replace
or extend authorization. If an agent hits missing Lark authorization during a
conversation, the managed `lark-shared` guidance points the user back to the
same settings entry with `?settings=integrations`.

Installing the Lark skill pack resolves the latest official `larksuite/cli`
release from GitHub and downloads that version's skills at install time, so the
Gateway needs outbound internet access for that step (it falls back to a
bottom-line pinned version if the release lookup fails). The settings page shows
the installed version and, when available, the newest published version so an
admin can reinstall to upgrade. Air-gapped deployments can pre-stage the archive
and point `DEER_FLOW_LARK_CLI_SKILLS_ARCHIVE` at the local file. Integrity does
not depend on a pinned archive byte hash (GitHub does not guarantee stable
source-archive bytes); instead the download is restricted to the official GitHub
host, every archive member passes structural safety guards, and a content hash
of the effective installed skill tree (including DeerFlow's injected shared
guidance) is recorded so content changes are auditable across reinstalls.

When `sandbox.use` selects the AIO provider, the same install also downloads the
official Linux amd64 and arm64 CLI release archives, verifies their published
SHA-256 checksums, safely extracts one executable per architecture, and mounts
the resulting runtime read-only at `/mnt/integrations/lark-cli/runtime`. An
architecture-selecting launcher in that mount makes `lark-cli` available in the
sandbox `PATH`. Air-gapped AIO deployments can pre-stage a symlink-free runtime
tree containing `bin/lark-cli` plus both `linux-{amd64,arm64}/lark-cli` files and
set `DEER_FLOW_LARK_CLI_SANDBOX_RUNTIME_DIR` to that directory.

> **Sandbox trust boundary:** the browser never receives the Lark app secret, but
> agent conversations run `lark-cli` inside the sandbox, so the per-user
> credential directories are mounted into it: `config` (holding the long-lived
> `appSecret`) is mounted **read-only** and `data` (refreshable OAuth tokens)
> writable. Both remain *readable* by any process the agent runs there, so code
> reached via prompt injection in a tool result could read them. Treat the
> sandbox as inside the Lark credential trust boundary until the sidecar
> credential-broker follow-up removes these mounts from sandbox execution.

For remote/Kubernetes deployments (the provisioner backend), the sandbox
`lark-cli` runtime can instead be supplied by an optional init container that
copies the binaries into a shared `emptyDir` — no install-time GitHub download and
no hostPath/PVC runtime mount. Publish the image under
[`docker/lark-cli-init`](docker/lark-cli-init/README.md) and set
`LARK_CLI_INIT_IMAGE` on the provisioner; it stays off (legacy behavior) when
unset. The Lark integration status (`GET /api/integrations/lark/status`) reports
`sandbox_runtime_mode` and `sandbox_runtime_ready` so the Settings UI shows
whether `lark-cli` will actually be present in the sandbox at chat time, rather
than a green status hiding a later `command not found`.

If a trusted operator manages the configured skills directory through an external mount such as MinIO, NFS, or CSI, an administrator can call `POST /api/skills/reload` after changing files. This invalidates skill prompt caches for the current Gateway process and waits up to the bounded refresh timeout so subsequent runs rescan the latest files; running tasks are unchanged. A loader-level filesystem failure returns a generic server error and preserves the last successfully loaded process cache rather than publishing an empty catalog. Uvicorn workers and Kubernetes Pods must each be targeted separately. Direct mount writes bypass the validation, SkillScan, and history applied by DeerFlow's install/edit APIs, so only operator-controlled systems should have write access.

Skill installs and agent-managed skill edits run through **SkillScan**, a native deterministic safety scanner before the LLM-based skill scanner. Phase 1 runs offline with no Semgrep/OpenGrep dependency, blocks high-confidence `CRITICAL` findings such as private keys or shell execution, and passes warning findings to the LLM scanner for contextual review. Python instance-client exfiltration checks follow a minimal same-scope evidence chain: a simple name bound to a known client constructor, optional name-to-name aliases, and an actual outbound method or context-manager use supported by that constructor. Constructor roots must be proven imports; bare canonical-looking names are not inferred as modules. Nested scopes do not inherit client handles and inherit only constructor import aliases that are never rebound in the enclosing scope. Comprehensions, walrus-bearing statements, annotations, complex binding targets, unsupported operations, and ambiguous branch flows produce no finding from this signal; skipped constructs conservatively invalidate every name they may bind so stale client state cannot create a finding. A deterministic work budget or recursion limit reached by this best-effort analysis does not discard findings already collected for the file. Set `skill_scan.enabled: false` in `config.yaml` to disable only the deterministic analyzers; safe archive extraction and the LLM scanner still run.

DeerFlow also ships with **skill-reviewer**, a public skill for read-only skill quality review. It uses the built-in `review_skill_package` tool to inspect installed skills, local packages, archives, or pasted `SKILL.md` content without activating the target skill, binding its secrets, executing its scripts, or installing it. The tool returns a compact, tag-neutralized JSON payload to the model context and keeps the full raw review payload in the tool artifact for programmatic consumers. The deterministic review core reuses DeerFlow parsing and SkillScan facts, emits versioned JSON contracts under `contracts/skill_review/`, and can be run from the backend CLI:

```bash
cd backend
uv run python -m deerflow.skills.review.cli ../skills/public/data-analysis --format text --fail-on error --fail-on-incomplete
```

Tools follow the same philosophy. DeerFlow comes with a core toolset — web search, web fetch, rendered web capture, file operations, bash execution — and supports custom tools via MCP servers and Python functions. Swap anything. Add anything.

Advanced deployments can enable pluggable authorization with `authorization.enabled` in `config.yaml`. A configured `AuthorizationProvider` filters denied tools before they reach the model or deferred-tool catalog, then the same provider is checked again before every business-tool execution through the existing guardrail middleware. Gateway `threads:*` and `runs:*` route permissions are derived from the same provider, while existing owner checks and admin-only management gates remain in force. A generated `tool_search` may bypass the second tool check only when it fronts the current build's already-filtered deferred catalog. The built-in RBAC provider supports per-role `tools` and `routes` allow/deny policies and validates that `default_role` names a configured role; authorization is disabled by default. See `config.example.yaml` and the [authorization RFC](docs/plans/2026-07-10-pluggable-authorization-rfc.md).

Advanced deployments can also extend the agent runtime itself by declaring zero-argument `AgentMiddleware` classes under `extensions.middlewares` in `config.yaml` or `extensions_config.json`. DeerFlow loads the same configured class list into the lead-agent and subagent pipelines after their built-in runtime middlewares and loop/token guards, but before the terminal-response/safety/clarification tail, so enterprise forks can add domain guardrails, tool-call governance, or observability hooks without patching the built-in middleware builders. Missing packages, invalid classes, and broken modules fail loudly at agent creation. Treat `config.yaml` and `extensions_config.json` as trusted operator-controlled files: middleware paths are code execution, just like custom tool, model, sandbox, guardrail, MCP server, and MCP interceptor declarations. Gateway skill/MCP toggle endpoints preserve this field but do not expose an API write path for `extensions.middlewares`. Per-context parameterization and separate lead-only/subagent-only middleware lists are not supported yet.

Gateway-generated follow-up suggestions now normalize both plain-string model output and block/list-style rich content before parsing the JSON array response, so provider-specific content wrappers do not silently drop suggestions.

The Web UI composer can polish draft input before sending. The rewrite runs as a short Gateway LLM request using the `input_polish` model configuration, keeps slash skill prefixes such as `/data-analysis`, and only replaces the local draft after the user clicks the polish button; it does not create a thread run or persist a message.

When the agent asks for clarification, the Web UI shows the structured response card but keeps the normal composer available. Users can complete the card or send a free-form chat message to bypass it; that message closes the latest pending clarification and becomes the agent's next input.

Unsent Web UI composer drafts survive page reloads and switching between conversations within the same browser tab. Drafts are isolated by user, agent, and conversation, include a selected slash skill when present, and are cleared once a send is accepted. Attachments and quoted conversation context are intentionally not persisted.

The Web UI composer also supports browser-based voice dictation when the browser exposes the Web Speech API. The microphone button transcribes speech into the local draft only; DeerFlow receives only the transcribed text, while audio handling is delegated to the browser or operating system speech-recognition service according to that environment's policy. Users can review or edit the text before sending.

The Web UI displays a localized AI-generated-content disclaimer below the composer in both standard and custom-agent conversations, reminding users to verify important
information.

Interrupted first-turn runs still persist a fallback conversation title, so stopping a streaming response does not leave the thread as "Untitled" after refresh.

Streaming Markdown responses animate only newly arrived words; text that is already visible is not faded out and replayed when the next chunk extends the same block.

In the Web UI, completed assistant turns can be branched into a new main conversation. The new thread starts from that turn's checkpoint and keeps the preceding replay checkpoint, so the branched response can be regenerated immediately. The latest response can also be regenerated after an interruption, even when its streamed partial text never reached a checkpoint. Regenerating the latest response preserves the thread's current title, including a title you renamed manually after the original response. Legacy or imported histories without checkpoint parent links use a bounded chronological fallback; if no earlier replay checkpoint exists, branching still succeeds with the legacy single-checkpoint shape, while regeneration remains unavailable for that inherited response. Existing single-checkpoint branches are left unchanged rather than attempting an unsafe checkpoint copy. Because workspace files are not checkpointed, the branch only receives a best-effort copy of the current workspace when you branch from the latest turn; branching from an older turn keeps just the restored message history so the branch never inherits files that were created in a later part of the conversation.

The Web UI reports completed task time once per run. This is total wall-clock time—including model reasoning, tool calls, and waiting—not a per-step or model-only thinking duration. Reasoning content remains available through its own separate disclosure.

In the Web UI, the latest completed user turn can also be edited and rerun from the message toolbar. DeerFlow restores the conversation checkpoint before that user message, submits the edited text as a new user message, and hides the superseded turn once the replay is in progress or succeeds. This is a conversation-state replay only: files, memory updates, and external tool side effects are not undone.

Web UI chat links percent-encode custom thread identifiers before placing them in route segments, so reserved URL characters such as `#` and `?` do not change which conversation is opened.

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

Then uncomment the `group: browser` tool entries in `config.yaml` (`browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_get_text`, `browser_back`, `browser_screenshot`, `browser_close`). `make dev` / Docker startup detects an enabled `browser_navigate` tool and preserves the `browser` extra on dependency syncs. The Gateway fails startup if browser control is configured but Playwright is missing, and `/api/features` hides the Browser UI unless the backend can actually serve it. Keep `headless: true` and `allow_private_addresses: false` for anything but local, trusted debugging. Attaching to an existing Chrome with `cdp_url` cannot enforce DeerFlow's subresource/redirect SSRF guard and therefore fails closed unless `allow_unguarded_cdp: true` explicitly acknowledges that risk; use it only with a trusted local browser. Browser sessions are process-local; keep `GATEWAY_WORKERS=1` while this tool group is enabled because ordinary uvicorn worker dispatch does not provide thread affinity.

### Context Engineering

**Isolated Sub-Agent Context**: Each sub-agent runs in its own isolated context. This means that the sub-agent will not be able to see the context of the main agent or other sub-agents. This is important to ensure that the sub-agent is able to focus on the task at hand and not be distracted by the context of the main agent or other sub-agents.

**Summarization**: Within a session, DeerFlow manages context aggressively — summarizing completed sub-tasks, offloading intermediate results to the filesystem, compressing what's no longer immediately relevant. This lets it stay sharp across long, multi-step tasks without blowing the context window.

**Strict Tool-Call Recovery**: When a provider or middleware interrupts a tool-call loop, DeerFlow now strips provider-level raw tool-call metadata on forced-stop assistant messages and injects placeholder tool results for dangling calls before the next model invocation. This keeps OpenAI-compatible reasoning models that strictly validate `tool_call_id` sequences from failing with malformed history errors.

**Visible Tool-Run Completion**: For interactive turns, DeerFlow retries an empty post-tool final response once, then surfaces a visible error instead of reporting a silent successful run.

### Long-Term Memory

Most agents forget everything the moment a conversation ends. DeerFlow remembers.

DeerFlow also includes an optional `openviking` memory backend. It connects to
an independent OpenViking server over HTTP, submits completed turns through
OpenViking Sessions, and recalls remote memories for prompt injection while
leaving DeerMem as the default. The initial integration supports
`memory.mode: middleware`. Bounded submitted-message watermarks cover long and
compacted histories and prevent a failed Session commit from duplicating
already accepted messages on retry; the shared HTTP client also has explicit
connection limits and jittered retries. See
[OpenViking memory backend](docs/OPENVIKING.md) for configuration and Docker
startup.

Across sessions, DeerFlow builds a persistent memory of your profile, preferences, and accumulated knowledge. The more you use it, the better it knows you — your writing style, your technical stack, your recurring workflows. Memory is stored locally and stays under your control.

Memory updates now skip duplicate fact entries at apply time, so repeated preferences and context do not accumulate endlessly across sessions.

File-backed memory now separates global user context from agent facts. Each user has one `memory.json` containing only the project-independent `user` and `history` summaries; every fact is a canonical Markdown file below `agents/{agent_name}/facts/`. Existing lead-agent middleware, API, Settings, import/export, and embedded-client calls that omit `agent_name` resolve inside DeerMem to the reserved `__default__` bucket. That bucket is outside the valid custom-agent name grammar, so a real custom agent named `lead-agent` has a separate fact repository and deleting a custom agent cannot delete a memory-only directory without `config.yaml`. Public agent identifiers are case-insensitive and canonicalized to lowercase. Runtime/API readers still receive a compatibility `facts` array for the selected/default agent, so the frontend does not read agent facts from `memory.json`; structured Markdown `source` metadata is projected to the historical string field at the MemoryManager boundary. An unscoped Clear All first migrates facts from unread legacy per-agent JSON without adopting its soon-to-be-cleared summaries, then removes shared summaries and facts from every agent bucket while preserving agent configuration files, so a later read cannot resurrect skipped legacy facts; an explicitly agent-scoped clear removes only that agent's facts. On first normal read, old facts embedded in the user JSON are migrated automatically to `__default__`; facts written to the earlier implicit `lead-agent` bucket are also moved when that directory is not a real custom agent. Migration and normal writes notify the configured retrieval adapter only after durable storage locks are released. DeerMem uses a scope-aware SQLite FTS5/BM25 adapter by default, stores only rebuildable derived index data under `.retrieval/`, and rebuilds it in the background during Gateway startup or lazily on the first scoped search. A corrupt derived index is recreated automatically. Set `memory.backend_config.retrieval_adapter` to an empty string to disable it and use the local substring fallback. Chinese tokenization is optional; install the backend `memory-zh` extra (`uv sync --extra memory-zh`) for jieba-assisted sub-phrase search. Journaled writes, a shared user lock, and optimistic user-memory revisions prevent silent lost updates.

Memory injection follows the configured operation mode. In `middleware` mode, DeerMem injects the user-global summaries and the selected agent's facts. In `tool` mode, the automatic `<memory>` block contains only the global `user` and `history` summaries; agent facts are retrieved explicitly through `memory_search`, avoiding duplicate automatic and tool-returned fact context. Setting `memory.injection_enabled: false` still disables the entire block in either mode.

Single-fact repository operations are genuinely incremental: an upsert/delete reads, journals, writes, and re-indexes only the addressed fact files, and returns an explicit incomplete delta rather than a cache-dependent fake full document. Summary change sets merge the supplied `user`/`history` child keys over the persisted sections so a partial update cannot erase omitted siblings; full imports normalize both sections to the complete compatibility schema before applying replacement values. Manager/API compatibility methods materialize a fresh full document only when their public response contract requires one. Fact-level point operations use separate expected user-memory and fact revisions and may explicitly rebase when every addressed fact precondition still holds. Snapshot-derived operations such as scoped clear, capped create, consolidation, and trimming never replay stale delete/trim sets: a manifest conflict reloads the complete document and recomputes the operation, with a bounded retry. Fact paths use the first two hexadecimal characters of `SHA-256(fact_id)` so generated `fact_*` IDs distribute across shards. The cache token combines the shared JSON's nanosecond mtime, size, and persisted revision; this prevents coarse-mtime same-size writes from returning stale data without scanning fact files. Direct out-of-band Markdown edits require an explicit reload. Storage-specific conflicts and corruption are translated at the MemoryManager boundary; the Gateway returns conflict as HTTP 409 and a stable, non-sensitive corruption error as HTTP 500. Full-document `save()` remains a compatibility API and computes a diff before writing; malformed or missing `facts` can no longer silently erase an agent's Markdown files. Legacy migration preserves non-empty `user`/`history` before deleting an agent `memory.json`; conflicting summaries keep the legacy file and fail loudly instead of choosing a winner.

Legacy facts in `memory.json` migrate automatically into the reserved `__default__` Markdown bucket on the user's first normal memory read. Operators who prefer to audit or complete the migration before serving traffic can run the optional idempotent CLI from `backend/`:

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

- [Contributing Guide](CONTRIBUTING.md) - Development environment setup and workflow
- [Configuration Guide](backend/docs/CONFIGURATION.md) - Setup and configuration instructions
- [Architecture Overview](backend/CLAUDE.md) - Technical architecture details
- [Backend Architecture](backend/README.md) - Backend architecture and API reference

## ⚠️ Security Notice

### Improper Deployment May Introduce Security Risks

DeerFlow has key high-privilege capabilities including **system command execution, resource operations, and business logic invocation**, and is designed by default to be **deployed in a local trusted environment (accessible only via the 127.0.0.1 loopback interface)**. If you deploy the agent in untrusted environments — such as LAN networks, public cloud servers, or other multi-endpoint accessible environments — without strict security measures, it may introduce security risks, including:

- **Unauthorized illegal invocation**: Agent functionality could be discovered by unauthorized third parties or malicious internet scanners, triggering bulk unauthorized requests that execute high-risk operations such as system commands and file read/write, potentially causing serious security consequences.
- **Compliance and legal risks**: If the agent is illegally invoked to conduct cyberattacks, data theft, or other illegal activities, it may result in legal liability and compliance risks.

### Security Recommendations

**Note: We strongly recommend deploying DeerFlow in a local trusted network environment.** If you need cross-device or cross-network deployment, you must implement strict security measures, such as:

- **IP allowlist**: Use `iptables`, or deploy hardware firewalls / switches with Access Control Lists (ACL), to **configure IP allowlist rules** and deny access from all other IP addresses.
- **Authentication gateway**: Configure a reverse proxy (e.g., nginx) and **enable strong pre-authentication**, blocking any unauthenticated access.
- **Network isolation**: Where possible, place the agent and trusted devices in the **same dedicated VLAN**, isolated from other network devices.
- **Stay updated**: Continue to follow DeerFlow's security feature updates.

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, workflow, and guidelines.

Backend `make test` is offline by default and excludes live external-API
coverage. Maintainers can explicitly run the real `DeerFlowClient` integration
suite with `cd backend && make test-live` after providing a valid root
`config.yaml` and API credentials; this may incur API costs and create local
sandboxes, artifacts, or files. Direct pytest runs additionally require
`DEER_FLOW_RUN_LIVE_TESTS=1`.

Regression coverage includes Docker sandbox mode detection and provisioner kubeconfig-path handling tests in `backend/tests/`.
Backend blocking-IO diagnostics are available from the repository root with
`make detect-blocking-io`: it statically scans backend business code for
blocking IO that may run on the backend event loop, prints a concise summary,
and writes complete JSON findings to `.deer-flow/blocking-io-findings.json`.
The JSON includes compact review records with `priority`, `location`,
`blocking_call`, `event_loop_exposure`, `reason`, and `code`.
Gateway artifact serving now forces active web content types (`text/html`, `application/xhtml+xml`, `image/svg+xml`) to download as attachments instead of inline rendering, reducing XSS risk for generated artifacts.

## License

This project is open source and available under the [MIT License](./LICENSE).

## Acknowledgments

DeerFlow is built upon the incredible work of the open-source community. We are deeply grateful to all the projects and contributors whose efforts have made DeerFlow possible. Truly, we stand on the shoulders of giants.

We would like to extend our sincere appreciation to the following projects for their invaluable contributions:

- **[LangChain](https://github.com/langchain-ai/langchain)**: Their exceptional framework powers our LLM interactions and chains, enabling seamless integration and functionality.
- **[LangGraph](https://github.com/langchain-ai/langgraph)**: Their innovative approach to multi-agent orchestration has been instrumental in enabling DeerFlow's sophisticated workflows.

These projects exemplify the transformative power of open-source collaboration, and we are proud to build upon their foundations.

### Key Contributors

A heartfelt thank you goes out to the core authors of `DeerFlow`, whose vision, passion, and dedication have brought this project to life:

- **[Daniel Walnut](https://github.com/hetaoBackend/)**
- **[Henry Li](https://github.com/magiccube/)**

## Upstream and license

GreenAgent is a maintained customization of
[ByteDance DeerFlow](https://github.com/bytedance/deer-flow). Upstream changes
are incorporated selectively to protect GreenAgent’s project, memory, and DBTL
architecture.

Licensed under the [MIT License](./LICENSE).
