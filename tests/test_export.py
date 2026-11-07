"""Organizers' results.json (appendix to the guide, format 1.0)."""
import copy
import json
from pathlib import Path

import pytest

from cqa import checklists, data_source
from cqa.harness import export
from cqa.models import CriterionResult, Evaluation
from cqa.scoring import score

FIXTURE = Path(__file__).parent / "fixtures" / "sample_payload.json"


@pytest.fixture
def convs():
    return data_source.parse(json.loads(FIXTURE.read_text(encoding="utf-8")))


def _pred(conv, answers, *, model="gemma-4-31b-it", engine="gemini:gemma-4-31b-it"):
    cl = checklists.for_conversation(conv)
    results = [CriterionResult(criterion_id=cid, answer=a, explanation=f"{cid} ok",
                               evidence=[0] if cid != "c08" else [],
                               checked_range=(0, 2) if cid == "c08" else None)
               for cid, a in answers.items()]
    ev = Evaluation(conversation_id=conv.conversation_id, checklist_id=cl.id, checklist_version=cl.version,
                    evaluator="chain", model=model, results=results, usage={"engine": engine})
    return {"conversation_id": conv.conversation_id, "language": conv.language, "channel": conv.channel,
            "checklist_id": cl.id, "checklist_fingerprint": checklists.fingerprint(cl),
            "evaluation": ev.model_dump(mode="json"), "score": score(cl, ev.answers()).model_dump(mode="json"),
            "error": None, "latency_s": 1.0}


def _run_dir(tmp_path, preds, evaluator="chain"):
    d = tmp_path / "run"
    d.mkdir()
    (d / "run.json").write_text(json.dumps({"group_id": "T", "evaluator": evaluator}), encoding="utf-8")
    (d / "predictions.jsonl").write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in preds) + "\n",
                                         encoding="utf-8")
    return d


ANSWERS = {"c01": "yes", "c02": "na", "c03": "na", "c04": 15, "c05": "yes", "c06": 7, "c07": "yes", "c08": "no"}


def test_export_maps_codes_values_turns_and_score(tmp_path, convs):
    chat = convs[0]
    doc = export.build(_run_dir(tmp_path, [_pred(chat, ANSWERS)]), [chat], "Team")
    assert doc["schema_version"] == "1.0" and doc["team_name"] == "Team"
    assert doc["model"] == {"mode": "external_api", "name": "gemma-4-31b-it"}
    assert len(doc["results"]) == 1
    r = doc["results"][0]
    assert r["status"] == "ok"
    by = {c["criterion_id"]: c for c in r["criteria"]}
    assert list(by) == ["contact", "discovery", "identification", "consultation", "empathy", "dialogue",
                        "closure", "critical"]
    assert by["contact"]["value"] is True and by["discovery"]["value"] is None
    assert by["consultation"]["value"] == 15 and by["dialogue"]["value"] == 7
    assert by["critical"]["value"] is False and r["score"]["critical_flag"] is False
    assert by["contact"]["evidence_turns"] == [1]              # 0-based position 0 -> turn 1
    assert by["critical"]["evidence_turns"] == [1, 2, 3]       # checked range when there is no single turn
    # N/A weights (discovery 15 + identification 20) go to the rest: (10+15+15+10.5+5)/65*100
    assert r["score"]["final_score"] == 85.38


def test_failed_and_missing_dialogs_stay_in_the_file_as_errors(tmp_path, convs):
    chat, call = convs
    failed = {**_pred(chat, ANSWERS), "evaluation": None, "score": None, "error": "LLMError: HTTP 500"}
    doc = export.build(_run_dir(tmp_path, [failed]), [chat, call], "Team")
    by = {r["conversation_id"]: r for r in doc["results"]}
    assert by[chat.conversation_id] == {"conversation_id": chat.conversation_id, "status": "error",
                                        "criteria": [], "score": None, "error": "LLMError: HTTP 500"}
    assert by[call.conversation_id]["status"] == "error" and by[call.conversation_id]["score"] is None


def test_models_and_mode_follow_the_engines_that_answered(tmp_path, convs):
    chat, call = convs
    preds = [_pred(chat, ANSWERS), _pred(call, ANSWERS, model="qwen3:1.7b", engine="ollama:qwen3:1.7b")]
    doc = export.build(_run_dir(tmp_path, preds), [chat, call], "Team")
    assert doc["model"] == {"mode": "hybrid", "name": "gemma-4-31b-it, qwen3:1.7b"}


def test_gross_error_flag_is_separate_from_the_total(tmp_path, convs):
    chat = convs[0]
    doc = export.build(_run_dir(tmp_path, [_pred(chat, {**ANSWERS, "c08": "yes"})]), [chat], "Team")
    r = doc["results"][0]
    assert r["score"]["critical_flag"] is True and r["score"]["final_score"] == 85.38


def test_run_with_foreign_dialog_is_refused(tmp_path, convs):
    chat, call = convs
    with pytest.raises(export.ExportError, match="not in the group"):
        export.build(_run_dir(tmp_path, [_pred(chat, ANSWERS), _pred(call, ANSWERS)]), [chat], "Team")


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d["results"].append(copy.deepcopy(d["results"][0])), "duplicate"),
    (lambda d: d["results"][0]["criteria"].pop(), "exactly once"),
    (lambda d: d["results"][0]["criteria"][0].update(value=1), "not allowed"),        # 1 is not true
    (lambda d: d["results"][0]["criteria"][5].update(value=5), "not allowed"),        # not a dialogue step
    (lambda d: d["results"][0]["criteria"][0].update(evidence_turns=[0]), "from 1"),
    (lambda d: d["results"][0]["score"].update(final_score=85.385), "2 decimals"),
    (lambda d: d["results"][0]["score"].update(critical_flag=True), "critical_flag"),
    (lambda d: d.update(team_name=""), "team_name"),
    (lambda d: d["model"].update(mode="cloud"), "mode"),
])
def test_check_rejects_format_violations(tmp_path, convs, mutate, message):
    doc = export.build(_run_dir(tmp_path, [_pred(convs[0], ANSWERS)]), [convs[0]], "Team")
    bad = copy.deepcopy(doc)
    mutate(bad)
    with pytest.raises(export.ExportError, match=message):
        export.check(bad)


def test_check_requires_exactly_the_group_ids(tmp_path, convs):
    doc = export.build(_run_dir(tmp_path, [_pred(convs[0], ANSWERS)]), [convs[0]], "Team")
    with pytest.raises(export.ExportError, match="missing"):
        export.check(doc, expected_ids=[c.conversation_id for c in convs])


def test_unreadable_record_becomes_an_error_entry_not_a_lost_group(tmp_path, convs):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["data"]["records"].append({"conversation_id": "bad-001", "channel": "agent chat", "language": "RU",
                                       "transcript": [{"speaker": "client", "text": "нет метки времени"}]})
    payload["data"]["records"][0]["language"] = "ru"  # case differences are normalized, not rejected
    with pytest.raises(KeyError):
        data_source.parse(payload)                    # strict by default (uploads report the problem)
    rejected: dict = {}
    good = data_source.parse(payload, rejected=rejected)
    assert [c.conversation_id for c in good] == ["t-ru-001", "t-ru-call-001"] and good[0].language == "RU"
    assert list(rejected) == ["bad-001"] and "timestamp" in rejected["bad-001"]
    doc = export.build(_run_dir(tmp_path, [_pred(good[0], ANSWERS)]), good, "Team", rejected)
    by = {r["conversation_id"]: r for r in doc["results"]}
    assert by["bad-001"]["status"] == "error" and "timestamp" in by["bad-001"]["error"]
    assert by["t-ru-001"]["status"] == "ok" and by["t-ru-call-001"]["status"] == "error"


def test_group_from_a_file_is_cached_like_an_api_response(tmp_path, monkeypatch):
    monkeypatch.setattr(data_source, "RAW_DIR", tmp_path / "raw")
    records = json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]["records"]
    f = tmp_path / "prod.json"
    f.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    convs = data_source.import_file("PROD1", f)
    assert [c.conversation_id for c in convs] == ["t-ru-001", "t-ru-call-001"]
    assert [c.conversation_id for c in data_source.load("PROD1")] == ["t-ru-001", "t-ru-call-001"]


def test_record_variants_are_mapped_not_rejected():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rec = payload["data"]["records"][0]
    rec.update(channel="chat", language="kk")
    rec["transcript"][0]["speaker"] = "customer"
    rec["transcript"][1]["speaker"] = "moderator"   # unknown role: kept as context, never a lost dialog
    rejected: dict = {}
    conv = data_source.parse(payload, rejected=rejected)[0]
    assert not rejected and conv.channel == "agent chat" and conv.language == "KZ"
    assert conv.transcript[0].speaker == "client" and conv.transcript[1].speaker == "system"


def test_unknown_roles_without_an_operator_are_rejected_not_silently_scored():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rec = payload["data"]["records"][0]
    for t in rec["transcript"]:
        if t["speaker"] == "operator":
            t["speaker"] = "консультант"   # a role we do not know: the operator would vanish
    rejected: dict = {}
    convs = data_source.parse(payload, rejected=rejected)
    assert rec["conversation_id"] in rejected and "no operator" in rejected[rec["conversation_id"]]
    payload2 = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload2["data"]["records"][0]["transcript"][0]["speaker"] = "Оператор"   # known RU variant
    payload2["data"]["records"][0]["conversation_id"] = "ctl.2026-001"         # dots are fine
    payload2["data"]["records"][0]["transcript"][1]["text"] = None              # missing text is ""
    conv = data_source.parse(payload2)[0]
    assert conv.conversation_id == "ctl.2026-001" and conv.transcript[0].speaker == "operator"


def test_checklist_override_never_crosses_channels(convs):
    chat, call = convs
    assert checklists.for_conversation(chat, "chat_default_v1").id == "chat_default_v1"
    assert checklists.for_conversation(call, "chat_default_v1").id == "call_default_v1"
