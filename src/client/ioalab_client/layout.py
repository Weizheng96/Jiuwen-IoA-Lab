"""Non-secret client locators; configuration contents belong to the SDK."""
from pathlib import Path
import sys


def configuration_path(value=None):
    return Path(value or Path.home()/'.ioalab/client/settings.local.json').expanduser().resolve()


def locations(config=None, python=None):
    config = configuration_path(config)
    python = Path(python or sys.executable).absolute()
    program = python.parent.parent
    root = program.parent if program.name == 'env' and (program.parent/'ioalab-bootstrap.json').is_file() else program
    return {'clientDirectory': str(root), 'programDirectory': str(program),
            'python': str(python), 'configurationFile': str(config)}


def binding(config=None, python=None):
    return {'python': str(Path(python or sys.executable).absolute()),
            'configurationFile': str(configuration_path(config))}
