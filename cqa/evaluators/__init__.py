"""Evaluator registry. An evaluator turns (conversation, checklist) into an Evaluation."""
from __future__ import annotations

from typing import Callable, Protocol

from cqa.models import Checklist, Conversation, Evaluation


class Evaluator(Protocol):
    name: str
    model: str | None

    def evaluate(self, conv: Conversation, checklist: Checklist) -> Evaluation: ...


_REGISTRY: dict[str, Callable[..., Evaluator]] = {}


def register(name: str) -> Callable[[Callable[..., Evaluator]], Callable[..., Evaluator]]:
    def deco(factory: Callable[..., Evaluator]) -> Callable[..., Evaluator]:
        _REGISTRY[name] = factory
        return factory
    return deco


def create(name: str, **kwargs: object) -> Evaluator:
    if name not in _REGISTRY:
        raise KeyError(f"unknown evaluator {name!r}; available: {sorted(_REGISTRY)}")
    return _REGISTRY[name](**kwargs)


def available() -> list[str]:
    return sorted(_REGISTRY)


from cqa.evaluators import baseline, llm_judge  # noqa: E402,F401  (registers built-ins)


def _register_chain() -> None:
    import cqa.engines  # noqa: F401  (registers "chain"; imported late to avoid a cycle)


_register_chain()
