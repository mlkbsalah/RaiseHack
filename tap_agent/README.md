# Tap Agent

Minimal manager-specialist prototype for detecting a kitchen tap left running.

## Setup

```bash
cd /path/to/repo
uv sync
```

Or with plain `pip`:

```bash
cd /path/to/repo/tap_agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set `CRUSOE_API_KEY` in `.env` for live Crusoe inference. For local smoke tests without an API key, keep:

```text
TAP_AGENT_MOCK=true
```

## Environment

Required for live mode:

```text
CRUSOE_API_KEY
```

Optional:

```text
CRUSOE_MULTIMODAL_MODEL
CRUSOE_REASONING_MODEL
TAP_AGENT_DB
TAP_AGENT_MOCK
```

The client is configured with Crusoe's OpenAI-compatible endpoint:

```python
OpenAI(base_url="https://api.inference.crusoecloud.com/v1/", api_key=...)
```

Default model choices match the RAISE workshop free catalog:

```text
CRUSOE_MULTIMODAL_MODEL=nvidia/Nemotron-3-Nano-Omni-Reasoning-30B-A3B
CRUSOE_REASONING_MODEL=deepseek-ai/Deepseek-V4-Flash
```

Nemotron Omni is used for audio because it is the workshop model that accepts `audio_url`. DeepSeek Flash is used only after deterministic manager thresholds require a text recommendation.

## CLI

From the repository root:

```bash
TAP_AGENT_MOCK=true python tap_agent/app.py \
  --image examples/kitchen.jpg \
  --audio examples/kitchen.wav \
  --flow-rate 4.2 \
  --elapsed-seconds 45
```

With uv:

```bash
TAP_AGENT_MOCK=true uv run tap-agent \
  --image examples/kitchen.jpg \
  --audio examples/kitchen.wav \
  --flow-rate 4.2 \
  --elapsed-seconds 45
```

Image, audio, and flow rate are individually optional. In mock mode, supplied file paths are ignored by specialists so the CLI can be exercised without example media.

For live Crusoe testing, unset mock mode and single-quote the API key if exporting it in a shell:

```bash
export CRUSOE_API_KEY='your-crusoe-api-key'
unset TAP_AGENT_MOCK
python tap_agent/app.py \
  --image /path/to/kitchen.jpg \
  --audio /path/to/kitchen.wav \
  --flow-rate 4.2 \
  --elapsed-seconds 45
```

## Architecture

```text
Image -> Vision Specialist --+
Audio -> Audio Specialist ---+-> deterministic Tap Manager -> Decision Specialist
Flow sensor -----------------+
```

The audio and vision specialists only interpret raw evidence and return structured JSON. The decision specialist combines already-extracted evidence into a resident-facing recommendation. It is not allowed to control physical devices.

## Deterministic Manager

The manager is ordinary Python, not an autonomous loop. It fuses evidence with fixed rules:

```python
sensor_positive = flow_rate_lpm is not None and flow_rate_lpm >= threshold
audio_positive = audio.confidence >= 0.70 and audio.water_sound_detected
vision_positive = vision.confidence >= 0.70 and vision.water_stream_visible
```

Water is considered probably running when a positive sensor or visual stream exists, or when confident audio is paired with a visible tap. Python safety rules override model output: visible overflow alerts immediately, visible active sink use suppresses normal alerts, uncertain evidence continues observation, and the system never closes a valve.

## SQLite Memory

`TapMemory` uses the standard library `sqlite3` module and creates two tables:

```text
preferences
incidents
```

Default preferences include ask and alert thresholds, the minimum flow rate, and an explicit `automatic_shutoff_allowed=false`. Incidents are saved when they are completed, escalated, or reach a resident-facing action. Recent feedback events are sent to the decision specialist for context.

## Feedback

Record feedback for a saved incident:

```bash
python tap_agent/app.py \
  --record-feedback-event-id EVENT_ID \
  --feedback "False alarm; I was filling a pot."
```

## Known Limitations

- Audio upload formats vary across OpenAI-compatible multimodal APIs; this prototype sends audio as a base64 data URL in the prompt.
- The manager keeps only one active event in memory per process.
- There is no camera/audio capture loop; the CLI processes one simulated tick.
- Mock mode returns fixed realistic observations and is intended only for local testing.
