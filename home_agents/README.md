# Home Agents

A general multi-agent framework for a smart home: describe what you want
watched in a chat window, the orchestrator turns that into a scheduled task,
and a fresh agent call runs periodically to observe, remember, and — only
with your explicit approval — propose action.

This generalizes `tap_agent/`, which hard-codes exactly one use case (a
kitchen tap left running). `home_agents/` is a new codebase; it reuses only
the *pattern* from `tap_agent/specialists.py` — a Crusoe/OpenAI-compatible
client, JSON-only structured responses validated with Pydantic, a mock mode
for offline testing — generalized so any task, not just the tap, can be
described at runtime.

## Setup

```bash
cd /path/to/repo
python3 -m venv .venv312          # any Python 3.10+ interpreter
source .venv312/bin/activate
pip install -r home_agents/requirements.txt
cp home_agents/.env.example home_agents/.env   # or export the vars directly
```

Required for live mode (real Crusoe calls):

```text
CRUSOE_API_KEY
```

Everything else has a default (see `home_agents/.env.example`):

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
GOOGLE_OAUTH_CLIENT_SECRETS=/absolute/path/to/google-oauth-client.json
GOOGLE_OAUTH_TOKEN=home_agents_data/google/token.json
```

`HOME_AGENTS_MOCK=true` runs the whole system — orchestrator, scheduler,
agents — without ever calling Crusoe, using keyword heuristics and canned
observations instead. This is the fastest way to see the full loop.

## Google actions

Agents can propose Google actions, but they still cannot execute anything
until a resident clicks **Approve**. When approved, the app can run a
structured `action_proposal` with one of these `action_type` values:

- `send_email` — Gmail send; payload: `to`, `subject`, `body`
- `create_calendar_event` — Calendar event / optional Google Meet; payload:
  `summary`, `start`, `end`, `timezone`, `attendees`, `create_meet`
- `create_task` — Google Tasks item; payload: `title`, optional `notes`,
  optional `due`
- `create_keep_note` — Google Keep note; payload: `title`, `text`

To connect Google:

1. Create an OAuth web client in Google Cloud and enable the Gmail, Calendar,
   Tasks, and Keep APIs needed by your account. Add
   `http://127.0.0.1:8000/api/google/auth/callback` as an authorized redirect
   URI, changing the host/port if your app runs elsewhere.
2. Download the OAuth client JSON locally. Do not commit it.
3. Set `GOOGLE_OAUTH_CLIENT_SECRETS` in `.env` to that local JSON path.
4. Start the app and click **Connect Google** in the web UI.

The OAuth token is stored locally at `GOOGLE_OAUTH_TOKEN`, which defaults to
`home_agents_data/google/token.json`; `home_agents_data/` is ignored by git.
Google Keep API access is Workspace-oriented, so `create_keep_note` may fail
cleanly if the connected account or enabled API does not support note creation.

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
never touches the task store directly, mirroring how `tap_agent`'s decision
specialist only *recommends*, never *executes*.

The prompt is grounded in the current system state — existing task
titles/ids, connected stream ids, known subject ids — passed as `context`,
so the model references real ids instead of inventing them.

Supported intents: `create_task`, `pause_task`, `resume_task`,
`delete_task`, `list_tasks`, `list_streams`, `chat`. `update_task` is
accepted by the schema but not required for the demo scope.

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

This is the generalized version of `tap_agent`'s three specialists. Where
the reference project has a separate vision specialist, audio specialist,
and decision specialist wired together by hand for one scenario, here a
single multimodal Crusoe call plays all three roles at once, scoped by
`task.focus` (the free-text description of what this particular task cares
about). Every run is stateless and self-contained:

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
   agent never acts on it itself.

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

A pending-approval queue plus execution metadata. The agent never executes an
action itself; it files a proposal here. When a resident clicks **Approve**,
`app.py` can hand a structured Google proposal to `google_actions.py`, which
executes only known action types with explicit payloads. Proposals without an
`action_type` remain manual-only and simply get logged.

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

- Google actions require a local OAuth client JSON and a connected account.
  Keep support depends on whether the connected account has access to the
  official Keep API.
- The scheduler runs tasks sequentially in one background thread; fine for
  a handful of tasks, not meant to scale to dozens of high-frequency ones.
- Audio uploads are passed through in whatever mime type the browser's
  `MediaRecorder` produces (typically `audio/webm`); whether a given
  multimodal endpoint accepts that container is a model/provider detail,
  not something this code can control.
- Subject identity is a user-supplied label (e.g. "pet", "mom"), not
  face/voice recognition — there's no biometric matching in this prototype.
- `update_task` intent is defined in the schema but not exercised by the
  UI; editing is currently pause → delete → recreate.
