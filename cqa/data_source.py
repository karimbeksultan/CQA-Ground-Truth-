"""Organizer API adapter: GET <CQA_API_URL>&group_id=<GROUP> -> list[Conversation].

The raw response is cached in data/raw/<GROUP>.json (gitignored). The API URL carries
a signature, so it lives only in .env.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

from cqa.models import Conversation, Turn

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"


class DataSourceError(RuntimeError):
    pass


def api_url() -> str:
    load_dotenv(ROOT / ".env")
    url = os.environ.get("CQA_API_URL", "").strip()
    if not url:
        raise DataSourceError("CQA_API_URL is not set (copy .env.example to .env and fill it in)")
    return url


def group_url(base: str, group_id: str) -> str:
    # Appended by hand: httpx `params=` re-encodes the signed query (%2F -> /) and the API answers 400.
    return f"{base}{'&' if '?' in base else '?'}group_id={quote(group_id, safe='')}"


def fetch_raw(group_id: str, timeout: float = 120.0) -> dict:
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(group_url(api_url(), group_id))
    except httpx.HTTPError as exc:
        # re-raised without the request URL: it carries the API signature
        raise DataSourceError(f"API request for group {group_id!r} failed: {type(exc).__name__}") from None
    if resp.status_code != 200:
        raise DataSourceError(f"API returned HTTP {resp.status_code} for group {group_id!r}")
    payload = resp.json()
    if not payload.get("isSuccess"):
        raise DataSourceError(f"API error for group {group_id!r}: {payload.get('error')!r}")
    return payload


# Variants an unseen control set might use, mapped to our values instead of losing the dialog.
CHANNELS = {"agent chat": "agent chat", "agent_chat": "agent chat", "chat": "agent chat", "web chat": "agent chat",
            "voice call": "voice call", "voice_call": "voice call", "call": "voice call", "voice": "voice call",
            "phone": "voice call"}
LANGUAGES = {"ru": "RU", "rus": "RU", "kz": "KZ", "kk": "KZ", "kaz": "KZ", "en": "EN", "eng": "EN"}
SPEAKERS = {"client": "client", "customer": "client", "user": "client", "operator": "operator", "agent": "operator",
            "bot": "bot", "assistant": "bot", "system": "system", "event": "system"}


def _speaker(value: object) -> str:
    return SPEAKERS.get(str(value).strip().lower(), "system")  # unknown roles are context, never a lost dialog


def _record(rec: dict, group: str | None) -> Conversation:
    turns = [Turn(index=i, timestamp=str(t["timestamp"]),
                  speaker=_speaker(t["speaker"]), text=t["text"])
             for i, t in enumerate(rec["transcript"])]
    channel = str(rec["channel"]).strip().lower()
    language = str(rec["language"]).strip().lower()
    return Conversation(
        conversation_id=rec["conversation_id"], channel=CHANNELS.get(channel, channel),
        language=LANGUAGES.get(language, language.upper()),
        transcript=turns, audio_url=rec.get("audio_url") or None, group_id=group,
    )


def parse(payload: dict, group_id: str | None = None,
          rejected: dict[str, str] | None = None) -> list[Conversation]:
    """Records -> conversations. Strict by default. With `rejected`, a record that cannot be read is
    skipped and listed there (id -> reason) so one bad record never loses the whole group: the
    results file still lists it with status "error"."""
    data = payload.get("data") or {}
    records = data.get("records")
    if records is None:
        raise DataSourceError("response has no data.records")
    group = group_id or data.get("dataset_group_id")
    convs = []
    for i, rec in enumerate(records):
        try:
            convs.append(_record(rec, group))
        except Exception as exc:
            if rejected is None:
                raise
            rid = rec.get("conversation_id") if isinstance(rec, dict) else None
            key = str(rid) if rid else f"record-{i + 1}"
            rejected[key] = f"запись не читается: {type(exc).__name__}: {str(exc)[:300]}"
    ids = [c.conversation_id for c in convs] + list(rejected or {})
    if len(set(ids)) != len(ids):
        raise DataSourceError("duplicate conversation_id in response")
    return convs


def cache_path(group_id: str) -> Path:
    return RAW_DIR / f"{group_id}.json"


def load(group_id: str, refresh: bool = False, rejected: dict[str, str] | None = None) -> list[Conversation]:
    """Conversations for a group, from cache unless refresh=True or the cache is missing."""
    path = cache_path(group_id)
    if refresh or not path.exists():
        payload = fetch_raw(group_id)
        if not (payload.get("data") or {}).get("records"):
            # not cached: a group fetched before the organizers publish it must not stay empty
            raise DataSourceError(f"group {group_id!r} returned no records (not published yet or a wrong id?)")
        _write_atomic(path, payload)
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
    return parse(payload, group_id, rejected)


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def import_file(group_id: str, file: Path) -> list[Conversation]:
    """A group delivered as a file (organizer response, {"records": [...]} or a list of records)
    instead of the API: validated, then cached exactly like an API response."""
    raw = json.loads(Path(file).read_text(encoding="utf-8-sig"))
    if isinstance(raw, list):
        payload = {"isSuccess": True, "data": {"dataset_group_id": group_id, "records": raw}}
    elif isinstance(raw, dict) and isinstance(raw.get("data"), dict):
        payload = raw
    elif isinstance(raw, dict) and "records" in raw:
        payload = {"isSuccess": True, "data": {**raw, "dataset_group_id": group_id}}
    else:
        raise DataSourceError("file must hold the organizer response, {\"records\": [...]} or a list of records")
    rejected: dict[str, str] = {}
    convs = parse(payload, group_id, rejected)
    if not convs and rejected:
        raise DataSourceError(f"no record could be read: {rejected}")
    _write_atomic(cache_path(group_id), payload)
    return convs
