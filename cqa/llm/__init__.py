"""LLM provider clients. Each returns raw text plus token usage; parsing lives in the evaluator."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class LLMError(RuntimeError):
    pass


class FatalRunError(Exception):
    """Mixin: the whole run should stop (every further call would fail the same way)."""


class QuotaExhausted(LLMError, FatalRunError):
    pass


@dataclass
class Message:
    role: str  # "user" or "model"
    text: str


class ModelMismatch(LLMError, FatalRunError):
    """The API answered with a different model than the one pinned for the run."""


@dataclass
class LLMReply:
    text: str
    usage: dict = field(default_factory=dict)
    served_model: str | None = None  # model name the API reports it actually used


class LLMClient(Protocol):
    model: str

    def generate(self, system: str, messages: list[Message], json_schema: dict | None = None) -> LLMReply: ...
