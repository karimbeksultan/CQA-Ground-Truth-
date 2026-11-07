import pytest

from cqa import checklists
from cqa.models import Checklist
from cqa.scoring import ScoringError, round_half_up, score

ALL_PASS = {"c01": "yes", "c02": "yes", "c03": "yes", "c04": 20, "c05": "yes", "c06": 10, "c07": "yes", "c08": "no"}


@pytest.fixture
def chat() -> Checklist:
    return checklists.load("chat_default_v1")


def test_all_pass_is_100(chat):
    s = score(chat, ALL_PASS)
    assert s.total == 100.0
    assert s.gross_error is False


def test_all_fail_is_0(chat):
    s = score(chat, {**ALL_PASS, "c01": "no", "c02": "no", "c03": "no", "c04": 0, "c05": "no", "c06": 0, "c07": "no"})
    assert s.total == 0.0


def test_na_redistributes_weight_and_scales_are_proportional(chat):
    # applicable weights 10+20+15+15+5 = 65; earned 10 + 15 + 15 + 10.5 + 0 = 50.5 -> 50.5/65*100
    answers = {**ALL_PASS, "c02": "na", "c03": "na", "c04": 15, "c06": 7, "c07": "no"}
    s = score(chat, answers)
    assert s.total == 77.69
    eff = {c.criterion_id: c.effective_weight for c in s.criteria}
    assert eff["c02"] == 0 and eff["c03"] == 0
    assert sum(eff.values()) == pytest.approx(100.0, abs=1e-3)  # display values are rounded to 4 dp


def test_gross_error_is_flagged_without_changing_total(chat):
    s = score(chat, {**ALL_PASS, "c08": "yes"})
    assert s.gross_error is True
    assert s.total == 100.0


@pytest.mark.parametrize("bad", [
    {"c01": "na"},          # N/A not allowed for c01
    {"c04": 12},            # not a scale step
    {"c06": "yes"},         # scale needs a number
    {"c08": "na"},          # flag has no N/A
    {"c04": True},          # bool is not a scale step even though True == 1
    {"c06": 10.0},          # float is not a scale step
])
def test_invalid_answers_rejected(chat, bad):
    with pytest.raises(ScoringError):
        score(chat, {**ALL_PASS, **bad})


def test_missing_and_unknown_criteria_rejected(chat):
    with pytest.raises(ScoringError):
        score(chat, {k: v for k, v in ALL_PASS.items() if k != "c05"})
    with pytest.raises(ScoringError):
        score(chat, {**ALL_PASS, "c99": "yes"})


def test_no_applicable_weight_gives_no_total():
    cl = Checklist.model_validate({
        "id": "x", "name": "x", "criteria": [
            {"id": "a", "name": "a", "type": "binary", "weight": 50, "na_allowed": True, "description": "d", "na_rule": "r"},
            {"id": "f", "name": "f", "type": "flag", "weight": 0, "description": "d"},
        ]})
    s = score(cl, {"a": "na", "f": "no"})
    assert s.total is None


def test_weights_not_summing_to_100_are_normalised():
    cl = Checklist.model_validate({
        "id": "x", "name": "x", "criteria": [
            {"id": "a", "name": "a", "type": "binary", "weight": 3, "description": "d"},
            {"id": "b", "name": "b", "type": "binary", "weight": 1, "description": "d"},
        ]})
    assert score(cl, {"a": "yes", "b": "no"}).total == 75.0


def test_sections(chat):
    s = score(chat, {**ALL_PASS, "c01": "no"})
    start = next(x for x in s.sections if x.section_id == "start")
    assert start.points == 15.0 and start.max_points == 25.0 and start.percent == 60.0
    assert all(x.section_id != "gross" for x in s.sections)


def test_exact_half_rounds_up_despite_float_error():
    # true total 201/20000*100 = 1.005 exactly; float arithmetic gives 1.00499... and would round down
    cl = Checklist.model_validate({
        "id": "x", "name": "x", "criteria": [
            {"id": "a", "name": "a", "type": "binary", "weight": 201, "description": "d"},
            {"id": "b", "name": "b", "type": "binary", "weight": 19799, "description": "d"},
        ]})
    assert score(cl, {"a": "yes", "b": "no"}).total == 1.01


def test_round_half_up():
    assert round_half_up(2.675, 2) == 2.68
    assert round_half_up(77.69230769, 2) == 77.69
