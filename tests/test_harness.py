import json
from pathlib import Path

import pytest

from cqa import checklists, data_source, evaluators
from cqa.harness import ground_truth, runner
from cqa.harness.metrics import Pair, compare
from cqa.models import Evaluation

FIXTURE = Path(__file__).parent / "fixtures" / "sample_payload.json"
PASS = {"c01": "yes", "c02": "yes", "c03": "yes", "c04": 20, "c05": "yes", "c06": 10, "c07": "yes", "c08": "no"}


@pytest.fixture
def convs():
    return data_source.parse(json.loads(FIXTURE.read_text(encoding="utf-8")))


def test_metrics_per_criterion_and_totals():
    chat = checklists.load("chat_default_v1")
    pairs = [
        Pair("a", "RU", "agent chat", chat, pred=PASS, gold=PASS),
        Pair("b", "EN", "agent chat", chat, pred=PASS, gold={**PASS, "c07": "no", "c04": 10, "c08": "yes"}),
    ]
    m = compare(pairs)
    pc = m["per_criterion"]
    assert pc["c01"]["accuracy"] == 1.0
    assert pc["c07"]["accuracy"] == 0.5
    assert pc["c07"]["per_class"]["no"] == {"recall": 0.0, "support": 1}
    assert pc["c04"]["step_mae"] == 5.0
    # gold b: 100 - 10 (c04 half of 20) - 5 (c07) = 85 -> error 15; a: 0
    assert m["overall"]["total"]["mae"] == 7.5
    assert m["overall"]["total"]["bias"] == 7.5
    assert m["overall"]["gross_error"]["recall"] == 0.0
    assert m["by_group"]["language"]["EN"]["total"]["mae"] == 15.0
    assert m["worst"][0]["conversation_id"] == "b"


def test_gold_template_splits_are_deterministic_and_stratified(convs):
    labels = ground_truth.template(convs)
    assert {l.conversation_id: l.checklist_id for l in labels} == {
        "t-ru-001": "chat_default_v1", "t-ru-call-001": "call_default_v1"}
    assert all(not l.is_complete() for l in labels)
    assert ground_truth.assign_splits(convs) == ground_truth.assign_splits(list(reversed(convs)))


def test_run_end_to_end_with_gold(tmp_path, convs):
    gold_path = tmp_path / "gold.jsonl"
    labels = ground_truth.template(convs)
    labels[0].answers = {**PASS, "c07": "no"}  # chat labelled; call left incomplete
    ground_truth.save(gold_path, labels)

    out = runner.run("TEST", "optimistic", convs=convs, gold_path=gold_path, runs_dir=tmp_path / "runs",
                     run_id="r1", workers=2)
    meta = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert meta["n_ok"] == 2 and meta["errors"] == {}
    assert set(meta["checklists"]) == {"chat_default_v1", "call_default_v1"}

    preds = runner.load_predictions(out)
    assert preds[0]["score"]["total"] == 100.0
    Evaluation.model_validate(preds[0]["evaluation"])

    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["n_conversations"] == 1
    assert metrics["skipped"]["gold_incomplete"] == ["t-ru-call-001"]
    assert metrics["overall"]["total"]["mae"] == 5.0
    assert "Per criterion" in (out / "report.md").read_text(encoding="utf-8")


def test_evaluator_failures_are_recorded_not_swallowed(tmp_path, convs):
    @evaluators.register("boom")
    class Boom:
        name, model = "boom", None

        def evaluate(self, conv, checklist):
            raise RuntimeError("model unavailable")

    out = runner.run("TEST", "boom", convs=convs, gold_path=tmp_path / "none.jsonl",
                     runs_dir=tmp_path / "runs", run_id="r2")
    meta = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert meta["n_ok"] == 0
    assert meta["errors"]["t-ru-001"] == "RuntimeError: model unavailable"
    assert not (out / "metrics.json").exists()


def test_duplicate_criterion_results_rejected():
    ev = Evaluation(conversation_id="x", checklist_id="c", checklist_version=1, evaluator="t",
                    results=[{"criterion_id": "c01", "answer": "yes"}, {"criterion_id": "c01", "answer": "no"}])
    with pytest.raises(ValueError):
        ev.answers()


def test_gold_with_unicode_line_separator_roundtrips(tmp_path, convs):
    labels = ground_truth.template(convs)
    labels[0].notes = {"c04": "line separator\u0085next"}
    path = tmp_path / "g.jsonl"
    ground_truth.save(path, labels)
    assert ground_truth.load(path)["t-ru-001"].notes["c04"] == "line separator\u0085next"


def test_unknown_checklist_fails_before_creating_run_dir(tmp_path, convs):
    with pytest.raises(FileNotFoundError):
        runner.run("TEST", "optimistic", convs=convs, checklist_override="nope", gold_path=tmp_path / "none.jsonl",
                   runs_dir=tmp_path / "runs", run_id="r3")
    assert not (tmp_path / "runs" / "r3").exists()


def test_invalid_gold_and_changed_checklist_are_skipped_with_reason(tmp_path, convs, monkeypatch):
    gold_path = tmp_path / "gold.jsonl"
    labels = ground_truth.template(convs)
    labels[0].answers = {**PASS, "c01": "na"}  # N/A not allowed on c01
    labels[1].answers = dict(PASS)
    ground_truth.save(gold_path, labels)
    out = runner.run("TEST", "optimistic", convs=convs, gold_path=gold_path, runs_dir=tmp_path / "runs", run_id="r4")
    skipped = json.loads((out / "metrics.json").read_text(encoding="utf-8"))["skipped"]
    assert skipped["gold_invalid"] == ["t-ru-001"]

    real_load = checklists.load
    monkeypatch.setattr(checklists, "load", lambda cid, *a, **k: real_load(cid, *a, **k).model_copy(update={"version": 9}))
    m = runner.score_run(out, ground_truth.load(gold_path))
    assert m["skipped"]["checklist_changed"] == ["t-ru-001", "t-ru-call-001"]


def test_daily_quota_aborts_the_rest_of_the_run(tmp_path, convs):
    from cqa.llm import QuotaExhausted

    @evaluators.register("quota")
    class Quota:
        name, model = "quota", None
        calls = 0

        def evaluate(self, conv, checklist):
            Quota.calls += 1
            raise QuotaExhausted("daily free-tier quota exhausted")

    out = runner.run("TEST", "quota", convs=convs, gold_path=tmp_path / "none.jsonl",
                     runs_dir=tmp_path / "runs", run_id="r5", workers=1)
    meta = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert Quota.calls == 1 and meta["aborted"] is True
    assert meta["errors"]["t-ru-call-001"].startswith("skipped: run aborted")


def test_import_labels_validates_whole_batch_before_writing(tmp_path, convs):
    path = tmp_path / "gold.jsonl"
    ground_truth.save(path, ground_truth.template(convs))
    good = {"conversation_id": "t-ru-001", "answers": {**PASS, "c07": "no"}, "notes": {"c07": "нет предложения помощи"},
            "review": ["c07"]}
    bad = {"conversation_id": "t-ru-call-001", "answers": {**PASS, "c01": "na"}}
    with pytest.raises(ValueError, match="nothing written"):
        ground_truth.import_labels(path, [good, bad], labeler="t")
    assert not ground_truth.load(path)["t-ru-001"].is_complete()

    assert ground_truth.import_labels(path, [good], labeler="t") == ["t-ru-001"]
    label = ground_truth.load(path)["t-ru-001"]
    assert label.status == "draft" and label.review == ["c07"] and label.answers["c07"] == "no"
    assert label.split == ground_truth.assign_splits(convs)["t-ru-001"]


def test_rerun_retries_only_failed_with_same_settings(tmp_path, convs):
    calls: list[str] = []

    @evaluators.register("flaky")
    class Flaky:
        name = "flaky"

        def __init__(self, model=None):
            self.model = model

        def evaluate(self, conv, checklist):
            calls.append(conv.conversation_id)
            if conv.conversation_id == "t-ru-001" and calls.count("t-ru-001") == 1:
                raise RuntimeError("HTTP 503 busy")
            return evaluators.create("optimistic").evaluate(conv, checklist).model_copy(
                update={"evaluator": "flaky", "model": self.model})

    out = runner.run("TEST", "flaky", convs=convs, gold_path=tmp_path / "none.jsonl", runs_dir=tmp_path / "runs",
                     run_id="r6", workers=1, evaluator_kwargs={"model": "m-9"})
    assert json.loads((out / "run.json").read_text(encoding="utf-8"))["n_ok"] == 1
    res = runner.rerun_failed(out, workers=1, convs=convs, gold_path=tmp_path / "none.jsonl")
    meta = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert res == {"retried": 1, "fixed": 1} and meta["n_ok"] == 2 and meta["errors"] == {}
    assert calls == ["t-ru-001", "t-ru-call-001", "t-ru-001"]  # the successful one was not re-evaluated
    preds = runner.load_predictions(out)
    assert [p["conversation_id"] for p in preds] == ["t-ru-001", "t-ru-call-001"]
    assert preds[0]["evaluation"]["model"] == "m-9" and len(meta["reruns"]) == 1


def test_predictions_survive_an_interrupted_run_and_rerun_completes_it(tmp_path, convs):
    seen_on_disk: list[int] = []
    out_dir = tmp_path / "runs" / "r7"

    @evaluators.register("watcher")
    class Watcher:
        name = "watcher"
        model = None

        def evaluate(self, conv, checklist):
            # with one worker the first dialog must already be on disk when the second one starts
            seen_on_disk.append(len(runner.load_predictions(out_dir)))
            return evaluators.create("optimistic").evaluate(conv, checklist).model_copy(update={"evaluator": "watcher"})

    out = runner.run("TEST", "watcher", convs=convs, gold_path=tmp_path / "none.jsonl", runs_dir=tmp_path / "runs",
                     run_id="r7", workers=1)
    assert seen_on_disk == [0, 1]
    meta = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert meta["status"] == "finished" and meta["selected"] == ["t-ru-001", "t-ru-call-001"]

    # simulate a crash after the first dialog: only one line written, run.json still "running"
    first = (out / "predictions.jsonl").read_text(encoding="utf-8").split("\n")[0]
    (out / "predictions.jsonl").write_text(first + "\n", encoding="utf-8")
    meta.update(status="running")
    (out / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    res = runner.rerun_failed(out, workers=1, convs=convs, gold_path=tmp_path / "none.jsonl")
    assert res == {"retried": 1, "fixed": 1}
    preds = runner.load_predictions(out)
    assert [p["conversation_id"] for p in preds] == ["t-ru-001", "t-ru-call-001"]
    meta = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert meta["status"] == "finished" and meta["n_ok"] == 2


def test_split_requires_gold(convs, tmp_path):
    with pytest.raises(ValueError):
        runner.run("TEST", "optimistic", convs=convs, split="dev", gold_path=tmp_path / "none.jsonl",
                   runs_dir=tmp_path / "runs")
