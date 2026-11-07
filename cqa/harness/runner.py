"""Run an evaluator over a dataset group and (if gold labels exist) score the run.

Output: runs/<run_id>/
  run.json          metadata: evaluator, model, checklists + fingerprints, git commit, timing, errors
  predictions.jsonl one line per conversation: evaluation, recomputed score, latency, error
  metrics.json      comparison with gold (only when gold labels are available)
  report.md         human-readable version of metrics.json
"""
from __future__ import annotations

import json
import subprocess
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from cqa import checklists, data_source, evaluators
from cqa.harness import ground_truth, report
from cqa.harness.metrics import Pair, compare
from cqa.models import Checklist, Conversation, Evaluation
from cqa.scoring import ScoringError, score

ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = ROOT / "runs"


def _git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, timeout=10)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                               capture_output=True, text=True, timeout=10).stdout.strip()
        return out.stdout.strip() + ("-dirty" if dirty else "") if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _evaluate_one(evaluator: evaluators.Evaluator, conv: Conversation, checklist: Checklist) -> dict:
    rec: dict = {"conversation_id": conv.conversation_id, "language": conv.language, "channel": conv.channel,
                 "checklist_id": checklist.id, "checklist_fingerprint": checklists.fingerprint(checklist)}
    started = time.perf_counter()
    try:
        ev = evaluator.evaluate(conv, checklist)
        rec["evaluation"] = ev.model_dump(mode="json")
        rec["score"] = score(checklist, ev.answers()).model_dump(mode="json")
        rec["error"] = None
    except Exception as exc:  # recorded per conversation, never swallowed: run.json lists every failure
        rec["evaluation"] = None
        rec["score"] = None
        rec["error"] = f"{type(exc).__name__}: {exc}"
        rec["traceback"] = traceback.format_exc(limit=5)
    rec["latency_s"] = round(time.perf_counter() - started, 3)
    return rec


def select(convs: list[Conversation], ids: list[str] | None, split: str | None,
           gold: dict[str, ground_truth.GoldLabel] | None) -> list[Conversation]:
    if ids:
        wanted = set(ids)
        unknown = wanted - {c.conversation_id for c in convs}
        if unknown:
            raise KeyError(f"unknown conversation ids: {sorted(unknown)}")
        convs = [c for c in convs if c.conversation_id in wanted]
    if split:
        if not gold:
            raise ValueError("--split needs a gold file (eval/goldens/<GROUP>.jsonl)")
        convs = [c for c in convs if c.conversation_id in gold and gold[c.conversation_id].split == split]
    return convs


def run(group_id: str, evaluator_name: str, *, checklist_override: str | None = None,
        ids: list[str] | None = None, split: str | None = None, workers: int = 4,
        run_id: str | None = None, convs: list[Conversation] | None = None,
        gold_path: Path | None = None, runs_dir: Path = RUNS_DIR, evaluator_kwargs: dict | None = None) -> Path:
    evaluator = evaluators.create(evaluator_name, **(evaluator_kwargs or {}))
    convs = convs if convs is not None else data_source.load(group_id)
    gold_path = gold_path or ground_truth.golden_path(group_id)
    gold = ground_truth.load(gold_path) if gold_path.exists() else None
    selected = select(convs, ids, split, gold)
    if not selected:
        raise ValueError("no conversations selected")
    # resolved before any output exists: a bad --checklist fails fast instead of leaving a half run
    plan = [(c, checklists.for_conversation(c, checklist_override)) for c in selected]

    run_id = run_id or f"{datetime.now():%Y%m%d-%H%M%S}-{group_id}-{evaluator_name}" + (f"-{split}" if split else "")
    out = runs_dir / run_id
    out.mkdir(parents=True, exist_ok=False)
    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        records = list(pool.map(lambda job: _evaluate_one(evaluator, *job), plan))

    with (out / "predictions.jsonl").open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    meta = {
        "run_id": run_id, "group_id": group_id, "evaluator": evaluator_name,
        "model": getattr(evaluator, "model", None), "split": split, "git_commit": _git_commit(),
        "checklists": {cl.id: checklists.fingerprint(cl) for _, cl in plan},
        "started_at": started.isoformat(), "duration_s": round(time.perf_counter() - t0, 2),
        "n_selected": len(selected), "n_ok": sum(r["error"] is None for r in records),
        "errors": {r["conversation_id"]: r["error"] for r in records if r["error"]},
        "latency_s": {"mean": round(sum(r["latency_s"] for r in records) / len(records), 3),
                      "max": max(r["latency_s"] for r in records)},
    }
    (out / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    if gold:
        score_run(out, gold)
    return out


def load_predictions(run_dir: Path) -> list[dict]:
    text = (run_dir / "predictions.jsonl").read_text(encoding="utf-8")
    return [json.loads(l) for l in text.split("\n") if l.strip()]  # not splitlines(): see ground_truth.load


def score_run(run_dir: Path, gold: dict[str, ground_truth.GoldLabel]) -> dict:
    """Compare a run with gold labels. Every conversation left out is listed under `skipped` with a reason."""
    preds = load_predictions(run_dir)
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    skipped: dict[str, list[str]] = {"no_gold": [], "gold_incomplete": [], "gold_invalid": [],
                                     "prediction_error": [], "checklist_mismatch": [], "checklist_changed": []}
    current: dict[str, Checklist] = {}
    pairs: list[Pair] = []
    for rec in preds:
        cid = rec["conversation_id"]
        g = gold.get(cid)
        if g is None:
            skipped["no_gold"].append(cid)
            continue
        if not g.is_complete():
            skipped["gold_incomplete"].append(cid)
            continue
        if rec["error"]:
            skipped["prediction_error"].append(cid)
            continue
        if g.checklist_id != rec["checklist_id"]:
            skipped["checklist_mismatch"].append(cid)
            continue
        if g.checklist_id not in current:
            current[g.checklist_id] = checklists.load(g.checklist_id)
        checklist = current[g.checklist_id]
        if checklists.fingerprint(checklist) != rec["checklist_fingerprint"]:
            skipped["checklist_changed"].append(cid)  # checklist edited after the run: re-run instead
            continue
        try:
            score(checklist, g.complete_answers())
        except ScoringError:
            skipped["gold_invalid"].append(cid)
            continue
        ev = Evaluation.model_validate(rec["evaluation"])
        pairs.append(Pair(conversation_id=cid, language=rec["language"], channel=rec["channel"],
                          checklist=checklist, pred=ev.answers(), gold=g.complete_answers()))
    metrics = compare(pairs) if pairs else {"n_conversations": 0}
    metrics["skipped"] = skipped
    (run_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "report.md").write_text(report.render(meta, metrics), encoding="utf-8")
    return metrics
