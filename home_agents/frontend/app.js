const chatLog = document.getElementById("chat-log");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const taskGrid = document.getElementById("task-grid");
const streamGallery = document.getElementById("stream-gallery");
const streamCardsEl = document.getElementById("stream-cards");
const addStreamBtn = document.getElementById("add-stream-btn");
const modeBadge = document.getElementById("mode-badge");
const googleStatusEl = document.getElementById("google-status");
const googleConnectBtn = document.getElementById("google-connect-btn");
const micBtn = document.getElementById("mic-btn");
const speakToggle = document.getElementById("speak-replies");
const safetyBanner = document.getElementById("safety-banner");
const debugPanel = document.getElementById("debug-panel");
const debugToggleBtn = document.getElementById("debug-toggle");
const debugSessionEl = document.getElementById("debug-session");
const debugTabs = document.querySelectorAll("[data-debug-tab]");
const debugLogView = document.getElementById("debug-log-view");
const debugMemoryView = document.getElementById("debug-memory-view");
const debugLogEl = document.getElementById("debug-log");
const debugMemoryEl = document.getElementById("debug-memory");
const debugClearBtn = document.getElementById("debug-clear");

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
  if (role === "assistant") {
    const parts = String(text).split(/(https?:\/\/\S+)/g);
    for (const part of parts) {
      if (/^https?:\/\//.test(part)) {
        const link = document.createElement("a");
        link.href = part;
        link.textContent = part;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        div.appendChild(link);
      } else {
        div.appendChild(document.createTextNode(part));
      }
    }
  } else {
    div.textContent = text;
  }
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
  return div;
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
      const actionType = approval.action_type || "manual review";
      const payload = approval.action_payload && Object.keys(approval.action_payload).length
        ? `<pre>${escapeHtml(JSON.stringify(approval.action_payload, null, 2))}</pre>`
        : "";
      const execution = approval.execution_result
        ? `<br/>Execution: ${escapeHtml(approval.execution_status)} — ${escapeHtml(approval.execution_result)}`
        : `<br/>Execution: ${escapeHtml(approval.execution_status)}`;
      html += `
        <div class="approval-box">
          <strong>Needs your approval</strong><br/>
          Action: ${escapeHtml(approval.action)}<br/>
          Type: ${escapeHtml(actionType)}<br/>
          Reason: ${escapeHtml(approval.reason)} (${escapeHtml(approval.risk)} risk)
          ${payload}
          <span class="hint">${execution}</span>
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
  try {
    await api(`/api/approvals/${approvalId}/decision`, {
      method: "POST",
      body: JSON.stringify({ approve }),
    });
  } catch (err) {
    addMessage("assistant", `Approval error: ${err.message}`);
  }
  await refreshTasks();
}

async function refreshTasks() {
  const views = await api("/api/tasks");
  renderTasks(views);
}

// The safety banner surfaces the hidden background monitor's alerts — it is
// not a task, so it never shows up in the task grid above, only here.

function renderSafetyAlerts(alerts) {
  const active = alerts.filter((a) => a.status === "active");
  if (active.length === 0) {
    safetyBanner.hidden = true;
    safetyBanner.innerHTML = "";
    return;
  }
  safetyBanner.hidden = false;
  safetyBanner.innerHTML = active
    .map(
      (a) => `
        <div class="safety-alert ${escapeAttr(a.urgency)}">
          <div class="info">
            <strong>\u{1F6A8} ${escapeHtml(a.stream_name)}: ${escapeHtml(a.danger_type)}</strong>
            <span>${escapeHtml(a.description)}</span>
            <span class="meta">confidence ${Math.round(a.confidence * 100)}% · ${escapeHtml(a.urgency)} urgency</span>
          </div>
          <button data-dismiss-alert="${a.alert_id}">Dismiss</button>
        </div>
      `
    )
    .join("");
  safetyBanner.querySelectorAll("[data-dismiss-alert]").forEach((btn) =>
    btn.addEventListener("click", () =>
      api(`/api/safety/alerts/${btn.dataset.dismissAlert}/dismiss`, { method: "POST" }).then(refreshSafetyAlerts)
    )
  );
}

async function refreshSafetyAlerts() {
  const alerts = await api("/api/safety/alerts");
  renderSafetyAlerts(alerts);
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
  startDebug(status);
  if (status.stt === "gradium") {
    micBtn.title = "Voice input (Gradium speech-to-text)";
  } else if (status.stt) {
    micBtn.title = "Voice input (mock transcript — set GRADIUM_API_KEY for real STT)";
  }
  gradiumTTS = status.tts === "gradium";
}

async function refreshGoogleStatus() {
  const status = await api("/api/google/status");
  googleConnectBtn.disabled = false;
  googleStatusEl.classList.toggle("connected", Boolean(status.connected));
  if (!status.configured) {
    googleConnectBtn.textContent = status.account_email ? "Finish setup" : "Add Gmail";
    googleStatusEl.textContent =
      status.account_email
        ? `Gmail saved: ${status.account_email}. OAuth client setup is still needed before real actions can run.`
        : "Start by adding your Gmail address. OAuth client setup is needed before real actions can run.";
    return;
  }
  googleConnectBtn.textContent = status.connected ? "Reconnect Google" : "Connect Google";
  googleStatusEl.textContent = status.connected
    ? `Google connected${status.account_email ? ` for ${status.account_email}` : ""}. Approved structured actions can execute.`
    : status.account_email
      ? `Gmail saved: ${status.account_email}. Ready to authorize Google.`
      : "Google configured. Add your Gmail address, then authorize Google.";
}

googleConnectBtn.addEventListener("click", async () => {
  try {
    let status = await api("/api/google/status");
    if (!status.account_email) {
      const email = window.prompt("Which Gmail address should Home Agents connect?");
      if (!email) return;
      status = await api("/api/google/account", {
        method: "POST",
        body: JSON.stringify({ email }),
      });
      addMessage("assistant", `Saved ${status.account_email}.`);
    }
    if (!status.configured) {
      addMessage(
        "assistant",
        "I saved the Gmail address. Real Google actions still need OAuth configured in this app with GOOGLE_OAUTH_CLIENT_SECRETS."
      );
      await refreshGoogleStatus();
      return;
    }
    const { auth_url } = await api("/api/google/auth/start");
    window.location.href = auth_url;
  } catch (err) {
    addMessage("assistant", `Google auth error: ${err.message}`);
  }
});

// ---------- agent console: live trace + current memory tails ----------
//
// This console is always available. It polls /api/debug/log for human-readable
// events newer than the last sequence number we've seen, and /api/debug/memory
// for the current tails of agent and subject memory files. Dynamic text is set
// via textContent since it can echo user messages and memory contents back.

let debugSeq = 0;
let debugStarted = false;
let activeDebugTab = "log";

function appendDebugEntry(ev) {
  const entry = document.createElement("div");
  const safeCategory = String(ev.category || "event").replace(/[^a-z0-9_-]/gi, "-");
  entry.className = `debug-entry cat-${safeCategory}`;

  const meta = document.createElement("div");
  meta.className = "debug-meta";
  const time = document.createElement("span");
  time.className = "debug-time";
  time.textContent = new Date(ev.at * 1000).toLocaleTimeString();
  const cat = document.createElement("span");
  cat.className = "debug-cat";
  cat.textContent = ev.category;
  meta.append(time, cat);

  const summary = document.createElement("div");
  summary.className = "debug-summary";
  summary.textContent = ev.summary;

  entry.append(meta, summary);
  if (ev.detail) {
    const detail = document.createElement("pre");
    detail.className = "debug-detail";
    detail.textContent = ev.detail;
    entry.append(detail);
  }
  debugLogEl.appendChild(entry);
}

async function refreshDebug() {
  const data = await api(`/api/debug/log?after=${debugSeq}`);
  if (!data.events.length) return;
  const nearBottom =
    debugLogEl.scrollHeight - debugLogEl.scrollTop - debugLogEl.clientHeight < 40;
  debugLogEl.querySelector(".debug-empty")?.remove();
  for (const ev of data.events) {
    appendDebugEntry(ev);
    debugSeq = Math.max(debugSeq, ev.seq);
  }
  if (nearBottom) debugLogEl.scrollTop = debugLogEl.scrollHeight;
}

function memoryCard(kind, title, subtitle, memory) {
  const card = document.createElement("div");
  card.className = "memory-card";

  const head = document.createElement("div");
  head.className = "memory-card-head";
  const kindEl = document.createElement("span");
  kindEl.className = "memory-kind";
  kindEl.textContent = kind;
  const titleEl = document.createElement("strong");
  titleEl.textContent = title;
  const subEl = document.createElement("span");
  subEl.className = "hint";
  subEl.textContent = subtitle;
  head.append(kindEl, titleEl, subEl);

  const body = document.createElement("pre");
  body.textContent = memory.trim() || "(empty)";
  card.append(head, body);
  return card;
}

async function refreshMemory() {
  const data = await api("/api/debug/memory");
  debugMemoryEl.innerHTML = "";
  if (!data.agents.length && !data.subjects.length) {
    const empty = document.createElement("div");
    empty.className = "debug-empty";
    empty.textContent = "No task or subject memory yet.";
    debugMemoryEl.appendChild(empty);
    return;
  }
  for (const agent of data.agents) {
    debugMemoryEl.appendChild(
      memoryCard("agent", agent.title, agent.status, agent.memory)
    );
  }
  for (const subject of data.subjects) {
    debugMemoryEl.appendChild(
      memoryCard("subject", subject.subject_id, "shared memory", subject.memory)
    );
  }
}

function selectDebugTab(tab) {
  activeDebugTab = tab;
  debugTabs.forEach((btn) => {
    const active = btn.dataset.debugTab === tab;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  });
  debugLogView.classList.toggle("active", tab === "log");
  debugMemoryView.classList.toggle("active", tab === "memory");
  debugClearBtn.style.display = tab === "log" ? "inline-block" : "none";
  if (tab === "memory") refreshMemory().catch(() => {});
}

function setConsoleCollapsed(collapsed) {
  debugPanel.classList.toggle("collapsed", collapsed);
  debugToggleBtn.setAttribute("aria-expanded", collapsed ? "false" : "true");
  debugToggleBtn.textContent = collapsed ? "Console" : "Collapse";
  debugToggleBtn.title = collapsed ? "Expand console" : "Collapse console";
}

function startDebug(status = {}) {
  if (debugStarted) return;
  debugStarted = true;
  if (status.debug_log_path) {
    const sessionName = status.debug_log_path.split("/").pop();
    debugSessionEl.textContent = `Persisting session log: ${sessionName}`;
  }
  const empty = document.createElement("div");
  empty.className = "debug-empty";
  empty.textContent = "Waiting for activity — send a message or run a task.";
  debugLogEl.appendChild(empty);
  debugToggleBtn.addEventListener("click", () => {
    setConsoleCollapsed(!debugPanel.classList.contains("collapsed"));
  });
  debugTabs.forEach((btn) =>
    btn.addEventListener("click", () => selectDebugTab(btn.dataset.debugTab))
  );
  debugClearBtn.addEventListener("click", () => {
    debugLogEl.innerHTML = ""; // clears the view only; server keeps its buffer
  });
  refreshDebug().catch(() => {});
  refreshMemory().catch(() => {});
  selectDebugTab(activeDebugTab);
  setInterval(() => refreshDebug().catch(() => {}), 2000);
  setInterval(() => refreshMemory().catch(() => {}), 4000);
}

// ---------- voice mode: talk to the orchestrator ----------
//
// Voice is just another front-end to the SAME orchestrator. We record a mono
// WAV in the browser (Gradium's pre-recorded STT endpoint takes audio/wav),
// POST it to /api/chat/voice, and the server transcribes it and runs the exact
// same handle_message path as typed chat. Replies are optionally spoken back
// with the browser's built-in speechSynthesis (no extra service or key).

let voiceRecorder = null; // { stop: () => Promise<Blob> } while recording
let recording = false;

function encodeWav(samples, sampleRate) {
  // 16-bit PCM, mono. WAV header carries the sample rate, so we don't resample.
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeString = (offset, str) => {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  };
  writeString(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true); // PCM fmt chunk size
  view.setUint16(20, 1, true); // format = PCM
  view.setUint16(22, 1, true); // channels = mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  writeString(36, "data");
  view.setUint32(40, samples.length * 2, true);
  let offset = 44;
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    offset += 2;
  }
  return new Blob([view], { type: "audio/wav" });
}

async function startVoiceRecording() {
  const media = await navigator.mediaDevices.getUserMedia({ audio: true });
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  const ctx = new AudioCtx();
  const source = ctx.createMediaStreamSource(media);
  const processor = ctx.createScriptProcessor(4096, 1, 1);
  const chunks = [];
  processor.onaudioprocess = (e) => {
    chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  };
  source.connect(processor);
  processor.connect(ctx.destination); // some browsers only fire the callback when connected
  return {
    async stop() {
      processor.disconnect();
      source.disconnect();
      media.getTracks().forEach((t) => t.stop());
      const sampleRate = ctx.sampleRate;
      await ctx.close();
      const total = chunks.reduce((n, c) => n + c.length, 0);
      const merged = new Float32Array(total);
      let o = 0;
      for (const c of chunks) {
        merged.set(c, o);
        o += c.length;
      }
      return encodeWav(merged, sampleRate);
    },
  };
}

async function toggleMic() {
  if (recording) {
    recording = false;
    micBtn.classList.remove("recording");
    micBtn.textContent = "🎤";
    const rec = voiceRecorder;
    voiceRecorder = null;
    if (!rec) return;
    let blob;
    try {
      blob = await rec.stop();
    } catch (err) {
      addMessage("assistant", `Couldn't finish recording: ${err.message}`);
      return;
    }
    await sendVoice(blob);
    return;
  }
  // The browser only exposes the microphone in a secure context (HTTPS, or
  // http://localhost). Over a plain-HTTP LAN IP `navigator.mediaDevices` is
  // undefined — say so explicitly instead of throwing an opaque TypeError.
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    addMessage(
      "assistant",
      "🎤 Microphone unavailable: the browser only allows it over HTTPS or on http://localhost. " +
        "If you opened the app via a LAN IP, use http://localhost:" + location.port + " instead."
    );
    return;
  }
  // Give an instant cue that the click registered — asking for mic permission
  // can block for a while (or forever, if the prompt is dismissed), and without
  // this the button would just sit there looking dead.
  micBtn.disabled = true;
  micBtn.textContent = "…";
  try {
    voiceRecorder = await startVoiceRecording();
  } catch (err) {
    // NotAllowedError (permission blocked/dismissed), NotFoundError (no mic), etc.
    addMessage("assistant", `Mic error: ${err.name ? err.name + " — " : ""}${err.message || err}`);
    micBtn.textContent = "🎤";
    return;
  } finally {
    micBtn.disabled = false;
  }
  recording = true;
  micBtn.classList.add("recording");
  micBtn.textContent = "⏹";
}

async function sendVoice(blob) {
  const pending = addMessage("user", "🎤 …"); // fill in with the transcript once heard
  const form = new FormData();
  form.append("file", blob, "voice.wav");
  try {
    const res = await fetch("/api/chat/voice", { method: "POST", body: form });
    if (!res.ok) throw new Error(await errorDetail(res));
    const { transcript, reply } = await res.json();
    pending.textContent = transcript;
    addMessage("assistant", reply);
    if (speakToggle && speakToggle.checked) speak(reply);
  } catch (err) {
    pending.textContent = "🎤 (voice)";
    addMessage("assistant", `Voice error: ${err.message}`);
  }
  refreshTasks();
}

// Reply speech: use Gradium TTS when the server has it configured (so both
// halves of the conversation share one voice), otherwise the browser's own
// voice. Any Gradium failure falls back to the browser rather than going silent.
let gradiumTTS = false;

// Pull a human-readable message out of a failed response, unwrapping FastAPI's
// {"detail": ...} envelope so Gradium's own error text reaches the user.
async function errorDetail(res) {
  let body = "";
  try {
    body = await res.text();
  } catch {
    /* no body */
  }
  try {
    body = JSON.parse(body).detail ?? body;
  } catch {
    /* not JSON */
  }
  return `${res.status} ${body}`.trim().slice(0, 240);
}

async function speak(text) {
  if (gradiumTTS) {
    try {
      const res = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) throw new Error(await errorDetail(res));
      const audio = new Audio(URL.createObjectURL(await res.blob()));
      await audio.play();
      return;
    } catch (err) {
      // Explicit, not silent: show why Gradium TTS failed, then fall back to
      // the browser voice so the reply is still spoken.
      console.error("Gradium TTS failed:", err);
      addMessage("assistant", `⚠️ Gradium TTS failed — using the browser voice instead. (${err.message})`);
    }
  }
  browserSpeak(text);
}

function browserSpeak(text) {
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}

if (micBtn) micBtn.addEventListener("click", toggleMic);

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
refreshGoogleStatus();
refreshTasks();
refreshStreams();
refreshSafetyAlerts();
setInterval(refreshTasks, 4000);
setInterval(refreshStreams, 4000);
setInterval(refreshSafetyAlerts, 3000);
