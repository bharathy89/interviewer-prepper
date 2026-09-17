import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from . import auth, design_helper, design_interviewer, interviewer, monitor, persistence, sandbox
from .audio import stt, tts
from .audio.vad import UtteranceSegmenter
from .companies import list_companies
from .design_problems import loader as design_loader
from .ollama_client import InterviewerUnavailable
from .problems import loader
from .seniority import DEFAULT_SENIORITY, SENIORITY_LEVELS

MIN_UTTERANCE_SECONDS = 0.6
MIN_UTTERANCE_BYTES = int(16000 * 2 * MIN_UTTERANCE_SECONDS)  # 16kHz, 16-bit mono

# Pure backchannel ("okay", "mm-hmm") while the candidate is thinking out loud
# isn't worth a round trip to the LLM — every one of these used to trigger a
# full "sounds good, keep going" reply, which in practice meant the
# interviewer talked over the candidate every few seconds. Skip the call
# entirely for these; the transcript still shows up in the chat log either way.
_FILLER_TRANSCRIPTS = {
    "okay", "ok", "yeah", "yep", "yup", "mhm", "mm-hmm", "mm hmm", "uh-huh",
    "uh huh", "got it", "sure", "right", "hmm", "mm", "um", "uh", "alright",
    "cool", "gotcha",
}


def _is_filler(transcript: str) -> bool:
    return transcript.strip(" .,!?").lower() in _FILLER_TRANSCRIPTS

SESSIONS: dict[str, dict] = {}

# Sessions are only ever kept in memory, so a long-running public instance
# needs to reclaim abandoned ones itself rather than accumulating forever.
SESSION_LIFETIME_SECONDS = 3 * 60 * 60
SESSION_SWEEP_INTERVAL_SECONDS = 15 * 60


async def _sweep_expired_sessions():
    while True:
        await asyncio.sleep(SESSION_SWEEP_INTERVAL_SECONDS)
        cutoff = time.time() - SESSION_LIFETIME_SECONDS
        expired = [sid for sid, s in SESSIONS.items() if s["start_time"] < cutoff]
        for sid in expired:
            del SESSIONS[sid]
            persistence.delete_session(sid)


def _persist(session_id: str) -> None:
    persistence.save_session(session_id, SESSIONS[session_id])


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Sessions live in memory for speed, but are mirrored to a local sqlite
    # file (backend/sessions.db) so an interview survives a server restart —
    # the VAD segmenter and audio buffer are the only pieces not restored,
    # since they're just in-flight audio-capture state, safe to reset.
    for session_id, data in persistence.load_all_sessions().items():
        SESSIONS[session_id] = {
            **data,
            "audio_buffer": bytearray(),
            "vad_segmenter": UtteranceSegmenter(),
        }
    task = asyncio.create_task(_sweep_expired_sessions())
    yield
    task.cancel()


app = FastAPI(title="Interviewer Prepper", lifespan=lifespan)
app.add_middleware(auth.PassphraseGateMiddleware)


@app.middleware("http")
async def no_cache_static_files(request: Request, call_next):
    # There's no build step / hashed filenames for the frontend, so a stale
    # browser cache silently serves an old app.js/canvas.js after an edit —
    # force revalidation on every load for anything that isn't an API call.
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


@app.post("/api/login")
def login(req: auth.LoginRequest):
    return auth.login(req)


class StartRequest(BaseModel):
    problem_id: str | None = None
    company: str | None = None
    interview_type: str = "coding"  # "coding" | "system_design"
    voice: str = tts.DEFAULT_VOICE
    seniority: str = DEFAULT_SENIORITY
    mode: str = "interview"  # "interview" | "guided" — guided is system_design only


class ChatRequest(BaseModel):
    message: str
    code: str = ""


class CodeRequest(BaseModel):
    code: str


class MicRequest(BaseModel):
    enabled: bool


class TTSRequest(BaseModel):
    text: str
    voice: str = tts.DEFAULT_VOICE


class CanvasRequest(BaseModel):
    shapes: list
    image_b64: str | None = None


class DesignReviewRequest(BaseModel):
    image_b64: str


class DesignUpdateRequest(BaseModel):
    image_b64: str
    current_elements: list


@app.get("/api/companies")
def get_companies():
    return list_companies()


@app.get("/api/problems")
def get_problems(company: str | None = None):
    return loader.list_problems(company)


@app.get("/api/design_problems")
def get_design_problems(company: str | None = None):
    return design_loader.list_problems(company)


@app.post("/api/session/start")
def start_session(req: StartRequest):
    is_design = req.interview_type == "system_design"
    active_loader = design_loader if is_design else loader

    mode = req.mode if (is_design and req.mode == "guided") else "interview"
    active_interviewer = design_helper if mode == "guided" else (
        design_interviewer if is_design else interviewer
    )
    seniority_level = req.seniority if req.seniority in SENIORITY_LEVELS else DEFAULT_SENIORITY

    try:
        problem = active_loader.pick_problem(req.problem_id, req.company)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    voice = req.voice if req.voice in tts.ALLOWED_VOICES else tts.DEFAULT_VOICE
    persona_name = tts.PERSONA_NAMES[voice]
    session_id = str(uuid.uuid4())
    opening = active_interviewer.opening_message(problem, req.company, persona_name)
    initial_content = "[]" if is_design else problem["starter_code"]

    SESSIONS[session_id] = {
        "problem": problem,
        "company": req.company,
        "interview_type": req.interview_type,
        "mode": mode,
        "seniority": seniority_level,
        "voice": voice,
        "history": [{"role": "assistant", "content": opening}],
        "start_time": time.time(),
        "last_canvas_image": None,  # design mode only; kept fresh by canvas_snapshot
        **monitor.new_state(initial_content),
        "vad_segmenter": UtteranceSegmenter(),
    }
    _persist(session_id)

    return {
        "session_id": session_id,
        "problem": problem,
        "company": req.company,
        "interview_type": req.interview_type,
        "mode": mode,
        "opening_message": opening,
    }


def _get_session(session_id: str) -> dict:
    session = SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown session")
    return session


def _persona_name(session: dict) -> str:
    return tts.PERSONA_NAMES.get(session.get("voice"), tts.PERSONA_NAMES[tts.DEFAULT_VOICE])


def _seniority(session: dict) -> str:
    return session.get("seniority", DEFAULT_SENIORITY)


def _active_design_module(session: dict):
    return design_helper if session.get("mode") == "guided" else design_interviewer


@app.get("/api/sessions")
def list_sessions():
    summaries = []
    for session_id, session in SESSIONS.items():
        last_activity = max(
            session["start_time"], session["last_code_change_at"], session["last_hint_at"]
        )
        summaries.append(
            {
                "session_id": session_id,
                "interview_type": session["interview_type"],
                "mode": session.get("mode", "interview"),
                "company": session["company"],
                "problem_title": session["problem"]["title"],
                "problem_difficulty": session["problem"].get("difficulty"),
                "start_time": session["start_time"],
                "last_activity": last_activity,
                "message_count": len(session["history"]),
            }
        )
    summaries.sort(key=lambda s: s["last_activity"], reverse=True)
    return summaries


@app.get("/api/session/{session_id}")
def get_session(session_id: str):
    session = _get_session(session_id)
    return {
        "session_id": session_id,
        "problem": session["problem"],
        "company": session["company"],
        "interview_type": session["interview_type"],
        "mode": session.get("mode", "interview"),
        "seniority": _seniority(session),
        "voice": session.get("voice", tts.DEFAULT_VOICE),
        "history": session["history"],
        "last_code": session["last_code"],
        "start_time": session["start_time"],
    }


@app.post("/api/session/{session_id}/chat")
def chat(session_id: str, req: ChatRequest):
    session = _get_session(session_id)
    is_design = session["interview_type"] == "system_design"

    try:
        if is_design:
            reply = _active_design_module(session).respond(
                session["problem"], session["company"], session["history"], req.message,
                _persona_name(session), _seniority(session), session.get("last_canvas_image"),
            )
        else:
            reply = interviewer.respond(
                session["problem"], session["company"], session["history"], req.message, req.code,
                _persona_name(session), _seniority(session),
            )
    except InterviewerUnavailable as exc:
        return {"reply": str(exc), "error": True}

    if is_design:
        session["history"].append({"role": "user", "content": req.message})
    else:
        session["history"].append(
            {
                "role": "user",
                "content": f"{req.message}\n\n--- Candidate's current code ---\n{req.code or '(empty)'}",
            }
        )
    session["history"].append({"role": "assistant", "content": reply})
    _persist(session_id)
    return {"reply": reply, "error": False}


@app.post("/api/session/{session_id}/run")
def run(session_id: str, req: CodeRequest):
    _get_session(session_id)
    return sandbox.run_code(req.code)


@app.post("/api/session/{session_id}/mic")
def set_mic(session_id: str, req: MicRequest):
    session = _get_session(session_id)
    session["mic_enabled"] = req.enabled
    session["vad_segmenter"].reset()
    _persist(session_id)
    return {"mic_enabled": session["mic_enabled"]}


async def _generate_voice_reply(session_id: str, transcript: str) -> None:
    # Runs as a background task, off the audio_chunk request/response cycle —
    # the LLM call this makes is the slow part (1-3+ seconds even on a fast
    # cloud model), and blocking the transcript response on it was the real
    # cause of "the transcript takes a few seconds to show up": the frontend
    # was waiting on this whole call before it ever saw what was said.
    session = SESSIONS.get(session_id)
    if session is None:
        return

    try:
        if session["interview_type"] == "system_design":
            reply = await run_in_threadpool(
                _active_design_module(session).maybe_intervene,
                session["problem"], session["company"], session["history"],
                _persona_name(session), _seniority(session), transcript,
                session.get("last_canvas_image"),
            )
        else:
            reply = await run_in_threadpool(
                interviewer.maybe_intervene,
                session["problem"], session["company"], session["history"], session["last_code"],
                _persona_name(session), _seniority(session), transcript,
            )
    except InterviewerUnavailable as exc:
        reply = str(exc)

    if reply:
        session["history"].append({"role": "assistant", "content": reply})
        monitor.mark_hint_given(session)
        _persist(session_id)
    session["pending_reply"] = reply


@app.post("/api/session/{session_id}/audio_chunk")
async def audio_chunk(session_id: str, request: Request):
    session = _get_session(session_id)
    if not session["mic_enabled"]:
        return {"transcript": None, "reply": None}

    chunk = await request.body()
    utterance = session["vad_segmenter"].feed(chunk)
    response = {"transcript": None}

    if utterance is not None and len(utterance) >= MIN_UTTERANCE_BYTES:
        # Too-short utterances (a click/cough) skip Whisper entirely, rather
        # than risk it hallucinating on a near-silent clip.
        stt_t0 = time.time()
        transcript = await run_in_threadpool(stt.transcribe, utterance)
        response["stt_ms"] = round((time.time() - stt_t0) * 1000)

        if transcript:
            response["transcript"] = transcript
            session["history"].append({"role": "user", "content": f'(spoken) "{transcript}"'})
            _persist(session_id)
            if not _is_filler(transcript):
                asyncio.create_task(_generate_voice_reply(session_id, transcript))

    # audio_chunk fires continuously while the mic is on, so a reply that
    # finished computing (from this utterance or an earlier one) rides along
    # on whichever poll comes next, instead of holding up the transcript.
    response["reply"] = session.pop("pending_reply", None)

    return response


@app.post("/api/session/{session_id}/code_snapshot")
def code_snapshot(session_id: str, req: CodeRequest):
    session = _get_session(session_id)
    changed_lines = monitor.record_code_snapshot(session, req.code)
    if changed_lines:
        _persist(session_id)
    return {"changed_lines": changed_lines}


@app.post("/api/session/{session_id}/canvas_snapshot")
def canvas_snapshot(session_id: str, req: CanvasRequest):
    session = _get_session(session_id)
    monitor.record_code_snapshot(session, json.dumps(req.shapes))
    if req.image_b64:
        session["last_canvas_image"] = req.image_b64
    _persist(session_id)
    return {"ok": True}


@app.post("/api/session/{session_id}/design_review")
def design_review(session_id: str, req: DesignReviewRequest):
    session = _get_session(session_id)
    try:
        reply = _active_design_module(session).review_diagram(
            session["problem"], session["company"], session["history"], req.image_b64,
            _persona_name(session), _seniority(session),
        )
    except InterviewerUnavailable as exc:
        return {"reply": str(exc), "error": True}

    session["history"].append({"role": "user", "content": "(shared the current diagram)"})
    session["history"].append({"role": "assistant", "content": reply})
    monitor.mark_hint_given(session)
    _persist(session_id)
    return {"reply": reply, "error": False}


@app.post("/api/session/{session_id}/design_update")
def design_update(session_id: str, req: DesignUpdateRequest):
    session = _get_session(session_id)
    try:
        elements = _active_design_module(session).suggest_diagram_update(
            session["problem"], session["company"], session["history"],
            req.image_b64, req.current_elements, _persona_name(session),
        )
    except InterviewerUnavailable as exc:
        return {"elements": [], "error": str(exc)}
    return {"elements": elements, "error": None}


@app.get("/api/session/{session_id}/proactive")
def proactive(session_id: str):
    session = _get_session(session_id)
    if not monitor.check_stagnation(session):
        return {"message": None}

    try:
        if session["interview_type"] == "system_design":
            reply = _active_design_module(session).maybe_intervene(
                session["problem"], session["company"], session["history"],
                _persona_name(session), _seniority(session), None,
                session.get("last_canvas_image"),
            )
        else:
            reply = interviewer.maybe_intervene(
                session["problem"], session["company"], session["history"], session["last_code"],
                _persona_name(session), _seniority(session), None,
            )
    except InterviewerUnavailable as exc:
        return {"message": str(exc)}

    monitor.mark_hint_given(session)
    if reply:
        session["history"].append({"role": "assistant", "content": reply})
    _persist(session_id)
    return {"message": reply}


@app.post("/api/tts")
def synthesize_speech(req: TTSRequest):
    t0 = time.time()
    audio = tts.synthesize(req.text, voice=req.voice)
    synth_ms = round((time.time() - t0) * 1000)
    return Response(
        content=audio, media_type="audio/wav", headers={"X-Synth-Ms": str(synth_ms)}
    )


# --- Standalone STT test harness (frontend/stt_test.html) — no interview
# session involved, just mic audio -> VAD -> Whisper, for isolating STT issues
# from the rest of the app. ---
_STT_TEST_SEGMENTER = UtteranceSegmenter()
_STT_TEST_LAST_PARTIAL_AT = 0.0
_STT_TEST_COMMITTED = ""  # transcript of windows already finalized-and-frozen, this utterance
_STT_TEST_WINDOW_START = 0  # byte offset into the buffer where the live (uncommitted) window begins
_STT_TEST_BUSY = False  # single-flight guard: never run two transcribe calls concurrently —
# faster-whisper/CTranslate2 isn't guaranteed safe (or fast) for concurrent calls on one model

PARTIAL_INTERVAL_SECONDS = 1.0  # re-transcribe the live window at most this often
PARTIAL_WINDOW_SECONDS = 8.0  # cap how much audio one partial call re-transcribes, so a long
PARTIAL_WINDOW_BYTES = int(16000 * 2 * PARTIAL_WINDOW_SECONDS)  # utterance doesn't make each
# call progressively slower (Whisper re-decodes a window from scratch, no incremental decoding).
# Once a window fills up, its transcript is committed (frozen) and a fresh window starts, so the
# live preview is committed-so-far + a small live tail, not just the last few seconds — and the
# final transcript at utterance-end always re-transcribes the complete, un-windowed audio.


@app.post("/api/stt_test/reset")
def stt_test_reset():
    global _STT_TEST_LAST_PARTIAL_AT, _STT_TEST_COMMITTED, _STT_TEST_WINDOW_START, _STT_TEST_BUSY
    _STT_TEST_SEGMENTER.reset()
    _STT_TEST_LAST_PARTIAL_AT = 0.0
    _STT_TEST_COMMITTED = ""
    _STT_TEST_WINDOW_START = 0
    _STT_TEST_BUSY = False
    return {"ok": True}


@app.post("/api/stt_test/chunk")
async def stt_test_chunk(request: Request):
    global _STT_TEST_LAST_PARTIAL_AT, _STT_TEST_COMMITTED, _STT_TEST_WINDOW_START, _STT_TEST_BUSY

    chunk = await request.body()
    utterance = _STT_TEST_SEGMENTER.feed(chunk)

    if utterance is not None and len(utterance) >= MIN_UTTERANCE_BYTES:
        t0 = time.time()
        transcript = await run_in_threadpool(stt.transcribe, utterance)
        _STT_TEST_COMMITTED = ""
        _STT_TEST_WINDOW_START = 0
        return {
            "final": transcript or None,
            "partial": None,
            "utterance_seconds": round(len(utterance) / (16000 * 2), 2),
            "transcribe_seconds": round(time.time() - t0, 2),
        }

    # No utterance finished yet — if one is in progress, periodically transcribe the
    # live window for a "typing preview" effect.
    now = time.time()
    partial_audio = _STT_TEST_SEGMENTER.partial()
    live_window = partial_audio[_STT_TEST_WINDOW_START:] if partial_audio else b""

    if (
        not _STT_TEST_BUSY
        and len(live_window) >= MIN_UTTERANCE_BYTES
        and now - _STT_TEST_LAST_PARTIAL_AT > PARTIAL_INTERVAL_SECONDS
    ):
        _STT_TEST_LAST_PARTIAL_AT = now
        _STT_TEST_BUSY = True
        try:
            live_text = await run_in_threadpool(stt.transcribe, live_window)

            if len(live_window) >= PARTIAL_WINDOW_BYTES:
                # This window is full — freeze its text and start a fresh one, so the
                # next few ticks only re-transcribe new audio, not this whole span again.
                if live_text:
                    _STT_TEST_COMMITTED = f"{_STT_TEST_COMMITTED} {live_text}".strip()
                _STT_TEST_WINDOW_START = len(partial_audio)
                live_text = ""

            combined = f"{_STT_TEST_COMMITTED} {live_text}".strip()
            return {"final": None, "partial": combined or None}
        finally:
            _STT_TEST_BUSY = False

    return {"final": None, "partial": None}


@app.get("/", response_class=HTMLResponse)
def index():
    # There's no build step / hashed filenames for the frontend, so a
    # browser's disk cache can silently keep serving a stale copy after an
    # edit even past a hard refresh. Stamping each with its own mtime as a
    # query string forces a cache miss exactly when the file actually changed.
    html = (FRONTEND_DIR / "index.html").read_text()
    for name, attr in (("canvas.js", "src"), ("app.js", "src"), ("style.css", "href")):
        mtime = int((FRONTEND_DIR / name).stat().st_mtime)
        html = html.replace(f'{attr}="{name}"', f'{attr}="{name}?v={mtime}"')
    return html


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
