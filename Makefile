PY := .venv/bin/python

.PHONY: test mutate hostile sabotage all

test:
	$(PY) -m pytest -q -p no:cacheprovider tests/

mutate:
	$(PY) tests/mutate.py

hostile:
	$(PY) tests/hostile_selftest.py

sabotage:
	$(PY) tests/sabotage.py

all: test mutate hostile sabotage
