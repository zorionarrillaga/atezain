#!/bin/sh
# Is it up, and does it answer? One line per check, appended, from the owner's machine:
#   */10 * * * * cd <repo> && sh ops/probe.sh https://<service>.onrender.com >> /dev/null 2>&1
# The first and the last line of ops/uptime.log are the two numbers that go into NUMBERS.md after
# seven days. A free instance spins down when idle, so a cold start is a slow 200, not a failure —
# the latency column is what says which.
set -eu
URL="${1:-http://127.0.0.1:8000}"
LOG="$(dirname "$0")/uptime.log"
START=$(date -u +%s)
CODE=$(curl -s -o /tmp/atezain-probe.$$ -w '%{http_code}' --max-time 60 "$URL/healthz" || true)
CODE=${CODE:-000}
MS=$(( ($(date -u +%s) - START) * 1000 ))
BODY=$(head -c 300 /tmp/atezain-probe.$$ 2>/dev/null || true)
rm -f /tmp/atezain-probe.$$
printf '%s %s %s %sms %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$URL" "$CODE" "$MS" "$BODY" >> "$LOG"
[ "$CODE" = "200" ]
