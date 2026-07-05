const chatLog = document.getElementById("chat-log");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const taskGrid = document.getElementById("task-grid");
const streamGallery = document.getElementById("stream-gallery");
const streamCardsEl = document.getElementById("stream-cards");
const addStreamBtn = document.getElementById("add-stream-btn");
const modeBadge = document.getElementById("mode-badge");

async function api(path, options) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("\n", "&#10;");
}

function addMessage(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
}

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = chatInput.value.trim();
  if (!message) return;
  addMessage("user", message);
  chatInput.value = "";
  try {
    const { reply } = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message }),
    });
    addMessage("assistant", reply);
  } catch (err) {
    addMessage("assistant", `Error: ${err.message}`);
  }
  refreshTasks();
});

function renderTasks(views) {
  taskGrid.innerHTML = "";
  if (views.length === 0) {
    taskGrid.innerHTML = '<p class="hint">No tasks yet — describe one in the chat.</p>';
    return;
  }
  for (const view of views) {
    const task = view.task;
    const result = view.last_result;
    const approval = view.pending_approval;
    const tile = document.createElement("div");
    tile.className = "tile";
    if (result && result.observation.anomaly_detected) tile.classList.add("anomaly");
    if (approval) tile.classList.add("approval");

    const streams = task.streams.map((s) => s.stream_id).join(", ") || "none";
    const streamLines = task.streams.map((s) => `${s.stream_id} ${s.kind}`).join("\n");
    let html = `
      <h3>${escapeHtml(task.title)}</h3>
      <div class="meta">
        <span class="pill ${task.status}">${escapeHtml(task.status)}</span>
        <span class="pill">every ${task.interval_seconds}s</span>
        ${task.subject_id ? `<span class="pill">subject: ${escapeHtml(task.subject_id)}</span>` : ""}
      </div>
      <div class="meta">streams: ${escapeHtml(streams)}</div>
    `;
    if (result) {
      html += `<div class="summary">${escapeHtml(result.observation.summary)}</div>`;
    } else {
      html += `<div class="summary hint">Waiting for the first scheduled run…</div>`;
    }
    if (approval) {
      html += `
        <div class="approval-box">
          <strong>Needs your approval</strong><br/>
          Action: ${escapeHtml(approval.action)}<br/>
          Reason: ${escapeHtml(approval.reason)} (${escapeHtml(approval.risk)} risk)
          <div class="row" style="margin-top:6px;">
            <button class="approve" data-approve="${approval.approval_id}">Approve</button>
            <button class="deny" data-deny="${approval.approval_id}">Deny</button>
          </div>
        </div>
      `;
    }
    html += `
      <div class="row">
        <button data-run="${task.task_id}">Run now</button>
        ${task.status === "active"
          ? `<button data-pause="${task.task_id}">Pause</button>`
          : `<button data-resume="${task.task_id}">Resume</button>`}
        <button data-delete="${task.task_id}">Delete</button>
      </div>
      <details class="edit-task">
        <summary>Edit details</summary>
        <form data-edit-task="${task.task_id}">
          <label>
            Title
            <input name="title" type="text" value="${escapeAttr(task.title)}" />
          </label>
          <label>
            Description
            <textarea name="description" rows="2">${escapeHtml(task.description)}</textarea>
          </label>
          <label>
            Focus
            <textarea name="focus" rows="3">${escapeHtml(task.focus)}</textarea>
          </label>
          <label>
            Check every
            <input name="interval_seconds" type="number" min="1" step="1" value="${task.interval_seconds}" />
            <span class="unit">seconds</span>
          </label>
          <label>
            Streams
            <textarea name="streams" rows="2" placeholder="kitchen-cam image">${escapeHtml(streamLines)}</textarea>
          </label>
          <label class="checkline">
            <input name="requires_approval" type="checkbox" ${task.requires_approval ? "checked" : ""} />
            Require approval for proposed actions
          </label>
          <div class="row">
            <button class="primary" type="submit">Save</button>
          </div>
        </form>
      </details>
    `;
    tile.innerHTML = html;
    taskGrid.appendChild(tile);
  }

  taskGrid.querySelectorAll("[data-approve]").forEach((btn) =>
    btn.addEventListener("click", () => decide(btn.dataset.approve, true))
  );
  taskGrid.querySelectorAll("[data-deny]").forEach((btn) =>
    btn.addEventListener("click", () => decide(btn.dataset.deny, false))
  );
  taskGrid.querySelectorAll("[data-run]").forEach((btn) =>
    btn.addEventListener("click", () => api(`/api/tasks/${btn.dataset.run}/run_now`, { method: "POST" }).then(refreshTasks))
  );
  taskGrid.querySelectorAll("[data-pause]").forEach((btn) =>
    btn.addEventListener("click", () => api(`/api/tasks/${btn.dataset.pause}/pause`, { method: "POST" }).then(refreshTasks))
  );
  taskGrid.querySelectorAll("[data-resume]").forEach((btn) =>
    btn.addEventListener("click", () => api(`/api/tasks/${btn.dataset.resume}/resume`, { method: "POST" }).then(refreshTasks))
  );
  taskGrid.querySelectorAll("[data-delete]").forEach((btn) =>
    btn.addEventListener("click", () => api(`/api/tasks/${btn.dataset.delete}`, { method: "DELETE" }).then(refreshTasks))
  );
  taskGrid.querySelectorAll("[data-edit-task]").forEach((form) =>
    form.addEventListener("submit", (event) => saveTaskEdit(event, form))
  );
}

function parseStreams(value) {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [stream_id, kind = stream_id.endsWith("-mic") ? "audio" : "image"] = line.split(/\s+/);
      if (!["image", "audio"].includes(kind)) {
        throw new Error(`Stream "${stream_id}" must use kind image or audio`);
      }
      return { stream_id, kind };
    });
}

async function saveTaskEdit(event, form) {
  event.preventDefault();
  const data = new FormData(form);
  const interval = Number(data.get("interval_seconds"));
  if (!Number.isInteger(interval) || interval < 1) {
    addMessage("assistant", "Task interval must be at least 1 second.");
    return;
  }
  let streams;
  try {
    streams = parseStreams(String(data.get("streams") || ""));
  } catch (err) {
    addMessage("assistant", `Error: ${err.message}`);
    return;
  }
  await api(`/api/tasks/${form.dataset.editTask}`, {
    method: "PATCH",
    body: JSON.stringify({
      title: String(data.get("title") || "").trim() || "Untitled task",
      description: String(data.get("description") || "").trim(),
      focus: String(data.get("focus") || "").trim(),
      interval_seconds: interval,
      streams,
      requires_approval: data.has("requires_approval"),
    }),
  });
  refreshTasks();
}

async function decide(approvalId, approve) {
  await api(`/api/approvals/${approvalId}/decision`, {
    method: "POST",
    body: JSON.stringify({ approve }),
  });
  refreshTasks();
}

async function refreshTasks() {
  const views = await api("/api/tasks");
  renderTasks(views);
}

// The "Live view" gallery shows the latest frame + clip of EVERY connected
// stream, whichever phone or script pushed it — not just this device's own
// camera. Tiles are updated in place (keyed by stream name) so refreshing the
// image doesn't interrupt audio that's mid-playback.

const streamTiles = new Map(); // name -> { el, img, noCam, audio, meta }
let pollTick = 0; // bumped each poll so image URLs bust the browser cache

function createGalleryTile(name) {
  const el = document.createElement("div");
  el.className = "stream-tile";
  el.innerHTML = `
    <div class="stream-tile-media">
      <img class="frame" alt="${name} camera" />
      <div class="no-cam hint">no camera</div>
    </div>
    <audio class="clip" controls preload="none"></audio>
    <div class="stream-tile-meta hint"></div>
  `;
  streamGallery.appendChild(el);
  const tile = {
    el,
    img: el.querySelector(".frame"),
    noCam: el.querySelector(".no-cam"),
    audio: el.querySelector(".clip"),
    meta: el.querySelector(".stream-tile-meta"),
  };
  streamTiles.set(name, tile);
  return tile;
}

function updateGalleryTile(tile, p) {
  if (p.camera) {
    tile.img.src = `/api/streams/${p.camera.stream_id}/latest?t=${pollTick}`;
    tile.img.style.display = "block";
    tile.noCam.style.display = "none";
  } else {
    tile.img.removeAttribute("src");
    tile.img.style.display = "none";
    tile.noCam.style.display = "flex";
  }
  if (p.mic) {
    tile.audio.style.display = "block";
    // Only refresh the clip when it isn't playing, so we never cut off audio.
    if (tile.audio.paused) tile.audio.src = `/api/streams/${p.mic.stream_id}/latest?t=${pollTick}`;
  } else {
    tile.audio.style.display = "none";
    tile.audio.removeAttribute("src");
  }
  const cam = p.camera ? `📷 ${p.camera.age_seconds}s` : "📷 —";
  const mic = p.mic ? `🎙 ${p.mic.age_seconds}s` : "🎙 —";
  tile.meta.innerHTML = `<strong>${p.name}</strong> <small>(${p.source})</small> · ${cam} · ${mic}`;
}

async function refreshStreams() {
  const pairs = await api("/api/streams/pairs");
  pollTick += 1;
  streamGallery.querySelector(".gallery-empty")?.remove();
  const seen = new Set();
  for (const p of pairs) {
    seen.add(p.name);
    updateGalleryTile(streamTiles.get(p.name) || createGalleryTile(p.name), p);
  }
  for (const [name, tile] of streamTiles) {
    if (!seen.has(name)) {
      tile.el.remove();
      streamTiles.delete(name);
    }
  }
  if (streamTiles.size === 0 && !streamGallery.querySelector(".gallery-empty")) {
    const empty = document.createElement("span");
    empty.className = "gallery-empty hint";
    empty.textContent = "No streams connected yet.";
    streamGallery.appendChild(empty);
  }
}

async function refreshStatus() {
  const status = await api("/api/status");
  modeBadge.textContent = status.mock_mode ? "mock mode" : "live mode";
}

// ---------- multi-stream live capture: each stream = camera + voice ----------
//
// A stream card owns one browser MediaStream (camera + mic together). Every
// few seconds it grabs a JPEG frame off the video track and posts it to
// `<name>-cam`, and records a short audio clip off the mic track and posts it
// to `<name>-mic`. Many cards can run at once — different cameras, different
// places — which is what "multi-stream, each stream camera+voice" means here.

const FRAME_INTERVAL_MS = 4000;
const CLIP_LENGTH_MS = 4000;

let videoDevices = [];
let streamSeq = 0;

async function refreshVideoDevices() {
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    videoDevices = devices.filter((d) => d.kind === "videoinput");
  } catch {
    videoDevices = [];
  }
  document.querySelectorAll("select.cam-select").forEach(fillCameraOptions);
}

function fillCameraOptions(select) {
  const current = select.value;
  select.innerHTML =
    '<option value="">default camera</option>' +
    videoDevices
      .map((d, i) => `<option value="${d.deviceId}">${d.label || `camera ${i + 1}`}</option>`)
      .join("");
  if (current) select.value = current;
}

function createStreamCard(defaultName) {
  const localId = ++streamSeq;
  const card = document.createElement("div");
  card.className = "stream-card";
  card.innerHTML = `
    <div class="stream-card-head">
      <input class="name-input" type="text" value="${defaultName}" />
      <button class="toggle">Start</button>
      <button class="remove" title="Remove stream">✕</button>
    </div>
    <div class="stream-card-modes">
      <label><input type="checkbox" class="use-cam" checked /> Camera</label>
      <label><input type="checkbox" class="use-mic" checked /> Mic</label>
      <select class="cam-select"></select>
    </div>
    <video class="preview" autoplay muted playsinline></video>
    <div class="stream-card-status hint">idle</div>
  `;
  streamCardsEl.appendChild(card);

  const nameInput = card.querySelector(".name-input");
  const camSelect = card.querySelector(".cam-select");
  const camCheck = card.querySelector(".use-cam");
  const micCheck = card.querySelector(".use-mic");
  const toggleBtn = card.querySelector(".toggle");
  const removeBtn = card.querySelector(".remove");
  const preview = card.querySelector(".preview");
  const statusEl = card.querySelector(".stream-card-status");
  fillCameraOptions(camSelect);

  // The camera picker only matters when this stream includes a camera.
  const syncCamSelect = () => (camSelect.disabled = !camCheck.checked);
  camCheck.addEventListener("change", syncCamSelect);
  syncCamSelect();

  const canvas = document.createElement("canvas");
  let media = null;
  let frameTimer = null;
  let recorder = null;
  let running = false;

  const setStatus = (t) => (statusEl.textContent = t);
  const baseName = () => nameInput.value.trim() || `stream-${localId}`;

  async function start() {
    const useCam = camCheck.checked;
    const useMic = micCheck.checked;
    if (!useCam && !useMic) {
      setStatus("pick camera, mic, or both first");
      return;
    }
    const constraints = {};
    if (useCam) constraints.video = camSelect.value ? { deviceId: { exact: camSelect.value } } : true;
    if (useMic) constraints.audio = true;
    try {
      media = await navigator.mediaDevices.getUserMedia(constraints);
    } catch (err) {
      setStatus(`error: ${err.message}`);
      return;
    }
    running = true;
    toggleBtn.textContent = "Stop";
    toggleBtn.classList.add("primary");
    nameInput.disabled = true;
    camSelect.disabled = true;
    camCheck.disabled = true;
    micCheck.disabled = true;
    refreshVideoDevices(); // device labels become available once permission is granted
    if (useCam) {
      preview.srcObject = media;
      preview.style.display = "block";
      startFrameLoop();
    }
    if (useMic) startClipLoop();
    setStatus(
      useCam && useMic ? "streaming camera + voice…" : useCam ? "streaming camera…" : "streaming voice…"
    );
  }

  function startFrameLoop() {
    frameTimer = setInterval(() => {
      if (!running) return;
      const w = preview.videoWidth || 320;
      const h = preview.videoHeight || 240;
      canvas.width = w;
      canvas.height = h;
      canvas.getContext("2d").drawImage(preview, 0, 0, w, h);
      const dataUrl = canvas.toDataURL("image/jpeg", 0.7);
      api(`/api/streams/${baseName()}-cam/image`, {
        method: "POST",
        body: JSON.stringify({ data_url: dataUrl }),
      }).catch((err) => setStatus(`camera error: ${err.message}`));
    }, FRAME_INTERVAL_MS);
  }

  function startClipLoop() {
    const audioTracks = media ? media.getAudioTracks() : [];
    if (audioTracks.length === 0) return;
    const audioOnly = new MediaStream(audioTracks);
    const recordClip = () => {
      if (!running) return;
      const chunks = [];
      recorder = new MediaRecorder(audioOnly);
      recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      recorder.onstop = async () => {
        if (chunks.length) {
          const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
          const form = new FormData();
          form.append("file", blob, "clip.webm");
          try {
            await fetch(`/api/streams/${baseName()}-mic/audio`, { method: "POST", body: form });
          } catch (err) {
            setStatus(`mic error: ${err.message}`);
          }
        }
        if (running) recordClip(); // keep recording back-to-back clips while live
      };
      recorder.start();
      setTimeout(() => recorder && recorder.state === "recording" && recorder.stop(), CLIP_LENGTH_MS);
    };
    recordClip();
  }

  function stop() {
    const wasStreaming = running;
    const name = baseName();
    running = false;
    clearInterval(frameTimer);
    if (recorder && recorder.state === "recording") recorder.stop();
    if (media) media.getTracks().forEach((t) => t.stop());
    media = null;
    preview.srcObject = null;
    preview.style.display = "none";
    toggleBtn.textContent = "Start";
    toggleBtn.classList.remove("primary");
    nameInput.disabled = false;
    camCheck.disabled = false;
    micCheck.disabled = false;
    syncCamSelect();
    setStatus("idle");
    if (wasStreaming) {
      // Drop this stream from the server now so it leaves the live view
      // immediately instead of freezing until the staleness TTL expires.
      for (const suffix of ["-cam", "-mic"]) {
        fetch(`/api/streams/${name}${suffix}`, { method: "DELETE" }).catch(() => {});
      }
    }
  }

  toggleBtn.addEventListener("click", () => (running ? stop() : start()));
  removeBtn.addEventListener("click", () => {
    stop();
    card.remove();
  });
}

addStreamBtn.addEventListener("click", () => {
  // Name is set on the card itself; add it with a sensible default to edit.
  createStreamCard(`stream-${streamSeq + 1}`);
});

createStreamCard("kitchen"); // seed one ready-to-go stream card
refreshVideoDevices();
if (navigator.mediaDevices && navigator.mediaDevices.addEventListener) {
  navigator.mediaDevices.addEventListener("devicechange", refreshVideoDevices);
}

refreshStatus();
refreshTasks();
refreshStreams();
setInterval(refreshTasks, 4000);
setInterval(refreshStreams, 4000);
