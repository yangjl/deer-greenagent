# DBTL Run 6 Contract and Routing Repair

## Goal

Prevent Build evidence that depends on undeclared ambient `DBTL_INPUT_n` values from reaching Test, and route explicit “repair/fix <stage>” requests to the existing governed cycle instead of new-cycle setup.

## Implementation

1. Add Build-verifier regression tests proving that an issued-but-undeclared input is absent at execution and that declared runtime inputs are compactly numbered exactly as Test numbers `rerun_spec.inputs`.
2. Make the Build verifier construct its execution environment from `phase_manifest.execution_inputs`, after validating those paths against the broader server-issued catalog.
3. During v12 phase verification, reject a parseable worker `rerun_spec` whose ordered inputs differ from the phase manifest's runtime inputs. This makes the Build execution proof and the persisted Test contract identical.
4. Update Build and correction prompts plus backend architecture notes to describe the compact runtime-input contract.
5. Add the exact Run 6 “Perform one minimal governed Build repair…” sentence to the unscoped-stage routing regression and recognize only the additional explicit verbs `repair` and `fix`.
6. Run focused tests, adjacent DBTL suites, Ruff, OCR delegate review, then restart Gateway and replay the failed Run 6 path in the browser.

## Non-goals

- Do not weaken Test validation or inject undeclared inputs into Test.
- Do not change DBTL gate authority, cycle selection rules, or card actions.
- Do not resume Runs 7–8 until the Run 6 replay is clean.
- Do not commit or push in this task.
