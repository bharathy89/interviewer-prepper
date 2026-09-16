import difflib
import random
import time

# The proactive check-in (transcript=None poll) is a flat periodic cadence,
# not an activity-based "you've been stuck" detector — a real interviewer
# checks in every few minutes regardless of whether the candidate is quietly
# making progress. Randomized within the range so it doesn't feel metronomic.
# Voice-triggered engagement (the candidate actually saying something) is a
# separate, immediate path — see interviewer.py / design_interviewer.py.
CHECKIN_MIN_SECONDS = 180
CHECKIN_MAX_SECONDS = 300


def _next_checkin(now: float) -> float:
    return now + random.uniform(CHECKIN_MIN_SECONDS, CHECKIN_MAX_SECONDS)


def new_state(starter_code: str) -> dict:
    now = time.time()
    return {
        "mic_enabled": False,
        "audio_buffer": bytearray(),
        "vad_segmenter": None,  # set lazily in main.py to avoid importing audio here
        "last_code": starter_code,
        "last_code_change_at": now,
        "last_hint_at": 0.0,
        "next_checkin_at": _next_checkin(now),
    }


def record_code_snapshot(state: dict, code: str) -> list[int]:
    if code == state["last_code"]:
        return []

    old_lines = state["last_code"].splitlines()
    new_lines = code.splitlines()
    changed = []
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines)
    for tag, _, _, b1, b2 in matcher.get_opcodes():
        if tag != "equal":
            changed.extend(range(b1 + 1, b2 + 1))

    state["last_code"] = code
    state["last_code_change_at"] = time.time()
    return changed


def check_stagnation(state: dict) -> bool:
    return time.time() >= state["next_checkin_at"]


def mark_hint_given(state: dict) -> None:
    now = time.time()
    state["last_hint_at"] = now
    state["next_checkin_at"] = _next_checkin(now)
