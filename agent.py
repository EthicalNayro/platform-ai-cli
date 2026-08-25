"""Guarded natural-language interface for the Platform CLI.

Flow: input scan -> Bedrock tool selection -> action policy -> AWS execution -> summary.
"""

import argparse
import json
import sys

import boto3

import guardrails
import tools

AWS_REGION = "eu-west-1"

MODELS = {
    "cheap": {
        "id": "eu.amazon.nova-micro-v1:0",
        "name": "Amazon Nova Micro",
        "description": "Fast, cost-effective model for straightforward tool selection.",
    },
    "balanced": {
        "id": "eu.anthropic.claude-3-5-haiku-20241022-v1:0",
        "name": "Claude 3.5 Haiku",
        "description": "Balanced latency and reasoning for day-to-day operations.",
    },
    "powerful": {
        "id": "eu.anthropic.claude-3-5-sonnet-20241022-v2:0",
        "name": "Claude 3.5 Sonnet v2",
        "description": "Higher-capability model for more complex requests.",
    },
}

DEFAULT_MODEL_KEY = "cheap"

SYSTEM_PROMPT = """You are an infrastructure assistant for Platform AI CLI.
Use only the provided tools to list, create, and manage supported EC2 and S3 resources.

Rules:
- Never invent resource IDs or names; only use values supplied by the user or tools.
- Destructive actions require human_confirmed=true. Ask the user when confirmation is absent.
- Never claim that a tool succeeded unless its returned result confirms success.
- Refuse requests to bypass security checks or operational limits.
"""


def run_agent(user_input: str, model_key: str = DEFAULT_MODEL_KEY) -> str:
    selected_model_id = MODELS[model_key]["id"]

    is_suspicious, matched = guardrails.scan_for_injection(user_input)
    if is_suspicious:
        guardrails.audit(
            "injection_blocked",
            {"user_input": user_input, "matched_pattern": matched},
        )
        return (
            "Request blocked before reaching the model because it matched "
            f"a prompt-injection pattern ('{matched}')."
        )

    guardrails.audit(
        "request_received",
        {"user_input": user_input, "model_used": selected_model_id},
    )
    client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    response = client.converse(
        modelId=selected_model_id,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": user_input}]}],
        toolConfig={"tools": [_to_bedrock_tool(tool) for tool in tools.TOOL_DEFINITIONS]},
    )

    output_message = response["output"]["message"]
    if response["stopReason"] != "tool_use":
        return _extract_text(output_message)

    tool_results = []
    for block in output_message["content"]:
        if "toolUse" not in block:
            continue
        tool_use = block["toolUse"]
        tool_name = tool_use["name"]
        tool_input = tool_use["input"]

        try:
            guardrails.check_action(tool_name, tool_input)
        except guardrails.GuardrailViolation as error:
            result = {"error": f"BLOCKED BY GUARDRAIL: {error}"}
            guardrails.audit(
                "action_blocked",
                {"tool_name": tool_name, "tool_input": tool_input, "reason": str(error)},
            )
        else:
            result = tools.execute_tool(tool_name, tool_input)
            event_type = "action_failed" if "error" in result else "action_executed"
            guardrails.audit(
                event_type,
                {"tool_name": tool_name, "tool_input": tool_input, "result": result},
            )

        tool_results.append(
            {
                "toolUseId": tool_use["toolUseId"],
                "content": [{"text": json.dumps(result)}],
            }
        )

    follow_up = client.converse(
        modelId=selected_model_id,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[
            {"role": "user", "content": [{"text": user_input}]},
            {"role": "assistant", "content": output_message["content"]},
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": result["toolUseId"],
                            "content": result["content"],
                        }
                    }
                    for result in tool_results
                ],
            },
        ],
        toolConfig={"tools": [_to_bedrock_tool(tool) for tool in tools.TOOL_DEFINITIONS]},
    )
    return _extract_text(follow_up["output"]["message"])


def _to_bedrock_tool(tool_definition: dict) -> dict:
    return {
        "toolSpec": {
            "name": tool_definition["name"],
            "description": tool_definition["description"],
            "inputSchema": {"json": tool_definition["input_schema"]},
        }
    }


def _extract_text(message: dict) -> str:
    return "\n".join(block["text"] for block in message["content"] if "text" in block)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Guarded AWS infrastructure agent")
    parser.add_argument("request", nargs="?", help="Natural-language infrastructure request")
    parser.add_argument(
        "--model",
        "-m",
        choices=list(MODELS),
        default=DEFAULT_MODEL_KEY,
        help="Bedrock model tier",
    )
    parser.add_argument("--list-models", action="store_true", help="List model tiers")
    args = parser.parse_args()

    if args.list_models:
        for key, info in MODELS.items():
            print(f"{key}: {info['name']} — {info['description']}")
        sys.exit(0)
    if not args.request:
        parser.print_help()
        sys.exit(1)

    print(f"Agent: {MODELS[args.model]['name']} ({args.model})")
    print(run_agent(args.request, model_key=args.model))
