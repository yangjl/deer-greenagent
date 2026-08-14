# DBTL Ten-Run Reliability Campaign

**Status:** Runs 1–4, 6, and 7 complete on the repaired current tip; Run 5 is
non-qualifying; Run 8 is paused at Build recovery after its first intended
`invalidated` Test outcome; Runs 9–10 have not started

**Date:** 2026-08-09

## Current campaign checkpoint — 2026-08-14

- Run 6 completed through the valid `not_supported` route to Learn with no
  promotion or publication.
- Run 7 preserved two `inconclusive` Test attempts and closed fail-closed when
  the immutable fixture could not establish authoritative row identity.
- Run 8 reached the declared first `invalidated` Test outcome, returned to
  Build through the visible control, and stopped at the durable Build-recovery
  checkpoint with 4,119 of its 500,000-token budget remaining. Its continuation
  requires a fresh budget and remains deliberately paused.
- The current-tip repair, proof, and review checkpoint is being consolidated
  separately; it does not authorize or execute the Run 8 continuation.

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
| 5 | Build request changes | Build revises once, then passes Test and completes Learn |
| 6 | Test `not_supported` | The valid negative outcome follows its allowed route to Learn |
| 7 | Test `inconclusive` | Test repeats once, preserves both attempts, and closes if the required evidence is still missing |
| 8 | Test `invalidated` | The cycle returns to Build, repairs the package, retests, and completes |
| 9 | Recovery and idempotency | Hold, refresh, stale action, and duplicate action handling preserve state before completion |
| 10 | Final supported replay | Replays the repaired straight-through route without a product bug |

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

Simple runs should finish in about 25 minutes. Runs 3–5 have an owner-amended
hard cap of 45 minutes or 500,000 model tokens per attempt; this supersedes the
earlier 350,000-token attempt ceiling for those runs only. Waiting for an operator at a visible
human gate is recorded separately and does not count as model runtime.

### Campaign ceiling

- Checkpoint forecast for ten qualifying governed runs: 3.8 million tokens,
  including the completed Run 1 ordinary control.
- Additional failed-attempt, replay, diagnosis, and review reserve: 1.2 million
  tokens.
- Hard campaign ceiling: 5.0 million tokens and eight elapsed working hours.
- Run 1 is the only ordinary-chat control. Runs 2 through 10 use governed DBTL
  only.

### 2026-08-11 owner amendment — Run 6 replay and Run 7

The owner has authorized the clean Run 6 replay and Run 7 with **no model-token
ceiling**. This supersedes both the default later-run per-attempt ceiling and
the aggregate campaign token ceiling for these two runs only, including their
required canary, diagnosis, and governed replay work. The previously discussed
additional 500,000-token allowance is therefore not a stop condition for these
two runs.

Token use and elapsed time must still be measured and reported. Every other
campaign control remains in force: sequential execution, visible authenticated
deck decisions, server-owned handoffs, immutable inputs, hash-bound evidence,
read-only diagnostics, zero hidden transitions or database repair, frozen craft
memory, product-bug stop/repair/replay rules, and a clean Run 6 result before
Run 7 starts.

The completed current-tip Run 7 replay refined the expected terminal route.
The frozen train and holdout CSVs have no authoritative row/entity identifier,
so the repeat cannot independently prove unit-level fold disjointness. Both
attempts therefore correctly remain `inconclusive`; after exactly one repeat,
the human closes the cycle and Learn stays locked. This is a successful
fail-closed outcome, not permission to infer identities from filenames,
feature values, positional labels, or content hashes.

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

Stop Runs 3–5 when an attempt reaches 45 minutes or 500,000 model tokens. The
original 350,000-token stop remains the default for later runs unless the owner
amends it.

Pause the campaign when:

- the same product failure occurs twice;
- a transition is unauthorized;
- evidence is lost or a required control is invisible;
- database integrity or artifact provenance is uncertain;
- specialist craft memory changes unexpectedly;
- frozen configuration drifts; or
- total use reaches 4.5 million tokens before eight qualifying successes.

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
9. Total use remains within eight working hours and five million model tokens.
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

Complete the owner-authorized Runs 3–5 sequentially, preserving the Run 1
fixture, frozen models, stage specs, specialist configuration, and empty
craft-memory control. Do not create another ordinary-chat comparison, and stop
before preparing or starting revised Run 6. The former reconciliation-judgement
scenario is removed: reconciliation is not a Build gate, and the campaign must
not revive it as one.

## Feasibility checkpoint amendment — Run 1

**Recorded:** 2026-08-09

Run 1 is qualifying product success 1 of 10. The governed cycle completed the
declared straight-through `supported` route with Design, Build, Test, and Learn
approved. All human actions used visible browser controls; the refreshed page
and database both showed the completed cycle. No source patch, hidden transition,
database repair, memory write, promotion, or publication occurred.

The governed run used 308,813 model tokens and 16 minutes 16 seconds wall time,
including about 8 minutes 8 seconds of recorded run execution and about 8
minutes 8 seconds of operator review. The ordinary control used 144,949 tokens
and 1 minute 58 seconds. Governed usage was:

| Component | Tokens | Share |
| --- | ---: | ---: |
| Supervisor and routing | 9,429 | 3.1% |
| Design | 22,594 | 7.3% |
| Build planning and execution | 100,487 | 32.5% |
| Test | 120,954 | 39.2% |
| Learn | 55,349 | 17.9% |
| **Governed total** | **308,813** | **100%** |

Test dominated token use. It independently recomputed the result, verified the
server-owned clean rerun, and recorded `supported` with recommendation
`advance_to_learn`. The Build outputs reproduced byte-for-byte and the immutable
input hashes did not change.

One worker/package defect remains in the denominator. The Design chair added a
required replay notebook that was not requested by the scenario. During Build
planning, the operator used **Change the plan** to remove it; the Build worker
then marked that Design-approved deliverable `not_applicable`. Test correctly
kept the approved Design manifest authoritative, failed the notebook deliverable
audit, and treated it as a non-gating limitation because it is not one of the
pinned validity pack's seven scientific checks. This is not classified as a
product bug: the governance boundary, evidence, outcome computation, route, and
terminal state behaved as designed. It is a worker/package-quality defect and
an avoidable operator detour.

The following amendments apply from Run 2 onward:

1. A Build-plan revision may not be used to remove a Design-approved deliverable.
   Either accept and build the manifest or revise the Design before approval.
2. For Run 2, accept the one-phase Build plan when it covers the approved
   manifest; do not use **Change the plan** merely to remove an added small
   artifact. This avoids the two extra planning calls that cost 15,579 tokens
   in Run 1.
3. Keep the 350,000-token and 45-minute per-attempt hard stops. For scenarios
   that intentionally repeat Test or return to Build, ledger each bounded stage
   continuation as a new attempt while preserving the single durable cycle.
4. Replace the 240,000-token typical-run forecast with 295,000 tokens for a
   straight-through run. Use 420,000 for a one-repeat scenario and 520,000 for
   a Build-repair-plus-retest scenario.
5. The owner approved a five-million-token hard ceiling. The 3.8-million-token
   checkpoint forecast is the operating forecast; the remaining 1.2 million is
   reserved for failed attempts, recovery-route continuations, diagnosis, and
   clean replay. The eight-hour wall ceiling remains unchanged.
6. Run 1's ordinary control remains historical evidence but is not repeated.
   Runs 2 through 10 contain only the governed cycle, its visible human gates,
   and read-only verification. Run 2 is authorized under this amendment.

## Feasibility checkpoint amendment — Run 2

**Recorded:** 2026-08-09

Run 2 is qualifying terminal-route success 2 of 10. It exactly replayed the
straight-through route: Design, one-phase Build, server-computed Test
`supported`, Learn, then completed. All state transitions and human decisions
used visible product controls. Refresh preserved the completed cycle and all
four approved stage decisions. Build reproduced byte-for-byte, Test passed all
seven required validity checks, and Learn created only eleven provisional
synthetic-software candidates. There were no hidden writes, database repairs,
craft-memory writes, promotions, publications, or ordinary-chat control.

Run 2 used 325,166 model tokens and 13 minutes 23 seconds wall time, including
about 8 minutes 59 seconds of recorded execution. Usage was:

| Component | Tokens | Share |
| --- | ---: | ---: |
| Supervisor and routing | 9,400 | 2.9% |
| Design | 22,470 | 6.9% |
| Build planning and execution | 86,850 | 26.7% |
| Test | 150,236 | 46.2% |
| Learn | 56,210 | 17.3% |
| **Governed total** | **325,166** | **100%** |

The Run 1 worker/package defect did not recur. Build produced every approved
deliverable, including the validator and replay notebook, and Test audited all
seven artifacts. Avoiding the plan-removal detour reduced Build usage by 13,637
tokens. Test increased by 29,282 tokens because it independently reviewed the
larger complete package; Test remains the largest and most variable cost.

One non-routing product defect was observed and fixed after the terminal cycle.
When the operator changed the Design preflight from the recommended four-worker
roster to **Light debate**, the structured editor correctly showed three
participants but the immutable fallback Markdown still said `4 workers`. The
backend dispatched the selected three participants, and route/evidence state
was unaffected. The UI now hides the stale fallback after a depth change, with
a DOM regression test. Run 2 therefore counts toward the ten terminal-route
successes but resets the product-bug-free streak; the required final five
bug-free attempts have not started.

The following amendments apply after Run 2:

1. Raise the straight-through operating forecast from 295,000 to 320,000 model
   tokens. Keep the 350,000 per-attempt hard stop; Run 2 left only 24,834 tokens
   of headroom.
2. Keep Test as a separate specialist stage and preserve its independent rerun
   and validity workers. Do not reduce evidence to save tokens.
3. Preserve the complete Design manifest in Build planning. The full package
   eliminated Run 1's deliverable defect and improved downstream audit quality.
4. The campaign has used 633,979 governed tokens. Including Run 1's historical
   ordinary control, total experiment use is 778,928 tokens, leaving 4,221,072
   under the five-million hard ceiling.
5. Run 3 is not authorized. Do not prepare, start, or infer authorization for it
   from this checkpoint; wait for an explicit owner decision.
