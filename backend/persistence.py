import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "sessions.db"

# Only the JSON-serializable, resumable parts of a session get persisted.
# vad_segmenter (a live audio-buffering object) and audio_buffer (unused
# dead state) are recreated fresh on load — losing mid-utterance audio state
# across a restart is fine, losing the interview transcript is not.
_PERSISTED_FIELDS = [
    "problem",
    "company",
    "interview_type",
    "mode",
    "seniority",
    "voice",
    "history",
    "start_time",
    "last_canvas_image",
    "mic_enabled",
    "last_code",
    "last_code_change_at",
    "last_hint_at",
    "next_checkin_at",
]


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions ("
        "session_id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at REAL NOT NULL)"
    )
    return conn


def save_session(session_id: str, session: dict) -> None:
    data = {field: session[field] for field in _PERSISTED_FIELDS}
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (session_id, data, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
            (session_id, json.dumps(data), time.time()),
        )


def delete_session(session_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))


def load_all_sessions() -> dict[str, dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT session_id, data FROM sessions").fetchall()
    return {session_id: json.loads(data) for session_id, data in rows}
