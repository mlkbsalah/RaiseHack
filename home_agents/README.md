# Home Agents

A general multi-agent framework for a smart home: describe what you want
watched in a chat window, the orchestrator turns that into a scheduled task,
and a fresh agent call runs periodically to observe, remember, and — only
with your explicit approval — propose action.

This started as a generalization of a reference prototype (`tap_agent/`,
since removed from this repo) that hard-coded exactly one use case — a
kitchen tap left running. `home_agents/` reused only its *pattern*: a
Crusoe/OpenAI-compatible client, JSON-only structured responses validated
with Pydantic, a mock mode for offline testing — generalized so any task,
not just the tap, can be described at runtime. It has since grown a web UI
with live multi-stream camera/mic capture and an optional Telegram bridge
(see below), but the core loop is unchanged.

## Setup

```bash
cd /path/to/repo
python3 -m venv .venv312          # any Python 3.10+ interpreter
source .venv312/bin/activate
pip install -r home_agents/requirements.txt
```

Create `home_agents/.env` (gitignored — never commit real secrets in it) with
at least:

```text
CRUSOE_API_KEY=your-crusoe-key
```

Everything else has a default:

```text
CRUSOE_MULTIMODAL_MODEL=nvidia/Nemotron-3-Nano-Omni-Reasoning-30B-A3B
CRUSOE_REASONING_MODEL=deepseek-ai/Deepseek-V4-Flash
HOME_AGENTS_MOCK=true
HOME_AGENTS_DATA_DIR=home_agents_data
HOME_AGENTS_HOST=127.0.0.1
HOME_AGENTS_PORT=8000
HOME_AGENTS_TICK_SECONDS=5
HOME_AGENTS_STREAM_TTL=12   # seconds a live stream may go quiet before it's
                            # treated as closed and dropped from the live view
TELEGRAM_BOT_TOKEN=          # optional — see "Telegram bridge" below
TELEGRAM_ALLOWED_CHAT_IDS=   # optional — comma-separated chat ids
```

`HOME_AGENTS_MOCK=true` runs the whole system — orchestrator, scheduler,
agents — without ever calling Crusoe, using keyword heuristics and canned
observations instead. This is the fastest way to see the full loop.

The UI always includes a collapsible **Agent console** at the bottom. Its Log
tab shows a human-readable live trace of orchestrator decisions, agent
observations, and memory writes; its Memory tab shows current tails of each
agent and subject memory file. Log events are kept in an in-memory ring buffer
for polling (`/api/debug/log`) and persisted as JSONL session files under
`HOME_AGENTS_DATA_DIR/debug_sessions/`.

## Run it

```bash
HOME_AGENTS_MOCK=true python -m home_agents.app
```

Then open **http://127.0.0.1:8000** in a browser on the same machine. To
connect a phone camera/mic as a live stream, expose the server over HTTPS with
a Cloudflare tunnel — phone browsers only allow camera/mic capture on a secure
origin, so see [Connecting phones (Cloudflare
tunnel)](#connecting-phones-cloudflare-tunnel) below. For a real deployment,
unset mock mode and export `CRUSOE_API_KEY`.

## Architecture

```text
User chat ─▶ Orchestrator ─▶ Tasks.md + tasks.json (task registry)
                                       │
                              Scheduler (every N seconds, per task)
                                       │
                                TaskAgent.run(task)   ◀── one Crusoe API call
                              /        │        \
                     agent memory   stream(s)   subject memory
                     (this task)   (image/audio)  (this person/pet)
                                       │
                         AgentObservation: summary, anomaly?,
                         new subject facts, optional action_proposal
                                       │
                         action_proposal? ─▶ ApprovalStore (pending)
                                       │              │
                                 tiles in UI ◀── user clicks Approve/Deny
```

Each box below is one module in `home_agents/`.

### 1. Orchestrator (`orchestrator.py`)

The chat endpoint. One user message → one LLM call → one structured
`OrchestratorReply` (`intent`, a conversational `reply`, and either a
`TaskDraft` or a `target_task_id`). Deterministic Python (`_apply`) then
carries out exactly that one operation against the task store — the LLM
never touches the task store directly, only ever recommending, never
executing.

The prompt is grounded in the current system state — existing task
titles/ids, connected stream ids, known subject ids — passed as `context`,
so the model references real ids instead of inventing them.

Supported intents: `create_task`, `pause_task`, `resume_task`,
`delete_task`, `list_tasks`, `list_streams`, `chat`. `update_task` is
accepted by the schema but not required for the demo scope.

`handle_message` is the single entrypoint both interfaces use: the web
chat panel calls it via `/api/chat`, and the Telegram bridge (see below)
calls it directly for any text message from an authorized chat — so the
orchestrator has no notion of "web" vs. "Telegram," it just answers
whoever asked.

In mock mode, `_mock_reply` replaces the LLM call with keyword matching
(`tap`/`water` → a tap-watching task pre-wired to the demo streams,
`fridge` → inventory task, `pet`/`cat`/`dog` → a task with a `pet` subject,
otherwise a generic task using whatever stream is already connected).

### 2. Task registry (`task_store.py`)

`Tasks.md` is the literal artifact the assignment calls for — a
human-readable ledger the orchestrator writes to. It's regenerated from
`tasks.json` on every change rather than parsed back in, because hand-edited
markdown is not a reliable machine-readable format and there is no reason to
make the system fragile against its own output. `tasks.json` is the actual
source of truth (task id, title, description, focus, interval, subject,
required streams, approval flag, status, timestamps).

### 3. Scheduler (`scheduler.py`)

Plain Python, not an agent loop: every `HOME_AGENTS_TICK_SECONDS` it checks
`now - last_run_at >= task.interval_seconds` for every `active` task and
calls the agent for the ones that are due. A `run_now` escape hatch exists
for the UI's "Run now" button and for demos, so you don't have to wait out
real intervals to see the loop work.

### 4. Task agent (`agent_runner.py`)

Rather than separate vision/audio/decision specialists wired together by
hand for one scenario, a single multimodal Crusoe call plays all three
roles at once, scoped by `task.focus` (the free-text description of what
this particular task cares about). Every run is stateless and
self-contained:

1. Read this task's own memory tail (`memory_store.read_agent_memory`).
2. Read the subject's memory tail, if the task has a `subject_id`.
3. Pull the latest frame/clip for every stream the task declares
   (`stream_registry.get_payload`).
4. One Crusoe multimodal chat call → a validated `AgentObservation`:
   `summary`, `anomaly_detected`, `anomaly_description`, `subject_findings`
   (new durable facts), `action_proposal` (optional), `confidence`.
5. Append a log line to the agent's own memory.
6. Append any `subject_findings` to the subject's memory.
7. If there's an `action_proposal`, file it with the `ApprovalStore` — the
   agent never acts on it itself — and, if Telegram is configured, push it
   there immediately with Approve/Deny buttons (see "Telegram bridge").

Mock mode returns a deterministic cycle (every third run flags an anomaly
with a proposed action) so the approval UI has something to show without
live inference.

### 5. Memory (`memory_store.py`)

Two kinds of markdown file, matching the architecture 1:1:

- `home_agents_data/memory/agents/<task_id>.md` — this agent's own append-only
  log of what happened on each run (equivalent to the diagram's "agent
  memory" boxes).
- `home_agents_data/memory/subjects/<subject_id>.md` — durable facts about a
  specific person or pet, contributed by any task/agent that involves them
  (equivalent to the diagram's "subject memory" boxes, keyed by title/id).

Both are plain append-only markdown rather than a database: the only access
pattern is "read the recent tail for prompt context" and "append one
entry," so a database would add ceremony without adding capability.

### 6. Streams (`stream_registry.py`)

A stream is just a named latest-blob: `stream_id → (kind, mime type, bytes,
timestamp, source)`. In **mock mode** two demo streams (`demo-kitchen-cam`,
`demo-kitchen-mic`) are seeded at startup from `data/running-tap-*` so the
tap scenario works with zero hardware; **live mode seeds nothing** — real
devices push their own streams. Live streams are created the moment
a browser posts a frame or clip to `/api/streams/{id}/image` or
`/api/streams/{id}/audio` — there is no separate "register a stream" step.
A task simply references a `stream_id`; if nothing has posted to it yet,
the agent is told explicitly ("no data received yet for stream X") instead
of guessing. A live stream stays "connected" only while it keeps receiving
data: once it goes quiet past `HOME_AGENTS_STREAM_TTL` it's treated as closed
and disappears from the live view and the agent's inputs, so a stopped phone
drops out instead of freezing on its last frame. Stopping a card in the UI
also `DELETE`s its streams for immediate removal; demo/seeded streams are
exempt from the TTL.

### 7. Approvals (`approvals.py`)

A pending-approval queue, nothing more. There is intentionally **no**
integration wired up behind "approve" — no real smart-home, email, or
calendar control. Approving a proposal in this codebase only changes its
status and logs the decision; it can never have a real-world side effect.
The rule is simply "must never automatically do anything, ever" — this
prototype has no actuators to safely gate, on the web or via Telegram.

Both interfaces write through this same `ApprovalStore` instance, so a
decision made in one place is immediately visible in the other: the web UI
picks it up on its next 4-second poll, and Telegram gets an explicit push
(see "Telegram bridge") since it has no polling loop of its own to fall
back on.

### 8. Web app (`app.py` + `frontend/`)

FastAPI serves both the JSON API and the static frontend, so the whole
thing is one process and one URL. The frontend is plain HTML/CSS/JS (no
build step) so `python -m home_agents.app` and opening a browser is the
entire "launch" story:

- **Chat panel** — talks to `/api/chat`, i.e. the orchestrator.
- **Task tiles** — one per task, polling `/api/tasks` every 4s. A tile
  turns amber when its last run flagged an anomaly, and red with an
  inline **Approve / Deny** panel when there's a pending action proposal —
  the whole point of the "should the interface show when an agent needs
  approval" requirement.
- **Streams panel** — lets *this device* host one or more live streams,
  where **each stream can be a camera, a microphone, or both** — the user
  ticks Camera and/or Mic per card. "Add camera + mic stream" spawns a card;
  on Start it opens a `getUserMedia` for exactly the modalities ticked (with
  an optional camera picker so multiple cards can use different physical
  cameras). When Camera is on it grabs a JPEG frame off a canvas every 4s and
  posts it to `/api/streams/<name>-cam/image`; when Mic is on it records
  back-to-back 4-second clips with `MediaRecorder` posted to
  `/api/streams/<name>-mic/audio`. Many cards run at once, so one phone or
  laptop can drive several streams — the "Connected" list shows them grouped
  by name into camera+mic pairs (📷 camera age · 🎙 mic age, with `—` for a
  modality a stream doesn't provide). Opening this page on a phone and tapping
  "Start" is the live phone-camera path — no native app, just the browser's
  camera/mic permission prompt.
  Below the cards, a **"Live view" gallery** shows the latest frame and clip
  of *every* connected stream — whichever phone or script pushed it, not just
  this device's own camera — by polling `/api/streams/pairs` and pointing an
  `<img>`/`<audio>` at `/api/streams/<id>/latest`. Open the app on a wall
  display and every phone in the house shows up as a live tile.

### 9. Telegram bridge (`telegram_bot.py`)

Optional, and fully inert unless `TELEGRAM_BOT_TOKEN` is set: no background
thread starts and no network call is ever made without it, so the rest of
the app behaves identically whether or not Telegram is configured. When
enabled it does three things:

1. **Mirrors the orchestrator chat.** Any authorized chat can type the same
   free-text task descriptions the web chat panel accepts — it calls the
   exact same `Orchestrator.handle_message`, so task creation, editing, and
   listing all work identically from Telegram.
2. **Pushes approval requests as they're filed.** The moment `TaskAgent.run`
   creates a new `ApprovalRequest` (see step 7 above), it's sent to every
   authorized chat as a message with inline **✅ Approve / ❌ Deny** buttons
   — this is the main point of the integration: a permission request
   reaches you wherever you are, not only when the web tab is open.
3. **Exposes `/tasks`** with inline **▶ Run now / ⏸ Pause (or ▶️ Resume) /
   🗑 Delete** buttons per task, so the task list can be operated entirely
   from the chat, same as the tile buttons in the web UI.

**Setup:**

1. Message [@BotFather](https://t.me/BotFather) on Telegram, `/newbot`, and
   copy the token it gives you into `TELEGRAM_BOT_TOKEN` in
   `home_agents/.env`.
2. Restart the app and message your new bot anything. It will reply "Not
   authorized" and include your numeric chat id in that reply — copy it into
   `TELEGRAM_ALLOWED_CHAT_IDS` (comma-separated if more than one person
   should have access) and restart once more.
3. Message it again — you're in. `/help` or `/start` prints a short usage
   summary, `/tasks` lists tasks with buttons.

**Cross-notification is origin-aware in one direction only.** A decision
made *in Telegram* never re-notifies Telegram — the button press already
gives that chat its own feedback by editing the message in place — but:

- A **task created anywhere** (web or Telegram) sends a "🆕 New task" message
  to Telegram. Task creation always goes through the same
  `Orchestrator._create_task`, so there's no need to track which interface
  triggered it — a Telegram-originated confirmation just doubles as a receipt.
- A **decision made in the web UI** (`POST /api/approvals/{id}/decision`)
  explicitly pushes a resolution message to Telegram, per the requirement
  that the two interfaces stay in sync in both directions.
- A **decision made in Telegram** updates the shared `ApprovalStore` directly,
  so the web UI's next poll reflects it automatically — no separate push is
  needed the other way since the web UI already polls.

`Orchestrator` and `TaskAgent` each hold a plain `telegram` attribute
(`None` until `app.py` wires it up after constructing everything) rather
than importing `TelegramBot` — this keeps both modules working with zero
knowledge of Telegram when it isn't configured, and avoids a constructor
cycle (`TelegramBot` needs the orchestrator to answer chat messages; the
orchestrator needs `TelegramBot` to send notifications).

**Why long polling, not a webhook:** this runs locally or behind a
Cloudflare tunnel whose URL changes on every run (see below), and long
polling (`getUpdates`) needs no public endpoint at all — the same tradeoff
already made for the task `Scheduler`, just one more background thread.

### 10. Safety monitor (`safety_monitor.py`)

A second, independent background loop, started in `app.py` alongside the
`Scheduler` but not built on top of it: it isn't a task, so it's never
created by the orchestrator, never appears in `Tasks.md` / `/api/tasks` /
the Telegram `/tasks` list, and can't be paused, edited, or deleted by a
user. It exists to catch things nobody explicitly asked to be watched for.

Every `SAFETY_TICK_SECONDS` (hardcoded to 5s, not a per-task interval a
user configures) it walks every currently-connected camera+mic pair from
`stream_registry.list_pairs()` and checks the latest frame/clip against a
fixed, hardcoded checklist of household dangers — fire/smoke, a person
down, a likely intruder, a stove left on unattended, an unattended child
near a hazard, flooding, distress sounds, an alarm, breaking glass, a
visible weapon. "Dangerous or abnormal" has no agreed-upon definition, so
rather than let the model freelance a new definition every run — and drift
— the checklist is the single source of truth for what counts as a safety
alert here, in the same spirit as the tap manager's rule that the manager
is deterministic and the LLM only interprets evidence: the *categories* are
fixed in code, only the *judgment call per frame* is the model's job.

A hit is stored in an in-memory `SafetyAlertStore` (most-recent-first,
capped list) and, if Telegram is configured, pushed immediately via
`notify_safety_alert` with siren emoji (🚨) so it reads as more urgent than
an ordinary approval ping. There's nothing to approve here — it's a
notification, not an `action_proposal` — so it bypasses `ApprovalStore`
entirely. `GET /api/safety/alerts` feeds a banner pinned above the rest of
the web UI (`#safety-banner` in `frontend/index.html`, styled in
`styles.css`) that stays hidden while there are no active alerts and
renders every active one with a Dismiss button
(`POST /api/safety/alerts/{id}/dismiss`); like everything else in this
prototype, dismissing an alert only changes its own status — there's no
actuator behind it.

Mock mode uses the same per-stream deterministic cycle pattern as
`TaskAgent`'s mock observations (see above), cycling through the hardcoded
checklist so the banner and Telegram push can both be demoed without a
Crusoe key.

## Connecting phones (Cloudflare tunnel)

Phone browsers only grant camera/mic access on a **secure origin** — `https://`
or `localhost`. Serving the app over plain HTTP to a LAN IP does *not* work:
the browser silently blocks `getUserMedia`. The simplest way to give the app a
public HTTPS URL for a demo is a **Cloudflare tunnel** pointed at your
locally-running server — no deploy, no account, and the phones don't even need
to be on the same Wi-Fi.

### 1. Install the `cloudflared` CLI

```bash
brew install cloudflared                          # macOS (Homebrew)
winget install --id Cloudflare.cloudflared        # Windows
sudo apt-get install cloudflared                  # Debian/Ubuntu (or grab the .deb below)
```

Other platforms / binaries:
<https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/>

### 2. Start the app

```bash
CRUSOE_API_KEY=... python -m home_agents.app      # binding 127.0.0.1:8000 is fine
```

Run in **live mode** (`CRUSOE_API_KEY` set, `HOME_AGENTS_MOCK` unset) so the
orchestrator can wire your arbitrary stream names into tasks; mock mode only
keyword-matches the built-in demo scenarios.

### 3. Open a tunnel to it

In a second terminal:

```bash
cloudflared tunnel --url http://localhost:8000
```

It prints a public HTTPS URL like `https://random-words.trycloudflare.com`.
This "quick tunnel" needs no Cloudflare account; the URL is ephemeral and
changes each run.

### 4. Connect the phones

1. Open the printed `https://…trycloudflare.com` URL on each phone. Making a QR
   code of it is the fastest way to get several phones onto the page.
2. On each phone, give the stream a **unique name** (e.g. `kitchen`,
   `front-door`) — streams are keyed by name on the server, so two phones
   sharing a name overwrite each other. Tick Camera and/or Mic, tap **Start**,
   and grant the camera/mic permission prompt.
3. Every phone now appears as a live tile in the **Live view** gallery, and you
   can wire any of them into a task from the chat: *"watch the front-door
   stream and tell me if anyone is at the door"* — the orchestrator sees the
   `front-door-cam` (image) and `front-door-mic` (audio) streams grouped as a
   pair under `known_stream_pairs` and wires both in, so the agent both sees and
   hears the door.

Prefer a stable URL (persistent tunnel or a real deploy)? Any HTTPS host works
— the tunnel above is just the quickest path for a live demo.

## Known limitations

- No real device/email/calendar integration behind approvals — see
  "Approvals" above. This is deliberate, not a stub waiting to be filled in
  carelessly; wiring a real actuator to "approve" is a separate, larger
  safety decision.
- The scheduler runs tasks sequentially in one background thread; fine for
  a handful of tasks, not meant to scale to dozens of high-frequency ones.
- Audio uploads are passed through in whatever mime type the browser's
  `MediaRecorder` produces (typically `audio/webm`); whether a given
  multimodal endpoint accepts that container is a model/provider detail,
  not something this code can control.
- Subject identity is a user-supplied label (e.g. "pet", "mom"), not
  face/voice recognition — there's no biometric matching in this prototype.
- `update_task` intent is defined in the schema but not exercised by the
  web UI; editing is available via the tile's "Edit details" form or the
  chat, but not yet from Telegram.
- The Telegram bridge polls on a single background thread: a "Run now" that
  triggers a real (slow) Crusoe call blocks that thread until it returns,
  so another incoming Telegram message won't be processed until it's done.
  Fine for personal, occasional use; not built for high message volume.
- `TELEGRAM_ALLOWED_CHAT_IDS` is a flat allow-list with no separate linking
  flow — anyone who has your bot token and guesses/observes a valid chat id
  in transit could act as that chat. Keep the token secret the same way you
  keep `CRUSOE_API_KEY` secret.
- The safety monitor (see above) makes one multimodal call per connected
  camera+mic pair every 5 seconds regardless of how many user tasks exist,
  so its Crusoe cost and rate-limit exposure scale with the number of
  connected streams, independent of the task scheduler's own load.
- The hardcoded danger checklist in `safety_monitor.py` is fixed in code,
  not user-editable from the chat or UI — by design, per the request that
  started this feature, but it means adding or removing a danger category
  requires a code change and restart.
