"""HTTP API for the CQA prototype.  Run:  uvicorn cqa.api:create_app --factory --port 8000

All errors share one shape: {"error": {"code": "...", "message": "..."}}. Long evaluations run as
background jobs (POST /api/evaluations -> job id, poll GET /api/jobs/{id}). The UI in web/ is served
at / when present. No auth (the guide says the demo needs none); CORS is limited to CQA_CORS_ORIGINS.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal, Union
from urllib.parse import urljoin

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from cqa import engines, evaluators
from cqa.data_source import DataSourceError
from cqa.models import SLUG_PATTERN, Checklist
from cqa.service import ConflictError, Service, audio_offsets, check_audio_url, extract_audio_src
from cqa.store import NotFound

ROOT = Path(__file__).resolve().parent.parent
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_PLAYER_PAGE_BYTES = 1024 * 1024
MAX_REDIRECTS = 3


class EvaluateRequest(BaseModel):
    conversation_ids: list[str] = Field(min_length=1, max_length=500)
    checklist_id: str | None = Field(default=None, pattern=SLUG_PATTERN)
    engines: list[str] | None = Field(default=None, max_length=5)


class AppealRequest(BaseModel):
    criterion_id: str = Field(pattern=SLUG_PATTERN)
    comment: str = Field(min_length=1, max_length=2000)


class DecisionRequest(BaseModel):
    decision: Literal["confirm", "change"]
    reason: str = Field(min_length=1, max_length=2000)
    new_answer: Union[Literal["yes", "no", "na"], int, None] = None


def create_app(service: Service | None = None) -> FastAPI:
    load_dotenv(ROOT / ".env")
    svc = service or Service()
    app = FastAPI(title="CQA — Conversation Quality Analytics", version="0.1.0")
    app.state.service = svc
    origins = [o.strip() for o in os.environ.get("CQA_CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "PUT"],
                       allow_headers=["Content-Type"])

    def error(status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})

    # A public demo URL must not let anyone spend the LLM quota or read the organizer transcripts:
    # CQA_BASIC_AUTH="user:password" puts the whole app (UI and API, not /api/health) behind HTTP Basic.
    basic = os.environ.get("CQA_BASIC_AUTH", "").strip()
    if basic:
        import base64
        import secrets

        expected = "Basic " + base64.b64encode(basic.encode("utf-8")).decode("ascii")

        @app.middleware("http")
        async def basic_auth(request: Request, call_next):
            if request.url.path == "/api/health" or request.method == "OPTIONS":
                return await call_next(request)
            if not secrets.compare_digest(request.headers.get("authorization", "").encode(), expected.encode()):
                return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="CQA demo", charset="UTF-8"'})
            return await call_next(request)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # declared size checked before any body is read; chunked uploads are capped while streaming
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
            return error(413, "too_large", f"request limit is {MAX_UPLOAD_BYTES // 1024 // 1024} MB")
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(NotFound)
    async def not_found(_: Request, exc: NotFound):
        return error(404, "not_found", str(exc).strip("'"))

    @app.exception_handler(ConflictError)
    async def conflict(_: Request, exc: ConflictError):
        return error(409, "conflict", str(exc))

    @app.exception_handler(ValidationError)
    async def invalid_model(_: Request, exc: ValidationError):
        return error(422, "invalid", "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:10]))

    @app.exception_handler(ValueError)
    async def bad_request(_: Request, exc: ValueError):
        return error(400, "bad_request", str(exc))

    @app.exception_handler(DataSourceError)
    async def upstream(_: Request, exc: DataSourceError):
        return error(502, "data_source", str(exc))

    # ---- meta -----------------------------------------------------------------------------
    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/engines")
    def list_engines():
        return {"configured": [str(s) for s in engines.configured()], "available": evaluators.available()}

    # ---- checklists -----------------------------------------------------------------------
    @app.get("/api/checklists")
    def list_checklists():
        return [c.model_dump(mode="json") for c in svc.checklists()]

    @app.get("/api/checklists/{checklist_id}")
    def get_checklist(checklist_id: str):
        return svc.checklist(checklist_id).model_dump(mode="json")

    @app.post("/api/checklists", status_code=201)
    def create_checklist(body: dict):
        return svc.save_checklist(Checklist.model_validate(body), create=True).model_dump(mode="json")

    @app.put("/api/checklists/{checklist_id}")
    def update_checklist(checklist_id: str, body: dict):
        checklist = Checklist.model_validate(body)
        if checklist.id != checklist_id:
            raise ValueError("checklist id in the body must match the URL")
        return svc.save_checklist(checklist, create=False).model_dump(mode="json")

    # ---- conversations --------------------------------------------------------------------
    @app.post("/api/datasets/{group_id}/import")
    def import_group(group_id: str, refresh: bool = False):
        if not re.fullmatch(SLUG_PATTERN, group_id):
            raise ValueError("invalid group_id")
        convs = svc.import_group(group_id, refresh)
        return {"group_id": group_id, "imported": len(convs)}

    @app.post("/api/conversations/upload", status_code=201)
    async def upload(request: Request):
        if "application/json" not in request.headers.get("content-type", ""):
            return error(415, "unsupported_media_type", "send the transcript as application/json")
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                return error(413, "too_large", f"upload limit is {MAX_UPLOAD_BYTES // 1024 // 1024} MB")
            chunks.append(chunk)
        raw = b"".join(chunks)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"not valid JSON: {exc.msg}") from None
        convs = svc.upload(payload)
        return {"imported": [c.conversation_id for c in convs]}

    @app.get("/api/results")
    def results(group_id: str, team: str | None = None, download: bool = False):
        """results.json in the organizers' format for one group (same builder as `cqa export`)."""
        if not re.fullmatch(SLUG_PATTERN, group_id):
            raise ValueError("invalid group_id")
        team_name = (team or os.environ.get("CQA_TEAM_NAME", "")).strip()
        if not team_name:
            raise ValueError("team name is not set: CQA_TEAM_NAME in .env")
        doc = svc.results(group_id, team_name)
        headers = {"Content-Disposition": f'attachment; filename="results-{group_id}.json"'} if download else {}
        return JSONResponse(doc, headers=headers)

    @app.get("/api/conversations")
    def list_conversations(group_id: str | None = None):
        return svc.conversations(group_id)

    @app.get("/api/conversations/{conversation_id}")
    def get_conversation(conversation_id: str):
        conv = svc.conversation(conversation_id)
        offsets = audio_offsets(conv)
        body = conv.model_dump(mode="json")
        body.pop("audio_url", None)  # signed organizer link stays on the server; use /audio
        body["has_audio"] = bool(conv.audio_url)
        for t in body["transcript"]:
            t["audio_offset"] = offsets.get(t["index"])
        body["evaluations"] = [{"id": e["id"], "status": e["status"], "checklist_id": e["checklist_id"],
                                "checklist_version": e.get("checklist_version"), "created_at": e["created_at"],
                                "total": (e.get("final_score") or {}).get("total")}
                               for e in svc.store.list("evaluations", conversation_id=conversation_id)]
        return body

    @app.get("/api/conversations/{conversation_id}/audio")
    def get_audio(conversation_id: str, request: Request):
        conv = svc.conversation(conversation_id)
        if not conv.audio_url:
            raise NotFound(f"conversations/{conversation_id}/audio")
        client = httpx.Client(timeout=60, follow_redirects=False)

        def open_checked(url: str, headers: dict) -> httpx.Response:
            """GET with redirects followed by hand so every hop passes the host/IP check (SSRF guard)."""
            for _ in range(MAX_REDIRECTS + 1):
                check_audio_url(url)
                resp = client.send(client.build_request("GET", url, headers=headers), stream=True)
                if resp.status_code in (301, 302, 303, 307, 308) and "location" in resp.headers:
                    url = urljoin(url, resp.headers["location"])
                    resp.close()
                    continue
                return resp
            raise ValueError("too many redirects")

        try:
            # step 1: the organizer link is a player page; read at most 1 MB and take the recording link
            page = open_checked(conv.audio_url, {})
            source = conv.audio_url
            if "html" in page.headers.get("content-type", ""):
                buf = b""
                for chunk in page.iter_bytes():
                    buf += chunk
                    if len(buf) > MAX_PLAYER_PAGE_BYTES:
                        break
                page.close()
                source = extract_audio_src(buf[:MAX_PLAYER_PAGE_BYTES].decode("utf-8", "replace"))
                if not source:
                    client.close()
                    return error(502, "audio_unavailable", "player page has no recording link")
            else:
                page.close()
            # step 2: stream the recording, forwarding Range so the browser can seek
            headers = {"Range": request.headers["range"]} if "range" in request.headers else {}
            upstream = open_checked(source, headers)
        except (httpx.HTTPError, ValueError) as exc:
            client.close()
            msg = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            return error(502, "audio_unavailable", f"recording unavailable: {msg}")
        ctype = upstream.headers.get("content-type", "")
        if upstream.status_code not in (200, 206) or not ctype.startswith("audio/"):
            upstream.close(), client.close()
            return error(502, "audio_unavailable", f"recording source answered HTTP {upstream.status_code} ({ctype or 'no type'})")
        passthrough = {k: v for k, v in upstream.headers.items()
                       if k.lower() in ("content-type", "content-length", "content-range", "accept-ranges")}

        def body():
            try:
                yield from upstream.iter_bytes()
            finally:
                upstream.close(), client.close()

        return StreamingResponse(body(), status_code=upstream.status_code, headers=passthrough, media_type=ctype)

    # ---- evaluations & jobs ---------------------------------------------------------------
    @app.post("/api/evaluations", status_code=202)
    def evaluate(req: EvaluateRequest):
        return svc.start_job(req.conversation_ids, req.checklist_id, req.engines)

    @app.get("/api/jobs")
    def list_jobs():
        return svc.store.list("jobs")

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return svc.store.get("jobs", job_id)

    @app.get("/api/evaluations")
    def list_evaluations(conversation_id: str | None = None, job_id: str | None = None):
        where = {k: v for k, v in {"conversation_id": conversation_id, "job_id": job_id}.items() if v}
        return svc.store.list("evaluations", **where)

    @app.get("/api/evaluations/{evaluation_id}")
    def get_evaluation(evaluation_id: str):
        ev = svc.evaluation(evaluation_id)
        ev["appeal_details"] = [svc.store.get("appeals", a) for a in ev.get("appeals", [])]
        return ev

    # ---- appeals --------------------------------------------------------------------------
    @app.post("/api/evaluations/{evaluation_id}/appeals", status_code=201)
    def create_appeal(evaluation_id: str, req: AppealRequest):
        return svc.appeal(evaluation_id, req.criterion_id, req.comment)

    @app.get("/api/appeals")
    def list_appeals(status: Literal["open", "resolved"] | None = Query(default=None)):
        out = []
        for a in svc.appeals(status):
            conv = svc.conversation(a["conversation_id"])
            idx = set(a["disputed"]["evidence"])
            rng = a["disputed"].get("checked_range")
            if rng:
                idx |= set(range(rng[0], rng[1] + 1))
            a["fragment"] = [t.model_dump() for t in conv.transcript if t.index in idx][:40]
            out.append(a)
        return out

    @app.post("/api/appeals/{appeal_id}/decision")
    def decide(appeal_id: str, req: DecisionRequest):
        appeal = svc.decide(appeal_id, req.decision, req.reason, req.new_answer)
        return {"appeal": appeal, "evaluation": svc.evaluation(appeal["evaluation_id"])}

    web = ROOT / "web"
    if web.is_dir():
        app.mount("/", StaticFiles(directory=web, html=True), name="web")
    return app

