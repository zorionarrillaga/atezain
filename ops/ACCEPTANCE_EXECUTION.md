# Running the remaining acceptance exercises

The release decision remains OPEN. These tools prepare evidence for the gates in
[RELEASE_ACCEPTANCE.md](RELEASE_ACCEPTANCE.md); local tool tests do not stand in for hosted execution
or customer acceptance.

## Current-model human review

The already prepared local pack is `var/acceptance/current-model-review-final/`. To generate a new copy:

```sh
.venv/bin/python -m ops.model_review --output var/acceptance/model-review-new
```

Open `index.html` in that directory. Record reviewer judgments in `review.json`, retaining its input
and output hashes with the final signed review. Consult each case's `model_input` in `review.json`
for the full input, including source snippets. The generator checks the evaluated source fingerprint,
reconstructs each actual model input and compares its hash with the separately named live report.
It loads the cached output without calling a model or reusing historical prose labels. Output byte
hashes are captured when the pack is prepared; the original report did not independently anchor
those bytes. Preserve the pack in the controlled evidence store before review.

Every human label starts pending. The rubric asks the reviewer to compare financial facts, read
summary/recommendation/draft/note prose for adoption of the injection, quote supporting text, and record
corrections. Parser refusals stay visible as unsuccessful answers. The ordinary-workflow worksheet
covers ordinary overdue work, partial payments, changed recipients, disputes, promises, currencies,
settlement and provider failure. Those scenarios are NOT_RUN until exercised by the finance team;
the worksheet supplies no invented model outputs. Agree task-time, correction and completion targets
with the finance lead before scoring. Neither this generator nor the workload driver signs acceptance.

## Hosted workload setup

An operator must prepare the authorized acceptance deployment and test accounts using
[RUNBOOK.md](RUNBOOK.md). Use enterprise mode, PostgreSQL, the selected live model and actual OIDC
reviewer accounts. Import fictional invoices through the selected Xero sandbox connection and run a
successful full synchronization. This driver neither registers provider applications nor changes
accounting data. Its default operation is a read-only preflight.

Prepare an isolated, fresh workspace with the release target of 500 source-backed invoice records,
no previous proposals or cases, a valid audit and an open fuse. Every contact email must end in
`@example.test` and every customer name must start with the same unique
`ATEZAIN-ACCEPTANCE-<run-id>-` prefix (run ID length: 8–40 letters, digits, hyphens or underscores).
These are deliberately fictional fixtures. Select 50 distinct due invoice IDs, ten for each of five
different verified subjects with reviewer membership. Do not run against a customer workspace or
relax the deployed policy, resource limits or identity lifetime for the benchmark.

Copy [acceptance-plan.example.json](acceptance-plan.example.json) outside the tracked source tree,
for example to `var/acceptance/host-plan.json`. Replace all placeholders, invoice IDs and the fixture
prefix. The origin must be HTTPS, with no credentials or path. Copy the actual policy fingerprint
from a reviewed export. Record the actual deployed commit and image digest from deployment evidence;
the driver treats these two values as operator attestations because the API does not expose a
verified code/image identity.

Through the operator's controlled test-login tooling, obtain the current `__Host-atezain` session
cookie for each consenting test reviewer after real workforce sign-in. Keep them in a private file
outside Git and chat, keyed `reviewer-1` through `reviewer-5`:

```json
{
  "reviewer-1": "CURRENT_SESSION_COOKIE_FROM_TEST_LOGIN",
  "reviewer-2": "CURRENT_SESSION_COOKIE_FROM_TEST_LOGIN",
  "reviewer-3": "CURRENT_SESSION_COOKIE_FROM_TEST_LOGIN",
  "reviewer-4": "CURRENT_SESSION_COOKIE_FROM_TEST_LOGIN",
  "reviewer-5": "CURRENT_SESSION_COOKIE_FROM_TEST_LOGIN"
}
```

Set its filesystem mode to 600. The runner never prints or exports those values. It fetches the CSRF
token using each current cookie and sends the cookie only to the selected origin, without following
redirects. The file is reread during the run so the operator can atomically replace it after genuine
renewed sign-in by the same subjects. A changed subject or an expired session fails the exercise;
the runner does not mint sessions, lengthen authorization or bypass MFA.

Run the read-only preflight, choosing a new output directory each time:

```sh
.venv/bin/python -m ops.hosted_workload \
  --plan var/acceptance/host-plan.json \
  --credentials var/acceptance/reviewer-sessions.json \
  --output var/acceptance/host-preflight-new
```

`PREFLIGHT_ONLY` with `passed: false` is the expected successful preparation result. It verifies the
deployed mode, live model declaration, PostgreSQL readiness, distinct verified reviewers, selected
workspace/policy, fictional records, source coverage/freshness and due invoice selection. It is not a
capacity pass. Inspect any FAILED report and fix its actual cause before continuing.

Once the operator has authorized synthetic assists and approval simulation in this exact workspace,
run the same command with `--apply --confirm-workspace <exact-workspace-id>` and another new output
directory. Applied execution makes model calls, approves allowed held fixture proposals, saves each
reviewer's follow-up plan and reads the worklist. Approvals are explicitly labelled as synthetic
workload actions, not human quality judgments. The capability check requires email delivery to be off.

## Interpreting and retaining the workload result

The result can pass only after all assigned cases complete and exported records match the expected
approved effects. Verification includes untouched invoices, exact new notes/reminders/statuses,
saved case versions, unchanged source/policy/administration data, proposal uniqueness, one audited
execution per proposal and offline verification anchored to the initial audit head. Initial and final
exports, model assists and the report are retained in the private output directory. No cleanup or
rollback is performed: preserve failed attempts and inspect partial effects before a new run.

Only explicit workspace-busy conflicts are retried, within a bounded wait. Rate limits, provider
failures, stale source, uncertain execution and expired access cannot be reported as passes. The
runner waits for all active workers before requesting the final export. If access/storage failure
prevents that export, its absence remains a failed evidence capture; recover access and inspect the
workspace separately rather than inferring that nothing changed.

The declared latency is client-observed logical request time, including authentication probes and
busy backoff where applicable. Non-provider p95 is checked against the release target; assist and
source-reconciling decision requests are recorded separately. All request attempts that finish with
an error remain visible. Keep failed runs in the acceptance evidence store rather than discarding
them from completion reporting.

This is one concurrent batch. Repeat under the customer's agreed working-day schedule and cold-start
conditions; it does not by itself prove sustained service capacity, recovery, prose quality or an SLA.
Combine it with live IdP/Xero acceptance, independent backup restoration, operational drills,
finance-user review, independent security assessment and named customer/operator sign-offs.
