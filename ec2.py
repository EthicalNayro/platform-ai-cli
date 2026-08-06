import click
import boto3
from botocore.exceptions import ClientError

TAG_FILTER = [{"Name": "tag:CreatedBy", "Values": ["platform-cli"]}]
MAX_INSTANCES = 2
ALLOWED_TYPES = ["t3.micro", "t2.small"]

def is_cli_instance(client, instance_id: str) -> bool:
    try:
        resp = client.describe_instances(
            InstanceIds=[instance_id],
            Filters=TAG_FILTER
        )
        return len(resp.get("Reservations", [])) > 0
    except ClientError:
        return False

ec2 = click.Group("ec2", help="EC2 management operations")

@ec2.command("create")
@click.option("--type", "instance_type", type=click.Choice(ALLOWED_TYPES), required=True, help="Instance type")
@click.option("--name", default="dev-instance", help="Tag Name for the instance")
@click.option("--os", "os_type", type=click.Choice(["ubuntu", "amazon-linux"]), default="ubuntu", help="OS image")
def create(instance_type, name, os_type):
    client = boto3.client("ec2")
    ssm = boto3.client("ssm")

    param_name = (
        "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id"
        if os_type == "ubuntu"
        else "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
    )

    try:
        ami = ssm.get_parameter(Name=param_name)["Parameter"]["Value"]
    except ClientError as e:
        click.echo(f"Error fetching AMI: {e}", err=True)
        return

    reservations = client.describe_instances(
        Filters=TAG_FILTER + [{"Name": "instance-state-name", "Values": ["running", "pending"]}]
    ).get("Reservations", [])

    running_count = sum(len(r.get("Instances", [])) for r in reservations)

    if running_count >= MAX_INSTANCES:
        click.echo(f"Error: Hard cap of {MAX_INSTANCES} running/pending CLI instances reached.")
        return

    try:
        user_arn = boto3.client("sts").get_caller_identity()["Arn"].split("/")[-1]
    except ClientError:
        user_arn = "unknown"

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
                    {"Key": "Owner", "Value": user_arn},
                    {"Key": "Name", "Value": name},
                ]
            }]
        )
        instance_id = resp["Instances"][0]["InstanceId"]
        click.echo(f"Successfully created instance: {instance_id}")
    except ClientError as e:
        click.echo(f"Error creating instance: {e}", err=True)

@ec2.command("list")
def list_instances():
    client = boto3.client("ec2")
    try:
        resp = client.describe_instances(Filters=TAG_FILTER)
        found = False
        for r in resp.get("Reservations", []):
            for i in r.get("Instances", []):
                found = True
                name_tag = next((t["Value"] for t in i.get("Tags", []) if t["Key"] == "Name"), "N/A")
                state = i.get("State", {}).get("Name", "unknown")
                click.echo(f"{i['InstanceId']} | Name: {name_tag} | State: {state} | Type: {i.get('InstanceType')}")
        if not found:
            click.echo("No CLI-managed instances found.")
    except ClientError as e:
        click.echo(f"Error listing instances: {e}", err=True)

@ec2.command("start")
@click.argument("instance_id")
def start(instance_id):
    client = boto3.client("ec2")
    if not is_cli_instance(client, instance_id):
        click.echo(f"Error: Instance {instance_id} is not managed by platform-cli.", err=True)
        return
    try:
        client.start_instances(InstanceIds=[instance_id])
        click.echo(f"Started instance: {instance_id}")
    except ClientError as e:
        click.echo(f"Error starting instance: {e}", err=True)

@ec2.command("stop")
@click.argument("instance_id")
def stop(instance_id):
    client = boto3.client("ec2")
    if not is_cli_instance(client, instance_id):
        click.echo(f"Error: Instance {instance_id} is not managed by platform-cli.", err=True)
        return
    try:
        client.stop_instances(InstanceIds=[instance_id])
        click.echo(f"Stopped instance: {instance_id}")
    except ClientError as e:
        click.echo(f"Error stopping instance: {e}", err=True)
