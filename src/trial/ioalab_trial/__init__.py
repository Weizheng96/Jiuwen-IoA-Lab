"""Optional three-seat trial application. No protocol execution lives here.

The package entry is the only import surface for tools; processes start through their own `-m` commands.
"""
from .probe import has_assistant_skill
from .profile import DOCKER_COMMAND, capacity
from .sandbox import DockerSandbox

__all__ = ['DOCKER_COMMAND', 'DockerSandbox', 'capacity', 'has_assistant_skill']
