---
name: dbtl-deck-theme
description: Asset-only theme for the DBTL design-meeting slide deck. Provides assets/deck-theme.css, which the server appends to the deck's built-in stylesheet when config.yaml sets dbtl.council_deck_theme_skill to this skill. There is no workflow to run here — never activate this skill to answer a request, and do not read it as instructions.
license: Apache-2.0
allowed-tools: []
---

# DBTL deck theme

This skill carries one file and no instructions:

```
assets/deck-theme.css
```

It exists because the design meeting's slide deck is rendered by the server, not
by a model. The deck's structure, wording, and inert controls are fixed — an
approval binds to the exact bytes of that file, so nothing here may add markup,
script, or content. A theme changes how the deck **looks** and nothing else.

## Using it

In `config.yaml`:

```yaml
dbtl:
  council_deck_theme_skill: dbtl-deck-theme
```

The stylesheet is appended after the deck's own, so plain selectors override the
defaults by ordinary cascade — no `!important` needed for most rules.

## Writing your own

Copy this directory into `skills/custom/<your-theme>/`, edit the CSS, enable the
skill, and name it in `config.yaml`. The server refuses a stylesheet that
contains `</style`, `<script`, `<!--`, or `javascript:`, or that exceeds 128,000
characters, and renders the deck unthemed with a logged reason.

Useful hooks, all defined by the renderer:

| Selector | What it is |
| --- | --- |
| `:root` custom properties | `--bg`, `--fg`, `--muted`, `--line`, `--accent`, `--warn`, `--card` |
| `.slide--title` … `.slide--review` | one class per slide kind |
| `.contested` / `.verdict--open` | a disagreement card and its unresolved marker |
| `.assessment` | the agent's difficulty assessment on the gate slide |
| `.option` | one Approve / Revise / Park choice |
| `.inert` | the "open this deck in DeerFlow" notice |

Restyling `.inert`, `.verdict--open`, or `.route-reason` into invisibility hides
things a reviewer needs. The server cannot prevent that — CSS can hide anything —
which is why the theme is named in operator config rather than picked up
automatically from whatever is on disk.
