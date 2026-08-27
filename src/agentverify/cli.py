"""Command-line interface for AgentVerify."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from agentverify import __version__
from agentverify.browser import BaseURLValidationError
from agentverify.browser_plan import BrowserVerificationPlan
from agentverify.domain import Verdict
from agentverify.inspection import (
    InspectionInputError,
    RunIntegrityError,
    inspect_run_directory,
)
from agentverify.plan import PlanError, load_plan
from agentverify.run import (
    RunConfigurationError,
    RunOperationalError,
    verify_local_application,
)

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_UNKNOWN = 3
EXIT_SUCCESS = EXIT_PASS


def _startup_timeout(value: str) -> int:
    try:
        timeout_ms = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if not 100 <= timeout_ms <= 60_000:
        raise argparse.ArgumentTypeError("must be from 100 to 60000 milliseconds")
    return timeout_ms


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
        help="execute a Plan v2 against one managed local web application",
    )
    verify_parser.add_argument(
        "--plan",
        required=True,
        type=Path,
        help="path to a verification plan file",
    )
    verify_parser.add_argument(
        "--base-url",
        required=True,
        help="loopback HTTP(S) origin exposed by the application",
    )
    verify_parser.add_argument(
        "--run-dir",
        required=True,
        type=Path,
        help="new or empty directory for evidence and receipts",
    )
    verify_parser.add_argument(
        "--startup-timeout-ms",
        type=_startup_timeout,
        default=10_000,
        help="bounded TCP readiness timeout from 100 to 60000 ms (default: 10000)",
    )
    verify_parser.add_argument(
        "--revision",
        help="optional local Git revision verified in a disposable detached worktree",
    )
    verify_parser.add_argument(
        "--isolation",
        choices=("none", "docker"),
        default="none",
        help="optional application isolation route (default: none)",
    )
    verify_parser.add_argument(
        "--isolation-image",
        help="existing local Linux image required by --isolation docker",
    )
    verify_parser.add_argument(
        "--app-command",
        required=True,
        nargs=argparse.REMAINDER,
        help="application argv; this must be the final AgentVerify option",
    )
    inspect_parser = subparsers.add_parser(
        "inspect",
        help="check the integrity of an existing AgentVerify run directory",
    )
    inspect_parser.add_argument(
        "--run-dir",
        required=True,
        type=Path,
        help="existing run directory containing a supported receipt",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the AgentVerify CLI and return its process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "verify":
        try:
            invocation_root = Path.cwd().resolve()
            plan_path = _resolve_from_invocation(args.plan, invocation_root)
            run_dir = _resolve_from_invocation(args.run_dir, invocation_root)
            plan = load_plan(plan_path)
            if not isinstance(plan, BrowserVerificationPlan):
                raise RunConfigurationError(
                    "executable local verification currently requires Plan v2"
                )
            outcome = verify_local_application(
                plan=plan,
                base_url=args.base_url,
                run_dir=run_dir,
                app_command=args.app_command,
                startup_timeout_ms=args.startup_timeout_ms,
                plan_source_path=plan_path,
                invocation_root=invocation_root,
                requested_revision=args.revision,
                isolation_mode=args.isolation,
                isolation_image=args.isolation_image,
            )
        except KeyboardInterrupt:
            print("agentverify: verification interrupted", file=sys.stderr)
            return EXIT_UNKNOWN
        except (PlanError, BaseURLValidationError, RunConfigurationError) as error:
            print(f"agentverify: error: {error}", file=sys.stderr)
            return EXIT_USAGE
        except RunOperationalError as error:
            print(f"agentverify: verification incomplete: {error}", file=sys.stderr)
            return EXIT_UNKNOWN
        except RunIntegrityError as error:
            print(f"agentverify: integrity warning: {error}", file=sys.stderr)
            return EXIT_UNKNOWN

        if outcome.plan_drift_warning is not None:
            print(outcome.plan_drift_warning, file=sys.stderr)
        print(f"Verdict: {outcome.receipt.overall_verdict.value}")
        print(f"Receipt: {outcome.receipt_text_path}")
        print(f"Receipt JSON: {outcome.receipt_json_path}")
        print(f"Evidence manifest: {outcome.evidence_manifest_path}")
        if outcome.receipt.overall_verdict is Verdict.PASS:
            return EXIT_PASS
        if outcome.receipt.overall_verdict is Verdict.FAIL:
            return EXIT_FAIL
        return EXIT_UNKNOWN

    if args.command == "inspect":
        try:
            inspection = inspect_run_directory(args.run_dir)
        except InspectionInputError as error:
            print(f"agentverify: error: {error}", file=sys.stderr)
            return EXIT_USAGE
        except RunIntegrityError as error:
            print(f"agentverify: integrity warning: {error}", file=sys.stderr)
            return EXIT_UNKNOWN

        print(f"Verdict: {inspection.receipt.overall_verdict.value}")
        print("Integrity: OK")
        print(f"Receipt schema: {inspection.receipt.schema_version}")
        print(f"Manifest: {inspection.receipt.evidence_manifest_digest}")
        return EXIT_SUCCESS

    parser.error(f"unsupported command: {args.command}")


def _resolve_from_invocation(path: Path, invocation_root: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        expanded = invocation_root / expanded
    return expanded.resolve()
