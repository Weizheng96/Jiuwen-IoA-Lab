import os
import tempfile

import pytest

@pytest.fixture(scope="session", autouse=True)
def _owned_a2c_cache(tmp_path_factory):
    # The unique repository runner owns this cache; never reuse or mutate user caches.
    previous = os.environ.get("A2C_CACHE_DIR")
    os.environ["A2C_CACHE_DIR"] = str(tmp_path_factory.getbasetemp() / "a2c-cache")
    yield
    if previous is None:
        os.environ.pop("A2C_CACHE_DIR", None)
    else:
        os.environ["A2C_CACHE_DIR"] = previous


@pytest.fixture
def world():
    from .support import World
    return World()


@pytest.fixture(autouse=True)
def _close_transports():
    yield
    from a2c.binding import A2ATransport
    A2ATransport.close_all()
