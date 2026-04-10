"""
Prompt Loading Utilities
========================

Functions for loading prompt templates from the prompts directory.
Supports both build-mode (Harness 2) and spec-mode (Harness 1).
"""

import shutil
from pathlib import Path


PROMPTS_DIR = Path(__file__).parent / "prompts"
SPEC_PROMPTS_DIR = PROMPTS_DIR / "spec_mode"


# ─── Build-Mode Prompts (Harness 2) ──────────────────────────────────

def load_prompt(name: str) -> str:
    """Load a prompt template from the prompts directory."""
    prompt_path = PROMPTS_DIR / f"{name}.md"
    return prompt_path.read_text()


def get_initializer_prompt() -> str:
    """Load the build-mode initializer prompt."""
    return load_prompt("initializer_prompt")


def get_coding_prompt() -> str:
    """Load the build-mode coding agent prompt."""
    return load_prompt("coding_prompt")


def copy_spec_to_project(project_dir: Path) -> None:
    """Copy the app spec file into the project directory for the agent to read."""
    spec_source = PROMPTS_DIR / "app_spec.txt"
    spec_dest = project_dir / "app_spec.txt"
    if not spec_dest.exists():
        shutil.copy(spec_source, spec_dest)
        print("Copied app_spec.txt to project directory")


# ─── Spec-Mode Prompts (Harness 1) ───────────────────────────────────

def load_spec_prompt(name: str) -> str:
    """Load a prompt template from the spec_mode prompts directory."""
    prompt_path = SPEC_PROMPTS_DIR / f"{name}.md"
    return prompt_path.read_text()


def get_architect_prompt() -> str:
    """Load the spec-mode Lead Systems Architect initial prompt."""
    return load_spec_prompt("architect_prompt")


def get_architect_amend_prompt() -> str:
    """Load the spec-mode Architect amendment prompt."""
    return load_spec_prompt("architect_amend_prompt")


def get_requirements_auditor_prompt() -> str:
    """Load the Gate A requirements auditor prompt."""
    return load_spec_prompt("requirements_auditor")


def get_slice_reviewer_prompt() -> str:
    """Load the Gate B slice reviewer prompt template."""
    return load_spec_prompt("slice_reviewer")


def get_integration_reviewer_prompt() -> str:
    """Load the Gate C integration reviewer prompt."""
    return load_spec_prompt("integration_reviewer")


def get_skill_feasibility_prompt() -> str:
    """Load the Gate C skill feasibility tester prompt."""
    return load_spec_prompt("skill_feasibility")


def copy_seed_to_project(project_dir: Path) -> None:
    """Copy the seed spec into the project directory for the Architect to read."""
    seed_source = SPEC_PROMPTS_DIR / "seed_spec.txt"
    seed_dest = project_dir / "seed_spec.txt"
    if not seed_dest.exists():
        shutil.copy(seed_source, seed_dest)
        print("Copied seed_spec.txt to project directory")
