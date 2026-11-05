"""Explicit deployment capacity; WSL is never inferred to be a Linux server."""
import platform
import sys

CAPACITIES={'linux-server':3,'windows-wsl':1}


def capacity(profile):
    if not isinstance(profile,str) or profile not in CAPACITIES:
        raise ValueError('deployment_profile must explicitly be linux-server or windows-wsl')
    return CAPACITIES[profile]


def validate_environment(profile, *, system=None, release=None):
    capacity(profile)
    system=sys.platform if system is None else system
    release=platform.release() if release is None else release
    wsl='microsoft' in release.lower() or 'wsl' in release.lower()
    if system!='linux' or (profile=='windows-wsl') != wsl:
        raise ValueError('Deployment profile does not match the Linux host; Windows must use the WSL launcher')
    if profile=='windows-wsl' and 'wsl2' not in release.lower():
        raise ValueError('The Windows trial requires a verified WSL2 kernel')
