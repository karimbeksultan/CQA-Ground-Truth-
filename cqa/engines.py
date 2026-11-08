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


class Voting:
    """Majority of N independent evaluations per criterion (self-consistency). Each vote is a full chain
    evaluation; per criterion the most common answer wins, a tie goes to the first vote's answer, and the
    explanation and evidence come from the first vote that gave the winning answer. Every vote's answers
    are kept in usage["votes"], so a split decision is visible on the evaluation."""

    name = "chain"

    def __init__(self, inner: FallbackChain, n: int):
        if n < 2:
            raise ValueError("voting needs at least 2 votes")
        self.inner, self.n = inner, n
        self.model = inner.model

    def evaluate(self, conv: Conversation, checklist: Checklist) -> Evaluation:
        from collections import Counter

        runs, errors = [], []
        for _ in range(self.n):
            try:
                runs.append(self.inner.evaluate(conv, checklist))
            except Exception as exc:  # a failed vote is recorded; the majority uses the votes that came back
                errors.append(f"{type(exc).__name__}: {str(exc)[:200]}")
        if not runs:
            raise ChainError("every vote failed: " + "; ".join(errors))
        results = []
        for crit in checklist.criteria:
            answers = [next(r for r in ev.results if r.criterion_id == crit.id).answer for ev in runs]
            counts = Counter(str(a) for a in answers)
            top = max(counts.values())
            winner = next(a for a in answers if counts[str(a)] == top)  # ties: the first vote's answer
            chosen = next(r for ev in runs for r in ev.results if r.criterion_id == crit.id and str(r.answer) == str(winner))
            results.append(chosen)
        usage: dict = {"engine": (runs[0].usage or {}).get("engine"), "votes": [
            {"model": ev.model, "engine": (ev.usage or {}).get("engine"),
             "answers": {r.criterion_id: r.answer for r in ev.results}} for ev in runs]}
        for k in ("prompt_tokens", "output_tokens", "thinking_tokens", "calls"):
            usage[k] = sum(int((ev.usage or {}).get(k) or 0) for ev in runs)
        if errors:
            usage["failed_votes"] = errors
        return runs[0].model_copy(update={"results": results, "usage": usage, "raw_response": runs[0].raw_response})


@register("chain")
def chain_factory(engines: str | None = None, model: str | None = None, votes: int = 1) -> FallbackChain | Voting:
    spec = engines or model
    chain = FallbackChain([EngineSpec.parse(s) for s in spec.split(",")] if spec else configured())
    return Voting(chain, int(votes)) if int(votes) > 1 else chain
