# Status

Design (private, the author's working repo): `venture/DESIGN_2026-09-01_credential_project.md`. **The executable plan for every remaining step is `PLAN.md`** (2026-09-02): files, interfaces, acceptance commands, KILL, and who does it — BUILDER (a cheaper model) · ⚖ JUDGE (the judgment-dense model) · ✋ OWNER. Rules for any model here: `CLAUDE.md`.

| step | what | state |
|---|---|---|
| 1 | policy layer + tests that can fail + PROVENANCE | **built 2026-09-01; REFUTED the same night by an outside seat (17 of 19 new bypass attempts got through); REPAIRED 2026-09-02** — see the table below. Gauges now: 61 tests · 34 checks killed by assertion (0 crashed, 0 survived) · 26/26 scored hostile attempts blocked + 1 out of scope shown · 6/6 sabotages caught. Trust boundary declared in `README.md`. |
| 2 | the assistant graph + ONE adapter (`invoices-es`) | **built 2026-09-01 with a stub model**: LangGraph retrieve→think→propose→hold(interrupt)→execute; the resume value is untrusted (the store decides); deny-all policy ⇒ zero writes across all 12 invoices; a manipulated model's `update_amount` and an exfiltration via a forbidden channel are denied at propose; approve→resume executes exactly once under node re-execution; the only write path is `agent/executor.py` (grep-tested). NOT yet exercised: a real model (Groq class present, no key), pgvector (local keyword retriever behind the same interface) |
| 3 | red-team with promptfoo, boundary ON/OFF, numbers with intervals | not started — `PLAN.md` §3. BUILDER can build all of it against the stub now; the real run needs ✋ `GROQ_API_KEY`; ⚖ the case texts |
| 7 | the same policy layer under the author's own outbound pipeline | not started — `PLAN.md` §5. BUILDER: the `outreach` adapter + CLI; ⚖ the wiring under `bin/venture` |
| 4 | deploy ($0: Render + Neon + Groq + Langfuse Hobby), self-serve upload | not started — `PLAN.md` §4. BUILDER: `store_pg.py`, the API routes, ops, retrieval with its measurement; ⚖ `api/auth.py` (identity closure); ✋ four free accounts |
| 5 | write-up; every number from `make numbers` | not started — `PLAN.md` §6. ⚖ prose; BUILDER tooling. Blocked until `NUMBERS.md` exists from a named model |
| 5b | text-only "try it" offers, measured | not started — `PLAN.md` §7. ✋ his voice |
| 6 | one external refutation of the built thing | not started — `PLAN.md` §8. A second seat on the REPAIRED step 1 was launched 2026-09-02 02:50 ET; verdict pending |

## What the seat found at step 1, and what changed (2026-09-02)

| # | the seat's finding | the fix | now exercised by |
|---|---|---|---|
| D1 | two enforcement checks read the caller's `params` object while the executor got a copy; a Mapping whose `keys()`, `__iter__` or `__contains__` disagree smuggled fields and values through (3 breaches) | `propose` makes ONE copy through canonical JSON and every check reads that copy | `test_a_params_object_whose_iteration_lies…`, hostile b1 b2 |
| D2 | the hostile gauge had one attempt that returned "blocked" unconditionally and one that never asserted its named property; sabotaging the detector still printed 10/10 | both attempts assert; scored attempts only are counted; `tests/sabotage.py` breaks six properties and demands red | `make hostile`, `make sabotage` |
| D3 | "the chain cannot hide it" was false four ways: tail truncation, a rejection forged to approval, an action swapped under an approved id, a fuse cleared through the store — all with `audit_verify() == True` | seq in the hash + an `audit_head` row written in the same transaction + an out-of-band anchor; orphans require a human's `approve == True`; outcome rows carry action and record_id; every fuse change writes its row in the same transaction; `audit_anomalies()` names seven shapes | tests under "audit anomalies", hostile b3 b4 b5 b8 b9 |
| D4 | an executor that wrote and then raised left no audit row at all; a non-JSON effect did the same | `EXECUTION_ATTEMPTED` committed before the executor runs; a raise ends in `EXECUTION_UNKNOWN` / `executed_unknown`; effects are recorded by repr when JSON cannot | `test_an_executor_that_writes_and_then_raises…`, `…dies_inside_the_executor…`, hostile b6 b7 |
| D5 | the budget was raced past (6 of 6 approved against 2) and refunded by every human rejection (40 in front of the human against 3) | checks, count and insert in one `BEGIN IMMEDIATE` transaction under a process lock; REJECTED counts (stated in the TOML) | `…cannot_race_past_the_budget`, `…count_and_the_write_are_one_transaction`, hostile b14 b15 |
| D6 | `record = "invoice"` was declared in the TOML and never read; `record_id` was unvalidated (`customer:9999;DROP TABLE…` executed) | a `[records]` table maps each record type to an id pattern; `record_scope` check | `test_record_id_outside…`, hostile b12 |
| D7 | identity is the whole guarantee, is assumed, and was nowhere declared | README §Trust boundary: identity, the executor, the store — with what each costs | the last line of `make hostile`, shown unscored |
| M1 | non-dict params raised out of `propose` unaudited | `params_shape` check inside the fail-closed path | `test_non_mapping_params…`, hostile b16 |
| M2 | the config was a mutable dataclass; a denied action was re-specced in three lines | frozen dataclass + read-only mappings; the policy fingerprint is in every PROPOSAL row | `test_the_configuration_cannot_be_edited_in_place`, hostile b13 |
| M3 | re-validation trusted `applied.__eq__` | canonical JSON comparison | `…not_what_the_object_claims`, hostile b11 |
| M4/M5 | two PROVENANCE citations at the wrong line; one mis-attributed | corrected, and the correction is noted in the table | — |
| C1 | one of 19 "checks" was a bare assignment; its kill was a NameError | marker removed; `mutate.py` now classifies CRASH separately and fails on it | `make mutate` |
| S11 | the agent chose the trip time and locked the human out for a hundred years | the fuse owns its clock; no caller passes a time | `test_the_fuse_keeps_its_own_clock`, hostile b10 |

## Open questions (a builder writes here instead of deciding; ⚖ or ✋ answers)

_(none yet)_

Nothing is pushed or public until the author says so.
