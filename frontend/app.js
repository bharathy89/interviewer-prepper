let editor = null;
let sessionId = null;
let currentProblem = null;
let timerInterval = null;
let startTime = null;
let interviewType = "coding";
let selectedVoice = localStorage.getItem("ttsVoice") || "af_heart";

let micEnabled = false;
let silentMode = false;
let audioContext = null;
let mediaStream = null;
let sourceNode = null;
let processorNode = null;
let proactivePollTimer = null;
let codeSnapshotTimer = null;
let bargeInStreak = 0;
let userTalking = false;
let userTalkingTimeout = null;
const USER_TALKING_HANGOVER_MS = 800; // avoid flickering false between words
let micResumeAt = 0; // Date.now() timestamp; mic audio isn't forwarded to STT until this passes
const MIC_RESUME_GRACE_MS = 300; // covers room reverb / buffered audio settling after TTS stops

// Originally raised from 0.02 (that triggered on ordinary ambient noise) to
// 0.05, then nudged back down slightly now that echoCancellation is back on
// (see enableMic) — AEC should suppress most of the TTS's own signal, so a
// genuine interruption spoken over it should still leave enough residual
// signal to cross a somewhat lower bar. BARGE_IN_CONSECUTIVE_BUFFERS still
// requires that to be sustained across several buffers (not a single blip)
// before treating it as the candidate actually starting to talk, mirroring
// how the server-side VAD avoids reacting to a single noisy frame. Check the
// console.debug rms/streak log during a real interruption and retune from
// real numbers if it's still not sensitive enough.
const BARGE_IN_RMS_THRESHOLD = 0.035;
const BARGE_IN_CONSECUTIVE_BUFFERS = 3;
const CODE_SNAPSHOT_DEBOUNCE_MS = 1500;
const PROACTIVE_POLL_MS = 5000;

const $ = (id) => document.getElementById(id);

async function api(path, options) {
  const res = await fetch(path, options);
  if (res.status === 401 && path !== "/api/login") {
    showGateScreen();
    throw new Error("Not authenticated");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

function showGateScreen() {
  $("start-screen").classList.add("hidden");
  $("gate-screen").classList.remove("hidden");
}

async function submitPassphrase() {
  const passphrase = $("passphrase-input").value;
  $("gate-error").textContent = "";
  try {
    await api("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ passphrase }),
    });
    $("gate-screen").classList.add("hidden");
    $("start-screen").classList.remove("hidden");
    await loadPickers();
    await loadResumableSessions();
  } catch (err) {
    $("gate-error").textContent = "Incorrect passphrase.";
  }
}

$("gate-submit-btn").addEventListener("click", submitPassphrase);
$("passphrase-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    submitPassphrase();
  }
});

async function loadPickers() {
  const companies = await api("/api/companies");
  const companySelect = $("company-select");
  for (const c of companies) {
    const opt = document.createElement("option");
    opt.value = c.name;
    opt.textContent = c.name;
    opt.title = c.style;
    companySelect.appendChild(opt);
  }

  companySelect.addEventListener("change", () => loadProblems(companySelect.value));

  document.querySelectorAll("#interview-type-toggle .type-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      interviewType = btn.dataset.type;
      document
        .querySelectorAll("#interview-type-toggle .type-btn")
        .forEach((b) => b.classList.toggle("active", b === btn));
      loadProblems(companySelect.value);
    });
  });

  document.querySelectorAll(".voice-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.voice === selectedVoice);
    btn.addEventListener("click", () => {
      selectedVoice = btn.dataset.voice;
      localStorage.setItem("ttsVoice", selectedVoice);
      document.querySelectorAll(".voice-btn").forEach((b) => b.classList.toggle("active", b === btn));
    });
  });

  await loadProblems("");
}

async function loadProblems(company) {
  const endpoint = interviewType === "system_design" ? "/api/design_problems" : "/api/problems";
  const problems = await api(`${endpoint}${company ? `?company=${encodeURIComponent(company)}` : ""}`);
  const problemSelect = $("problem-select");
  problemSelect.innerHTML = '<option value="">Random</option>';
  for (const p of problems) {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = `${p.title} (${p.difficulty})`;
    problemSelect.appendChild(opt);
  }
}

function initMonaco(starterCode) {
  return new Promise((resolve) => {
    require.config({ paths: { vs: "https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs" } });
    require(["vs/editor/editor.main"], () => {
      editor = monaco.editor.create($("editor"), {
        value: starterCode,
        language: "python",
        theme: "vs-dark",
        automaticLayout: true,
        minimap: { enabled: false },
        fontSize: 13,
      });
      resolve();
    });
  });
}

function fillList(el, items) {
  el.innerHTML = "";
  for (const item of items) {
    const li = document.createElement("li");
    li.textContent = item;
    el.appendChild(li);
  }
}

function renderProblem(problem, company, isDesign) {
  $("problem-title-text").textContent = problem.title;
  $("difficulty-badge").textContent = problem.difficulty;
  $("company-badge").textContent = company || "General";
  $("company-badge").classList.toggle("hidden", !company);

  $("problem-heading").textContent = problem.title;
  $("problem-prompt").textContent = problem.prompt;

  $("coding-problem-info").classList.toggle("hidden", isDesign);
  $("design-problem-info").classList.toggle("hidden", !isDesign);

  if (isDesign) {
    fillList($("problem-requirements"), problem.requirements);
    fillList($("problem-discussion"), problem.discussion_points);
    return;
  }

  fillList($("problem-constraints"), problem.constraints);

  const examplesEl = $("problem-examples");
  examplesEl.innerHTML = "";
  for (const ex of problem.examples) {
    const div = document.createElement("div");
    div.className = "example-block";
    div.textContent = `Input: ${ex.input}\nOutput: ${ex.output}`;
    examplesEl.appendChild(div);
  }
}

function addChatBubble(role, text) {
  const el = document.createElement("div");
  el.className = `chat-bubble ${role}`;
  el.textContent = text;
  $("chat-messages").appendChild(el);
  $("chat-messages").scrollTop = $("chat-messages").scrollHeight;
}

function updateTimerDisplay() {
  const elapsed = Math.floor((Date.now() - startTime) / 1000);
  const mins = String(Math.floor(elapsed / 60)).padStart(2, "0");
  const secs = String(elapsed % 60).padStart(2, "0");
  $("timer").textContent = `${mins}:${secs}`;
}

function startTimer(customStartTime) {
  startTime = customStartTime || Date.now();
  timerInterval = setInterval(updateTimerDisplay, 1000);
}

function renderConsoleOutput(results) {
  const panel = $("console-output");
  const stdoutEl = $("console-stdout");
  const stderrEl = $("console-stderr");

  stdoutEl.textContent = results.stdout ? `stdout:\n${results.stdout}` : "";
  stderrEl.textContent = results.stderr ? `stderr:\n${results.stderr}` : "";
  panel.classList.toggle("hidden", !results.stdout && !results.stderr);
}

function renderResults(results) {
  const summaryEl = $("results-summary");
  renderConsoleOutput(results);

  if (results.timed_out) {
    summaryEl.textContent = "Timed out.";
    summaryEl.style.color = "var(--fail)";
    return;
  }

  if (results.exit_code === 0) {
    summaryEl.textContent = "Ran successfully.";
    summaryEl.style.color = "var(--pass)";
  } else {
    summaryEl.textContent = `Exited with code ${results.exit_code} — see console output below.`;
    summaryEl.style.color = "var(--fail)";
  }
}

// --- Text-to-speech via the local Kokoro model (backend /api/tts), queued so
// overlapping messages don't talk over each other ---

const ttsAudio = new Audio();
let speechQueue = [];
let speaking = false;
let currentTtsUrl = null;

// Surfaces STT/TTS latency so it's easy to tell "the model is slow" apart from
// "the network/deployment hop is slow" — server_ms is time spent actually
// running Whisper/Kokoro, round_trip_ms also includes the request over the wire.
let lastSttMetric = null;
let lastTtsMetric = null;

function reportVoiceMetric(kind, serverMs, roundTripMs) {
  const entry = { serverMs, roundTripMs };
  if (kind === "stt") lastSttMetric = entry;
  else lastTtsMetric = entry;

  console.debug(`[voice-metrics] ${kind}: server=${serverMs}ms round-trip=${roundTripMs}ms`);

  const fmt = (e) => (e ? `${e.serverMs ?? "?"}ms (rt ${e.roundTripMs}ms)` : "—");
  $("voice-metrics").textContent = `STT ${fmt(lastSttMetric)} · TTS ${fmt(lastTtsMetric)}`;
}

const MAX_TTS_QUEUE = 4;
let prefetch = null; // { text, promise } for the chunk after the one currently playing

function fetchTtsBlob(text) {
  const t0 = performance.now();
  return fetch("/api/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, voice: selectedVoice }),
  }).then((res) => {
    if (!res.ok) throw new Error(`TTS request failed: ${res.status}`);
    const synthMs = res.headers.get("X-Synth-Ms");
    const roundTripMs = Math.round(performance.now() - t0);
    reportVoiceMetric("tts", synthMs !== null ? Number(synthMs) : null, roundTripMs);
    return res.blob();
  });
}

// Splitting into sentences means the first sentence starts playing as soon as
// it's synthesized instead of waiting for the whole (often multi-sentence)
// reply to finish, which is what made audio feel like it lagged the text by a
// long time.
function splitIntoSentences(text) {
  const parts = text.match(/[^.!?,\n]+[.!?,]*(\s+|$)/g) || [text];
  return parts.map((p) => p.trim()).filter(Boolean);
}

function speak(text) {
  if (!text || silentMode) return;
  speechQueue.push(...splitIntoSentences(text));
  if (speechQueue.length > MAX_TTS_QUEUE) {
    // Cap the backlog so a burst of messages can't leave audio lagging minutes
    // behind text that's already on screen — drop the oldest, keep the latest.
    speechQueue = speechQueue.slice(-MAX_TTS_QUEUE);
    prefetch = null; // queue changed under us; any in-flight prefetch is now stale
  }
  if (!speaking) processSpeechQueue();
}

async function processSpeechQueue() {
  micResumeAt = Date.now() + MIC_RESUME_GRACE_MS;
  if (speechQueue.length === 0) {
    speaking = false;
    prefetch = null;
    return;
  }
  if (userTalking) {
    // Don't start a new reply on top of the candidate mid-sentence — wait
    // for them to pause. Keep `speaking` true so speak() doesn't re-enter.
    speaking = true;
    setTimeout(processSpeechQueue, 250);
    return;
  }
  speaking = true;
  const text = speechQueue.shift();

  try {
    const blob = prefetch && prefetch.text === text ? await prefetch.promise : await fetchTtsBlob(text);
    prefetch = null;

    // Kick off synthesis for the next chunk now, in parallel with this one's
    // playback, so there's no gap waiting on Kokoro between sentences.
    if (speechQueue.length > 0) {
      const nextText = speechQueue[0];
      prefetch = { text: nextText, promise: fetchTtsBlob(nextText) };
    }

    if (currentTtsUrl) URL.revokeObjectURL(currentTtsUrl);
    currentTtsUrl = URL.createObjectURL(blob);
    ttsAudio.src = currentTtsUrl;
    ttsAudio.onended = processSpeechQueue;
    ttsAudio.onerror = processSpeechQueue;
    await ttsAudio.play();
  } catch (err) {
    console.warn("TTS failed:", err.message);
    prefetch = null;
    processSpeechQueue();
  }
}

function isSpeaking() {
  return !ttsAudio.paused;
}

function stopSpeaking() {
  speechQueue = [];
  prefetch = null;
  speaking = false;
  ttsAudio.pause();
  ttsAudio.currentTime = 0;
  micResumeAt = Date.now() + MIC_RESUME_GRACE_MS;
}

// --- Mic capture: continuous PCM16 streaming + client-side barge-in detection ---

function floatTo16BitPCM(floatSamples) {
  const buffer = new ArrayBuffer(floatSamples.length * 2);
  const view = new DataView(buffer);
  for (let i = 0; i < floatSamples.length; i++) {
    const s = Math.max(-1, Math.min(1, floatSamples[i]));
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buffer;
}

function rms(floatSamples) {
  let sum = 0;
  for (let i = 0; i < floatSamples.length; i++) sum += floatSamples[i] * floatSamples[i];
  return Math.sqrt(sum / floatSamples.length);
}

// Browsers don't reliably honor `new AudioContext({sampleRate: 16000})` for a
// context tied to real mic hardware (Safari especially just uses the device's
// native rate). The backend's VAD + Whisper pipeline requires true 16kHz PCM, so
// we always resample down from whatever rate the context actually reports.
function resampleTo16k(floatSamples, inputSampleRate) {
  if (inputSampleRate === 16000) return floatSamples;
  const ratio = inputSampleRate / 16000;
  const outLength = Math.floor(floatSamples.length / ratio);
  const result = new Float32Array(outLength);
  for (let i = 0; i < outLength; i++) {
    const srcIndex = i * ratio;
    const i0 = Math.floor(srcIndex);
    const i1 = Math.min(i0 + 1, floatSamples.length - 1);
    const frac = srcIndex - i0;
    result[i] = floatSamples[i0] * (1 - frac) + floatSamples[i1] * frac;
  }
  return result;
}

// Audio chunks must reach the server in the exact order they were captured, or
// the VAD/segmenter reconstructs a corrupted byte stream. `onaudioprocess` fires
// on a fixed schedule regardless of whether the previous send finished, so this
// queue sends strictly one request at a time and merges any backlog into a
// single batched follow-up rather than firing overlapping, unordered requests.
let pendingAudioChunks = [];
let sendingAudio = false;

function mergeBuffers(buffers) {
  const total = buffers.reduce((sum, b) => sum + b.byteLength, 0);
  const merged = new Uint8Array(total);
  let offset = 0;
  for (const b of buffers) {
    merged.set(new Uint8Array(b), offset);
    offset += b.byteLength;
  }
  return merged.buffer;
}

function enqueueAudioChunk(buffer) {
  pendingAudioChunks.push(buffer);
  if (!sendingAudio) drainAudioQueue();
}

async function drainAudioQueue() {
  if (pendingAudioChunks.length === 0) {
    sendingAudio = false;
    return;
  }
  sendingAudio = true;
  const toSend = mergeBuffers(pendingAudioChunks);
  pendingAudioChunks = [];

  try {
    const t0 = performance.now();
    const data = await api(`/api/session/${sessionId}/audio_chunk`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: toSend,
    });
    const roundTripMs = Math.round(performance.now() - t0);
    if (data.stt_ms !== undefined) reportVoiceMetric("stt", data.stt_ms, roundTripMs);
    if (data.transcript) addChatBubble("user", `(spoken) ${data.transcript}`);
    if (data.reply) {
      addChatBubble("assistant", data.reply);
      speak(data.reply);
    }
  } catch (err) {
    // Transient network hiccups on a single chunk shouldn't interrupt the session.
    console.warn("audio_chunk failed:", err.message);
  }

  drainAudioQueue();
}

async function enableMic() {
  pendingAudioChunks = [];
  sendingAudio = false;
  // echoCancellation back on: turning it off to help barge-in caused a worse
  // bug — on speakers, the TTS's own leaked audio was strong enough to pass
  // the VAD and get transcribed verbatim as if the candidate said it (real
  // report: an assistant message echoed back as a "(spoken)" user turn).
  // Barge-in is instead helped by NOT forwarding mic audio to the STT
  // pipeline at all while TTS is speaking (see the isSpeaking() gate below)
  // — belt and suspenders against self-echo regardless of how good the
  // browser's AEC is.
  mediaStream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true },
  });
  audioContext = new AudioContext({ sampleRate: 16000 });
  sourceNode = audioContext.createMediaStreamSource(mediaStream);
  processorNode = audioContext.createScriptProcessor(4096, 1, 1);

  bargeInStreak = 0;
  processorNode.onaudioprocess = (event) => {
    const raw = event.inputBuffer.getChannelData(0);
    const samples = resampleTo16k(raw, audioContext.sampleRate);

    const level = rms(samples);
    if (level > BARGE_IN_RMS_THRESHOLD) {
      bargeInStreak++;
      if (bargeInStreak >= BARGE_IN_CONSECUTIVE_BUFFERS) {
        // Tracked independent of isSpeaking() — a reply that finishes
        // computing while the candidate is mid-sentence on their next
        // thought should wait for them to pause, not just avoid
        // interrupting whatever happened to already be playing.
        userTalking = true;
        clearTimeout(userTalkingTimeout);
        userTalkingTimeout = setTimeout(() => { userTalking = false; }, USER_TALKING_HANGOVER_MS);
      }
      if (isSpeaking()) {
        console.debug(`[barge-in] rms=${level.toFixed(4)} streak=${bargeInStreak}/${BARGE_IN_CONSECUTIVE_BUFFERS}`);
      }
      if (bargeInStreak >= BARGE_IN_CONSECUTIVE_BUFFERS && isSpeaking()) {
        console.debug("[barge-in] triggered — stopping TTS");
        stopSpeaking();
      }
    } else {
      bargeInStreak = 0;
    }

    // Never forward mic audio to the STT pipeline while our own TTS is
    // playing — otherwise there's nothing stopping leaked/reflected TTS
    // audio from being transcribed as if the candidate said it. A short
    // grace period after TTS actually stops covers room reverberation /
    // buffered audio still settling. Barge-in detection above still runs
    // unconditionally since it's a local RMS check, not a transcription.
    if (!isSpeaking() && Date.now() >= micResumeAt) {
      enqueueAudioChunk(floatTo16BitPCM(samples));
    }
  };

  sourceNode.connect(processorNode);
  processorNode.connect(audioContext.destination);

  await api(`/api/session/${sessionId}/mic`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: true }),
  });
}

async function disableMic() {
  if (processorNode) processorNode.disconnect();
  if (sourceNode) sourceNode.disconnect();
  if (audioContext) await audioContext.close();
  if (mediaStream) mediaStream.getTracks().forEach((t) => t.stop());
  processorNode = sourceNode = audioContext = mediaStream = null;
  pendingAudioChunks = [];
  bargeInStreak = 0;

  await api(`/api/session/${sessionId}/mic`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: false }),
  });
}

async function toggleMic() {
  const btn = $("mic-btn");
  const status = $("mic-status");
  try {
    if (!micEnabled) {
      await enableMic();
      micEnabled = true;
      btn.textContent = "🎤 Mic on";
      btn.classList.add("active");
      status.textContent = "";
    } else {
      await disableMic();
      micEnabled = false;
      btn.textContent = "🎤 Mic off";
      btn.classList.remove("active");
    }
  } catch (err) {
    status.textContent = err.message;
  }
}

// Silent mode: mutes interviewer TTS output and disables the mic, so the whole
// interview can be conducted by typing only — for practicing in public spaces
// without making noise in either direction.
function updateMicButtonDisabled() {
  $("mic-btn").disabled = isPaused || silentMode;
}

async function toggleSilentMode() {
  silentMode = !silentMode;
  const btn = $("silent-mode-btn");
  btn.classList.toggle("active", silentMode);

  if (silentMode) {
    stopSpeaking();
    if (micEnabled) await toggleMic();
  }
  updateMicButtonDisabled();
}

// --- Pause: freezes the timer, stops proactive hints, mutes audio, and locks
// interaction controls, for stepping away mid-interview without losing state. ---

let isPaused = false;
let pausedElapsedMs = 0;

function setControlsDisabledForPause(disabled) {
  $("run-btn").disabled = disabled;
  $("chat-input").disabled = disabled;
  $("chat-send-btn").disabled = disabled;
  $("silent-mode-btn").disabled = disabled;
  $("review-diagram-btn").disabled = disabled;
  $("suggest-update-btn").disabled = disabled;
  updateMicButtonDisabled();
}

async function togglePause() {
  isPaused = !isPaused;
  const btn = $("pause-btn");

  if (isPaused) {
    pausedElapsedMs = Date.now() - startTime;
    clearInterval(timerInterval);
    clearInterval(proactivePollTimer);
    stopSpeaking();
    if (micEnabled) await toggleMic();
    setControlsDisabledForPause(true);
    btn.textContent = "▶ Resume";
    btn.classList.add("active");
  } else {
    startTime = Date.now() - pausedElapsedMs;
    timerInterval = setInterval(updateTimerDisplay, 1000);
    startProactivePolling();
    setControlsDisabledForPause(false);
    btn.textContent = "⏸ Pause";
    btn.classList.remove("active");
  }
}

// Leaves the interview screen without ending the interview — it's already
// persisted server-side (see backend/persistence.py), so this is just a
// clean teardown of client-side state; the "Resume a session" list picks it
// right back up.
async function exitInterview() {
  if (micEnabled) await toggleMic();
  stopSpeaking();
  clearInterval(timerInterval);
  clearInterval(proactivePollTimer);
  clearTimeout(codeSnapshotTimer);

  if (editor) {
    editor.dispose();
    editor = null;
  }

  isPaused = false;
  setControlsDisabledForPause(false);
  $("pause-btn").textContent = "⏸ Pause";
  $("pause-btn").classList.remove("active");

  sessionId = null;
  currentProblem = null;

  $("interview-screen").classList.add("hidden");
  $("start-screen").classList.remove("hidden");
  await loadResumableSessions();
}

// --- Passive monitoring: debounced code snapshots + proactive-hint polling ---

function scheduleCodeSnapshot() {
  clearTimeout(codeSnapshotTimer);
  codeSnapshotTimer = setTimeout(async () => {
    try {
      await api(`/api/session/${sessionId}/code_snapshot`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: editor.getValue() }),
      });
    } catch (err) {
      console.warn("code_snapshot failed:", err.message);
    }
  }, CODE_SNAPSHOT_DEBOUNCE_MS);
}

function startProactivePolling() {
  proactivePollTimer = setInterval(async () => {
    try {
      const data = await api(`/api/session/${sessionId}/proactive`);
      if (data.message) {
        addChatBubble("assistant", data.message);
        speak(data.message);
      }
    } catch (err) {
      console.warn("proactive poll failed:", err.message);
    }
  }, PROACTIVE_POLL_MS);
}

async function startInterview() {
  const problemId = $("problem-select").value || null;
  const company = $("company-select").value || null;
  $("start-error").textContent = "";
  $("start-btn").disabled = true;
  $("start-btn").textContent = "Starting...";

  try {
    const data = await api("/api/session/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        problem_id: problemId,
        company,
        interview_type: interviewType,
        voice: selectedVoice,
      }),
    });

    sessionId = data.session_id;
    currentProblem = data.problem;
    const isDesign = data.interview_type === "system_design";

    $("start-screen").classList.add("hidden");
    $("interview-screen").classList.remove("hidden");
    $("editor-panel").classList.toggle("hidden", isDesign);
    $("canvas-panel").classList.toggle("hidden", !isDesign);

    renderProblem(data.problem, data.company, isDesign);

    if (isDesign) {
      resetCanvasState();
      initCanvas();
    } else {
      await initMonaco(data.problem.starter_code);
      editor.onDidChangeModelContent(scheduleCodeSnapshot);
    }

    addChatBubble("assistant", data.opening_message);
    speak(data.opening_message);
    startTimer();
    startProactivePolling();
    if (!silentMode) await toggleMic();
  } catch (err) {
    $("start-error").textContent = err.message;
    $("start-btn").disabled = false;
    $("start-btn").textContent = "Start Interview";
  }
}

// --- Resuming a previous session: sessions survive a server restart (see
// backend/persistence.py), so the start screen offers a way back into them —
// one recency-sorted list, a colored left edge distinguishing the type. ---

function formatRelativeTime(epochSeconds) {
  const diffMs = Date.now() - epochSeconds * 1000;
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

async function loadResumableSessions() {
  try {
    const sessions = await api("/api/sessions");
    const list = $("resume-list");
    list.innerHTML = "";

    for (const s of sessions) {
      const isDesign = s.interview_type === "system_design";
      const li = document.createElement("li");
      li.className = `resume-item ${isDesign ? "design" : "coding"}`;
      const title = document.createElement("div");
      title.className = "resume-title";
      title.textContent = s.problem_title;
      const meta = document.createElement("div");
      meta.className = "resume-meta";
      const parts = [
        isDesign ? "System Design" : "Coding",
        s.company || "General",
        `${s.message_count} messages`,
        formatRelativeTime(s.last_activity),
      ];
      meta.textContent = parts.join(" · ");
      li.appendChild(title);
      li.appendChild(meta);
      li.addEventListener("click", () => resumeSession(s.session_id));
      list.appendChild(li);
    }

    $("resume-panel").classList.toggle("hidden", sessions.length === 0);
  } catch (err) {
    console.warn("loadResumableSessions failed:", err.message);
  }
}

// Coding-mode chat history stores the candidate's code appended to their
// message (so the LLM sees it) — strip that back off for display, so a
// resumed chat bubble shows what the candidate actually typed.
function displayContent(content) {
  const marker = "\n\n--- Candidate's current code ---";
  const idx = content.indexOf(marker);
  return idx === -1 ? content : content.slice(0, idx);
}

async function resumeSession(id) {
  try {
    const data = await api(`/api/session/${id}`);
    const isDesign = data.interview_type === "system_design";

    sessionId = data.session_id;
    currentProblem = data.problem;
    interviewType = data.interview_type;
    selectedVoice = data.voice;
    document.querySelectorAll(".voice-btn").forEach((b) => b.classList.toggle("active", b.dataset.voice === selectedVoice));

    $("start-screen").classList.add("hidden");
    $("interview-screen").classList.remove("hidden");
    $("editor-panel").classList.toggle("hidden", isDesign);
    $("canvas-panel").classList.toggle("hidden", !isDesign);

    renderProblem(data.problem, data.company, isDesign);

    if (isDesign) {
      resetCanvasState();
      let elements = [];
      try {
        elements = JSON.parse(data.last_code || "[]");
      } catch {
        elements = [];
      }
      initCanvas(elements);
    } else {
      await initMonaco(data.last_code || data.problem.starter_code);
      editor.onDidChangeModelContent(scheduleCodeSnapshot);
    }

    $("chat-messages").innerHTML = "";
    for (const turn of data.history) {
      addChatBubble(turn.role, displayContent(turn.content));
    }

    startTimer(data.start_time * 1000);
    startProactivePolling();
    if (!silentMode) await toggleMic();
  } catch (err) {
    $("start-error").textContent = err.message;
  }
}

async function runCode() {
  $("run-btn").disabled = true;
  try {
    const results = await api(`/api/session/${sessionId}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: editor.getValue() }),
    });
    renderResults(results);
  } catch (err) {
    $("results-summary").textContent = err.message;
  } finally {
    $("run-btn").disabled = false;
  }
}

async function reviewDiagram() {
  const btn = $("review-diagram-btn");
  btn.disabled = true;
  btn.textContent = "Reviewing...";
  try {
    const data = await api(`/api/session/${sessionId}/design_review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image_b64: await exportCanvasImage() }),
    });
    addChatBubble(data.error ? "error" : "assistant", data.reply);
    if (!data.error) speak(data.reply);
  } catch (err) {
    addChatBubble("error", err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Review my diagram";
  }
}

async function suggestDiagramUpdate() {
  const btn = $("suggest-update-btn");
  btn.disabled = true;
  btn.textContent = "Thinking...";
  try {
    const data = await api(`/api/session/${sessionId}/design_update`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image_b64: await exportCanvasImage(),
        current_elements: getSimplifiedElements(),
      }),
    });
    if (data.error) {
      addChatBubble("error", data.error);
    } else if (data.elements && data.elements.length > 0) {
      applySuggestedElements(data.elements);
      addChatBubble(
        "assistant",
        `Added ${data.elements.length} new element(s) to your diagram — undo with Cmd+Z if you'd rather do it yourself.`
      );
    } else {
      addChatBubble("assistant", "Nothing concrete to add right now — keep going and try again later.");
    }
  } catch (err) {
    addChatBubble("error", err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "🪄 Suggest update";
  }
}

async function sendChat() {
  const input = $("chat-input");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  addChatBubble("user", message);

  $("chat-send-btn").disabled = true;
  try {
    const data = await api(`/api/session/${sessionId}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, code: editor ? editor.getValue() : "" }),
    });
    addChatBubble(data.error ? "error" : "assistant", data.reply);
  } catch (err) {
    addChatBubble("error", err.message);
  } finally {
    $("chat-send-btn").disabled = false;
  }
}

$("start-btn").addEventListener("click", startInterview);
$("run-btn").addEventListener("click", runCode);
$("chat-send-btn").addEventListener("click", sendChat);
$("mic-btn").addEventListener("click", toggleMic);
$("silent-mode-btn").addEventListener("click", toggleSilentMode);
$("review-diagram-btn").addEventListener("click", reviewDiagram);
$("suggest-update-btn").addEventListener("click", suggestDiagramUpdate);
$("pause-btn").addEventListener("click", togglePause);
$("exit-btn").addEventListener("click", exitInterview);
$("chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendChat();
  }
});

// --- Collapsible side panels, to free up room for the editor/canvas ---

$("problem-panel-toggle").addEventListener("click", () => {
  const collapsed = $("problem-panel").classList.toggle("collapsed");
  $("main-layout").classList.toggle("left-collapsed", collapsed);
  $("problem-panel-toggle").textContent = collapsed ? "▶" : "◀";
  $("problem-panel-toggle").title = collapsed ? "Expand" : "Collapse";
});

$("chat-panel-toggle").addEventListener("click", () => {
  const collapsed = $("chat-panel").classList.toggle("collapsed");
  $("main-layout").classList.toggle("right-collapsed", collapsed);
  $("chat-panel-toggle").textContent = collapsed ? "◀" : "▶";
  $("chat-panel-toggle").title = collapsed ? "Expand" : "Collapse";
});

loadPickers()
  .then(() => {
    $("start-screen").classList.remove("hidden");
    return loadResumableSessions();
  })
  .catch(() => {}); // a 401 already triggered showGateScreen() inside api()
