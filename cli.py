import click
from ec2 import ec2
from s3 import s3
from route53 import route53

@click.group()
def cli():
    pass

cli.add_command(ec2)
cli.add_command(s3)
cli.add_command(route53)

if __name__ == "__main__":
    cli()
