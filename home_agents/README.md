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
```

`HOME_AGENTS_MOCK=true` runs the whole system — orchestrator, scheduler,
agents — without ever calling Crusoe, using keyword heuristics and canned
observations instead. This is the fastest way to see the full loop.

## Run it

```bash
HOME_AGENTS_MOCK=true python -m home_agents.app
```

Then open **http://127.0.0.1:8000** in a browser — on the same machine, or
on a phone on the same network using the machine's LAN IP instead of
`127.0.0.1`, which is how you connect a phone camera as a live stream (see
below). For a real deployment, unset mock mode and export `CRUSOE_API_KEY`.

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
timestamp, source)`. Two demo streams (`demo-kitchen-cam`,
`demo-kitchen-mic`) are seeded at startup from `data/running-tap-*` so the
tap scenario works with zero hardware. Live streams are created the moment
a browser posts a frame or clip to `/api/streams/{id}/image` or
`/api/streams/{id}/audio` — there is no separate "register a stream" step.
A task simply references a `stream_id`; if nothing has posted to it yet,
the agent is told explicitly ("no data received yet for stream X") instead
of guessing.

### 7. Approvals (`approvals.py`)

A pending-approval queue, nothing more. There is intentionally **no**
integration wired up behind "approve" — no real smart-home, email, or
calendar control. Approving a proposal in this codebase only changes its
status and logs the decision; it can never have a real-world side effect.
This mirrors `tap_agent`'s hard rule that the system "must never
automatically close a valve," generalized to "must never automatically do
anything," since this prototype has no actuators to safely gate.

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

## Live camera walkthrough

1. Start the server, note the machine's LAN IP (e.g. `192.168.1.42`).
2. On a phone on the same network, open `http://192.168.1.42:8000`.
3. Name a stream (e.g. `front-door`), tap "Start", grant camera + mic
   permission, leave the tab open. Add more streams with "Add camera + mic
   stream" — each is an independent camera+voice pair.
4. In the chat (from any device), say something like: *"watch the front-door
   stream and tell me if anyone is at the door"* — the orchestrator sees the
   `front-door-cam` (image) and `front-door-mic` (audio) streams grouped as a
   pair under `known_stream_pairs` and wires both into the task, so the agent
   both sees and hears the door.

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
  UI; editing is currently pause → delete → recreate.
