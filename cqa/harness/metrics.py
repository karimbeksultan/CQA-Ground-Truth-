"""Compare predicted answers with gold answers.

Reported per criterion (never only one blended number): exact agreement, recall for each
gold class (yes / no / na / scale step), confusion matrix, and step MAE for scales.
Overall: total-score MAE/RMSE, share within 5 and 10 points, gross-error flag recall/precision,
and the same numbers per language and channel.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass

from cqa.models import Answer, Checklist
from cqa.scoring import score


@dataclass
class Pair:
    conversation_id: str
    language: str
    channel: str
    checklist: Checklist
    pred: dict[str, Answer]
    gold: dict[str, Answer]


def _key(a: Answer) -> str:
    return str(a)


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def _total_stats(errors: list[float]) -> dict:
    if not errors:
        return {"n": 0, "mae": None, "rmse": None, "max_abs": None, "within_5": None, "within_10": None}
    abs_e = [abs(e) for e in errors]
    return {
        "n": len(errors),
        "mae": round(sum(abs_e) / len(abs_e), 3),
        "rmse": round(math.sqrt(sum(e * e for e in errors) / len(errors)), 3),
        "max_abs": round(max(abs_e), 2),
        "within_5": round(sum(e <= 5 for e in abs_e) / len(abs_e), 3),
        "within_10": round(sum(e <= 10 for e in abs_e) / len(abs_e), 3),
        "bias": round(sum(errors) / len(errors), 3),  # >0: we score higher than gold
    }


def compare(pairs: list[Pair]) -> dict:
    per_crit: dict[str, dict] = {}
    agree_all: list[bool] = []
    total_errors: list[float] = []
    totals_one_side_missing: list[str] = []
    group_errors: dict[str, dict[str, list[float]]] = {"language": defaultdict(list), "channel": defaultdict(list)}
    group_agree: dict[str, dict[str, list[bool]]] = {"language": defaultdict(list), "channel": defaultdict(list)}
    flag = Counter()
    worst = []

    for p in pairs:
        gold_score = score(p.checklist, p.gold)
        pred_score = score(p.checklist, p.pred)
        diffs = []
        for c in p.checklist.criteria:
            g, pr = p.gold[c.id], p.pred[c.id]
            ok = _key(g) == _key(pr)
            st = per_crit.setdefault(c.id, {"name": c.name, "type": c.type, "n": 0, "correct": 0,
                                            "confusion": defaultdict(Counter), "step_errors": []})
            st["n"] += 1
            st["correct"] += ok
            st["confusion"][_key(g)][_key(pr)] += 1
            if c.type == "scale" and g != "na" and pr != "na":
                st["step_errors"].append(abs(int(pr) - int(g)))  # type: ignore[arg-type]
            if c.type == "flag":
                flag[("gold_yes" if g == "yes" else "gold_no", "pred_yes" if pr == "yes" else "pred_no")] += 1
            else:
                agree_all.append(ok)
                group_agree["language"][p.language].append(ok)
                group_agree["channel"][p.channel].append(ok)
            if not ok:
                diffs.append({"criterion": c.id, "gold": g, "pred": pr})

        if gold_score.total is not None and pred_score.total is not None:
            err = pred_score.total - gold_score.total
            total_errors.append(err)
            group_errors["language"][p.language].append(err)
            group_errors["channel"][p.channel].append(err)
        elif gold_score.total != pred_score.total:
            totals_one_side_missing.append(p.conversation_id)
        worst.append({
            "conversation_id": p.conversation_id, "language": p.language, "channel": p.channel,
            "gold_total": gold_score.total, "pred_total": pred_score.total,
            "abs_err": None if gold_score.total is None or pred_score.total is None
            else round(abs(pred_score.total - gold_score.total), 2),
            "diffs": diffs,
        })

    criteria_out = {}
    for cid, st in per_crit.items():
        recall = {}
        for gold_cls, preds in st["confusion"].items():
            support = sum(preds.values())
            recall[gold_cls] = {"recall": round(preds.get(gold_cls, 0) / support, 3), "support": support}
        criteria_out[cid] = {
            "name": st["name"], "type": st["type"], "n": st["n"],
            "accuracy": round(st["correct"] / st["n"], 3) if st["n"] else None,
            "per_class": recall,
            "confusion": {g: dict(c) for g, c in st["confusion"].items()},
            "step_mae": _mean(st["step_errors"]),
        }

    gy = flag[("gold_yes", "pred_yes")] + flag[("gold_yes", "pred_no")]
    py = flag[("gold_yes", "pred_yes")] + flag[("gold_no", "pred_yes")]
    gross = {
        "gold_positive": gy,
        "recall": round(flag[("gold_yes", "pred_yes")] / gy, 3) if gy else None,
        "precision": round(flag[("gold_yes", "pred_yes")] / py, 3) if py else None,
        "false_alarms": flag[("gold_no", "pred_yes")],
    }

    worst.sort(key=lambda w: (w["abs_err"] is None, -(w["abs_err"] or 0), -len(w["diffs"])))
    return {
        "n_conversations": len(pairs),
        "overall": {
            "criterion_agreement": _mean([float(x) for x in agree_all]),
            "total": _total_stats(total_errors),
            "total_missing_on_one_side": totals_one_side_missing,
            "gross_error": gross,
        },
        "per_criterion": criteria_out,
        "by_group": {
            dim: {k: {"agreement": _mean([float(x) for x in group_agree[dim][k]]),
                      "total": _total_stats(group_errors[dim].get(k, []))}
                  for k in sorted(group_agree[dim])}
            for dim in ("language", "channel")
        },
        "worst": worst[:15],
    }
