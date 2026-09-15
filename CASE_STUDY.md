# Atezain: building a supervised AI records assistant

**An engineering portfolio project by Zorion Arrillaga.** Built with coding-model assistance;
the owner directed the work. The aim was an inspectable deployed system demonstrating AI
workflow engineering, permission enforcement and evaluation. It is not a claim of customer
adoption or a commercially validated collections product.

## The problem explored

An AI assistant reads customer records and proposes a next action. A malicious instruction
inside those records can influence its answer. Atezain separates the model's suggestion
from the authority to change records: the model proposes, policy checks, a reviewer decides
held actions, and the executor records the observed result.

The invoice adapter provides a concrete demonstration: import fictional invoices, prepare
a follow-up, inspect a proposed change, approve or reject it, and inspect the audit history.
Approving a reminder records it; this application does not deliver email.

## What to inspect

| Capability | Implementation / evidence |
| --- | --- |
| API and assistant orchestration | [FastAPI application](api/app.py), [LangGraph workflow](agent/graph.py) |
| Permissions, approvals and execution | [Policy service](policy/service.py), [executor](agent/executor.py) |
| Durable records and workflow state | [PostgreSQL records](records/store_pg.py), [checkpoints](agent/checkpoints.py) |
| Identity and accounting boundaries | [OIDC verification](api/oidc.py), [Xero connector](integrations/xero.py); customer configuration remains unvalidated |
| Failure-oriented verification | [Tests](tests), [mutation checks](tests/mutate.py), [hostile checks](tests/hostile_selftest.py), [sabotage checks](tests/sabotage.py) |
| Model evaluation and limitations | [Generated results](NUMBERS.md), [experiment write-up](WRITEUP.md), [served evaluation](redteam/served-live-results.json) |
| Deployment and operation | [Dockerfile](ops/Dockerfile), [deployment record](ops/deployment-result.json), [runbook](ops/RUNBOOK.md) |

Recorded counts and model results live in the linked evidence. Model prose evaluation was
performed by models; agreement between them does not establish correctness or human acceptance.
Permission enforcement does not guarantee truthful drafts. The host, identity provider,
executor and store have explicit [trust boundaries](README.md#trust-boundary).

## Try and reproduce

The [hosted demo](https://atezain.onrender.com/demo) uses fictional/sample data and the existing
demo configuration; free hosting may sleep. The deployment record is dated, not an uptime SLA.
The [README](README.md#run-it) provides the local setup. `make serve` uses a stub model;
`make all` runs the repository's engineering checks. PostgreSQL checks require a dedicated
test database and are reported skipped when it is absent. No paid model call is needed for
those local checks.

## Product judgment and lesson

The manual workflow asks users to transfer invoices, inspect drafts, copy messages and keep
payment information current. That burden can cancel the benefit. The build demonstrates
engineering capability, but it does not yet supply a convincing reason for a finance worker
to adopt another tool.

The lesson is to reason through the complete user workflow, integration, maintenance burden,
existing alternatives and route to users before building. Technical completeness cannot
substitute for net user value. Product expansion is paused under the [current purpose and
decision](PROJECT_PURPOSE.md); the engineering artifact remains useful for code review and
technical discussion.
