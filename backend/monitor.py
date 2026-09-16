import difflib
import time

STAGNATION_SECONDS = 45
HINT_COOLDOWN_SECONDS = 30


def new_state(starter_code: str) -> dict:
    now = time.time()
    return {
        "mic_enabled": False,
        "audio_buffer": bytearray(),
        "vad_segmenter": None,  # set lazily in main.py to avoid importing audio here
        "last_code": starter_code,
        "last_code_change_at": now,
        "last_hint_at": 0.0,
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
    now = time.time()
    if now - state["last_hint_at"] < HINT_COOLDOWN_SECONDS:
        return False

    return (now - state["last_code_change_at"] > STAGNATION_SECONDS) and (
        now - state["last_hint_at"] > STAGNATION_SECONDS
    )


def mark_hint_given(state: dict) -> None:
    state["last_hint_at"] = time.time()
