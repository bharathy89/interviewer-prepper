import json
import random
from pathlib import Path
from typing import Optional

BANK_DIR = Path(__file__).parent / "bank"


def _load_all() -> dict[str, dict]:
    problems = {}
    for path in sorted(BANK_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        problems[data["id"]] = data
    return problems


_PROBLEMS = _load_all()


def list_problems(company: Optional[str] = None) -> list[dict]:
    summaries = [
        {
            "id": p["id"],
            "title": p["title"],
            "difficulty": p["difficulty"],
            "companies": p["companies"],
        }
        for p in _PROBLEMS.values()
    ]
    if company and company != "General":
        summaries = [p for p in summaries if company in p["companies"]]
    return summaries


def get_problem(problem_id: str) -> dict:
    if problem_id not in _PROBLEMS:
        raise KeyError(f"Unknown design problem id: {problem_id}")
    return _PROBLEMS[problem_id]


def pick_problem(problem_id: Optional[str] = None, company: Optional[str] = None) -> dict:
    if problem_id:
        return get_problem(problem_id)

    candidates = list(_PROBLEMS.values())
    if company and company != "General":
        tagged = [p for p in candidates if company in p["companies"]]
        if tagged:
            candidates = tagged
    return random.choice(candidates)
