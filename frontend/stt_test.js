const $ = (id) => document.getElementById(id);

let micEnabled = false;
let audioContext = null;
let mediaStream = null;
let sourceNode = null;
let processorNode = null;

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

function addLogEntry(data) {
  const log = $("log");
  const el = document.createElement("div");
  if (data.final) {
    el.className = "entry";
    el.textContent = data.final;
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${data.utterance_seconds}s of audio, transcribed in ${data.transcribe_seconds}s`;
    el.appendChild(meta);
  } else {
    el.className = "entry empty";
    el.textContent = "(utterance detected, but nothing transcribed — likely filtered as noise/silence)";
  }
  log.prepend(el);
}

// Audio chunks must reach the server in the exact order they were captured —
// the VAD/segmenter reconstructs a byte stream from them. `onaudioprocess` fires
// on a fixed schedule regardless of whether the last send finished, so firing
// sendChunk() per callback without waiting lets multiple requests overlap with
// no guaranteed ordering once the server falls behind (e.g. during a slow
// partial-transcribe call) — that's what caused both the lag and the garbled
// text. This queue sends strictly one request at a time, and if audio piles up
// while a request is in flight, it's merged into a single batched follow-up
// request rather than fired as several overlapping ones.
let pendingChunks = [];
let sending = false;

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

function enqueueChunk(buffer) {
  pendingChunks.push(buffer);
  if (!sending) drainQueue();
}

async function drainQueue() {
  if (pendingChunks.length === 0) {
    sending = false;
    return;
  }
  sending = true;
  const toSend = mergeBuffers(pendingChunks);
  pendingChunks = [];

  try {
    const res = await fetch("/api/stt_test/chunk", {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: toSend,
    });
    const data = await res.json();

    if (data.final !== null && data.final !== undefined) {
      $("partial-preview").textContent = "";
      addLogEntry(data);
    } else if (data.utterance_seconds !== undefined) {
      // an utterance was long enough to send to Whisper but produced nothing
      $("partial-preview").textContent = "";
      addLogEntry(data);
    } else if (data.partial) {
      $("partial-preview").textContent = `${data.partial} …`;
    }
  } catch (err) {
    $("status").textContent = `chunk send failed: ${err.message}`;
  }

  drainQueue();
}

async function enableMic() {
  await fetch("/api/stt_test/reset", { method: "POST" });
  pendingChunks = [];
  sending = false;

  mediaStream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true },
  });
  audioContext = new AudioContext({ sampleRate: 16000 });
  sourceNode = audioContext.createMediaStreamSource(mediaStream);
  processorNode = audioContext.createScriptProcessor(4096, 1, 1);

  processorNode.onaudioprocess = (event) => {
    const raw = event.inputBuffer.getChannelData(0);
    const samples = resampleTo16k(raw, audioContext.sampleRate);
    $("level-bar").style.width = `${Math.min(100, rms(samples) * 400)}%`;
    enqueueChunk(floatTo16BitPCM(samples));
  };

  sourceNode.connect(processorNode);
  processorNode.connect(audioContext.destination);
}

function disableMic() {
  if (processorNode) processorNode.disconnect();
  if (sourceNode) sourceNode.disconnect();
  if (audioContext) audioContext.close();
  if (mediaStream) mediaStream.getTracks().forEach((t) => t.stop());
  processorNode = sourceNode = audioContext = mediaStream = null;
  pendingChunks = [];
  $("level-bar").style.width = "0%";
  $("partial-preview").textContent = "";
}

async function toggleMic() {
  const btn = $("mic-btn");
  try {
    if (!micEnabled) {
      await enableMic();
      micEnabled = true;
      btn.textContent = "🎤 Mic on";
      btn.classList.add("active");
      $("status").textContent = "listening...";
    } else {
      disableMic();
      micEnabled = false;
      btn.textContent = "🎤 Mic off";
      btn.classList.remove("active");
      $("status").textContent = "";
    }
  } catch (err) {
    $("status").textContent = err.message;
  }
}

$("mic-btn").addEventListener("click", toggleMic);
