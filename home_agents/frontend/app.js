const chatLog = document.getElementById("chat-log");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const taskGrid = document.getElementById("task-grid");
const streamList = document.getElementById("stream-list");
const streamCardsEl = document.getElementById("stream-cards");
const newStreamNameInput = document.getElementById("new-stream-name");
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
    let html = `
      <h3>${task.title}</h3>
      <div class="meta">
        <span class="pill ${task.status}">${task.status}</span>
        <span class="pill">every ${task.interval_seconds}s</span>
        ${task.subject_id ? `<span class="pill">subject: ${task.subject_id}</span>` : ""}
      </div>
      <div class="meta">streams: ${streams}</div>
    `;
    if (result) {
      html += `<div class="summary">${result.observation.summary}</div>`;
    } else {
      html += `<div class="summary hint">Waiting for the first scheduled run…</div>`;
    }
    if (approval) {
      html += `
        <div class="approval-box">
          <strong>Needs your approval</strong><br/>
          Action: ${approval.action}<br/>
          Reason: ${approval.reason} (${approval.risk} risk)
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

async function refreshStreams() {
  const pairs = await api("/api/streams/pairs");
  if (pairs.length === 0) {
    streamList.innerHTML = '<span class="hint">No streams connected yet.</span>';
    return;
  }
  streamList.innerHTML = pairs
    .map((p) => {
      const cam = p.camera ? `📷 ${p.camera.age_seconds}s` : "📷 —";
      const mic = p.mic ? `🎙 ${p.mic.age_seconds}s` : "🎙 —";
      return `<span class="stream-chip">${p.name} <small>(${p.source})</small> · ${cam} · ${mic}</span>`;
    })
    .join("");
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
      <select class="cam-select"></select>
      <button class="toggle">Start</button>
      <button class="remove" title="Remove stream">✕</button>
    </div>
    <video class="preview" autoplay muted playsinline></video>
    <div class="stream-card-status hint">idle</div>
  `;
  streamCardsEl.appendChild(card);

  const nameInput = card.querySelector(".name-input");
  const camSelect = card.querySelector(".cam-select");
  const toggleBtn = card.querySelector(".toggle");
  const removeBtn = card.querySelector(".remove");
  const preview = card.querySelector(".preview");
  const statusEl = card.querySelector(".stream-card-status");
  fillCameraOptions(camSelect);

  const canvas = document.createElement("canvas");
  let media = null;
  let frameTimer = null;
  let recorder = null;
  let running = false;

  const setStatus = (t) => (statusEl.textContent = t);
  const baseName = () => nameInput.value.trim() || `stream-${localId}`;

  async function start() {
    const constraints = {
      video: camSelect.value ? { deviceId: { exact: camSelect.value } } : true,
      audio: true,
    };
    try {
      media = await navigator.mediaDevices.getUserMedia(constraints);
    } catch (err) {
      setStatus(`error: ${err.message}`);
      return;
    }
    preview.srcObject = media;
    preview.style.display = "block";
    running = true;
    toggleBtn.textContent = "Stop";
    toggleBtn.classList.add("primary");
    nameInput.disabled = true;
    camSelect.disabled = true;
    setStatus("streaming camera + voice…");
    refreshVideoDevices(); // device labels become available once permission is granted
    startFrameLoop();
    startClipLoop();
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
    camSelect.disabled = false;
    setStatus("idle");
  }

  toggleBtn.addEventListener("click", () => (running ? stop() : start()));
  removeBtn.addEventListener("click", () => {
    stop();
    card.remove();
  });
}

addStreamBtn.addEventListener("click", () => {
  createStreamCard(newStreamNameInput.value.trim() || `stream-${streamSeq + 1}`);
  newStreamNameInput.value = "";
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
