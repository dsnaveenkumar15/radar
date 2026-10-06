# tests/test_radar_agent_eval.py
from pathlib import Path

from evals.radar_agent_eval import evaluate

CASES = Path(__file__).parent / "fixtures" / "radar_agent"


def test_offline_eval_meets_the_quality_bar():
    result = evaluate(CASES)
    assert result["cases"] == 8 and result["precision"] >= 0.9 and result["recall"] >= 0.8, result
