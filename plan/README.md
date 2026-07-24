# Plans

Agent-authored plans are saved here before implementation.

## Directory Structure

| Directory | DBTL Role | Contents |
|-----------|-----------|----------|
| `design/` | Designer | Hypotheses, one-page designs, debate plans, decision requests |
| `build/`  | Builder  | Phased implementation plans, baselines, self-test results |
| `test/`   | Tester   | Drift reports, boundary checks, reproduction reports |
| `learn/`  | Learner  | Cycle reviews, promotion proposals, next-step plans |

## Conventions

- Use descriptive filenames: `YYYY-MM-DD-short-title.md`.
- Place files in the correct DBTL subdirectory. Only cross-role roadmaps belong directly in `plan/`.
- Include a `Human Input Needed` section when the plan requires human decisions.
- The human must cross-check a saved plan before implementation begins.
- A saved plan does not override human-review gates for research direction, methodology, or interpretation.
