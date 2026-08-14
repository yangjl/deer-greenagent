# Current-Tip Gate Repair and Run 6–7 Replay

**Status:** Completed 2026-08-11; current-tip gates repaired, canary passed,
Run 6 replay completed, and Run 7 completed fail-closed

## Goal

Repair the current `fix/build-bugs` tip until the required backend, Ruff, and
frontend gates are green. Then replay Run 6 from its durable Build recovery
state and execute Run 7 through the governed DBTL workflow.

## Budget authorization

Run 6 replay and Run 7 have no model-token ceiling. This removes the proposed
additional shared 500,000-token cap. Human gates, durable DBTL authority,
fail-closed evidence checks, and stop controls remain unchanged; unlimited
tokens do not authorize bypassing a gate or manufacturing evidence.

## Repair scope

Use the smallest fixes that restore required proof:

1. Classify the new `craft_memory` framework block in the shared untrusted-input
   sanitizer and add explicit lead/subagent regression coverage.
2. Update stale migration-head assertions from revision `0031` to the actual
   current head, `0033_dbtl_evidence_exception_assessment`, without changing
   migrations or production persistence behavior.
3. Apply only the mechanical Ruff and Prettier formatting required by the
   repository-wide gates.
4. Diagnose the default Turbopack build separately. Change product code only
   if a reproducible repository bug—not worktree isolation, a running dev
   server, sandboxing, or resource pressure—causes the failure.

The Build/Test coordinator decomposition warning remains outside this repair:
it is an architectural follow-up, not the cause of the failed runtime gates.

## Verification and execution order

1. Prove the sanitizer regression red, apply the shared denylist fix, and prove
   the focused security tests green.
2. Run every migration test containing a current-head assertion.
3. Run the complete current-tip DBTL backend selection, Ruff lint/format,
   frontend unit tests, ESLint/TypeScript, Prettier, the default production
   build, and `git diff --check`.
4. If any required check remains red, stop and update the campaign ledger; do
   not start a model run.
5. When green, execute the small Build → Test canary covering both an
   input-consuming and self-contained rerun contract.
6. Replay Run 6 from its durable Build recovery control, verify the deck after
   refresh, and compare persisted stage, artifact, surface, and lineage records.
7. Update the campaign ledger with exact evidence. Start Run 7 only after Run 6
   is clean and the review gate has no unresolved blocker.

## Non-goals

- No gate weakening, migration rewrite, direct database repair, hidden state
  transition, or Lead-agent substitute for governed stage work.
- No unrelated adapter refactor or formatting beyond files required by the
  configured checks.
- No commit, push, or pull request without separate authorization.
