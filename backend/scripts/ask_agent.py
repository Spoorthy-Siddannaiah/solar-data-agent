#!/usr/bin/env python3
"""Ask one scoped plant-data question from the command line."""

from __future__ import annotations

import argparse
import sys

from backend.agent.runner import AgentRunnerError, run_agent_question
from backend.auth import UserContextError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ask the scoped solar data agent.")
    parser.add_argument(
        "--user",
        required=True,
        help="Stored demo user ID or email.",
    )
    parser.add_argument("--question", required=True, help="Question to answer.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        answer = run_agent_question(args.user, args.question)
    except (AgentRunnerError, UserContextError, ValueError) as error:
        print(f"Agent failed: {error}", file=sys.stderr)
        return 1
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
