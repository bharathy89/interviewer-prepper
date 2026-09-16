// Wraps the real Excalidraw React component (loaded via CDN UMD bundle, see
// index.html) for the system-design canvas. Loaded before app.js; shares
// top-level `let`/`const` scope with it across sibling non-module <script>
// tags, so it reads/writes the same `sessionId` app.js declares.

let excalidrawRoot = null;
let excalidrawAPI = null;
let canvasSnapshotTimer = null;

function handleExcalidrawChange() {
  clearTimeout(canvasSnapshotTimer);
  canvasSnapshotTimer = setTimeout(async () => {
    if (!sessionId || !excalidrawAPI) return;
    try {
      // Also grabs a fresh image on every debounced snapshot, so the interviewer
      // has a reasonably current view of the diagram on ordinary chat/voice turns,
      // not only when "Review my diagram" is explicitly clicked.
      const image_b64 = await exportCanvasImage();
      await fetch(`/api/session/${sessionId}/canvas_snapshot`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ shapes: excalidrawAPI.getSceneElements(), image_b64 }),
      });
    } catch (err) {
      console.warn("canvas_snapshot failed:", err.message);
    }
  }, 1500);
}

function initCanvas() {
  const container = document.getElementById("excalidraw-container");
  if (!excalidrawRoot) {
    excalidrawRoot = ReactDOM.createRoot(container);
  }
  excalidrawRoot.render(
    React.createElement(ExcalidrawLib.Excalidraw, {
      excalidrawAPI: (api) => {
        excalidrawAPI = api;
      },
      onChange: handleExcalidrawChange,
      theme: "dark",
    })
  );
}

function resetCanvasState() {
  if (excalidrawAPI) {
    excalidrawAPI.resetScene();
  }
}

async function exportCanvasImage() {
  const blob = await ExcalidrawLib.exportToBlob({
    elements: excalidrawAPI.getSceneElements(),
    appState: excalidrawAPI.getAppState(),
    files: excalidrawAPI.getFiles(),
    mimeType: "image/png",
  });
  const buffer = await blob.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}

// A compact, LLM-friendly view of the current scene — real Excalidraw elements
// carry many internal fields (seed, version, roundness, ...) the model doesn't
// need and that just add prompt noise. Bound text (a box's label) is stored as
// its own separate element pointed at by containerId, so it's folded back into
// its container's "label" here rather than listed as a floating text element.
function getSimplifiedElements() {
  const elements = excalidrawAPI.getSceneElements();
  const labelByContainer = {};
  for (const el of elements) {
    if (el.type === "text" && el.containerId) {
      labelByContainer[el.containerId] = el.text;
    }
  }
  return elements
    .filter((el) => !(el.type === "text" && el.containerId))
    .map((el) => ({
      id: el.id,
      type: el.type,
      x: Math.round(el.x),
      y: Math.round(el.y),
      width: Math.round(el.width),
      height: Math.round(el.height),
      label: labelByContainer[el.id] ?? (el.type === "text" ? el.text : undefined),
    }));
}

// Two independent problems, verified directly against the Excalidraw API:
//   1. The model's guessed x/y for an arrow frequently doesn't actually span
//      between the two elements it's meant to connect — arrows would render
//      pointing into empty space, well short of the target.
//   2. convertToExcalidrawElements() can only create a live start/end *binding*
//      between elements that are BOTH present in that same conversion call. Our
//      new arrows are converted separately from whatever's already on the
//      canvas, so a binding referencing an existing element's id silently comes
//      back null — confirmed empirically, not just per the types.
// The fix for both: compute the arrow's real geometry ourselves from the actual
// positions of its start/end elements (looked up across both the existing scene
// and any new boxes in this same suggestion), and emit a plain arrow with that
// precomputed x/y/points — no start/end binding at all. This sacrifices
// "auto-reroutes if you later drag the connected box," which is an acceptable
// tradeoff for always rendering correctly connected in the first place.
// Point where a ray from box's center toward (towardX, towardY) crosses the
// box's own boundary — i.e. where an arrow aimed at this box should actually
// terminate, rather than at its center (which buries the arrowhead in the
// box's label text).
function pointOnBoxEdgeTowards(box, towardX, towardY) {
  const cx = box.x + (box.width || 0) / 2;
  const cy = box.y + (box.height || 0) / 2;
  const hw = (box.width || 0) / 2 || 1;
  const hh = (box.height || 0) / 2 || 1;
  const dx = towardX - cx;
  const dy = towardY - cy;
  if (dx === 0 && dy === 0) return { x: cx, y: cy };
  const scaleX = dx !== 0 ? hw / Math.abs(dx) : Infinity;
  const scaleY = dy !== 0 ? hh / Math.abs(dy) : Infinity;
  const t = Math.min(scaleX, scaleY);
  return { x: cx + dx * t, y: cy + dy * t };
}

function fixArrowGeometry(skeleton, existingElements) {
  const boxById = {};
  for (const el of existingElements) {
    if (el.id) boxById[el.id] = el;
  }
  for (const el of skeleton) {
    if (el.id && el.type !== "arrow") boxById[el.id] = el;
  }

  for (const el of skeleton) {
    if (el.type !== "arrow") continue;
    const startBox = el.start && boxById[el.start.id];
    const endBox = el.end && boxById[el.end.id];
    if (!startBox || !endBox) continue;
    const startCenter = { x: startBox.x + (startBox.width || 0) / 2, y: startBox.y + (startBox.height || 0) / 2 };
    const endCenter = { x: endBox.x + (endBox.width || 0) / 2, y: endBox.y + (endBox.height || 0) / 2 };
    const startPoint = pointOnBoxEdgeTowards(startBox, endCenter.x, endCenter.y);
    const endPoint = pointOnBoxEdgeTowards(endBox, startCenter.x, startCenter.y);
    el.x = startPoint.x;
    el.y = startPoint.y;
    el.points = [
      [0, 0],
      [endPoint.x - startPoint.x, endPoint.y - startPoint.y],
    ];
    delete el.start;
    delete el.end;
  }
  return skeleton;
}

// The model's guessed positions for new boxes/text frequently collide with
// what's already on the canvas (or with each other), causing overlapping,
// unreadable labels. Rather than trust that spatial reasoning, greedily nudge
// any new element straight down until it's clear of everything placed so far.
function boxesOverlap(a, b) {
  return !(
    a.x + a.width <= b.x ||
    b.x + b.width <= a.x ||
    a.y + a.height <= b.y ||
    b.y + b.height <= a.y
  );
}

function avoidOverlaps(skeleton, existingElements) {
  const placed = existingElements
    .filter((e) => e.width || e.height)
    .map((e) => ({ x: e.x, y: e.y, width: e.width || 0, height: e.height || 0 }));

  for (const el of skeleton) {
    if (el.type === "arrow" || el.type === "line") continue;
    const width = el.width || Math.max(60, (el.text || "").length * 8);
    const height = el.height || 30;
    let box = { x: el.x, y: el.y, width, height };
    let guard = 0;
    while (placed.some((p) => boxesOverlap(box, p)) && guard < 20) {
      box = { ...box, y: box.y + height + 24 };
      guard++;
    }
    el.x = box.x;
    el.y = box.y;
    placed.push(box);
  }
  return skeleton;
}

// Applies the model's suggested *additions* on top of the existing scene — never
// a full replacement, so a bad or garbled suggestion can never delete the
// candidate's own work. commitToHistory: true registers it as a normal undo-able
// step, so Cmd+Z reverts it like any other edit.
function applySuggestedElements(skeleton) {
  if (!skeleton || skeleton.length === 0) return false;
  const existingElements = excalidrawAPI.getSceneElements();
  avoidOverlaps(skeleton, existingElements);
  fixArrowGeometry(skeleton, existingElements);
  const newElements = ExcalidrawLib.convertToExcalidrawElements(skeleton);
  const merged = [...existingElements, ...newElements];
  excalidrawAPI.updateScene({ elements: merged, commitToHistory: true });
  return true;
}
