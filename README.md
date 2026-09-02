# atezain

*Atezain* is Basque for goalkeeper.

An assistant over your records (invoices, orders, customers) that summarises, recommends the next
action and drafts the message — and **cannot change anything on its own**. Every write it wants
to make is a proposal; a policy layer outside the model decides whether the proposal is even
allowed to exist; a human approves the ones that matter; the layer executes exactly once and
checks that what happened is what was approved. Then it is attacked, in public, with injections
planted in the very records it reads, and the result is a number.

**Status: step 1 of 6 — the policy layer.** Nothing here is deployed yet. See `STATUS.md`.

## What is built

- `policy/` — the boundary. Permissions as data (`adapters/*/permissions.toml`), an approval queue,
  an append-only audit log (hash chain), a fuse the agent can trip and cannot clear, a daily budget
  that trips it. Pure Python, no dependencies.
- `tests/test_policy.py` — one test per rule.
- `tests/mutate.py` — deletes each rule in turn and demands that the suite goes red. A rule whose
  deletion changes nothing is reported and fails the build.
- `tests/hostile_selftest.py` — an attacker holding the application's own objects tries to get a
  write through without a human. Prints one line per attempt.
- `PROVENANCE.md` — where each rule comes from: the incident, the date, the price.

## Run it

```
python3 -m venv .venv && .venv/bin/pip install -q pytest
make test      # the suite
make mutate    # every check deleted in turn; all must be KILLED
make hostile   # the attacker with the application's objects
```

## What it does not claim

Not the first of its kind (CaMeL, APort, OpenAPPA and others enforce boundaries of this shape);
not a guardrail framework; not a benchmark. Numbers about the model appear only when they have been
measured, with the model's name, the date, the interval and the N.
