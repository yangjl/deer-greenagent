# Debate Mode: redesigning the Design council

**Status:** design, not yet implemented. The execution fix in §2 has landed.
**Date:** 2026-07-27

## 1. What actually went wrong

The council did not produce a bad debate. It never held one.

Every seat of the failing run is recorded in `dbtl_stage_worker_runs`:

| unit | capability | agent | via_generalist | status | stop_reason |
| --- | --- | --- | --- | --- | --- |
| `…-1-experimental_design` | `experimental_design` | `general-purpose` | yes | failed | `turn_capped` |
| `…-red-team` | `design_red_team` | `general-purpose` | yes | failed | `turn_capped` |
| `…-chair` | `design_council_chair` | `general-purpose` | yes | failed | `turn_capped` |

All three carry the same summary: *"The worker returned prose instead of a
structured result."* Three independent facts compound here, and a redesign that
fixes only the first would fail the same way.

**No specialists exist.** Nothing in this deployment declares
`subagents.custom_agents.<name>.dbtl_capabilities`, so `select_agents` fell back
to `general-purpose` for every seat. Worse, `agent_selector` only allocates an
*optional* capability when a real specialist covers it, so a generalist-only
project produces exactly **one** position unit — the red team and chair are then
synthesized from it. The "council" was one model, on one prompt lineage, three
times. A reviewer could not have told that from the output, which is the
problem `feat/design-council` was already written to solve.

**A worker that spends its turn budget can never satisfy its contract.** This
is the mechanical cause and it is fixed (§2).

**Design is chair-only gated.** `counts_toward_stage_output` is true only for
the chair, so a failed chair zeroes the stage even when positions succeeded.
Combined with the above, one failure mode discarded everything.

**The chair prompt contradicted its own validator.** Its JSON template showed
`"claims": []` beside `"evidence_refs": []`, while
`StageWorkerResult.__post_init__` rejects any result with claims and no
evidence. A chair that did the right thing was rejected for it.

**The failure note hid all of this.** No package is written when nothing is
trustworthy, so the review Markdown that normally renders "Work units not
included" never reached disk, and the chat said only *"none produced usable
evidence"*.

The council's *shape* — independent positions, a guaranteed red team, a chair
who does not average incompatible views, and a human who owns the gate — is
sound and matches the governance model. Debate Mode should extend it, not
replace it.

## 2. What has landed

`deerflow/agents/middlewares/finalization_deadline_middleware.py`. The turn axis
now behaves like the token and loop axes: warn at three model calls remaining,
then strip `tool_calls` on the last call so the loop ends with a real final
message. `_model_call_budget` halves `budget.max_turns` because
`recursion_limit` counts super-steps and a tool-calling turn costs two.

It deliberately reports **no** `stop_reason`. That channel feeds
`CAPPED_STOP_REASONS` → `is_trustworthy = False`, which is right for a run that
blew through a safety limit and wrong for one that was warned and answered on
time. Forced finalization surfaces as `forced_any()` and becomes a `limitation`
on the result, so the reviewer still sees the investigation was cut short
without the evidence being thrown away.

Also landed: the chair prompt now states the evidence, boolean, and
`clarification_question` rules it is validated against, and the failure note
lists per-worker reasons with the guardrail named separately (a cap and a
contract violation need opposite fixes).

## 3. Debate Mode

### 3.1 Merge `feat/design-council` first

That branch (`3b8b9cdb`, `a3261383`, `08e30e86`) is unmerged and already
contains roughly a third of this: `deerflow/dbtl/council.py` with `CouncilSeat`,
`CouncilPlan`, `plan_council`, three `DepthPolicy` levels, a rule-based
`recommend_depth`, `UnknownAgent` fail-closed seat reassignment, and the
`council_preflight` Human Input card that shows the roster before anyone spends
time. Rebuilding it would be waste; extending it is the plan.

### 3.2 Dynamic council

Today the roster is derived from a closed twelve-value `Capability` enum with
breeding-specific names. That is deterministic and auditable, and both
properties must survive — a run that cannot be reconstructed from its record is
not evidence.

The change is to make the roster *proposed* rather than *derived*:

1. The lead agent proposes seats from the request and project manifest, as
   structured JSON, over the existing `nostream` one-shot path
   (`utils/oneshot_llm`) that setup-question drafting already uses. Prompt,
   reasoning, and raw JSON never enter the thread.
2. The proposal is **validated against the registered agent set**.
   `CouncilPlan.with_seat_agent` already raises `UnknownAgent` rather than
   falling back to the generalist, exactly so a mistyped name cannot become an
   undisclosed monologue. Keep that; extend it to the whole proposed roster.
3. The validated plan is shown on the existing preflight card and the human can
   reassign seats. Only then is anything dispatched.
4. The plan — proposal, validation result, and any human edit — is recorded with
   the attempt, as `plan_council` already records depth and effective budget.

The generalist stays legal but must be *visible*: a council of three
generalists should say so on the card, before the time is spent.

**This is also where per-seat models belong.** Every worker currently inherits
the composer's model (here, the cheapest configured one). A chair synthesizing a
research design is not the same task as a file survey, and the roster is the
natural place to say so.

### 3.3 Four levels

`CouncilDepth` gains a fourth member below `LIGHT`:

| level | positions | what it is for |
| --- | --- | --- |
| **Human Input** | 0 | No worker is dispatched. The roster and the framing questions are shown; the scientist writes the design themselves and it becomes the review package. |
| **Light** | 1 | One position, one challenge, one synthesis. |
| **Medium** | 2 | Default. Reproduces today's budget exactly. |
| **Heavy** | 4 | Room to read the workspace and argue in detail. |

`DepthPolicy` already carries `max_positions` and a `WorkerBudget`, and
`plan_council` already refuses to let any depth buy skipping the red team or the
chair. Human Input is the one exception and needs an explicit branch, because
"zero seats" must not reuse the not-dispatchable path — that path means
*refused*, and this one means *the human is the council*.

### 3.4 Live debate UI

The event plumbing exists. Each unit already emits `task_started` /
`task_running` / `task_completed` / `task_failed` with its `unit_id` as
`task_id`, persisted as `subagent.*` run events and queryable by `task_id` with
an `after_seq` cursor. Today they render as generic subtask cards.

What is missing is *seat identity* and *round structure* on those events:
`seat_id`, `role` (position / red team / chair), `agent_name`, `model`,
`via_generalist`, `round`. With those, a Debate panel can render seats as live
lanes with status, streamed argument summaries, a round counter, and a
consensus meter.

The consensus meter needs a real signal rather than a heuristic. Extend the
chair's structured output with `agreements`, `disagreements`, and
`open_questions`; consensus is then a count, not a vibe. `nostream` must keep
holding: seat reasoning belongs in the debate panel and the subtask timeline,
never in the parent thread stream.

### 3.5 Consensus review

Keep the Markdown. The approval binds to the document hash the human actually
read, and `design-review.tsx` renders that Markdown deliberately rather than
re-rendering the JSON — that is a governance property, not a UI shortcut.

Add a `consensus.v1` block to the JSON package beside it, and render it as the
structured review sheet: the positions, where they converged, where they did not
and why, the chosen path with its tradeoff, success and rejection criteria,
risks, and any decision still open. The Markdown stays the bound artifact; the
sheet is how a person reads it.

Approve / Request changes / Reject must call the existing
`POST …/stages/{stage}/review` endpoint. Chat text is not a review record and
the supervisor already refuses to treat it as one.

### 3.6 Iterative refinement

"Request changes" should launch a *focused* follow-up round rather than a fresh
council: seeded with the prior consensus, the human's specific objection, and
only the seats that objection touches. `_compact_design_history` already feeds
the last four chair syntheses back in, so the carrier exists. What it needs is
the objection as a durable input and a bounded round counter, so refinement
converges instead of looping.

### 3.7 DBTL binding

An approved consensus is already bound to the stage approval. What is missing is
propagation: Build, Test, and Learn worker prompts get `declared_datasets`,
`reconciliation`, and `build_test` context but not the approved design brief.
Add it as `approved_design_brief` in `stage_context`, pinned by the same
content hash the approval bound, so a later design change invalidates downstream
work the way a dataset change already invalidates a reconciliation approval.

## 4. Order of work

1. ~~Finalization deadline, failure reasons, chair prompt~~ — landed.
2. Merge `feat/design-council`; add `HUMAN_INPUT` to `CouncilDepth`.
3. Dynamic roster proposal + validation + per-seat models.
4. Seat/round metadata on task events; Debate panel.
5. `consensus.v1` in the package; structured review sheet.
6. Focused refinement rounds.
7. `approved_design_brief` propagation into Build/Test/Learn.

Step 3 is what makes the council a council. Everything after it is how a person
watches and judges one.
