# Stable Project Memory

This file is the stable memory contract for the project. It defines the shared workflow and structure that human collaborators and AI assistants rely on.

Do not casually rewrite this file. Change it only when intentionally updating the project's shared assistant workflow.

## What This Project Tracks

Project memory lives in plain Markdown files inside the repository so humans and AI assistants can all work from the same source of truth.

Goals:

- humans stay in control of research direction and interpretation
- assistants help with coding, documentation, and routine project maintenance
- project context stays portable instead of being trapped inside one AI tool

## Core Tracking Files

| File | Purpose | When to update |
|------|---------|----------------|
| `doc/PROJECT_STATUS.md` | What is done, active, and next | After a phase changes |
| `doc/DECISIONS.md` | Important choices and rationale | When direction or method changes |
| `doc/DAILY_LOG.md` | One record per meaningful commit | After each commit |
| `doc/WORKLOG.md` | Session notes and handoff context | End of a work session |
| `doc/ENVIRONMENT.md` | Local and compute setup notes | When environment changes |
| `doc/DATA_PROVENANCE.md` | Data origins, ownership, and constraints | When data are added or changed |
| `doc/REVIEW_QUEUE.md` | Human decisions needed before proceeding | When a review gate opens or closes |

## Research Workflow

1. **Decide before you automate** — review status, decisions, and provenance before substantial new work. If the work changes research direction, methodology, or interpretation, a human should decide first.
2. **Keep analysis reproducible** — record random seeds, package versions, and enough metadata to regenerate important outputs.
3. **Keep the repository light** — do not commit files larger than 100 MB. Use `largedata/` for large working files.
4. **Record work as you go** — append one entry to `doc/DAILY_LOG.md` after each meaningful commit.
