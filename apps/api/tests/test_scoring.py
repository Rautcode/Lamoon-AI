"""Scoring is the deterministic verdict layer — it gets thorough checks."""
from app.modules.ats.scoring import (
    final_score,
    job_match,
    recommended_action,
    tier_of,
)


def test_job_match_full():
    m = job_match(["Python", "React", "AWS"], ["python", "react"], ["aws"], 3, 5)
    assert m.matched_skills == ["python", "react"]
    assert m.missing_skills == []
    assert m.match_pct == 100.0  # all required + preferred + exp met


def test_job_match_partial_and_missing():
    m = job_match(["Python"], ["python", "react", "aws"], [], 5, 2)
    assert m.missing_skills == ["react", "aws"]
    assert 0 < m.match_pct < 100


def test_final_and_tier_bands():
    assert tier_of(final_score(9.0, 100)) == "A"      # 0.6*9 + 0.4*10 = 9.4
    assert tier_of(final_score(7.0, 60)) == "B"       # 4.2 + 2.4 = 6.6
    assert tier_of(final_score(5.0, 50)) == "C"       # 3.0 + 2.0 = 5.0
    assert tier_of(final_score(2.0, 20)) == "D"       # 1.2 + 0.8 = 2.0


def test_actions():
    assert recommended_action("A") == "Immediate Interview"
    assert recommended_action("D") == "Reject"
