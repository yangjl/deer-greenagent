# StageWorkHydrator Stability and Run 3 Design

**Status:** Approved by the owner on 2026-08-09

## Goal

Remove the extra `StageWorkHydrator` fetch exposed by frontend verification,
prove the frontend gate is green, and then execute campaign Run 3's Design
clarification route without authorizing Run 4.

## Hydrator root cause

`StageWorkHydrator` intentionally depends on `isLoading` so a live run can
cancel an older durable read and immediately rehydrate stage workers. Its
cleanup currently clears the per-thread hydration guard on both transitions:
idle → running and running → idle. The first clear is required; the second
clear allows an unintended third request. The existing committed DOM test
reproduces that third request as `fetchStageWorkers(...).then` on `undefined`.

## Approved direction and investigation amendment

The initially approved cleanup-only condition prevented the third fetch but
also canceled the valid second request when the run settled. The corrected
root fix makes hydration react to the rising edge of `isLoading`, not both
edges: a small loading-epoch signal changes only when a run starts. The main
effect still cleans up normally on a new run, retry, thread change, or unmount,
but settling a run no longer tears down its in-flight hydration.

The regression mock returns the same settled fixture for later calls so an
excess request fails on the behavior assertion (`called twice`) rather than an
incidental `.then` error. Wrapping an undefined mock result in
`Promise.resolve` remains rejected because it hides the excess call. A larger
hydration state machine remains unnecessary.

## Verification gate

The already-failing same-epoch cancellation test is the red test. After the
one-line fix, run its file, the entire frontend Rstest suite, TypeScript, ESLint,
Prettier, and `git diff --check`. Run 3 may start only after this gate is green.

## Run 3 scenario

Restore the frozen `dbtl-reliability-run1-start` baseline. Use only the
governed project and visible browser controls; do not run an ordinary-chat arm.
The Design prompt declares one deliberately human-owned ambiguity: the chair
must ask whether `human_replay.ipynb` or `REPLAY.md` is the canonical human
replay surface. The owner answers `human_replay.ipynb` through the authenticated
Design deck.

Success requires the independent and red-team positions to run once, the first
chair attempt to end `needs_input`, and the answer to resume the chair alone
over the recorded positions. The resulting Design is approved, followed by one
small Build, independent Test `supported`, provisional-only Learn, and a durable
completed cycle. The immutable input hashes and the 350,000-token/45-minute
attempt stops remain unchanged. Run 4 is not authorized.

## Evidence

Record the frontend red/green output, route and worker-run IDs, both chair
attempts, exact clarification and answer, tokens and elapsed time, deck/evidence
hashes, Build manifest and clean-rerun hashes, Test checks/outcome/route, Learn
candidate/promotion/publication counts, refreshed terminal state, and any
product or worker defect.
