# GAUGES.md

Written by `make all` on 2026-09-15 (`tests/gauge_record.py`) from the last line each gauge printed,
every line measured on tree `378e92b04f03` at `f49a4b0+` (`tests/gauge_record.py tree` prints the
current one; `write` refuses a line measured on another). This is the only source of a gauge count in
`README.md`, `WRITEUP.md` and `STATUS.md` — `tests/gauges.py` holds them to it, and `make all` ends red
if they disagree. Do not edit by hand. The `test` line is the run without a database; the line with
`ATEZAIN_TEST_DSN` is the last run that had one, and names its own date and tree — carried forward
from this file when the working tree has no DSN run of its own, so that a clone with no database
can still reach a green `make all`.

| gauge | result |
|---|---|
| `make test` | 395 passed, 115 skipped |
| `make test` with `ATEZAIN_TEST_DSN` | 509 passed, 1 skipped (2026-09-15, tree 378e92b04f03 at f49a4b0+) |
| `make mutate` | 48 checks · 48 killed by assertion · 0 killed only by a crash · 0 survived · 25 crashing test(s) alongside assertion kills |
| `make hostile` | 37/37 scored attempts blocked · 1 out of scope, shown |
| `make sabotage` | 74/74 sabotages caught by at least one gauge |
