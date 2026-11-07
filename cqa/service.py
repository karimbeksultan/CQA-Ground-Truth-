"""Application service used by the API: dialogs, evaluations, background jobs, appeals.

Every evaluation stores a snapshot of the checklist it was graded with. Appeal decisions are
re-scored against that snapshot, so editing or replacing a criterion later never changes past
results; re-evaluating with the new checklist creates a new evaluation.

Appeals and decisions mutate one evaluation document, so they run under one service-wide lock
(read-check-write as a unit); the decision writes the evaluation before closing the appeal.
"""
from __future__ import annotations

import html
import ipaddress
import os
import re
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal
from urllib.parse import urlparse

from dotenv import load_dotenv

from cqa import checklists, data_source, engines
from cqa.evaluators.prompt import _parse_ts
from cqa.models import Answer, Checklist, Conversation
from cqa.scoring import ScoringError, score, validate_answer
from cqa.store import NotFound, Store, now

ROOT = Path(__file__).resolve().parent.parent
UPLOAD_GROUP = "UPLOAD"


class ConflictError(ValueError):
    pass


def _engine_factory(specs: list[str] | None):
    parsed = [engines.EngineSpec.parse(s) for s in specs] if specs else engines.configured()
    return engines.FallbackChain(parsed)


def cost_usd(usage: dict | None) -> float | None:
    """Price from CQA_PRICE_IN/OUT_PER_MTOK (USD per 1M tokens); 0 on a free tier (prices unset)."""
    load_dotenv(ROOT / ".env")
    try:
        p_in = float(os.environ.get("CQA_PRICE_IN_PER_MTOK", "0") or 0)
        p_out = float(os.environ.get("CQA_PRICE_OUT_PER_MTOK", "0") or 0)
    except ValueError:
        return None  # a malformed price must not fail the evaluation; cost is shown as unknown
    if not usage:
        return None
    out = (usage.get("output_tokens") or 0) + (usage.get("thinking_tokens") or 0)
    return round(((usage.get("prompt_tokens") or 0) * p_in + out * p_out) / 1e6, 6)


def audio_offsets(conv: Conversation) -> dict[int, float]:
    """Seconds into the call recording for each turn of a voice call.

    The recorder is paused during IVR transfers ("Запись ... приостановлены"), so paused time is
    subtracted; the first turn is 0. Turns without a parsable timestamp are omitted.
    """
    if conv.channel != "voice call":
        return {}
    out: dict[int, float] = {}
    start: datetime | None = None
    paused_since: datetime | None = None
    paused_total = 0.0
    for t in conv.transcript:
        ts = _parse_ts(t.timestamp)
        if ts is None:
            continue
        start = start or ts
        low = t.text.lower()
        if t.speaker == "system" and "приостановлен" in low and paused_since is None:
            paused_since = ts
        elif t.speaker == "system" and "возобновлен" in low and paused_since is not None:
            paused_total += (ts - paused_since).total_seconds()
            paused_since = None
        effective = (paused_since or ts) - start
        out[t.index] = round(max(0.0, effective.total_seconds() - paused_total), 2)
    return out


_AUDIO_SRC = re.compile(r"<(?:source|audio)\b[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.I)


def extract_audio_src(page: str) -> str | None:
    """The organizers' audio_url is an HTML player page; the recording is its <source src> (short-lived link)."""
    m = _AUDIO_SRC.search(page)
    if not m:
        return None
    src = html.unescape(m.group(1)).strip()
    return src if src.startswith("https://") else None


def allowed_audio_hosts() -> list[str]:
    """Hosts the audio proxy may contact: the organizer API host and Dynamics file storage, or CQA_AUDIO_HOSTS.
    A leading dot means "this domain and its subdomains"."""
    load_dotenv(ROOT / ".env")
    configured = [h.strip().lower() for h in os.environ.get("CQA_AUDIO_HOSTS", "").split(",") if h.strip()]
    if configured:
        return configured
    hosts = [".dynamics.com"]
    api_host = urlparse(os.environ.get("CQA_API_URL", "")).hostname
    if api_host:
        hosts.append(api_host.lower())
    return hosts


def check_audio_url(url: str, hosts: list[str] | None = None, resolve: Callable = socket.getaddrinfo) -> None:
    """Refuse anything but https to an allowed host that resolves to public addresses (SSRF guard)."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        raise ValueError("recording link must be https")
    allowed = hosts if hosts is not None else allowed_audio_hosts()
    if not any(host == h or (h.startswith(".") and host.endswith(h)) for h in allowed):
        raise ValueError(f"recording host {host!r} is not allowed")
    for info in resolve(host, 443):
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError(f"recording host {host!r} resolves to a non-public address")


class Service:
    def __init__(self, store: Store | None = None, engine_factory: Callable = _engine_factory,
                 checklist_dir: Path = checklists.CHECKLIST_DIR, workers: int = 2,
                 fetch: Callable[[str, bool], list[Conversation]] = data_source.load, resume_jobs: bool = True,
                 job_concurrency: int | None = None):
        load_dotenv(ROOT / ".env")
        self.job_concurrency = max(1, job_concurrency or int(os.environ.get("CQA_JOB_CONCURRENCY", "3") or 3))
        self.store = store or Store()
        self.engine_factory = engine_factory
        self.checklist_dir = checklist_dir
        self.fetch = fetch
        self._lock = threading.RLock()  # serialises appeal/decision read-modify-write on evaluations
        self.pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="cqa-job")
        # Jobs cut off by a restart continue from the first conversation they had not finished.
        # Assumes one server process per database (do not run uvicorn --reload/--workers >1 on it).
        if resume_jobs:
            for job in self.store.list("jobs"):
                if job["status"] in ("queued", "running"):
                    self.store.update("jobs", job["id"], status="queued", resumed_at=now())
                    self.pool.submit(self._run_job, job["id"])

    # ---- checklists -------------------------------------------------------------------------
    def checklists(self) -> list[Checklist]:
        return checklists.list_all(self.checklist_dir)

    def checklist(self, checklist_id: str) -> Checklist:
        try:
            return checklists.load(checklist_id, self.checklist_dir)
        except FileNotFoundError:
            raise NotFound(f"checklists/{checklist_id}") from None

    def save_checklist(self, checklist: Checklist, *, create: bool) -> Checklist:
        with self._lock:
            exists = (self.checklist_dir / f"{checklist.id}.json").exists()
            if create and exists:
                raise ConflictError(f"checklist {checklist.id!r} already exists")
            if not create and not exists:
                raise NotFound(f"checklists/{checklist.id}")
            if exists:
                old = self.checklist(checklist.id)
                same = checklists.fingerprint(old.model_copy(update={"version": checklist.version})) == \
                    checklists.fingerprint(checklist)
                if not same:  # any content change bumps the version, so evaluations stay traceable
                    checklist = checklist.model_copy(update={"version": old.version + 1})
            checklists.save(checklist, self.checklist_dir)
            return checklist

    # ---- conversations ----------------------------------------------------------------------
    def import_group(self, group_id: str, refresh: bool = False) -> list[Conversation]:
        convs = self.fetch(group_id, refresh)
        for c in convs:
            self.store.put("conversations", {**c.model_dump(mode="json"), "source": "api"}, doc_id=c.conversation_id)
        return convs

    def upload(self, payload: object) -> list[Conversation]:
        """Organizer response, {"records": [...]}, a list of records, or a single record.

        Uploaded dialogs never carry an audio link (recordings come only from the organizer API),
        and an id that already exists is rejected so past evaluations keep their transcript.
        """
        if isinstance(payload, list):
            body: dict = {"data": {"records": payload}}
        elif isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            body = payload
        elif isinstance(payload, dict) and "records" in payload:
            body = {"data": payload}
        elif isinstance(payload, dict):
            body = {"data": {"records": [payload]}}
        else:
            raise ValueError("upload must be a JSON object or a list of records")
        body["data"].setdefault("dataset_group_id", UPLOAD_GROUP)
        try:
            convs = data_source.parse(body)
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f"record is missing a required field or has a wrong type: {exc}") from None
        with self._lock:
            for c in convs:
                try:
                    self.store.get("conversations", c.conversation_id)
                except NotFound:
                    continue
                raise ConflictError(f"conversation {c.conversation_id!r} already exists")
            for c in convs:
                doc = {**c.model_dump(mode="json"), "audio_url": None, "source": "upload"}
                self.store.put("conversations", doc, doc_id=c.conversation_id)
        return [c.model_copy(update={"audio_url": None}) for c in convs]

    def conversation(self, conversation_id: str) -> Conversation:
        doc = self.store.get("conversations", conversation_id)
        for k in ("source", "created_at", "id", "updated_at"):
            doc.pop(k, None)
        return Conversation.model_validate(doc)

    def conversations(self, group_id: str | None = None) -> list[dict]:
        docs = self.store.list("conversations", **({"group_id": group_id} if group_id else {}))
        latest = self._latest_evaluations()
        out = []
        for d in docs:
            ev = latest.get(d["conversation_id"])
            out.append({"conversation_id": d["conversation_id"], "group_id": d.get("group_id"),
                        "channel": d["channel"], "language": d["language"], "turns": len(d["transcript"]),
                        "has_audio": bool(d.get("audio_url")), "source": d.get("source"),
                        "latest_evaluation": None if ev is None else {
                            "id": ev["id"], "status": ev["status"], "checklist_id": ev["checklist_id"],
                            "total": (ev.get("final_score") or {}).get("total"),
                            "gross_error": (ev.get("final_score") or {}).get("gross_error"),
                            # earned share per criterion (None = N/A) for the set summary on the list page
                            "criteria": {c["criterion_id"]: (None if c["points"] is None or not c["effective_weight"]
                                                             else round(c["points"] / c["effective_weight"], 4))
                                         for c in (ev.get("final_score") or {}).get("criteria", [])
                                         if c.get("weight")},
                            "open_appeals": self._open_appeals(ev["id"])}})
        return out

    def import_run(self, run_dir: Path, group_id: str | None = None) -> dict:
        """Store the evaluations of a CLI run (python -m cqa run) so the UI shows them. Each record keeps
        the run id as its source; checklists are snapshotted only when the run's fingerprint still matches.
        Dialogs must already be imported; failed predictions are skipped (the run report lists them)."""
        import json as _json

        from cqa.harness.runner import load_predictions

        meta = _json.loads((Path(run_dir) / "run.json").read_text(encoding="utf-8"))
        group = group_id or meta["group_id"]
        snapshots: dict[str, Checklist] = {}
        stored, skipped = 0, {}
        latest = {}
        for rec in load_predictions(Path(run_dir)):
            latest[rec["conversation_id"]] = rec
        for cid, rec in latest.items():
            if rec.get("error") or not rec.get("evaluation"):
                skipped[cid] = "failed in the run"
                continue
            try:
                self.store.get("conversations", cid)
            except NotFound:
                skipped[cid] = f"dialog not imported (import group {group} first)"
                continue
            cl_id = rec["checklist_id"]
            if cl_id not in snapshots:
                snapshots[cl_id] = self.checklist(cl_id)
            cl = snapshots[cl_id]
            if checklists.fingerprint(cl) != rec["checklist_fingerprint"]:
                skipped[cid] = f"checklist {cl_id} changed since the run"
                continue
            ev = rec["evaluation"]
            usage = ev.get("usage") or {}
            self.store.put("evaluations", {
                "conversation_id": cid, "checklist_id": cl.id, "checklist_version": cl.version,
                "checklist_fingerprint": rec["checklist_fingerprint"], "checklist": cl.model_dump(mode="json"),
                "job_id": None, "source": f"run:{meta['run_id']}", "status": "done", "error": None,
                "evaluation": ev, "engine": usage.get("engine"), "model": ev.get("model"), "cost_usd": cost_usd(usage),
                "score": rec["score"], "final_answers": {r["criterion_id"]: r["answer"] for r in ev["results"]},
                "final_score": rec["score"]})
            stored += 1
        return {"stored": stored, "skipped": skipped}

    def import_results(self, path: Path) -> dict:
        """Show a committed results.json (organizers' format) in the UI of a fresh deployment, where no CLI
        run exists. Dialogs that already have an evaluation are left alone, so this is safe on every boot.
        Each record is marked source="results.json"; new evaluations from the UI run live as usual."""
        import json as _json

        from cqa.harness.export import CODES
        from cqa.models import CriterionResult, Evaluation

        doc = _json.loads(Path(path).read_text(encoding="utf-8"))
        by_code = {code: cid for cid, code in CODES.items()}
        has_eval = {e["conversation_id"] for e in self.store.list("evaluations")}
        stored, skipped = 0, {}
        for r in doc.get("results", []):
            cid = r["conversation_id"]
            if r.get("status") != "ok":
                skipped[cid] = "error in the results file"
                continue
            if cid in has_eval:
                skipped[cid] = "already evaluated"
                continue
            try:
                conv = self.conversation(cid)
            except NotFound:
                skipped[cid] = "dialog not imported"
                continue
            cl = checklists.for_conversation(conv, None, self.checklist_dir)
            results = []
            for c in r["criteria"]:
                v = c["value"]
                answer = "yes" if v is True else "no" if v is False else "na" if v is None else v
                results.append(CriterionResult(criterion_id=by_code[c["criterion_id"]], answer=answer,
                                               explanation=c.get("rationale", ""),
                                               evidence=[i - 1 for i in c.get("evidence_turns", [])]))
            ev = Evaluation(conversation_id=cid, checklist_id=cl.id, checklist_version=cl.version, evaluator="chain",
                            model=(doc.get("model") or {}).get("name"), results=results,
                            usage={"source": "results.json"})
            try:
                sc = score(cl, ev.answers())
            except ScoringError as exc:
                skipped[cid] = f"does not fit the current checklist: {exc}"
                continue
            self.store.put("evaluations", {
                "conversation_id": cid, "checklist_id": cl.id, "checklist_version": cl.version,
                "checklist_fingerprint": checklists.fingerprint(cl), "checklist": cl.model_dump(mode="json"),
                "job_id": None, "source": "results.json", "status": "done", "error": None,
                "evaluation": ev.model_dump(mode="json"), "engine": None, "model": ev.model, "cost_usd": 0.0,
                "score": sc.model_dump(mode="json"), "final_answers": ev.answers(),
                "final_score": sc.model_dump(mode="json")})
            stored += 1
        return {"stored": stored, "skipped": skipped}

    def results(self, group_id: str, team_name: str) -> dict:
        """The organizers' results.json (format 1.0) for a group: the latest evaluation of every dialog,
        as the system produced it (appeal decisions are human corrections and are not exported)."""
        from cqa.harness import export

        docs = self.store.list("conversations", group_id=group_id)
        if not docs:
            raise NotFound(f"no dialogs imported for group {group_id!r}")
        convs = [self.conversation(d["conversation_id"]) for d in docs]
        by_conv: dict[str, list[dict]] = {}
        for ev in self.store.list("evaluations"):  # ordered by created_at
            by_conv.setdefault(ev["conversation_id"], []).append(ev)
        preds = {}
        for c in convs:
            evs = by_conv.get(c.conversation_id) or []
            if not evs:
                continue
            as_pred = lambda ev: {"evaluation": ev.get("evaluation"), "score": ev.get("score"),
                                  "error": None if ev["status"] == "done" else (ev.get("error") or "оценка не удалась")}
            # the newest evaluation that fits the organizers' 8 criterion codes: a live demo swap that adds
            # a new criterion (c09) or changes a type must not turn the submitted file into errors
            fitting = next((ev for ev in reversed(evs) if ev["status"] == "done"
                            and export.record(as_pred(ev), c)["status"] == "ok"), None)
            preds[c.conversation_id] = as_pred(fitting or evs[-1])
        return export.build_from_predictions(preds, convs, team_name, "chain")

    def _latest_evaluations(self) -> dict[str, dict]:
        latest: dict[str, dict] = {}
        for ev in self.store.list("evaluations"):
            latest[ev["conversation_id"]] = ev  # list is ordered by created_at
        return latest

    def _open_appeals(self, evaluation_id: str) -> int:
        return len(self.store.list("appeals", evaluation_id=evaluation_id, status="open"))

    # ---- evaluation -------------------------------------------------------------------------
    def evaluate(self, conversation_id: str, checklist_id: str | None = None,
                 engine_specs: list[str] | None = None, job_id: str | None = None) -> dict:
        record: dict = {"conversation_id": conversation_id, "checklist_id": checklist_id, "job_id": job_id}
        try:
            conv = self.conversation(conversation_id)
            checklist = checklists.for_conversation(conv, checklist_id, self.checklist_dir)
            record.update(checklist_id=checklist.id, checklist_version=checklist.version,
                          checklist_fingerprint=checklists.fingerprint(checklist),
                          checklist=checklist.model_dump(mode="json"))
            ev = self.engine_factory(engine_specs).evaluate(conv, checklist)
            sc = score(checklist, ev.answers())
            usage = ev.usage or {}
            record.update(status="done", error=None, evaluation=ev.model_dump(mode="json"),
                          engine=usage.get("engine"), model=ev.model, cost_usd=cost_usd(usage),
                          score=sc.model_dump(mode="json"), final_answers=ev.answers(),
                          final_score=sc.model_dump(mode="json"))
        except Exception as exc:  # stored as a failed evaluation, shown to the user, never swallowed
            record.update(status="failed", error=f"{type(exc).__name__}: {str(exc)[:500]}")
        return self.store.put("evaluations", record)

    def start_job(self, conversation_ids: list[str], checklist_id: str | None = None,
                  engine_specs: list[str] | None = None) -> dict:
        if not conversation_ids:
            raise ValueError("no conversations selected")
        for cid in conversation_ids:
            self.store.get("conversations", cid)  # fail fast on unknown ids
        if checklist_id:
            self.checklist(checklist_id)
        self.engine_factory(engine_specs)  # validate engine specs before queueing
        job = self.store.put("jobs", {"kind": "evaluate", "status": "queued", "conversation_ids": conversation_ids,
                                      "checklist_id": checklist_id, "engines": engine_specs,
                                      "total": len(conversation_ids), "done": 0, "failed": 0,
                                      "evaluation_ids": []})
        self.pool.submit(self._run_job, job["id"])
        return job

    def _run_job(self, job_id: str) -> None:
        try:
            job = self.store.update("jobs", job_id, status="running", started_at=now())
            # evaluations already done for this job: those listed on it plus any written just before a crash
            existing = {e["conversation_id"]: e for e in self.store.list("evaluations", job_id=job_id)}
            for eid in job.get("evaluation_ids", []):
                try:
                    e = self.store.get("evaluations", eid)
                except NotFound:
                    continue
                existing.setdefault(e["conversation_id"], e)
            ids = [existing[c]["id"] for c in job["conversation_ids"] if c in existing]
            done = sum(existing[c]["status"] == "done" for c in job["conversation_ids"] if c in existing)
            failed = len(ids) - done
            self.store.update("jobs", job_id, done=done, failed=failed, evaluation_ids=ids)
            todo = [c for c in job["conversation_ids"] if c not in existing]
            progress = threading.Lock()

            def one(cid: str) -> None:
                nonlocal done, failed
                rec = self.evaluate(cid, job["checklist_id"], job["engines"], job_id=job_id)
                with progress:
                    ids.append(rec["id"])
                    done += rec["status"] == "done"
                    failed += rec["status"] != "done"
                    self.store.update("jobs", job_id, done=done, failed=failed, evaluation_ids=list(ids))

            # a few dialogs in parallel: ~100 s per dialog with Gemma, kept under the per-minute limit
            with ThreadPoolExecutor(max_workers=self.job_concurrency, thread_name_prefix="cqa-eval") as pool:
                list(pool.map(one, todo))
            self.store.update("jobs", job_id, status="finished", finished_at=now())
        except Exception as exc:  # the job itself broke (e.g. storage): mark it, never leave it "running"
            self.store.update("jobs", job_id, status="failed", error=f"{type(exc).__name__}: {str(exc)[:500]}",
                              finished_at=now())

    def evaluation(self, evaluation_id: str) -> dict:
        ev = self.store.get("evaluations", evaluation_id)
        ev["open_appeals"] = self._open_appeals(evaluation_id)
        return ev

    # ---- appeals ----------------------------------------------------------------------------
    def appeal(self, evaluation_id: str, criterion_id: str, comment: str) -> dict:
        if not comment.strip():
            raise ValueError("comment is required")
        with self._lock:
            ev = self.evaluation(evaluation_id)
            if ev["status"] != "done":
                raise ValueError("only completed evaluations can be appealed")
            checklist = Checklist.model_validate(ev["checklist"])
            try:
                checklist.criterion(criterion_id)
            except KeyError:
                raise NotFound(f"criterion {criterion_id!r} in evaluation {evaluation_id}") from None
            if any(a["criterion_id"] == criterion_id
                   for a in self.store.list("appeals", evaluation_id=evaluation_id, status="open")):
                raise ConflictError(f"{criterion_id} already has an open appeal")
            result = next((r for r in ev["evaluation"]["results"] if r["criterion_id"] == criterion_id), None)
            if result is None:
                raise ConflictError(f"evaluation has no result for {criterion_id}")
            appeal = self.store.put("appeals", {
                "evaluation_id": evaluation_id, "conversation_id": ev["conversation_id"],
                "criterion_id": criterion_id, "comment": comment.strip(), "status": "open",
                "disputed": {"answer": ev["final_answers"][criterion_id], "explanation": result["explanation"],
                             "evidence": result["evidence"], "checked_range": result.get("checked_range")}})
            self.store.update("evaluations", evaluation_id, appeals=ev.get("appeals", []) + [appeal["id"]])
            return appeal

    def decide(self, appeal_id: str, decision: Literal["confirm", "change"], reason: str,
               new_answer: Answer | None = None) -> dict:
        if not reason.strip():
            raise ValueError("reason is required")
        if decision not in ("confirm", "change"):
            raise ValueError("decision must be confirm or change")
        with self._lock:
            appeal = self.store.get("appeals", appeal_id)
            if appeal["status"] != "open":
                raise ConflictError("appeal is already resolved")
            ev = self.store.get("evaluations", appeal["evaluation_id"])
            checklist = Checklist.model_validate(ev["checklist"])
            crit = checklist.criterion(appeal["criterion_id"])
            answers: dict = dict(ev["final_answers"])
            if decision == "change":
                if new_answer is None:
                    raise ValueError("new_answer is required to change the score")
                try:
                    validate_answer(crit, new_answer)
                except ScoringError as exc:
                    raise ValueError(str(exc)) from None
                if str(new_answer) == str(answers[crit.id]):
                    raise ValueError("new_answer equals the current answer; use decision=confirm")
                answers[crit.id] = new_answer
            final = score(checklist, answers)
            before = ev["final_score"]["total"]
            # evaluation first: if the process dies in between, the appeal stays open and can be decided again
            self.store.update("evaluations", ev["id"], final_answers=answers, final_score=final.model_dump(mode="json"))
            return self.store.update("appeals", appeal_id, status="resolved", decision=decision,
                                     new_answer=new_answer if decision == "change" else None,
                                     reason=reason.strip(), decided_at=now(),
                                     total_before=before, total_after=final.total)

    def appeals(self, status: str | None = None) -> list[dict]:
        return self.store.list("appeals", **({"status": status} if status else {}))
