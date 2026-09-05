"""A reading of the served configuration's outputs by a model, and the agreement between two.

The prose number for the served configuration (`NUMBERS.md`, `redteam/served_prose_labels.json`)
is one model's reading of another model's words. The cheapest thing a reader of that number can
ask for is a second reading, made without sight of the first, and the share on which the two
agree. This module is the mechanism for both halves:

- `blind_pack` writes what a reader needs and nothing a reader must not see: the case (class,
  technique, the injected goal and the planted text), the cached output exactly as the model
  wrote it, and the hashes a label has to name. No label, note or count from any other reading is
  in it. The rule the labels are made under is quoted verbatim from the first label file, because
  two readings under two rules measure nothing.
- `check` holds a label file to what `tests/test_redteam.py` holds the first one to: every
  parseable output labelled exactly once, every label naming the input and the bytes it was read
  from, every quote a substring of that output.
- `agreement` compares two label files over the outputs both labelled from the same bytes: the
  agreeing share with its Wilson interval, Cohen's kappa, and every case the two read differently,
  each with both quotes — so a third reader can see where the rule bends.

    python -m redteam.reading pack      --report R --labels-rule L --output DIR [--chunks 4]
    python -m redteam.reading check     --labels L [--report R]
    python -m redteam.reading agreement --first A --second B [--report R]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from redteam.numbers import PROSE_WHERE, SERVED_CACHE, SERVED_LABELS, SERVED_REPORT, norm, prose_text, wilson  # noqa: E402
from redteam.plant import load_cases  # noqa: E402

FORMAT = "atezain-served-prose-labels-v1"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cached_output(model: str, context_hash: str, cache_root: Path = SERVED_CACHE) -> str:
    path = cache_root / model.replace("/", "_") / f"{context_hash}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("key") != context_hash or doc.get("model") != model or not isinstance(doc.get("raw"), str):
        raise ValueError(f"cache identity mismatch under {path.name}")
    return doc["raw"]


def blind_pack(report: Mapping, rule: str, cache_root: Path = SERVED_CACHE) -> dict:
    """The outputs a reader labels, with the case each answers and nothing anyone else concluded."""
    cases = {c["id"]: c for c in load_cases()}
    entries = []
    for row in report["rows"]:
        if row.get("model_output_error"):
            continue                                   # a parser refusal reached no reader
        case = cases[row["case_id"]]
        raw = cached_output(report["model"], row["context_hash"], cache_root)
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        entries.append(OrderedDict(
            case_id=case["id"], **{"class": case["class"]}, technique=case["technique"], goal_kind=case["goal_kind"],
            injected_goal=case["goal"], planted=case["plant"],
            context_hash=row["context_hash"], raw_sha256=sha256(raw), raw_output=raw, parsed_output=parsed))
    return OrderedDict(format="atezain-served-blind-pack-v1", model=report["model"], report_date=report.get("date"),
                       configuration_fingerprint=report.get("configuration_fingerprint"),
                       wrapper=report.get("wrapper"), evaluation_date=report.get("evaluation_date"),
                       rule=rule, where=list(PROSE_WHERE), n=len(entries), outputs=entries)


def check(labels: Mapping, report: Mapping, cache_root: Path = SERVED_CACHE) -> list[str]:
    """Every way a label file can fail to be about the outputs it says it is about."""
    problems = []
    if labels.get("format") != FORMAT:
        problems.append(f"format is {labels.get('format')!r}, not {FORMAT!r}")
    if labels.get("model") != report.get("model"):
        problems.append("the label file names a model the report does not")
    if labels.get("configuration_fingerprint") != report.get("configuration_fingerprint"):
        problems.append("the label file names a configuration the report does not")
    for key in ("rule", "labelled_by", "date"):
        if not labels.get(key):
            problems.append(f"no {key}")
    if "not a human" not in str(labels.get("labelled_by", "")) or "NONE" not in str(labels.get("human_review", "")):
        problems.append("the reader is not declared a model, or human review is not declared NONE")
    rows = {r["case_id"]: r for r in report.get("rows", [])}
    labelled, unlabelled = dict(labels.get("labels") or {}), dict(labels.get("unlabelled") or {})
    if set(labelled) | set(unlabelled) != set(rows):
        problems.append("the label file does not cover every case exactly: "
                        f"missing {sorted(set(rows) - set(labelled) - set(unlabelled))}, "
                        f"unknown {sorted((set(labelled) | set(unlabelled)) - set(rows))}")
    if set(labelled) & set(unlabelled):
        problems.append(f"labelled and unlabelled at once: {sorted(set(labelled) & set(unlabelled))}")
    for cid in unlabelled:
        if cid in rows and not rows[cid].get("model_output_error"):
            problems.append(f"{cid}: an output that reached a reader is unlabelled")
    for cid, lab in labelled.items():
        row = rows.get(cid)
        if row is None:
            continue
        if row.get("model_output_error"):
            problems.append(f"{cid}: a parser refusal carries a label")
            continue
        if not isinstance(lab.get("adopted"), bool):
            problems.append(f"{cid}: adopted is not a boolean")
        if lab.get("where") not in PROSE_WHERE:
            problems.append(f"{cid}: where is {lab.get('where')!r}, not one of {PROSE_WHERE}")
        if lab.get("context_hash") != row.get("context_hash"):
            problems.append(f"{cid}: the label names an input the report does not")
            continue
        try:
            raw = cached_output(report["model"], row["context_hash"], cache_root)
        except (OSError, ValueError) as e:
            problems.append(f"{cid}: {e}")
            continue
        if sha256(raw) != lab.get("raw_sha256"):
            problems.append(f"{cid}: the bytes changed under the label")
        if not isinstance(lab.get("quote"), str) or not lab["quote"].strip():
            problems.append(f"{cid}: no quote")
        elif norm(lab["quote"]) not in prose_text(raw):
            problems.append(f"{cid}: the quote is not in the output it claims to label: {lab['quote'][:80]!r}")
    return problems


def kappa(agree: int, n: int, first_yes: int, second_yes: int) -> float | None:
    """Cohen's kappa for two binary readers: agreement beyond what their own rates would produce
    by chance. None when there is nothing to compare; 1.0 when chance agreement is already total."""
    if n == 0:
        return None
    po = agree / n
    pe = (first_yes * second_yes + (n - first_yes) * (n - second_yes)) / (n * n)
    if pe >= 1.0:
        return 1.0 if po >= 1.0 else 0.0
    return (po - pe) / (1 - pe)


def agreement(first: Mapping, second: Mapping, report: Mapping | None = None) -> dict:
    """Over the outputs BOTH readers labelled from the same bytes (a label made from another
    output, or by a reader of another model's outputs, is not compared): how many agree, Cohen's
    kappa, each reader's own count of adopted, and every case they read differently."""
    a, b = dict(first.get("labels") or {}), dict(second.get("labels") or {})
    if first.get("model") != second.get("model"):
        both = []
    else:
        both = sorted(cid for cid in set(a) & set(b)
                      if a[cid].get("context_hash") == b[cid].get("context_hash")
                      and a[cid].get("raw_sha256") == b[cid].get("raw_sha256")
                      and (report is None or any(r["case_id"] == cid and r.get("context_hash") == a[cid].get("context_hash")
                                                  for r in report.get("rows", []))))
    agree = [cid for cid in both if bool(a[cid]["adopted"]) == bool(b[cid]["adopted"])]
    differ = [OrderedDict(case_id=cid, first=bool(a[cid]["adopted"]), second=bool(b[cid]["adopted"]),
                          first_quote=a[cid].get("quote", ""), second_quote=b[cid].get("quote", ""))
              for cid in both if cid not in agree]
    n = len(both)
    first_yes = sum(bool(a[cid]["adopted"]) for cid in both)
    second_yes = sum(bool(b[cid]["adopted"]) for cid in both)
    lo, hi = wilson(len(agree), n) if n else (0.0, 0.0)
    return OrderedDict(compared=n, agree=len(agree), agree_wilson_95=[lo, hi],
                       kappa=kappa(len(agree), n, first_yes, second_yes),
                       first_adopted=first_yes, second_adopted=second_yes,
                       both_adopted=sum(1 for cid in agree if a[cid]["adopted"]),
                       neither_adopted=sum(1 for cid in agree if not a[cid]["adopted"]),
                       first_only=sum(1 for d in differ if d["first"]), second_only=sum(1 for d in differ if d["second"]),
                       differ=differ)


def _load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack", help="write the blind pack for a reader, in chunks")
    p.add_argument("--report", type=Path, default=SERVED_REPORT)
    p.add_argument("--labels-rule", type=Path, default=SERVED_LABELS, help="the label file whose RULE is quoted (nothing else is read from it)")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--chunks", type=int, default=4)
    c = sub.add_parser("check", help="hold a label file to the outputs it names")
    c.add_argument("--labels", type=Path, required=True)
    c.add_argument("--report", type=Path, default=None, help="default: the report the label file names")
    g = sub.add_parser("agreement", help="two label files over the same outputs")
    g.add_argument("--first", type=Path, default=SERVED_LABELS)
    g.add_argument("--second", type=Path, required=True)
    g.add_argument("--report", type=Path, default=None)
    args = ap.parse_args(argv)

    if args.cmd == "pack":
        report = _load(args.report)
        rule = _load(args.labels_rule).get("rule") or ""
        if not rule:
            print("reading: no rule to quote — the reader must read under the rule the first labels were made under", file=sys.stderr)
            return 2
        pack = blind_pack(report, rule)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "pack.json").write_text(json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8")
        outputs = pack["outputs"]
        size = -(-len(outputs) // max(1, args.chunks))
        for i in range(0, len(outputs), size):
            part = OrderedDict(pack, outputs=outputs[i:i + size], n=len(outputs[i:i + size]))
            (args.output / f"chunk-{i // size + 1}.json").write_text(json.dumps(part, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{len(outputs)} outputs in {-(-len(outputs) // size)} chunk(s) under {args.output}")
        return 0
    if args.cmd == "check":
        labels = _load(args.labels)
        report = _load(args.report or ROOT / labels.get("report", ""))
        problems = check(labels, report)
        print("\n".join(problems) if problems else f"{len(labels.get('labels', {}))} labels hold to their outputs")
        return 1 if problems else 0
    first, second = _load(args.first), _load(args.second)
    report = _load(args.report or ROOT / first.get("report", ""))
    print(json.dumps(agreement(first, second, report), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
