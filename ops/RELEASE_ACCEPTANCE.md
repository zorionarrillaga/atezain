# Closing the supervised collections release

This checklist applies to the **future enterprise release**. The immediate owner-authorized milestone
is [solo evaluation](../SOLO_EVALUATION.md), which requires no external accepting team. These enterprise
gates remain deferred and open while the assistant evaluates the fictional demo on the owner's behalf.

This is the execution checklist for [PRODUCT_RELEASE.md](../PRODUCT_RELEASE.md), prepared on
2026-09-05. It adds no customer approval or measurement. The recorded engineering results support
a candidate; **customer release acceptance is OPEN**. The recorded deployment still uses demo mode.

## Establish the acceptance environment

The owner and finance lead must name the accepting team, confirm its accounting and identity
providers, select the hosting/model data path, and name an operator. Xero and Entra ID are provisional.
If there is no customer yet, prepare and exercise a reference test environment with fictional data;
customer acceptance stays OPEN. If the selected providers differ, revise the integration scope before
collecting acceptance evidence against the wrong systems.

Record these decisions before external exercises:

| Decision | Responsible role | Current state |
|---|---|---|
| Accepting finance team and reviewer; workflow and language | Owner + finance lead | OPEN |
| Accounting test organisation and registered OAuth application | Owner + accounting administrator | OPEN; Xero provisional |
| Workforce test tenant, application and enforced assurance policy | Identity administrator | OPEN; Entra ID provisional |
| Acceptance host, PostgreSQL instance, ingress and model configuration | Operator + owner | OPEN |
| Approved test-data use and model/provider processing path | Customer + owner | OPEN |
| Workload, recovery, retention, support and model-quality acceptance thresholds | Finance lead + operator + owner | OPEN; release targets provisional |

Use secret-manager references for credentials, never secret values in this checklist or Git. An
external exercise requires access to its designated test environment. Deployments, purchases and
contractual commitments follow the owner's applicable authorization; preparing this checklist does
not activate a service or establish a commercial commitment.

## Gate closure matrix

The responsible roles below must be replaced by named people in the acceptance record. Existing
evidence retains its original environment and date. Each row remains OPEN for customer release.

| Gate | Evidence already recorded | Remaining exercise and pass condition | Responsible role |
|---|---|---|---|
| Identity | Generated-key protocol tests; persistent membership, session and revocation tests | Sign in through the actual customer tenant with required MFA; refuse missing assurance, wrong tenant/client and disabled-user login. Verify signing-key rollover, immediate local membership removal, and expiry of an existing browser authorization within five minutes. Record provider-disable behavior separately from immediate local removal. | Identity administrator + engineer |
| Accounting | Read-only scope/tenant checks, exact balances, source transactions and stale-proposal refusals with fixtures | In the selected sandbox: create an invoice; sync twice; partially pay; change the contact; settle/void; revoke and reconnect. Compare stable IDs, recipients and exact outstanding balances with exports after each step. Exercise pagination and a failed page; the cursor must not advance on partial failure. After the freshness ceiling, draft/approval must refuse; a selected-invoice reread must refuse an obsolete reminder. | Accounting administrator + engineer |
| Daily work | API regression and local keyboard/narrow-screen browser exercises | Finance users complete due-list review, assignment, dispute, promise, deferral, exact amendment, separate approval, restart/resume and export. Verify source changes invalidate held work. Measure task time, corrections and completion against a manual baseline and agreed targets; obtain reviewer acceptance. | Finance lead + reviewers |
| Capacity | Local SQLite/stub workload report with synthetic identity and accounting | Run the agreed workload through HTTPS on the selected host with PostgreSQL and actual deployed limits: candidate target 500 invoice records, five reviewers and 50 assisted cases per working day. Verify expected record effects and saved cases, with no losses or duplicate effects. Non-provider request p95 must be below two seconds in the declared environment; report model/source latency, failures, retries and cold starts separately. | Engineer + operator |
| Recovery | Automated isolated SQLite and PostgreSQL restoration; local encrypted deployment snapshot | Restore an independently stored encrypted PostgreSQL backup into an isolated destination on the selected hosting. Verify records, access/revocations, cases, approvals, checkpoints, source state, credential decryptability and audit heads retained outside the backup. Reconcile post-backup deletions/revocations and stale sessions before access. Measure recovery time and recoverable data age against agreed objectives; provisional ceilings are four hours and 24 hours respectively. | Operator, witnessed by customer/operator reviewer |
| Quality | Green recorded gauges; separately named current live-model boundary report | Run the engineering gates for the selected candidate. Review current cached adversarial prose with explicit human labels and quoted evidence; separately evaluate representative ordinary collections cases. Keep malformed outputs and provider errors in completion reporting. Obtain finance-user acceptance against an agreed rubric; preserve historical labels and results. | Engineer + independent reviewer + finance lead |
| Procurement and security | Operating procedures and configuration requirements | Document and obtain customer approval of data locations, model/subprocessors, retention/deletion including backups, support, incident owner/escalation and service scope. Exercise monitoring and cleanup in the acceptance environment. An independent assessment must cover deployed identity, isolation, connector, secrets and ingress, with no unresolved release-blocking finding. | Owner + customer + operator + independent assessor |

## Tools prepared and remaining execution

The hosted driver and model review generator are now implemented. Follow
[ACCEPTANCE_EXECUTION.md](ACCEPTANCE_EXECUTION.md) for setup, commands and interpretation. Their
external exercises and human acceptance remain open.

- **Hosted workload driver:** `python -m ops.hosted_workload` performs HTTPS preflight and an explicitly
  confirmed synthetic workload using verified reviewer sessions. It checks exact record/case effects,
  anchored exported audit, source/policy stability and latency. Session expiry, provider failures and
  incomplete cases fail the run. It still needs the designated enterprise host and fictional sandbox
  workspace; its offline tests are not hosting evidence. The original `python -m ops.workload` remains
  a separate local SQLite/stub exercise. Do not lower requirements or bypass limits to obtain a pass.
- **Finance review pack:** `python -m ops.model_review` reconstructs and checks current input hashes,
  loads cached outputs and prepares a readable pack with blank labels and quoted-evidence fields.
  The ordinary-workflow worksheet remains NOT_RUN. Finance reviewers must agree sample coverage and
  usefulness/correction/time thresholds, perform those exercises and score the current outputs.
  Parser refusals remain unsuccessful answers. No human label or acceptance is supplied by the tool.
  A model reader's labels over the same outputs exist since 2026-09-05 (`redteam/served_prose_labels.json`,
  reported in `NUMBERS.md` with the reader named); they are a starting point a finance reviewer may
  accept or overturn label by label, and they do not close this gate.
- **Hosted verification evidence:** `.github/workflows/verify.yml` is prepared but its execution is
  recorded as SKIPPED. Once activation is authorized, run it on the candidate, fix failures and retain
  the run URL, commit, gate logs and image digest. Add artifact retention for gauges, workload and
  recovery reports; building an image alone does not test the customer deployment. Never use the
  customer database as the test-suite DSN.
- **Operational automation:** configure scheduled source synchronization, daily full reconciliation,
  retention cleanup, independent backups and monitoring on the selected host. The runbook supplies
  commands; scheduling and witnessing their success remain deployment work. Retain a sanitized
  failure-and-response drill, backup retrieval evidence and independent audit anchors.

Follow [RUNBOOK.md](RUNBOOK.md) for the implemented configuration and commands. Run `make all` for
repository changes; run the dedicated PostgreSQL suite and restoration drill for the release
candidate. Reuse relevant dated evidence when its code, configuration and environment still match;
rerun affected exercises after changes. Do not relabel a historical run as a fresh measurement.

## Sequence and evidence record

1. Confirm the accepting team, providers, environment, responsible people and acceptance thresholds.
2. Use the prepared workload driver and human review pack. Resolve engineering failures through
   the existing tests, mutation, hostile and sabotage gates.
3. Configure the authorized acceptance deployment in `ATEZAIN_MODE=enterprise`, including OIDC,
   read-only Xero, PostgreSQL, ingress and operational jobs. Verify `/capabilities` reports required
   identity and approval for all writes, and that legacy bearer access is refused. A green smoke
   check of the current demo deployment does not close this step.
4. Complete provider acceptance, then hosted capacity/recovery exercises and finance-user review.
   Arrange the independent assessment against this actual configuration and retest any fixes.
5. Collect customer/operator sign-offs and record the release decision for that exact candidate.

Use one record per gate in the controlled acceptance evidence store:

```text
Gate and requirement:
Candidate commit and image digest:
Environment and sanitized configuration fingerprint:
Provider tenant/organisation reference and model configuration, where applicable:
Executor and independent reviewer:
UTC start/end; procedure and approved test-data reference:
Expected result and pre-agreed threshold:
Observed result, counts/errors and limitations:
Evidence URI and checksum (logs, export, report or signed review):
Status: OPEN / RUNNING / FAIL / PASS / SKIPPED
Failure/retest reference or reason skipped:
Accepting person and dated sign-off:
```

Store sensitive evidence under appropriate access controls; commit only sanitized references and
reports. Missing evidence, unsigned acceptance and SKIPPED exercises keep the corresponding gate
open. There is no average pass score: every release gate must pass for the chosen scope. A changed
requirement needs an explicit, versioned agreement; it is not a pass against the original criteria.

The final customer/operator decision must identify the candidate, deployment configuration and all
gate records. Until then the defensible status remains **engineering candidate; acceptance OPEN**.
