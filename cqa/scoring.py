"""Deterministic scoring from the checklist rules (CQA_Chat_Quality_Checklist, page 2).

1. N/A only where allowed.
2. yes = full weight, no = 0; scale = chosen step.
3. Scale steps map to points proportionally; the top step gives the full weight.
4. N/A criteria are dropped and their weight is redistributed proportionally,
   so the maximum stays 100.
5. Total is rounded to two decimals; no applicable weighted criteria -> no total.
   Gross errors (flag criteria) are reported separately and do not change the total.

Arithmetic is exact (fractions) until the final half-up rounding, so a true x.xx5 always
rounds up instead of depending on float accumulation.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from cqa.models import Answer, Checklist, Criterion, Score, ScoredCriterion, SectionScore

MAX_TOTAL = Fraction(100)


class ScoringError(ValueError):
    pass


def round_half_up(value: float | Fraction, digits: int) -> float:
    q = Decimal(1).scaleb(-digits)
    if isinstance(value, Fraction):
        exact = Decimal(value.numerator) / Decimal(value.denominator)
    else:
        exact = Decimal(repr(value))
    return float(exact.quantize(q, rounding=ROUND_HALF_UP))


def validate_answer(criterion: Criterion, answer: Answer) -> None:
    if criterion.type == "scale":
        well_typed = answer == "na" or type(answer) is int  # rejects True and 2.0
    else:
        well_typed = isinstance(answer, str)
    if not well_typed or answer not in criterion.allowed_answers():
        raise ScoringError(
            f"{criterion.id}: answer {answer!r} not allowed, expected one of {criterion.allowed_answers()}"
        )


def fraction(criterion: Criterion, answer: Answer) -> Fraction | None:
    """Share of the criterion weight earned; None for N/A."""
    validate_answer(criterion, answer)
    if answer == "na":
        return None
    if criterion.type == "scale":
        return Fraction(answer, criterion.scale_max)  # type: ignore[arg-type]
    return Fraction(1) if answer == "yes" else Fraction(0)


def score(checklist: Checklist, answers: dict[str, Answer]) -> Score:
    missing = [c.id for c in checklist.criteria if c.id not in answers]
    if missing:
        raise ScoringError(f"missing answers for {missing}")
    unknown = set(answers) - {c.id for c in checklist.criteria}
    if unknown:
        raise ScoringError(f"answers for unknown criteria {sorted(unknown)}")

    digits = checklist.rounding
    fractions = {c.id: fraction(c, answers[c.id]) for c in checklist.criteria}
    weighted = [c for c in checklist.criteria if c.type != "flag" and c.weight > 0]
    applicable = [c for c in weighted if fractions[c.id] is not None]
    applicable_ids = {c.id for c in applicable}
    applicable_weight = sum((Fraction(c.weight) for c in applicable), Fraction(0))
    scale_factor = MAX_TOTAL / applicable_weight if applicable_weight > 0 else Fraction(0)

    effective: dict[str, Fraction] = {}
    points: dict[str, Fraction | None] = {}
    for c in checklist.criteria:
        f = fractions[c.id]
        effective[c.id] = Fraction(c.weight) * scale_factor if c.id in applicable_ids else Fraction(0)
        points[c.id] = None if (f is None or c.type == "flag") else f * effective[c.id]

    scored = [ScoredCriterion(criterion_id=c.id, answer=answers[c.id], weight=c.weight,
                              effective_weight=round_half_up(effective[c.id], 4),
                              points=None if points[c.id] is None else round_half_up(points[c.id], 4))
              for c in checklist.criteria]

    total = None
    if applicable:
        total = round_half_up(sum((points[c.id] or Fraction(0) for c in applicable), Fraction(0)), digits)

    gross = any(answers[c.id] == "yes" for c in checklist.criteria if c.type == "flag")

    sections: list[SectionScore] = []
    for sec in checklist.sections:
        members = [c.id for c in checklist.criteria if c.section == sec.id and c.type != "flag"]
        if not members:
            continue
        pts = sum((points[m] or Fraction(0) for m in members), Fraction(0))
        mx = sum((effective[m] for m in members), Fraction(0))
        sections.append(SectionScore(
            section_id=sec.id,
            points=round_half_up(pts, digits),
            max_points=round_half_up(mx, digits),
            percent=round_half_up(pts / mx * 100, digits) if mx > 0 else None,
        ))

    return Score(total=total, gross_error=gross, criteria=scored, sections=sections)
