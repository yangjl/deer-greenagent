# Which validity pack, and who chooses it? — a debate

**Status:** Debate document. **No decision is reached here and no implementation
is authorized.** It exists to make the disagreement explicit and to record which
arguments are settled by the code rather than by preference.

**Date:** 2026-08-09

**Predecessor:** [2026-08-02-dbtl-test-stage-debate.md](2026-08-02-dbtl-test-stage-debate.md).
That document argued (§8) that the check pack should be pinned to *the claim the
Build made* rather than to the stage, and left **"Who selects the pack?"** as the
first of its unsettled questions (§9). This document takes up exactly that
question and nothing else. The prior debate's motion — whether Test should stop
being a validity gate at all — is **not** re-litigated here; this document
assumes a computed gate survives in some form.

**Scope:** Validity-pack selection and the applicability of checks. The Build
stage, the human gate's mechanics, and the Learn hand-off are treated as given.

**Amendment:** §12 adds **Position C**, which argues the motion is the wrong
question — that the gate a discovery workflow needs is already built and simply
not wired to the verdict. It was added after §§1–11 were written and is not
answered by the cross-examination in §6. Read it before acting on §9.

---

## 1. What has changed since 2026-08-02

The predecessor's §10 asked that its fact list be re-verified before being
relied on. It was, on 2026-08-09 against branch `fix/build-bugs`. Four items
moved, and two of them change the argument.

**`validity_gates` is no longer inert.** The predecessor recorded that the field
"names a pack as a *string* but nothing resolves that string," with a single
unrelated reader. There are now **eighteen** functional readers across
`build_phases.py`, `adapter.py`, `stage_runner.py`, `build_test_ops.py` and
`test_review.py` — every Build contract gate, and the Test rerun gate. The
decisive one for this debate is `test_review.py:243`:

```python
rerun_required = "server_verified_build_rerun" in resolve_spec_by_key(stage_spec_key).validity_gates
```

A gate string is resolved *from the spec pinned on the attempt* and changes
server behaviour. That is precisely the mechanism a selectable pack needs, now
demonstrated in production code. It was hypothetical seven days ago.

**The reproducibility check now re-executes.** The predecessor's §9 asked
whether it would verify "in principle" or actually regenerate. `TEST_SPEC_V4`
answered: the server runs the recorded command in a fresh workspace and requires
exit 0 **and** every expected output matching the approved Build hash
byte-for-byte (`test_rerun.py:519-527`). That question is closed.

**Learn gained a second authority.** `knowledge_ops.py:286-298` now admits a
cycle whose Test stage is `SKIPPED` — "a human-owned decision that this Build was
not worth qualifying" — but such a cycle **can produce no candidate**, enforced
by producing nothing promotable rather than by a downstream rule. This matters
below: the system already knows how to say *this ran on a weaker authority, so it
cannot create a claim.*

**`specs_for_cycle` is gone.** The predecessor's third code finding — that a
"lighter cycle" vocabulary existed unused — no longer holds as written. The
function has no definition and `CycleWeight` has no readers. Treat that finding
as stale.

---

## 2. What the code does today

**One pack, reached by import, not by resolution.** `DEFAULT_VALIDITY_PACK`
(`validity.py:88-104`) is referenced directly at five call sites in three
modules. `evaluate_validity` accepts a `validity_pack` parameter and **no caller
passes one**. `GENERIC_PREDICTIVE_V1_PACK` still has zero readers.

**Its seven required checks** are `fold_composition`, `predictive_ceiling`,
`direction`, `leakage`, `tester_holdout`, `reproducibility`, `reconciled_inputs`.

**Two of the seven are not the worker's to write.** `reproducibility` is
overwritten from the server rerun record and `reconciled_inputs` from the Build
input lineage (`test_review.py:79-92`), whatever the assessment worker said.
Those two are also the only two that are **domain-independent**: "does this
regenerate" and "are the inputs hash-bound" are meaningful for any computational
cycle. The other five are predictive-holdout concepts and are worker-authored.
The pack is therefore already two packs wearing one name.

**`not_applicable` is scored as `missing`** (`validity.py:266`) and forces
`INCONCLUSIVE`, which cannot advance to Learn. And N/A is **free**: only
`passed`/`failed` require a `detail`, and only `passed` requires evidence
(`validity.py:160-163`). A worker can mark a check N/A with no justification
recorded anywhere, and the audit trail cannot distinguish "did not apply" from
"was not done."

**The check vocabulary is a closed enum.** `ValidityCheckName`
(`validity.py:21-31`) has exactly ten members, and `ValidityCheck.__post_init__`
raises `ValidityRefused` on any name outside it. **A new pack cannot introduce a
new check.** A wet-lab protocol pack cannot require `positive_control_present`;
a simulation pack cannot require `convergence_diagnostics`; a reconciliation
pack cannot require `unit_harmonization`. Every one of the ten existing names is
a predictive-modelling or provenance concept. This is a harder ceiling than the
hardcoded pack constant and it is not mentioned in the predecessor.

**The schema already expects variation.** `validity_pack_key` is a per-assessment
column since migration `0016` (`model.py:585`), written on every assessment
(`build_test_ops.py:519`) and surfaced to the review meeting
(`supervisor.py:1187`). It has only ever held one value.

**The concrete failure.** A descriptive or non-predictive cycle — a QC
distribution, a protocol optimization, a simulation study, a reconciliation —
has no folds, no holdout, no predictive ceiling and no effect direction. An
honest assessor marks four checks `not_applicable`; the outcome is
`inconclusive`; the route menu contains no `advance`; Learn is unreachable. The
only path through the gate is to mark inapplicable checks `passed`, which
requires inventing a detail and an evidence reference. **The system's incentive
today is to lie.**

---

## 3. The motion

> **The validity pack must be selected and frozen by a human at the Design gate,
> before Build runs.**

---

## 4. Position A — for the motion (*the Constitutionalist*)

**A1. The guarantee is temporal, not technical.** What makes a computed gate
worth anything is not that a machine computed it — it is that *the bar was fixed
before the result existed*. Every other pinning in this architecture works that
way: the stage spec is pinned on the attempt before dispatch, the Design brief is
hash-bound before Build reads it, the Build contract version is frozen before a
phase runs. A pack selected after the work exists is not a gate; it is a grade
chosen once the answer is known.

**A2. Design is the only place a human is already reading the plan.** The Design
gate exists, a person already approves it, and the approval is already
hash-bound. Selecting a pack there costs one field on an existing decision.
Every other candidate location invents a new human interaction on a workflow
already accused of having too many.

**A3. It converts a deadlock into a design statement.** Today "this check does
not apply" is an assertion the worker makes after seeing the result, and it is
scored as a failure to do the work. Under the motion, inapplicability is
expressed by the pack simply not requiring the check — decided in advance, by a
person, on the record. `not_applicable` at assessment time can then go back to
meaning what it should mean: *the worker did not establish this*, which is
correctly inconclusive.

**A4. Design already commits to far more specific things than a pack.** The
approved Design names deliverables, expected paths, acceptance criteria and
success criteria — the Test stage's deliverable audit checks the Build against
that manifest verbatim. A workflow that can commit in advance to "this run will
produce `figures/pca.png` and it will show population structure" can commit to
"this is a descriptive cycle, judged on reproducibility and lineage."

**A5. The alternative hands the actor its own judge.** If the pack is derived
from what Build produced, then a Build that quietly becomes something easier to
validate has selected an easier bar for itself. The architecture refuses this
everywhere else — the council chair does not argue a position, an agent may not
close a judgement row, the Test outcome is computed rather than chosen.

---

## 5. Position B — against the motion (*the Empiricist*)

**B1. Design does not know what Build will do, and this workflow says so out
loud.** The predecessor's §1 records the owner's own cycle logic: Design is
"mostly pilot and exploratory," *expected* to change in a later cycle, "a
starting position, not a commitment." Requiring that starting position to
correctly predict the evidentiary shape of work that has not happened is
requiring exactly the certainty the stage is defined as lacking.

**B2. A pack chosen too early is chosen wrong in one of two directions, and both
are bad.** Guess too strict and an honest Build that legitimately changed course
is graded on checks its final method never had — the v1 pedigree failure that
forced v2, repeated structurally. Guess too loose and the cycle carries a
weak bar it did not deserve, permanently, with a human's signature on it.

**B3. The escape hatch reopens the hole the motion claims to close.** A
Design-time pack that turns out wrong must be amendable, or cycles die on a
clerical error. But "amend the pack after seeing the result" *is* the
after-the-fact selection A1 objects to, now with extra ceremony. A guarantee
with a documented override is the override.

**B4. The claim type is a fact about the Build, and facts should be read, not
predicted.** A Build's result already declares its shape in machine-readable
form: headline metrics with thresholds and plausible ceilings, a rerun spec,
declared outputs, a deliverable fulfilment record. Whether a holdout exists is
*observable*. Deriving the pack from that record is not the actor choosing its
judge — it is the server classifying evidence it already verified, by rules
pinned in advance. A5 conflates "derived from the Build" with "chosen by the
Build worker."

**B5. Selection is the wrong lever entirely while the vocabulary is closed.**
Ten check names, all predictive or provenance. Whoever selects, and whenever,
they are selecting a subset of a list that cannot express a wet-lab control, a
convergence diagnostic or a unit harmonization. The motion optimizes the
selection ceremony for a menu with nothing on it. Open the vocabulary first and
the selection question may look different.

---

## 6. Cross-examination

**Where A must concede.** B5 lands, and it reorders the work. `ValidityCheckName`
is closed and every member is predictive or provenance; no selection mechanism,
however principled, produces a pack for a domain whose checks cannot be named.
A's programme is not wrong, it is *second*. B1 also lands narrowly: A4's
appeal to the Design manifest proves Design can commit to deliverables, not that
it can commit to a *method*, and the pack grades the method.

**Where B must concede.** A1 lands and B has no answer to it. B4's "the server
classifies, by rules pinned in advance" is the strongest version of B, but notice
what it concedes: *something* must be pinned before the run — the classification
rules. B has therefore accepted A's principle and relocated it one level up. The
disagreement is no longer whether the bar is fixed in advance, only *what* is
fixed in advance: a pack, or the function that picks one.

**Where both are wrong.** Both sides argue as if one pack must be selected once.
The code says otherwise: two of the seven current checks are server-owned and
domain-independent, and five are worker-authored and domain-specific. Those two
groups do not need the same selection story, and forcing them to share one is
what makes the question look hard.

---

## 7. What the code settles

Four findings decide arguments that would otherwise be preference.

**Gate-string resolution is now a shipped pattern, not a proposal.**
`test_review.py:243` resolves a gate string off the pinned spec and changes
server behaviour; seventeen other sites do the same in Build. Whatever this
debate concludes, "resolve the pack from the pinned spec key" is a pattern-match
against existing code. Cost is not an argument for either side.

**The two-tier split is observed, not designed.** `reproducibility` and
`reconciled_inputs` are already overwritten by the server from evidence no
worker touches, and are already the only two checks meaningful outside
predictive work. A core tier exists in behaviour today; it simply has no name.

**The closed vocabulary is the binding constraint.** Selection, applicability,
and who signs are all downstream of a ten-name enum in which no domain outside
predictive modelling can state its own checks. Any sequencing that does not
address this first is rearranging a menu.

**The system already has a precedent for "weaker authority, non-promotable."**
`knowledge_ops.py:286-298` lets a cycle proceed to Learn with a skipped Test on a
human-owned decision, and prices it by producing nothing promotable. A cycle
that runs with no domain pack does not need a new concept — it needs this one.

---

## 8. Where this leaves the argument

The motion asks *who selects*. The code says the prior question is *what is even
selectable*, and that once the vocabulary opens, selection splits cleanly in two.

**A core tier, pinned to the stage, selectable by nobody.** `reproducibility`
and `reconciled_inputs` — server-owned, domain-free, already behaving this way.
No cycle of any kind escapes them. B1's uncertainty about method does not touch
them, because they grade provenance rather than method.

**A domain tier, drawn from an open vocabulary, pinned before Build runs.** Here
A1 wins on principle and B1's objection is real but survivable, because the
domain tier is small and about *kind of evidence*, not about method detail. "This
is a predictive holdout" is a commitment a Design can make; "these are the folds"
is not, and should not be in a pack.

**Applicability moves from the assessor to the pack author.** A check that does
not apply is a check the pack does not require. `not_applicable` at assessment
time then honestly means "not established" and correctly yields inconclusive —
and should be required to carry a detail like `passed` and `failed` do, because a
free undocumented N/A is the one status a worker can spend without cost.

**And a declared no-domain-pack path, priced rather than forbidden.** A genuinely
novel domain runs core-only, reaches `supported` if it earns it, and carries in
the validity report the plain statement that no domain pack applied. Whether it
may create a knowledge candidate is exactly the question `knowledge_ops` already
answers for skipped Test, and should be answered the same way.

Under that framing B5 is honoured first, A1 governs the domain tier, B4's
observation of claim shape becomes *evidence the human uses when selecting at
Design* rather than an automatic selector, and B3's escape-hatch objection
narrows to a single answerable question: may an approved pack be amended
mid-cycle, and if so does the cycle keep its promotability.

---

## 9. If this direction is pursued — sequencing

**Not authorized.** Recorded so the next session does not re-derive it.

1. **Resolve the pack from the pinned spec.** `resolve_validity_pack(pack_key)`
   plus a registry; replace the five `DEFAULT_VALIDITY_PACK` imports. Behaviour
   identical — the only registered pack is still `generic-predictive:v2`. No
   migration: `validity_pack_key` already exists and already records it. This is
   worth doing on its own merits and blocks nothing.
2. **Open `ValidityCheckName`.** The binding constraint. Whether checks become
   pack-declared strings validated against the pack, or the enum simply grows,
   is the first real design decision and is not settled here. Note the current
   closed enum is load-bearing for refusal: whatever replaces it must still make
   a worker unable to invent or drop a check *relative to its pinned pack*.
3. **Name the core tier** and make it un-selectable, so no pack can drop
   `reproducibility` or `reconciled_inputs`.
4. **Repair `not_applicable`** — require a detail, and decide whether an
   assessor-declared N/A stays inconclusive (recommended) now that pack-declared
   inapplicability has somewhere else to live.
5. **Add pack selection to the Design gate**, defaulting to the current pack so
   every existing cycle is unchanged.
6. **Add the core-only disclosure path** and decide its promotability against
   the skipped-Test precedent.

Steps 1 and 3 are compatible with either position in this debate. Step 5 is the
motion. Nothing after step 2 should be scoped before step 2 is decided.

---

## 10. Open questions this debate did not settle

**Should the check vocabulary be open strings or a grown enum?** Open strings let
a domain state its own checks and make the refusal relative to the pack rather
than absolute. A grown enum keeps every check name reviewable in one place and
keeps cross-cycle queries meaningful. The first scales; the second is auditable.

**May an approved pack be amended mid-cycle?** B3's objection in full. If yes,
the "fixed before the result existed" guarantee needs a narrower statement —
perhaps that an amendment is itself a human-owned, hash-bound decision that is
visible in the validity report. If no, a clerical error kills a cycle.

**Who may author a pack?** Code-shipped and reviewed, or data in the workspace,
or drafted by the Design council and human-approved. The last is most useful and
is one small step from the model choosing the bar it will be graded against.

**Does the plausible-accuracy ceiling belong to the core or the domain tier?**
It is metric-level rather than check-level and currently forces `invalidated` on
its own. A descriptive cycle has no headline metric at all, so today it also has
no ceiling — which may be correct or may be a second silent gap.

**What does a non-predictive cycle's headline metric look like?** `supported`
requires metrics meeting thresholds. A cycle whose deliverable is a figure has
none, and `not metrics` currently forces `inconclusive` independently of any
check. The pack question does not reach this; it is a separate hole on the same
wall.

---

## 11. Notes for the next session

- Nothing here is decided. §8 is this document's argument, not an agreed
  direction, and §9 is a sketch rather than a plan.
- The single most load-bearing new fact is §2's closed `ValidityCheckName` enum.
  Verify it independently before scoping anything — if it is wrong, §6's
  concession and §9's ordering both change.
- §1 supersedes three items in the predecessor's fact list. That document was
  accurate on 2026-08-02 and is now partly stale; re-verify rather than assume,
  and expect this document to age the same way.
- Facts assembled 2026-08-09 against branch `fix/build-bugs`.

---

## 12. Amendment — Position C (added 2026-08-09, after the debate above)

Added at the owner's argument. Sections 1–11 are unchanged; this position was
not available to the cross-examination in §6 and is not answered by it.

### Position C — the gate we need is already built and unplugged (*the Naturalist*)

**C1. This workflow explores; it does not verify.** DBTL cycles ask open
questions about things nobody knows yet. A and B both assume the fix is a
better-fitting checklist. For exploratory work there is no checklist that fits,
because the point of the work is that the right questions are not known in
advance. Grading a pilot against a confirmatory pack turns every honest
exploration into a failed confirmation, and no amount of pack selection changes
that.

**C2. Reproduction of the figure is already proven, in every domain, with no
pack at all.** `expected_outputs` is required in the rerun record and holds the
files the entry point itself creates (`build_execution.py:205`). The Test rerun
requires each one to match the approved Build hash byte-for-byte
(`test_rerun.py:519-527`). So "did this picture come from this data, by this
script" is settled by machinery that is already shipped and is indifferent to
whether the cycle is predictive. That is the entire deterministic guarantee a
figure-first workflow needs, and it is domain-free by construction.

**C3. What remains is only: what did Design ask for, and did Test get it.** Both
halves exist. Design emits a `deliverable_manifest` with `expected_paths` and
`acceptance_criteria`. Test runs `deliverable_audit`, which **recomputes** each
item's verdict rather than trusting the worker's, requires the criteria be
repeated verbatim so they cannot be softened, and cross-checks every cited hash
against the server-owned Build lineage (`deliverable_audit.py:124-155`,
`adapter.py:2094-2103`).

**C4. And it is unplugged.** `deliverable_audit` contributes **nothing** to
`evaluate_validity` — no reference in either direction. It is a precondition
only: a refusal makes the evidence unusable, but the verdict is computed purely
from the predictive checks and the headline metrics. The system already asks the
right question, already answers it rigorously, and then discards the answer at
the moment of judgement. This is the defect. Pack selection is a detour around
it.

**C5. Meaning is not mechanizable, and pretending otherwise is the actual harm.**
Whether a plot is interesting, surprising, or worth keeping is the human's
call. The remaining machine job is to make that call fast and well-informed:
say what the figure shows, and say what would have to be true for it to be
wrong. That is a presenter's job, and it must **interpret, never certify** —
read-only and citation-bound, as the existing summarizer seat already is. A
model that both composes the presentation and rules on it is the actor judging
itself, which this architecture refuses everywhere else.

### Where C must concede

**B1 survives untouched and C has no answer to it.** Leakage is invisible in a
figure, and "the promised deliverable was delivered and it regenerates" says
nothing whatever about whether the holdout was drawn after filtering. C does not
claim the predictive checks are worthless — it claims they are not *universal*.
A predictive cycle should still face them in full. C removes the pack as the
default judge of all work; it does not remove it as an option.

**C also inherits A1.** Whatever a Design asks for must be asked *before* the
work exists, or the acceptance criteria are a grade chosen after the fact. C
relies on Design-time commitment exactly as the motion does — it simply commits
to *deliverables*, which B1 conceded Design can do, rather than to *method*,
which B argued it cannot.

### What changes if C is right

The critical path in §9 moves. Opening `ValidityCheckName` (step 2) stops being
the blocker, because a discovery cycle no longer needs a domain check vocabulary
at all — it needs reproduction, lineage, and delivery, all of which exist. The
order becomes: wire the deliverable audit into the verdict, make the predictive
pack opt-in rather than default, and fix the two blockers below.

### Two blockers, independent of this whole debate

**PDF and SVG figures are not byte-reproducible.** Measured 2026-08-09 in the
repo's own gateway venv, matplotlib 3.11.1: repeated `savefig` of an identical
figure gives an identical hash for PNG, and a **different** hash for PDF and SVG
(both embed a creation date). A cycle that saves a publication-quality figure in
either format fails the Test rerun deterministically, every time, and comes out
`invalidated` with reason `irreproducible_execution` — over a timestamp, with
nothing wrong with the science. Build's worked example happens to use `.png`,
which is why this has not been hit; nothing requires it, and SVG is recognised
as a figure elsewhere in the bundle. Under C this stops being a curiosity and
becomes a correctness property of the gate.

**A cycle with no headline metric cannot reach a verdict.** `evaluate_validity`
forces `INCONCLUSIVE` when `metrics` is empty, independently of every check and
every pack (`validity.py:266`). A figure-first cycle has no headline metric by
definition. So even with all seven checks passing and every deliverable
delivered, a descriptive cycle is stuck — and `inconclusive` cannot advance to
Learn. This line has to change for C, and arguably for A and B as well.

Both are small, both stand alone, and neither depends on how the larger argument
is resolved.
