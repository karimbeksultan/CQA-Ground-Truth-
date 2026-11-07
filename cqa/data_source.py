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


def parse(payload: dict, group_id: str | None = None) -> list[Conversation]:
    data = payload.get("data") or {}
    records = data.get("records")
    if records is None:
        raise DataSourceError("response has no data.records")
    group = group_id or data.get("dataset_group_id")
    convs = []
    for rec in records:
        turns = [Turn(index=i, timestamp=t["timestamp"], speaker=t["speaker"], text=t["text"])
                 for i, t in enumerate(rec["transcript"])]
        convs.append(Conversation(
            conversation_id=rec["conversation_id"], channel=rec["channel"], language=rec["language"],
            transcript=turns, audio_url=rec.get("audio_url") or None, group_id=group,
        ))
    ids = [c.conversation_id for c in convs]
    if len(set(ids)) != len(ids):
        raise DataSourceError("duplicate conversation_id in response")
    return convs


def cache_path(group_id: str) -> Path:
    return RAW_DIR / f"{group_id}.json"


def load(group_id: str, refresh: bool = False) -> list[Conversation]:
    """Conversations for a group, from cache unless refresh=True or the cache is missing."""
    path = cache_path(group_id)
    if refresh or not path.exists():
        payload = fetch_raw(group_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
    return parse(payload, group_id)
