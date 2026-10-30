"""Shared resident infrastructure; role handlers and hosts are injected separately."""
from .connection import AgentConnection
from .store import RuntimeStore
from .protocol import RuntimeFault

__all__ = ['AgentConnection', 'RuntimeStore', 'RuntimeFault']
