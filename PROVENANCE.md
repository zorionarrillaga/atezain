# Where each rule comes from

Every rule in the policy layer exists because something specific went wrong, on a date, with a
price. The incidents are from an autonomous trading system the author ran against a real
proprietary-firm evaluation account between May and August 2026. The files cited are in that
system's private repository; the sentences quoted are verbatim from its records.

| rule in `policy/` | the incident | date | price | record |
|---|---|---|---|---|
| **Few rules may block; everything else advises.** The permission table is small and closed. | Sixteen enforcement hooks accumulated on the trade path and strangled trades that should have fired: *"−$846 from system friction, not a market call"*. The rebuild cut the hook surface from sixteen to three and wrote a constitution of at most eight rules that may block. | 2026-05-29 | −$846 | `architecture/SESS-2026-05-29_two_regime_rebuild.md` (l.22, l.49); `CONSTITUTION.md` |
| **A limit the operator can reason past is not a limit.** The daily budget trips a fuse outside the agent; the agent cannot clear it. | A −$2,500 dynamic loss limit was in force on the day the evaluation account's $3,000 daily cap was breached by $182. The limit existed and was soft; the account was terminated. | 2026-05-18 | a $100,000 evaluation account | `sessions/2026-05-18/log.md` (l.10); `architecture/ARCH-v0.md` (l.219); `decisions/D015.md` |
| **Re-validate after the action, never trust the intent.** `execute` compares what the executor reports it applied with what was approved and flags a mismatch loudly. | A trade gated at $588 of risk filled at $838 with no warning — slippage silently pushed it over its tier. The rule was written the same day (constitution C7). **The post-submit check itself was never built in that system** — its own record says so (*"No `oas_fire` post-submit check exists"*). This layer builds it. | 2026-05-29 | $250 of unapproved risk | `CONSTITUTION.md` (C2 l.21, C7 l.26) |
| **Cannot-see = block.** If the policy state is unreachable, a proposal is denied, never allowed. | The lockdown guard was inverted from "block on a bad signal" to "block when the signal cannot be read", after a period when the guard's blindness would have meant permission. | 2026-07 (D037) | — | `bin/lockdown/watcher.sh` (l.456 *"fail-safe inversion (can't-see = block)"*) |
| **A trip cannot be cleared the same day, and never by the agent.** `Fuse.clear` refuses a non-human and refuses the day of the trip. | The lockdown daemon: *"a REAL trip cannot be cleared the same ET day"*, *"un-undoable in-state … no flag, no force path"*. It exists to stop the operator's own revenge, not an attacker's. | 2026-07 (D037) | — | `bin/lockdown/lockdown.sh` (l.7; l.15–16, two sentences from one comment block) |
| **A check must be able to fail.** `tests/mutate.py` deletes every check and demands a red suite. | A daily-kill check read a key the live state never carried, so it evaluated `0 > −2000` on every trade and could not fail — known for weeks before it was replaced. | 2026-07-15 | $0 (superseded before it cost) | `CONSTITUTION.md` C1 note; `sessions/2026-07-15/REVIEW.md` (l.15) |
| **Policy is data, not prompt.** The agent never sees the permission table as an instruction it could be argued out of. | A design note from the same project, 2026-08-24: *"a rule that can be argued with is not a rule"* — written about a supervisor that could be persuaded. The sentence was written by the AI assistant the author works with, in that note; it is not the author's own line and not a quotation of anyone else. | 2026-08-24 | — | `venture/demand/IDEA_supervised_blocker.md` (l.20–21) |

Citations were checked against the source repository on 2026-09-01 by an outside seat: five of seven
were exact; two carried the right text at the wrong line, and one of those mis-attributed the sentence.
Both are corrected above.

What this table does not claim: that these incidents were unique, that the rules are novel (they
are not — CaMeL, APort, OpenAPPA and others enforce similar boundaries), or that the trading system
made money (it did not). It claims only that each rule here was paid for before it was written.
