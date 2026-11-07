"""Checklist storage: JSON files in checklists/, one file per checklist id."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from cqa.models import Checklist, Conversation

ROOT = Path(__file__).resolve().parent.parent
CHECKLIST_DIR = ROOT / "checklists"
DEFAULT_BY_CHANNEL = {"agent chat": "chat_default_v1", "voice call": "call_default_v1"}


def load(checklist_id: str, directory: Path = CHECKLIST_DIR) -> Checklist:
    path = directory / f"{checklist_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"checklist {checklist_id!r} not found in {directory}")
    return Checklist.model_validate_json(path.read_text(encoding="utf-8"))


def list_all(directory: Path = CHECKLIST_DIR) -> list[Checklist]:
    return [Checklist.model_validate_json(p.read_text(encoding="utf-8"))
            for p in sorted(directory.glob("*.json"))]


def save(checklist: Checklist, directory: Path = CHECKLIST_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{checklist.id}.json"
    path.write_text(checklist.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
    return path


def fingerprint(checklist: Checklist) -> str:
    """Stable hash of the checklist content; changes whenever any rule changes."""
    raw = json.dumps(checklist.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def for_conversation(conv: Conversation, override: str | None = None,
                     directory: Path = CHECKLIST_DIR) -> Checklist:
    """Explicit checklist id, or the channel default (chat vs call)."""
    return load(override or DEFAULT_BY_CHANNEL[conv.channel], directory)
