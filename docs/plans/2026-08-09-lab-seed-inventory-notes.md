# Lab seed inventory + provenance graph — design notes

Working notes, 9 Aug 2026. A separate system from GreenAgent/DBTL, but the layer
underneath it. Captures a conversation that started with field data ingest and
ended somewhere more useful.

---

## 1. The problem, stated precisely

Students get confused — and so does everyone else — because **"BC3F2" is a stage,
not an identity**. Two envelopes labelled `edit × B73 BC3F2` can hold genetically
different material: different BC2 ancestor, different segregants. The label
describes the recipe, not what's in the packet.

Zygosity compounds it. It's a property of a specific lot, it re-segregates every
generation, and it is only known if somebody genotyped *that lot*. The classic
error is "this line is homozygous" when the assay was run on the grandparent and
two selfings have happened since.

Nobody is being careless. The naming scheme is throwing information away.

---

## 2. The model

**Barcode the packet, not the line.** The name becomes a human-readable label; the
ID is the identity. Two BC3F2 sibs then become structurally impossible to conflate.
That single decision is most of the fix.

Three tables:

- **Lot** (node) — a physical packet. ID, location, quantity, immutable once created.
- **Event** (edge) — cross, self, or increase. Links parent lot(s) → child lot.
- **Observation** — attaches to a *lot*, never to a name. Edit present/absent,
  zygosity, marker data, who assayed it, when.

That's a DAG, and the questions that currently require memory become queries:

- real pedigree of this packet → walk up the edges
- BC3 lots homozygous for the edit with >50 seeds → filter
- **lots never genotyped → left join for nulls** (nobody can answer this today)
- fraction recurrent parent → count backcross edges, or use markers if typed

---

## 3. Why this matters beyond convenience

This is the same identity problem as the 100-year, multi-lab germplasm mess —
caught upstream, where it is free. That mess exists precisely because labs in 1975
labelled envelopes instead of assigning IDs.

Do this now and the crosswalk is *authored as you go* rather than reconstructed
from wreckage. Your program's data never becomes somebody's entity-resolution
nightmare in 2050.

---

## 4. Shared-lab deployment

**Decision: managed Postgres (Supabase or Neon).**

SQLite was the earlier recommendation and is wrong the moment it's shared — a
SQLite file on a network drive or Dropbox is the classic way to corrupt a lab's
data, because file locking over NFS/SMB is unreliable.

**The real constraint is succession, not concurrency.** Whoever sets this up will
graduate. Hence: no server anyone must babysit, data exportable to plain files at
any moment so the platform stays disposable, correctness enforced by the database
rather than by everyone remembering the rules.

**Scheduled dump to CSV in a git repo.** This is what makes the vendor replaceable
and the diffs reviewable. Non-negotiable.

### Three rules that outrank the platform choice

**Append-only wherever possible.** Never update a lot's seed count — insert a
transaction row (20 seeds out, packet returned, lot exhausted). Balance is a sum.
If students can overwrite a number they will silently undo each other and nobody
will know which value was right. Costs nothing to adopt on day one; impossible to
retrofit.

**Constraints in the database, not the app.** Unique on barcode, FK from every edge
to real lots, CHECK on the zygosity vocabulary. Anything enforced in a script gets
bypassed the first time someone writes their own script — and someone always does.

**Controlled vocabularies as tables, not free text.** Otherwise: fourteen spellings
of "homozygous", and you are doing entity resolution on your own lab's data.

### Two smaller ones

Nobody ever types a barcode — generate and print. A transposed lot ID is
undetectable. And keep **one write path**: if people can also edit a spreadsheet
and re-import, the constraints and the audit trail are both gone.

### The alternative worth naming

Airtable, or NocoDB/Baserow self-hosted. Zero training, native barcode scanning on
mobile, running by Friday. Cost is schema discipline — people add columns and
nothing stops bad data. Where adoption is the real risk, that trade can be correct:
a tool everyone uses beats a better one they route around.

---

## 5. LLM ingestion of legacy material

**The rule: the model writes to staging, never to the real tables.**

```sql
create table staging_lot (
  id             bigserial primary key,
  source_sha256  text not null,          -- the photo; immutable, in storage
  verbatim       text not null,          -- what the model literally read
  parsed         jsonb not null,         -- its interpretation
  disagreement   text[] default '{}',    -- fields two passes disagreed on
  model          text not null,
  prompt_version text not null,
  status         text not null default 'pending'
                 check (status in ('pending','committed','rejected')),
  reviewed_by    text,
  reviewed_at    timestamptz
);
```

**Separate verbatim from parsed.** The non-obvious one. The model returns both what
is literally written and what it thinks that means, so a reviewer can check the
parse against the transcription without reopening the image. Roughly 3× faster
review, and it catches the case where the transcription is right and the
interpretation is wrong.

**Confidence comes from disagreement, not from the model.** Self-reported
confidence is close to worthless. Run each image twice — two passes or two models —
and flag every field where they differ. Real signal, a few cents per envelope, and
it lets the review queue be sorted so attention goes where uncertainty is. Most
envelopes are clean and can be approved in bulk.

**"Illegible" must be a first-class value in the schema.** With no way to say "I
can't read this," the model invents something plausible. Enum every field you can —
zygosity, generation type, cross vs. self — so you validate against a vocabulary
instead of parsing prose afterwards.

**The model never assigns IDs.** It transcribes and parses; code mints lot IDs. A
model-generated identifier is unverifiable by construction.

**Version the prompt in every row.** You will improve the prompt and re-run, and
you'll need to know which rows came from which — the same spec-pinning discipline
already built into DBTL, for the same reason.

**Notebooks need a different pipeline than envelopes.** An envelope is short and
semi-structured. A notebook page is narrative and self-referential ("same cross as
above", "the good one from last year"). Two passes: extract and review the
*transcript* first, then extract records from the approved transcript. One pass
compounds a transcription error into a wrong record with no trace of where it went
wrong.

**Review UI: don't build a web app.** Streamlit — image left, editable fields
right, approve/reject — is ~100 lines. Supabase's own table editor with the image
URL rendered is less work still if it's tolerable.

### Sequencing matters more than the pipeline

Do **not** start with the backlog. Start with this season's material, where output
can be verified against your own memory, and use it to calibrate.

Then work backward, **newest first** — the people who can confirm what a 2024
envelope means are still in the lab; the people who could confirm 1998 are gone.
Every year of delay makes verification harder.

**Lazy filter that saves the most time:** only ingest lots whose seed is still
viable, plus whatever ancestors are needed for their pedigree. Forty years of
envelopes holding dead seed is archaeology, not inventory.

---

## 6. Prior art

Real and mature, worth not reimplementing:

- **Breedbase** — full seedlot management ([manual ch. 7](https://solgenomics.github.io/sgn/managing-seed-lots.html)),
  Chado schema, BrAPI, open source ([G3 paper](https://academic.oup.com/g3journal/article/12/7/jkac078/6564228))
- **BMS / Integrated Breeding Platform** — [inventory module](https://bmspro.io/617/breeding-management-system-manual-40/manage-inventory)
- **Germinate** (Hutton), **Phenome Networks** (commercial), **Agronomix**
- **QBMS** (R) queries BMS/Breedbase/Germinate through one interface

Where all of them get thin is exactly this case: **edit zygosity, null segregants,
transgene-free status, and per-lot genotyping provenance.** Which is why every lab
ends up in a spreadsheet.

For the identity-matching half, if it's ever needed: Splink (probabilistic linkage),
`dedupe` (active learning — proposes ambiguous pairs, human labels, retrains; the
"AI proposes, human verdicts" loop, shipped since ~2014), Zingg.

---

## 7. Scope discipline

This is **not** a DBTL cycle and barely needs an agent. Three tables, a barcode
printer, a phone that scans. The LLM earns its place in exactly two spots: reading
existing labels and notebooks into the graph retroactively, and letting a student
ask "which BC3 families still need genotyping" without writing SQL.

Related but separate, from the same conversation: the genuinely DBTL-shaped problem
in this space is **germplasm identity reconciliation** — merges are claims with
evidence, needing a durable human verdict that stays *invalidatable* when new
genotyping contradicts it. Nothing in the breeding informatics stack propagates a
retracted identity assertion forward to the analyses that consumed it. That gap is
the real contribution, not the AI and not the database.

---

## 8. Open

- Whether the department could host Postgres and still be hosting it in five years
  (if yes, that beats a vendor — otherwise managed is right)
- Barcode format and label stock that survives a cold room and a pocket
- Whether to borrow BrAPI column names so the schema isn't invented from scratch
- Offline capture in the field — no signal in a cornfield, so field notes need a
  local queue that syncs later
