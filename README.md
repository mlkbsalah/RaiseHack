# Home Agents

Home Agents is a smart-home observation prototype. You describe what you want
watched in a conversational web UI, and the app creates scheduled monitoring
tasks that can use camera and microphone streams.

The system can run fully in mock mode for local demos, or with real model and
voice providers when API keys are configured.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r home_agents/requirements.txt
HOME_AGENTS_MOCK=true python -m home_agents.app
```

Open http://127.0.0.1:8000 in your browser.

## Configuration

Create `home_agents/.env` for local settings and secrets. Useful values:

```text
HOME_AGENTS_MOCK=true
HOME_AGENTS_HOST=127.0.0.1
HOME_AGENTS_PORT=8000
CRUSOE_API_KEY=your-key-for-real-model-calls
GRADIUM_API_KEY=optional-key-for-real-speech-to-text
```

## Project Layout

- `home_agents/` - FastAPI app, agents, scheduler, task store, and frontend.
- `home_agents/frontend/` - Browser UI assets.
- `home_agents_data/` - Local runtime data such as tasks, memory, and debug logs.
- `data/` - Demo media.
- `example_cmds.sh` - Example commands for running and testing flows.

## More Detail

See `home_agents/README.md` for the full architecture notes, provider setup,
voice mode details, Telegram bridge, and stream examples.
