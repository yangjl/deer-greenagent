# Conversational DBTL discovery manual scenarios

Use the isolated DBTL profile so a scenario captures the SQL database and the
human-visible project directory together:

```bash
make dbtl-manual-refresh
make dbtl-manual-dev
```

The generated profile enables explicit conversational discovery and bounded
project history. Classifier entry, automatic offers, global memory, and every
background writer stay disabled. Open Settings → DBTL readiness first and
confirm that cycle creation says **server** and the rollout stage says
**Explicit discovery**.

For each scenario, record the visible transcript, the discovery status beside
the composer, the cycle count before and after, and any Design preflight. Once
no run is active, capture the state with:

```bash
make dbtl-manual-capture SCENARIO=<scenario-name>
```

## Required release-window scenarios

1. **Explicit start, incomplete brief** — Ask to start a DBTL cycle with only a
   broad objective. Confirm ordinary Lead discussion continues, the status is
   gathering, and no cycle or project file is created.
2. **Ready proposal** — Supply objective, intended output, known inputs, and a
   success criterion. Ask to see the proposal. Confirm the card identifies
   user statements versus suggestions and carries the current revision/hash.
3. **Keep discussing** — Choose Keep discussing. Confirm no cycle exists and a
   later turn updates the same discovery rather than opening another one.
4. **Continue as ordinary work** — Decline the proposal. Confirm the discovery
   is terminal, no cycle exists, and a later classifier-shaped request remains
   ordinary. An explicit start must still open a new discovery.
5. **Start exactly once** — Accept the newest card and retry the same answer.
   Confirm one cycle, one provenance event, one creation receipt, and one
   Design preflight; the browser sends no hidden kickoff message.
6. **Stale card** — Preserve an older proposal card, advance the discovery, and
   answer the old card. Confirm the server refuses it and creates no cycle.
7. **Refresh recovery** — Refresh while gathering and again while a start card
   is waiting. Confirm status/card recovery comes from the server and the same
   thread can continue.
8. **Project-history boundary** — Put relevant and conflicting facts in another
   authorized conversation in the same project. Confirm bounded source refs and
   conflict labels appear. Facts from another project/member must not appear.
9. **Unavailable enrichment** — Make project history or memory unavailable.
   Confirm the current transcript still progresses and absent sources are not
   claimed as consulted.
10. **Rollback setup** — Set `dbtl.conversational_discovery: false`, restart,
    and explicitly start a cycle. Confirm the server-owned immediate setup card
    creates the cycle, then answering Design questions opens the Design
    preflight without browser cycle creation or a hidden kickoff.
11. **Classifier rollout** — In a disposable profile, enable classifier entry
    while leaving automatic offers off. Confirm eligible suggestions gather
    silently, an ordinary-work choice suppresses re-entry, and an explicit
    request to review is required. Then enable automatic offers and verify only
    ready drafts receive a card.

## Rollout evidence

Do not enable classifier entry or automatic offers in a shared environment
from code completion alone. Review the admin evaluation drawer and captured
scenarios for false entry, abandonment, time-to-offer, accepted-draft
corrections, stale-card refusals, and duplicate-cycle attempts. Roll back by
disabling `dbtl.conversational_discovery`; the fallback remains server-owned
through the release window.
