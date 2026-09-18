"""One-time setup step: pre-downloads the local voice models (Kokoro TTS,
Whisper STT) so the first interview doesn't stall on a mid-session download.

Usage: python -m scripts.download_voice_models
"""

from backend.audio import stt, tts


def main() -> None:
    print("Downloading Kokoro TTS model (~330MB)...")
    tts._ensure_model_files()
    print("Kokoro TTS model ready.")

    print("Downloading Whisper STT model (base.en)...")
    stt._get_pool()
    print("Whisper STT model ready.")


if __name__ == "__main__":
    main()
