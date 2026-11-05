"""Launch the single-seat trial controller using an explicitly selected WSL2 environment."""
import argparse
import subprocess
import sys
from pathlib import PurePosixPath


def command(distribution,python,config):
    if not distribution or distribution.startswith('-') or any(c in distribution for c in '\r\n\0'):
        raise ValueError('Use an explicit WSL distribution name')
    for value in (python,config):
        path=PurePosixPath(value)
        if not path.is_absolute() or '..' in path.parts or any(c in value for c in '\r\n\0'):
            raise ValueError('Use absolute Linux interpreter/configuration paths')
    return ['wsl.exe','--distribution',distribution,'--exec',python,'-m','ioalab_trial',
            '--config',config,'--deployment-profile','windows-wsl']


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--distribution',required=True)
    parser.add_argument('--python',required=True)
    parser.add_argument('--config',required=True)
    args=parser.parse_args(argv)
    try:
        invocation=command(args.distribution,args.python,args.config)
        if sys.platform!='win32':raise ValueError('This launcher is for Windows; on Linux use ioalab-trial directly')
        return subprocess.run(invocation,check=False).returncode
    except (OSError,ValueError) as exc:
        print('Trial launcher failed: '+str(exc),file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
