"""
Gate Logic — SRR / PDR / CDR
=============================

Implements the three design review gates with recursive cascade behavior.
Gates are safety-netted (iteration caps) but terminate on zero findings,
not on iteration count. The cap is a fail-safe, not a target.

Gate A (SRR): Requirements auditor checks root + north star.
Gate B (PDR): Slice reviewers check subsystems in parallel. Systemic findings
              trigger full re-review after Architect amends.
Gate C (CDR): Integration reviewer + skill feasibility tester. Both must pass.
"""

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from review_agents import (
    SPEC_SLICES,
    run_requirements_auditor,
    run_slice_reviewers_parallel,
    run_integration_reviewer,
    run_skill_feasibility,
)


# Default safety-net iteration caps
# These are fail-safes, NOT targets. Gates should pass well before hitting these.
GATE_A_MAX_ITERATIONS = 10
GATE_B_MAX_ITERATIONS = 15
GATE_C_MAX_ITERATIONS = 10


async def run_architect_session(
    project_dir: Path,
    model: str,
    prompt_template: str,
    commit_message: str,
) -> str:
    """
    Run an Architect agent session (initial or amendment).

    Args:
        project_dir: Project directory
        model: Claude model to use
        prompt_template: The prompt text to send
        commit_message: Git commit message for this session

    Returns:
        Response text from the agent
    """
    from client import create_spec_client

    print("\nStarting Architect session...\n")

    client = create_spec_client(
        project_dir, model, role="architect",
        system_prompt="You are the Lead Systems Architect for the RISC Engine project. You are a meticulous systems engineer who values conceptual integrity above all else.",
    )

    response_text = ""
    async with client:
        await client.query(prompt_template)
        async for msg in client.receive_response():
            msg_type = type(msg).__name__
            if msg_type == "AssistantMessage" and hasattr(msg, "content"):
                for block in msg.content:
                    block_type = type(block).__name__
                    if block_type == "TextBlock" and hasattr(block, "text"):
                        response_text += block.text
                        print(block.text, end="", flush=True)
                    elif block_type == "ToolUseBlock" and hasattr(block, "name"):
                        print(f"\n[Tool: {block.name}]", flush=True)
                        if hasattr(block, "input"):
                            input_str = str(block.input)
                            if len(input_str) > 200:
                                print(f"   Input: {input_str[:200]}...", flush=True)
                            else:
                                print(f"   Input: {input_str}", flush=True)
            elif msg_type == "UserMessage" and hasattr(msg, "content"):
                for block in msg.content:
                    block_type = type(block).__name__
                    if block_type == "ToolResultBlock":
                        result_content = getattr(block, "content", "")
                        is_error = getattr(block, "is_error", False)
                        if "blocked" in str(result_content).lower():
                            print(f"   [BLOCKED] {result_content}", flush=True)
                        elif is_error:
                            error_str = str(result_content)[:500]
                            print(f"   [Error] {error_str}", flush=True)
                        else:
                            print("   [Done]", flush=True)

    print("\n" + "─" * 60 + "\n")
    return response_text


def load_architect_prompt(is_initial: bool) -> str:
    """Load the appropriate architect prompt."""
    prompt_dir = Path(__file__).parent / "prompts" / "spec_mode"
    if is_initial:
        return (prompt_dir / "architect_prompt.md").read_text()
    else:
        return (prompt_dir / "architect_amend_prompt.md").read_text()


def _format_findings_for_architect(all_findings: dict | list, gate: str) -> str:
    """Format findings into a text block the Architect can read."""
    lines = [f"\n## Findings from {gate} that require your attention:\n"]

    if isinstance(all_findings, list):
        # Gate A / Gate C single-reviewer format
        for finding in all_findings:
            lines.append(f"### {finding.get('id', 'UNKNOWN')}")
            lines.append(f"- **Severity**: {finding.get('severity', '?')}")
            lines.append(f"- **Scope**: {finding.get('scope', '?')}")
            lines.append(f"- **Location**: {finding.get('location', '?')}")
            lines.append(f"- **Description**: {finding.get('description', '?')}")
            lines.append(f"- **Affected subsystems**: {', '.join(finding.get('affected_subsystems', []))}")
            lines.append(f"- **Recommendation**: {finding.get('recommendation', '?')}")
            lines.append("")
    elif isinstance(all_findings, dict):
        # Gate B multi-slice format
        for slice_name, findings in all_findings.items():
            if not findings:
                continue
            lines.append(f"\n### Slice: {slice_name}")
            for finding in findings:
                lines.append(f"#### {finding.get('id', 'UNKNOWN')}")
                lines.append(f"- **Severity**: {finding.get('severity', '?')}")
                lines.append(f"- **Scope**: {finding.get('scope', '?')}")
                lines.append(f"- **Location**: {finding.get('location', '?')}")
                lines.append(f"- **Description**: {finding.get('description', '?')}")
                lines.append(f"- **Affected subsystems**: {', '.join(finding.get('affected_subsystems', []))}")
                lines.append(f"- **Recommendation**: {finding.get('recommendation', '?')}")
                lines.append("")

    return "\n".join(lines)


async def run_gate_a(
    project_dir: Path,
    model: str,
    max_iterations: int = GATE_A_MAX_ITERATIONS,
) -> bool:
    """
    Gate A — System Requirements Review (SRR).

    Exit criteria: zero blocking findings from the requirements auditor.
    The North Star is frozen after this gate passes.

    Args:
        project_dir: Project directory
        model: Claude model to use
        max_iterations: Safety-net iteration cap

    Returns:
        True if gate passed, False if exhausted
    """
    print("\n" + "=" * 70)
    print("  GATE A — SYSTEM REQUIREMENTS REVIEW (SRR)")
    print("=" * 70)

    for iteration in range(1, max_iterations + 1):
        print(f"\n  Gate A iteration {iteration}/{max_iterations} (safety net)")

        # Run requirements auditor
        findings = await run_requirements_auditor(project_dir, model, iteration)

        if not findings:
            print(f"\n  GATE A PASSED on iteration {iteration}")
            _write_gate_result(project_dir, "A", "passed", iteration)
            return True

        print(f"\n  Gate A: {len(findings)} blocking finding(s) — routing to Architect")

        # Build amendment prompt with findings
        amend_prompt = load_architect_prompt(is_initial=False)
        findings_text = _format_findings_for_architect(findings, "Gate A (SRR)")
        full_prompt = amend_prompt + "\n\n" + findings_text

        await run_architect_session(
            project_dir, model, full_prompt,
            commit_message=f"Amend spec: address Gate A iteration {iteration} findings",
        )

    print(f"\n  GATE A EXHAUSTED safety net ({max_iterations} iterations)")
    _write_escalation(project_dir, "A", max_iterations)
    return False


async def run_gate_b(
    project_dir: Path,
    model: str,
    max_iterations: int = GATE_B_MAX_ITERATIONS,
) -> bool:
    """
    Gate B — Preliminary Design Review (PDR).

    Runs slice reviewers in parallel. If any finding has scope "systemic",
    ALL slices are re-reviewed after the Architect amends (recursive cascade).
    If findings are local-only, only affected slices are re-reviewed.

    Exit criteria: zero blocking findings across all slices.

    Args:
        project_dir: Project directory
        model: Claude model to use
        max_iterations: Safety-net iteration cap

    Returns:
        True if gate passed, False if exhausted
    """
    print("\n" + "=" * 70)
    print("  GATE B — PRELIMINARY DESIGN REVIEW (PDR)")
    print("=" * 70)

    all_slice_names = {s["name"] for s in SPEC_SLICES}
    slices_to_review = all_slice_names.copy()  # Start by reviewing all

    for iteration in range(1, max_iterations + 1):
        print(f"\n  Gate B iteration {iteration}/{max_iterations} (safety net)")
        print(f"  Reviewing {len(slices_to_review)} slice(s)")

        # Run slice reviewers in parallel
        all_findings = await run_slice_reviewers_parallel(
            project_dir, model, iteration, slices_to_review,
        )

        # Check if any findings exist
        total_findings = sum(len(f) for f in all_findings.values())
        if total_findings == 0:
            print(f"\n  GATE B PASSED on iteration {iteration}")
            _write_gate_result(project_dir, "B", "passed", iteration)
            return True

        print(f"\n  Gate B: {total_findings} blocking finding(s) across slices")

        # Determine cascade scope
        has_systemic = any(
            finding.get("scope") == "systemic"
            for findings in all_findings.values()
            for finding in findings
        )

        if has_systemic:
            print("  SYSTEMIC finding detected — full cascade: ALL slices will be re-reviewed")
        else:
            print("  Local findings only — re-reviewing affected slices")

        # Build amendment prompt with all findings
        amend_prompt = load_architect_prompt(is_initial=False)
        findings_text = _format_findings_for_architect(all_findings, "Gate B (PDR)")
        full_prompt = amend_prompt + "\n\n" + findings_text

        await run_architect_session(
            project_dir, model, full_prompt,
            commit_message=f"Amend spec: address Gate B iteration {iteration} findings ({'systemic' if has_systemic else 'local'})",
        )

        # Determine which slices need re-review
        if has_systemic:
            # Systemic change — re-review everything
            slices_to_review = all_slice_names.copy()
        else:
            # Local changes — only re-review slices that had findings
            slices_to_review = {
                name for name, findings in all_findings.items()
                if findings
            }

    print(f"\n  GATE B EXHAUSTED safety net ({max_iterations} iterations)")
    _write_escalation(project_dir, "B", max_iterations)
    return False


async def run_gate_c(
    project_dir: Path,
    model: str,
    max_iterations: int = GATE_C_MAX_ITERATIONS,
) -> bool:
    """
    Gate C — Critical Design Review (CDR).

    Runs integration reviewer and skill feasibility tester in parallel.
    Both must pass for the gate to pass.
    The skill feasibility test generates actual code and type-checks it.

    Exit criteria: zero blocking findings from both reviewers.

    Args:
        project_dir: Project directory
        model: Claude model to use
        max_iterations: Safety-net iteration cap

    Returns:
        True if gate passed, False if exhausted
    """
    print("\n" + "=" * 70)
    print("  GATE C — CRITICAL DESIGN REVIEW (CDR)")
    print("=" * 70)

    for iteration in range(1, max_iterations + 1):
        print(f"\n  Gate C iteration {iteration}/{max_iterations} (safety net)")

        # Run integration reviewer and skill feasibility in parallel
        integration_task = run_integration_reviewer(project_dir, model, iteration)
        skill_task = run_skill_feasibility(project_dir, model, iteration)

        results = await asyncio.gather(
            integration_task, skill_task,
            return_exceptions=True,
        )

        # Collect findings from both
        integration_findings = []
        skill_findings = []

        if isinstance(results[0], Exception):
            print(f"\n  [ERROR] Integration reviewer crashed: {results[0]}")
            integration_findings = [{
                "id": "CDR-CRASH-001",
                "severity": "critical",
                "scope": "systemic",
                "description": f"Integration reviewer crashed: {results[0]}",
                "affected_subsystems": [],
                "recommendation": "Investigate and retry",
            }]
        else:
            integration_findings = results[0]

        if isinstance(results[1], Exception):
            print(f"\n  [ERROR] Skill feasibility tester crashed: {results[1]}")
            skill_findings = [{
                "id": "CDR-CRASH-002",
                "severity": "critical",
                "scope": "systemic",
                "description": f"Skill feasibility tester crashed: {results[1]}",
                "affected_subsystems": [],
                "recommendation": "Investigate and retry",
            }]
        else:
            skill_findings = results[1]

        all_findings = integration_findings + skill_findings

        if not all_findings:
            print(f"\n  GATE C PASSED on iteration {iteration}")
            _write_gate_result(project_dir, "C", "passed", iteration)
            return True

        print(f"\n  Gate C: {len(all_findings)} blocking finding(s)")
        print(f"    Integration: {len(integration_findings)}")
        print(f"    Skill feasibility: {len(skill_findings)}")

        # Build amendment prompt
        amend_prompt = load_architect_prompt(is_initial=False)
        findings_text = _format_findings_for_architect(all_findings, "Gate C (CDR)")
        full_prompt = amend_prompt + "\n\n" + findings_text

        await run_architect_session(
            project_dir, model, full_prompt,
            commit_message=f"Amend spec: address Gate C iteration {iteration} findings",
        )

    print(f"\n  GATE C EXHAUSTED safety net ({max_iterations} iterations)")
    _write_escalation(project_dir, "C", max_iterations)
    return False


def freeze_north_star(project_dir: Path) -> None:
    """
    Freeze the North Star after Gate A passes.
    Write a hash so any future modification can be detected.
    """
    north_star_path = project_dir / "spec" / "00_north_star.md"
    if north_star_path.exists():
        content = north_star_path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()

        hash_path = project_dir / "spec" / "00_north_star.sha256"
        hash_path.write_text(f"{digest}  00_north_star.md\n")

        print(f"\n  North Star frozen: {digest[:16]}...")
    else:
        print("\n  [WARNING] 00_north_star.md not found — cannot freeze")


def freeze_spec_bundle(project_dir: Path) -> None:
    """
    Freeze the entire spec bundle after Gate C passes.
    Compute hashes for all spec files and write FROZEN.sha256.
    """
    spec_dir = project_dir / "spec"
    if not spec_dir.exists():
        print("\n  [WARNING] spec/ directory not found — cannot freeze")
        return

    lines = []
    for spec_file in sorted(spec_dir.glob("*")):
        if spec_file.suffix == ".sha256":
            continue
        content = spec_file.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        lines.append(f"{digest}  {spec_file.name}")

    # Also hash feature_list.json
    feature_list = project_dir / "feature_list.json"
    if feature_list.exists():
        content = feature_list.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        lines.append(f"{digest}  feature_list.json")

    frozen_path = project_dir / "FROZEN.sha256"
    frozen_content = "\n".join(lines) + "\n"
    frozen_path.write_text(frozen_content)

    timestamp = datetime.now(timezone.utc).isoformat()
    print(f"\n  Spec bundle frozen at {timestamp}")
    print(f"  Hash manifest written to FROZEN.sha256")
    print(f"  {len(lines)} files hashed")


def _write_gate_result(project_dir: Path, gate: str, result: str, iteration: int) -> None:
    """Write a gate result file for audit trail."""
    results_dir = project_dir / "findings"
    results_dir.mkdir(parents=True, exist_ok=True)

    result_data = {
        "gate": gate,
        "result": result,
        "iteration": iteration,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    result_path = results_dir / f"gate_{gate.lower()}_result.json"
    with open(result_path, "w") as f:
        json.dump(result_data, f, indent=2)


def _write_escalation(project_dir: Path, gate: str, max_iterations: int) -> None:
    """Write an escalation document when a gate exhausts its iteration budget."""
    escalation_path = project_dir / "ESCALATION.md"

    # Read existing escalation if any
    existing = ""
    if escalation_path.exists():
        existing = escalation_path.read_text()

    timestamp = datetime.now(timezone.utc).isoformat()

    escalation = f"""
## Gate {gate} Escalation — {timestamp}

Gate {gate} exhausted its safety-net iteration budget of {max_iterations} iterations
without achieving zero blocking findings.

This means one of:
1. The North Star is underspecified and the Architect cannot converge
2. Two or more review agents have contradictory expectations
3. A finding is not actionable and the Architect is stuck in a loop

**Action required**: Review the findings in `findings/gate_{gate.lower()}/` and
determine whether to:
- Relax a constraint in the seed spec
- Override a specific finding
- Increase the iteration budget and retry

The spec bundle is NOT frozen. Do not proceed to the build harness.
"""

    with open(escalation_path, "w") as f:
        f.write(existing + escalation)

    print(f"\n  Escalation written to ESCALATION.md")
