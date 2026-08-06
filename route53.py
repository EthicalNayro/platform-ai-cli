import uuid
import click
import boto3
from botocore.exceptions import ClientError

def is_cli_zone(client, zone_id: str) -> bool:
    try:
        clean_id = zone_id.split("/")[-1]
        tags = client.list_tags_for_resource(
            ResourceType="hostedzone",
            ResourceId=clean_id
        ).get("ResourceTagSet", {}).get("Tags", [])
        return any(t["Key"] == "CreatedBy" and t["Value"] == "platform-cli" for t in tags)
    except ClientError:
        return False

route53 = click.Group("route53", help="Route53 DNS operations")

@route53.command("create-zone")
@click.option("--name", required=True, help="Domain name (e.g. example.com)")
def create_zone(name):
    client = boto3.client("route53")
    try:
        resp = client.create_hosted_zone(
            Name=name,
            CallerReference=str(uuid.uuid4()),
            HostedZoneConfig={"Comment": "Created by platform-cli"}
        )
        zone_id = resp["HostedZone"]["Id"].split("/")[-1]
        client.change_tags_for_resource(
            ResourceType="hostedzone",
            ResourceId=zone_id,
            AddTags=[{"Key": "CreatedBy", "Value": "platform-cli"}]
        )
        click.echo(f"Hosted zone created: {zone_id} ({name})")
    except ClientError as e:
        click.echo(f"Error creating hosted zone: {e}", err=True)

@route53.command("create-record")
@click.option("--zone-id", required=True, help="Hosted Zone ID")
@click.option("--name", required=True, help="Record name")
@click.option("--type", "record_type", default="A", help="Record type (A, CNAME, TXT, etc.)")
@click.option("--value", required=True, help="Target value")
@click.option("--ttl", default=300, help="TTL in seconds")
def create_record(zone_id, name, record_type, value, ttl):
    client = boto3.client("route53")
    clean_id = zone_id.split("/")[-1]

    if not is_cli_zone(client, clean_id):
        click.echo("Error: Records can only be managed on CLI-created zones.", err=True)
        return

    try:
        client.change_resource_record_sets(
            HostedZoneId=clean_id,
            ChangeBatch={
                "Changes": [{
                    "Action": "UPSERT",
                    "ResourceRecordSet": {
                        "Name": name,
                        "Type": record_type,
                        "TTL": ttl,
                        "ResourceRecords": [{"Value": value}]
                    }
                }]
            }
        )
        click.echo(f"Record UPSERT succeeded: {name} -> {value}")
    except ClientError as e:
        click.echo(f"Error creating record: {e}", err=True)

@route53.command("delete-record")
@click.option("--zone-id", required=True, help="Hosted Zone ID")
@click.option("--name", required=True, help="Record name")
@click.option("--type", "record_type", default="A", help="Record type")
@click.option("--value", required=True, help="Target value")
@click.option("--ttl", default=300, help="TTL in seconds")
def delete_record(zone_id, name, record_type, value, ttl):
    client = boto3.client("route53")
    clean_id = zone_id.split("/")[-1]

    if not is_cli_zone(client, clean_id):
        click.echo("Error: Records can only be managed on CLI-created zones.", err=True)
        return

    try:
        client.change_resource_record_sets(
            HostedZoneId=clean_id,
            ChangeBatch={
                "Changes": [{
                    "Action": "DELETE",
                    "ResourceRecordSet": {
                        "Name": name,
                        "Type": record_type,
                        "TTL": ttl,
                        "ResourceRecords": [{"Value": value}]
                    }
                }]
            }
        )
        click.echo(f"Record DELETE succeeded: {name}")
    except ClientError as e:
        click.echo(f"Error deleting record: {e}", err=True)

@route53.command("list")
def list_zones():
    client = boto3.client("route53")
    try:
        zones = client.list_hosted_zones().get("HostedZones", [])
        found = False
        for z in zones:
            clean_id = z["Id"].split("/")[-1]
            if is_cli_zone(client, clean_id):
                click.echo(f"{clean_id} | {z['Name']}")
                found = True
        if not found:
            click.echo("No CLI-managed hosted zones found.")
    except ClientError as e:
        click.echo(f"Error listing zones: {e}", err=True)
