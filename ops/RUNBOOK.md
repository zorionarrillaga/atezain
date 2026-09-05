# Operating a controlled Atezain pilot

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
- `executed_unknown`, `executed_mismatch`, a broken chain or a missing outcome stops further assist/decision work at the HTTP boundary. Export the evidence and compare the actual records with the approved fields. Do not remove an execution claim or invent a success row to make the UI green. A future external connector needs its own reconciliation procedure.
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
