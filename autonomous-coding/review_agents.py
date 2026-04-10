"""
Review Agent Spawning
=====================

Functions for creating and running review sub-agents that audit the spec bundle.
Each review agent gets a fresh context, reads its assigned files, and writes
structured findings to the findings/ directory.
"""

import asyncio
import json
from pathlib import Path
from typing import Optional

from claude_code_sdk import ClaudeSDKClient

from client import create_spec_client


# The spec bundle files that correspond to slice review assignments
SPEC_SLICES = [
    {
        "name": "north_star",
        "file": "spec/00_north_star.md",
        "label": "North Star & RISC Principles",
    },
    {
        "name": "render_graph",
        "file": "spec/01_render_graph.md",
        "label": "Render Graph Pipeline",
    },
    {
        "name": "scene_graph",
        "file": "spec/02_scene_graph.md",
        "label": "Scene Graph Primitives",
    },
    {
        "name": "systems",
        "file": "spec/03_systems.md",
        "label": "Input, Physics, Audio, Character Systems",
    },
    {
        "name": "module_interfaces",
        "file": "spec/04_module_interfaces.md",
        "label": "Module Interfaces & TypeScript Contracts",
    },
    {
        "name": "skill_authoring",
        "file": "spec/05_skill_authoring.md",
        "label": "Skill Authoring Guide",
    },
    {
        "name": "verification_plan",
        "file": "spec/06_verification_plan.md",
        "label": "Verification Plan",
    },
    {
        "name": "dependency_policy",
        "file": "spec/07_dependency_policy.md",
        "label": "Dependency Policy",
    },
]


async def run_review_session(
    client: ClaudeSDKClient,
    prompt: str,
) -> str:
    """
    Run a single review agent session and return the response text.

    Args:
        client: Configured Claude SDK client
        prompt: The review prompt to send

    Returns:
        Response text from the agent
    """
    await client.query(prompt)

    response_text = ""
    async for msg in client.receive_response():
        msg_type = type(msg).__name__
        if msg_type == "AssistantMessage" and hasattr(msg, "content"):
            for block in msg.content:
                block_type = type(block).__name__
                if block_type == "TextBlock" and hasattr(block, "text"):
                    response_text += block.text
                    print(block.text, end="", flush=True)
                elif block_type == "ToolUseBlock" and hasattr(block, "name"):
                    print(f"\n  [{block.name}]", end="", flush=True)
        elif msg_type == "UserMessage" and hasattr(msg, "content"):
            for block in msg.content:
                block_type = type(block).__name__
                if block_type == "ToolResultBlock":
                    is_error = getattr(block, "is_error", False)
                    if is_error:
                        error_str = str(getattr(block, "content", ""))[:300]
                        print(f"\n  [Error] {error_str}", flush=True)

    return response_text


def load_prompt_template(template_name: str) -> str:
    """Load a prompt template from prompts/spec_mode/."""
    prompt_path = Path(__file__).parent / "prompts" / "spec_mode" / f"{template_name}.md"
    return prompt_path.read_text()


def build_review_prompt(
    template: str,
    iteration: int,
    findings_dir: str,
    **kwargs,
) -> str:
    """
    Build a review prompt from a template with variable substitution.

    Args:
        template: The prompt template text
        iteration: Current gate iteration number
        findings_dir: Path where findings should be written
        **kwargs: Additional template variables (e.g., slice_file, slice_name)

    Returns:
        The fully substituted prompt
    """
    prompt = template

    # Substitute template variables
    for key, value in kwargs.items():
        prompt = prompt.replace(f"{{{key}}}", str(value))

    # Replace iteration number
    prompt = prompt.replace("ITERATION_NUMBER", str(iteration))

    # Append output instruction
    prompt += f"\n\n---\n\nWrite your findings JSON file to: `{findings_dir}`\n"

    return prompt


async def run_requirements_auditor(
    project_dir: Path,
    model: str,
    iteration: int,
) -> list[dict]:
    """
    Run the Gate A requirements auditor.

    Args:
        project_dir: Project directory
        model: Claude model to use
        iteration: Current iteration number

    Returns:
        List of findings (empty if passed)
    """
    findings_path = f"findings/gate_a/requirements_auditor_{iteration:03d}.json"
    full_findings_path = project_dir / findings_path

    # Ensure directory exists
    full_findings_path.parent.mkdir(parents=True, exist_ok=True)

    template = load_prompt_template("requirements_auditor")
    prompt = build_review_prompt(
        template,
        iteration=iteration,
        findings_dir=findings_path,
    )

    print(f"\n{'─' * 60}")
    print(f"  REQUIREMENTS AUDITOR — Iteration {iteration}")
    print(f"{'─' * 60}\n")

    client = create_spec_client(
        project_dir, model, role="reviewer",
        system_prompt="You are a rigorous systems engineer performing a System Requirements Review. You are precise, thorough, and fair.",
    )

    async with client:
        await run_review_session(client, prompt)

    return _read_findings(full_findings_path)


async def run_slice_reviewer(
    project_dir: Path,
    model: str,
    iteration: int,
    slice_info: dict,
) -> tuple[str, list[dict]]:
    """
    Run a single Gate B slice reviewer.

    Args:
        project_dir: Project directory
        model: Claude model to use
        iteration: Current iteration number
        slice_info: Dict with name, file, label

    Returns:
        (slice_name, list of findings)
    """
    slice_name = slice_info["name"]
    findings_path = f"findings/gate_b/{slice_name}_{iteration:03d}.json"
    full_findings_path = project_dir / findings_path

    full_findings_path.parent.mkdir(parents=True, exist_ok=True)

    template = load_prompt_template("slice_reviewer")
    prompt = build_review_prompt(
        template,
        iteration=iteration,
        findings_dir=findings_path,
        slice_file=slice_info["file"],
        slice_name=slice_name,
    )

    print(f"\n  SLICE REVIEWER [{slice_name}] — Iteration {iteration}")

    client = create_spec_client(
        project_dir, model, role="reviewer",
        system_prompt=f"You are a systems engineer reviewing the {slice_info['label']} subsystem. You focus on interface coherence and structural integrity.",
    )

    async with client:
        await run_review_session(client, prompt)

    findings = _read_findings(full_findings_path)
    return slice_name, findings


async def run_slice_reviewers_parallel(
    project_dir: Path,
    model: str,
    iteration: int,
    slices_to_review: Optional[set[str]] = None,
) -> dict[str, list[dict]]:
    """
    Run Gate B slice reviewers in parallel.

    Args:
        project_dir: Project directory
        model: Claude model to use
        iteration: Current iteration number
        slices_to_review: Set of slice names to review (None = all)

    Returns:
        Dict mapping slice_name -> list of findings
    """
    slices = SPEC_SLICES
    if slices_to_review:
        slices = [s for s in SPEC_SLICES if s["name"] in slices_to_review]

    print(f"\n{'─' * 60}")
    print(f"  SLICE REVIEWERS — Iteration {iteration}")
    print(f"  Reviewing: {', '.join(s['name'] for s in slices)}")
    print(f"{'─' * 60}")

    tasks = [
        run_slice_reviewer(project_dir, model, iteration, slice_info)
        for slice_info in slices
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_findings = {}
    for result in results:
        if isinstance(result, Exception):
            print(f"\n  [ERROR] Slice reviewer crashed: {result}")
            continue
        slice_name, findings = result
        all_findings[slice_name] = findings

    return all_findings


async def run_integration_reviewer(
    project_dir: Path,
    model: str,
    iteration: int,
) -> list[dict]:
    """
    Run the Gate C integration reviewer.

    Args:
        project_dir: Project directory
        model: Claude model to use
        iteration: Current iteration number

    Returns:
        List of findings
    """
    findings_path = f"findings/gate_c/integration_{iteration:03d}.json"
    full_findings_path = project_dir / findings_path

    full_findings_path.parent.mkdir(parents=True, exist_ok=True)

    template = load_prompt_template("integration_reviewer")
    prompt = build_review_prompt(
        template,
        iteration=iteration,
        findings_dir=findings_path,
    )

    print(f"\n{'─' * 60}")
    print(f"  INTEGRATION REVIEWER — Iteration {iteration}")
    print(f"{'─' * 60}\n")

    client = create_spec_client(
        project_dir, model, role="reviewer",
        system_prompt="You are a systems engineer performing a full-system integration review. You see the forest, not the trees.",
    )

    async with client:
        await run_review_session(client, prompt)

    return _read_findings(full_findings_path)


async def run_skill_feasibility(
    project_dir: Path,
    model: str,
    iteration: int,
) -> list[dict]:
    """
    Run the Gate C skill feasibility tester.

    Args:
        project_dir: Project directory
        model: Claude model to use
        iteration: Current iteration number

    Returns:
        List of findings
    """
    findings_path = f"findings/gate_c/skill_feasibility_{iteration:03d}.json"
    full_findings_path = project_dir / findings_path

    full_findings_path.parent.mkdir(parents=True, exist_ok=True)

    template = load_prompt_template("skill_feasibility")
    prompt = build_review_prompt(
        template,
        iteration=iteration,
        findings_dir=findings_path,
    )

    print(f"\n{'─' * 60}")
    print(f"  SKILL FEASIBILITY TESTER — Iteration {iteration}")
    print(f"{'─' * 60}\n")

    client = create_spec_client(
        project_dir, model, role="reviewer",
        system_prompt="You are a systems engineer testing skill feasibility by actually generating code. You simulate a fresh Claude session with only the SKILL.md as context.",
    )

    async with client:
        await run_review_session(client, prompt)

    return _read_findings(full_findings_path)


def _read_findings(findings_path: Path) -> list[dict]:
    """
    Read findings from a JSON file written by a review agent.

    Returns:
        List of finding dicts, or empty list if file missing or parse error.
    """
    if not findings_path.exists():
        print(f"\n  [WARNING] Expected findings file not found: {findings_path}")
        print("  The review agent may not have written its output.")
        return []

    try:
        with open(findings_path, "r") as f:
            data = json.load(f)

        verdict = data.get("verdict", "unknown")
        findings = data.get("findings", [])

        if verdict == "pass":
            print(f"\n  Verdict: PASS (0 findings)")
            return []

        # Filter to only blocking findings (critical + major)
        blocking = [
            f for f in findings
            if f.get("severity") in ("critical", "major")
        ]
        minor = [f for f in findings if f.get("severity") == "minor"]

        print(f"\n  Verdict: FAIL")
        print(f"    Blocking findings: {len(blocking)}")
        print(f"    Minor findings: {len(minor)}")
        for finding in blocking:
            print(f"    - [{finding.get('severity', '?').upper()}] {finding.get('id', '?')}: {finding.get('description', '?')[:100]}")

        return blocking  # Only return blocking findings

    except (json.JSONDecodeError, IOError) as e:
        print(f"\n  [WARNING] Could not parse findings file: {e}")
        return []


def collect_all_findings(project_dir: Path, gate: str) -> dict:
    """
    Collect all findings files for a given gate.

    Args:
        project_dir: Project directory
        gate: Gate name ("gate_a", "gate_b", "gate_c")

    Returns:
        Dict with all findings data
    """
    findings_dir = project_dir / "findings" / gate
    if not findings_dir.exists():
        return {}

    all_data = {}
    for f in sorted(findings_dir.glob("*.json")):
        try:
            with open(f, "r") as fh:
                all_data[f.stem] = json.load(fh)
        except (json.JSONDecodeError, IOError):
            continue

    return all_data
