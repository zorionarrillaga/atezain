# GAUGES.md

Written by `make all` on 2026-09-05 (`tests/gauge_record.py`) from the last line each gauge printed;
the only source of a gauge count in `README.md`, `WRITEUP.md` and `STATUS.md` — `tests/gauges.py`
holds them to it, and `make all` ends red if they disagree. Do not edit by hand. The `test` line
is the run without a database; the line with `ATEZAIN_TEST_DSN` is the last run that had one —
carried forward from this file, with its own date, when the working tree has no DSN run of its
own, so that a clone with no database can still reach a green `make all`.

| gauge | result |
|---|---|
| `make test` | 348 passed, 115 skipped |
| `make test` with `ATEZAIN_TEST_DSN` | 462 passed, 1 skipped (2026-09-05) |
| `make mutate` | 48 checks · 48 killed by assertion · 0 killed only by a crash · 0 survived · 25 crashing test(s) alongside assertion kills |
| `make hostile` | 37/37 scored attempts blocked · 1 out of scope, shown |
| `make sabotage` | 63/63 sabotages caught by at least one gauge |
