# atezain

*Atezain* is Basque for goalkeeper.

An assistant over your records (invoices, orders, customers) that summarises, recommends the next
action and drafts the message — and **cannot change anything on its own**. Every write it wants
to make is a proposal; a policy layer outside the model decides whether the proposal is even
allowed to exist; a human approves the ones that matter; the layer executes exactly once and
checks that what happened is what was approved. Then it is attacked, in public, with injections
planted in the very records it reads, and the result is a number.

**Status: steps 1 and 2 of 6 — the policy layer, and the assistant graph over it with a stub
model.** Nothing here is deployed yet. See `STATUS.md`.

## What is built

- `policy/` — the boundary. Permissions as data (`adapters/*/permissions.toml`: actions, the fields
  each may write, allowed values, the record type and id shape each may touch), an approval queue,
  an append-only audit log (a hash chain with a published head), a fuse the agent can trip and
  cannot clear, a daily budget that trips it. Pure Python, no dependencies.
- `agent/` — the assistant as a LangGraph graph: retrieve → think → propose → hold → execute. The
  model talks only to `think`; the only write path is `agent/executor.py`, called only from inside
  the policy's `execute`; the value a client passes on resume is untrusted.
- `tests/test_policy.py` — one test per rule, and one per shape of root write the audit can see.
- `tests/mutate.py` — deletes each rule in turn and demands that the suite goes red **by
  assertion**. A rule whose deletion changes nothing, or only crashes the suite, fails the build.
- `tests/hostile_selftest.py` — an attacker holding the application's own objects tries to get a
  write through without a human, or to hide one that happened. Scored attempts only.
- `tests/sabotage.py` — breaks a property on purpose and demands that the numbers above fall.
- `PROVENANCE.md` — where each rule comes from: the incident, the date, the price.

## Run it

```
python3 -m venv .venv && .venv/bin/pip install -q pytest langgraph
make test       # the suite
make mutate     # every check deleted in turn; all must be KILLED by an assertion
make hostile    # the attacker with the application's objects
make sabotage   # the gauges themselves, broken on purpose, must go red
make all
```

## Numbers, and where they come from

On 2026-09-01 the first version of this layer shipped with "26 tests · 19/19 checks killed · 10/10
attacks blocked". The same night an outside refutation seat, working in a copy with no contact with
the author, got **17 of 19 new attempts through it** and showed that two of the three numbers were
inflated by construction: one "check" was a bare assignment whose deletion crashed the suite, and
one hostile attempt returned "blocked" unconditionally — sabotaging the detector still printed 10/10.
The seat's report is in the author's private repo; the defects and their fixes are listed in
`STATUS.md`. Every attempt class it used is re-authored here as `b1`–`b16` in the hostile self-test.

After the fix (2026-09-02, `make all`):

| gauge | result |
|---|---|
| `make test` | 61 passed |
| `make mutate` | 34 checks · 34 killed by assertion · 0 crashed · 0 survived |
| `make hostile` | 26/26 scored attempts blocked · 1 out of scope, shown |
| `make sabotage` | 6/6 sabotages caught by at least one gauge |

What these prove and do not: the mutation pass proves every check **present** can fail; it says
nothing about a check that is absent (the seat found one — a `record` key the policy declared and
never read — precisely because there was no block to delete). The hostile test's concurrency
attempt is timing-dependent and did not catch a removed lock on its own; the deterministic
interleaving test in the suite did. The sabotage pass covers six properties, not all of them.

## Trust boundary

Three things this layer trusts and cannot check. They are the host's job, and the numbers above
do not cover them.

1. **Identity.** A `Principal` says whether it is a human. The layer believes it. Whoever can
   construct `Principal("owner", HUMAN)` can approve their own proposal, and the chain will show a
   clean human decision. In the deployed shape (step 4) principals are minted by the authenticated
   API surface only, and the agent process never holds a `HUMAN` principal; until then, the last
   line of `make hostile` shows exactly this write going through, unscored.
2. **The executor.** The layer knows what the executor *reports* it applied, compared as canonical
   JSON so an object with a lying `__eq__` does not pass. The write itself is unobserved. An
   executor that lies consistently is not caught; one that raises after committing is recorded as
   `executed_unknown` with the attempt row committed before it ran.
3. **The store.** Anything holding the `Store` can write proposals, fuse state and audit rows
   directly. The chain does not prevent this; it makes it show: `audit_verify()` fails on an edited,
   relinked, or truncated chain, and `audit_anomalies()` lists an execution without a human's
   approval, an action swapped under an approved id, a rejection forged into an approval, an
   attempt with no outcome, a fuse cleared by a non-human, and fuse state that disagrees with the
   chain. One case is caught only with help: a root that truncates the tail **and** rewrites the
   head passes a bare `audit_verify()`; it fails against a head published earlier out-of-band
   (`audit_head()`), which is why that method exists. In this repo the graph holds the
   `PolicyService` — and so its store — which is the shape the hostile self-test attacks. Step 4
   puts the service behind an API so the agent process holds `propose` and nothing else.

## What it does not claim

Not the first of its kind (CaMeL, APort, OpenAPPA and others enforce boundaries of this shape);
not a guardrail framework; not a benchmark. Numbers about the model appear only when they have been
measured, with the model's name, the date, the interval and the N — none have been yet (step 3).
