# CLAUDE.md — rules for any model working in this repo

This is `atezain`: a policy layer between an LLM agent and a customer's records, an assistant graph
over it, and a red-team that measures it. `README.md` says what it is; `STATUS.md` says what exists;
`PLAN.md` says what to build next and exactly how. Read those three before touching anything.

## The rules (they bind regardless of which model you are)

1. **Never push, publish, deploy or create a public repo.** The owner says so in writing, that day, or it
   does not happen. There is no remote configured on purpose.
2. **Never spend.** The stack is $0 (Render free · Neon free · Groq free · Langfuse Hobby · promptfoo).
   Anything priced is written down with its amount for the owner's YES. Never Gemini (EEA terms).
3. **Never invent a number.** A number in any document comes from `make numbers` (`NUMBERS.md`) or
   from a gauge's output copied that minute. A number about a model carries the model id, the date,
   N and a Wilson interval. "0/N" is a hypothesis, never a result.
4. **`make all` is the definition of done.** test · mutate · hostile · sabotage, all green, and the
   counts in `STATUS.md` copied from the output. A "done" with a red gauge does not exist here.
5. **The only write path to records is `agent/executor.py`**, called only from `PolicyService.execute`.
   The control arm `redteam/off.py` is the single sanctioned exception. A grep test enforces it.
6. **Every `# CHECK:` block in `policy/` has a test that kills it by assertion.** Adding a check means
   adding its test; `make mutate` reports CRASH or SURVIVED otherwise and the build is red.
7. **Do not decide what `PLAN.md` left open.** Write the question under `STATUS.md › Open questions`
   and continue with everything that does not depend on it. Items marked ⚖ are for the judgment-dense
   model; items marked ✋ are the owner's hand.
8. **Do not widen scope.** One step per session; the step's files only; a needed change elsewhere is a
   one-line bug with a test or an open question.
9. **The trust boundary is declared, not hidden.** Identity, the executor's honesty, and the store are
   the host's (README §Trust boundary). Never write a sentence that implies the layer covers them.
10. **Barred words:** *first*, *nobody has*, *novel*, and any comparative against named projects.
    APort, CaMeL and OpenAPPA exist and are cited as such.
11. **Refutation seats are external.** A seat's attack script is never copied into this repo; its
    attack classes are re-authored in `tests/hostile_selftest.py` in this repo's words.
12. **Handoff.** End every session with: `make all` green, `STATUS.md` updated, one commit naming the step
    and the counts, nothing pushed, and anything skipped written as SKIPPED with the reason.

## Commands

```
python3 -m venv .venv && .venv/bin/pip install -q pytest langgraph langgraph-checkpoint-sqlite
make test       # 61 tests (09-02)
make mutate     # every # CHECK: deleted in turn; must be KILLED by assertion (~45 s)
make hostile    # the attacker with the application's objects; scored attempts only
make sabotage   # the gauges broken on purpose must go red
make all
```

## Where the private record lives

The design, the seat reports and the session records are in the owner's private repo
(`~/Desktop/FTMO/venture/`, not readable from a public clone). This repo must stand on its own:
anything a reader needs is in `README.md`, `STATUS.md`, `PLAN.md`, `PROVENANCE.md`.
