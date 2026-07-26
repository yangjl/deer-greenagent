# DBTL Phase 7 — implementation notes

Status: implementation complete; human exit review pending
Date: 2026-07-26
Plan authority: `plan/build/2026-07-25-dbtl-human-visible-phased-implementation-plan.md` § "Phase 7"
Design authority: `plan/design/2026-07-25-dbtl-workflow-final-plan.md` §§ 6.4–6.6

## Shipped

- Executable `generic:build:v1` and `generic:test:v1` stage contracts. Learn
  remains unavailable until Phase 8.
- A versioned, provisional `generic-predictive:v1` validity pack with structure
  null, fold composition, predictive ceiling, direction, leakage, tester
  holdout, within-group, duplicates/relatedness, reproducibility, and reconciled
  input checks.
- Build lineage bound to the approved dataset fingerprint with code/config,
  environment, inputs, versioned outputs, deviations, and logs.
- A typed human Test assessment that computes `supported`, `not_supported`,
  `inconclusive`, or `invalidated` and constrains workflow routing.
- A migration for `dbtl_build_lineage` and
  `dbtl_validity_assessments`, plus REST reads/writes and live Build/Test stage
  execution.
- A Build reproducibility ledger and Test two-column evidence review. Failed
  validity is dominant even when the headline metric meets its threshold.
- A presentation icon beside Cycles opens a one-click, no-write human demo with
  invalidated, inconclusive, and supported fixtures.
- Starting a real cycle queues `generic:design:v2`: project context feeds
  independent specialist and red-team positions, a chair asks one focused
  clarification when needed, and only a completed synthesis creates review
  evidence. Human review still owns stage advancement.

## Safety properties

1. Build submission is refused without durable reproducibility lineage.
2. Test cannot use the generic approve endpoint.
3. The server computes the outcome; the client supplies evidence, not a verdict.
4. `invalidated` and `inconclusive` cannot advance to Learn.
5. A valid negative (`not_supported`) can advance to Learn for synthesis.
6. Reviewer identity and project role are server-owned and internal principals
   cannot make the human decision.
7. Knowledge promotion remains unavailable; Phase 7 cannot turn a Test result
   into authoritative project knowledge.

## Demo fixtures covered

- pooled accuracy `0.94` plus leakage → `invalidated`, routable to Build;
- missing independent holdout → `inconclusive`, closable without a claim; and
- complete validity evidence plus a passing metric → `supported`, eligible for
  a human-recorded Learn route.

## Deliberate deferrals

The first pack is marked provisional. Domain-specific evidence grades,
plant-science thresholds, and the first maize/genomic-selection validity pack
remain human review decisions; this implementation does not invent them.
Phase 6’s deferred decisions remain recorded in
`2026-07-26-dbtl-phase6-implementation-notes.md`.

## Human exit review

A plant-science practitioner and Tester can open the presentation icon beside
Cycles and inspect all three fixtures without creating records. They must
confirm that result language cannot overstate the evidence. No-go if headline
accuracy can render as success while any validity gate fails.
