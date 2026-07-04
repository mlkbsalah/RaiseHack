Implement a minimal but runnable **manager–specialist agent workflow** for a smart-home “tap left running” use case using Crusoe’s OpenAI-compatible inference API.

Reference repository:
https://github.com/c-jg/raise-hack-crusoe-workshop-2026

## Goal

Build a Python prototype that receives:

* one image frame from a kitchen camera;
* one short audio recording;
* an optional water-flow value;
* elapsed event duration.

It should determine whether a tap is running unattended and return one action:

* `ignore`
* `continue_observing`
* `ask_resident`
* `send_alert`

## Architecture

Use the following pattern:

```text
Image ──> Vision Specialist ──┐
Audio ──> Audio Specialist ───┤
Flow sensor ──────────────────┤
                              ↓
                       Tap Manager
                   state + rules + memory
                              ↓
                     Decision Specialist
```

The **manager must be deterministic Python code**. Do not implement a free-form autonomous agent loop.

The LLM specialists should only interpret evidence and produce structured JSON.

## Required files

Create:

```text
tap_agent/
├── app.py
├── config.py
├── models.py
├── specialists.py
├── manager.py
├── memory.py
├── requirements.txt
├── .env.example
└── README.md
```

## Dependencies

Use:

```text
openai
pydantic
python-dotenv
```

Use only the Python standard library for persistence. Use SQLite through `sqlite3`.

## Crusoe configuration

Use the OpenAI client:

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://api.inference.crusoecloud.com/v1/",
    api_key=os.environ["CRUSOE_API_KEY"],
)
```

Put model names in environment variables with sensible defaults:

```text
CRUSOE_MULTIMODAL_MODEL
CRUSOE_REASONING_MODEL
```

Do not hardcode secrets.

## Structured data models

Create Pydantic models.

### AudioObservation

Fields:

```python
water_sound_detected: bool
confidence: float
sound_type: Literal[
    "running_tap",
    "shower",
    "washing_machine",
    "dishwasher",
    "unknown_water",
    "not_water"
]
continuous_sound: bool
explanation: str
```

### VisionObservation

Fields:

```python
tap_visible: bool
water_stream_visible: bool
confidence: float
person_near_tap: bool
person_using_sink: bool
sink_overflow_visible: bool
explanation: str
```

### DecisionRecommendation

Fields:

```python
situation: Literal[
    "normal_use",
    "probably_unattended",
    "possible_leak",
    "uncertain"
]
recommended_action: Literal[
    "ignore",
    "continue_observing",
    "ask_resident",
    "send_alert"
]
urgency: Literal["low", "medium", "high", "critical"]
confidence: float
evidence_summary: str
user_message: str
```

All confidence values must be validated between `0.0` and `1.0`.

## Specialists

Implement three classes.

### AudioSpecialist

Method:

```python
analyze(audio_path: str) -> AudioObservation
```

Responsibilities:

* encode the audio file as a base64 data URL;
* send it to the Crusoe multimodal model;
* classify whether the sound is running tap water;
* return only structured JSON;
* validate the response with Pydantic.

### VisionSpecialist

Method:

```python
analyze(image_path: str) -> VisionObservation
```

Responsibilities:

* encode JPG, PNG or WebP as a base64 data URL;
* detect whether water is visibly running;
* detect whether someone is using the sink;
* detect visible overflow;
* return validated structured JSON.

### DecisionSpecialist

Method:

```python
recommend(
    event: TapEvent,
    preferences: dict,
    previous_events: list[dict],
) -> DecisionRecommendation
```

Responsibilities:

* combine already-extracted evidence;
* generate a concise user-facing message;
* never directly control a physical valve;
* never override manager safety rules.

For all specialist calls:

* use temperature `0`;
* ask for JSON only;
* validate every response;
* raise a clear error when parsing fails.

## Event state

Create a `TapEvent` dataclass containing:

```python
event_id: str
started_at: float
last_seen_at: float
consecutive_positive_ticks: int
audio: AudioObservation | None
vision: VisionObservation | None
flow_rate_lpm: float | None
recommendation: DecisionRecommendation | None
evidence_log: list[dict]
```

Add a property:

```python
duration_seconds
```

## Memory

Implement `TapMemory` using SQLite.

Create two tables:

```text
preferences
incidents
```

Default preferences:

```json
{
  "ask_after_seconds": 30,
  "alert_after_seconds": 90,
  "minimum_flow_rate_lpm": 0.3,
  "ignore_when_person_using_sink": true,
  "automatic_shutoff_allowed": false
}
```

Required methods:

```python
set_default_preferences()
get_preferences() -> dict
save_incident(event, user_feedback=None)
get_recent_feedback_events(limit=5) -> list[dict]
record_feedback(event_id, feedback)
```

No vector database is needed.

## Manager logic

Implement `TapIncidentManager`.

Method:

```python
process_tick(
    image_path: str | None,
    audio_path: str | None,
    flow_rate_lpm: float | None,
    timestamp: float | None = None,
) -> dict
```

The manager must:

1. call the available perception specialists;
2. determine whether water is probably running;
3. create or update an active event;
4. track event duration;
5. suppress alerts when a person is visibly using the sink;
6. immediately alert when visible overflow is detected;
7. observe silently before `ask_after_seconds`;
8. call the decision specialist only for sustained incidents;
9. force `send_alert` once `alert_after_seconds` is exceeded, unless the sink is visibly being used;
10. save completed or escalated incidents to SQLite.

Use deterministic evidence fusion:

```python
sensor_positive = flow_rate_lpm is not None and flow_rate_lpm >= threshold
audio_positive = audio confidence >= 0.70 and water_sound_detected
vision_positive = vision confidence >= 0.70 and water_stream_visible
```

Water is probably running when:

```python
sensor_positive or vision_positive or (
    audio_positive and tap is visible
)
```

Do not ask the LLM whether the tap is running when deterministic sensor fusion is sufficient.

## Safety rules

Enforce these in Python, not in prompts:

* visible overflow causes immediate alert;
* visible active sink use suppresses normal alerts;
* the system must never automatically close a valve;
* LLM output cannot bypass duration thresholds or safety rules;
* missing or uncertain evidence should lead to continued observation, not confident claims.

## CLI demo

Create `app.py` with:

```bash
python app.py \
  --image examples/kitchen.jpg \
  --audio examples/kitchen.wav \
  --flow-rate 4.2 \
  --elapsed-seconds 45
```

Output JSON such as:

```json
{
  "event_id": "...",
  "action": "ask_resident",
  "duration_seconds": 45.0,
  "confidence": 0.86,
  "message": "The kitchen tap appears to have been running for 45 seconds while nobody is using it. Is this intentional?"
}
```

Allow image, audio and flow rate to be individually optional.

## README

Document:

* setup;
* required environment variables;
* installation;
* example CLI command;
* architecture;
* why the manager is deterministic;
* how SQLite memory works;
* how to record feedback;
* known limitations.

## Code quality

Requirements:

* Python 3.10+;
* type hints everywhere;
* clear error messages;
* no framework unless necessary;
* no LangChain;
* no unnecessary abstractions;
* no asynchronous code;
* no Docker unless needed;
* keep the implementation compact and understandable;
* include comments only where they explain important design choices.

After implementation, run a local syntax check:

```bash
python -m compileall tap_agent
```

Also provide a mock mode controlled by:

```text
TAP_AGENT_MOCK=true
```

In mock mode, do not call Crusoe. Return fixed realistic specialist outputs so the CLI can be tested without an API key.
