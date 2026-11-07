"""Domain models: conversations, checklists, evaluations, scores."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

Speaker = Literal["client", "operator", "bot", "system"]
Channel = Literal["agent chat", "voice call"]
Answer = Union[Literal["yes", "no", "na"], int]
# ids end up in file names and URLs: letters, digits, "_" and "-" only (no paths, no dots)
SLUG_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
Slug = Annotated[str, StringConstraints(pattern=SLUG_PATTERN)]
# conversation ids come from the organizer API: dots allowed, never used as file names
ConversationId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]


class Turn(BaseModel):
    index: int
    timestamp: str
    speaker: Speaker
    text: str


class Conversation(BaseModel):
    conversation_id: ConversationId
    channel: Channel
    language: Literal["RU", "KZ", "EN"]
    transcript: list[Turn]
    audio_url: str | None = None
    group_id: str | None = None


class ScaleStep(BaseModel):
    value: int
    description: str


class Section(BaseModel):
    id: Slug
    name: str


class Criterion(BaseModel):
    id: Slug
    name: str
    type: Literal["binary", "scale", "flag"]
    weight: float = Field(ge=0)
    na_allowed: bool = False
    description: str
    na_rule: str | None = None
    scale: list[ScaleStep] | None = None
    guidance: str | None = None
    section: Slug | None = None

    @model_validator(mode="after")
    def _check_type_fields(self) -> "Criterion":
        if self.type == "scale":
            if not self.scale:
                raise ValueError(f"{self.id}: scale criterion needs scale steps")
            values = [s.value for s in self.scale]
            if len(set(values)) != len(values):
                raise ValueError(f"{self.id}: duplicate scale values")
            if max(values) <= 0:
                raise ValueError(f"{self.id}: top scale step must be > 0")
        elif self.scale:
            raise ValueError(f"{self.id}: only scale criteria may define scale steps")
        if self.type == "flag" and self.weight != 0:
            raise ValueError(f"{self.id}: flag criteria carry no points (weight must be 0)")
        if self.na_allowed and not self.na_rule:
            raise ValueError(f"{self.id}: na_allowed requires na_rule")
        return self

    @property
    def scale_max(self) -> int:
        assert self.scale is not None
        return max(s.value for s in self.scale)

    def allowed_answers(self) -> list[Answer]:
        if self.type == "scale":
            base: list[Answer] = sorted((s.value for s in self.scale or []), reverse=True)
        else:
            base = ["yes", "no"]
        return base + (["na"] if self.na_allowed else [])


class Checklist(BaseModel):
    id: Slug
    name: str
    version: int = 1
    channel: Literal["chat", "call", "any"] = "any"
    rounding: int = 2
    sections: list[Section] = []
    criteria: list[Criterion]
    guidance: list[str] = []

    @model_validator(mode="after")
    def _check_refs(self) -> "Checklist":
        ids = [c.id for c in self.criteria]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate criterion ids")
        section_ids = {s.id for s in self.sections}
        for c in self.criteria:
            if c.section is not None and c.section not in section_ids:
                raise ValueError(f"{c.id}: unknown section {c.section!r}")
        return self

    def criterion(self, criterion_id: str) -> Criterion:
        for c in self.criteria:
            if c.id == criterion_id:
                return c
        raise KeyError(criterion_id)


class CriterionResult(BaseModel):
    criterion_id: str
    answer: Answer
    explanation: str = ""
    evidence: list[int] = []  # turn indices supporting the answer
    checked_range: tuple[int, int] | None = None  # where a missed action was expected
    confidence: float | None = None

    @field_validator("answer", mode="before")
    @classmethod
    def _normalize_answer(cls, v: object) -> object:
        if isinstance(v, str):
            s = v.strip().lower()
            if s in {"n/a", "na", "n.a."}:
                return "na"
            if s.lstrip("-").isdigit():
                return int(s)
            return s
        return v


class Evaluation(BaseModel):
    conversation_id: str
    checklist_id: str
    checklist_version: int
    evaluator: str
    model: str | None = None
    results: list[CriterionResult]
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    usage: dict | None = None
    raw_response: str | None = None  # model output as received, kept to show the evaluation really ran

    def answers(self) -> dict[str, Answer]:
        ids = [r.criterion_id for r in self.results]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"{self.conversation_id}: duplicate results for {dupes}")
        return {r.criterion_id: r.answer for r in self.results}


class ScoredCriterion(BaseModel):
    criterion_id: str
    answer: Answer
    weight: float
    effective_weight: float
    points: float | None


class SectionScore(BaseModel):
    section_id: str
    points: float
    max_points: float
    percent: float | None


class Score(BaseModel):
    total: float | None
    gross_error: bool
    criteria: list[ScoredCriterion]
    sections: list[SectionScore] = []
