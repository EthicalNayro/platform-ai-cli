import click
import boto3
from botocore.exceptions import ClientError

def is_cli_bucket(client, bucket_name: str) -> bool:
    try:
        tags = client.get_bucket_tagging(Bucket=bucket_name).get("TagSet", [])
        return any(t["Key"] == "CreatedBy" and t["Value"] == "platform-cli" for t in tags)
    except ClientError:
        return False

s3 = click.Group("s3", help="S3 bucket operations")

@s3.command("create")
@click.option("--name", required=True, help="Unique bucket name")
@click.option("--public", is_flag=True, help="Make bucket public")
def create(name, public):
    client = boto3.client("s3")
    session = boto3.session.Session()
    region = session.region_name or "us-east-1"

    if public:
        if not click.confirm("Are you sure you want to create a PUBLIC bucket?"):
            click.echo("Aborted.")
            return

    try:
        kwargs = {"Bucket": name}
        if region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}

        client.create_bucket(**kwargs)

        if public:
            client.delete_public_access_block(Bucket=name)
        else:
            client.put_public_access_block(
                Bucket=name,
                PublicAccessBlockConfiguration={
                    'BlockPublicAcls': True,
                    'IgnorePublicAcls': True,
                    'BlockPublicPolicy': True,
                    'RestrictPublicBuckets': True
                }
            )

        client.put_bucket_tagging(
            Bucket=name,
            Tagging={"TagSet": [{"Key": "CreatedBy", "Value": "platform-cli"}]}
        )
        status = "PUBLIC" if public else "PRIVATE"
        click.echo(f"Bucket '{name}' created ({status}) in region '{region}'")
    except ClientError as e:
        click.echo(f"Error creating bucket: {e}", err=True)

@s3.command("upload")
@click.argument("bucket")
@click.argument("file_path")
def upload(bucket, file_path):
    client = boto3.client("s3")
    if not is_cli_bucket(client, bucket):
        click.echo("Error: Upload allowed only to CLI-created buckets.", err=True)
        return
    try:
        key = file_path.split("/")[-1]
        client.upload_file(file_path, bucket, key)
        click.echo(f"Uploaded {file_path} to {bucket}/{key}")
    except ClientError as e:
        click.echo(f"Error uploading file: {e}", err=True)

@s3.command("list")
def list_buckets():
    client = boto3.client("s3")
    try:
        buckets = client.list_buckets().get("Buckets", [])
        found = False
        for b in buckets:
            name = b["Name"]
            if is_cli_bucket(client, name):
                click.echo(name)
                found = True
        if not found:
            click.echo("No CLI-managed buckets found.")
    except ClientError as e:
        click.echo(f"Error listing buckets: {e}", err=True)
