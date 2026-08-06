"""ATS scoring — pure, deterministic domain logic. This is the rule-based half
of the verdict (ADR-0005: LLM scores, deterministic layer decides), so it's
fully unit-tested with no external calls.

Final = 60% AI score + 40% job match (both normalised to /10).
"""
from dataclasses import dataclass

TIER_ACTION = {"A": "Immediate Interview", "B": "HR Review", "C": "Hold", "D": "Reject"}


@dataclass(frozen=True)
class Match:
    matched_skills: list[str]
    missing_skills: list[str]
    match_pct: float  # 0–100


def job_match(
    candidate_skills: list[str],
    required: list[str],
    preferred: list[str],
    min_experience: int,
    candidate_experience: float,
) -> Match:
    have = {s.lower() for s in candidate_skills}
    matched = [s for s in required if s.lower() in have]
    missing = [s for s in required if s.lower() not in have]

    skill_ratio = len(matched) / len(required) if required else 1.0
    pref_hits = [s for s in preferred if s.lower() in have]
    pref_bonus = 0.1 * (len(pref_hits) / len(preferred)) if preferred else 0.0
    if min_experience <= 0:
        exp_ratio = 1.0
    else:
        exp_ratio = min(1.0, candidate_experience / min_experience)

    pct = 100 * min(1.0, 0.7 * skill_ratio + 0.2 * exp_ratio + pref_bonus)
    return Match(matched, missing, round(pct, 1))


def final_score(overall_ai_score: float, match_pct: float) -> float:
    """overall_ai_score is /10; match_pct is /100. Result is /10."""
    return round(0.6 * overall_ai_score + 0.4 * (match_pct / 10), 2)


def tier_of(final: float) -> str:
    if final >= 8.0:
        return "A"
    if final >= 6.5:
        return "B"
    if final >= 5.0:
        return "C"
    return "D"


def recommended_action(tier: str) -> str:
    return TIER_ACTION[tier]
