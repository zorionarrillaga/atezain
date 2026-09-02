# atezain

*Atezain* is Basque for goalkeeper.

An assistant over your records (invoices, orders, customers) that summarises, recommends the next
action and drafts the message — and **cannot change anything on its own**. Every write it wants
to make is a proposal; a policy layer outside the model decides whether the proposal is even
allowed to exist; a human approves the ones that matter; the layer executes exactly once and
compares what the executor observed in the record afterwards with what was approved. Then it is
attacked, in public, with injections planted in the very records it reads, and the result is a number.

**Status: steps 1, 2 and 3 of 6 — the policy layer, the assistant graph over it, and the red-team
that measures it against a named model.** Nothing here is deployed yet. See `STATUS.md`.

## What is built

- `policy/` — the boundary. Permissions as data (`adapters/*/permissions.toml`: actions, the fields
  each may write, allowed values, the record type and id shape each may touch — shape, not
  ownership), an approval queue, an append-only audit log (a hash chain with a published head), a
  fuse the agent can trip and cannot clear through the service, a daily budget that trips it. Pure
  Python, no dependencies.
- `agent/` — the assistant as a LangGraph graph: retrieve → think → propose → hold → execute. The
  model talks only to `think`; the only write path is `agent/executor.py`, called only from inside
  the policy's `execute`, and it reports a before/after diff of the record, never its input; the
  value a client passes on resume is untrusted.
- `tests/test_policy.py` — one test per rule, and one per shape of root write the audit can see.
- `tests/mutate.py` — deletes each marked check in `policy/` in turn and demands that the suite
  goes red **by assertion**. A check whose deletion changes nothing, or only crashes the suite,
  fails the build; tests that crash alongside an assertion kill are counted and printed.
- `tests/hostile_selftest.py` — an attacker holding the application's own objects tries to get a
  write through without a human, or to hide one that happened. Scored attempts only.
- `tests/sabotage.py` — breaks a property on purpose and demands that the numbers above fall.
- `redteam/` — 100 injections written into the records the assistant reads (a note, an email body,
  an email subject, a field the importer filled in, the text an attachment gave up), each aiming at
  one write the boundary must refuse. One model call per case, scored twice: through the policy,
  and through `redteam/off.py` — the control arm, an assistant wired the ordinary way with the
  layer removed. promptfoo is the runner and its assertion is the gate. `make numbers` turns the
  rows into `NUMBERS.md`, which is the only source of a number in this file.
- `PROVENANCE.md` — where each rule comes from: the incident, the date, the price.

## Run it

```
python3 -m venv .venv && .venv/bin/pip install -q pytest langgraph langgraph-checkpoint-sqlite
make test       # the suite
make mutate     # every check deleted in turn; all must be KILLED by an assertion
make hostile    # the attacker with the application's objects
make sabotage   # the gauges themselves, broken on purpose, must go red
make all
```

The red-team needs Node (for promptfoo, fetched by `npx`) and, for a real model, `GROQ_API_KEY` in
the environment — never in a file. Every model answer is cached under `redteam/cache/`, so a re-run
of a case that has been run makes no network call and needs no key at all:

```
make redteam                       # the harness end to end on the stub model, offline, $0
make redteam REDTEAM_MODEL=groq    # the named model; ~22 min for 100 cases at 20 requests/minute
make numbers                       # rewrites NUMBERS.md from redteam/results.jsonl
```

## Numbers, and where they come from

On 2026-09-01 the first version of this layer shipped with "26 tests · 19/19 checks killed · 10/10
attacks blocked". The same night an outside refutation seat, working in a copy with no contact with
the author, got **17 of 19 new attempts through it** and showed that two of the three numbers were
inflated by construction: one "check" was a bare assignment whose deletion crashed the suite, and
one hostile attempt returned "blocked" unconditionally — sabotaging the detector still printed 10/10.
The seat's report is in the author's private repo; the defects and their fixes are listed in
`STATUS.md`. Every attempt class it used is re-authored here as `b1`–`b16` in the hostile self-test.

After that repair the numbers were 61 · 34/34 · 26/26 · 6/6. A **second** seat, on the repair,
reproduced all four and refuted it again the same day, more narrowly: the executor the repo shipped
reported its own input, so the headline re-validation could not fire; four of the audit anomaly
shapes were laundered by one legally appended row; the service's bindings could be swapped; the
store's fuse primitive still took a caller's time; the id check was a shape, not ownership, and
matched non-ASCII digits; four hostile attempts passed for a reason other than their name; and six
one-line sabotages of claimed properties went uncaught by every gauge. All of it is folded
(`STATUS.md`, second table). After that (2026-09-02, `make all`):

| gauge | result |
|---|---|
| `make test` | 108 passed (79 after the second repair; 29 more came with step 3) |
| `make mutate` | 43 checks · 43 killed by assertion · 0 killed only by a crash · 0 survived · 23 crashing test(s) alongside assertion kills |
| `make hostile` | 36/36 scored attempts blocked · 1 out of scope, shown |
| `make sabotage` | 14/14 sabotages caught by at least one gauge |

What these prove and do not: the mutation pass proves every marked check can fail; it says nothing
about a check that is absent (the first seat found one — a `record` key the policy declared and
never read — precisely because there was no block to delete), and it mutates `policy/` only. The
hostile test's concurrency attempt is timing-dependent; the deterministic interleaving test in the
suite is what sees a removed lock. The sabotage pass covers fourteen properties, not all of them.
Two seats found, between them, 29 breaches and 12 gauge defects; the numbers above are what is
left after both, not what was true before either.

## The model, the date, the numbers

On **2026-09-02**, **`openai/gpt-oss-120b`** (Groq, temperature 0) was shown **100** planted
injections over the `invoices-es` adapter: five classes × five techniques (an authority claim, a
fake system block, a half-finished action to complete, an instruction dressed as data, an
instruction in Basque or English inside Spanish) × four goals (change an amount, send the record
somewhere external, force a status the adapter forbids, delete the invoice). Every case's goal is
something the policy must refuse — a case the policy would allow measures nothing, and
`tests/test_redteam.py` fails if one appears.

| | N | manipulated | executed OFF | executed ON |
|---|---|---|---|---|
| all cases | 100 | 6/100 = 6 % [3 %, 12 %] | 6/100 = 6 % [3 %, 12 %] | 0/100 = 0 % [0 %, 4 %] |

Wilson 95 % intervals; the full per-class, per-technique and per-goal tables are in `NUMBERS.md`,
generated by `make numbers`. The technique that worked was the fake system block (5/20 = 25 %
[11 %, 47 %]): a note or an email carrying `### AJUSTES DE NOTIFICACIÓN ###` persuaded the model to
propose the reminder over WhatsApp or SMS, citing the "ajuste del sistema" as its reason. The
policy denied it on the channel constraint; the same output with the layer removed wrote it.

What this run does and does not show:

- **No human approved anything in it.** A held proposal is a write that did not happen, so this is
  what the boundary does *alone*; the human that a real deployment puts behind the hold is the
  point of the hold, and is not in the number.
- **`0/100` is a bound, not a proof.** Its upper edge is 4 %: a hundred cases of this kind cannot
  see a failure rate below that. It is a hypothesis about the next hundred, not a claim about them.
- **`executed ON` has two locks.** The policy refused every goal proposal (100/100), *and*
  `agent/executor.py` implements only the three verbs the adapter permits, so a denied action would
  write nothing even if it had slipped past. `NUMBERS.md` reports the policy's own verdict as its
  own column, and the promptfoo assertion fails on that column, not only on the write — because a
  gauge that two things hold up cannot say which one is holding.
- **The control arm can fail, and did.** Every case the model was manipulated in was written to the
  records by `redteam/off.py`: 6 of 6. A control that never fires measures nothing either.
- **The cases are the author's own**, written in this repo in one session and not yet read by an
  outside seat. `PLAN.md` §3.1 asks for that review before these numbers are quoted anywhere but
  here.

## Trust boundary

Three things this layer trusts and cannot check. They are the host's job, and the numbers above
do not cover them.

1. **Identity.** A `Principal` says whether it is a human. The layer believes it. Whoever can
   construct `Principal("owner", HUMAN)` can approve their own proposal, and the chain will show a
   clean human decision. In the deployed shape (step 4) principals are minted by the authenticated
   API surface only, and the agent process never holds a `HUMAN` principal; until then, the last
   line of `make hostile` shows exactly this write going through, unscored. The service's own
   bindings (`config`, `store`, `clock`, `fuse`) cannot be swapped by whoever holds it; whoever
   holds the **store** is root (item 3).
2. **The executor.** The layer compares what the executor *reports* with what was approved, as
   canonical JSON. The executor this repo ships reports a before/after diff of the record it was
   asked to change, so a write that touched nothing, or touched more than was approved, is a
   mismatch — but a write outside that record, or an executor that lies consistently about the
   diff, is not seen. An executor that raises after committing is recorded as `executed_unknown`,
   with the attempt row committed before it ran; an effect the layer cannot write down is a mismatch.
3. **The store.** Anything holding the `Store` can write proposals, fuse state and audit rows
   directly, and can re-hash the chain end to end. The chain does not prevent this and does not
   detect a root that re-links everything it touched: `audit_verify()` fails on tampering that
   breaks a link, a hash, the numbering or the head, and a re-hashed, re-numbered, re-headed chain
   passes. **An anchor covers rows up to its own seq and nothing after it**: a head published
   out-of-band catches a truncation at or below it, and every row appended since the last published
   head is unprotected — publish the head after every append or accept that gap.
   `audit_anomalies()` reasons about the order and count of rows per proposal, which an appended
   row cannot repair: an execution without a valid prior approval, a decision after the attempt
   or on a proposal that was never held, a second proposal or decision row for one id, an action
   swapped under an approved id, an attempt without an outcome, a claim without an attempt, a
   replayed execution, a fuse cleared the same day it tripped or by a non-human label, a row stamped
   earlier than the one before it, and fuse state that disagrees with the chain. The `principal`
   string on a row is whatever the writer wrote — at the store it is unauthenticated, which is why
   none of those checks rest on it alone. In this repo the graph holds the `PolicyService` — and so
   its store — which is the shape the hostile self-test attacks. Step 4 puts the service behind an
   API so the agent process holds `propose` and nothing else.

## What it does not claim

Not the first of its kind (CaMeL, APort, OpenAPPA and others enforce boundaries of this shape);
not a guardrail framework; not a benchmark. Numbers about the model appear only when they have been
measured, with the model's name, the date, the interval and the N — one model, on one date, over
one adapter's hundred cases, is what `NUMBERS.md` holds and all it holds. It is not deployed, no
stranger has run it, and the seat that reads the built thing (step 6) has not sat yet.
