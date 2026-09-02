# Status

Design and plan (private, the author's working repo): `venture/DESIGN_2026-09-01_credential_project.md`.

| step | what | state |
|---|---|---|
| 1 | policy layer + tests that can fail + PROVENANCE | **built 2026-09-01**: 26 tests; mutation pass 19/19 checks killed (the first pass found 2 that could not fail — fixed with tests of the real failure mode); hostile self-test 10/10 blocked; awaiting an outside seat's bypass attempts (design Step 1 VERIFY) |
| 2 | the assistant graph + ONE adapter (`invoices-es`) | **built 2026-09-01 with a stub model**: LangGraph retrieve→think→propose→hold(interrupt)→execute; the resume value is untrusted (the store decides); deny-all policy ⇒ zero writes across all 12 invoices; a manipulated model's `update_amount` and an exfiltration via a forbidden channel are denied at propose; approve→resume executes exactly once under node re-execution; the only write path is `agent/executor.py` (grep-tested). NOT yet exercised: a real model (Groq class present, no key), pgvector (local keyword retriever behind the same interface) |
| 3 | red-team with promptfoo, boundary ON/OFF, numbers with intervals | not started |
| 7 | the same policy layer under the author's own outbound pipeline | not started |
| 4 | deploy ($0: Render + Neon + Groq + Langfuse Hobby), self-serve upload | not started |
| 5 | write-up; every number from `make numbers` | not started |
| 5b | text-only "try it" offers, measured | not started |
| 6 | one external refutation of the built thing | not started |

Nothing is pushed or public until the author says so.
