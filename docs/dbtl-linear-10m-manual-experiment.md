# DBTL linear-fit manual experiment

This is a paired, human-replayable smoke experiment for the manual DBTL profile. It compares the governed Design → Build → Test → Learn path with an ordinary chat asked to solve the same deterministic task. A healthy replay should finish in less than ten minutes after the two prompts are submitted; setup and profile startup are not included.

## Start from the recorded baseline

```bash
make dbtl-manual-init
make dbtl-manual-restore SCENARIO=linear-10m-start
make dbtl-manual-dev
```

Open `http://localhost:2026`, sign in to the manual profile, and select **GPT-5.6 Sol (Codex Subscription)** in both conversations. Run the two arms at the same time:

- DBTL project: `DBTL Linear 10m`
- Ordinary project: `Ordinary Linear 10m`

The baseline already contains the same immutable inputs in each project:

- `EXPERIMENT.md`
- `data/train.csv`
- `data/holdout.csv`

## Prompts

DBTL arm:

> Start a new DBTL cycle for EXPERIMENT.md. Objective: infer y = a*x + b from data/train.csv and verify it on data/holdout.csv using only Python's standard library. Required outputs are fit.py, model.json, predictions.csv, and metrics.json in the project workspace. Success requires a = 2, b = 1, holdout MAE = 0, and byte-identical outputs after rerunning python fit.py. The input CSV files are immutable.

Ordinary-chat arm:

> Read EXPERIMENT.md and complete it now as a small file-creation task. Use only Python's standard library. Create fit.py, model.json, predictions.csv, and metrics.json in the project root, run python fit.py, rerun it, verify the outputs are byte-identical, and report the fitted slope, intercept, and holdout MAE.

## Human decisions in the DBTL arm

Use these choices so different people exercise the same path:

1. Choose **Create this DBTL cycle**.
2. Answer the Design input question with the suggested strict schema and numeric-validation choice.
3. Choose **Light** for the Design meeting.
4. In the Design HTML deck, submit and approve the design.
5. Choose **Start Build**. If the planner proposes more than one phase, choose **Change the plan** and reply exactly:

   > Use exactly one small phase. In that phase implement fit.py, run it twice, verify the four required files, exact a=2 and b=1, MAE=0, unchanged input hashes, byte-identical reruns, and return typed key_outcomes plus a precise rerun_spec. Do not add a second independent verification phase; Test owns independent verification.

   Accept the resulting one-phase plan, and approve the Build deck only if the four outputs and deterministic rerun evidence are present.
6. Choose **Start Test**. Record the server-computed outcome and select the offered route to Learn only when it is outcome-compatible.
7. Choose **Start Learn**. Leave any knowledge candidate provisional; do not promote or publish it in this smoke experiment.

The ordinary arm has one setup proposal when conversational discovery classifies the request. Choose **Keep as ordinary chat**; subsequent messages must remain ordinary.

## Pass criteria

Both arms pass the numerical task only when:

- the fitted values are `a = 2` and `b = 1`;
- holdout MAE is `0`;
- `fit.py`, `model.json`, `predictions.csv`, and `metrics.json` exist;
- rerunning `python fit.py` leaves all four files byte-identical; and
- the two input CSV hashes are unchanged.

The DBTL arm additionally passes its workflow check only when every stage result is server-recorded, every stage transition uses a visible human control, Test computes an outcome from typed evidence, and Learn creates only provisional candidates.

Capture the end state only after all runs are terminal:

```bash
make dbtl-manual-capture SCENARIO=linear-10m-final
```

`linear-10m-routing-failure` preserves the first failed routing attempt for regression diagnosis. It is evidence, not a replay starting point.
`linear-10m-contract-failures` preserves the first Build/Test contract and path failures. `linear-10m-final` preserves the repaired end state with Learn candidates still proposed and unpublished. Neither is a replay starting point.

## Reference run on 2026-08-05

The paired run started at 18:39:48 UTC with GPT-5.6 Sol Codex. The ordinary arm completed in 38.8 seconds and passed every numerical/file criterion. The DBTL arm produced correct Design and Build evidence, but did **not** meet the ten-minute budget: Design was approved at 3m45s and Build at 11m59s. Test then exposed runtime blockers, so the cycle was deliberately continued past the time box to diagnose and repair them. A server-computed `supported` Test outcome was recorded at 19:26:19 UTC, and corrected Learn evidence was recorded at 19:32:30 UTC. The debugging run therefore took about 52m42s from the paired start; treat the under-ten-minute target as a benchmark that currently fails, not as a documented product capability.

The final DBTL evidence passes the numerical/file checks. Test's authoritative rerun verified both pinned inputs, exited zero, and reproduced all three generated output hashes. Its seven required validity checks passed. Learn produced five evidence-bound candidates, all with status `proposed`; the project has zero promoted claims and zero publications.

The reference arm kept the interpretation narrow: the perfect result verifies the supplied two-row noiseless affine fixture; it is not evidence of broad predictive generalization. The repaired run also confirmed that Test approval now surfaces a revision-bound **Start Learn / Hold here** card before Learn can dispatch. During API-driven replay, include `context.model_name: gpt-5.6-sol-codex`; omitting the manual UI's model selection falls back to the deployment default and is not the recorded experiment configuration.

## Blockers repaired during the reference run

- Project routing resolved the lead agent before authenticated project context existed, so explicit DBTL requests could fall into ordinary chat.
- Graph-authored Human Input cards could be dropped from durable history, hidden by an over-broad Design-deck rule, or treated as answered without a bound response.
- Local sandbox command/path rewriting and project-scope caching failed on workspace paths containing spaces and on fresh Test rerun sandboxes.
- Build outputs and rerun metadata were not normalized tightly enough for server-owned Test verification; Test metric criteria also lacked an explicit `gte`/`lte` contract.
- Test review selected the oldest failed retry instead of the newest complete retry.
- A separately submitted Test could fail to recover its chat decision card, and advancing Test to Learn did not emit or recover the required Start/Hold handoff.

Each repair has a focused regression check. The consolidated backend, frontend unit, typecheck, lint, and diff checks used for this run are recorded in the implementation handoff rather than in the replay procedure.
