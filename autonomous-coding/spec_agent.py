"""
Spec-Mode Agent Orchestration
==============================

Top-level orchestration for the spec-mode harness (Harness 1).
Runs the Lead Systems Architect to produce the initial spec bundle,
then drives it through three design review gates (SRR → PDR → CDR)
before freezing the final specification.

Output: a frozen spec bundle in project_dir/spec/ plus a feature_list.json,
ready to be consumed by the build-mode harness (Harness 2).
"""

import shutil
from pathlib import Path
from typing import Optional

from gates import (
    run_gate_a,
    run_gate_b,
    run_gate_c,
    run_architect_session,
    load_architect_prompt,
    freeze_north_star,
    freeze_spec_bundle,
    GATE_A_MAX_ITERATIONS,
    GATE_B_MAX_ITERATIONS,
    GATE_C_MAX_ITERATIONS,
)


def copy_seed_to_project(project_dir: Path) -> None:
    """Copy the seed spec into the project directory for the Architect to read."""
    seed_source = Path(__file__).parent / "prompts" / "spec_mode" / "seed_spec.txt"
    seed_dest = project_dir / "seed_spec.txt"
    if not seed_dest.exists():
        shutil.copy(seed_source, seed_dest)
        print("  Copied seed_spec.txt to project directory")


def detect_spec_state(project_dir: Path) -> str:
    """
    Detect the current state of the spec process.

    Returns one of:
        "fresh" — No spec files exist yet
        "architect_done" — Spec bundle exists but no gate results
        "gate_a_passed" — Gate A passed, North Star frozen
        "gate_b_passed" — Gate B passed
        "gate_c_passed" — Gate C passed, spec frozen
        "escalated" — A gate exhausted its budget
        "in_progress" — Some gate is in progress (has findings but no result)
    """
    spec_dir = project_dir / "spec"
    findings_dir = project_dir / "findings"
    frozen_path = project_dir / "FROZEN.sha256"
    escalation_path = project_dir / "ESCALATION.md"

    if frozen_path.exists():
        return "gate_c_passed"

    if escalation_path.exists():
        return "escalated"

    # Check gate results
    gate_c_result = findings_dir / "gate_c_result.json"
    gate_b_result = findings_dir / "gate_b_result.json"
    gate_a_result = findings_dir / "gate_a_result.json"

    if gate_c_result.exists():
        return "gate_c_passed"
    if gate_b_result.exists():
        return "gate_b_passed"
    if gate_a_result.exists():
        return "gate_a_passed"

    if spec_dir.exists() and any(spec_dir.iterdir()):
        return "architect_done"

    return "fresh"


async def run_spec_agent(
    project_dir: Path,
    model: str,
    max_iterations: Optional[int] = None,
) -> None:
    """
    Run the spec-mode agent pipeline.

    Phases:
    1. Architect produces initial spec bundle
    2. Gate A (SRR): Requirements auditor checks root + north star
    3. Gate B (PDR): Slice reviewers check subsystems with recursive cascade
    4. Gate C (CDR): Integration reviewer + skill feasibility tester
    5. Freeze: Hash the bundle, write FROZEN.sha256

    Args:
        project_dir: Directory for the spec project
        model: Claude model to use
        max_iterations: Optional global iteration override (applies to all gates)
    """
    print("\n" + "=" * 70)
    print("  RISC ENGINE — SPEC-MODE HARNESS (Harness 1)")
    print("=" * 70)
    print(f"\n  Project directory: {project_dir}")
    print(f"  Model: {model}")
    print()

    # Create project directory
    project_dir.mkdir(parents=True, exist_ok=True)

    # Detect current state for resume support
    state = detect_spec_state(project_dir)
    print(f"  Current state: {state}")

    if state == "gate_c_passed":
        print("\n  Spec bundle is already frozen. Nothing to do.")
        print(f"  Frozen manifest: {project_dir / 'FROZEN.sha256'}")
        return

    if state == "escalated":
        print("\n  A previous run escalated. Review ESCALATION.md before retrying.")
        print("  To retry, delete ESCALATION.md and run again.")
        return

    # Determine iteration caps
    gate_a_cap = max_iterations or GATE_A_MAX_ITERATIONS
    gate_b_cap = max_iterations or GATE_B_MAX_ITERATIONS
    gate_c_cap = max_iterations or GATE_C_MAX_ITERATIONS

    # ─── PHASE 1: ARCHITECT INITIAL SESSION ───────────────────────────

    if state == "fresh":
        print("\n" + "=" * 70)
        print("  PHASE 1: LEAD SYSTEMS ARCHITECT — INITIAL SPEC BUNDLE")
        print("=" * 70)
        print()
        print("  The Architect will read the seed specification and produce")
        print("  the complete spec bundle. This may take 15-30 minutes.")
        print()

        copy_seed_to_project(project_dir)

        architect_prompt = load_architect_prompt(is_initial=True)
        await run_architect_session(
            project_dir, model, architect_prompt,
            commit_message="Initial spec bundle from Lead Systems Architect",
        )

        state = "architect_done"

    # ─── PHASE 2: GATE A — SYSTEM REQUIREMENTS REVIEW ────────────────

    if state in ("architect_done", "in_progress"):
        print("\n" + "=" * 70)
        print("  PHASE 2: GATE A — SYSTEM REQUIREMENTS REVIEW (SRR)")
        print("=" * 70)
        print()
        print("  The Requirements Auditor will check the root documents for")
        print("  ambiguity, completeness, and consistency with the North Star.")
        print(f"  Safety net: {gate_a_cap} iterations")
        print()

        gate_a_passed = await run_gate_a(project_dir, model, gate_a_cap)

        if not gate_a_passed:
            print("\n  Gate A did not converge. See ESCALATION.md.")
            return

        # Freeze North Star — it is now immutable
        freeze_north_star(project_dir)
        state = "gate_a_passed"

    # ─── PHASE 3: GATE B — PRELIMINARY DESIGN REVIEW ─────────────────

    if state == "gate_a_passed":
        print("\n" + "=" * 70)
        print("  PHASE 3: GATE B — PRELIMINARY DESIGN REVIEW (PDR)")
        print("=" * 70)
        print()
        print("  Slice reviewers will audit each subsystem for interface")
        print("  coherence and structural integrity. Systemic findings")
        print("  trigger full re-review (recursive cascade).")
        print(f"  Safety net: {gate_b_cap} iterations")
        print()

        gate_b_passed = await run_gate_b(project_dir, model, gate_b_cap)

        if not gate_b_passed:
            print("\n  Gate B did not converge. See ESCALATION.md.")
            return

        state = "gate_b_passed"

    # ─── PHASE 4: GATE C — CRITICAL DESIGN REVIEW ────────────────────

    if state == "gate_b_passed":
        print("\n" + "=" * 70)
        print("  PHASE 4: GATE C — CRITICAL DESIGN REVIEW (CDR)")
        print("=" * 70)
        print()
        print("  Integration reviewer checks cross-file coherence.")
        print("  Skill feasibility tester generates actual scene trees")
        print("  and type-checks them against the declared interfaces.")
        print(f"  Safety net: {gate_c_cap} iterations")
        print()

        gate_c_passed = await run_gate_c(project_dir, model, gate_c_cap)

        if not gate_c_passed:
            print("\n  Gate C did not converge. See ESCALATION.md.")
            return

        state = "gate_c_passed"

    # ─── PHASE 5: FREEZE ─────────────────────────────────────────────

    print("\n" + "=" * 70)
    print("  PHASE 5: FREEZE SPEC BUNDLE")
    print("=" * 70)

    freeze_spec_bundle(project_dir)

    # Print final summary
    print("\n" + "=" * 70)
    print("  SPEC-MODE COMPLETE")
    print("=" * 70)
    print()
    print(f"  Spec bundle: {project_dir / 'spec'}/")
    print(f"  Feature list: {project_dir / 'feature_list.json'}")
    print(f"  Frozen manifest: {project_dir / 'FROZEN.sha256'}")
    print(f"  Change log: {project_dir / 'CHANGELOG.md'}")
    print()
    print("  Review the spec bundle. When satisfied, run Harness 2:")
    print()
    print(f"    python autonomous_agent_demo.py \\")
    print(f"      --mode build \\")
    print(f"      --spec-dir {project_dir / 'spec'} \\")
    print(f"      --project-dir ./engine_build")
    print()
    print("=" * 70)
