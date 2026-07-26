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
  cycles, evidence, reviews, and work items.
- **Scoped memory** — project conversations use project-specific memory rather
  than leaking context across unrelated projects.
- **Durable DBTL governance** — cycle state, evidence, blockers, revisions, and
  human reviews are stored in the application database.
- **Human-controlled automation** — AI may recommend or route DBTL work, but it
  cannot satisfy scientific gates or create authoritative results by itself.
- **DeerFlow capabilities** — sandboxed execution, tools, skills, MCP,
  subagents, persistent conversations, and multiple model providers.

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
```

DBTL modes:

- `audit_only` — readiness and legacy inventory are visible; mutations and
  graph execution remain blocked.
- `manual` — authorized humans can operate durable DBTL cycles.
- `graph_enabled` — enables the opt-in project supervisor. Scientific stage
  execution remains behind a non-writing stub until explicitly implemented.

PostgreSQL is the intended production database. SQLite is suitable for local
development but cannot be approved for DBTL production cutover.

## Architecture

| Service | Port | Purpose |
| --- | ---: | --- |
| Nginx | 2026 | Browser entry point |
| Gateway | 8001 | REST API and agent runtime |
| Frontend | 3000 | Next.js workspace |
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
