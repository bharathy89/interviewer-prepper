import webrtcvad

SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_BYTES = int(SAMPLE_RATE * FRAME_MS / 1000) * 2  # 16-bit mono samples

SPEECH_START_FRAMES = 3  # ~60ms of speech to confirm an utterance has started
SILENCE_HANGOVER_FRAMES = 30  # ~600ms of trailing silence to end an utterance

VAD_MODE = 2  # moderate filtering — mode 3 was rejecting/clipping real speech with
# normal background noise; the actual hallucination fix was the sample-rate bug, not this.


class UtteranceSegmenter:
    """Feeds 20ms PCM16 frames through webrtcvad and yields complete utterances.

    Requires a short run of consecutive speech frames before declaring an utterance
    has started (filters clicks/pops), then accumulates audio until a longer run of
    silence frames follows, at which point the buffered utterance is returned.
    """

    def __init__(self):
        self._vad = webrtcvad.Vad(VAD_MODE)
        self._pending = bytearray()  # unclassified leftover bytes (< one frame)
        self._speech_run = 0
        self._silence_run = 0
        self._in_utterance = False
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> bytes | None:
        self._pending.extend(chunk)
        result = None

        while len(self._pending) >= FRAME_BYTES:
            frame = bytes(self._pending[:FRAME_BYTES])
            del self._pending[:FRAME_BYTES]

            is_speech = self._vad.is_speech(frame, SAMPLE_RATE)

            if not self._in_utterance:
                if is_speech:
                    self._speech_run += 1
                    self._buffer.extend(frame)
                    if self._speech_run >= SPEECH_START_FRAMES:
                        self._in_utterance = True
                        self._silence_run = 0
                else:
                    self._speech_run = 0
                    self._buffer.clear()
            else:
                self._buffer.extend(frame)
                if is_speech:
                    self._silence_run = 0
                else:
                    self._silence_run += 1
                    if self._silence_run >= SILENCE_HANGOVER_FRAMES:
                        result = bytes(self._buffer)
                        self._reset()

        return result

    def partial(self) -> bytes | None:
        """The audio buffered so far for an utterance still in progress, or None."""
        return bytes(self._buffer) if self._in_utterance else None

    def _reset(self):
        self._buffer = bytearray()
        self._speech_run = 0
        self._silence_run = 0
        self._in_utterance = False

    def reset(self):
        self._pending = bytearray()
        self._reset()
