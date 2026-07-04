const chatLog = document.getElementById("chat-log");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const taskGrid = document.getElementById("task-grid");
const streamList = document.getElementById("stream-list");
const streamStatus = document.getElementById("stream-status");
const streamIdInput = document.getElementById("stream-id-input");
const cameraBtn = document.getElementById("camera-btn");
const micBtn = document.getElementById("mic-btn");
const cameraPreview = document.getElementById("camera-preview");
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
  const streams = await api("/api/streams");
  streamList.innerHTML = streams
    .map((s) => `<span class="stream-chip">${s.stream_id} (${s.kind}, ${s.source}, ${s.age_seconds}s ago)</span>`)
    .join("");
}

async function refreshStatus() {
  const status = await api("/api/status");
  modeBadge.textContent = status.mock_mode ? "mock mode" : "live mode";
}

// -------------------- live camera / microphone capture --------------------

let cameraStream = null;
let cameraTimer = null;

cameraBtn.addEventListener("click", async () => {
  if (cameraStream) {
    cameraStream.getTracks().forEach((t) => t.stop());
    clearInterval(cameraTimer);
    cameraStream = null;
    cameraPreview.style.display = "none";
    cameraBtn.textContent = "Share camera";
    return;
  }
  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({ video: true });
    cameraPreview.srcObject = cameraStream;
    cameraPreview.style.display = "block";
    cameraBtn.textContent = "Stop camera";
    const canvas = document.createElement("canvas");
    cameraTimer = setInterval(() => {
      const streamId = streamIdInput.value.trim() || "phone-cam-1";
      canvas.width = cameraPreview.videoWidth || 320;
      canvas.height = cameraPreview.videoHeight || 240;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(cameraPreview, 0, 0, canvas.width, canvas.height);
      const dataUrl = canvas.toDataURL("image/jpeg", 0.7);
      api(`/api/streams/${streamId}/image`, {
        method: "POST",
        body: JSON.stringify({ data_url: dataUrl }),
      })
        .then(() => (streamStatus.textContent = `sent frame to ${streamId}`))
        .catch((err) => (streamStatus.textContent = `error: ${err.message}`));
    }, 4000);
  } catch (err) {
    streamStatus.textContent = `camera error: ${err.message}`;
  }
});

let micStream = null;
let micRecorder = null;

micBtn.addEventListener("click", async () => {
  if (micStream) {
    micRecorder.stop();
    micStream.getTracks().forEach((t) => t.stop());
    micStream = null;
    micBtn.textContent = "Share microphone";
    return;
  }
  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    micBtn.textContent = "Stop microphone";
    startMicClip();
  } catch (err) {
    streamStatus.textContent = `microphone error: ${err.message}`;
  }
});

function startMicClip() {
  if (!micStream) return;
  const streamId = streamIdInput.value.trim() || "phone-cam-1";
  const chunks = [];
  micRecorder = new MediaRecorder(micStream);
  micRecorder.ondataavailable = (e) => chunks.push(e.data);
  micRecorder.onstop = async () => {
    const blob = new Blob(chunks, { type: micRecorder.mimeType || "audio/webm" });
    const form = new FormData();
    form.append("file", blob, "clip.webm");
    try {
      await fetch(`/api/streams/${streamId}-mic/audio`, { method: "POST", body: form });
      streamStatus.textContent = `sent clip to ${streamId}-mic`;
    } catch (err) {
      streamStatus.textContent = `error: ${err.message}`;
    }
    if (micStream) startMicClip(); // keep recording clips while sharing is on
  };
  micRecorder.start();
  setTimeout(() => micRecorder.state === "recording" && micRecorder.stop(), 4000);
}

refreshStatus();
refreshTasks();
refreshStreams();
setInterval(refreshTasks, 4000);
setInterval(refreshStreams, 4000);
