"""Command line demo for the tap incident manager."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from time import time

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from tap_agent.manager import TapIncidentManager
from tap_agent.memory import TapMemory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smart-home tap incident CLI demo")
    parser.add_argument("--image", help="Path to a JPG, PNG, or WebP kitchen frame")
    parser.add_argument("--audio", help="Path to a short audio recording")
    parser.add_argument("--flow-rate", type=float, help="Optional flow rate in L/min")
    parser.add_argument(
        "--elapsed-seconds",
        type=float,
        default=0.0,
        help="Simulated elapsed duration for this event",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Optional SQLite path; defaults to TAP_AGENT_DB or tap_agent.sqlite3",
    )
    parser.add_argument("--record-feedback-event-id", help="Existing event id")
    parser.add_argument("--feedback", help="Feedback text to attach to an incident")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.feedback or args.record_feedback_event_id:
        if not (args.feedback and args.record_feedback_event_id):
            raise SystemExit("--feedback and --record-feedback-event-id must be used together")
        memory = TapMemory(args.db or "tap_agent.sqlite3")
        memory.record_feedback(args.record_feedback_event_id, args.feedback)
        print(json.dumps({"recorded": True, "event_id": args.record_feedback_event_id}, indent=2))
        return 0

    manager = TapIncidentManager(memory=TapMemory(args.db) if args.db else None)
    timestamp = time()
    if args.elapsed_seconds > 0:
        manager.process_tick(
            image_path=args.image,
            audio_path=args.audio,
            flow_rate_lpm=args.flow_rate,
            timestamp=timestamp - args.elapsed_seconds,
        )
    result = manager.process_tick(
        image_path=args.image,
        audio_path=args.audio,
        flow_rate_lpm=args.flow_rate,
        timestamp=timestamp,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

