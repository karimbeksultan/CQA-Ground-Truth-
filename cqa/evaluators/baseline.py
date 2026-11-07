"""Non-LLM baselines. They set the floor every real evaluator must beat."""
from __future__ import annotations

from cqa.evaluators import register
from cqa.models import Checklist, Conversation, CriterionResult, Evaluation


@register("optimistic")
class OptimisticBaseline:
    """Everything passed: yes / top scale step, no gross errors."""

    name = "optimistic"
    model = None

    def evaluate(self, conv: Conversation, checklist: Checklist) -> Evaluation:
        results = []
        for c in checklist.criteria:
            if c.type == "scale":
                answer = c.scale_max
            elif c.type == "flag":
                answer = "no"
            else:
                answer = "yes"
            results.append(CriterionResult(criterion_id=c.id, answer=answer,
                                           explanation="baseline: assumes the operator passed"))
        return Evaluation(conversation_id=conv.conversation_id, checklist_id=checklist.id,
                          checklist_version=checklist.version, evaluator=self.name, results=results)
