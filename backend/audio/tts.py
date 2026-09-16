import io
import re
import urllib.request
from pathlib import Path

import soundfile as sf
from kokoro_onnx import Kokoro

MODELS_DIR = Path(__file__).parent / "models"
# The int8-quantized model is smaller but its ConvInteger ops fall back to a
# slow, non-vectorized path in onnxruntime's CPU execution provider on x86 —
# measured ~25-40s per reply on a cloud VM (vs. ~4s for this fp32 model on the
# same hardware). Fine on Apple Silicon locally, but pick fp32 for portability.
MODEL_PATH = MODELS_DIR / "kokoro-v1.0.onnx"
VOICES_PATH = MODELS_DIR / "voices-v1.0.bin"

_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1"
_DOWNLOADS = {
    MODEL_PATH: f"{_RELEASE}/kokoro-v1.0.onnx",
    VOICES_PATH: f"{_RELEASE}/voices-v1.0.bin",
}

DEFAULT_VOICE = "af_heart"

_kokoro: Kokoro | None = None


def _ensure_model_files() -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for path, url in _DOWNLOADS.items():
        if not path.exists():
            urllib.request.urlretrieve(url, str(path) + ".partial")
            Path(str(path) + ".partial").rename(path)


def _get_kokoro() -> Kokoro:
    global _kokoro
    if _kokoro is None:
        _ensure_model_files()
        _kokoro = Kokoro(str(MODEL_PATH), str(VOICES_PATH))
    return _kokoro


def _strip_markdown(text: str) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", text)  # inline code
    text = re.sub(r"\*\*([^*]*)\*\*", r"\1", text)  # bold
    text = re.sub(r"\*([^*]*)\*", r"\1", text)  # italic
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)  # headers
    text = re.sub(r"^[-*]\s+", "", text, flags=re.MULTILINE)  # bullet markers
    return text


def synthesize(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    samples, sample_rate = _get_kokoro().create(
        _strip_markdown(text), voice=voice, speed=1.0, lang="en-us"
    )
    buffer = io.BytesIO()
    sf.write(buffer, samples, sample_rate, format="WAV")
    return buffer.getvalue()
