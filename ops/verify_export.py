"""Verify an exported audit chain offline, optionally against an independently retained head."""
import argparse
import json
from pathlib import Path
from policy.store import Store, GENESIS


def verify(document, anchor=None):
    audit = document["audit"]
    previous, seq = GENESIS, 0
    anchored = anchor is None or anchor == (0, GENESIS)
    for row in audit["rows"]:
        seq += 1
        if row["seq"] != seq or row["prev_hash"] != previous:
            return False
        actual = Store._hash(previous, seq, row["ts"], row["kind"], row["principal"], row["proposal_id"], row["detail"])
        if actual != row["hash"]:
            return False
        previous = actual
        if anchor == (seq, actual):
            anchored = True
    return anchored and seq == audit["head_seq"] and previous == audit["head_hash"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path)
    parser.add_argument("--anchor-seq", type=int)
    parser.add_argument("--anchor-hash")
    args = parser.parse_args()
    if (args.anchor_seq is None) != (args.anchor_hash is None):
        parser.error("supply both anchor arguments")
    anchor = None if args.anchor_seq is None else (args.anchor_seq, args.anchor_hash)
    try:
        ok = verify(json.loads(args.export.read_text()), anchor)
    except (OSError, ValueError, KeyError, TypeError):
        ok = False
    print("audit chain verifies" if ok else "audit verification FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
