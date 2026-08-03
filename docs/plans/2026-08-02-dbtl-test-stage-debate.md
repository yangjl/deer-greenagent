# What is the Test stage for? — a debate

**Status:** Debate document. **No decision is reached here and no implementation
is authorized.** It exists to make the disagreement explicit and to record which
arguments are settled by the code rather than by preference.

**Date:** 2026-08-02

**Scope:** The Test stage only. Build's shape is discussed in
[2026-08-01-dbtl-build-observable-retryable-workflow-plan.md](2026-08-01-dbtl-build-observable-retryable-workflow-plan.md)
and is treated here as a given.

---

## 1. The workflow this has to serve

This is not a manufacturing pipeline or a confirmatory clinical trial. It is
**computational, statistical, and bioinformatics workflow development**, and the
owner's stated cycle logic is:

- **Design** — mostly pilot and exploratory. It is *expected* to change in a
  later cycle. A Design is a starting position, not a commitment.
- **Build** — implements the Design's decision: the hypothesis, the exploration.
  It must present results as **figures first**, then text and other scientific
  evidence. This is where the human reads the work and leaves comments and
  suggestions.
- **Test** — needed only to decide whether the results are **worth keeping**.
- **Learn** — synthesizes the original Design, the human's comments, and the
  Build results into the cycle's outcome. If more is needed, open a new cycle.

Three properties of that workflow matter for everything below. Most Builds are
**exploratory, not confirmatory**. Most deliverables are **figures, not
metrics**. And the **human's reading of the work happens in Build**, before Test
sees anything.

The current Test stage was not designed for any of those three.

---

## 2. What Test actually does today

Stated plainly, because several of these were surprises and two of them
invalidate arguments people (including this document's first draft) have made.

**It computes an outcome from a fixed check pack.** A Test worker returns
headline metrics and a set of named validity checks; the server computes
`supported` / `not_supported` / `inconclusive` / `invalidated`. A stage approval
cannot override that computation — deliberately.

**The pack is `generic-predictive:v2`, and it is effectively hardcoded.**
`evaluate_validity` accepts a `validity_pack` parameter, and **no caller ever
passes one** (`validity.py:250`; call sites `test_review.py:65` and
`build_test_ops.py:371`). `GENERIC_PREDICTIVE_V1_PACK` has no reader anywhere and
is unreachable at runtime. `StageSpec.validity_gates` names a pack as a *string*
(`"generic-predictive:v2"`), but nothing resolves that string to a pack — its
only functional reader in the codebase is an unrelated Build prompt branch
(`stage_runner.py:271`). Bumping Test to v3 changed the declared string; the
runtime pack did not move. Meanwhile `validity_pack_key` **is** persisted per
assessment (`model.py:485`), so the schema anticipates a variation the code has
never produced.

**Its seven required checks are:** `fold_composition`, `predictive_ceiling`,
`direction`, `leakage`, `tester_holdout`, `reproducibility`, `reconciled_inputs`.

**`not_applicable` is treated as `missing`** (`validity.py:266`) and therefore
forces `INCONCLUSIVE`. A check marked N/A requires no detail, so a worker can
silently N/A its way to an unusable outcome with no justification recorded, and
the audit trail cannot distinguish "didn't apply" from "wasn't done."

**`inconclusive` and `invalidated` cannot advance to Learn** (`validity.py:198`,
re-checked against the stage graph in `build_test_ops.py:568`). `supported` and
`not_supported` both can.

**Learn is bolted to Test at the repository layer.** `knowledge_ops.py:239`
refuses Learn synthesis unless a human-owned validity assessment exists, and
`candidate_eligibility` refuses claims from inconclusive or invalidated cycles.
Test cannot simply be deleted; something must occupy that slot or Learn stops.

**Test workers are fully capable.** `_tools_for_unit` filters by *role*, not
stage — only Build's `summarizer` and `planner` are read-only
(`adapter.py:256`, `337`). Every Test unit takes the default role and receives
the full tool list plus a writable workspace (`adapter.py:3577`). Test's own
budget comment says it must "inspect Build outputs and execute validity checks."
**Test can run code.** It is the only post-Build stage that can.

**`CycleWeight.LIGHT` and `CycleClass.COMPUTATIONAL` exist and do nothing.**
Every spec declares `_ALL_CLASSES` / `_ALL_WEIGHTS`, and `specs_for_cycle` is
called from exactly one place — a test (`test_dbtl_stage_contracts.py:128`). The
vocabulary for "this is a lighter cycle" is already in the schema and the router,
wired to nothing.

---

## 3. The motion

> **Test should stop being a validity gate and become a decision about whether
> the results are worth keeping.**

---

## 4. Position A — for the motion (*the Curator*)

**A1. The pack is a genomic-prediction-shaped hole, and it structurally cannot
pass a figure-first Build.** Take the most ordinary deliverable in this
workflow: a population-structure PCA, a QC distribution, a trait correlation
plot. It has no folds, no holdout, no predictive ceiling, and no effect
direction. Four of the seven required checks can only be `not_applicable` — and
N/A is scored as missing, which forces `INCONCLUSIVE`, which **cannot reach
Learn**. This is not a calibration problem to be tuned. In the workflow this
system is being built for, the default outcome for the default deliverable is
structural failure.

**A2. The judgement already happened, one stage earlier.** Under the figure-first
model the human reads the figures in Build, comments on them, and approves the
Build gate. Test then convenes workers to re-examine the same evidence and asks
the human to decide again, in vocabulary that does not fit what they are looking
at. That is not a second opinion; it is the same opinion, taxed.

**A3. Validity and worth are orthogonal, and only one axis exists.** The current
outcome vocabulary answers *is the claim true?* It has no way to say "true and
uninteresting," or — far more valuable in exploratory work — "invalidated, and
worth keeping precisely as a record of the trap." An invalidated cycle currently
cannot create a claim at all, so the most instructive results this workflow
produces are the ones it is least able to record.

**A4. A gate calibrated for confirmation, applied to a pilot, fails every
pilot.** The Design is explicitly exploratory and expected to change next cycle.
Asking a pilot to satisfy a confirmatory validity contract converts every honest
exploration into a failed confirmation.

**A5. The retreat has already started; it is simply incomplete.** `v1` required
all ten checks and a legitimate approved family holdout failed for lacking a
pedigree. `v2` dropped three checks and shipped marked `provisional=True`. The
pack has been backing away from over-gating since it existed. Finish the retreat.

---

## 5. Position B — against the motion (*the Skeptic*)

**B1. Leakage is invisible in a figure. That is the whole reason a check
exists.** A person looking at an excellent plot cannot see that the test set
leaked into training, that folds were composed by a column correlated with the
outcome, or that the holdout was drawn after filtering. Ask "is this worth
keeping?" about a leaked result and the answer is *yes, obviously* — the figure
looks great. This is the single most famous failure mode in computational
biology, and it is invisible to precisely the method the motion proposes to
rely on.

**B2. The Build commenter is not a neutral party.** They have been living with
this work and want it to be right. Every other authority boundary in this
system separates the actor from the judge: the council chair does not argue a
position, an agent may propose a Reconciliation resolution but may never close a
judgement row, the Test outcome is computed rather than chosen. Making the
person who approved the Build also the person who decides it is keepable
collapses the one separation that has held everywhere else.

**B3. Learn publishes, and published claims are citable.** Learn candidates
become knowledge claims with retrieval pointers and cross-project publication.
A knowledge base populated by things somebody liked the look of is *worse* than
no knowledge base, because a future cycle will cite it. The gate is not
protecting this cycle; it is protecting every later one.

**B4. Figure-first raises the reproducibility bar, it does not lower it.** A
figure is a claim you cannot audit by reading it. If the deliverable is a
picture, the floor is that the picture regenerates from hash-bound inputs — and
that single check subsumes an enormous amount, because it proves the script
exists, the inputs are bound, the environment works, and the image was not made
by hand. **Test is the only post-Build stage with the tools to actually do
this**, and today nobody asks it to.

**B5. The motion re-opens a hole the design deliberately closed.** "A generic
stage approval cannot bypass that computation" is a load-bearing sentence. It
exists because approval is exactly the pressure point where a tired reviewer
waves work through. Replacing the computation with a human choice restores the
bypass under a new name.

---

## 6. Cross-examination

**Where A must concede.** B4 lands. Figure-first genuinely does make
"does it regenerate?" the minimum bar, and A has no answer to B1 — no amount of
human commenting detects leakage. A's position, taken literally, would let a
leaked result through every time. A must accept that *some* mechanical check
survives.

**Where B must concede.** A1 lands, and it is not a matter of degree. B's
implicit premise is that the current pack is a considered instrument being
misapplied. It is not: the pack is hardcoded, the selection parameter is never
passed, the alternative pack is unreachable, and the stage-spec declaration
resolves to nothing. B is defending a calibration that was never performed. And
the structural consequence — a descriptive figure cannot reach Learn — is a
defect by any reading.

**Where both are wrong.** Both sides argue about *how much* Test. The evidence
suggests the question is wrong.

---

## 7. What the code settles

Three findings decide arguments that would otherwise be preference.

**The pack was always meant to vary, and the wiring is missing, not absent.**
`evaluate_validity` takes a pack. `validity_pack_key` is persisted per
assessment. `validity_gates` is declared per stage spec. Three layers anticipate
selection and none of them are connected. Making the pack selectable is
plumbing, not architecture — which removes cost as an argument for keeping one
pack.

**The system already half-agrees with the motion.** `supported` and
`not_supported` both advance to Learn. A negative result is already treated as
worth keeping. What is missing is not the *idea* that worth is separable from
truth, but the ability to say it about an inconclusive or invalidated cycle.

**A "lighter cycle" vocabulary already exists, unused.** `CycleWeight.LIGHT`
and `RETROACTIVE` are accepted by the router, stored on the cycle, and read by
nothing. If proportionality is the answer, the hook is already there — and its
existence suggests someone once intended exactly this and never returned.

---

## 8. Where this leaves the argument

The debate's real content is not *how much Test* but **Test of what claim?**

Today one check pack is pinned to the *stage*. The evidence above says it should
be pinned to the **claim the Build made**. A predictive claim inherits the
predictive pack, unchanged, with every one of B1's protections intact. A
descriptive or figure claim inherits a much smaller pack — does the figure
regenerate from bound inputs, do those inputs match the recorded lineage, does
the caption assert only what the data shows. An exploratory pilot inherits the
smallest pack that is still worth having, which is probably reproducibility
alone.

Under that framing, A1 dissolves (a figure is no longer graded on folds it never
had), B1 survives intact (a predictive claim still faces leakage), and B4 gets
what it asked for: a reproducibility check that Test is actually equipped to
run.

Then the motion's genuine contribution — **worth** — becomes a *second*
question rather than a replacement for the first. Test asks, in order: is the
evidence sound for the kind of claim it makes, and is this worth keeping? The
first is computed. The second is the human's, and it is the axis the system
currently cannot express.

---

## 9. Open questions this debate did not settle

**Who selects the pack?** Design is early enough to be principled and too early
to know what Build will produce. Build knows, and is self-serving. The human at
Test time is accurate and is one more click on a workflow already accused of too
many. All three have a failure mode; none is obviously worst.

**Is the human's Build comment evidence, or a conclusion?** If the scientific
reading of a figure is recorded in Build, Test can either treat it as an input
to check or as a judgement already made. The first keeps the separation B2 wants;
the second is what makes the workflow feel fast. They are not compatible.

**Does "worth keeping" gate Learn, or is it Learn's own first question?** The
motion places it in Test. But Learn already synthesizes Design + comments +
results; deciding worth may be the opening move of that synthesis rather than a
prerequisite for it. Against that: the architecture's most consistent refusal is
letting one run both judge and synthesize.

**Does the reproducibility check re-execute?** Test has bash and a writable
workspace, so it *can* actually regenerate a figure and compare — definitive,
and expensive. Verifying in principle (script exists, inputs bound, environment
recorded) is cheap and weaker. Figure-first argues for the expensive one, and
the Build refactor's whole premise is that expensive work should stop happening
where it keeps getting discarded.

**What replaces the assessment record if Test collapses?** `knowledge_ops.py:239`
refuses Learn without a human-owned validity assessment. Any change to Test must
say what writes that row, or Learn stops for every cycle.

---

## 10. Notes for the next session

- Nothing here is decided. The pack-per-claim framing in §8 is this document's
  argument, not an agreed direction.
- If the pack-per-claim direction is pursued, §7's finding matters most: the
  selection machinery exists at three layers and is unconnected. Verify that
  independently before scoping anything.
- §2's fact list should be re-verified against the code before it is relied on;
  it was assembled on 2026-08-02 against branch `feat/build-refactor`.
