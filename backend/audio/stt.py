import os
import queue

import numpy as np
from faster_whisper import WhisperModel

# faster-whisper/CTranslate2 isn't guaranteed safe (or fast) for concurrent
# calls on one model instance — a single shared model meant two overlapping
# live-voice sessions could corrupt or serialize each other's transcription.
# A small pool of independent instances gives real bounded parallelism (up to
# POOL_SIZE concurrent transcribes) with automatic backpressure beyond that:
# queue.Queue.get() just blocks until an instance frees up, which is exactly
# right since transcribe() already runs on a worker thread (run_in_threadpool
# in main.py), not the asyncio event loop.
#
# Each instance is ~150MB resident (base.en, int8) — factor that into VM
# sizing alongside tts.py's pool (larger, ~330MB/instance) when deploying.
POOL_SIZE = int(os.environ.get("STT_POOL_SIZE", min(os.cpu_count() or 2, 4)))

_pool: queue.Queue[WhisperModel] | None = None


def _get_pool() -> queue.Queue[WhisperModel]:
    global _pool
    if _pool is None:
        pool: queue.Queue[WhisperModel] = queue.Queue()
        for _ in range(POOL_SIZE):
            pool.put(WhisperModel("base.en", device="cpu", compute_type="int8"))
        _pool = pool
    return _pool


# Whisper is prone to hallucinating stock phrases ("thank you", "bye bye", ...)
# on audio that doesn't contain real speech (silence, noise, or corrupted audio).
# The real fix was catching the upstream sample-rate bug that was corrupting audio
# before it ever reached here — these are cheap, no-downside defenses on top of that:
#   - condition_on_previous_text=False stops one bad segment from seeding the next
#   - vad_filter runs faster-whisper's own (Silero) VAD as a second pass on top of
#     our upstream webrtcvad, trimming any residual silence/noise at utterance edges
#   - compression_ratio_threshold flags/drops repetitive degenerate output
#     (exactly the "Bye. Bye. Bye..." failure mode)
#   - the no_speech/logprob thresholds below are a final post-hoc filter on top
#
# Deliberately NOT used: a bigger model / beam_size>1 (too slow for live use with no
# real gain once the audio itself is correct) and hallucination_silence_threshold
# (meant for long-form transcription; on a short live utterance it can misfire on an
# ordinary mid-sentence pause and cut off legitimate speech partway through).
NO_SPEECH_PROB_THRESHOLD = 0.6
AVG_LOGPROB_THRESHOLD = -1.0


def transcribe(pcm_bytes: bytes) -> str:
    if not pcm_bytes:
        return ""

    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    pool = _get_pool()
    model = pool.get()
    try:
        segments, _ = model.transcribe(
            audio,
            language="en",
            beam_size=1,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False,
            compression_ratio_threshold=2.4,
        )
        kept = [
            segment.text.strip()
            for segment in segments
            if segment.no_speech_prob < NO_SPEECH_PROB_THRESHOLD
            and segment.avg_logprob > AVG_LOGPROB_THRESHOLD
        ]
        return " ".join(kept).strip()
    finally:
        pool.put(model)
