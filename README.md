# Interviewer Prepper

A local, CoderPad-style interview practice tool with two modes: Python coding interviews and
system design interviews. Pick a company style and a problem (or go random), and an AI
interviewer — powered by `glm-5.3-flash:cloud` via Ollama — walks you through a live interview:
it listens (voice or typed), asks about your approach, answers clarifying questions, and gives
escalating hints when you're stuck or fall silent.

## Prerequisites

- Python 3.11+ (3.12 recommended)
- [Ollama](https://ollama.com/download) installed, with a free ollama.com account for cloud model
  access (the interviewer model runs on Ollama's cloud, not on your machine)
- Optional: [Docker](https://docs.docker.com/get-docker/) — if installed and running, code you
  submit to "Run" executes inside an isolated, network-disabled container instead of a bare
  subprocess. Not required to use the app.

## Setup

Clone the repo and install dependencies into a virtual environment:

```bash
git clone <this-repo-url>
cd interviewer-prepper
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Sign in to your own Ollama account (this is a one-time step per machine — it links this machine
to *your* ollama.com account for cloud model access, no API key or secret to configure):

```bash
ollama signin
ollama list   # should show glm-5.3-flash:cloud is reachable
```

The model in use is configured in `backend/ollama_client.py` (`MODEL = "glm-5.3-flash:cloud"`),
used for both text chat and vision (system-design diagram review). Ollama cloud models
occasionally get retired — if you see a "retired" / status 410 error in the chat panel, run
`ollama list` and swap in a currently-live model there.

Pre-download the local voice models (not checked into the repo — see `.gitignore`) so the first
interview doesn't stall on a download mid-session:

```bash
python -m scripts.download_voice_models
```

This fetches [Kokoro](https://github.com/thewh1teagle/kokoro-onnx) (TTS, voice `af_heart` by
default, set in `backend/audio/tts.py`, ~330MB) and Whisper `base.en` (STT). The fp32 Kokoro model
is used deliberately over the smaller int8-quantized one — the quantized model's `ConvInteger`
ops fall back to a slow unvectorized path on some CPUs (observed ~25-40s per reply on a cloud x86
VM vs. ~4s for fp32 on the same hardware), even though it's faster on Apple Silicon.

## Run

```bash
uvicorn backend.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000) in your browser, pick a problem, and start
the interview.

## How it works

- **Coding problems** live as JSON files in `backend/problems/bank/`, each tagged with the
  companies known to ask that style of question.
- **System design problems** live as JSON files in `backend/design_problems/bank/` (prompt,
  requirements, discussion points — no code involved).
- **Company profiles** (`backend/companies.py`) define an interview style per company — Google,
  Amazon, Meta, Microsoft, Apple, Netflix, OpenAI, Anthropic, Google DeepMind, xAI, Mistral AI,
  NVIDIA, Databricks, Scale AI — used to steer the interviewer's tone and question set.
- **Run** executes your code and shows console output — no LLM involved, purely deterministic,
  and there's no scored Submit/evaluation step. If Docker is installed and running, code executes
  inside an isolated container (`--network=none`, memory/CPU/pid limits, read-only filesystem);
  otherwise it falls back to a plain subprocess with a timeout and best-effort resource limits.
- **System design mode**: sketch your architecture on a real Excalidraw canvas (`frontend/canvas.js`).
  Click "Review my diagram" to have the interviewer look at it directly (vision) and comment on
  what's actually drawn, or "Suggest update" to have it propose concrete additions to the diagram.
  Has two modes, picked on the start screen: **Interview** (the default — evaluative, asks probing
  questions, hints only when you're stuck) and **Guided** (`backend/design_helper.py` — a coaching
  mode that teaches instead of grading, building up the design step by step from simple to complex
  through each problem's discussion points, explaining and proposing solutions rather than waiting
  you out).
- **Experience level** (`backend/seniority.py`): Fresh Grad through Principal, picked on the start
  screen — calibrates how much the interviewer leads vs. waits before hinting, and what it expects
  you to raise unprompted (e.g. a Principal candidate is expected to bring up cost/scale tradeoffs
  unasked; a Fresh Grad gets walked toward a basic working design).
- **Voice**: toggle the mic to talk to the interviewer — speech is transcribed locally with
  Whisper (`backend/audio/stt.py`) and segmented with a VAD (`backend/audio/vad.py`); replies are
  spoken back with the local Kokoro TTS model (`backend/audio/tts.py`), voiced as either Lucy or
  Mike (picked on the start screen). A stagnation/struggle monitor (`backend/monitor.py`) also has
  the interviewer proactively check in every few minutes, mic on or off.
- **Resume a session**: interviews persist to a local sqlite file (`backend/persistence.py`), so
  the start screen always offers a "Resume a session" list to pick back up where you left off —
  including after a server restart. "Exit" on the interview screen returns to the start screen
  without ending the session.

## Hosting for others (optional)

By default there's no access control — fine for local, single-user use. To share a running
instance with others, set an `ACCESS_PASSPHRASE` environment variable before starting the server:

```bash
ACCESS_PASSPHRASE=your-shared-passphrase uvicorn backend.main:app
```

This gates every `/api/*` route behind a simple passphrase-entry screen (`backend/auth.py`) — a
signed cookie, not real per-user accounts, just enough friction to keep random traffic (and your
Ollama billing) in check. Pick your own passphrase; there's nothing to generate or configure
beyond that one environment variable. Leave it unset to keep the app open, as before.

When hosting publicly:
- Make sure Docker is installed and running so `Run` uses the isolated sandbox path described
  above rather than the bare-subprocess fallback.
- Serve over HTTPS — `getUserMedia` (the mic) only works on a secure context for any origin other
  than `localhost`.
- Each machine that serves requests needs its own `ollama signin` — Ollama cloud access is tied to
  the machine (via a local keypair), not something you can copy into an env var or secret.
