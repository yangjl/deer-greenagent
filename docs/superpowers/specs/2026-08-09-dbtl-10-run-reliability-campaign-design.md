# DBTL Ten-Run Reliability Campaign

**Status:** Approved design; execution pending written-spec review

**Date:** 2026-08-09

## Goal

Establish a meaningful product-reliability signal by completing ten small,
qualifying DBTL cycles within one working day while exercising the workflow's
important happy, revision, negative-outcome, and recovery routes.

The campaign measures whether DeerFlow reliably governs work from Design through
Build, Test, and Learn. It does not measure whether specialists improve over
time, whether a large scientific workload scales, or whether ten successes are
sufficient evidence for production readiness.

## Audience and use

The primary users are the DeerFlow product owner and engineering team. They need
an evidence-backed answer to two questions:

1. Can a small governed research cycle repeatedly reach the correct terminal
   state without hidden repair?
2. Do the important review, revision, outcome, and recovery routes behave as
   designed when exercised through the browser product surface?

## Experimental controls

The campaign freezes the following for every qualifying run:

- model names and reasoning settings;
- specialist roster, prompts, skills, and tool policy;
- DBTL policy and stage-spec versions;
- deck-composition behavior;
- fixture schema, file names, and scenario definitions;
- manual-profile configuration; and
- specialist craft memory, which starts empty and remains read-only.

Only product-bug fixes may change between attempts. After a fix, the affected
scenario restarts from the captured clean baseline and must complete without
source-code changes or database repair during the qualifying replay.

The frozen memory control is deliberate. Accumulating specialist lessons would
change later model context and make it impossible to distinguish product fixes
from worker learning. A separate follow-up experiment may replay this matrix
with human-approved craft memory enabled.

## Small plant-breeding fixture

Every run uses one synthetic maize genomic-calibration project:

- `train.csv`: 12 immutable rows;
- `holdout.csv`: 4 immutable rows;
- one marker-score feature and one phenotype target;
- fixed schema, seeds, thresholds, and input hashes;
- Python standard library only; and
- four required Build outputs: runner, model, predictions, and metrics.

The fixture is intentionally too small to support real breeding conclusions.
It is a deterministic routing and governance probe. Scenario variants may
change one declared input condition or one human review decision, but may not
silently change the base schema, worker configuration, or governance policy.

## Alternatives considered

### Ten identical happy-path replays

This gives the cleanest basic repeatability estimate but leaves most routing
behavior untested. It was rejected because reducing product routing bugs is the
campaign's primary goal.

### Balanced ten-run route matrix

This combines two repeatability runs with eight controlled route exercises. It
was selected because it gives useful breadth within the eight-hour and
three-million-token limits.

### Exhaustive transition campaign

Testing every available transition and every combination would require roughly
15 to 20 runs. It was deferred because it exceeds the agreed budget and adds
less immediate value than first stabilizing representative route classes.

## Run matrix

Runs execute sequentially in increasing route complexity:

| Run | Primary route | Expected terminal behavior |
| --- | --- | --- |
| 1 | Straight-through `supported` | Design, Build, Test, and Learn complete without revision |
| 2 | Exact supported replay | Same fixture and route reproduce run 1's product behavior |
| 3 | Design clarification | Chair requests input, resumes from the answer, then completes |
| 4 | Design request changes | A second meeting/deck is created and the revised Design completes |
| 5 | Reconciliation judgement | A human resolves the declared judgement before Build proceeds |
| 6 | Build request changes | Build revises once, then passes Test and completes Learn |
| 7 | Test `not_supported` | The valid negative outcome follows its allowed route to Learn |
| 8 | Test `inconclusive` | Test repeats once, preserves both attempts, then routes to Learn |
| 9 | Test `invalidated` | The cycle returns to Build, repairs the package, retests, and completes |
| 10 | Recovery and idempotency | Hold, refresh, stale action, and duplicate action handling preserve state before completion |

Each scenario must declare before execution:

- its starting baseline and fixture hashes;
- expected stage transitions and visible controls;
- intended human decisions;
- expected Test outcome;
- expected terminal state; and
- route-specific evidence that proves the scenario was exercised.

Outcomes must arise from immutable fixture conditions and recorded evidence.
Database edits, hidden transition APIs, fabricated checkpoint artifacts, and
manual state repair are prohibited.

## First-run feasibility checkpoint

Run 1 is both a qualifying candidate and a feasibility study. It uses the
straight-through supported path and the smallest fixture. After it finishes,
the team reviews:

- total and per-stage tokens;
- total and per-stage elapsed time;
- Build clean-rerun evidence;
- Test's server-computed outcome;
- route fidelity and control durability after refresh;
- operator interaction time;
- product and worker defects; and
- whether the remaining nine runs fit inside the campaign reserve.

The plan may be amended after this checkpoint, but amendments must be explicit
and versioned. They may tighten budgets, simplify fixture content, or reorder
later scenarios. They may not retroactively redefine a failed run as successful,
remove governance requirements, hide failures from the denominator, or enable
accumulating craft memory.

If run 1 requires a product fix, its first attempt is recorded as failed. The
fixed replay must start from the clean baseline and complete without intervention
before it becomes qualifying success 1.

## Budget

### Per-run target

| Component | Target tokens |
| --- | ---: |
| Supervisor and routing | 10,000 |
| Design meeting | 25,000 |
| Build | 80,000 |
| Test | 80,000 |
| Learn | 35,000 |
| Deck and review overhead | 10,000 |
| **Typical run** | **240,000** |

Simple runs should finish in about 25 minutes. A single attempt has a hard cap
of 45 minutes or 350,000 model tokens. Waiting for an operator at a visible
human gate is recorded separately and does not count as model runtime.

### Campaign ceiling

- Ten qualifying runs: 2.4 million tokens.
- Failed-attempt and replay reserve: 400,000 tokens.
- Diagnosis and review reserve: 200,000 tokens.
- Hard campaign ceiling: 3.0 million tokens and eight elapsed working hours.

The working-day allocation is approximately five hours for qualifying runs, two
hours for diagnosis and clean replays, and one hour for setup and final analysis.

## Execution protocol

Runs are sequential. Parallel execution is prohibited during this campaign
because shared database, workspace, browser, and model-provider state would make
failure attribution less reliable.

For each attempt:

1. Restore the named clean baseline and verify its database and project hashes.
2. Verify the frozen model, worker, skill, policy, deck, and memory configuration.
3. Start one new cycle through the browser product surface.
4. Make only the scenario's declared human decisions.
5. Refresh at the scenario's specified checkpoints and verify durable controls.
6. Let the server compute outcomes and transitions; do not use hidden APIs.
7. Capture the terminal or stopped state, logs, hashes, artifacts, and run ledger.
8. Classify any failure before deciding whether to fix and replay.

The browser-based DBTL pilot workflow is the authoritative interaction path.
Read-only database queries and filesystem inspection may verify evidence after a
state is recorded, but they may not create or repair state.

## Success definition

A run is a product success when all of the following hold:

- it starts from the captured clean baseline;
- all work and human decisions use visible product controls;
- no source patch or database repair occurs during the run;
- each control is visible, correctly scoped, single-use, and durable on refresh;
- Build and Test evidence is hash-bound and independently rerunnable;
- the actual route matches the scenario's declared route;
- governed work never falls into ordinary chat;
- Learn records only policy-permitted candidates; and
- the cycle reaches its expected terminal state.

A scientifically `not_supported`, `inconclusive`, or `invalidated` result is a
product success when classification and routing are correct. Conversely, a
numerically correct result is a product failure if it bypasses governance,
cannot be reproduced, loses evidence, or takes the wrong route.

## Failure taxonomy

### Product bug

Incorrect routing, lost state, invisible controls, persistence defects, stale or
duplicate action errors, incorrect artifact binding, or orchestration rejecting
a valid package. Pause the campaign, diagnose the cause, add regression coverage,
fix it, restore the clean baseline, and replay the scenario.

### Worker or package defect

Malformed code, incorrect rerun order, missing outputs, or invalid structured
evidence. The product succeeds only if its gates detect and route the defect
correctly. A worker defect is not automatically a product bug.

### Scientific outcome

A server-computed supported, not-supported, inconclusive, or invalidated result
based on valid evidence. It is not a product failure when policy behavior is
correct.

### Protocol or operator error

An unintended human decision, wrong model/configuration, or accidental browser
action. Mark the attempt invalid and restart it. Do not report it as a product
bug or omit it from the attempt ledger.

### Expected human gate

Waiting for a visible human decision is normal. It is neither a product failure
nor model runtime while the operator is inactive.

## Run ledger and metrics

Every attempt records:

- scenario, attempt, project, thread, cycle, stage-run, and worker-run IDs;
- baseline, database, project, input, output, and evidence hashes;
- model, reasoning, worker, skill, prompt, stage-spec, and policy versions;
- tokens and elapsed time by stage and for the complete attempt;
- expected and actual transitions;
- Build clean-rerun result and exact command;
- server-computed Test outcome and failed checks;
- refresh, hold, retry, stale-action, and duplicate-action results;
- human decisions and any undeclared manual intervention;
- failure classification and linked regression test or fix when applicable;
- terminal state; and
- craft-memory writes, which must remain zero.

Campaign reporting includes:

- qualifying terminal runs divided by all attempts;
- route fidelity;
- eligible first-pass Build-to-Test success rate;
- recovery-route success rate;
- product bugs per attempt and per route;
- manual interventions per attempt;
- tokens and elapsed time by stage and route; and
- final-five consecutive product-bug-free status.

## Stop and escalation rules

Stop one attempt when it reaches 45 minutes or 350,000 model tokens.

Pause the campaign when:

- the same product failure occurs twice;
- a transition is unauthorized;
- evidence is lost or a required control is invisible;
- database integrity or artifact provenance is uncertain;
- specialist craft memory changes unexpectedly;
- frozen configuration drifts; or
- total use reaches 2.7 million tokens before eight qualifying successes.

Do not weaken evidence, validation, or governance rules to make a scenario pass.

## Campaign acceptance criteria

The campaign is successful only when:

1. Ten qualifying cycles reach their declared terminal states.
2. The final five consecutive attempts contain no product bug.
3. Every qualifying run follows its declared route.
4. Refresh, hold, retry, stale-action, and duplicate-action controls preserve
   state in their assigned scenarios.
5. No qualifying run uses database repair, hidden transitions, or unauthorized
   knowledge promotion/publication.
6. Build and Test evidence remains content-bound and reproducible as required by
   each scenario.
7. Eligible happy-path Builds achieve at least 80% first-pass Test success.
8. Craft-memory writes remain zero.
9. Total use remains within eight working hours and three million model tokens.
10. Failed and invalid attempts remain in the denominator and failure ledger.

## Non-goals

- Accumulating or evaluating specialist memory.
- Changing worker prompts, skills, or models during the campaign.
- Using real breeding data or large simulations.
- Exhausting every combinatorial route sequence.
- Establishing production scale, production readiness, or scientific power.
- Promoting or publishing Learn candidates.
- Redesigning the product beyond fixes required by observed failures.

## Recommended next step

After written-spec review, create an executable run plan for scenario 1, capture
its clean baseline, and run the straight-through supported cycle as the
feasibility checkpoint. Review its budget, route, evidence, and failures before
locking the detailed procedures for scenarios 2 through 10.
