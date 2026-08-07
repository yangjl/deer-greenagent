# DBTL Stage Work as a Native Chat Flow

## Summary

Display governed DBTL workers as a chronological native-chat sequence instead
of collecting their explanations inside one progress presentation. Each worker
keeps one compact, expandable native activity card. When that worker finishes,
its existing model-written summary appears immediately after the card as normal
assistant prose. The following worker then appears below it.

This is a presentation change only. It does not change worker dispatch,
streaming events, durable task records, evidence validation, stage transitions,
or human review gates.

## User experience

A phased Build reads from top to bottom:

1. A quiet `Build stage` label identifies the governed stage.
2. The implementation specialist card streams its current native tool activity.
3. When the specialist finishes, its model summary appears below the card using
   ordinary assistant Markdown typography.
4. The independent-audit specialist card appears next, followed by its model
   summary when complete.
5. The synthesis worker follows in the same form.
6. The existing assistant conclusion, presented files, review deck, and human
   gate remain unchanged.

The cards retain their title, model, token usage, status, expandable tool
history, and failure state. They no longer repeat their terminal summary inside
the card when the summary is rendered as prose below it.

## Architecture

Keep the existing `StageWorkPanel` and `SubtaskCard` ownership boundaries:

- `StageWorkPanel` continues selecting the visible run, hydrating durable stage
  workers, grouping them by server-provided DBTL stage, and preserving task
  order.
- `SubtaskCard` continues rendering the native worker header and expandable
  tool timeline.
- Existing task presentation helpers continue selecting safe display prose from
  `displaySummary`, structured-result summaries, or failure information. Raw
  governed JSON remains hidden.

Add only the smallest presentation seam needed for composition:

- Let `StageWorkPanel` suppress a stage worker card's internal terminal report.
- After each terminal card, render that same report through the existing
  `MarkdownContent` component in the ordinary assistant-text style.
- Running workers show no invented prose. Their card remains the live status
  surface until a terminal model summary exists.

No new backend event, task type, API, persistence field, component library, or
model call is introduced.

## Data flow

1. The backend emits the same stage-worker lifecycle and step events.
2. The task provider folds those events into the same `Subtask` records.
3. `StageWorkPanel` resolves the active or most recent governed run exactly as
   it does today.
4. For each task, it renders the card first.
5. For a completed or failed task with safe display prose, it renders that prose
   immediately after the card.
6. Durable hydration reconstructs the same sequence after refresh because both
   card and prose derive from the same task record.

Task order remains oldest first. The client does not infer Build phase meaning
from descriptions or task IDs; the server-provided stage and event order remain
authoritative.

## Failure and recovery behavior

- A failed worker keeps its failed status and expandable diagnostic activity.
- Its bounded failure explanation appears after the card in the same reading
  position as successful prose; accessible status remains present in the card.
- Capped workers continue using the existing capped-failure explanation rather
  than a misleading partial-success summary.
- A hydration error keeps the current capped retry behavior.
- A retry run that has no worker of its own continues falling back to the most
  recent run with governed stage work.
- Refresh must not duplicate prose, move a task to another run, or expose raw
  result contracts.

## Visual rules

- Use the existing chat width, spacing, foreground colors, and Markdown
  renderer.
- Do not add an enclosing card, vertical timeline, progress rail, decorative
  connector, glow, gradient, or new status vocabulary.
- Retain one restrained stage label for orientation.
- Separate each card/prose pair with ordinary chat spacing so the sequence reads
  naturally without looking like a dashboard.
- Preserve reduced-motion behavior and text status; color is never the only
  indication of success or failure.

## Testing

Follow test-driven development:

1. Add a focused DOM test that renders two terminal stage workers and proves the
   order is card 1, prose 1, card 2, prose 2.
2. Prove the summary is no longer rendered inside the stage worker card.
3. Cover a running worker with no terminal prose and a failed/capped worker with
   the existing safe failure explanation.
4. Preserve current durable hydration and live-run-selection tests.
5. Run the focused frontend tests, the affected unit suite, `pnpm check`,
   formatting, and `git diff --check`.
6. Use the manual DBTL profile and in-app browser to replay the deterministic
   `y = 2x + 1` Build, then refresh and verify the sequence survives.

## Acceptance criteria

- Every governed Build worker has its own native expandable activity card.
- Each terminal worker's model prose follows that card outside its border.
- Multiple workers read chronologically rather than as one hidden report.
- No terminal prose is duplicated inside and outside a card.
- Running, failed, capped, refreshed, and retried runs remain truthful.
- Existing review decks and human gates are unchanged.
- No backend contract or new dependency is required.

## Out of scope

- Persisting worker summaries as new assistant messages.
- Changing model prompts or adding narration calls.
- Redesigning ordinary subagent cards.
- Replacing the authenticated Design, Build, Test, or Learn review decks.
- Changing DBTL routing, evidence validation, or stage transitions.
