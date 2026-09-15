# Portfolio publication review

Review date: 2026-09-15. Destination: <https://github.com/zorionarrillaga/atezain>.

## Decision and scope

The owner requested preserving the contracting purpose and publishing GitHub material if
useful. Publication is useful here: it allows a hiring reviewer to inspect the implemented
system, tests and evaluation rather than rely on a description. Publish as an engineering
portfolio project with the [case study](CASE_STUDY.md) and [current purpose](PROJECT_PURPOSE.md).
Do not present it as evidence of customers or a production-ready collections service.

Status: **PUBLIC**, verified through GitHub on 2026-09-15 after changing the existing
repository's visibility. The repository description now identifies an engineering case study.
The existing demo `/healthz` also responded with PostgreSQL backing and demo mode that day.
No new deployment was made.

## Review performed

- Reviewed the tracked file inventory and all reachable Git history with Gitleaks v8.30.1,
  downloaded from the official release with its archive checksum verified.
- The scanner flagged generic API keys in evaluation caches. Every finding was checked
  against the corresponding file/version: each is the hexadecimal cache identifier in the
  `key` field, equal to its filename. `redteam/run.py` and `redteam/served.py` derive those
  identifiers from hashes. No unresolved scanner finding remained.
- Scanned an export of tracked and new publishable working files separately. Its findings
  were the same cache-identifier class, checked individually; no unresolved finding remained.
- Reviewed fixture contacts, operational evidence and the provenance document. Fixtures use
  example domains apart from an existing public careers address in the outreach test.
  Deployment identifiers and expired probe workspace identifiers are not credentials.
- No private career dossiers, CV, parent Work memory, customer records, database files or
  environment files are included in the publication changes. Local runtime directories and
  the virtual environment remain ignored. Existing public-intended provenance deliberately
  describes the author's prior failures and their costs; this review does not rewrite that history.
- The repository already carries the MIT license and coding-model attribution.
- GitHub reported no workflow runs, no issues or pull requests (including closed entries),
  no forks and a disabled wiki before the visibility change.

A secret scan is a publication check, not an independent security assessment. No hosted CI,
new deployment, paid service, customer connection or outbound message is part of this step.
The existing manual workflow and disabled automatic deployment remain unchanged.

## Verification

Documentation checks passed before publication preparation. Full local engineering results
are recorded in `GAUGES.md`; PostgreSQL evidence keeps its own run date when no dedicated
DSN is available. Deployment evidence remains separately dated in `ops/deployment-result.json`.
