"""SupportService CLI

A CLI interface used for provisioning, deploying, and updating 
SupportService. 
"""
import os
import subprocess
import sys
import ldclient 
import logging 
import paramiko 

import click
import click_log

from circleci.api import Api
from cli.generators import ConfigGenerator
from cli.ld import LaunchDarklyApi
from cli.aws.aws import AwsApi
from cli.aws.ec2 import EC2Client
from paramiko import SSHClient
from paramiko.client import AutoAddPolicy
from os.path import expanduser

# set up logging 
logger = logging.getLogger(__name__)
click_log.basic_config(logger)

# key for production environment
ldclient.set_sdk_key(os.environ.get("LD_PROD_KEY"))
client = ldclient.get()

@click.group()
@click_log.simple_verbosity_option(logger)
def cli():
    pass

@click.command()
def deploy_relay():
    """Deploy LD Relay to LightSail."""
    l = LaunchDarklyApi(os.environ.get('LD_API_KEY'), 'ldsolutions.tk')
    envs = l.getEnvironments('support-service')
    c = ConfigGenerator()
    a = EC2Client(logger)

    c.generate_relay_config(envs)

    relays = a.getRelayInstances()
    # run reploy script
    for relay in relays:
        subprocess.run(["./scripts/deploy_relay.sh", "{0}".format(relay['PublicIpAddress'])], check=True)

@click.command()
def restart_relays():
    """Restart Relay Instances."""
    a = EC2Client(logger)
    for r in a.getRelayInstances():
        subprocess.run(["./scripts/restart_relay.sh", "{0}".format(r['PublicIpAddress'])], check=True)

@click.command()
@click_log.simple_verbosity_option(logger)
def deploy():
    """Deploy SupportService to LightSail.
    
    Uses a feature flag to disable automatically deploying specific 
    environments. It can be found in the production environment of
    the support-service project. 
    """
    l = LaunchDarklyApi(os.environ.get('LD_API_KEY'), 'ldsolutions.tk')
    a = AwsApi(logger, keyPairName='SupportService')
    c = ConfigGenerator()
    ci = Api(os.environ.get('CIRCLE_TOKEN'))

    envs = l.getEnvironments('support-service')

    for env in envs:

        hostname = env['hostname']

        ctx = {
            "key": "circleci",
            "custom": {
                "hostname": hostname
            }
        }

        params = {
            "build_parameters": {
                "CIRCLE_JOB": "deploy_instance",
                "LD_HOST": hostname
            }
        }

        if client.variation("auto-deploy-env", ctx, False):
            # run deploy job for environment 
            resp = ci.trigger_build(
                'launchdarkly',
                'SupportService',
                branch='master',
                params=params
            )
            click.echo("Sending Deploy Job to CircleCI for {0}".format(hostname))
            click.echo("CircleCI Build URL: {0}".format(resp['build_url']))
        else:
            click.echo("Not Auto Deploying, auto-deploy-env flag is off for {0}".format(hostname))

@click.command()
@click.argument('hostname')
@click_log.simple_verbosity_option(logger)
def deploy_instance(hostname):
    l = LaunchDarklyApi(os.environ.get('LD_API_KEY'), 'ldsolutions.tk')
    a = AwsApi(logger, keyPairName='SupportService')
    c = ConfigGenerator()

    envs = l.getEnvironments('support-service')
    env = [env for env in envs if env['hostname'] == hostname][0]

    click.echo("Deploying {0}".format(hostname))
    # create instance if needed
    a.upsert_instance(hostname)
    
    # get instace IP address 
    ip = a.getInstanceIp(hostname)
    # upset Route 53 record for instance
    a.upsertDnsRecord(hostname)

    # generate docker-compose file 
    c.generate_prod_config(env)
    c.generate_nginx_config(env)

    # run reploy script
    subprocess.run(["./scripts/deploy.sh", "{0}".format(ip)], check=True)

@click.command()
@click.argument('command')
@click_log.simple_verbosity_option(logger)
def run(command):
    """Execute Command on Every Instance

    This command requires the SupportService.pem file to be located in
    ~/.ssh/SupportService.pem

    To view the output execute the command with LOG_LEVEL=DEBUG 
    i.e.

        LOG_LEVEL=DEBUG python cli.py run 'free -m'
        
    :param command: command to execute on instance
    """
    logger.setLevel(logging.getLevelName(os.environ.get('LOG_LEVEL', default='INFO')))
    l = LaunchDarklyApi(os.environ.get('LD_API_KEY'), 'ldsolutions.tk')

    envs = l.getEnvironments('support-service')

    key = paramiko.RSAKey.from_private_key_file(expanduser("~/.ssh/SupportService.pem"))

    client = SSHClient()
    client.set_missing_host_key_policy(AutoAddPolicy)
    client.load_system_host_keys()

    for env in envs:

        host = env['hostname']
        logger.info('running command on {0}'.format(host))

        try:
            client.connect(
                hostname=host, 
                username='centos',
                pkey = key)
            stdin, stdout, stderr = client.exec_command(command)
            logger.debug(stdout.read())
        except:
            logger.error('Unable to SSH into {0}'.format(host))

cli.add_command(deploy_relay)
cli.add_command(deploy)
cli.add_command(restart_relays)
cli.add_command(deploy_instance)
cli.add_command(run)

if __name__ == '__main__':
    cli()
