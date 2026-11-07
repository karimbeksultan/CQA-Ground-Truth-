"""LLM evaluator: prompt from the checklist -> JSON per criterion -> strict validation.

Invalid output (missing/duplicate criteria, answers outside the checklist, turn numbers outside the
dialog) gets one repair round with the errors listed; if it is still invalid the evaluation fails
loudly and the runner records the error. Nothing is guessed or silently fixed.
"""
from __future__ import annotations

import json
import re

from cqa.evaluators import register
from cqa.evaluators.prompt import RESPONSE_SCHEMA, SYSTEM, user_prompt
from cqa.llm import LLMClient, Message
from cqa.models import Checklist, Conversation, CriterionResult, Evaluation
from cqa.scoring import ScoringError, validate_answer


class EvaluationError(RuntimeError):
    pass


def extract_json(text: str) -> dict:
    s = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    if fenced:
        s = fenced.group(1).strip()
    if not s.startswith("{"):
        start, end = s.find("{"), s.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object in response")
        s = s[start:end + 1]
    data = json.loads(s)
    if not isinstance(data, dict):
        raise ValueError("response JSON is not an object")
    return data


def validate(data: dict, conv: Conversation, checklist: Checklist) -> tuple[list[CriterionResult], list[str]]:
    errors: list[str] = []
    items = data.get("criteria")
    if not isinstance(items, list):
        return [], ["поле criteria отсутствует или не является списком"]
    n_turns = len(conv.transcript)
    seen: dict[str, CriterionResult] = {}

    def turn_ids(value: object) -> list[int] | None:
        """Strict: a list of real ints inside the dialog (no "3", 3.0 or True). The model sees turns
        numbered from 1 (as the organizers count them); internally turns are 0-based array positions."""
        if value is None:
            return []
        if not isinstance(value, list) or not all(type(i) is int and 1 <= i <= n_turns for i in value):
            return None
        return [i - 1 for i in value]

    for item in items:
        if not isinstance(item, dict):
            errors.append(f"элемент criteria не является объектом: {str(item)[:40]!r}")
            continue
        cid = str(item.get("criterion_id", "")).strip()
        try:
            crit = checklist.criterion(cid)
        except KeyError:
            errors.append(f"неизвестный критерий {cid!r}")
            continue
        if cid in seen:
            errors.append(f"{cid}: оценён дважды")
            continue
        raw_answer = item.get("answer")
        try:
            answer = CriterionResult(criterion_id=cid, answer=raw_answer).answer
            validate_answer(crit, answer)
        except (ValueError, ScoringError):
            errors.append(f"{cid}: недопустимый ответ {raw_answer!r}, допустимо: "
                          f"{', '.join(str(a) for a in crit.allowed_answers())}")
            continue
        evidence, rng = turn_ids(item.get("evidence")), turn_ids(item.get("checked_range"))
        if evidence is None or rng is None or (rng and (len(rng) != 2 or rng[0] > rng[1])):
            errors.append(f"{cid}: evidence — список номеров реплик 1–{n_turns}, "
                          "checked_range — [начало, конец] с началом не больше конца")
            continue
        if answer != "na" and not evidence and not rng:
            errors.append(f"{cid}: укажи реплики-доказательства (evidence) или проверенный участок (checked_range)")
            continue
        explanation = item.get("explanation")
        if not isinstance(explanation, str) or not explanation.strip():
            errors.append(f"{cid}: нужно объяснение" + (" причины N/A" if answer == "na" else ""))
            continue
        seen[cid] = CriterionResult(criterion_id=cid, answer=answer, explanation=explanation.strip(),
                                    evidence=evidence, checked_range=tuple(rng) if rng else None)
    missing = [c.id for c in checklist.criteria if c.id not in seen]
    if missing:
        errors.append(f"нет оценки для {', '.join(missing)}")
    return [seen[c.id] for c in checklist.criteria if c.id in seen], errors


class LLMJudge:
    def __init__(self, client: LLMClient, name: str, repair_rounds: int = 1):
        self.client = client
        self.name = name
        self.model = client.model
        self.repair_rounds = repair_rounds

    def evaluate(self, conv: Conversation, checklist: Checklist) -> Evaluation:
        messages = [Message("user", user_prompt(conv, checklist))]
        usage: dict = {"prompt_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "calls": 0, "served_models": []}
        raw = ""
        for round_ in range(self.repair_rounds + 1):
            reply = self.client.generate(SYSTEM, messages, RESPONSE_SCHEMA)
            usage["calls"] += 1
            if reply.served_model and reply.served_model not in usage["served_models"]:
                usage["served_models"].append(reply.served_model)
            for k in ("prompt_tokens", "output_tokens", "thinking_tokens"):
                usage[k] += int(reply.usage.get(k) or 0)
            raw = reply.text
            try:
                results, errors = validate(extract_json(raw), conv, checklist)
            except (ValueError, json.JSONDecodeError) as exc:
                results, errors = [], [f"ответ не является корректным JSON: {exc}"]
            if not errors:
                return Evaluation(conversation_id=conv.conversation_id, checklist_id=checklist.id,
                                  checklist_version=checklist.version, evaluator=self.name, model=self.model,
                                  results=results, usage=usage, raw_response=raw)
            if round_ < self.repair_rounds:
                messages += [Message("model", raw),
                             Message("user", "В ответе ошибки:\n- " + "\n- ".join(errors)
                                     + "\nВерни полный исправленный JSON по всем критериям.")]
        raise EvaluationError(f"invalid model output after {usage['calls']} calls ({usage}): "
                              f"{'; '.join(errors[:5])}; last response: {raw[:500]!r}")


@register("gemini")
def gemini_judge(model: str | None = None, **kwargs: object) -> LLMJudge:
    from cqa.llm.gemini import DEFAULT_MODEL, GeminiClient
    return LLMJudge(GeminiClient(model or DEFAULT_MODEL, **kwargs), name="gemini")


def _openai_compat_factory(provider: str):
    def factory(model: str | None = None, **kwargs: object) -> LLMJudge:
        from cqa.llm.openai_compat import OpenAICompatClient, resolve
        base, key = resolve(provider)
        return LLMJudge(OpenAICompatClient(model or "", base, key, provider=provider, **kwargs), name=provider)
    return factory


for _provider in ("groq", "cerebras", "openrouter", "mistral", "ollama", "freellmapi"):
    register(_provider)(_openai_compat_factory(_provider))
