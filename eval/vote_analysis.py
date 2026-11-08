"""Repeat-run variance and majority-of-N voting, computed offline from runs with an identical config.

    python eval/vote_analysis.py runs/<run1> runs/<run2> runs/<run3>

All runs must share the checklist fingerprints (same config). Scores c01–c07 (the flag c08 is reported
separately), by split, plus agreement with the blind independent labels (eval/blind/DEV-blind-20.jsonl).
Read-only: prints a markdown report.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cqa.harness import ground_truth, runner  # noqa: E402

SCORED = ["c01", "c02", "c03", "c04", "c05", "c06", "c07"]


def answers(run_dir: Path) -> tuple[dict, dict]:
    latest = {}
    for r in runner.load_predictions(run_dir):
        latest[r["conversation_id"]] = r
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    out = {cid: {x["criterion_id"]: x["answer"] for x in r["evaluation"]["results"]}
           for cid, r in latest.items() if r.get("evaluation")}
    return out, meta["checklists"]


def majority(votes: list[dict]) -> dict:
    out = {}
    for k in votes[0]:
        vals = [v[k] for v in votes]
        c = Counter(str(x) for x in vals)
        top = max(c.values())
        out[k] = next(x for x in vals if c[str(x)] == top)
    return out


def agree(pred: dict, gold: dict, ids: list[str], keys=SCORED) -> tuple[int, int]:
    ok = n = 0
    for cid in ids:
        for k in keys:
            if cid in pred:
                n += 1
                ok += str(pred[cid][k]) == str(gold[cid][k])
    return ok, n


def pct(t: tuple[int, int]) -> str:
    return f"{100 * t[0] / t[1]:.1f}% ({t[0]}/{t[1]})" if t[1] else "—"


def recall(pred: dict, gold: dict, ids: list[str], k: str, cls) -> str:
    sel = [c for c in ids if str(gold[c][k]) == str(cls)]
    hit = sum(str(pred[c][k]) == str(cls) for c in sel if c in pred)
    return f"{hit}/{len(sel)}"


def main(paths: list[str]) -> None:
    runs = [answers(Path(p)) for p in paths]
    fps = {json.dumps(fp, sort_keys=True) for _, fp in runs}
    if len(fps) != 1:
        sys.exit(f"runs differ in checklist fingerprints: {fps}")
    preds = [r for r, _ in runs]
    labels = ground_truth.load(ground_truth.golden_path("DEV"))
    gold = {cid: g.complete_answers() for cid, g in labels.items()}
    common = sorted(set.intersection(*(set(p) for p in preds)))
    split = {s: [c for c in common if labels[c].split == s] for s in ("train", "dev", "test")}
    split["dev+test"] = split["dev"] + split["test"]
    split["all"] = common
    vote = {cid: majority([p[cid] for p in preds]) for cid in common}

    print(f"Runs: {', '.join(Path(p).name for p in paths)} — {len(common)} dialogs in common, scored c01–c07\n")
    print("| | " + " | ".join(split) + " | c06=4 | c06=7 | c05=no | gross (c08) |")
    print("|---|" + "---|" * (len(split) + 4))
    rows = [(f"run {i + 1}", p) for i, p in enumerate(preds)] + [(f"majority of {len(preds)}", vote)]
    for name, p in rows:
        cells = [pct(agree(p, gold, ids)) for ids in split.values()]
        cells += [recall(p, gold, common, "c06", 4), recall(p, gold, common, "c06", 7),
                  recall(p, gold, common, "c05", "no"), recall(p, gold, common, "c08", "yes")]
        print(f"| {name} | " + " | ".join(cells) + " |")

    print("\nRepeat-run stability (identical config): share of identical answers, c01–c08")
    for i in range(len(preds)):
        for j in range(i + 1, len(preds)):
            same = n = 0
            for cid in common:
                for k in preds[i][cid]:
                    n += 1
                    same += str(preds[i][cid][k]) == str(preds[j][cid][k])
            print(f"- run {i + 1} vs run {j + 1}: {100 * same / n:.1f}% ({n - same} of {n} answers differ)")

    blind_path = ROOT / "eval" / "blind" / "DEV-blind-20.jsonl"
    if blind_path.exists():
        blind = {}
        for line in blind_path.read_text(encoding="utf-8").split("\n"):
            if line.strip():
                r = json.loads(line)
                blind[r["conversation_id"]] = {k: (int(v) if isinstance(v, str) and v.isdigit() else v)
                                               for k, v in r["answers"].items()}
        ids = [c for c in blind if c in vote]
        print(f"\nAgainst the blind independent labels ({len(ids)} chats, c01–c07):")
        print(f"- gold vs blind: {pct(agree(gold, blind, ids))}")
        for name, p in rows:
            print(f"- {name} vs blind: {pct(agree(p, blind, ids))}")


if __name__ == "__main__":
    main(sys.argv[1:])
