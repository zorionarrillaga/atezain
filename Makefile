PY := .venv/bin/python
PYABS := $(abspath $(PY))
PROMPTFOO := promptfoo@0.122.2
REDTEAM_MODEL ?= stub
REDTEAM_MODEL_ID ?= openai/gpt-oss-120b

.PHONY: test mutate hostile sabotage all redteam numbers vocabulary serve

# With ATEZAIN_TEST_DSN set, the policy suite runs TWICE — once on SQLite, once on that Postgres
# (PLAN.md §4.1). That is ~3.5 minutes of network round-trips, so for the fast loop:
#   ATEZAIN_TEST_DSN= make test        the SQLite arm only; the Postgres arm SKIPS, loudly
# `make mutate` and `make sabotage` always drop the variable in the child they run: what they
# measure is whether a check can fail, not which database it fails on.
test:
	$(PY) tests/gauge_record.py run test -- $(PY) -m pytest -q -p no:cacheprovider tests/

mutate:
	$(PY) tests/gauge_record.py run mutate -- $(PY) tests/mutate.py

hostile:
	$(PY) tests/gauge_record.py run hostile -- $(PY) tests/hostile_selftest.py

sabotage:
	$(PY) tests/gauge_record.py run sabotage -- $(PY) tests/sabotage.py

# `all` writes GAUGES.md from successful output, refreshes documented gauge values, and checks
# agreement. Unsupported edits still fail the pre-run document checks.
all: test mutate hostile sabotage
	$(PY) tests/gauge_record.py write --sync

# The red-team. promptfoo is the runner and its assertion is the gate: a planted injection whose
# goal proposal reaches execution fails the eval and this target exits non-zero. The test list is
# regenerated from redteam/cases/*.json first, so a new case is a case promptfoo runs.
#   make redteam                        the harness, on StubLLM, at $0 and no network
#   make redteam REDTEAM_MODEL=groq     the named model (needs GROQ_API_KEY in the environment)
# Concurrency is 1 on purpose: the free tier is 30 requests/minute and redteam/run.py paces itself.
redteam:
	$(PY) -m redteam.provider
	ATEZAIN_MODEL=$(REDTEAM_MODEL) ATEZAIN_MODEL_ID=$(REDTEAM_MODEL_ID) \
	PROMPTFOO_PYTHON=$(PYABS) PROMPTFOO_DISABLE_TELEMETRY=1 PROMPTFOO_DISABLE_UPDATE=1 \
	npx --yes $(PROMPTFOO) eval -c redteam/promptfooconfig.yaml \
	    -o redteam/promptfoo_results.json -j 1 --no-cache --no-progress-bar
	$(PY) -m redteam.run --from-promptfoo redteam/promptfoo_results.json

numbers:
	$(PY) -m redteam.numbers

# The prose gauges alone (they are also in `make test`): every percentage in README.md, WRITEUP.md
# and PAGE.md is in NUMBERS.md and the pasted block is a fresh render; every technology the write-up
# names is imported and called on the main path, and a name that maps to nothing is red.
vocabulary:
	$(PY) -m pytest -q -p no:cacheprovider tests/vocabulary.py tests/numbers.py tests/gauges.py

# The deployed face, locally: SQLite under var/, the stub model, no key and no network.
# Live providers and Postgres use explicitly configured uvicorn / the pilot image (ops/RUNBOOK.md).
serve:
	env -u ATEZAIN_DSN -u DATABASE_URL -u GROQ_API_KEY \
	    -u LANGSMITH_TRACING -u LANGCHAIN_TRACING -u LANGCHAIN_TRACING_V2 \
	    ATEZAIN_MODE=demo ATEZAIN_MODEL=stub ATEZAIN_ADAPTER=invoices-es \
	    ATEZAIN_STATE_DIR=$${ATEZAIN_STATE_DIR:-var} .venv/bin/uvicorn api.app:app --port $${PORT:-8000} --reload
