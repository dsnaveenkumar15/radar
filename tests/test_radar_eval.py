import pytest

from evals.radar_eval import CASES, MIN_PRECISION, MIN_RECALL, evaluate


def test_labeled_set_covers_both_ecosystems():
    assert len(CASES) >= 15 and {c["lang"] for c in CASES} == {"python", "typescript"}


def test_diff_engine_meets_recall_and_precision_gates():
    result = evaluate()
    assert result["recall"] >= MIN_RECALL and result["precision"] >= MIN_PRECISION, result["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_case_finds_every_labeled_break_and_flags_nothing_else(case):
    result = evaluate()["cases"][case["name"]]
    assert result["missed"] == [] and result["false_positives"] == [], result
