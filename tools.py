"""
tools.py — Real Execution Layer for Bedrock Agent

This module defines the tools schema for Claude and handles the actual
execution of AWS commands using boto3.
"""

import boto3
from botocore.exceptions import ClientError

TAG_FILTER = [{"Name": "tag:CreatedBy", "Values": ["platform-cli"]}]

# ── 1. TOOL DEFINITIONS (Contract with Bedrock/Claude) ───────────
TOOL_DEFINITIONS = [
    {
        "name": "list_ec2_instances",
        "description": "List all EC2 instances managed by this platform-cli tool (tagged CreatedBy=platform-cli).",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "create_ec2_instance",
        "description": "Create a new EC2 instance. Only t3.micro or t2.small allowed. Max 2 managed instances.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name tag for the instance"},
                "instance_type": {"type": "string", "enum": ["t3.micro", "t2.small"]},
                "os": {"type": "string", "enum": ["ubuntu", "amazon-linux"]},
            },
            "required": ["name", "instance_type", "os"],
        },
    },
    {
        "name": "stop_instance",
        "description": "Stop a running EC2 instance by ID. Instance must be tagged as CLI-managed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {"type": "string"},
            },
            "required": ["instance_id"],
        },
    },
    {
        "name": "terminate_instance",
        "description": "PERMANENTLY delete an EC2 instance. Destructive — requires human_confirmed=true.",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {"type": "string"},
                "human_confirmed": {
                    "type": "boolean",
                    "description": "Must be explicitly set to true by the human, never inferred by the model."
                },
            },
            "required": ["instance_id"],
        },
    },
    {
        "name": "list_s3_buckets",
        "description": "List all S3 buckets managed by this tool.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "create_s3_bucket",
        "description": "Create an S3 bucket. Private by default. Public buckets require human_confirmed=true.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "public": {"type": "boolean", "default": False},
                "human_confirmed": {"type": "boolean"},
            },
            "required": ["name"],
        },
    },
]


# ── 2. REAL AWS EXECUTION LAYER ──────────────────────────────────
def execute_tool(tool_name: str, tool_input: dict) -> dict:
    """
    Executes real boto3 calls against AWS.
    Called ONLY after guardrails.check_action() has approved the request.
    """
    ec2_client = boto3.client("ec2")
    s3_client = boto3.client("s3")
    ssm_client = boto3.client("ssm")

    try:
        # ── EC2 Operations ──
        if tool_name == "list_ec2_instances":
            resp = ec2_client.describe_instances(Filters=TAG_FILTER)
            instances = []
            for r in resp.get("Reservations", []):
                for i in r.get("Instances", []):
                    name_tag = next((t["Value"] for t in i.get("Tags", []) if t["Key"] == "Name"), "N/A")
                    instances.append({
                        "id": i["InstanceId"],
                        "name": name_tag,
                        "type": i.get("InstanceType"),
                        "state": i.get("State", {}).get("Name", "unknown")
                    })
            return {"instances": instances}

        if tool_name == "create_ec2_instance":
            os_type = tool_input.get("os", "ubuntu")
            param_name = (
                "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id"
                if os_type == "ubuntu"
                else "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
            )
            ami = ssm_client.get_parameter(Name=param_name)["Parameter"]["Value"]

            try:
                user_arn = boto3.client("sts").get_caller_identity()["Arn"].split("/")[-1]
            except ClientError:
                user_arn = "agent"

            resp = ec2_client.run_instances(
                ImageId=ami,
                InstanceType=tool_input["instance_type"],
                MinCount=1,
                MaxCount=1,
                TagSpecifications=[{
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": "CreatedBy", "Value": "platform-cli"},
                        {"Key": "Owner", "Value": user_arn},
                        {"Key": "Name", "Value": tool_input["name"]},
                    ]
                }]
            )
            inst = resp["Instances"][0]
            return {
                "created": {
                    "id": inst["InstanceId"],
                    "name": tool_input["name"],
                    "type": inst["InstanceType"],
                    "state": inst.get("State", {}).get("Name", "pending")
                }
            }

        if tool_name == "stop_instance":
            instance_id = tool_input["instance_id"]
            ec2_client.stop_instances(InstanceIds=[instance_id])
            return {"stopped_instance_id": instance_id}

        if tool_name == "terminate_instance":
            instance_id = tool_input["instance_id"]
            ec2_client.terminate_instances(InstanceIds=[instance_id])
            return {"terminated_instance_id": instance_id}

        # ── S3 Operations ──
        if tool_name == "list_s3_buckets":
            buckets_resp = s3_client.list_buckets().get("Buckets", [])
            cli_buckets = []
            for b in buckets_resp:
                name = b["Name"]
                try:
                    tags = s3_client.get_bucket_tagging(Bucket=name).get("TagSet", [])
                    if any(t["Key"] == "CreatedBy" and t["Value"] == "platform-cli" for t in tags):
                        cli_buckets.append({"name": name})
                except ClientError:
                    continue
            return {"buckets": cli_buckets}

        if tool_name == "create_s3_bucket":
            name = tool_input["name"]
            public = tool_input.get("public", False)
            session = boto3.session.Session()
            region = session.region_name or "us-east-1"

            kwargs = {"Bucket": name}
            if region != "us-east-1":
                kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}

            s3_client.create_bucket(**kwargs)

            if public:
                s3_client.delete_public_access_block(Bucket=name)
            else:
                s3_client.put_public_access_block(
                    Bucket=name,
                    PublicAccessBlockConfiguration={
                        'BlockPublicAcls': True,
                        'IgnorePublicAcls': True,
                        'BlockPublicPolicy': True,
                        'RestrictPublicBuckets': True
                    }
                )

            s3_client.put_bucket_tagging(
                Bucket=name,
                Tagging={"TagSet": [{"Key": "CreatedBy", "Value": "platform-cli"}]}
            )
            return {"created_bucket": {"name": name, "public": public, "region": region}}

        return {"error": f"Unknown tool: {tool_name}"}

    except ClientError as e:
        return {"error": str(e)}