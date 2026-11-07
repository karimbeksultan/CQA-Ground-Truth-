"""CLI: python -m cqa <command> ...

  fetch        --group DEV [--refresh]         download/cached dataset summary
  gt-template  --group DEV                     write/extend eval/goldens/<GROUP>.jsonl with empty labels
  gt-status    --group DEV                     labeling progress per split and status
  run          --group DEV --evaluator NAME    evaluate and (if gold exists) score -> runs/<id>/
  compare      --run runs/<id> [--group DEV]   re-score an existing run against current gold
  checklists                                   list checklists
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from cqa import checklists, data_source, evaluators
from cqa.harness import ground_truth, runner


def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def cmd_fetch(a: argparse.Namespace) -> None:
    convs = data_source.load(a.group, refresh=a.refresh)
    print(f"{a.group}: {len(convs)} conversations -> {data_source.cache_path(a.group)}")
    print(" ", dict(Counter((c.channel, c.language) for c in convs)))


def cmd_gt_template(a: argparse.Namespace) -> None:
    convs = data_source.load(a.group)
    path = ground_truth.golden_path(a.group)
    existing = ground_truth.load(path) if path.exists() else {}
    labels = ground_truth.template(convs, existing)
    ground_truth.save(path, labels)
    print(f"{path}: {len(labels)} labels ({len(labels) - len(existing)} new, {len(existing)} kept)")


def cmd_gt_status(a: argparse.Namespace) -> None:
    labels = ground_truth.load(ground_truth.golden_path(a.group))
    by = Counter((l.split, l.status, l.is_complete()) for l in labels.values())
    for (split, status, complete), n in sorted(by.items()):
        print(f"  {split:5} {status:8} {'complete' if complete else 'incomplete':10} {n}")


def cmd_run(a: argparse.Namespace) -> None:
    out = runner.run(a.group, a.evaluator, checklist_override=a.checklist,
                     ids=a.ids.split(",") if a.ids else None, split=a.split, workers=a.workers)
    meta = json.loads((out / "run.json").read_text(encoding="utf-8"))
    print(f"run: {out}")
    print(f"  evaluated {meta['n_ok']}/{meta['n_selected']}, errors: {len(meta['errors'])}, {meta['duration_s']} s")
    for cid, err in meta["errors"].items():
        print(f"  ERROR {cid}: {err}")
    if (out / "report.md").exists():
        print(f"  report: {out / 'report.md'}")


def cmd_compare(a: argparse.Namespace) -> None:
    run_dir = Path(a.run)
    group = a.group or json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["group_id"]
    m = runner.score_run(run_dir, ground_truth.load(ground_truth.golden_path(group)))
    print(f"scored {m.get('n_conversations', 0)} conversations -> {run_dir / 'report.md'}")


def cmd_checklists(_: argparse.Namespace) -> None:
    for cl in checklists.list_all():
        weights = sum(c.weight for c in cl.criteria)
        print(f"  {cl.id:18} v{cl.version} {cl.channel:5} {len(cl.criteria)} criteria, weights {weights:g}  {cl.name}")


def main(argv: list[str] | None = None) -> None:
    _utf8_stdout()
    p = argparse.ArgumentParser(prog="python -m cqa")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch"); f.add_argument("--group", default="DEV"); f.add_argument("--refresh", action="store_true")
    f.set_defaults(fn=cmd_fetch)
    t = sub.add_parser("gt-template"); t.add_argument("--group", default="DEV"); t.set_defaults(fn=cmd_gt_template)
    s = sub.add_parser("gt-status"); s.add_argument("--group", default="DEV"); s.set_defaults(fn=cmd_gt_status)
    r = sub.add_parser("run")
    r.add_argument("--group", default="DEV")
    r.add_argument("--evaluator", required=True, choices=evaluators.available())
    r.add_argument("--checklist", help="force one checklist id instead of the channel default")
    r.add_argument("--ids", help="comma-separated conversation ids")
    r.add_argument("--split", choices=["train", "dev", "test"])
    r.add_argument("--workers", type=int, default=4)
    r.set_defaults(fn=cmd_run)
    c = sub.add_parser("compare"); c.add_argument("--run", required=True); c.add_argument("--group")
    c.set_defaults(fn=cmd_compare)
    sub.add_parser("checklists").set_defaults(fn=cmd_checklists)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
