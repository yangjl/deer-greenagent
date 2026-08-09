---
name: dbtl-build-deck-style
description: Compose a concise DBTL Build review slide plan from verified outcomes, figures, caveats, and reviewer feedback.
---

# DBTL Build deck style

Use this skill when producing the Build summarizer's `slide_plan`. You choose
content and order only. The deterministic renderer owns typography, layout,
navigation, controls, and HTML.

## Composition rules

- Target 5–8 content slides; never exceed 10.
- Lead with the result. If a verified figure carries the result, put it before
  process detail.
- Put at most one verified figure on a slide and write one sentence saying what
  it shows. Never grade the result or claim it satisfies the Design.
- Give every non-figure slide concise body text. Omit a slide instead of writing
  “none reported.”
- Rank and deduplicate deviations and limitations. Keep the few that could
  change a review decision; the Markdown record retains the complete audit list.
- Use recent reviewer comments as presentation guidance, not as scientific
  evidence. Current verified artifacts always win.
- Keep titles declarative and specific: “Holdout error rises at low yield,” not
  “Results.”

## Contract

Each item uses one supported `kind`: `summary`, `outcomes`, `figure`, `phases`,
`deliverables`, `limitations`, or `rerun`. Supply `title` and trimmed `body`.
For `figure`, also supply exactly one `figure_path` copied byte-for-byte from the
verified bundle and one short `figure_reading`.

If a safe plan cannot be made from the verified bundle, omit `slide_plan`. The
server will render its deterministic fallback deck.
