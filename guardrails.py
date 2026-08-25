"""Deterministic security checks for the Bedrock-powered AWS agent."""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("guardrails")

AUDIT_LOG_PATH = Path("audit_log.jsonl")

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

_COMPILED_PATTERNS = [re.compile(pattern, re.IGNORECASE) for pattern in INJECTION_PATTERNS]

ALLOWED_INSTANCE_TYPES = {"t3.micro", "t2.small"}
DESTRUCTIVE_ACTIONS = {"terminate_instance"}


class GuardrailViolation(Exception):
    """Raised when a proposed agent action violates a deterministic policy."""


def scan_for_injection(user_input: str) -> tuple[bool, str | None]:
    """Return whether raw input matches a known prompt-injection phrase."""
    for pattern in _COMPILED_PATTERNS:
        match = pattern.search(user_input)
        if match:
            return True, match.group(0)
    return False, None


def check_action(tool_name: str, tool_input: dict) -> None:
    """Validate a model-proposed tool call before the AWS execution layer."""
    if tool_name == "create_ec2_instance":
        instance_type = tool_input.get("instance_type")
        if instance_type not in ALLOWED_INSTANCE_TYPES:
            raise GuardrailViolation(
                f"Instance type '{instance_type}' is not allowed. "
                f"Choose one of {sorted(ALLOWED_INSTANCE_TYPES)}."
            )

    if tool_name in DESTRUCTIVE_ACTIONS and not tool_input.get("human_confirmed"):
        raise GuardrailViolation(
            f"'{tool_name}' is destructive and requires explicit human confirmation. "
            "The agent cannot self-approve this action."
        )

    if (
        tool_name == "create_s3_bucket"
        and tool_input.get("public")
        and not tool_input.get("human_confirmed")
    ):
        raise GuardrailViolation(
            "Disabling S3 Block Public Access requires explicit human confirmation. "
            "The agent cannot self-approve this action."
        )


def audit(event_type: str, detail: dict) -> None:
    """Append a structured UTC audit record as one JSON line."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        **detail,
    }
    with AUDIT_LOG_PATH.open("a", encoding="utf-8") as audit_file:
        audit_file.write(json.dumps(record, default=str) + "\n")
    logger.info("[AUDIT] %s", event_type)
