# Enterprise readiness review

Reviewed on 2026-09-05. This is an engineering assessment of the working tree, not an independent security certification or a statement about the currently deployed service.

## Buyer verdict

Atezain has a useful, testable approval boundary. The original application around it was a public demonstration, not a complete enterprise service. The changes in this review make a controlled evaluation more defensible and useful. They do not establish production readiness or superiority over alternatives.

The product to validate is a **supervised invoice follow-up workspace**: import records, inspect the evidence, prepare a draft, approve an exact change, and retain evidence of the outcome. Its clearest technical asset is the separation between model suggestions, permission enforcement, human decisions, and observed record changes. Position the product around that behavior, supported by reproducible tests.

Do not sell the current application as an autonomous collections service, a payments system, an accounting ledger, or a general AI security platform. It has no accounting connector, email delivery, verified workforce identity, or operational service-level commitment. Those omissions materially affect purchasing decisions.

## What changed

| Buyer concern | Finding | Implemented response | Remaining boundary |
|---|---|---|---|
| Correct customer context | Keyword retrieval could include a different customer's messages | Served graphs use `customer_context`; a versioned checkpoint namespace separates the revised retrieval from older answers | The customer field is an uploaded label, not a verified entity identifier |
| Trustworthy notes | An injected assertion could become an automatically written assistant note | Pilot mode holds every permitted write, including notes; demo behavior remains disclosed | A reviewer can still approve incorrect content; policy enforcement does not establish truth |
| Workforce access | A shared token was the only identity and had no expiry | Owner, reviewer and viewer scopes; individually issued token IDs; expiry, revocation and owner rotation; strict Bearer parsing | Tokens authenticate possession. No SSO, MFA, SCIM or independently verified person identity |
| Resource abuse | Anyone could open unbounded sessions; active connections accumulated | Global and peer provisioning limits, workspace capacity, bounded live session cache, closed connections on eviction | Distributed denial-of-service protection and provider spending ceilings remain operator responsibilities |
| Concurrent requests | Checkpoint caching did not prevent simultaneous assists from running twice | Cross-process workspace locks; a busy operation returns a retryable conflict before doing work | Operations within one workspace are serialized; throughput needs workload measurement |
| Interrupted work | An answer checkpoint could conceal incomplete proposal processing | Durable proposal idempotency keys and workflow resume after the model has answered | Unknown executor outcomes require investigation and are never blindly retried |
| Honest failure reporting | Every graph error was described as a model failure with no writes | Provider/output errors and interrupted workflow errors have different responses; uncertain effects pause new work | Recovery of external effects needs adapter-specific reconciliation before real integrations |
| Policy mistakes | An unrecognized approval value could bypass the hold | Policy files are validated on load and invalid runtime approval modes are denied | Host operators remain responsible for the policy they intentionally authorize |
| Imported money and dates | Non-finite amounts, malformed grouping, conflicting formats and spreadsheet dates were unsafe | Bounded CSV/XLSX parsing; finite cent-precision amounts; actual calendar dates; explicit format/status mapping | This remains an invoice snapshot store, not an authoritative financial ledger |
| Repeat imports | Uploading an existing ID appended the same notes repeatedly | Existing IDs are skipped entirely; accepted invoice rows and their notes are transactional | Updating imported financial truth needs a designed synchronization workflow |
| Day-to-day work | Long undifferentiated tables; totals mixed currencies | Search, filters, page controls, review desk, separate currency totals, visible remaining proposal budget | No promise that the default evaluation budget supports a full collections workload |
| Approval clarity | Technical queue and limited context | Exact proposed fields, model rationale, decision notes, and token attribution are visible | Editing the copyable draft does not edit the proposal; the UI says so |
| Data ownership | No complete export or deletion path | Records/proposals/audit export; offline audit verifier; confirmed workspace deletion; expiry cleanup command | Expiry revokes access; physical removal requires the operator's scheduled cleanup. Backups are separate |
| Budget durability | In-memory counters and local JSON could reset on restart | Atomic database rate windows and a durable model fuse shared across workers | Fixed windows permit boundary bursts; peer identity depends on a correctly configured trusted proxy |
| Privacy | Ambient tracing credentials could enable unwanted egress | Served graphs explicitly disable hosted tracing; general tracing requires opt-in; tests discard ambient model/database/tracing credentials | Assist requests still send relevant content to the configured model provider |
| Operability | Health checks returned success during storage failure | Readiness returns failure when storage is unavailable; separate liveness; request IDs and sanitized structured HTTP logs | No production uptime, alert response or disaster recovery evidence is established |
| Reproducibility | Runtime and installed multipart versions differed; transitive versions floated | Constrained runtime/development dependencies, an updated container recipe and a manual verification workflow | Container build and hosted workflow have not been run on this machine |

Regression coverage is in `tests/test_enterprise.py`, with policy-specific assertions in `tests/test_policy.py`. The existing mutation and sabotage checks are retained; sabotage targets moved with the refactored importer. Gauge values are generated from successful command output and refreshed mechanically in the documents.

## Market reference points

Enterprise AI governance products document organization/workspace isolation, roles, OIDC, and audit capabilities. See [Portkey's access controls](https://portkey.ai/docs/product/enterprise-offering/access-control-management) and [security documentation](https://portkey.ai/docs/product/enterprise-offering/security-portkey). Receivables products also offer actual collections automation and broader operational workflows; see [Chaser's product description](https://www.chaserhq.com/) and [data security information](https://www.chaserhq.com/security).

These are purchasing expectations, not evidence that Atezain outperforms either product. Atezain should demonstrate its own value on a customer's real approval workflow: time spent preparing and reviewing a reminder, correction frequency, handling of disputed invoices, and the ability to reconstruct a disputed change. No competitive benchmark or customer return on investment was measured in this review.

The security review uses the concerns in [OWASP's API Security Top Ten](https://owasp.org/API-Security/editions/2023/en/0x00-header/), particularly [unrestricted resource consumption](https://owasp.org/API-Security/editions/2023/en/0xa4-unrestricted-resource-consumption/). Passing this project's tests is not an OWASP certification.

## Release gates still required

**Before an evaluation with real customer data:** select and document the hosting/model data path; have the customer approve that data use; restrict provisioning; configure HTTPS and trusted ingress; establish named operator access; schedule and verify retention cleanup; rehearse backup and restore in an isolated environment. Use `ATEZAIN_MODE=pilot`, not the public demo defaults. `ops/RUNBOOK.md` contains the concrete configuration and recovery procedures.

**Before selling a production enterprise subscription:**

- Integrate the customer's identity provider with validated tenant and subject claims, MFA policy, and deprovisioning. Replace shared human identity with verified individuals while preserving the policy boundary.
- Implement the selected accounting connector using least-privilege credentials, stable customer identifiers, incremental synchronization, explicit conflicts, and an adapter-specific reconciliation test. Deliver real business value beyond a manually uploaded snapshot.
- If reminders will be sent, implement a transactional outbox and provider idempotency/reconciliation. Do not rename today's simulated record update into “delivery.”
- Agree capacity and retention requirements, then measure sustained workload, concurrent users, provider failures and cold starts. Size policy limits from that evidence. Do not promise an SLA from the free demo's existence.
- Rehearse restoration from an independent backup, including records, access state, checkpoints and the audit anchor. Define recovery objectives and incident ownership with the customer.
- Obtain an independent security assessment of the deployed configuration and connector. Run a new named-model evaluation for the served customer-scoped retrieval and all-approval pilot policy; preserve the historical results as historical.
- Finish browser and accessibility verification on an enabled browser, including narrow screens, keyboard navigation, expired sessions, recovery errors, exports and destructive-action confirmation.
- Complete procurement materials: service scope, subprocessors/data locations, support obligations, retention and incident procedures, security reporting channel, and the customer's legal review. No compliance badge is claimed.

## Evidence and limits of this session

`GAUGES.md` records the command results. `ops/dependency-audit.json` is the machine-readable package advisory scan, including every queried name and version; it reports no known vulnerabilities for those versions as queried in this session. This is a point-in-time advisory check, not proof that dependencies contain no defects.

The existing named-model corpus and prose labels in `NUMBERS.md` remain measurements of the earlier prompt/retrieval/policy configuration. The served application's new customer-scoped context and pilot policy have not been measured against a live model in this review. Stub and cached-output tests establish deterministic control behavior, not model quality or injection rates in the revised product.

Browser inspection was attempted but machine computer-use permissions were unavailable. The JavaScript syntax and API workflow were checked; a rendered browser result has not been verified. The constrained runtime resolved in a dry run for Linux x86-64 / Python 3.12, but Docker was not available for an image build or execution. The hosted CI workflow is manual and has not been activated. No deployment or push is part of this review.
