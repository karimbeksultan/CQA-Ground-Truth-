"""Ground-truth (golden) labels: eval/goldens/<GROUP>.jsonl, one conversation per line.

Labels are answers only; the total is always recomputed by cqa.scoring, so gold totals
can never drift from the checklist rules. Splits:
  train - may be used as few-shot examples in prompts
  dev   - iterate prompts against it
  test  - sealed: report on it, do not tune on it
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from cqa import checklists
from cqa.models import Answer, Conversation

ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DIR = ROOT / "eval" / "goldens"
SPLIT_PATTERN = ("test", "dev", "train", "dev", "test")  # 40% test, 40% dev, 20% train

Split = Literal["train", "dev", "test"]


class GoldLabel(BaseModel):
    conversation_id: str
    checklist_id: str
    split: Split
    status: Literal["todo", "draft", "reviewed"] = "todo"
    answers: dict[str, Answer | None]
    notes: dict[str, str] = {}
    labeler: str | None = None

    def is_complete(self) -> bool:
        return all(v is not None for v in self.answers.values())

    def complete_answers(self) -> dict[str, Answer]:
        if not self.is_complete():
            raise ValueError(f"{self.conversation_id}: label is incomplete")
        return {k: v for k, v in self.answers.items() if v is not None}


def golden_path(group_id: str) -> Path:
    return GOLDEN_DIR / f"{group_id}.jsonl"


def load(path: Path) -> dict[str, GoldLabel]:
    labels: dict[str, GoldLabel] = {}
    # split on "\n" only: str.splitlines() would also break on U+2028 etc. inside JSON strings
    for n, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if not line.strip():
            continue
        label = GoldLabel.model_validate_json(line)
        if label.conversation_id in labels:
            raise ValueError(f"{path}:{n}: duplicate conversation_id {label.conversation_id}")
        labels[label.conversation_id] = label
    return labels


def save(path: Path, labels: list[GoldLabel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [l.model_dump_json(exclude_none=True) for l in sorted(labels, key=lambda l: l.conversation_id)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def assign_splits(convs: list[Conversation]) -> dict[str, Split]:
    """Deterministic split, stratified by (channel, language)."""
    strata: dict[tuple[str, str], list[str]] = defaultdict(list)
    for c in convs:
        strata[(c.channel, c.language)].append(c.conversation_id)
    splits: dict[str, Split] = {}
    for ids in strata.values():
        for i, cid in enumerate(sorted(ids)):
            splits[cid] = SPLIT_PATTERN[i % len(SPLIT_PATTERN)]  # type: ignore[assignment]
    return splits


def template(convs: list[Conversation], existing: dict[str, GoldLabel] | None = None) -> list[GoldLabel]:
    """Empty labels for every conversation; existing labels are kept untouched."""
    existing = existing or {}
    splits = assign_splits(convs)
    out = []
    for c in convs:
        if c.conversation_id in existing:
            out.append(existing[c.conversation_id])
            continue
        cl = checklists.for_conversation(c)
        out.append(GoldLabel(conversation_id=c.conversation_id, checklist_id=cl.id,
                             split=splits[c.conversation_id],
                             answers={cr.id: None for cr in cl.criteria}))
    return out


def dumps_line(label: GoldLabel) -> str:
    return json.dumps(label.model_dump(mode="json", exclude_none=True), ensure_ascii=False)
