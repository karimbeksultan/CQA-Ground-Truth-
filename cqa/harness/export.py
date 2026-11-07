"""Results file for the organizers (appendix to the student guide, "Формат JSON 1.0").

    results/dev/results.json   all DEV dialogs
    results/prod/results.json  the 10 control dialogs

One object per file; one record per dialog of the group, with no gaps or duplicate ids. A dialog that
could not be evaluated stays in the file with status "error" and the reason (never a zero score).

Format rules encoded here:
  * criterion codes contact, discovery, identification, consultation, empathy, dialogue, closure, critical
    (our checklist ids c01..c08), every code exactly once per "ok" record;
  * value: true = Да, false = Нет, null = N/A, a number = scale step before weighting;
  * evidence_turns: numbered from 1 over the original transcript array (client, bot and events included);
  * score.final_score: 0..100, 2 decimals, N/A weight redistributed (cqa.scoring does this);
    score.critical_flag equals the value of "critical" and does not zero the total;
  * model.mode: local, external_api or hybrid; model.name lists every model that actually answered.
"""
from __future__ import annotations

import json
from pathlib import Path

from cqa.harness.runner import load_predictions
from cqa.models import Conversation

SCHEMA_VERSION = "1.0"
CODES = {"c01": "contact", "c02": "discovery", "c03": "identification", "c04": "consultation",
         "c05": "empathy", "c06": "dialogue", "c07": "closure", "c08": "critical"}
# allowed values per code (None = N/A); the appendix table, page 3
ALLOWED: dict[str, set] = {
    "contact": {True, False},
    "discovery": {True, False, None},
    "identification": {True, False, None},
    "consultation": {0, 5, 10, 15, 20, None},
    "empathy": {True, False},
    "dialogue": {0, 4, 7, 10},
    "closure": {True, False, None},
    "critical": {True, False},
}
LOCAL_EVALUATORS = {"ollama"}


class ExportError(ValueError):
    pass


def _value(answer: object) -> object:
    if answer == "yes":
        return True
    if answer == "no":
        return False
    if answer == "na":
        return None
    if type(answer) is int:
        return answer
    raise ExportError(f"unexpected answer {answer!r}")


def _evidence(res: dict, n_turns: int) -> list[int]:
    """1-based turn numbers. A missed action is grounded by the range where it was expected."""
    turns = list(res.get("evidence") or [])
    rng = res.get("checked_range")
    if not turns and rng:
        turns = list(range(rng[0], rng[1] + 1))
    out = sorted({i + 1 for i in turns})
    if any(not 1 <= i <= n_turns for i in out):
        raise ExportError(f"evidence outside the dialog (1..{n_turns}): {out}")
    return out


def _error_record(cid: str, reason: str) -> dict:
    return {"conversation_id": cid, "status": "error", "criteria": [], "score": None, "error": reason}


def record(rec: dict, conv: Conversation) -> dict:
    """One results entry from one prediction line of a run."""
    cid = conv.conversation_id
    if rec.get("error") or not rec.get("evaluation") or not rec.get("score"):
        return _error_record(cid, rec.get("error") or "нет результата оценки")
    ev, sc = rec["evaluation"], rec["score"]
    try:
        criteria = []
        for res in ev["results"]:
            code = CODES.get(res["criterion_id"])
            if code is None:
                raise ExportError(f"criterion {res['criterion_id']!r} has no code in the results format")
            criteria.append({"criterion_id": code, "value": _value(res["answer"]),
                             "rationale": (res.get("explanation") or "").strip(),
                             "evidence_turns": _evidence(res, len(conv.transcript))})
        if sc.get("total") is None:
            raise ExportError("no applicable criteria, total is undefined")
        out = {"conversation_id": cid, "status": "ok", "criteria": criteria,
               "score": {"final_score": round(float(sc["total"]), 2), "critical_flag": bool(sc["gross_error"])}}
        check_record(out)
        return out
    except (ExportError, KeyError, TypeError) as exc:
        return _error_record(cid, f"результат не приводится к формату: {exc}")


def _models(preds: list[dict], evaluator: str) -> tuple[str, str]:
    names, modes = [], set()
    for rec in preds:
        ev = rec.get("evaluation")
        if rec.get("error") or not ev:
            continue
        engine = (ev.get("usage") or {}).get("engine") or evaluator
        modes.add("local" if engine.split(":")[0] in LOCAL_EVALUATORS else "external_api")
        name = ev.get("model") or engine
        if name not in names:
            names.append(name)
    mode = "hybrid" if len(modes) > 1 else (modes.pop() if modes else
                                            ("local" if evaluator in LOCAL_EVALUATORS else "external_api"))
    return mode, ", ".join(names) or evaluator


def build(run_dir: Path, convs: list[Conversation], team_name: str,
          rejected: dict[str, str] | None = None) -> dict:
    """Results document for every dialog of the group: `convs` as the API returns them, plus
    `rejected` records that could not be read (listed as errors with the reason)."""
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    preds: dict[str, dict] = {}
    for r in load_predictions(run_dir):  # an interrupted run may list a dialog twice: keep the last
        preds[r["conversation_id"]] = r
    rejected = rejected or {}
    unknown = sorted(set(preds) - {c.conversation_id for c in convs} - set(rejected))
    if unknown:
        raise ExportError(f"run has dialogs that are not in the group: {unknown}")
    results = [record(preds[c.conversation_id], c) if c.conversation_id in preds
               else _error_record(c.conversation_id, "диалог не был оценён в этом запуске")
               for c in convs]
    results += [_error_record(cid, reason) for cid, reason in rejected.items()]
    mode, name = _models(list(preds.values()), meta["evaluator"])
    doc = {"schema_version": SCHEMA_VERSION, "team_name": team_name,
           "model": {"mode": mode, "name": name}, "results": results}
    check(doc, expected_ids=[c.conversation_id for c in convs] + list(rejected))
    return doc


def check_record(r: dict) -> None:
    cid = r.get("conversation_id")
    if not isinstance(cid, str) or not cid:
        raise ExportError("record without conversation_id")
    if r.get("status") == "error":
        if r.get("criteria") != [] or r.get("score") is not None or not r.get("error"):
            raise ExportError(f"{cid}: error records need criteria [], score null and a reason")
        return
    if r.get("status") != "ok":
        raise ExportError(f"{cid}: status must be ok or error")
    codes = [c.get("criterion_id") for c in r.get("criteria", [])]
    if sorted(codes) != sorted(ALLOWED):
        raise ExportError(f"{cid}: needs every criterion exactly once, got {codes}")
    values = {}
    for c in r["criteria"]:
        v = c.get("value")
        if v is not None and type(v) not in (bool, int):
            raise ExportError(f"{cid}/{c['criterion_id']}: bad value type {v!r}")
        # bool is an int in Python: compare type-aware so 1 never passes for true
        if not any(v is a or (type(v) is type(a) and v == a) for a in ALLOWED[c["criterion_id"]]):
            raise ExportError(f"{cid}/{c['criterion_id']}: value {v!r} not allowed")
        if not isinstance(c.get("rationale"), str):
            raise ExportError(f"{cid}/{c['criterion_id']}: rationale must be a string")
        turns = c.get("evidence_turns")
        if not isinstance(turns, list) or not all(type(i) is int and i >= 1 for i in turns):
            raise ExportError(f"{cid}/{c['criterion_id']}: evidence_turns must be turn numbers from 1")
        values[c["criterion_id"]] = v
    s = r.get("score") or {}
    fs = s.get("final_score")
    if type(fs) not in (int, float) or not 0 <= fs <= 100 or round(fs, 2) != fs:
        raise ExportError(f"{cid}: final_score must be 0..100 with at most 2 decimals, got {fs!r}")
    if s.get("critical_flag") is not values["critical"]:
        raise ExportError(f"{cid}: critical_flag must equal the value of critical")


def check(doc: dict, expected_ids: list[str] | None = None) -> None:
    """Validate a results document against the appendix; raises ExportError on the first problem."""
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise ExportError("schema_version must be '1.0'")
    if not isinstance(doc.get("team_name"), str) or not doc["team_name"].strip():
        raise ExportError("team_name is required")
    model = doc.get("model") or {}
    if model.get("mode") not in {"local", "external_api", "hybrid"} or not model.get("name"):
        raise ExportError("model needs mode (local/external_api/hybrid) and name")
    results = doc.get("results")
    if not isinstance(results, list) or not results:
        raise ExportError("results must be a non-empty list")
    ids = [r.get("conversation_id") for r in results]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ExportError(f"duplicate conversation ids: {dupes}")
    if expected_ids is not None and sorted(ids) != sorted(expected_ids):
        missing = sorted(set(expected_ids) - set(ids))
        extra = sorted(set(ids) - set(expected_ids))
        raise ExportError(f"ids differ from the group: missing {missing}, extra {extra}")
    for r in results:
        check_record(r)


def write(doc: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
