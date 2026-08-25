"""Bedrock tool schemas and the guarded AWS execution layer."""

import boto3
from botocore.exceptions import ClientError

MANAGED_BY_TAG = "platform-cli"
TAG_FILTER = [{"Name": "tag:CreatedBy", "Values": [MANAGED_BY_TAG]}]
ALLOWED_INSTANCE_TYPES = {"t3.micro", "t2.small"}
MAX_MANAGED_INSTANCES = 2


TOOL_DEFINITIONS = [
    {
        "name": "list_ec2_instances",
        "description": "List EC2 instances tagged CreatedBy=platform-cli.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "create_ec2_instance",
        "description": "Create a managed EC2 instance. Only t3.micro or t2.small; maximum two active managed instances.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name tag for the instance"},
                "instance_type": {"type": "string", "enum": sorted(ALLOWED_INSTANCE_TYPES)},
                "os": {"type": "string", "enum": ["ubuntu", "amazon-linux"]},
            },
            "required": ["name", "instance_type", "os"],
        },
    },
    {
        "name": "stop_instance",
        "description": "Stop an EC2 instance only when it is tagged CreatedBy=platform-cli.",
        "input_schema": {
            "type": "object",
            "properties": {"instance_id": {"type": "string"}},
            "required": ["instance_id"],
        },
    },
    {
        "name": "terminate_instance",
        "description": "Terminate a managed EC2 instance. Requires explicit human confirmation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {"type": "string"},
                "human_confirmed": {
                    "type": "boolean",
                    "description": "Must be explicitly supplied by the human and never inferred by the model.",
                },
            },
            "required": ["instance_id", "human_confirmed"],
        },
    },
    {
        "name": "list_s3_buckets",
        "description": "List S3 buckets tagged CreatedBy=platform-cli.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "create_s3_bucket",
        "description": "Create a tagged S3 bucket. Block Public Access is enabled by default.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "public": {"type": "boolean", "default": False},
                "human_confirmed": {"type": "boolean", "default": False},
            },
            "required": ["name"],
        },
    },
]


def _has_management_tag(tags: list[dict]) -> bool:
    return any(
        tag.get("Key") == "CreatedBy" and tag.get("Value") == MANAGED_BY_TAG
        for tag in tags
    )


def _is_managed_instance(client, instance_id: str) -> bool:
    """Verify ownership at execution time; never trust the model-provided ID."""
    try:
        response = client.describe_instances(InstanceIds=[instance_id])
    except ClientError:
        return False

    return any(
        _has_management_tag(instance.get("Tags", []))
        for reservation in response.get("Reservations", [])
        for instance in reservation.get("Instances", [])
    )


def _managed_instance_count(client) -> int:
    """Count managed EC2 instances that have not reached the terminated state."""
    response = client.describe_instances(Filters=TAG_FILTER)
    return sum(
        instance.get("State", {}).get("Name") != "terminated"
        for reservation in response.get("Reservations", [])
        for instance in reservation.get("Instances", [])
    )


def execute_tool(tool_name: str, tool_input: dict) -> dict:
    """Execute a model-selected operation after deterministic guardrail approval."""
    ec2_client = boto3.client("ec2")
    s3_client = boto3.client("s3")
    ssm_client = boto3.client("ssm")

    try:
        if tool_name == "list_ec2_instances":
            response = ec2_client.describe_instances(Filters=TAG_FILTER)
            instances = []
            for reservation in response.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    name = next(
                        (tag["Value"] for tag in instance.get("Tags", []) if tag["Key"] == "Name"),
                        "N/A",
                    )
                    instances.append(
                        {
                            "id": instance["InstanceId"],
                            "name": name,
                            "type": instance.get("InstanceType"),
                            "state": instance.get("State", {}).get("Name", "unknown"),
                        }
                    )
            return {"instances": instances}

        if tool_name == "create_ec2_instance":
            instance_type = tool_input["instance_type"]
            if instance_type not in ALLOWED_INSTANCE_TYPES:
                return {"error": f"Instance type '{instance_type}' is not allowed."}
            if _managed_instance_count(ec2_client) >= MAX_MANAGED_INSTANCES:
                return {
                    "error": (
                        f"Managed-instance limit reached: maximum {MAX_MANAGED_INSTANCES} "
                        "non-terminated instances."
                    )
                }

            os_type = tool_input.get("os", "ubuntu")
            parameter_name = (
                "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id"
                if os_type == "ubuntu"
                else "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
            )
            ami = ssm_client.get_parameter(Name=parameter_name)["Parameter"]["Value"]

            try:
                owner = boto3.client("sts").get_caller_identity()["Arn"].split("/")[-1]
            except ClientError:
                owner = "agent"

            response = ec2_client.run_instances(
                ImageId=ami,
                InstanceType=instance_type,
                MinCount=1,
                MaxCount=1,
                TagSpecifications=[
                    {
                        "ResourceType": "instance",
                        "Tags": [
                            {"Key": "CreatedBy", "Value": MANAGED_BY_TAG},
                            {"Key": "Owner", "Value": owner},
                            {"Key": "Project", "Value": "platform"},
                            {"Key": "Environment", "Value": "dev"},
                            {"Key": "Name", "Value": tool_input["name"]},
                        ],
                    }
                ],
            )
            instance = response["Instances"][0]
            return {
                "created": {
                    "id": instance["InstanceId"],
                    "name": tool_input["name"],
                    "type": instance["InstanceType"],
                    "state": instance.get("State", {}).get("Name", "pending"),
                }
            }

        if tool_name in {"stop_instance", "terminate_instance"}:
            instance_id = tool_input["instance_id"]
            if not _is_managed_instance(ec2_client, instance_id):
                return {
                    "error": (
                        f"Instance '{instance_id}' is not managed by platform-cli; "
                        "the action was blocked."
                    )
                }
            if tool_name == "stop_instance":
                ec2_client.stop_instances(InstanceIds=[instance_id])
                return {"stopped_instance_id": instance_id}
            ec2_client.terminate_instances(InstanceIds=[instance_id])
            return {"terminated_instance_id": instance_id}

        if tool_name == "list_s3_buckets":
            managed_buckets = []
            for bucket in s3_client.list_buckets().get("Buckets", []):
                name = bucket["Name"]
                try:
                    tags = s3_client.get_bucket_tagging(Bucket=name).get("TagSet", [])
                except ClientError:
                    continue
                if _has_management_tag(tags):
                    managed_buckets.append({"name": name})
            return {"buckets": managed_buckets}

        if tool_name == "create_s3_bucket":
            name = tool_input["name"]
            public = tool_input.get("public", False)
            region = boto3.session.Session().region_name or "us-east-1"
            create_args = {"Bucket": name}
            if region != "us-east-1":
                create_args["CreateBucketConfiguration"] = {"LocationConstraint": region}

            s3_client.create_bucket(**create_args)
            if public:
                s3_client.delete_public_access_block(Bucket=name)
            else:
                s3_client.put_public_access_block(
                    Bucket=name,
                    PublicAccessBlockConfiguration={
                        "BlockPublicAcls": True,
                        "IgnorePublicAcls": True,
                        "BlockPublicPolicy": True,
                        "RestrictPublicBuckets": True,
                    },
                )
            s3_client.put_bucket_tagging(
                Bucket=name,
                Tagging={"TagSet": [{"Key": "CreatedBy", "Value": MANAGED_BY_TAG}]},
            )
            return {
                "created_bucket": {
                    "name": name,
                    "block_public_access": not public,
                    "region": region,
                }
            }

        return {"error": f"Unknown tool: {tool_name}"}
    except ClientError as error:
        return {"error": str(error)}
