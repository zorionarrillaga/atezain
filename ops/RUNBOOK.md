# Operating a controlled Atezain pilot

## Enterprise candidate configuration and acceptance

The active release scope is `PRODUCT_RELEASE.md`. Demo and legacy pilot settings below remain useful
for fictional evaluation; customer workforce access uses `ATEZAIN_MODE=enterprise`. This mode requires
the configured PostgreSQL database, owner operator secret and Groq provider just as pilot mode does,
and additionally requires a complete OIDC configuration. It refuses legacy workspace bearer grants.
The operator still provisions with `POST /sessions` and `X-Owner-Token`, then grants a verified subject
through `PUT /operator/workspaces/{sid}/members` with JSON `subject`, `role` and `enabled`. The returned
legacy owner token cannot open an enterprise workspace. Keep the operator secret in an administrative
secret store, never in browser settings or model input.

### Workforce identity

| Variable | Required value |
|---|---|
| `ATEZAIN_OIDC_ISSUER` | Exact HTTPS tenant-specific issuer, including its path; never a common/organisations/consumers issuer |
| `ATEZAIN_OIDC_CLIENT_ID` | Registered confidential web application's client ID |
| `ATEZAIN_OIDC_CLIENT_SECRET` | Secret-manager value for the web application |
| `ATEZAIN_OIDC_REDIRECT_URI` | Exact registered HTTPS URL ending `/auth/callback` on this application's origin |
| `ATEZAIN_OIDC_TENANT` | Expected signed `tid` for Entra; omit only for a provider whose issuer alone establishes the tenant |
| `ATEZAIN_OIDC_MFA_CLAIM` | Provider-reviewed assurance claim: `acrs`, `acr` or `amr` |
| `ATEZAIN_OIDC_MFA_VALUE` | Required value enforced by the customer's actual MFA policy |

The server verifies discovery issuer, RS256 signing keys, issuer/audience/authorised party, expiration,
issued/authentication time, nonce, optional tenant and configured assurance. Browser callbacks use PKCE
and single-use state tied to a secure browser cookie. Provider tokens are discarded after validation;
the browser receives a short-lived opaque HttpOnly/Secure/SameSite cookie. Mutating browser requests
also require the session's CSRF token. Membership is checked for every request and after workspace
locking. Owners manage exact issuer-scoped subject IDs, not email or display-name aliases. The export
includes membership administration events; these are host records, not the policy hash chain.

Register each user's subject as issued to this application. After bootstrapping the owner, use Access
& controls to add reviewers or viewers. Removing a member invalidates existing local sessions; adding
them back requires another sign-in. Local authorization expires within the release target even when
the provider does not send a revocation event. Sign out revokes the local session; it does not sign the
person out of every provider application. SCIM and provider-initiated logout are outside this candidate.

**MFA acceptance must be performed in the actual tenant.** For Entra authentication context, configure
the claim as required for ID tokens and connect its value to a Conditional Access policy that requires
the intended MFA method. A signed `acrs` value can be issued without such a policy; configuring `c1`
in this application alone proves no MFA event. See the [Microsoft authentication-context guidance](https://learn.microsoft.com/en-nz/entra/identity-platform/developer-guide-conditional-access-authentication-context).
Record tests for missing assurance, wrong tenant/client, disabled user, local removal, renewed login
and signing-key rollover. The repository's generated-key tests are protocol evidence only.

### Xero source connection and reconciliation

Set `ATEZAIN_XERO_CLIENT_ID`, `ATEZAIN_XERO_CLIENT_SECRET`, `ATEZAIN_XERO_REDIRECT_URI` (the exact HTTPS
`/accounting/xero/callback` URL) and `ATEZAIN_CREDENTIAL_KEY` (a Fernet key generated and stored by the
operator's secret manager). Preserve the encryption key separately from database backups. A missing
key cannot be recovered from the database. Restrict secret reads to the application/operator identity.

A signed-in workspace owner enters the Xero organisation UUID in Access & controls and authorizes
read-only invoice/contact access. The callback checks `/connections` for that exact organisation.
The granted scopes must include `offline_access accounting.invoices.read accounting.contacts.read`
and no extra accounting capability. Tenant binding is immutable; another organisation needs another
workspace. A tenant cannot be connected to multiple active workspaces. No credential is returned to
the browser, included in exports or passed to the model. Reconnection after a revoke uses the same
organisation; disconnect discards local credentials. Revoke the grant in Xero as well when removing
provider access. Encryption-key rotation can be performed by disconnecting, changing the secret,
restarting and reconnecting; keep old keys under backup retention until older backups expire.

Use Sync accounting for an initial full import. Schedule the maintenance command with the service's
configuration; these calls access the authorized provider and should be monitored:

```sh
.venv/bin/python -m ops.manage sync-accounting
.venv/bin/python -m ops.manage sync-accounting --apply
```

The default is dry-run. Schedule applied synchronization more frequently than the freshness ceiling
in `PRODUCT_RELEASE.md`; the candidate refuses source-based drafting/approval after an hour without
successful synchronization. It uses an overlapping modified-since cursor and fetches contacts each
run; a full reconciliation is required daily. Paid/voided invoices remain visible as settled. The
stored amount is the outstanding balance, with the original total and source identifier retained.
Missing, regressed or inconsistent source records stop the import and leave its prior cursor intact.
A capacity refusal is explicit; never silently raise limits or drop older invoices to make it pass.
The release record limit includes all imported invoices, including historical settled ones.

Before each draft and reminder approval, the connector rereads the selected invoice and its contact.
A changed balance, recipient or follow-up plan refuses an obsolete proposal; reject it and prepare a
new follow-up. These single-record reads do not advance the organisation-wide cursor. A provider error
pauses approvals, preserves existing work and records a sanitized connection status; honor Retry-After
on quota failures, and reconnect for refused credentials. A source conflict needs comparison against
Xero by the operator, followed by a complete sync. Never edit snapshot rows or audit rows to bypass it.
The provider is not a transactional snapshot API: reconciliation and refusal reduce race exposure but
cannot prevent Xero changing after the last read. Atezain performs no external financial write.

Use a fresh workspace when moving from evaluation CSV data to Xero. Internal display IDs are allocated
without overwriting uploaded records, while source UUIDs remain authoritative. Enterprise mode refuses
an assist/approval on an invoice that lacks an accounting snapshot. CSV evaluation is still available
in demo/pilot mode. Correspondence supplied through imports remains untrusted text.

Sandbox acceptance: create an invoice, sync twice, record a partial payment, change its contact,
settle/void it, revoke/reconnect the grant, and verify the selected tenant and cursor after failures.
Compare exported source balances/IDs with Xero after each step. Use approved test data and confirm the
current developer terms, plan and model-processing permissions before customer use. The connector's
HTTP fixtures have no authority to claim provider acceptance.

### Daily review and release limits

Reviewers use the due list, assigned work, disputes and payment promises. Save and approve a plan
records their exact ownership/date/note through the policy executor. Conflicting versions require a
reload. A saved promise is a claim, never a payment. Disputes and future deferrals block reminders;
settled source balances also block them. Later drafts include the saved plan. Editing a held reminder
creates a new held proposal and preserves its rejected original; it needs a separate approval.
Nothing in this flow sends email. A finance reviewer sends manually only after reviewing the text.

Enterprise policy limits are defined in `api/configuration.py` and shared with the current-workflow
evaluation. All writes require approval; planned capacity and limits are in `PRODUCT_RELEASE.md`.
`ops/workload-result.json` measures an in-process SQLite/stub/identity/source-fixture exercise, including
workspace-lock retries. Repeat on the chosen host with its real latency, limits and model before
agreeing a capacity promise. Model calls and global fuses remain independently bounded.

### Encrypted backup and restore drill

`ATEZAIN_BACKUP_KEY` is a separate operator-held Fernet key. Save complete backups outside application
storage, with retention and deletion procedures independent from active workspace cleanup. Do not
store keys beside backups. Preserve the applicable credential encryption key as well, since it is
needed for restored Xero credentials. Use provider backups for databases above the bounded local
archive size or when independent off-host backups and retention are required.

SQLite requires all workers and maintenance commands to stop. A shared service lease makes the
offline backup refuse a running worker. Never remove its lock inode to bypass that refusal:

```sh
.venv/bin/python -m ops.recovery backup --state var --file /safe/backup.enc
.venv/bin/python -m ops.recovery restore --state /isolated/new-state --file /safe/backup.enc
```

Restore creates a new directory only, verifies authenticated encryption, file hashes, database
integrity and stored audit anchors before making it available. Compare those anchors with independently
retained exports: an anchor inside the backup alone does not prove the original service was honest.

PostgreSQL uses matching-or-newer vendor `pg_dump`/`pg_restore` clients. Configure the source as
`ATEZAIN_BACKUP_DSN` and the isolated empty destination as `ATEZAIN_RESTORE_DSN` in the operator's
environment, then run:

```sh
.venv/bin/python -m ops.pg_backup backup --file /safe/postgres.enc
.venv/bin/python -m ops.pg_backup restore --file /safe/postgres.enc
```

Credentials do not appear in subprocess arguments or normal error output. The backup includes control
storage, every workspace schema and checkpoints in a consistent vendor dump. Restore refuses a
nonempty database and uses a single transaction. Keep the destination inaccessible to application
users while restoring; do not reuse a live database or connect a worker concurrently. Ownership and
ACLs are deliberately not copied: apply the destination's reviewed role grants before service use.

The regression drill creates only its own temporary source/target databases, exercises the application,
restores the encrypted archive, compares its audit with the separately retained original export,
checks revoked access and checkpoint reuse, and removes those temporary databases. It requires
`ATEZAIN_TEST_DSN` with database-creation privilege and vendor clients on PATH. Run it against the
explicitly dedicated test instance. `ops/pg-recovery-result.json` records the actual completed drill.
This is separate from a customer-host restoration, independent backup retention and contractual RPO/RTO.
After any restoration reconcile deletions/revocations since the backup, disable stale login sessions,
reconcile accounting and validate independent audit heads before allowing user access.

### Current-workflow model verification

```sh
.venv/bin/python -m redteam.served --output redteam/served-stub-results.json
.venv/bin/python -m redteam.served --model groq --output redteam/served-live-results.json
```

The live command uses the configured free test-provider key and the existing fictional injection
corpus. Its cache is separate from the historical experiment and keys the actual model input. It
uses the same JSON response mode, output limit and model reasoning setting as serving and records
date, model, configuration, sample size and Wilson interval; a partial/provider-error run is red. Parser refusals are counted separately only after proving
that no proposals or record changes occurred; they are not useful completed user answers.
It measures the boundary without a human approving. It does not establish useful or safe prose:
review the new cached outputs independently with the finance user before release. Historical prose
labels are deliberately not reused. Do not run this against customer data without its authorization.

## Legacy evaluation setup and general operations

This runbook supports evaluation, not an unqualified production service. Read `ENTERPRISE_REVIEW.md` before accepting customer data. Keep deployment credentials in the platform's secret store; never put them in a CSV, URL, repository or chat log.

## Local evaluation

Use Python 3.12 or later and install the constrained environment:

```sh
python3 -m venv .venv
.venv/bin/pip install -r ops/requirements-dev.txt
.venv/bin/pip check
make serve
```

Open `http://127.0.0.1:8000/demo`. `make serve` forces the deterministic stub and SQLite under `var/` (or your explicit `ATEZAIN_STATE_DIR`), clearing inherited database/provider settings. Start a workspace and load the sample. It contains fictional invoice records and a planted instruction. The assistant produces Spanish copy. The interface is in English.

Keep access details from **Access & controls** before signing out. By default the browser retains access for the tab session; remembering it across browser restarts is an explicit option for a private device. Anyone holding a token inherits that token's role. Losing the only owner token is not an account recovery flow.

## Pilot configuration

Set these through the deployment's secret/configuration mechanism:

| Setting | Purpose |
|---|---|
| `ATEZAIN_MODE=pilot` | Requires operator-authorized provisioning, persistent storage and a configured live model; every permitted write requires approval |
| `ATEZAIN_DSN` or `DATABASE_URL` | Dedicated PostgreSQL database, encrypted transport, restricted network access and a database role limited to this application |
| `ATEZAIN_OWNER_TOKEN` | Random operator credential of at least 32 characters; provisions workspaces and clears the global model fuse |
| `ATEZAIN_MODEL=groq` and `GROQ_API_KEY` | The approved model provider and its credential |
| `ATEZAIN_MODEL_ID` | Model identifier agreed for the evaluation; default `openai/gpt-oss-120b` |
| `ATEZAIN_SESSION_DAYS` | Access lifetime measured from workspace creation; default 30 |
| `ATEZAIN_MAX_SESSIONS` | Total stored workspace cap, including expired workspaces awaiting purge; default 1000 |
| `ATEZAIN_MAX_LIVE_SESSIONS` | Per-process open workspace cache; default 8 |
| `ATEZAIN_MAX_RECORDS` | Total invoice capacity per workspace, across files; default 500 |
| `ATEZAIN_TRUSTED_PROXIES` | Exact ingress IP addresses/networks Uvicorn may trust for forwarding headers |

These values are configuration limits, not tested enterprise capacity promises. An assist can generate several proposals, and the adapter's proposal budget is visible in the UI before a user works the list. Changing that policy requires review of `adapters/invoices-es/permissions.toml`, verification, and a migration plan for existing workspaces.

The application does not terminate TLS. Put it behind an HTTPS ingress, restrict direct access to its listening port, reject unexpected Host headers at the ingress, and configure connection/body timeouts and abuse controls there. Trust only that ingress's forwarding addresses. The container defaults to trusting loopback; a platform with different proxy addresses must supply its actual range. Do not set a wildcard to make rate limits appear to work. Without correct proxy configuration, callers sharing a proxy address share the peer limit. Database counters still enforce the global creation/model limits.

Do not configure implicit LangSmith/LangChain tracing: startup rejects those tracing flags. The served graph also uses an explicit local tracer, so Langfuse credentials do not activate hosted output capture. Non-served experiments need an explicit `ATEZAIN_HOSTED_TRACING=1` opt-in. Model-provider requests are separate from tracing and contain relevant customer records.

The image runs as a non-root user. The Docker recipe limits concurrent HTTP tasks and connection backlog. Memory, CPU, file descriptor limits, network policy, encryption at rest and backup encryption belong to the deployment. Use a direct PostgreSQL endpoint or session pooling for the pilot. Transaction pooling is unsupported: workspace advisory locks and schema search paths require session affinity. SQLite requires a persistent local filesystem and is not a horizontally shared network filesystem solution.

Use the container entry point for a hosted pilot. For an explicitly configured local integration environment, launch `.venv/bin/uvicorn api.app:app --host 127.0.0.1 --port 8000` directly; `make serve` intentionally forces demo settings.

Provision with `POST /sessions` and the `X-Owner-Token` header from a trusted administrative client. It returns a workspace ID and owner token once. Give only the workspace credential to its owner; the operator credential is never a browser setting. Reviewers can read, assist and decide; viewers can read and export; only owners can import, grant/revoke access, rotate owner access, stop/reopen or delete a workspace. Pilot mode disables the unauthenticated interactive API documentation; `/capabilities` describes the active product mode.

## Verification and change control

```sh
make all
# Explicitly configure a disposable test database, then:
make test
# The Postgres arm runs only when ATEZAIN_TEST_DSN is set.
```

The tests clear ambient production/model/tracing settings. Use a dedicated test database anyway. The process-restart test creates an isolated control schema and separate record schemas. Do not use production credentials as a convenience.

`make all` runs tests, marked-check mutation, hostile-object probes and sabotage. Only successful output becomes a gauge record. Its final writer refreshes the documents' gauge values from those records; pre-run document consistency checks still fail on unsupported edits. The manual GitHub workflow in `.github/workflows/verify.yml` supplies a disposable PostgreSQL service, installs dependencies, runs the gates and builds the image. It has no deployment step and has not been activated here.

Before deploying, take a database backup, capture the deployed image/commit and configuration fingerprint, and verify the candidate against a copy. The new control tables and `proposal_requests` table are additive. The revised served retrieval uses `customer-v1` in checkpoint thread IDs, so previous retrieval answers are not silently reused; a new assist after migration can create new proposals alongside historical ones. Review outstanding proposals during migration. Do not roll back across a data or policy change without checking those states.

For dependency maintenance, refresh `ops/constraints.txt` deliberately, run an advisory scan on the complete list, and rerun the gates. The saved advisory report is evidence of a past query. It is not a continuing vulnerability-monitoring service. Package hashes and image digests should be captured by the release process before a production release.

## Monitoring and incidents

- `/livez` checks that the process can answer HTTP. `/healthz` checks control storage and returns HTTP 503 on failure. It does not call the provider or prove end-to-end model availability.
- HTTP responses carry `X-Request-ID`. The `atezain.http` logger emits JSON fields for route template, status and duration when enabled at INFO level. No request body, credentials or record values are logged by it. The container disables Uvicorn's ordinary URL access log; configure the JSON logger in the platform if request metrics are required.
- Monitor readiness failures, 5xx responses, repeated busy conflicts, provider failures, quota exhaustion, database connections and retention-job failures. Assign a person and escalation path before the pilot. This repository does not supply an on-call service.
- HTTP 401 means missing, expired, rotated or revoked access. HTTP 403 means the credential lacks the role. A busy-workspace 409 contains `Retry-After`; retry after the operation ends. An audit-investigation 409 is a different condition and must not be retried in a loop.
- Provider or malformed-output failures return 502 before proposals are processed and can be retried. A workflow 503 can follow recorded work. Retry the same invoice: the durable checkpoint and proposal keys resume work without making new copies of saved proposals.
- `executed_unknown`, `executed_mismatch`, a broken chain or a missing outcome stops further assist/decision work at the HTTP boundary. Export the evidence and compare the actual records with the approved fields. Do not remove an execution claim or invent a success row to make the UI green. The read-only Xero reconciliation procedure is above; future outbound effects need their own reconciliation.
- Stop a workspace through the controls or `POST /sessions/{sid}/fuse/stop`. Normal reopening is no earlier than the next day. Clearing a fuse does not erase uncertain execution outcomes. The global model fuse is separate and remains stopped until the operator explicitly clears it.

## Retention and deletion

Expired tokens stop opening their workspaces immediately; expiry is not a physical delete. Schedule the following with the same environment/database configuration as the service, and monitor its exit code:

```sh
.venv/bin/python -m ops.manage purge-expired
.venv/bin/python -m ops.manage purge-expired --apply
```

The default is a dry run. Applied cleanup revokes a workspace before deleting records and checkpoints. A failed cleanup leaves a revocation marker and is retried by the next run. Busy workspaces are reported as failures for a later retry. Do not remove the lock files while workers are running: replacing a locked inode could permit overlapping operations.

Owners can export and then explicitly delete their workspace in the UI. The API also requires `X-Confirm-Delete` equal to the workspace ID. Deletion includes active record/audit data and checkpoints, not third-party backups or previously downloaded exports. The operator must declare the separate backup retention period and apply deletions consistently when restoring a backup.

## Backup, restore and independent evidence

Back up the complete database, including the session/token tables, model budget, checkpoint tables and every workspace schema. Backing up only invoices loses approvals, access and recovery state. Retain backups outside the application's credentials, encrypt them, and restrict operator access. Use the database provider's supported consistent backup procedure; this repository has not performed a production restoration drill.

Rehearse restoration into a separate database and isolated application instance with outbound model calls disabled. Compare record counts and held/executed states, verify representative exported heads, verify revoked/expired access remains refused, and confirm the instance cannot send anything externally. Reconcile deletions since the backup. Record the measured recovery time and acceptable data loss before agreeing recovery objectives.

A workspace export includes its complete policy audit and head. Verify it offline:

```sh
.venv/bin/python -m ops.verify_export workspace.json
.venv/bin/python -m ops.verify_export workspace.json --anchor-seq SEQUENCE --anchor-hash HASH
```

Use a head kept independently of the service as the anchor. Hash verification detects a modified or truncated audit relative to that anchor. It does not authenticate the exported invoice data, prove a human's identity, prove a claim in a draft, or prevent an operator from rewriting history after the last independent anchor.

The served JSON response option follows [Groq’s JSON output documentation](https://console.groq.com/docs/structured-outputs). JSON syntax is not authorization or a guarantee of factual accuracy; strict local parsing and policy checks still apply.
