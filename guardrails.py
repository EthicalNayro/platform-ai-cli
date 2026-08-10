"""
guardrails.py — Security layer for the AI Agent

This module implements the "Zero Trust for AI Agents" pattern:
every action the LLM wants to take is checked here BEFORE it executes.

Think of this as a firewall that sits between "what the AI wants to do"
and "what actually happens to your AWS account."

Two independent defenses:
  1. Prompt Injection Detection — catches attempts to manipulate the
     model via the user's input text (e.g. "ignore previous instructions...")
  2. Action Guardrails — even if the model is "convinced" to do something
     bad, this layer enforces hard limits regardless of what the model says.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("guardrails")

AUDIT_LOG_PATH = Path("audit_log.jsonl")

# ── 1. PROMPT INJECTION DETECTION ────────────────────────────────
#
# These patterns are common "jailbreak" / injection phrasings.
# This is intentionally simple (regex-based) — in production you'd
# use a dedicated classifier, but this demonstrates the *concept*
# clearly for a portfolio project: catch suspicious instructions
# BEFORE they reach the model's decision-making.

INJECTION_PATTERNS = [
    r"ignore\s+(all|any|every|the)?\s*(previous|prior|above|earlier)?\s*instructions",
    r"disregard (the|your|all|any) (system prompt|rules|guardrails|instructions)",
    r"you are now",
    r"pretend (you are|to be)",
    r"act as (if|though) you have no restrictions",
    r"reveal your (system prompt|instructions)",
    r"do anything now",
    r"new instructions?:",
    r"override (the|your) (safety|security) (settings|checks)",
]

_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]


def scan_for_injection(user_input: str) -> tuple[bool, str | None]:
    """
    Scan raw user input for known prompt-injection phrasings.

    Returns (is_suspicious, matched_pattern).
    This runs BEFORE the input is ever sent to the model.
    """
    for pattern in _COMPILED_PATTERNS:
        match = pattern.search(user_input)
        if match:
            return True, match.group(0)
    return False, None


# ── 2. ACTION GUARDRAILS ─────────────────────────────────────────
#
# These mirror the guardrails already enforced in ec2.py / s3.py /
# route53.py in the Platform-CLI project. The agent must obey the
# SAME rules a human using the CLI directly would have to obey —
# the AI does not get elevated privileges just because it's an AI.

ALLOWED_INSTANCE_TYPES = {"t3.micro", "t2.small"}
MAX_MANAGED_INSTANCES = 2
DESTRUCTIVE_ACTIONS = {"terminate_instance", "delete_bucket", "delete_hosted_zone"}


class GuardrailViolation(Exception):
    """Raised when a proposed agent action violates a hard guardrail."""


def check_action(tool_name: str, tool_input: dict) -> None:
    """
    Validate a proposed tool call against hard guardrails.

    Raises GuardrailViolation if the action should be blocked.
    Called AFTER the model decides what to do, BEFORE we execute it.
    This is the "Zero Trust" checkpoint — we never trust the model's
    output blindly, even if the prompt looked clean.
    """
    if tool_name == "create_ec2_instance":
        instance_type = tool_input.get("instance_type")
        if instance_type not in ALLOWED_INSTANCE_TYPES:
            raise GuardrailViolation(
                f"Instance type '{instance_type}' is not in the allowed list "
                f"{ALLOWED_INSTANCE_TYPES}. The agent cannot override this."
            )

    if tool_name in DESTRUCTIVE_ACTIONS and not tool_input.get("human_confirmed"):
        # Destructive actions always require explicit human confirmation,
        # regardless of how confident the model sounds.
        raise GuardrailViolation(
            f"'{tool_name}' is a destructive action and requires "
            f"explicit human confirmation (human_confirmed=True). "
            f"The agent cannot self-approve this."
        )

    if tool_name == "create_s3_bucket" and tool_input.get("public") and not tool_input.get("human_confirmed"):
        raise GuardrailViolation(
            "Creating a PUBLIC S3 bucket requires explicit human "
            "confirmation. The agent cannot self-approve this."
        )


# ── 3. AUDIT LOGGING ─────────────────────────────────────────────
#
# Every decision — allowed or blocked — gets logged. This is the
# same principle as the AI Control Plane concept in the roadmap:
# nothing an agent does should be invisible.

def audit(event_type: str, detail: dict) -> None:
    """Append a structured audit record. Append-only, one JSON line per event."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        **detail,
    }
    with AUDIT_LOG_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")
    logger.info(f"[AUDIT] {event_type}: {detail}")
