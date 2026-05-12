"""QCS Agent — CLI entry point.

Usage
-----
    qcs-agent [options]
    python -m qcs_agent.main [options]

All options can be set via environment variables (see config.py):
    QCS_CENTER_URL   URL of the QCS Center       (required)
    QCS_AGENT_TOKEN  Bearer token for auth        (required)
    QCS_AGENT_NAME   Unique name for this agent   (default: COMPUTERNAME)
    QCS_AGENT_TAGS   Comma-separated tag list     (optional)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

import config
from qcs_agent.loop import run_agent_loop


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="qcs-agent",
        description="QCS Agent — polls the QCS Center and executes recording jobs.",
    )
    p.add_argument(
        "--center-url",
        default=config.QCS_CENTER_URL,
        help="URL of QCS Center, e.g. http://10.0.0.5:8080  (default: %(default)s)",
    )
    p.add_argument(
        "--token",
        default=config.QCS_AGENT_TOKEN,
        help="Bearer token (same as QCS_CENTER_API_KEY on the center)",
    )
    p.add_argument(
        "--name",
        default=config.QCS_AGENT_NAME or os.environ.get("COMPUTERNAME", "agent-1"),
        help="Unique agent name (default: COMPUTERNAME env var)",
    )
    p.add_argument(
        "--tags",
        default=config.QCS_AGENT_TAGS,
        help="Comma-separated capability tags e.g. 'oracle,win10'",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p.parse_args()


def main() -> None:
    args = _parse()
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s  %(name)-24s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not args.token:
        print(
            "ERROR: --token / QCS_AGENT_TOKEN is required.\n"
            "Set it to the same value as QCS_CENTER_API_KEY on the center.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not args.center_url:
        print(
            "ERROR: --center-url / QCS_CENTER_URL is required.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        asyncio.run(
            run_agent_loop(
                center_url=args.center_url,
                token=args.token,
                agent_name=args.name,
                tags=args.tags,
            )
        )
    except KeyboardInterrupt:
        print("\nAgent stopped.")


if __name__ == "__main__":
    main()
