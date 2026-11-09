"""Explicit Linux-server capacity; WSL is never inferred to be a Linux server."""
import platform
import sys

CAPACITY=3
RESOURCE_LAYOUT='epoch-ingress/1'
INGRESS_CAPABILITY='fixed-seat-tcp-8080-8767'
DOCKER_COMMAND=('docker','--host','unix:///var/run/docker.sock')


def capacity(profile):
    if profile!='linux-server':
        raise ValueError('deployment_profile must explicitly be linux-server')
    return CAPACITY


def validate_environment(profile, *, system=None, release=None):
    capacity(profile)
    system=sys.platform if system is None else system
    release=(platform.release() if release is None else release).lower()
    if system!='linux' or 'microsoft' in release or 'wsl' in release:
        raise ValueError('Online trial runs only on a real Linux server; Windows and WSL are not supported')
