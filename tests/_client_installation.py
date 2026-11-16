"""One real installed client for product-entry tests, away from pytest's source path."""
import os
from pathlib import Path
import venv

import pytest


@pytest.fixture(scope='session')
def installed_client_python(tmp_path_factory):
    from tools.build_client import build
    from test_package_installation import DEPENDENCY_PATHS, run
    folder = tmp_path_factory.mktemp('product-client-installation')
    wheel = build(folder/'wheels')
    environment = folder/'environment'
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    run([str(python), '-I', '-m', 'pip', 'install', '--no-index', '--no-deps', str(wheel)], folder)
    purelib = run([str(python), '-I', '-c', 'import sysconfig;print(sysconfig.get_paths()["purelib"])'], folder).strip()
    Path(purelib, 'zz-third-party.pth').write_text(DEPENDENCY_PATHS, encoding='utf-8')
    return python
