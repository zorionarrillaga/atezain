# Status

Design (private, the author's working repo): `venture/DESIGN_2026-09-01_credential_project.md`. **The executable plan for every remaining step is `PLAN.md`** (2026-09-02): files, interfaces, acceptance commands, KILL, and who does it — BUILDER (a cheaper model) · ⚖ JUDGE (the judgment-dense model) · ✋ OWNER. Rules for any model here: `CLAUDE.md`.

| step | what | state |
|---|---|---|
| 1 | policy layer + tests that can fail + PROVENANCE | **built 2026-09-01; REFUTED the same night by an outside seat (17 of 19 new bypass attempts through); REPAIRED 2026-09-02; REFUTED AGAIN by a second seat on the repair (12 of 27 new attempts through, 5 of 12 fix rows not holding as stated); REPAIRED AGAIN 2026-09-02** — both tables below. Gauges now: 79 tests · 43 checks · 43 killed by assertion · 0 killed only by a crash · 0 survived · 23 crashing test(s) alongside assertion kills · 36/36 scored hostile attempts blocked + 1 out of scope shown · 14/14 sabotages caught. Trust boundary declared in `README.md`, restated to what the code supports. |
| 2 | the assistant graph + ONE adapter (`invoices-es`) | **built 2026-09-01 with a stub model**: LangGraph retrieve→think→propose→hold(interrupt)→execute; the resume value is untrusted (the store decides); deny-all policy ⇒ zero writes across all 12 invoices; a manipulated model's `update_amount` and an exfiltration via a forbidden channel are denied at propose; approve→resume executes exactly once under node re-execution; the only write path is `agent/executor.py` (grep-tested). NOT yet exercised: a real model (Groq class present, no key), pgvector (local keyword retriever behind the same interface) |
| 3 | red-team with promptfoo, boundary ON/OFF, numbers with intervals | not started — `PLAN.md` §3. BUILDER can build all of it against the stub now; the real run needs ✋ `GROQ_API_KEY`; ⚖ the case texts |
| 7 | the same policy layer under the author's own outbound pipeline | not started — `PLAN.md` §5. BUILDER: the `outreach` adapter + CLI; ⚖ the wiring under `bin/venture` |
| 4 | deploy ($0: Render + Neon + Groq + Langfuse Hobby), self-serve upload | not started — `PLAN.md` §4. BUILDER: `store_pg.py`, the API routes, ops, retrieval with its measurement; ⚖ `api/auth.py` (identity closure); ✋ four free accounts |
| 5 | write-up; every number from `make numbers` | not started — `PLAN.md` §6. ⚖ prose; BUILDER tooling. Blocked until `NUMBERS.md` exists from a named model |
| 5b | text-only "try it" offers, measured | not started — `PLAN.md` §7. ✋ his voice |
| 6 | one external refutation of the built thing | not started — `PLAN.md` §8. Two seats have run on step 1 (09-01 and 09-02); the step-6 seat is on the DEPLOYED thing |

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

## What the second seat found on the repair, and what changed (2026-09-02)

| # | the seat's finding | the fix | now exercised by |
|---|---|---|---|
| D1 | the shipped executor returned its own input, so `applied == approved` was a tautology; a write to a non-existent record read EXECUTED; an executor writing twice or writing an extra note passed every gauge | `agent/executor.py` reports a before/after diff of the record (`Records.snapshot`/`diff`); nothing touched ⇒ `{}` ⇒ mismatch; more touched ⇒ the extra is in the diff ⇒ mismatch | `test_agent.py` executor tests; sabotage rows "reports its input", "writes twice", "extra note"; hostile b23 |
| D2 | four anomaly shapes fell to one legally appended row or one label: a `human:` DECISION appended after a forged execution; a second PROPOSAL row agreeing with a swapped action; a fuse clear labelled `human:owner` | `audit_anomalies()` reasons about ORDER and COUNT per proposal: exactly one PROPOSAL row, at most one DECISION, a DECISION only after a `held` PROPOSAL and before the attempt, one attempt and one outcome; a FUSE_CLEARED on the day of its trip is an anomaly whatever it is labelled; the store says plainly the principal string is unauthenticated | the "appended … cannot launder" tests; hostile b19 b20 b21 b26 |
| D3 | the anchor sentences pointed the wrong way: an anchor covers rows up to its own seq, not after | both sentences rewritten (README §Trust boundary 3, `store.py` docstring); the test anchors on an OLDER head | `test_a_truncated_and_reheaded_chain…`, `test_a_clean_run…` |
| D4 | `PolicyConfig` was frozen but `svc.config`/`clock`/`fuse`/`store` were rebindable in one line; the suite itself did it | `__slots__` + read-only properties + a `__setattr__` that refuses; the suite builds a new service instead | `test_the_services_bindings_cannot_be_swapped`; hostile b13 b17 b18; sabotage "bindings can be swapped" |
| D5 | `Store.fuse_set` took a caller's `tripped_at` and arbitrary columns — round-1's S11 reproduced at the store face | `fuse_set(kind, principal, detail, ts, tripped, reason)`: `tripped_at` IS `ts`; a trip stamped in the future binds nobody (clears at once) and shows as `audit_time_not_monotonic` | `test_a_trip_stamped_in_the_future…`; hostile b22 |
| D6 | `record_scope` was a shape, and `\d` matched non-ASCII digits | renamed `record_shape` with `re.ASCII`; the README says shape, not ownership; ownership arrives with the authenticated host (step 4) | `test_record_shape_means_ascii_digits`; hostile b12; sabotage "any digit" |
| D7 | four hostile attempts passed for the wrong reason: b7 scored an exception after a real write as a block; a3 was refused by `field_smuggling`, not `deny`; a6 by the status transition, not the claim; b13 by a TypeError while its property was false | the wrapper scores an exception after a write as BREACH; a3 uses a denied action that declares a field; a6 resets the status through the store and counts four claim refusals; b13 also tries rebinding | `make hostile` |
| D8 | six one-line sabotages uncaught | eight added (re-authored), two of them the executor writing more than approved; `sabotage.py` runs the whole suite and REFUSES to run over a red baseline (its first 14/14 was printed over a suite with one red test — the seats' own class, caught by the end-to-end run) | `make sabotage` 14/14 |
| M1/M2 | "0 crashed" hid tests that crashed alongside assertion kills; `model.py` never mutated; both passes ran a subset of the suite | crash counts printed per row and in the total; `model.py` in the pass (`Principal.is_human` is a check); both passes run `tests/` | `make mutate` |
| M3/T25 | a 20 000-deep param left ZERO audit rows (`asdict` recursed after the checks) | `Proposal.to_json` is shallow; params values must be scalars (`params_scalar`); params ≤ 64 KiB and evidence ≤ 2000 chars (`params_bounded`) | `test_a_nested_param_value…`, `test_params_and_evidence_are_bounded`; hostile b24 |
| M6 | the model's justification was in no audit row | `evidence` in the PROPOSAL row | `test_the_models_justification_is_in_the_audit_row` |
| M7 | the write-path grep caught `_apply_` only | it also catches `_raw`, `plant_email`, `load_seed`, `.conn` | `test_no_write_path_bypasses_policy` |
| M8/M9 | the anchor assertion could not fail; an unrenderable effect raised out of `execute` leaving `approved` | older anchor + wrong anchor asserted; `record_effect` inside the try ⇒ mismatch | `test_an_effect_that_cannot_be_written_down…`; hostile b25 |
| T4, T19–T22, M4 | ownership of records; root re-hashing, inserting, replaying; nested transactions are not savepoints | **not fixed, restated**: ownership is the host's (step 4); a root that re-links the chain is out of scope by design and the README now says so; the replay shows as `execution_row_count`; the savepoint gap is recorded here as open | README §Trust boundary 3 |

## Open questions (a builder writes here instead of deciding; ⚖ or ✋ answers)

- `Store.transaction()` nesting is not a savepoint: an inner failure that is caught leaves the inner writes in the outer transaction (seat 2, T24). No route from the agent's surface was found. Postgres store (step 4.1) should use real savepoints; decide then whether SQLite gets them too.

_(none yet)_

Nothing is pushed or public until the author says so.
