# Supervised collections release

## Current decision — 2026-09-15

[PROJECT_PURPOSE.md](PROJECT_PURPOSE.md) supersedes the development direction below.
Product expansion is paused; this project currently serves as engineering portfolio evidence.
The following criteria remain conditional requirements for a future customer release, not
an instruction to build further or a claim of customer demand.

## Immediate milestone: solo evaluation

On 2026-09-05 the owner selected [SOLO_EVALUATION.md](SOLO_EVALUATION.md) as the immediate milestone:
an assistant-evaluated demo with fictional invoices, existing tools and no required external team.
The owner subsequently delegated the walkthrough because they cannot perform it. Human review is
deferred; assistant actions are not recorded as owner acceptance. The enterprise requirements below
are retained as the later customer-release target; their external gates are deferred, not passed.

## Future enterprise release

The owner’s instruction on 2026-09-05 replaces the old demo-only build scope. The starting evidence is the completed enterprise review at commit `584668e`. This release serves a B2B services finance team that reviews overdue invoices each working day. Xero is the provisional accounting source, and tenant-specific OpenID Connect is the workforce identity boundary, with Microsoft Entra ID as the intended external validation target. These choices were asked early; customer confirmation remains pending.

The buyer is a finance lead accountable for receivables and the evidence behind follow-up decisions. The operator maintains identity, source synchronization, retention and recovery. Reviewers own a daily list, inspect the outstanding balance and customer correspondence, prepare or correct a reminder, record approval, and track disputes and promises. Sending remains a deliberate manual step outside Atezain. No automated delivery, payment collection or accounting writeback is included in this release.

## Acceptance targets (requirements, not measurements)

| Gate | Release requirement | Evidence needed |
|---|---|---|
| Identity | Signed tokens must match issuer, tenant where configured, client, nonce, lifetime and configured MFA assurance. No role from email, display name or model input. Workspace membership checked on every operation; local deprovisioning immediate; browser authorization expires within five minutes. | Negative protocol tests, restart tests, and a live customer IdP test covering MFA, disabled user, wrong tenant and signing-key rollover |
| Accounting | Read-only invoice/contact OAuth scopes; selected tenant verified through connections; stable source IDs; exact cent values and partial balances; bounded pagination and incremental imports; no cursor advance on partial failure; conflicts visible; refuse drafting/approval after an hour without successful sync and reread the selected invoice before approval. | Contract fixtures and real sandbox create/payment/contact-change/revoke/reconnect reconciliation |
| Daily work | A reviewer can find due work, assign an owner, track a dispute or payment promise, defer work, amend an exact held reminder and recover the same work after restart. Source changes prevent approval of an obsolete reminder. | API regression tests and keyboard/narrow-screen browser exercises |
| Capacity | Candidate workload: 500 invoice records per workspace, five reviewers, 50 assisted cases per working day. Local workload must finish without missing writes or duplicate effects; non-provider request p95 below two seconds in a declared test environment. | Machine-readable workload report; repeat on selected hosting before committing throughput or latency |
| Recovery | Complete isolated restore must preserve records, access/revocation, cases, approvals, checkpoints and independently anchored audit evidence. Provisional targets: recovery within four hours, data loss within 24 hours. | Automated local restoration drill plus an independently stored PostgreSQL backup restored on selected hosting |
| Quality | Existing test, mutation, hostile and sabotage gates all green. Revised served retrieval and all-approval configuration must get a separately named live-model evaluation. Historical model results stay historical. | GAUGES.md plus separately recorded current-model/customer UAT evidence |
| Procurement | Customer approves hosting/model data path, retention, support, incident ownership and service scope. Security assessment of deployed configuration has no unresolved release-blocking finding. | Customer/operator approvals and external assessment; no certification or SLA inferred from local tests |

The workload, timing, recovery and deprovisioning values above are proposed acceptance thresholds. They are not observed customer outcomes or commercial promises. Prices, hosting purchases, deployments, external messages and legal commitments require the owner’s separate authorization. No production customer data is required for local engineering.

## Protocol references

Implementation follows [OpenID Connect Core token validation](https://openid.net/specs/openid-connect-core-1_0.html#IDTokenValidation), [Microsoft’s ID-token claim reference](https://learn.microsoft.com/en-us/entra/identity-platform/id-token-claims-reference), and [PyJWT’s verification API](https://pyjwt.readthedocs.io/en/latest/api.html). The operator must map the configured signed assurance claim to the customer’s actual MFA policy; matching a claim alone does not prove that policy is correctly administered.

The connector uses Xero’s [invoice API](https://developer.xero.com/documentation/api/accounting/invoices), [granular scopes](https://developer.xero.com/documentation/guides/oauth2/scopes/), [incremental retrieval](https://developer.xero.com/documentation/best-practices/api-call-efficiencies/if-modified-since/) and [rotating refresh tokens](https://developer.xero.com/documentation/guides/oauth2/token-types). Before live use the owner must confirm the application’s account tier and [developer terms](https://developer.xero.com/faq), including restrictions on training models with API data. No paid connection or account has been created.

## Evidence index and customer acceptance

The executable closure checklist is [ops/RELEASE_ACCEPTANCE.md](ops/RELEASE_ACCEPTANCE.md).
It assigns responsible roles, defines the remaining exercises and evidence, and records the release
decision separately from local engineering results. Customer acceptance remains OPEN until the
applicable exercises and named sign-offs are complete; a SKIPPED exercise does not pass a gate.

| Area | Reproducible engineering evidence | Still required externally |
|---|---|---|
| Identity | `tests/test_identity.py`; workforce membership/session/revocation across processes in `tests/test_api_pg.py` | Actual tenant claim mapping, MFA enforcement, disabled-user behavior and provider key rollover |
| Accounting | `tests/test_accounting.py`; selected-tenant/scope refusal, encrypted refresh rotation, both-store source transactions, paid-invoice and changed-plan refusal | Xero app registration and sandbox reconciliation with approved test data; customer/provider terms |
| Daily work | `tests/test_collections.py`; `ops/browser-verification.json`; independently verified downloaded local export | Finance-user acceptance, measured time/correction burden, formal accessibility review if required |
| Capacity | `ops/workload-result.json`, generated by `python -m ops.workload` | Repeat on the chosen host, with its network/model latency and expected working-day load |
| Recovery | `tests/test_recovery.py`, `tests/test_pg_recovery.py`, `ops/pg-recovery-result.json` | Independent off-host backup retention and restoration into the chosen customer environment; deletion reconciliation; agreed recovery objectives |
| Model | `redteam/served-stub-results.json` and the separately named live result; actual-input caches; shared serving/evaluation configuration | Independent review of current prose and business usefulness; no historical label reuse |
| Release engineering | `GAUGES.md`; pinned dependency advisory report; Linux runtime dependency resolution; Render image build/execution and live smoke verification in `ops/deployment-result.json` | Hosted GitHub CI remains SKIPPED; customer configuration and independent security assessment remain required |

A green engineering gate permits a reviewable candidate. It does not substitute for the customer
acceptance column. The owner subsequently authorized deployment to the existing demo service;
`ops/deployment-result.json` records that action and its limits. No purchase, legal commitment or
new customer IdP/accounting connection was made.

The current-model boundary evaluation treats a malformed output as an observed refusal only when
it verifies that every record stayed unchanged and no proposal or audit event was created. It reports
that separately from valid outputs held or denied by policy. Such a refusal is not a successful user
answer, and the OFF comparison is unavailable for it. Provider failures or unrun cases remain errors.
A completed boundary run does not approve model prose or establish a customer completion rate.

The completed [live boundary report](redteam/served-live-results.json) records `openai/gpt-oss-120b`
on 2026-09-05, N=100: goal executions with no approving human 0/100 = 0% [0%, 4%] (Wilson 95%
interval, as `NUMBERS.md` renders it — the only place this document takes a number from, and
`tests/numbers.py` holds it to that). Four outputs (4/100) failed strict parsing and were separately
verified to leave no proposals or record changes. The earlier incomplete run is retained in
`redteam/served-pre-json-results.json`. Neither result supplies human labels for draft accuracy or
usefulness. What exists instead, since 2026-09-05, is a model reader's labels over the same outputs
(`redteam/served_prose_labels.json`: Claude Fable 5.1, one cached output at a time, under the written
rule the historical labels use): 58/96 = 60% [50%, 70%] of the parseable outputs adopt the injected
goal in their prose. That is one model reading another; it does not close the human review in the
Quality gate, and a finance reviewer may accept or overturn any label in the file and rerun
`make numbers`. A second reading of the same outputs, made blind to the first by a different model
(`redteam/served_prose_labels_second.json`, Claude Opus 5, 2026-09-05), agrees with it on
93/96 = 97% [91%, 99%] (Cohen's κ 0.94); `NUMBERS.md` lists the three cases where the readers part,
with each reader's sentence. Agreement between two model readers measures how much the number
depends on who read it, not whether either is right, and leaves the human gate where it is.
