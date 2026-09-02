# Status

Design and plan (private, the author's working repo): `venture/DESIGN_2026-09-01_credential_project.md`.

| step | what | state |
|---|---|---|
| 1 | policy layer + tests that can fail + PROVENANCE | **built 2026-09-01, then REFUTED by an outside seat the same night (17 of 19 new bypass attempts got through).** ⚠ The gauges as printed are inflated: one of the 19 "checks" is a bare assignment whose kill is a NameError (honest count 18); the hostile self-test has one attempt hard-wired to pass and one that never asserts its named property. Decisive defects, all locally fixable: the two enforcement checks read the caller's object instead of the sanitised copy; a rejected proposal forged to approved is not an orphan; the EXECUTED audit row omits the action; a tail row can be deleted and the chain still verifies; a write that throws after committing leaves no audit row; the budget is defeated by concurrency and refunded by rejections; `record` in the TOML is never enforced; identity (who is a human) is assumed and nowhere declared. **First task next session: fix, re-gauge, publish honest numbers, add a trust-boundary section.** Seat report: the author's private repo, `venture/steelman/2026-09-01/REPORT_refute_atezain_step1.md` |
| 2 | the assistant graph + ONE adapter (`invoices-es`) | **built 2026-09-01 with a stub model**: LangGraph retrieve→think→propose→hold(interrupt)→execute; the resume value is untrusted (the store decides); deny-all policy ⇒ zero writes across all 12 invoices; a manipulated model's `update_amount` and an exfiltration via a forbidden channel are denied at propose; approve→resume executes exactly once under node re-execution; the only write path is `agent/executor.py` (grep-tested). NOT yet exercised: a real model (Groq class present, no key), pgvector (local keyword retriever behind the same interface) |
| 3 | red-team with promptfoo, boundary ON/OFF, numbers with intervals | not started |
| 7 | the same policy layer under the author's own outbound pipeline | not started |
| 4 | deploy ($0: Render + Neon + Groq + Langfuse Hobby), self-serve upload | not started |
| 5 | write-up; every number from `make numbers` | not started |
| 5b | text-only "try it" offers, measured | not started |
| 6 | one external refutation of the built thing | not started |

Nothing is pushed or public until the author says so.
