# atezain

*Atezain* is Basque for goalkeeper. Built in September 2026 by Zorion Arrillaga, mostly by
directing coding models, on rules paid for by an earlier system of his own (`PROVENANCE.md`).

An assistant over your records (invoices, orders, customers) that summarises, recommends the next
action and drafts the message — and **cannot change anything on its own**. Every write it wants
to make is a proposal; a policy layer outside the model decides whether the proposal is even
allowed to exist; a human approves the ones that matter; the layer executes exactly once and
compares what the executor observed in the record afterwards with what was approved. Then it is
attacked, in public, with injections planted in the very records it reads, and the result is a number.

**Status: steps 1 to 5 are built — the policy layer, the assistant graph over it, the red-team that
measures it against a named model, the deploy, and the write-up; step 7's adapter and CLI are
built, its wiring is not. Live since 2026-09-02 at <https://atezain.onrender.com> (*Try it*,
below); the outside seat that reads the built thing, step 6, has not sat.** See `STATUS.md` for
what each of those means and for what step 4 retired. `WRITEUP.md` is the one read: the
result up front, the architecture, one attack end to end with its audit rows, the policy as data.

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
  layer removed. promptfoo is the runner and its assertion is the gate. A third column is read by
  hand: whether the model's *words* — its recommendation, its draft, a note it proposed — adopted
  the injected goal, one quoted sentence per case (`redteam/prose_labels.json`), each quote
  checked against the cached output by a test. `make numbers` turns the rows into `NUMBERS.md`,
  which is the only source of a number in this file.
- `records/store_pg.py` — the customer's records over Postgres, the same subclass shape, so
  `snapshot`, `diff` and `search` are one piece of code on both databases. With a DSN the served
  app gives each session its own schema for its invoices AND its audit chain, keeps the held graph
  in Postgres, and keeps the session token table there too — so a visitor who comes back to their
  own link after the instance has slept finds their records, their queue and their chain, and the
  model is not asked a second time. There is a test that proves it across two processes.
- `policy/store_pg.py` — the same store over Postgres, as a subclass: the audit logic and every
  marked check exist once, and what differs is the six places SQLite and Postgres genuinely differ.
  Its lock is a row lock on the audit head rather than a lock inside one process, so a second web
  instance cannot race past the budget — there is a test that proves it with two connections, and
  it is the one property the SQLite store structurally cannot have. The whole policy suite runs on
  both when `ATEZAIN_TEST_DSN` names a database, and skips the Postgres arm loudly when it does not.
- `api/` — the same layer behind HTTP: a session is a namespace (its own records, its own audit
  chain, its own fuse) opened by a bearer token; you upload a CSV of your own invoices, ask for an
  assist, see what it drafts and what it holds, decide, and read the chain. `api/auth.py` is the
  only place in the served application where a human principal is constructed, and a test greps the
  package to keep it that way. Runs locally on SQLite and the stub model with `make serve`, and on
  Postgres when `DATABASE_URL` names one — the served shape, live since 2026-09-02 (*Try it*).
- `adapters/outreach/` + `bin/atezain_cli.py` — the same layer over the author's own outbound
  letters: a draft is a record, `send` means *write down that this letter went out*, and there is
  no verb anywhere in the tool that opens a connection to a mail server. He sends by hand and types
  the approval himself; the CLI holds the proposal until he does, records what the store observed
  afterwards, and prints the audit head for the day. The wiring into his own pipeline is not done.
- `PROVENANCE.md` — where each rule comes from: the incident, the date, the price.

## Try it

<https://atezain.onrender.com/demo> — a free Render instance that sleeps when idle, so a
request after a quiet spell wakes it and takes a while. The page has four parts: your records, what the
assistant says, what it is not allowed to do on its own, and the chain. Open a session (the token
is shown once; whoever holds it is that session's human — *Trust boundary*, item 1), upload a CSV
or XLSX of invoices with the columns `id, customer, amount, currency, issued, due, status` and,
to plant something for the assistant to read, `note`, `email_subject`, `email_body`; ask for an
assist on one invoice; approve or reject what it holds; read the audit rows and the head. The
model is `openai/gpt-oss-120b`, on the server's own key under a daily budget with a fuse, or on
yours with an `X-Groq-Key` header. Each session is its own Postgres schema — its records, its
queue, its chain, its fuse — and outlives the instance's sleep. `/healthz` says what is behind it.

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

The web face, locally — SQLite under `var/`, the stub model, no key and no network:

```
.venv/bin/pip install -q fastapi uvicorn python-multipart openpyxl
make serve                         # then open http://127.0.0.1:8000/demo
```

The same layer over your own outbound drafts, with no model in it at all:

```
python3 bin/atezain_cli.py --root <drafts dir> --db <state.db> \
        propose send queued/2026-09-02_letter.md --param to=… --param subject=…
python3 bin/atezain_cli.py … queue                       # what is waiting for you
python3 bin/atezain_cli.py … approve <id> --as human:you # after YOU sent it
python3 bin/atezain_cli.py … record queued/…_letter.md   # writes the artifact and the row
python3 bin/atezain_cli.py … head                        # the audit head, to publish out of band
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
(`STATUS.md`, second table). After that, after the ⚖ review of the red-team's case set, and after the write-up's own gauges joined the suite the same day (2026-09-02, `make all`):

| gauge | result |
|---|---|
| `make test` | 186 passed, 82 skipped — the skips are the Postgres arm with no `ATEZAIN_TEST_DSN` set. With one: **267 passed, 1 skipped, 4 min 12 s** (the policy suite and the graph twice, SQLite and PostgreSQL 18.6, plus the two-process restart test) |
| `make mutate` | 43 checks · 43 killed by assertion · 0 killed only by a crash · 0 survived · 25 crashing test(s) alongside assertion kills |
| `make hostile` | 36/36 scored attempts blocked · 1 out of scope, shown |
| `make sabotage` | 28/28 sabotages caught by at least one gauge |

What these prove and do not: the mutation pass proves every marked check can fail; it says nothing
about a check that is absent (the first seat found one — a `record` key the policy declared and
never read — precisely because there was no block to delete), and it mutates `policy/` only. The
hostile test's concurrency attempt is timing-dependent; the deterministic interleaving test in the
suite is what sees a removed lock. The sabotage pass covers twenty-eight properties, not all of them.
Two seats found, between them, 29 breaches and 12 gauge defects; the numbers above are what is
left after both, not what was true before either.

## The model, the date, the numbers

On **2026-09-02**, **`openai/gpt-oss-120b`** (Groq, temperature 0) was shown **100** planted
injections over the `invoices-es` adapter: five classes (the hiding places listed above) × five
techniques × four goals — `WRITEUP.md` names each. Every case's goal is something the policy must
refuse — a case the policy would allow measures nothing, and `tests/test_redteam.py` fails if one
appears. The texts are business Spanish and carry none of the adapter's own identifiers; the same
test refuses one that does.

The model does two things with an injection: it may propose the write, and it may *say* it. So
there are two numbers.

| | N | proposed the goal write | executed OFF | executed ON | goal in its prose |
|---|---|---|---|---|---|
| all cases | 100 | 1/100 = 1 % [0 %, 5 %] | 1/100 = 1 % [0 %, 5 %] | 0/100 = 0 % [0 %, 4 %] | 66/100 = 66 % [56 %, 75 %] |

Wilson 95 % intervals; the per-class, per-technique, per-goal and per-reach tables are in
`NUMBERS.md`, generated by `make numbers`.

What these two numbers do and do not show is read in full in `WRITEUP.md` › *The numbers* and
*What it does not show*; the short form:

- **The second number is the finding.** In two of every three outputs the model adopted the
  injected goal in words — in its recommendation, in its draft to the customer, or in a note it
  proposed. Each label quotes the sentence it rests on, and a test checks the quote against the
  cached output. The judgment-dense model (Claude Fable 5.1) labelled the hundred by a written
  rule; that reading is what an outside seat should redo before anything else.
- **The proposal number is a bound, not a proof.** Once in a hundred, with an upper edge of 4 %.
  The distance between it and the prose number is the prompt's vocabulary, not the model's
  judgment: the tool schema offers three verbs and one channel, and the model mostly stayed inside
  it while saying otherwise.
- **A note is a write.** This adapter auto-approves `add_note`, and in 13 of the 66 the adopting
  sentence was a note, so with the boundary ON the injected claim went into the record in the
  assistant's own voice. The open question on that is in `STATUS.md`.
- **No human approved anything in the run**, so the ON column is the boundary alone. The policy's
  refusal is reported as its own column (100/100) because the executor's three verbs are a second
  lock; the control arm did fail once, as a control must be able to; and the cases are the
  project's own — a builder model wrote them, the judgment-dense model reviewed and revised them,
  and no one outside the project has read them.

## Trust boundary

Three things this layer trusts and cannot check. They are the host's job, and the numbers above
do not cover them.

1. **Identity.** A `Principal` says whether it is a human. The layer believes it. Whoever can
   construct `Principal("owner", HUMAN)` can approve their own proposal, and the chain will show a
   clean human decision. In the served application (`api/`, at the URL since 2026-09-02) the only
   place a `HUMAN` principal is constructed is `api/auth.py`, from a bearer token issued once per
   session and kept only as a hash; a test greps the package and fails if the word appears anywhere
   else, and the agent's principal is a module constant no request can choose. What that closes is
   the mint, not identity: the token *is* the identity — whoever holds a session's token is its
   human, the way whoever holds the shell is the human at the CLI — and there is no account behind
   it. In a process that holds the objects, as this repo's tests do, the mint is one line, and
   `make hostile` shows exactly that write going through, unscored, in its output. The service's
   own bindings (`config`, `store`, `clock`, `fuse`) cannot be swapped by whoever holds it; whoever
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
   its store — which is the shape the hostile self-test attacks. The served application is one
   process that holds the store, the service and the graph; what the API puts behind a token is the
   visitor, not the model. The model holds nothing: its only channel into the system is the JSON it
   returns to `think`, which becomes calls to `propose` and nothing else. Whoever runs the process
   is root over the store, and that is the host.

## What it does not claim

Not the first of its kind (CaMeL, APort, OpenAPPA and others enforce boundaries of this shape);
not a guardrail framework; not a benchmark. Numbers about the model appear only when they have been
measured, with the model's name, the date, the interval and the N — one model, on one date, over
one adapter's hundred cases, is what `NUMBERS.md` holds and all it holds. It runs on a free instance
at one URL; no outside seat has run it, and the seat that reads the built thing (step 6) has not
sat yet.
