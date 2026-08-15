"""Command-line interface for AgentVerify."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from agentverify import __version__

EXIT_SUCCESS = 0
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    """Build the AgentVerify argument parser."""
    parser = argparse.ArgumentParser(
        prog="agentverify",
        description="Independent verification for AI coding agent output.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"AgentVerify {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    verify_parser = subparsers.add_parser(
        "verify",
        help="validate verification inputs (execution is not implemented)",
    )
    verify_parser.add_argument(
        "--plan",
        required=True,
        type=Path,
        help="path to a verification plan file",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the AgentVerify CLI and return its process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "verify":
        plan = args.plan.expanduser().resolve()
        if not plan.exists():
            print(f"agentverify: error: plan does not exist: {plan}", file=sys.stderr)
            return EXIT_USAGE
        if not plan.is_file():
            print(f"agentverify: error: plan must be a regular file: {plan}", file=sys.stderr)
            return EXIT_USAGE

        print("Verification execution is not implemented yet.")
        print(f"Plan: {plan}")
        return EXIT_SUCCESS

    parser.error(f"unsupported command: {args.command}")
