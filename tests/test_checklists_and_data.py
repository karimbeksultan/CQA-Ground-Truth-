import json
from pathlib import Path

import pytest

from cqa import checklists, data_source
from cqa.models import Checklist, CriterionResult

FIXTURE = Path(__file__).parent / "fixtures" / "sample_payload.json"


@pytest.mark.parametrize("cid", ["chat_default_v1", "call_default_v1"])
def test_default_checklists_are_valid(cid):
    cl = checklists.load(cid)
    assert sum(c.weight for c in cl.criteria) == 100
    assert [c.id for c in cl.criteria] == [f"c0{i}" for i in range(1, 9)]


def test_save_roundtrip_and_fingerprint(tmp_path):
    cl = checklists.load("chat_default_v1")
    checklists.save(cl, tmp_path)
    again = checklists.load(cl.id, tmp_path)
    assert again == cl
    assert checklists.fingerprint(again) == checklists.fingerprint(cl)
    changed = cl.model_copy(update={"version": cl.version + 1})
    assert checklists.fingerprint(changed) != checklists.fingerprint(cl)


def test_checklist_rejects_bad_config():
    with pytest.raises(ValueError):
        Checklist.model_validate({"id": "x", "name": "x", "criteria": [
            {"id": "a", "name": "a", "type": "scale", "weight": 10, "description": "d"}]})  # no steps
    with pytest.raises(ValueError):
        Checklist.model_validate({"id": "x", "name": "x", "criteria": [
            {"id": "a", "name": "a", "type": "binary", "weight": 10, "description": "d", "section": "nope"}]})


def test_answer_normalisation():
    assert CriterionResult(criterion_id="c", answer="N/A").answer == "na"
    assert CriterionResult(criterion_id="c", answer=" Yes ").answer == "yes"
    assert CriterionResult(criterion_id="c", answer="15").answer == 15


def test_parse_payload_keeps_timestamps_and_indexes_turns():
    convs = data_source.parse(json.loads(FIXTURE.read_text(encoding="utf-8")))
    assert [c.conversation_id for c in convs] == ["t-ru-001", "t-ru-call-001"]
    chat, call = convs
    assert chat.group_id == "TEST" and chat.audio_url is None
    assert [t.index for t in chat.transcript] == list(range(6))
    assert call.transcript[0].timestamp == "2026-11-03T13:10:00.6388245+00:00"
    assert checklists.for_conversation(chat).id == "chat_default_v1"
    assert checklists.for_conversation(call).id == "call_default_v1"


def test_group_url_keeps_signed_query_intact():
    base = "https://x.invalid/invoke?api-version=1&sp=%2Ftriggers%2Fmanual%2Frun&sig=abc"
    assert data_source.group_url(base, "DEV") == base + "&group_id=DEV"
    assert data_source.group_url("https://x.invalid/q", "A B") == "https://x.invalid/q?group_id=A%20B"


def test_parse_rejects_duplicates_and_missing_records():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["data"]["records"].append(payload["data"]["records"][0])
    with pytest.raises(data_source.DataSourceError):
        data_source.parse(payload)
    with pytest.raises(data_source.DataSourceError):
        data_source.parse({"isSuccess": True, "data": {}})
