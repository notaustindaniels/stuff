"""
Claude SDK Client Configuration
===============================

Functions for creating and configuring the Claude Agent SDK client.
Supports both build-mode (Harness 2) and spec-mode (Harness 1) with
appropriate security restrictions for each.
"""

import json
import os
from pathlib import Path

from claude_code_sdk import ClaudeCodeOptions, ClaudeSDKClient
from claude_code_sdk.types import HookMatcher

from security import (
    bash_security_hook,
    create_bash_security_hook,
    SPEC_ALLOWED_COMMANDS,
    BUILD_ALLOWED_COMMANDS,
)


# Puppeteer MCP tools for browser automation (build-mode only)
PUPPETEER_TOOLS = [
    "mcp__puppeteer__puppeteer_navigate",
    "mcp__puppeteer__puppeteer_screenshot",
    "mcp__puppeteer__puppeteer_click",
    "mcp__puppeteer__puppeteer_fill",
    "mcp__puppeteer__puppeteer_select",
    "mcp__puppeteer__puppeteer_hover",
    "mcp__puppeteer__puppeteer_evaluate",
]

# Built-in tools
BUILTIN_TOOLS = [
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "Bash",
]

# Spec-mode tools: no Puppeteer, the agent only works with documents
SPEC_TOOLS = [
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "Bash",
]

# Spec-mode reviewer tools: read-only (no Write/Edit)
SPEC_REVIEWER_TOOLS = [
    "Read",
    "Write",  # Only to write findings JSON
    "Glob",
    "Grep",
    "Bash",
]


def create_client(project_dir: Path, model: str) -> ClaudeSDKClient:
    """
    Create a Claude Agent SDK client for build-mode (Harness 2).

    Args:
        project_dir: Directory for the project
        model: Claude model to use

    Returns:
        Configured ClaudeSDKClient

    Security layers (defense in depth):
    1. Sandbox - OS-level bash command isolation prevents filesystem escape
    2. Permissions - File operations restricted to project_dir only
    3. Security hooks - Bash commands validated against an allowlist
       (see security.py for BUILD_ALLOWED_COMMANDS)
    """
    security_settings = {
        "sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True},
        "permissions": {
            "defaultMode": "acceptEdits",
            "allow": [
                "Read(./**)",
                "Write(./**)",
                "Edit(./**)",
                "Glob(./**)",
                "Grep(./**)",
                "Bash(*)",
                *PUPPETEER_TOOLS,
            ],
        },
    }

    project_dir.mkdir(parents=True, exist_ok=True)

    settings_file = project_dir / ".claude_settings.json"
    with open(settings_file, "w") as f:
        json.dump(security_settings, f, indent=2)

    print(f"Created security settings at {settings_file}")
    print("   - Sandbox enabled (OS-level bash isolation)")
    print(f"   - Filesystem restricted to: {project_dir.resolve()}")
    print("   - Bash commands restricted to build-mode allowlist")
    print("   - MCP servers: puppeteer (browser automation)")
    print()

    return ClaudeSDKClient(
        options=ClaudeCodeOptions(
            model=model,
            system_prompt="You are an expert full-stack developer building a production-quality web application.",
            allowed_tools=[
                *BUILTIN_TOOLS,
                *PUPPETEER_TOOLS,
            ],
            mcp_servers={
                "puppeteer": {"command": "npx", "args": ["puppeteer-mcp-server"]}
            },
            hooks={
                "PreToolUse": [
                    HookMatcher(matcher="Bash", hooks=[bash_security_hook]),
                ],
            },
            max_turns=1000,
            cwd=str(project_dir.resolve()),
            settings=str(settings_file.resolve()),
        )
    )


def create_spec_client(
    project_dir: Path,
    model: str,
    role: str = "architect",
    system_prompt: str = "",
) -> ClaudeSDKClient:
    """
    Create a Claude Agent SDK client for spec-mode (Harness 1).

    Tightened security: no npm, node, Puppeteer, or network access beyond
    the Anthropic API. The spec harness only reads seed files and writes
    specification documents.

    Args:
        project_dir: Directory for the spec project
        model: Claude model to use
        role: "architect" (can read/write all spec files) or
              "reviewer" (can read spec, write only to findings/)
        system_prompt: System prompt for this agent

    Returns:
        Configured ClaudeSDKClient
    """
    # Determine permissions based on role
    if role == "architect":
        permissions = [
            "Read(./**)",
            "Write(./**)",
            "Edit(./**)",
            "Glob(./**)",
            "Grep(./**)",
            "Bash(*)",
        ]
        tools = SPEC_TOOLS
    elif role == "reviewer":
        permissions = [
            "Read(./**)",
            "Write(./findings/**)",
            "Glob(./**)",
            "Grep(./**)",
            "Bash(*)",
        ]
        tools = SPEC_REVIEWER_TOOLS
    else:
        raise ValueError(f"Unknown role: {role}. Must be 'architect' or 'reviewer'.")

    security_settings = {
        "sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True},
        "permissions": {
            "defaultMode": "acceptEdits",
            "allow": permissions,
        },
    }

    project_dir.mkdir(parents=True, exist_ok=True)

    settings_file = project_dir / ".claude_settings.json"
    with open(settings_file, "w") as f:
        json.dump(security_settings, f, indent=2)

    # Use the tightened spec-mode security hook
    spec_hook = create_bash_security_hook(SPEC_ALLOWED_COMMANDS)

    if not system_prompt:
        system_prompt = (
            "You are a systems engineer working on the RISC Engine specification. "
            "You value conceptual integrity, interface coherence, and architectural rigor."
        )

    return ClaudeSDKClient(
        options=ClaudeCodeOptions(
            model=model,
            system_prompt=system_prompt,
            allowed_tools=tools,
            # No MCP servers in spec mode — no Puppeteer, no browser
            hooks={
                "PreToolUse": [
                    HookMatcher(matcher="Bash", hooks=[spec_hook]),
                ],
            },
            max_turns=500,
            cwd=str(project_dir.resolve()),
            settings=str(settings_file.resolve()),
        )
    )
