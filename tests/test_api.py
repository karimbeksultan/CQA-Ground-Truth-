import json
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cqa import checklists, data_source, evaluators
from cqa.api import create_app
from cqa.engines import ChainError, EngineSpec, FallbackChain
from cqa.service import Service, audio_offsets
from cqa.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "sample_payload.json"
JSON = {"Content-Type": "application/json"}


class FakeEngine:
    name, model = "fake", "fake-1"

    def __init__(self, fail: bool = False):
        self.fail = fail

    def evaluate(self, conv, checklist):
        if self.fail:
            raise RuntimeError("engine down")
        ev = evaluators.create("optimistic").evaluate(conv, checklist)
        return ev.model_copy(update={"usage": {"engine": "fake:fake-1", "prompt_tokens": 1000, "output_tokens": 500}})


@pytest.fixture
def env(tmp_path):
    cl_dir = tmp_path / "checklists"
    shutil.copytree(checklists.CHECKLIST_DIR, cl_dir)
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    state = {"fail": False}
    svc = Service(Store(tmp_path / "db.sqlite"), engine_factory=lambda specs: FakeEngine(state["fail"]),
                  checklist_dir=cl_dir, workers=1, fetch=lambda group, refresh: data_source.parse(payload, group))
    client = TestClient(create_app(svc))
    return client, svc, state, cl_dir


def wait_job(client, job_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] == "finished":
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {job}")


def evaluate(client, ids, checklist_id=None):
    r = client.post("/api/evaluations", json={"conversation_ids": ids, **({"checklist_id": checklist_id} if checklist_id else {})})
    assert r.status_code == 202, r.text
    job = wait_job(client, r.json()["id"])
    return [client.get(f"/api/evaluations/{e}").json() for e in job["evaluation_ids"]]


def test_health_and_unknown_ids_use_one_error_shape(env):
    client, *_ = env
    assert client.get("/api/health").json() == {"status": "ok"}
    r = client.get("/api/evaluations/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_checklist_create_update_versioning_and_validation(env):
    client, *_ = env
    base = client.get("/api/checklists/chat_default_v1").json()
    new = {**base, "id": "chat_custom", "name": "Свой"}
    assert client.post("/api/checklists", json=new).status_code == 201
    assert client.post("/api/checklists", json=new).status_code == 409
    same = client.put("/api/checklists/chat_custom", json=new).json()
    assert same["version"] == base["version"]  # no content change -> same version
    new["criteria"][0]["description"] = "Да — оператор назвал номер обращения."
    changed = client.put("/api/checklists/chat_custom", json=new).json()
    assert changed["version"] == base["version"] + 1
    bad = client.post("/api/checklists", json={**new, "id": "../evil"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid"
    assert client.put("/api/checklists/other", json=new).status_code == 400
    broken = {**new, "id": "broken", "criteria": [{**new["criteria"][0], "type": "scale"}]}
    assert client.post("/api/checklists", json=broken).status_code == 422


def test_import_upload_and_conversation_view_hides_signed_audio_url(env):
    client, *_ = env
    assert client.post("/api/datasets/TEST/import").json() == {"group_id": "TEST", "imported": 2}
    rec = json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]["records"][0]
    up = client.post("/api/conversations/upload", content=json.dumps({**rec, "conversation_id": "up-1"}), headers=JSON)
    assert up.status_code == 201 and up.json() == {"imported": ["up-1"]}
    assert client.post("/api/conversations/upload", content=b"{nope", headers=JSON).status_code == 400
    listing = {c["conversation_id"]: c for c in client.get("/api/conversations").json()}
    assert set(listing) == {"t-ru-001", "t-ru-call-001", "up-1"} and listing["up-1"]["source"] == "upload"
    call = client.get("/api/conversations/t-ru-call-001").json()
    assert "audio_url" not in call and call["has_audio"] is True
    assert [t["audio_offset"] for t in call["transcript"]] == [0.0, 1.46, 5.36]


def test_evaluation_job_stores_snapshot_score_and_cost(env):
    client, *_ = env
    client.post("/api/datasets/TEST/import")
    ev, = evaluate(client, ["t-ru-001"])
    assert ev["status"] == "done" and ev["final_score"]["total"] == 100.0
    assert ev["checklist"]["id"] == "chat_default_v1" and ev["checklist_fingerprint"]
    assert ev["engine"] == "fake:fake-1" and ev["cost_usd"] == 0.0
    listing = client.get("/api/conversations").json()
    assert next(c for c in listing if c["conversation_id"] == "t-ru-001")["latest_evaluation"]["total"] == 100.0


def test_failed_engine_is_stored_as_failed_evaluation(env):
    client, _, state, _ = env
    client.post("/api/datasets/TEST/import")
    state["fail"] = True
    ev, = evaluate(client, ["t-ru-001"])
    assert ev["status"] == "failed" and "engine down" in ev["error"]
    assert client.post(f"/api/evaluations/{ev['id']}/appeals", json={"criterion_id": "c07", "comment": "x"}).status_code == 400


def test_appeal_decision_recomputes_against_the_snapshot(env):
    client, _, _, cl_dir = env
    client.post("/api/datasets/TEST/import")
    ev, = evaluate(client, ["t-ru-001"])
    # edit the live checklist after the evaluation: c07 weight 5 -> 50 must NOT affect this evaluation
    live = json.loads((cl_dir / "chat_default_v1.json").read_text(encoding="utf-8"))
    live["criteria"][6]["weight"] = 50
    assert client.put("/api/checklists/chat_default_v1", json=live).status_code == 409  # defaults are read-only
    (cl_dir / "chat_default_v1.json").write_text(json.dumps(live, ensure_ascii=False), encoding="utf-8")

    ap = client.post(f"/api/evaluations/{ev['id']}/appeals", json={"criterion_id": "c07", "comment": "Не предложил помощь"})
    assert ap.status_code == 201 and ap.json()["disputed"]["answer"] == "yes"
    assert client.post(f"/api/evaluations/{ev['id']}/appeals", json={"criterion_id": "c07", "comment": "again"}).status_code == 409
    open_ = client.get("/api/appeals", params={"status": "open"}).json()
    assert len(open_) == 1 and open_[0]["fragment"] is not None

    bad = client.post(f"/api/appeals/{ap.json()['id']}/decision", json={"decision": "change", "new_answer": "maybe", "reason": "x"})
    assert bad.status_code == 422
    r = client.post(f"/api/appeals/{ap.json()['id']}/decision",
                    json={"decision": "change", "new_answer": "no", "reason": "Только прощание, без итога"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["appeal"]["status"] == "resolved" and body["appeal"]["total_before"] == 100.0
    assert body["evaluation"]["final_score"]["total"] == 95.0      # snapshot weight 5, not the edited 50
    assert body["evaluation"]["score"]["total"] == 100.0           # original score kept for the card
    assert body["evaluation"]["final_answers"]["c07"] == "no" and body["evaluation"]["open_appeals"] == 0
    again = client.post(f"/api/appeals/{ap.json()['id']}/decision", json={"decision": "confirm", "reason": "x"})
    assert again.status_code == 409


def test_decision_validation(env):
    client, *_ = env
    client.post("/api/datasets/TEST/import")
    ev, = evaluate(client, ["t-ru-001"])
    ap = client.post(f"/api/evaluations/{ev['id']}/appeals", json={"criterion_id": "c01", "comment": "x"}).json()
    na = client.post(f"/api/appeals/{ap['id']}/decision", json={"decision": "change", "new_answer": "na", "reason": "x"})
    assert na.status_code == 400 and "not allowed" in na.json()["error"]["message"]
    same = client.post(f"/api/appeals/{ap['id']}/decision", json={"decision": "change", "new_answer": "yes", "reason": "x"})
    assert same.status_code == 400
    ok = client.post(f"/api/appeals/{ap['id']}/decision", json={"decision": "confirm", "reason": "Оценка верна"})
    assert ok.json()["evaluation"]["final_score"]["total"] == 100.0


def test_new_checklist_applies_via_job(env):
    client, *_ = env
    client.post("/api/datasets/TEST/import")
    base = client.get("/api/checklists/chat_default_v1").json()
    base["id"] = "chat_control"
    base["criteria"][6] = {"id": "c07", "section": "flow", "name": "Предложение помощи", "type": "binary",
                           "weight": 5, "na_allowed": False, "description": "Да — оператор предложил дальнейшую помощь."}
    client.post("/api/checklists", json=base)
    ev, = evaluate(client, ["t-ru-001"], checklist_id="chat_control")
    assert ev["checklist_id"] == "chat_control" and ev["checklist"]["criteria"][6]["name"] == "Предложение помощи"
    assert client.post("/api/evaluations", json={"conversation_ids": ["t-ru-001"], "checklist_id": "missing"}).status_code == 404
    assert client.post("/api/evaluations", json={"conversation_ids": ["nope"]}).status_code == 404


def test_interrupted_job_resumes_on_restart(env, tmp_path):
    client, svc, _, cl_dir = env
    client.post("/api/datasets/TEST/import")
    # a job that was cut off after its first conversation
    first = svc.evaluate("t-ru-001")
    svc.store.put("jobs", {"kind": "evaluate", "status": "running", "conversation_ids": ["t-ru-001", "t-ru-call-001"],
                           "checklist_id": None, "engines": None, "total": 2, "done": 1, "failed": 0,
                           "evaluation_ids": [first["id"]]}, doc_id="j1")
    restarted = Service(svc.store, engine_factory=svc.engine_factory, checklist_dir=cl_dir, workers=1, fetch=svc.fetch)
    client2 = TestClient(create_app(restarted))
    job = wait_job(client2, "j1")
    assert job["done"] == 2 and len(job["evaluation_ids"]) == 2 and job["evaluation_ids"][0] == first["id"]
    assert len(svc.store.list("evaluations", conversation_id="t-ru-001")) == 1  # not evaluated twice


def test_upload_rules(env):
    client, *_ = env
    rec = json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]["records"][1]  # a call with an audio link
    assert client.post("/api/conversations/upload", content=json.dumps(rec)).status_code == 415
    assert client.post("/api/conversations/upload", content=json.dumps({**rec, "conversation_id": "u2"}),
                       headers=JSON).status_code == 201
    assert client.get("/api/conversations/u2").json()["has_audio"] is False  # uploads never carry audio links
    dup = client.post("/api/conversations/upload", content=json.dumps({**rec, "conversation_id": "u2"}), headers=JSON)
    assert dup.status_code == 409
    broken = {k: v for k, v in rec.items() if k != "transcript"}
    r = client.post("/api/conversations/upload", content=json.dumps({**broken, "conversation_id": "u3"}), headers=JSON)
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"
    assert client.post("/api/conversations/upload", content=b'"just a string"', headers=JSON).status_code == 400
    big = client.post("/api/conversations/upload", content=b"x", headers={**JSON, "Content-Length": str(6 * 1024 * 1024)})
    assert big.status_code == 413


def test_audio_url_guard():
    from cqa.service import check_audio_url
    public = lambda host, port: [(None, None, None, None, ("20.50.2.1", port))]
    private = lambda host, port: [(None, None, None, None, ("127.0.0.1", port))]
    hosts = [".dynamics.com", "api.example.org"]
    check_audio_url("https://org.crm11.dynamics.com/file?sig=x", hosts, public)
    check_audio_url("https://api.example.org/p", hosts, public)
    for bad in ("http://org.crm11.dynamics.com/f", "https://evil.com/f", "https://dynamics.com.evil.io/f",
                "file:///etc/passwd"):
        with pytest.raises(ValueError):
            check_audio_url(bad, hosts, public)
    with pytest.raises(ValueError, match="non-public"):
        check_audio_url("https://internal.dynamics.com/f", hosts, private)


def test_concurrent_decisions_keep_both_changes(env):
    import threading
    client, svc, *_ = env
    client.post("/api/datasets/TEST/import")
    ev, = evaluate(client, ["t-ru-001"])
    a1 = svc.appeal(ev["id"], "c07", "x")
    a2 = svc.appeal(ev["id"], "c05", "y")
    barrier = threading.Barrier(2)

    def decide(appeal_id):
        barrier.wait()
        svc.decide(appeal_id, "change", "r", "no")

    threads = [threading.Thread(target=decide, args=(a["id"],)) for a in (a1, a2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    final = svc.evaluation(ev["id"])
    assert final["final_answers"]["c07"] == "no" and final["final_answers"]["c05"] == "no"
    assert final["final_score"]["total"] == 80.0 and final["open_appeals"] == 0
    svc.appeal(ev["id"], "c07", "again")  # a resolved appeal does not block a new one
    with pytest.raises(ValueError):       # but two open appeals on one criterion are rejected
        svc.appeal(ev["id"], "c07", "and again")


def test_job_survives_missing_conversation_and_resume_does_not_duplicate(env):
    client, svc, _, cl_dir = env
    client.post("/api/datasets/TEST/import")
    # conversation removed after the job was queued -> recorded as failed, job still finishes
    svc.store.put("jobs", {"kind": "evaluate", "status": "queued", "conversation_ids": ["gone", "t-ru-001"],
                           "checklist_id": None, "engines": None, "total": 2, "done": 0, "failed": 0,
                           "evaluation_ids": []}, doc_id="j2")
    svc._run_job("j2")
    job = svc.store.get("jobs", "j2")
    assert job["status"] == "finished" and job["failed"] == 1 and job["done"] == 1
    # crash after storing an evaluation but before updating the job: resume must reuse it
    svc.store.put("jobs", {"kind": "evaluate", "status": "running", "conversation_ids": ["t-ru-001"],
                           "checklist_id": None, "engines": None, "total": 1, "done": 0, "failed": 0,
                           "evaluation_ids": []}, doc_id="j3")
    svc.evaluate("t-ru-001", job_id="j3")
    svc._run_job("j3")
    assert len(svc.store.list("evaluations", job_id="j3")) == 1
    assert svc.store.get("jobs", "j3")["done"] == 1


def test_job_evaluates_dialogs_in_parallel(tmp_path):
    import threading
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    recs = payload["data"]["records"]
    payload["data"]["records"] = [{**recs[0], "conversation_id": f"t-{i}"} for i in range(6)]
    live, peak, lock = [0], [0], threading.Lock()

    class Slow(FakeEngine):
        def evaluate(self, conv, checklist):
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            time.sleep(0.2)
            with lock:
                live[0] -= 1
            return super().evaluate(conv, checklist)

    svc = Service(Store(tmp_path / "db.sqlite"), engine_factory=lambda s: Slow(), workers=1, job_concurrency=3,
                  fetch=lambda g, r: data_source.parse(payload, g))
    client = TestClient(create_app(svc))
    client.post("/api/datasets/TEST/import")
    r = client.post("/api/evaluations", json={"conversation_ids": [f"t-{i}" for i in range(6)]})
    job = wait_job(client, r.json()["id"])
    assert job["done"] == 6 and len(set(job["evaluation_ids"])) == 6
    assert peak[0] == 3


def test_fallback_chain_records_failures_and_engine():
    conv = data_source.parse(json.loads(FIXTURE.read_text(encoding="utf-8")))[0]
    cl = checklists.for_conversation(conv)
    chain = FallbackChain([EngineSpec("optimistic", None), EngineSpec("optimistic", None)])
    chain._engines = {EngineSpec("optimistic", None): FakeEngine(fail=True)}
    chain.specs = [EngineSpec("optimistic", None), EngineSpec("optimistic", "second")]
    chain._engines[EngineSpec("optimistic", "second")] = FakeEngine()
    ev = chain.evaluate(conv, cl)
    assert ev.usage["engine"] == "optimistic:second"
    assert ev.usage["fallback_from"][0]["engine"] == "optimistic" and "engine down" in ev.usage["fallback_from"][0]["error"]
    chain._engines[EngineSpec("optimistic", "second")] = FakeEngine(fail=True)
    with pytest.raises(ChainError):
        chain.evaluate(conv, cl)
    with pytest.raises(ValueError):
        EngineSpec.parse("nope:model")


def test_extract_audio_src_from_player_page():
    from cqa.service import extract_audio_src
    page = ('<audio id="a" controls><source src="https://x.invalid/file/1?sp=r&amp;sig=abc&amp;rsct=audio%2Fmpeg" '
            'type="audio/mpeg"></audio>')
    assert extract_audio_src(page) == "https://x.invalid/file/1?sp=r&sig=abc&rsct=audio%2Fmpeg"
    assert extract_audio_src("<p>no player</p>") is None
    assert extract_audio_src('<source src="javascript:alert(1)">') is None


def test_audio_offsets_skip_paused_recording():
    from cqa.models import Conversation
    ts = lambda s: f"2026-11-03T13:00:{s:02d}.0+00:00"
    conv = Conversation(conversation_id="c", channel="voice call", language="RU", transcript=[
        {"index": 0, "timestamp": ts(0), "speaker": "system", "text": "Запись и транскрибирование запущены."},
        {"index": 1, "timestamp": ts(10), "speaker": "system", "text": "Запись и транскрибирование приостановлены."},
        {"index": 2, "timestamp": ts(30), "speaker": "system", "text": "Запись и транскрибирование возобновлены."},
        {"index": 3, "timestamp": ts(35), "speaker": "operator", "text": "Здравствуйте"},
    ])
    assert audio_offsets(conv) == {0: 0.0, 1: 10.0, 2: 10.0, 3: 15.0}


def test_results_endpoint_exports_the_latest_evaluation_of_every_dialog(env):
    client, svc, state, _ = env
    client.post("/api/datasets/TEST/import")
    assert client.get("/api/results", params={"group_id": "TEST", "team": "T"}).json()["results"][0]["status"] == "error"
    evaluate(client, ["t-ru-001"])
    r = client.get("/api/results", params={"group_id": "TEST", "team": "T", "download": "true"})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    doc = r.json()
    by = {x["conversation_id"]: x for x in doc["results"]}
    assert doc["schema_version"] == "1.0" and doc["team_name"] == "T"
    assert by["t-ru-001"]["status"] == "ok" and len(by["t-ru-001"]["criteria"]) == 8
    assert by["t-ru-call-001"] == {"conversation_id": "t-ru-call-001", "status": "error", "criteria": [],
                                    "score": None, "error": "диалог не был оценён"}
    assert client.get("/api/results", params={"group_id": "NOPE", "team": "T"}).status_code == 404


def test_cli_run_evaluations_can_be_shown_in_the_ui(env, tmp_path):
    from cqa.harness import runner
    client, svc, _, cl_dir = env
    client.post("/api/datasets/TEST/import")
    convs = [svc.conversation("t-ru-001"), svc.conversation("t-ru-call-001")]
    out = runner.run("TEST", "optimistic", convs=convs, gold_path=tmp_path / "none.jsonl", runs_dir=tmp_path / "runs",
                     run_id="r-ui", workers=1)
    res = svc.import_run(out)
    assert res == {"stored": 2, "skipped": {}}
    rows = {c["conversation_id"]: c for c in client.get("/api/conversations").json()}
    ev = rows["t-ru-001"]["latest_evaluation"]
    assert ev["status"] == "done" and ev["total"] == 100.0 and ev["criteria"]["c01"] == 1.0
    full = client.get(f"/api/evaluations/{ev['id']}").json()
    assert full["source"] == "run:r-ui" and full["checklist"]["id"] == "chat_default_v1"


def test_basic_auth_protects_everything_but_health(tmp_path, monkeypatch):
    monkeypatch.setenv("CQA_BASIC_AUTH", "jury:secret")
    svc = Service(Store(tmp_path / "db.sqlite"), engine_factory=lambda specs: FakeEngine(False), workers=1)
    client = TestClient(create_app(svc))
    assert client.get("/api/health").status_code == 200
    r = client.get("/api/conversations")
    assert r.status_code == 401 and "Basic" in r.headers["www-authenticate"]
    assert client.get("/api/conversations", auth=("jury", "wrong")).status_code == 401
    assert client.get("/api/conversations", auth=("jury", "secret")).status_code == 200


def test_results_file_seeds_a_fresh_deployment_once(env, tmp_path):
    client, svc, _, _ = env
    client.post("/api/datasets/TEST/import")
    evaluate(client, ["t-ru-001"])
    doc = client.get("/api/results", params={"group_id": "TEST", "team": "T"}).json()
    f = tmp_path / "results.json"
    f.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    # a fresh store: nothing evaluated yet
    fresh = Service(Store(tmp_path / "fresh.sqlite"), engine_factory=lambda specs: FakeEngine(False), workers=1,
                    checklist_dir=svc.checklist_dir, fetch=svc.fetch)
    fresh.import_group("TEST")
    res = fresh.import_results(f)
    assert res["stored"] == 1 and res["skipped"]["t-ru-call-001"] == "error in the results file"
    seeded = fresh.store.list("evaluations")[0]
    assert seeded["source"] == "results.json" and seeded["score"]["total"] == doc["results"][0]["score"]["final_score"]
    assert fresh.import_results(f)["stored"] == 0  # idempotent on every boot


def test_results_skip_a_demo_evaluation_that_does_not_fit_the_format(env):
    client, svc, _, _ = env
    client.post("/api/datasets/TEST/import")
    evaluate(client, ["t-ru-001"])
    base = client.get("/api/checklists/chat_default_v1").json()
    swapped = {**base, "id": "chat_live_swap", "name": "swap",
               "criteria": [c for c in base["criteria"] if c["id"] != "c05"]
               + [{**next(c for c in base["criteria"] if c["id"] == "c05"), "id": "c09", "name": "Новый критерий"}]}
    assert client.post("/api/checklists", json=swapped).status_code == 201
    evaluate(client, ["t-ru-001"], "chat_live_swap")  # newest evaluation now has c09, which has no code
    rec = next(r for r in client.get("/api/results", params={"group_id": "TEST", "team": "T"}).json()["results"]
               if r["conversation_id"] == "t-ru-001")
    assert rec["status"] == "ok" and {c["criterion_id"] for c in rec["criteria"]} >= {"empathy"}


def test_upload_needs_exact_json_and_stays_out_of_organizer_groups(env):
    client, svc, *_ = env
    rec = json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]["records"][0]
    body = json.dumps({"data": {"dataset_group_id": "DEV", "records": [{**rec, "conversation_id": "sneaky"}]}})
    assert client.post("/api/conversations/upload", content=body,
                       headers={"Content-Type": "text/plain; x=application/json"}).status_code == 415
    assert client.post("/api/conversations/upload", content=body, headers=JSON).status_code == 201
    assert svc.store.get("conversations", "sneaky")["group_id"] == "UPLOAD"


def test_jobs_are_capped(env, monkeypatch):
    client, *_ = env
    client.post("/api/datasets/TEST/import")
    monkeypatch.setenv("CQA_MAX_JOB_IDS", "1")
    r = client.post("/api/evaluations", json={"conversation_ids": ["t-ru-001", "t-ru-call-001"]})
    assert r.status_code == 400 and "at most 1" in r.text
