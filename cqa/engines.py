"""Engine specs and the fallback chain used by the API.

An engine spec is "<evaluator>[:<model>]", e.g. "gemini:gemma-4-31b-it", "ollama:qwen3:1.7b",
"optimistic". The chain tries engines in order and moves to the next one only when an engine fails
(network/quota/server errors after its own retries, or output that stays invalid). Every evaluation
records which engine produced it and which engines failed before it, so a fallback is never silent.
Accuracy runs in the harness still use one engine (python -m cqa run), so numbers stay comparable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from cqa import evaluators
from cqa.evaluators import register
from cqa.models import Checklist, Conversation, Evaluation

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENGINES = ("gemini:gemma-4-31b-it,gemini:gemini-3.5-flash,gemini:gemini-3.8-flash,"
                   "gemini:gemini-3-flash-preview,gemini:gemini-2.5-flash,ollama:qwen3:1.7b")
# every free-tier model has its own daily quota: more fallbacks = a Gemma outage cannot stop the control hour;
# the last resort is the local Ollama model (works with no internet; slow and lenient, so only when the cloud fails)


@dataclass(frozen=True)
class EngineSpec:
    evaluator: str
    model: str | None

    @classmethod
    def parse(cls, spec: str) -> "EngineSpec":
        name, _, model = spec.strip().partition(":")
        if name not in evaluators.available():
            raise ValueError(f"unknown engine {name!r}; available: {evaluators.available()}")
        return cls(name, model or None)

    def __str__(self) -> str:
        return f"{self.evaluator}:{self.model}" if self.model else self.evaluator

    def create(self) -> evaluators.Evaluator:
        kwargs: dict = {"model": self.model} if self.model else {}
        # D13: Gemma at temperature 0 (dev split: 96.5% / MAE 1.25 vs 95.0% / 2.40 at the default, same 25
        # dialogs). Gemini models keep their default: Google advises against lowering it for Gemini 3.
        if self.evaluator == "gemini" and (self.model or "").startswith("gemma"):
            kwargs["temperature"] = 0.0
        return evaluators.create(self.evaluator, **kwargs)


def configured() -> list[EngineSpec]:
    """Engines from CQA_ENGINES in .env (comma-separated, first = primary)."""
    load_dotenv(ROOT / ".env")
    raw = os.environ.get("CQA_ENGINES", "").strip() or DEFAULT_ENGINES
    return [EngineSpec.parse(s) for s in raw.split(",") if s.strip()]


class ChainError(RuntimeError):
    pass


class FallbackChain:
    name = "chain"

    def __init__(self, specs: list[EngineSpec]):
        if not specs:
            raise ValueError("at least one engine is required")
        self.specs = specs
        self.model = str(specs[0])
        self._engines: dict[EngineSpec, evaluators.Evaluator] = {}

    def _engine(self, spec: EngineSpec) -> evaluators.Evaluator:
        if spec not in self._engines:
            self._engines[spec] = spec.create()
        return self._engines[spec]

    def evaluate(self, conv: Conversation, checklist: Checklist) -> Evaluation:
        failures: list[dict] = []
        for spec in self.specs:
            try:
                ev = self._engine(spec).evaluate(conv, checklist)
            except Exception as exc:  # each failure is recorded; the next engine is tried
                failures.append({"engine": str(spec), "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
                continue
            usage = dict(ev.usage or {})
            usage["engine"] = str(spec)
            if failures:
                usage["fallback_from"] = failures
            return ev.model_copy(update={"usage": usage})
        raise ChainError("all engines failed: " + "; ".join(f"{f['engine']}: {f['error']}" for f in failures))


@register("chain")
def chain_factory(engines: str | None = None, model: str | None = None) -> FallbackChain:
    spec = engines or model
    return FallbackChain([EngineSpec.parse(s) for s in spec.split(",")] if spec else configured())
