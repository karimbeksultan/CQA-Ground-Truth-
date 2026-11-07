"""Markdown report for a scored run."""
from __future__ import annotations


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _num(x: float | None) -> str:
    return "—" if x is None else f"{x:g}"


def render(meta: dict, m: dict) -> str:
    lines = [f"# Run `{meta['run_id']}`", "",
             f"- evaluator: **{meta['evaluator']}**, model: `{meta.get('model')}`",
             f"- group: `{meta['group_id']}`, split: `{meta.get('split') or 'all'}`, commit: `{meta.get('git_commit')}`",
             f"- conversations: {meta['n_selected']} selected, {meta['n_ok']} evaluated, "
             f"{len(meta['errors'])} failed; mean latency {meta['latency_s']['mean']} s", ""]
    skipped = m.get("skipped", {})
    skipped_txt = ", ".join(f"{k}: {len(v)}" for k, v in skipped.items() if v)
    lines += [f"Scored against gold: **{m.get('n_conversations', 0)}** conversations"
              + (f" (skipped — {skipped_txt})" if skipped_txt else ""), ""]
    if not m.get("n_conversations"):
        lines.append("_No complete gold labels for these conversations yet: nothing to compare._")
        return "\n".join(lines) + "\n"

    o = m["overall"]
    t = o["total"]
    g = o["gross_error"]
    lines += ["## Overall", "",
              "| metric | value |", "|---|---|",
              f"| criterion agreement (scored criteria) | {_pct(o['criterion_agreement'])} |",
              f"| 95% interval, bootstrap over dialogs | "
              f"{(_pct(o['criterion_agreement_ci95'][0]) + ' – ' + _pct(o['criterion_agreement_ci95'][1])) if o.get('criterion_agreement_ci95') else '—'} |",
              f"| majority-class baseline (always the most common gold answer) | {_pct(o.get('majority_baseline'))} |",
              f"| total MAE / RMSE | {_num(t['mae'])} / {_num(t['rmse'])} |",
              f"| total within ±5 / ±10 | {_pct(t['within_5'])} / {_pct(t['within_10'])} |",
              f"| total bias (pred − gold) | {_num(t.get('bias'))} |",
              f"| max abs total error | {_num(t['max_abs'])} |",
              f"| gross error recall / precision | {_pct(g['recall'])} / {_pct(g['precision'])} "
              f"(gold positives {g['gold_positive']}, false alarms {g['false_alarms']}) |", ""]
    if o["total_missing_on_one_side"]:
        lines += [f"Total computed on one side only: {', '.join(o['total_missing_on_one_side'])}", ""]

    lines += ["## Per criterion", "", "| id | criterion | accuracy | recall by gold class | step MAE |",
              "|---|---|---|---|---|"]
    for cid, c in m["per_criterion"].items():
        rec = ", ".join(f"{k}: {_pct(v['recall'])} (n={v['support']})" for k, v in sorted(c["per_class"].items()))
        lines.append(f"| {cid} | {c['name']} | {_pct(c['accuracy'])} | {rec} | {_num(c['step_mae'])} |")
    lines.append("")

    for dim, groups in m["by_group"].items():
        lines += [f"## By {dim}", "", f"| {dim} | agreement | total MAE | n |", "|---|---|---|---|"]
        for k, v in groups.items():
            lines.append(f"| {k} | {_pct(v['agreement'])} | {_num(v['total']['mae'])} | {v['total']['n']} |")
        lines.append("")

    lines += ["## Largest disagreements", "", "| conversation | gold | pred | abs err | differing criteria |",
              "|---|---|---|---|---|"]
    for w in m["worst"]:
        d = "; ".join(f"{x['criterion']}: {x['gold']}→{x['pred']}" for x in w["diffs"]) or "—"
        lines.append(f"| {w['conversation_id']} | {_num(w['gold_total'])} | {_num(w['pred_total'])} "
                     f"| {_num(w['abs_err'])} | {d} |")
    return "\n".join(lines) + "\n"
