#!/usr/bin/env python3
"""
Autonomous Coding Agent Demo
============================

A harness for long-running autonomous coding with Claude.
Supports two modes:

  spec  — Harness 1: Produces a systems-engineered specification bundle
          through staged design reviews (SRR → PDR → CDR).

  build — Harness 2: Builds an application from a specification using
          the two-agent pattern (initializer + coding agent).

Example Usage:
    # Spec mode (Harness 1): generate a systems-engineered spec
    python autonomous_agent_demo.py --mode spec --project-dir ./engine_spec

    # Build mode (Harness 2): build from a spec
    python autonomous_agent_demo.py --mode build --project-dir ./engine_build --spec-dir ./engine_spec/spec

    # Build mode (legacy): build from app_spec.txt (original behavior)
    python autonomous_agent_demo.py --project-dir ./my_project
"""

import argparse
import asyncio
import os
from pathlib import Path

from agent import run_autonomous_agent


# Default models per mode
SPEC_DEFAULT_MODEL = "claude-opus-4-6"
BUILD_DEFAULT_MODEL = "claude-sonnet-4-5-20250929"


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Autonomous Coding Agent — Spec-mode (Harness 1) and Build-mode (Harness 2)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Spec mode: produce a systems-engineered specification
  python autonomous_agent_demo.py --mode spec --project-dir ./engine_spec

  # Build mode: build from the produced spec
  python autonomous_agent_demo.py --mode build --project-dir ./engine_build --spec-dir ./engine_spec/spec

  # Build mode (legacy): use app_spec.txt directly
  python autonomous_agent_demo.py --project-dir ./my_project

  # Limit iterations for testing
  python autonomous_agent_demo.py --mode spec --project-dir ./engine_spec --max-iterations 3

Environment Variables:
  CLAUDE_CODE_OAUTH_TOKEN    Your Claude Code OAuth token (required)
        """,
    )

    parser.add_argument(
        "--mode",
        type=str,
        choices=["spec", "build"],
        default="build",
        help="Harness mode: 'spec' (Harness 1) produces a specification, 'build' (Harness 2) builds from one (default: build)",
    )

    parser.add_argument(
        "--project-dir",
        type=Path,
        default=Path("./autonomous_demo_project"),
        help="Directory for the project output (default: generations/autonomous_demo_project)",
    )

    parser.add_argument(
        "--spec-dir",
        type=Path,
        default=None,
        help="(Build mode only) Path to a spec/ directory produced by spec mode",
    )

    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Max iterations per gate (spec mode) or agent sessions (build mode). Default: unlimited for build, safety-net caps for spec.",
    )

    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help=f"Claude model to use (default: {SPEC_DEFAULT_MODEL} for spec, {BUILD_DEFAULT_MODEL} for build)",
    )

    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()

    # Check for API key
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        print("Error: CLAUDE_CODE_OAUTH_TOKEN environment variable not set")
        print("\nGet your OAuth token from: https://console.anthropic.com/")
        print("\nThen set it:")
        print("  export CLAUDE_CODE_OAUTH_TOKEN='your-oauth-token-here'")
        return

    # Set default model based on mode
    model = args.model
    if model is None:
        model = SPEC_DEFAULT_MODEL if args.mode == "spec" else BUILD_DEFAULT_MODEL

    # Automatically place projects in generations/ directory unless already specified
    project_dir = args.project_dir
    if not str(project_dir).startswith("generations/"):
        if project_dir.is_absolute():
            pass
        else:
            project_dir = Path("generations") / project_dir

    # Route to the appropriate harness
    try:
        if args.mode == "spec":
            from spec_agent import run_spec_agent

            asyncio.run(
                run_spec_agent(
                    project_dir=project_dir,
                    model=model,
                    max_iterations=args.max_iterations,
                )
            )
        else:
            # Build mode — original behavior
            asyncio.run(
                run_autonomous_agent(
                    project_dir=project_dir,
                    model=model,
                    max_iterations=args.max_iterations,
                )
            )
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        print("To resume, run the same command again")
    except Exception as e:
        print(f"\nFatal error: {e}")
        raise


if __name__ == "__main__":
    main()
