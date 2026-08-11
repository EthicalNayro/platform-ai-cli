import click
import boto3
from botocore.exceptions import ClientError

TAG_FILTER = [{"Name": "tag:CreatedBy", "Values": ["platform-cli"]}]
MAX_INSTANCES = 2  # Hard cap constraint
ALLOWED_TYPES = ["t3.micro", "t2.small"]  # Allowed instance types

def get_current_user_arn() -> str:
    try:
        return boto3.client("sts").get_caller_identity()["Arn"].split("/")[-1]
    except ClientError:
        return "unknown-user"

def is_cli_instance(client, instance_id: str) -> bool:
    """Verifies that the instance was created by platform-cli."""
    try:
        resp = client.describe_instances(
            InstanceIds=[instance_id],
            Filters=TAG_FILTER
        )
        return len(resp.get("Reservations", [])) > 0
    except ClientError:
        return False

def get_non_terminated_instances_count(client) -> int:
    """Counts all CLI instances that are NOT terminated (includes stopped, stopping, running, etc.)."""
    try:
        resp = client.describe_instances(Filters=TAG_FILTER)
        count = 0
        for reservation in resp.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                state = instance.get("State", {}).get("Name", "")
                if state != "terminated":
                    count += 1
        return count
    except ClientError as e:
        click.echo(f"Error checking instance count: {e}", err=True)
        return 0

ec2 = click.Group("ec2", help="EC2 management operations")

@ec2.command("create")
@click.option("--type", "instance_type", type=click.Choice(ALLOWED_TYPES), required=True, help="Instance type")
@click.option("--name", default="dev-instance", help="Instance name tag")
@click.option("--os", "os_type", type=click.Choice(["ubuntu", "amazon-linux"]), default="ubuntu", help="OS image selection")
@click.option("--env", default="dev", help="Environment (dev/staging/prod)")
@click.option("--project", default="platform", help="Project name")
def create(instance_type, name, os_type, env, project):
    client = boto3.client("ec2")
    ssm = boto3.client("ssm")

    # Check hard cap for non-terminated instances
    if get_non_terminated_instances_count(client) >= MAX_INSTANCES:
        click.echo(
            f"Error: Hard cap limit reached! Maximum {MAX_INSTANCES} CLI instances allowed (including stopped instances).",
            err=True
        )
        return

    # Fetch latest AMI ID from SSM Parameter Store
    param_name = (
        "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id"
        if os_type == "ubuntu"
        else "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
    )

    try:
        ami = ssm.get_parameter(Name=param_name)["Parameter"]["Value"]
    except ClientError as e:
        click.echo(f"Error fetching AMI via SSM: {e}", err=True)
        return

    owner = get_current_user_arn()

    try:
        resp = client.run_instances(
            ImageId=ami,
            InstanceType=instance_type,
            MinCount=1,
            MaxCount=1,
            TagSpecifications=[{
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "CreatedBy", "Value": "platform-cli"},
                    {"Key": "Owner", "Value": owner},
                    {"Key": "Project", "Value": project},
                    {"Key": "Environment", "Value": env},
                    {"Key": "Name", "Value": name},
                ]
            }]
        )
        instance_id = resp["Instances"][0]["InstanceId"]
        click.echo(f"Success: Created EC2 instance {instance_id} ({instance_type}, {os_type})")
    except ClientError as e:
        click.echo(f"Error creating instance: {e}", err=True)

@ec2.command("list")
def list_instances():
    """Lists only instances created via platform-cli."""
    client = boto3.client("ec2")
    try:
        resp = client.describe_instances(Filters=TAG_FILTER)
        found = False
        for r in resp.get("Reservations", []):
            for i in r.get("Instances", []):
                found = True
                name_tag = next((t["Value"] for t in i.get("Tags", []) if t["Key"] == "Name"), "N/A")
                state = i.get("State", {}).get("Name", "unknown")
                click.echo(f"ID: {i['InstanceId']} | Name: {name_tag} | State: {state} | Type: {i.get('InstanceType')}")
        if not found:
            click.echo("No CLI-managed EC2 instances found.")
    except ClientError as e:
        click.echo(f"Error listing instances: {e}", err=True)

@ec2.command("start")
@click.argument("instance_id")
def start(instance_id):
    """Starts a CLI instance after tag validation."""
    client = boto3.client("ec2")
    if not is_cli_instance(client, instance_id):
        click.echo(f"Error: Instance {instance_id} is not managed by platform-cli.", err=True)
        return

    try:
        client.start_instances(InstanceIds=[instance_id])
        click.echo(f"Success: Started instance {instance_id}")
    except ClientError as e:
        click.echo(f"Error starting instance: {e}", err=True)

@ec2.command("stop")
@click.argument("instance_id")
def stop(instance_id):
    """Stops a CLI instance after tag validation."""
    client = boto3.client("ec2")
    if not is_cli_instance(client, instance_id):
        click.echo(f"Error: Instance {instance_id} is not managed by platform-cli.", err=True)
        return
    try:
        client.stop_instances(InstanceIds=[instance_id])
        click.echo(f"Success: Stopped instance {instance_id}")
    except ClientError as e:
        click.echo(f"Error stopping instance: {e}", err=True)
