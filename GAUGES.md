# GAUGES.md

Written by `make all` on 2026-09-03 (`tests/gauge_record.py`) from the last line each gauge printed;
the only source of a gauge count in `README.md`, `WRITEUP.md` and `STATUS.md` — `tests/gauges.py`
holds them to it, and `make all` ends red if they disagree. Do not edit by hand. The `test` line
is the run without a database; the line with `ATEZAIN_TEST_DSN` is the last run that had one.

| gauge | result |
|---|---|
| `make test` | 201 passed, 86 skipped |
| `make test` with `ATEZAIN_TEST_DSN` | 286 passed, 1 skipped (2026-09-03) |
| `make mutate` | 43 checks · 43 killed by assertion · 0 killed only by a crash · 0 survived · 25 crashing test(s) alongside assertion kills |
| `make hostile` | 36/36 scored attempts blocked · 1 out of scope, shown |
| `make sabotage` | 36/36 sabotages caught by at least one gauge |
