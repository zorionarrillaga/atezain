PY := .venv/bin/python
PYABS := $(abspath $(PY))
PROMPTFOO := promptfoo@0.122.2
REDTEAM_MODEL ?= stub
REDTEAM_MODEL_ID ?= openai/gpt-oss-120b

.PHONY: test mutate hostile sabotage all redteam numbers serve

test:
	$(PY) -m pytest -q -p no:cacheprovider tests/

mutate:
	$(PY) tests/mutate.py

hostile:
	$(PY) tests/hostile_selftest.py

sabotage:
	$(PY) tests/sabotage.py

all: test mutate hostile sabotage

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

# The deployed face, locally: SQLite under var/, the stub model, no key and no network.
#   ATEZAIN_MODEL=groq make serve     to put the real model behind it
serve:
	ATEZAIN_STATE_DIR=$${ATEZAIN_STATE_DIR:-var} .venv/bin/uvicorn api.app:app --port $${PORT:-8000} --reload
