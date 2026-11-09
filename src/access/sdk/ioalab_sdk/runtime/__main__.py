"""`python -m ioalab_sdk.runtime` and `ioalab-access`; the commands live in lifecycle and the SDK assembly supplies the roles.

`start`, `require_configuration`, `setup_command` and `run` stay importable here for installed callers of this path."""
from .lifecycle import require_configuration, run, setup_command, start

__all__ = ['main', 'require_configuration', 'run', 'setup_command', 'start']


def main(argv=None):
    """Stable `ioalab-access` and `-m ioalab_sdk.runtime` command; the SDK assembly supplies the roles."""
    from ..access import main as assembled
    return assembled(argv)


if __name__=='__main__':raise SystemExit(main())
